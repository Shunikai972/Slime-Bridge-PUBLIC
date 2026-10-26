"""``bridge-mod-static-evidence`` v1: what a LOCAL static analyzer saw inside one mod (untrusted).

the local provider produces this document on the local machine with a metadata reader (for example a
Mono.Cecil-style reader over the mod's own assemblies). The reader never executes the mod, never
loads it into a game, and never ships decompiled code: the document holds names and counts only.
Cloud validates it strictly and never trusts it beyond what it states.

Shape (every list bounded; every string printable; paths are safe relative paths)::

    schema/schema_version     'bridge-mod-static-evidence' / 1
    mod_id                    mod id the evidence describes (``bridge-mod-manifest.id``)
    manifest_id               optional sha256 binding to one exact manifest
    extractor {id, version}   the local analyzer that produced it
    synthetic                 true for fixtures; synthetic evidence never supports a native claim
    code.assemblies[]         {path, sha256, size, assembly_name}: the mod's own managed assemblies
    references[]              {assembly, types[], members[]}: one entry per referenced assembly;
                              members are 'Full.Type.Name::Member' and their type must be listed
    harmony                   {patch_all_calls, patches[{target, prefix, postfix, transpiler, finalizer}]}
    native                    {pinvoke_modules[], detour_indicators[], function_pointer_hooks}
    content                   {asset_bundles[file], content_files[file], content_ids[]}
    config_files[]            files the mod reads as configuration
    reflection                {type_lookup_by_name, non_public_access, dynamic_invoke, emit}
    il2cpp                    {interop_references, type_injection, il2cpp_type_references}

Binding rules (``bind``): the evidence must name the manifest's mod id (and manifest_id when set);
every file it describes must be a manifest file with the same sha256; every manifest file that is
an assembly (``.dll``/``.exe``) must be covered by ``code.assemblies``. Anything else makes the
evidence unusable for classification (the analyzer then reports UNKNOWN).
"""

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D
from bridge.core import schema as S
from bridge.core.paths import safe_relative

from .manifest import ASSEMBLY_PATTERN, FILE_SPEC, ID_PATTERN, MAX_FILE_SIZE, PROVIDER_ID_PATTERN, SEMVER_PATTERN, \
    decode_json, diag, printable

EVIDENCE_SCHEMA = 'bridge-mod-static-evidence'
SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_REFERENCE_GROUPS = 256
MAX_TYPES_PER_GROUP = 2048
MAX_MEMBERS_PER_GROUP = 4096
MAX_REFERENCE_STRINGS = 20000
MAX_PATCHES = 1024
MAX_FILES = 2048
MAX_CONTENT_IDS = 4096
MAX_COUNT = 10 ** 6
CODE_SUFFIXES = ('.dll', '.exe')

NAME_PATTERN = r'[^\x00-\x1f\x7f\ud800-\udfff:]{1,256}'
MEMBER_PATTERN = r'[^\x00-\x1f\x7f\ud800-\udfff:]{1,256}::[^\x00-\x1f\x7f\ud800-\udfff]{1,256}'
NATIVE_MODULE_PATTERN = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}'
INDICATOR_PATTERN = r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}'
CONTENT_ID_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}'
TARGET_PATTERN = f'(?:{NAME_PATTERN})|(?:{MEMBER_PATTERN})'

