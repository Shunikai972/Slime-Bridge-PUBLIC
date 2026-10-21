"""``bridge-mod-manifest`` v1: one normalized, untrusted-input-safe description of an SR1/SR2 mod.

Inputs are hostile until validated. Three formats reach this module:

- ``SRML_MODINFO``: an SR1 SRML ``modinfo.json`` read from a mod folder (``parse_srml_modinfo``).
- ``MELON_METADATA``: a ``bridge-melon-metadata`` v1 document that a LOCAL provider extracts
  from MelonLoader assembly attributes (``parse_melon_metadata``). Cloud never opens, loads or
  executes a mod binary; attributes only arrive through this provider document.
- ``BRIDGE_MANIFEST``: an already normalized ``bridge-mod-manifest`` (``load_manifest``).

Every entry point returns ``(manifest_or_None, diagnostics)`` and never raises on input content:
oversized, non-UTF-8, malformed, duplicate-key, non-finite or too-deep JSON, wrong types, unknown
enums and unsafe paths all become ``modcompat`` diagnostics. Any error-level diagnostic means no
manifest. ``manifest_id`` is the content hash of every other field.

Loader facts used here come from repository evidence only and are NEEDS_LOCAL_RUNTIME_PROOF:
- SRML ``modinfo.json`` keys ``id``/``name``/``version``/``author``/``dependencies``
  (``mods/sr1-bridge/modinfo.json``); ``version`` must be ``X.Y[.Z]`` integers or SRML rejects the mod
  at PreLoad (``docs/RECON.md``, ``tests/test_plugin_manifests.py``). The dependency string syntax and
  ``load_after``/``load_before`` semantics are NOT established in this repository (LOCAL_QUERY Q052);
  a version after a dependency id is read as a minimum and flagged.
- SRML is the SR1 loader and MelonLoader the SR2 loader; SR2's ``MelonGame`` identity is
  ``("MonomiPark", "SlimeRancher2")`` (``docs/RECON.md``, ``mods/sr2-bridge/BridgeMod.cs``).

Version constraints use one Bridge grammar: ``*`` or comma-joined clauses ``<op><X[.Y[.Z[.W]]]>``
with ``op`` in ``= > >= < <= ^ ~`` (``^`` same first part, ``~`` same first two parts).
"""

import json
import math
import re

from bridge.content.canonical import canonical_json, sha256_json
from bridge.core import diagnostics as D
from bridge.core import schema as S
from bridge.core.paths import safe_relative

MANIFEST_SCHEMA = 'bridge-mod-manifest'
MELON_METADATA_SCHEMA = 'bridge-melon-metadata'
SCHEMA_VERSION = 1
FORMATS = ('SRML_MODINFO', 'MELON_METADATA', 'BRIDGE_MANIFEST')
REALMS = ('SR1', 'SR2')
LOADERS = ('SRML', 'MELONLOADER', 'UNKNOWN')
LOADER_REALM = {'SRML': 'SR1', 'MELONLOADER': 'SR2'}
SOURCE_KINDS = ('LOCAL_PATH', 'PROVIDER')
DEPENDENCY_KINDS = ('MOD', 'ASSEMBLY')
PLATFORM_DOMAINS = ('IL2CPP', 'MONO', 'BOTH', 'UNKNOWN')
# docs/RECON.md + mods/sr2-bridge/BridgeMod.cs: [MelonGame("MonomiPark","SlimeRancher2")]. No SR1 MelonGame
# identity is established in this repository (SR1 uses SRML), so none is listed.
MELON_GAME_REALMS = {('MonomiPark', 'SlimeRancher2'): 'SR2'}

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_DEPTH = 32
MAX_FILES = 2048
MAX_DEPENDENCIES = 128
MAX_AUTHORS = 16
MAX_GAME_VERSIONS = 32
MAX_ASSEMBLIES = 64
MAX_FILE_SIZE = 2 ** 40