_COUNT = S.integer(minimum=0, maximum=MAX_COUNT)
_SPEC = S.obj({
    'schema': S.enum(EVIDENCE_SCHEMA), 'schema_version': S.enum(SCHEMA_VERSION),
    'mod_id': S.string(ID_PATTERN, max_length=128), 'manifest_id': S.optional(S.sha256()),
    'extractor': S.obj({'id': S.string(PROVIDER_ID_PATTERN, max_length=128), 'version': S.string(SEMVER_PATTERN)}),
    'synthetic': S.boolean(),
    'code': S.obj({'assemblies': S.list_of(S.obj({
        'path': S.string(max_length=1024), 'sha256': S.sha256(), 'size': S.integer(minimum=0, maximum=MAX_FILE_SIZE),
        'assembly_name': S.string(ASSEMBLY_PATTERN, max_length=128)}), max_items=64)}),
    'references': S.list_of(S.obj({
        'assembly': S.string(ASSEMBLY_PATTERN, max_length=128),
        'types': S.list_of(S.string(NAME_PATTERN, max_length=256), max_items=MAX_TYPES_PER_GROUP, unique=True),
        'members': S.list_of(S.string(MEMBER_PATTERN, max_length=514), max_items=MAX_MEMBERS_PER_GROUP,
                             unique=True)}), max_items=MAX_REFERENCE_GROUPS),
    'harmony': S.obj({'patch_all_calls': _COUNT, 'patches': S.list_of(S.obj({
        'target': S.string(TARGET_PATTERN, max_length=514),
        'prefix': _COUNT, 'postfix': _COUNT, 'transpiler': _COUNT, 'finalizer': _COUNT}), max_items=MAX_PATCHES)}),
    'native': S.obj({
        'pinvoke_modules': S.list_of(S.string(NATIVE_MODULE_PATTERN, max_length=128), max_items=64, unique=True),
        'detour_indicators': S.list_of(S.string(INDICATOR_PATTERN, max_length=128), max_items=64, unique=True),
        'function_pointer_hooks': S.boolean()}),
    'content': S.obj({'asset_bundles': S.list_of(FILE_SPEC, max_items=MAX_FILES),
                      'content_files': S.list_of(FILE_SPEC, max_items=MAX_FILES),
                      'content_ids': S.list_of(S.string(CONTENT_ID_PATTERN, max_length=160), max_items=MAX_CONTENT_IDS,
                                               unique=True)}),
    'config_files': S.list_of(FILE_SPEC, max_items=MAX_FILES),
    'reflection': S.obj({'type_lookup_by_name': S.boolean(), 'non_public_access': S.boolean(),
                         'dynamic_invoke': S.boolean(), 'emit': S.boolean()}),
    'il2cpp': S.obj({'interop_references': S.boolean(), 'type_injection': S.boolean(),
                     'il2cpp_type_references': _COUNT}),
})


def described_files(evidence):
    """Every file a (schema-valid) evidence document describes, as (field, file) pairs."""
    return ([('code.assemblies', f) for f in evidence['code']['assemblies']] +
            [('content.asset_bundles', f) for f in evidence['content']['asset_bundles']] +
            [('content.content_files', f) for f in evidence['content']['content_files']] +
            [('config_files', f) for f in evidence['config_files']])


def validate_evidence(evidence):
    """Unified diagnostics for an untrusted evidence document. Never raises."""
    problems = S.check(_SPEC, evidence)
    subject = evidence.get('mod_id') if isinstance(evidence, dict) and isinstance(evidence.get('mod_id'), str) else None
    if problems:
        return [diag('EVIDENCE_SCHEMA_INVALID', 'error', 'static evidence schema violation', subject=subject,
                     details={'problems': [printable(p, 200) for p in problems[:20]]})]
    out = []

    def bad(code, message, **details):
        out.append(diag(code, 'error', message, subject=subject, details=details or None))

    folded = {}
    for field, entry in described_files(evidence):
        if not safe_relative(entry['path']):
            bad('EVIDENCE_PATH_UNSAFE', 'unsafe relative path', field=field, path=printable(entry['path'], 200))
        folded.setdefault(entry['path'].casefold(), []).append(field)
    for path, fields in sorted(folded.items()):
        if len(fields) > 1:
            bad('EVIDENCE_FILE_DUPLICATE', 'a file is described twice (case-insensitive)', path=printable(path, 200),
                fields=sorted(fields))
    seen, total = set(), 0
    for group in evidence['references']:
        name = group['assembly'].casefold()
        if name in seen:
            bad('EVIDENCE_REFERENCE_DUPLICATE', 'assembly listed twice', assembly=group['assembly'])
        seen.add(name)
        total += len(group['types']) + len(group['members'])
        types = set(group['types'])
        stray = sorted({m.split('::', 1)[0] for m in group['members']} - types)
        if stray:
            bad('EVIDENCE_MEMBER_TYPE_UNLISTED', 'member of a type the group does not list',
                assembly=group['assembly'], types=[printable(t, 120) for t in stray[:5]])
    if total > MAX_REFERENCE_STRINGS:
        bad('EVIDENCE_TOO_LARGE', 'too many referenced names', count=total, limit=MAX_REFERENCE_STRINGS)
    for index, patch in enumerate(evidence['harmony']['patches']):
        if not any(patch[k] for k in ('prefix', 'postfix', 'transpiler', 'finalizer')):
            bad('EVIDENCE_HARMONY_PATCH_EMPTY', 'a Harmony patch entry has no patch method', index=index)
    own = {a['assembly_name'].casefold() for a in evidence['code']['assemblies']}
    if len(own) != len(evidence['code']['assemblies']):
        bad('EVIDENCE_ASSEMBLY_DUPLICATE', 'two code assemblies share an assembly name')
    if not evidence['code']['assemblies'] and code_indicators(evidence):
        bad('EVIDENCE_CODE_INDICATORS_WITHOUT_CODE', 'code indicators but no analyzed assembly',
            indicators=code_indicators(evidence))
    return D.sort(out)


def code_indicators(evidence):
    """Names of the code-only sections that are non-empty (they need at least one analyzed assembly)."""
    native, il2cpp = evidence['native'], evidence['il2cpp']
    found = {'references': bool(evidence['references']),
             'harmony': bool(evidence['harmony']['patches'] or evidence['harmony']['patch_all_calls']),
             'native': bool(native['pinvoke_modules'] or native['detour_indicators'] or
                            native['function_pointer_hooks']),
             'reflection': any(evidence['reflection'].values()),
             'il2cpp': bool(il2cpp['interop_references'] or il2cpp['type_injection'] or
                            il2cpp['il2cpp_type_references'])}
    return sorted(k for k, v in found.items() if v)


def load_evidence(raw):
    """``(evidence|None, diagnostics)`` from untrusted JSON (bytes, text or parsed)."""
    doc, out = decode_json(raw, subject='bridge-mod-static-evidence', max_bytes=MAX_EVIDENCE_BYTES)
    if doc is None:
        return None, out
    out = validate_evidence(doc)
    return (None if D.has_errors(out) else doc), out


def evidence_hash(evidence):
    return sha256_json(evidence)


def bind(manifest, evidence):
    """Diagnostics explaining why schema-valid ``evidence`` does not describe ``manifest`` (empty = bound)."""
    out = []
    subject = manifest['id']

    def bad(code, message, **details):
        out.append(diag(code, 'warning', message, subject=subject, details=details or None))

    if evidence['mod_id'].casefold() != manifest['id'].casefold():
        bad('EVIDENCE_MOD_MISMATCH', 'evidence describes another mod', evidence_mod=evidence['mod_id'])
    if evidence['manifest_id'] is not None and evidence['manifest_id'] != manifest['manifest_id']:
        bad('EVIDENCE_MANIFEST_MISMATCH', 'evidence is bound to another manifest version')
    files = {f['path'].casefold(): f for f in manifest['files']}
    for field, entry in described_files(evidence):
        listed = files.get(entry['path'].casefold())
        if listed is None:
            bad('EVIDENCE_FILE_UNLISTED', 'evidence describes a file the manifest does not ship', field=field,
                path=printable(entry['path'], 200))
        elif listed['sha256'] != entry['sha256']:
            bad('EVIDENCE_STALE', 'file hash differs from the manifest', field=field,
                path=printable(entry['path'], 200))
    covered = {a['path'].casefold() for a in evidence['code']['assemblies']}
    missing = sorted(f['path'] for f in manifest['files']
                     if f['path'].casefold().endswith(CODE_SUFFIXES) and f['path'].casefold() not in covered)
    if missing:
        bad('EVIDENCE_INCOMPLETE', 'manifest assemblies were not analyzed',
            paths=[printable(p, 200) for p in missing[:8]])
    return D.sort(out)