ID_PATTERN = r'[a-z0-9][a-z0-9_.-]{0,127}'
ASSEMBLY_PATTERN = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}'
TEXT_PATTERN = r'[^\x00-\x1f\x7f\ud800-\udfff]{1,128}'
TYPE_NAME_PATTERN = r'[A-Za-z_][A-Za-z0-9_.`+<>]{0,255}'
VERSION_TEXT_PATTERN = r'[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}'
GAME_VERSION_PATTERN = r'[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}'
PROVIDER_ID_PATTERN = r'[a-z0-9]+(?:[._-][a-z0-9]+)+'
SEMVER_PATTERN = r'(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})'
REF_PATTERN = r'[^\x00-\x1f\x7f\ud800-\udfff]{1,256}'

_SRML_VERSION = re.compile(r'(\d{1,9})\.(\d{1,9})(?:\.(\d{1,9}))?')
_LOOSE_VERSION = re.compile(r'v?(\d{1,9}(?:\.\d{1,9}){0,3})(?:[-+][0-9A-Za-z.-]{1,48})?')
_CLAUSE = re.compile(r'(>=|<=|==|=|>|<|\^|~)?\s*(\d{1,9}(?:\.\d{1,9}){0,3})')
_SRML_DEPENDENCY = re.compile(r'([^\s@]{1,128})(?:(?:\s*@\s*|\s+)(\S{1,64}))?')
_NON_PRINTABLE = re.compile(r'[\x00-\x1f\x7f\ud800-\udfff]')

SRML_KNOWN_FIELDS = ('author', 'dependencies', 'description', 'id', 'load_after', 'load_before', 'name', 'version')

FILE_SPEC = S.obj({'path': S.string(max_length=1024), 'sha256': S.sha256(),
                   'size': S.integer(minimum=0, maximum=MAX_FILE_SIZE)})
_DEPENDENCY_SPEC = S.obj({'id': S.string(max_length=128), 'kind': S.enum(*DEPENDENCY_KINDS),
                          'constraint': S.string(max_length=128), 'optional': S.boolean()})
SOURCE_SPEC = S.obj({'kind': S.enum(*SOURCE_KINDS), 'path': S.optional(S.string(max_length=1024)),
                     'provider_id': S.optional(S.string(PROVIDER_ID_PATTERN, max_length=128)),
                     'ref': S.optional(S.string(REF_PATTERN, max_length=256)), 'synthetic': S.boolean()})
_MANIFEST_SPEC = S.obj({
    'schema': S.enum(MANIFEST_SCHEMA), 'schema_version': S.enum(SCHEMA_VERSION), 'manifest_id': S.sha256(),
    'id': S.string(ID_PATTERN, max_length=128), 'native_id': S.string(TEXT_PATTERN, max_length=128),
    'name': S.string(TEXT_PATTERN, max_length=128),
    'version': S.obj({'text': S.string(VERSION_TEXT_PATTERN, max_length=64),
                      'parts': S.list_of(S.integer(minimum=0, maximum=999999999), max_items=4)}),
    'realm': S.enum(*REALMS), 'loader': S.enum(*LOADERS),
    'authors': S.list_of(S.string(TEXT_PATTERN, max_length=128), max_items=MAX_AUTHORS, unique=True),
    'dependencies': S.list_of(_DEPENDENCY_SPEC, max_items=MAX_DEPENDENCIES),
    'load_after': S.list_of(S.string(ID_PATTERN, max_length=128), max_items=MAX_DEPENDENCIES, unique=True),
    'load_before': S.list_of(S.string(ID_PATTERN, max_length=128), max_items=MAX_DEPENDENCIES, unique=True),
    'game_versions': S.list_of(S.string(GAME_VERSION_PATTERN, max_length=32), max_items=MAX_GAME_VERSIONS,
                               unique=True),
    'provides_assemblies': S.list_of(S.string(ASSEMBLY_PATTERN, max_length=128), max_items=MAX_ASSEMBLIES,
                                     unique=True),
    'files': S.list_of(FILE_SPEC, max_items=MAX_FILES),
    'source': SOURCE_SPEC,
})
_MELON_SPEC = S.obj({
    'schema': S.enum(MELON_METADATA_SCHEMA), 'schema_version': S.enum(SCHEMA_VERSION),
    'extractor': S.obj({'id': S.string(PROVIDER_ID_PATTERN, max_length=128), 'version': S.string(SEMVER_PATTERN)}),
    'synthetic': S.boolean(),
    'declared_id': S.optional(S.string(ID_PATTERN, max_length=128)),
    'assembly': S.obj({'name': S.string(ASSEMBLY_PATTERN, max_length=128), 'file': FILE_SPEC,
                       'target_framework': S.optional(S.string(r'[a-z0-9][a-z0-9.]{0,31}'))}),
    'melon_info': S.obj({'type_name': S.string(TYPE_NAME_PATTERN, max_length=256),
                         'name': S.string(TEXT_PATTERN, max_length=128), 'version': S.string(max_length=64),
                         'author': S.optional(S.string(TEXT_PATTERN, max_length=128))}),
    'melon_games': S.list_of(S.obj({'developer': S.string(TEXT_PATTERN, max_length=128),
                                    'name': S.string(TEXT_PATTERN, max_length=128)}), max_items=16),
    'platform_domain': S.enum(*PLATFORM_DOMAINS),
    'game_versions': S.list_of(S.string(GAME_VERSION_PATTERN, max_length=32), max_items=MAX_GAME_VERSIONS,
                               unique=True),
    'dependencies': S.list_of(_DEPENDENCY_SPEC, max_items=MAX_DEPENDENCIES),
    'additional_files': S.list_of(FILE_SPEC, max_items=MAX_FILES),
})


# ----------------------------------------------------------------------------------------- helpers

def printable(value, limit=120):
    """A safe, bounded rendering of untrusted text for diagnostics (no controls, no lone surrogates)."""
    text = value if isinstance(value, str) else repr(value)
    return _NON_PRINTABLE.sub('?', text[:limit])


def diag(code, severity, message, *, subject=None, details=None, action=None, evidence=None):
    return D.make(code, severity, 'modcompat', message, subject=printable(subject, 160) if subject else None,
                  details=details, action=action, evidence=evidence)


def _problems(problems):
    return [printable(p, 200) for p in problems[:20]]


def deploy_key_id(mod_id):
    """The ``bridge-deploy-unit`` key segment for a mod id (``_`` is not allowed in unit keys)."""
    return mod_id.replace('_', '-')


def parse_version(text):
    """Integer parts of a version (``v`` prefix and pre-release/build suffix ignored), or None."""
    if not isinstance(text, str):
        return None
    match = _LOOSE_VERSION.fullmatch(text)
    return [int(p) for p in match.group(1).split('.')] if match else None


def parse_constraint(text, *, bare='='):
    """Canonical constraint text, or None if unparseable. A clause without operator uses ``bare``."""
    if not isinstance(text, str) or len(text) > 128:
        return None
    text = text.strip()
    if text in ('', '*'):
        return '*'
    clauses = []
    for raw in text.split(','):
        match = _CLAUSE.fullmatch(raw.strip())
        if not match:
            return None
        op = match.group(1) or bare
        op = '=' if op == '==' else op
        clauses.append(op + '.'.join(str(int(p)) for p in match.group(2).split('.')))
    return ','.join(clauses)


def satisfies(parts, constraint):
    """True/False, or None when the version parts are unknown or the constraint is not canonical."""
    if not isinstance(constraint, str) or parse_constraint(constraint) != constraint:
        return None
    if constraint == '*':
        return True
    if not parts:
        return None
    for clause in constraint.split(','):
        match = _CLAUSE.fullmatch(clause)
        op, want = match.group(1), [int(p) for p in match.group(2).split('.')]
        width = max(len(parts), len(want))
        have = tuple(parts) + (0,) * (width - len(parts))
        need = tuple(want) + (0,) * (width - len(want))
        ok = {'=': have == need, '>': have > need, '>=': have >= need, '<': have < need, '<=': have <= need,
              '^': have >= need and have[0] == need[0],
              '~': have >= need and have[:2] == need[:2]}[op]
        if not ok:
            return False
    return True


class _DuplicateKey(ValueError):
    pass


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('non-finite number')
    return value


def _reject_constant(name):
    raise ValueError(f'non-finite constant {name}')


def _depth(value):
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, depth = stack.pop()
        deepest = max(deepest, depth)
        if depth > MAX_DEPTH:
            return depth
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return deepest


def decode_json(raw, *, subject='<input>', max_bytes=MAX_DOCUMENT_BYTES):
    """``(object, diagnostics)`` for raw bytes, text or an already parsed JSON value. Never raises.

    Every Bridge mod document is a JSON object. Parsed values are re-serialized and re-read so the
    result is a fresh, plain JSON tree that shares nothing with the caller's value.
    """
    def bad(code, message, **details):
        return None, [diag(code, 'error', message, subject=subject, details=details or None)]

    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > max_bytes:
            return bad('MOD_DOCUMENT_TOO_LARGE', 'document exceeds the size limit', size=len(raw), limit=max_bytes)
        try:
            text = bytes(raw).decode('utf-8-sig')
        except UnicodeDecodeError:
            return bad('MOD_JSON_ENCODING_INVALID', 'document is not UTF-8')
    elif isinstance(raw, str):
        text = raw[1:] if raw.startswith('﻿') else raw
    else:
        try:
            text = canonical_json(raw)
        except (ValueError, TypeError, RecursionError):
            return bad('MOD_JSON_VALUE_INVALID', 'value is not finite, string-keyed JSON')
    try:
        size = len(text.encode('utf-8'))
    except UnicodeEncodeError:
        return bad('MOD_JSON_ENCODING_INVALID', 'document contains unencodable text')
    if size > max_bytes:
        return bad('MOD_DOCUMENT_TOO_LARGE', 'document exceeds the size limit', size=size, limit=max_bytes)
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_float=_finite_float,
                           parse_constant=_reject_constant)
    except _DuplicateKey as exc:
        return bad('MOD_JSON_DUPLICATE_KEY', 'object has a duplicate key', key=printable(exc.args[0], 64))
    except RecursionError:
        return bad('MOD_JSON_TOO_DEEP', 'document nesting exceeds the limit', limit=MAX_DEPTH)
    except ValueError as exc:
        return bad('MOD_JSON_MALFORMED', 'document is not valid JSON', error=printable(str(exc), 160))
    try:
        canonical_json(value).encode('utf-8')
    except UnicodeEncodeError:
        return bad('MOD_JSON_ENCODING_INVALID', 'document contains a lone surrogate escape')
    if _depth(value) > MAX_DEPTH:
        return bad('MOD_JSON_TOO_DEEP', 'document nesting exceeds the limit', limit=MAX_DEPTH)
    if not isinstance(value, dict):
        return bad('MOD_JSON_NOT_OBJECT', 'document must be a JSON object')
    return value, []


def local_source(path, *, synthetic=False):
    """Source block for a manifest read from ``path`` (relative to the scanned game/lab root)."""
    return {'kind': 'LOCAL_PATH', 'path': path, 'provider_id': None, 'ref': None, 'synthetic': synthetic}


def provider_source(provider_id, ref=None, *, synthetic=False):
    return {'kind': 'PROVIDER', 'path': None, 'provider_id': provider_id, 'ref': ref, 'synthetic': synthetic}


def make_manifest(*, id, native_id, name, version_text, version_parts, realm, loader, source, authors=(),
                  dependencies=(), load_after=(), load_before=(), game_versions=(), provides_assemblies=(), files=()):
    """Build a manifest from already-checked values (parsers validate before calling this)."""
    doc = {'schema': MANIFEST_SCHEMA, 'schema_version': SCHEMA_VERSION, 'id': id, 'native_id': native_id,
           'name': name, 'version': {'text': version_text, 'parts': list(version_parts)}, 'realm': realm,
           'loader': loader, 'authors': sorted(set(authors)),
           'dependencies': sorted((dict(d) for d in dependencies), key=lambda d: (d['kind'], d['id'])),
           'load_after': sorted(set(load_after)), 'load_before': sorted(set(load_before)),
           'game_versions': sorted(set(game_versions)), 'provides_assemblies': sorted(set(provides_assemblies)),
           'files': sorted(({'path': f['path'], 'sha256': f['sha256'], 'size': f['size']} for f in files),
                           key=lambda f: f['path']),
           'source': dict(source)}
    doc['manifest_id'] = sha256_json(doc)
    return doc


# ---------------------------------------------------------------------------------- validation

def _check_files(files, subject, out, label='files'):
    folded = {}
    for entry in files:
        if not safe_relative(entry['path']):
            out.append(diag('MANIFEST_PATH_UNSAFE', 'error', 'unsafe relative file path', subject=subject,
                            details={'field': label, 'path': printable(entry['path'], 200)}))
        folded.setdefault(entry['path'].casefold(), []).append(entry['path'])
    for paths in folded.values():
        if len(paths) > 1:
            out.append(diag('MANIFEST_FILE_DUPLICATE', 'error', 'file paths collide (case-insensitive)',
                            subject=subject, details={'paths': [printable(p, 200) for p in paths[:4]]}))


def _check_source(source, subject, out):
    if source['kind'] == 'LOCAL_PATH':
        if source['path'] is None or not safe_relative(source['path']) or source['provider_id'] is not None:
            out.append(diag('MANIFEST_SOURCE_INVALID', 'error',
                            'LOCAL_PATH source needs a safe relative path and no provider', subject=subject))
    elif source['provider_id'] is None or source['path'] is not None:
        out.append(diag('MANIFEST_SOURCE_INVALID', 'error', 'PROVIDER source needs a provider_id and no path',
                        subject=subject))


def _check_dependency(dep, owner, out):
    if dep['kind'] == 'MOD' and not re.fullmatch(ID_PATTERN, dep['id']):
        out.append(diag('MANIFEST_DEPENDENCY_INVALID', 'error', 'mod dependency id is not a mod id', subject=owner,
                        details={'dependency': printable(dep['id'], 128)}))
    if dep['kind'] == 'ASSEMBLY' and not re.fullmatch(ASSEMBLY_PATTERN, dep['id']):
        out.append(diag('MANIFEST_DEPENDENCY_INVALID', 'error', 'assembly dependency name is invalid',
                        subject=owner, details={'dependency': printable(dep['id'], 128)}))
    if parse_constraint(dep['constraint']) != dep['constraint']:
        out.append(diag('MANIFEST_DEPENDENCY_CONSTRAINT_INVALID', 'error', 'constraint is not canonical',
                        subject=owner, details={'dependency': printable(dep['id'], 128),
                                                'constraint': printable(dep['constraint'], 128)}))


def validate_manifest(doc):
    """Unified diagnostics for a normalized manifest. Never raises."""
    problems = S.check(_MANIFEST_SPEC, doc)
    subject = doc.get('id') if isinstance(doc, dict) and isinstance(doc.get('id'), str) else None
    if problems:
        return [diag('MANIFEST_SCHEMA_INVALID', 'error', 'mod manifest schema violation', subject=subject,
                     details={'problems': _problems(problems)})]
    out = []
    if sha256_json({k: v for k, v in doc.items() if k != 'manifest_id'}) != doc['manifest_id']:
        out.append(diag('MANIFEST_ID_MISMATCH', 'error', 'manifest_id is not the content hash', subject=subject))
    expected = LOADER_REALM.get(doc['loader'])
    if expected is not None and expected != doc['realm']:
        out.append(diag('MANIFEST_LOADER_REALM_MISMATCH', 'error', f"{doc['loader']} is not a {doc['realm']} loader",
                        subject=subject, details={'loader': doc['loader'], 'realm': doc['realm']}))
    seen = set()
    for dep in doc['dependencies']:
        _check_dependency(dep, subject, out)
        key = (dep['kind'], dep['id'].casefold())
        if key in seen:
            out.append(diag('MANIFEST_DEPENDENCY_DUPLICATE', 'error', 'dependency listed twice', subject=subject,
                            details={'dependency': printable(dep['id'], 128)}))
        seen.add(key)
        if dep['kind'] == 'MOD' and dep['id'].casefold() == doc['id'].casefold():
            out.append(diag('MANIFEST_SELF_DEPENDENCY', 'error', 'a mod cannot depend on itself', subject=subject))
    for field in ('load_after', 'load_before'):
        if doc['id'] in doc[field]:
            out.append(diag('MANIFEST_SELF_DEPENDENCY', 'error', f'{field} names the mod itself', subject=subject))
    _check_files(doc['files'], subject, out)
    _check_source(doc['source'], subject, out)
    return D.sort(out)


def load_manifest(raw, *, subject='<manifest>'):
    """A normalized ``bridge-mod-manifest`` from untrusted JSON. A missing ``manifest_id`` is computed."""
    doc, out = decode_json(raw, subject=subject)
    if doc is None:
        return None, out
    if 'manifest_id' not in doc:
        doc['manifest_id'] = sha256_json(doc)
    out = validate_manifest(doc)
    return (None if D.has_errors(out) else doc), out


def _caller_files_and_source(files, source, subject):
    """Validate the caller-supplied file list/source block before they reach ``make_manifest``."""
    problems = S.check(S.list_of(FILE_SPEC, max_items=MAX_FILES), files) + S.check(SOURCE_SPEC, source)
    if problems:
        return [diag('MANIFEST_SCHEMA_INVALID', 'error', 'file list or source block invalid', subject=subject,
                     details={'problems': _problems(problems)})]
    return []


def _finish(fields, subject, out):
    """Build + validate; return (manifest|None, diagnostics)."""
    if D.has_errors(out):
        return None, D.sort(out)
    try:
        manifest = make_manifest(**fields)
    except (TypeError, ValueError, KeyError) as exc:  # defensive: parsers validate first
        out.append(diag('MANIFEST_BUILD_FAILED', 'error', 'manifest could not be built', subject=subject,
                        details={'error': printable(str(exc), 160)}))
        return None, D.sort(out)
    out.extend(validate_manifest(manifest))
    return (None if D.has_errors(out) else manifest), D.sort(out)


# --------------------------------------------------------------------------------- SRML modinfo

def _text_field(doc, key, out, subject, *, required, pattern=TEXT_PATTERN, max_length=128):
    value = doc.get(key)
    if value is None:
        if required:
            out.append(diag('MODINFO_FIELD_MISSING', 'error', f'modinfo.json needs "{key}"', subject=subject,
                            details={'field': key}))
        return None
    if not isinstance(value, str) or len(value) > max_length or not re.fullmatch(pattern, value):
        out.append(diag('MODINFO_FIELD_INVALID', 'error', f'"{key}" has the wrong type or format', subject=subject,
                        details={'field': key}))
        return None
    return value


def _srml_id_list(doc, key, out, subject):
    value = doc.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_DEPENDENCIES:
        out.append(diag('MODINFO_FIELD_INVALID', 'error', f'"{key}" must be a bounded list', subject=subject,
                        details={'field': key}))
        return []
    ids = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(ID_PATTERN, item.lower()) or len(item) > 128:
            out.append(diag('MODINFO_FIELD_INVALID', 'error', f'"{key}" entry is not a mod id', subject=subject,
                            details={'field': key, 'entry': printable(item, 64)}))
            continue
        ids.append(item.lower())
    return ids


def _srml_dependencies(doc, out, subject):
    value = doc.get('dependencies')
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_DEPENDENCIES:
        out.append(diag('MODINFO_FIELD_INVALID', 'error', '"dependencies" must be a bounded list', subject=subject,
                        details={'field': 'dependencies'}))
        return []
    deps, assumed = [], False
    for item in value:
        match = _SRML_DEPENDENCY.fullmatch(item.strip()) if isinstance(item, str) and len(item) <= 200 else None
        dep_id = match.group(1).lower() if match else None
        if not match or not re.fullmatch(ID_PATTERN, dep_id):
            out.append(diag('MODINFO_DEPENDENCY_INVALID', 'error', 'dependency is not "<id>[ <version>]"',
                            subject=subject, details={'entry': printable(item, 64)}))
            continue
        constraint = '*'
        if match.group(2) is not None:
            constraint = parse_constraint(match.group(2), bare='>=')
            if constraint is None:
                out.append(diag('MODINFO_DEPENDENCY_INVALID', 'error', 'dependency version is not parseable',
                                subject=subject, details={'entry': printable(item, 64)}))
                continue
            assumed = True
        deps.append({'id': dep_id, 'kind': 'MOD', 'constraint': constraint, 'optional': False})
    if assumed:
        out.append(diag('SRML_DEPENDENCY_SEMANTICS_UNVERIFIED', 'info',
                        'a version after a dependency id is read as a minimum; SRML semantics unverified',
                        subject=subject, evidence='BLOCKED_ON_LOCAL_QUERY', details={'local_query': 'Q052'}))
    return deps


def parse_srml_modinfo(raw, *, source, files=()):
    """SR1 SRML ``modinfo.json`` -> manifest. ``source``/``files`` describe where it was read from."""
    subject = source.get('path') if isinstance(source, dict) else None
    out = _caller_files_and_source(list(files) if isinstance(files, (list, tuple)) else files, source, subject)
    if out:
        return None, out
    doc, out = decode_json(raw, subject=subject or 'modinfo.json')
    if doc is None:
        return None, out
    unknown = sorted(set(doc) - set(SRML_KNOWN_FIELDS))
    if unknown:
        out.append(diag('MODINFO_UNKNOWN_FIELD', 'warning', 'unrecognized modinfo.json fields are ignored',
                        subject=subject, details={'fields': [printable(k, 64) for k in unknown[:20]]}))
    native_id = _text_field(doc, 'id', out, subject, required=True)
    mod_id = native_id.lower() if native_id is not None else None
    if mod_id is not None and not re.fullmatch(ID_PATTERN, mod_id):
        out.append(diag('MOD_ID_INVALID', 'error', f'id must match {ID_PATTERN} (case-insensitive)', subject=subject))
    elif mod_id is not None and mod_id != native_id:
        out.append(diag('MOD_ID_NORMALIZED', 'info', 'id lower-cased for comparison', subject=subject,
                        details={'id': mod_id}))
    name = _text_field(doc, 'name', out, subject, required=False)
    if 'name' not in doc and native_id is not None:
        out.append(diag('MODINFO_NAME_MISSING', 'warning', 'no "name"; the id is used', subject=subject))
    version = _text_field(doc, 'version', out, subject, required=True, pattern=r'.{1,64}', max_length=64)
    match = _SRML_VERSION.fullmatch(version) if version is not None else None
    if version is not None and not match:
        out.append(diag('MODINFO_VERSION_INVALID', 'error',
                        'SRML accepts only X.Y[.Z] integer versions and rejects this mod at PreLoad',
                        subject=subject, details={'version': printable(version, 64)}, evidence='STATICALLY_VERIFIED'))
    author = _text_field(doc, 'author', out, subject, required=False)
    if 'description' in doc and not isinstance(doc['description'], str):
        out.append(diag('MODINFO_FIELD_INVALID', 'error', '"description" must be text', subject=subject,
                        details={'field': 'description'}))
    dependencies = _srml_dependencies(doc, out, subject)
    load_after = _srml_id_list(doc, 'load_after', out, subject)
    load_before = _srml_id_list(doc, 'load_before', out, subject)
    if load_after or load_before:
        out.append(diag('SRML_LOAD_ORDER_SEMANTICS_UNVERIFIED', 'info',
                        'load_after/load_before are kept as hints; SRML semantics unverified', subject=subject,
                        evidence='BLOCKED_ON_LOCAL_QUERY', details={'local_query': 'Q052'}))
    if D.has_errors(out):
        return None, D.sort(out)
    parts = [int(p) for p in match.groups() if p is not None]
    return _finish({'id': mod_id, 'native_id': native_id, 'name': name or native_id,
                    'version_text': '.'.join(map(str, parts)), 'version_parts': parts, 'realm': 'SR1',
                    'loader': 'SRML', 'source': source, 'authors': [author] if author else [],
                    'dependencies': dependencies, 'load_after': load_after, 'load_before': load_before,
                    'files': files}, subject, out)


# --------------------------------------------------------------------- MelonLoader provider metadata

def _slug(name):
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:128]
    return slug if re.fullmatch(ID_PATTERN, slug or '-') else None


def parse_melon_metadata(raw, *, realm_hint=None):
    """``bridge-melon-metadata`` v1 (LOCAL provider output) -> manifest.

    ``realm_hint`` is only used when the assembly declares no ``MelonGame`` (a universal Melon), e.g.
    because the provider found it in the SR2 lab's ``Mods/`` folder; that use is flagged.
    """
    doc, out = decode_json(raw, subject='bridge-melon-metadata')
    if doc is None:
        return None, out
    problems = S.check(_MELON_SPEC, doc)
    if problems:
        return None, [diag('MELON_METADATA_SCHEMA_INVALID', 'error', 'melon metadata schema violation',
                           details={'problems': _problems(problems)})]
    info = doc['melon_info']
    subject = doc['assembly']['file']['path']
    declared = sorted({MELON_GAME_REALMS.get((g['developer'], g['name'])) or 'UNSUPPORTED' for g in doc['melon_games']})
    if not doc['melon_games']:
        if realm_hint in REALMS:
            realm = realm_hint
            out.append(diag('MELON_GAME_UNDECLARED', 'warning', 'no MelonGame attribute; realm taken from the hint',
                            subject=subject, details={'realm_hint': realm_hint}))
        else:
            return None, [diag('MANIFEST_REALM_UNDETERMINED', 'error', 'no MelonGame attribute and no realm hint',
                               subject=subject)]
    elif declared != ['SR2']:
        return None, [diag('MELON_GAME_NOT_SUPPORTED', 'error', 'MelonGame does not name exactly the SR2 identity',
                           subject=subject, details={'games': [printable(f"{g['developer']}/{g['name']}", 80)
                                                               for g in doc['melon_games'][:4]]})]
    else:
        realm = 'SR2'
    if LOADER_REALM['MELONLOADER'] != realm:
        return None, [diag('MANIFEST_LOADER_REALM_MISMATCH', 'error', f'MelonLoader is not the {realm} loader',
                           subject=subject, details={'loader': 'MELONLOADER', 'realm': realm})]
    if doc['platform_domain'] == 'MONO':
        out.append(diag('MELON_DOMAIN_MISMATCH', 'warning', 'Mono-only Melon on an IL2CPP game (docs/RECON.md)',
                        subject=subject))
    if doc['declared_id'] is not None:
        mod_id = doc['declared_id']
    else:
        mod_id = _slug(info['name'])
        if mod_id is None:
            return None, [diag('MOD_ID_INVALID', 'error', 'no declared_id and MelonInfo name yields no id',
                               subject=subject)]
        out.append(diag('MOD_ID_DERIVED', 'info', 'id derived from the MelonInfo name', subject=subject,
                        details={'id': mod_id}))
    parts = parse_version(info['version'])
    if parts is None:
        out.append(diag('MELON_VERSION_UNPARSEABLE', 'warning', 'MelonInfo version has no numeric core',
                        subject=subject, details={'version': printable(info['version'], 64)}))
    version_text = '.'.join(map(str, parts)) if parts else 'unknown'
    dependencies = []
    for dep in doc['dependencies']:
        constraint = parse_constraint(dep['constraint'])
        dep_id = dep['id'].lower() if dep['kind'] == 'MOD' else dep['id']
        if constraint is None:
            out.append(diag('MANIFEST_DEPENDENCY_CONSTRAINT_INVALID', 'error', 'dependency constraint unparseable',
                            subject=subject, details={'dependency': printable(dep['id'], 128)}))
            continue
        dependencies.append({'id': dep_id, 'kind': dep['kind'], 'constraint': constraint, 'optional': dep['optional']})
    source = provider_source(doc['extractor']['id'], subject, synthetic=doc['synthetic'])
    return _finish({'id': mod_id, 'native_id': info['name'], 'name': info['name'], 'version_text': version_text,
                    'version_parts': parts or [], 'realm': realm, 'loader': 'MELONLOADER', 'source': source,
                    'authors': [info['author']] if info['author'] else [], 'dependencies': dependencies,
                    'game_versions': doc['game_versions'], 'provides_assemblies': [doc['assembly']['name']],
                    'files': [doc['assembly']['file'], *doc['additional_files']]}, subject, out)


def parse_manifest(fmt, raw, *, source=None, files=(), realm_hint=None):
    """Dispatch on ``fmt`` (one of ``FORMATS``). Never raises on input content."""
    if fmt == 'SRML_MODINFO':
        return parse_srml_modinfo(raw, source=source, files=files)
    if fmt == 'MELON_METADATA':
        return parse_melon_metadata(raw, realm_hint=realm_hint)
    if fmt == 'BRIDGE_MANIFEST':
        return load_manifest(raw)
    return None, [diag('MOD_FORMAT_UNKNOWN', 'error', 'unknown manifest format',
                       details={'format': printable(fmt, 64), 'known': list(FORMATS)})]
