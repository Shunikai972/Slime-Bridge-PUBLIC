"""UserLargo external package contract: discover, validate, hash, version and plan. Read-only.

The user designs and models every Largo separately. This module never creates, edits,
re-encodes or copies their files: it opens them read-only to stream hashes, validates the
declared manifest against what is on disk, and produces metadata plans that reference files
by content hash. Packages live in a directory with ``largo-package.json``::

    {"schema": "bridge-user-largo-package", "schema_version": 1,
     "canonical_largo_id": "largo.<a>.<b>", "parents": ["slime.<a>", "slime.<b>"],
     "version": "1.0.0", "assets": [{"path": "rel/path", "kind": "mesh", "sha256": "...", "role": "body"}],
     "materials": [{"id": "body", "family": "slime_body", "render_state": {...},
                    "channels": {"albedo.texture": {"asset": "rel/tex"}, "gradient.top": {"value": [r,g,b,a]}}}],
     "renderers": [{"id": "body", "mesh": "rel/mesh", "materials": ["body"], "skinned": false}],
     "face": {"expressions": {"neutral": "rel/tex"}} | null, "animations": ["rel/clip"],
     "behaviour": {"compose": "union" | "parent_a" | "parent_b" | "explicit",
                   "capabilities": [...], "exclude": [...]},
     "targets": {"SR1": {...}, "SR2": {...}}, "provenance": {"author": "...", "license": "..."}}
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from bridge.content.canonical import REALMS, diag, has_errors, sha256_json, sort_diagnostics
from bridge.conversion.capabilities import CATALOG
from bridge.conversion.materials import ALPHA_MODES, CULL_MODES, SEMANTICS, _value, plan_target_material
from bridge.sdk.largo_metadata import validate as validate_import_metadata

PACKAGE_FILE = 'largo-package.json'
PACKAGE_SCHEMA = 'bridge-user-largo-package'
ASSET_KINDS = ('mesh', 'texture', 'animation_clip', 'audio', 'icon', 'vfx', 'material_library')
COMPOSE = ('union', 'parent_a', 'parent_b', 'explicit')
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_DEPTH = 6
_VERSION = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
_SLUG = r'[a-z0-9]+(?:_[a-z0-9]+)*'
_LARGO = re.compile(rf'^largo\.({_SLUG})\.({_SLUG})$')
_SLIME = re.compile(rf'^slime\.({_SLUG})$')
_KEYS = {'schema', 'schema_version', 'canonical_largo_id', 'parents', 'version', 'assets', 'materials',
         'renderers', 'face', 'animations', 'behaviour', 'targets', 'provenance'}
_OPTIONAL_KEYS = {'import_metadata'}


def _hash_file(path, limit, keep=False):
    """Stream a read-only SHA-256; never follows a final symlink (O_NOFOLLOW where available).

    With ``keep`` the bytes are also returned, so a small file is hashed and parsed from one read.
    """
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('not a regular file')
        if info.st_size > limit:
            raise ValueError(f'file larger than {limit} bytes')
        digest, kept, size = hashlib.sha256(), [], 0
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f'file grew beyond {limit} bytes while reading')
                digest.update(chunk)
                if keep:
                    kept.append(chunk)
        return digest.hexdigest(), size, b''.join(kept) if keep else None
    finally:
        os.close(fd)


def _safe_relative(root, rel):
    """Resolve a declared relative POSIX path inside root without following any symlink or junction."""
    if not isinstance(rel, str) or not rel or '\\' in rel or ':' in rel:
        return None
    pure = PurePosixPath(rel)
    # Only the canonical spelling is accepted ('a/./b', 'a//b' would alias another declared path).
    if pure.is_absolute() or pure.as_posix() != rel or any(part in ('', '.', '..') for part in pure.parts):
        return None
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            return None
    return current


def discover_packages(root):
    """Package directories under ``root`` (sorted, bounded depth, symlinks and junctions never followed)."""
    root = Path(root)
    found, stack = [], [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
            continue
        if (directory / PACKAGE_FILE).is_file() and not (directory / PACKAGE_FILE).is_symlink():
            found.append(directory)
            continue
        if depth < MAX_DEPTH:
            stack.extend((child, depth + 1) for child in sorted(directory.iterdir()) if child.is_dir())
    return sorted(found)


def _largo_id(parents):
    return 'largo.' + '.'.join(sorted(p.split('.', 1)[1] for p in parents))


def validate_package(package_dir, *, known_base_species=None, max_file_bytes=MAX_FILE_BYTES):
    """Validate one package read-only. Returns manifest, files, hash, status and diagnostics. Never raises."""
    try:
        return _validate(Path(package_dir), known_base_species, max_file_bytes)
    except Exception as exc:  # a hostile manifest shape must reject, never crash an import run
        return {'package_dir': str(package_dir), 'status': 'REJECTED', 'manifest': None, 'files': [],
                'package_hash': None, 'diagnostics': [diag('VALIDATION_EXCEPTION', 'error', Path(package_dir).name,
                                                           f'{type(exc).__name__}: {str(exc)[:160]}')]}


def _validate(root, known_base_species, max_file_bytes):
    diags, files = [], {}
    subject = str(root.name)
    manifest_path = root / PACKAGE_FILE
    try:
        if manifest_path.is_symlink():
            raise ValueError('manifest is a symlink')
        manifest_hash, _, raw = _hash_file(manifest_path, 4 * 1024 * 1024, keep=True)
        manifest = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError) as exc:
        return {'package_dir': str(root), 'status': 'REJECTED', 'manifest': None, 'files': [],
                'package_hash': None, 'diagnostics': [diag('MANIFEST_UNREADABLE', 'error', subject, str(exc)[:200])]}
    if not isinstance(manifest, dict) or manifest.get('schema') != PACKAGE_SCHEMA:
        diags.append(diag('WRONG_SCHEMA', 'error', subject, f'expected {PACKAGE_SCHEMA}'))
        manifest = manifest if isinstance(manifest, dict) else {}
    elif manifest.get('schema_version') != 1:
        diags.append(diag('SCHEMA_VERSION_UNSUPPORTED', 'error', subject, repr(manifest.get('schema_version'))))
    for key in sorted(set(manifest) - _KEYS - _OPTIONAL_KEYS):
        diags.append(diag('UNKNOWN_FIELD', 'error', subject, key))
    for key in sorted(_KEYS - set(manifest)):
        diags.append(diag('MISSING_FIELD', 'error', subject, key))
    if has_errors(diags):
        return {'package_dir': str(root), 'status': 'REJECTED', 'manifest': manifest, 'files': [],
                'package_hash': None, 'diagnostics': sort_diagnostics(diags)}
    parents = manifest['parents']
    if not isinstance(parents, list) or len(parents) != 2 or len(set(parents)) != 2 or \
            not all(isinstance(p, str) and _SLIME.fullmatch(p) for p in parents):
        diags.append(diag('INVALID_PARENTS', 'error', subject, 'two distinct canonical base slime ids'))
    elif manifest['canonical_largo_id'] != _largo_id(parents):
        diags.append(diag('LARGO_ID_MISMATCH', 'error', subject, f'expected {_largo_id(parents)}'))
    elif known_base_species is not None:
        for parent in parents:
            if parent not in known_base_species:
                diags.append(diag('PARENT_UNKNOWN', 'error', subject, parent))
        diags.append(diag('PARENT_ELIGIBILITY_UNVERIFIED', 'warning', subject,
                          'Largo eligibility needs runtime-anchored registry evidence'))
    else:
        diags.append(diag('PARENTS_UNCHECKED', 'warning', subject, 'no reconciled base species supplied'))
    if not isinstance(manifest['version'], str) or not _VERSION.fullmatch(manifest['version']):
        diags.append(diag('INVALID_VERSION', 'error', subject, 'MAJOR.MINOR.PATCH'))
    declared = {}
    for index, asset in enumerate(manifest['assets'] if isinstance(manifest['assets'], list) else []):
        where = f'{subject}:assets[{index}]'
        if not isinstance(asset, dict) or asset.get('kind') not in ASSET_KINDS:
            diags.append(diag('INVALID_ASSET', 'error', where, 'kind'))
            continue
        rel = asset.get('path')
        path = _safe_relative(root, rel)
        if path is None:
            diags.append(diag('UNSAFE_PATH', 'error', where, repr(rel)))
            continue
        if rel in declared:
            diags.append(diag('DUPLICATE_ASSET', 'error', where, rel))
            continue
        try:
            digest, size, _ = _hash_file(path, max_file_bytes)
        except (OSError, ValueError) as exc:
            diags.append(diag('ASSET_UNREADABLE', 'error', where, f'{rel}: {str(exc)[:120]}'))
            continue
        if asset.get('sha256') != digest:
            diags.append(diag('ASSET_HASH_MISMATCH', 'error', where, rel))
        declared[rel] = asset['kind']
        files[rel] = {'path': rel, 'kind': asset['kind'], 'sha256': digest, 'size': size,
                      'role': asset.get('role')}
    if not isinstance(manifest['assets'], list):
        diags.append(diag('INVALID_ASSET', 'error', subject, 'assets must be a list'))
    on_disk = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            rel = Path(dirpath, name).relative_to(root).as_posix()
            if rel != PACKAGE_FILE:
                on_disk.add(rel)
    for rel in sorted(on_disk - set(declared)):
        diags.append(diag('UNDECLARED_FILE', 'warning', subject, rel))
    _check_references(manifest, declared, subject, diags)
    diags.extend(validate_import_metadata(manifest, declared, subject))
    package_hash = sha256_json({'manifest_sha256': manifest_hash,
                                'files': sorted((f['path'], f['sha256']) for f in files.values())})
    status = 'REJECTED' if has_errors(diags) else 'VALID'
    return {'package_dir': str(root), 'status': status, 'manifest': manifest, 'files': sorted(
        files.values(), key=lambda f: f['path']), 'package_hash': package_hash if status == 'VALID' else None,
        'diagnostics': sort_diagnostics(diags)}


def _check_references(manifest, declared, subject, diags):
    def need(rel, kind, where):
        if not isinstance(rel, str) or declared.get(rel) != kind:
            diags.append(diag('DANGLING_ASSET_REFERENCE', 'error', where, f'{rel!r} is not a declared {kind}'))

    materials = manifest['materials'] if isinstance(manifest['materials'], list) else []
    ids = set()
    for index, material in enumerate(materials):
        where = f'{subject}:materials[{index}]'
        if not isinstance(material, dict) or not isinstance(material.get('id'), str) or material['id'] in ids:
            diags.append(diag('INVALID_MATERIAL', 'error', where, 'unique string id required'))
            continue
        ids.add(material['id'])
        state = material.get('render_state', {})
        if not isinstance(material.get('family'), str) or not isinstance(state, dict) or \
                state.get('alpha_mode', 'OPAQUE') not in ALPHA_MODES or state.get('cull', 'BACK') not in CULL_MODES:
            diags.append(diag('INVALID_MATERIAL', 'error', where, 'family/render_state'))
        channels = material.get('channels')
        if not isinstance(channels, dict):
            diags.append(diag('INVALID_MATERIAL', 'error', where, 'channels must be an object'))
            continue
        for semantic, channel in channels.items():
            if semantic not in SEMANTICS or not isinstance(channel, dict):
                diags.append(diag('UNKNOWN_SEMANTIC', 'error', where, str(semantic)))
            elif SEMANTICS[semantic] == 'texture':
                need(channel.get('asset'), 'texture', where)
            elif _value(SEMANTICS[semantic], channel.get('value')) is None:
                diags.append(diag('INVALID_PROPERTY_VALUE', 'error', where, semantic))
    renderers = manifest['renderers'] if isinstance(manifest['renderers'], list) else []
    if not renderers:
        diags.append(diag('NOT_RENDERABLE', 'error', subject, 'at least one renderer with a mesh'))
    for index, renderer in enumerate(renderers):
        where = f'{subject}:renderers[{index}]'
        if not isinstance(renderer, dict):
            diags.append(diag('INVALID_RENDERER', 'error', where))
            continue
        need(renderer.get('mesh'), 'mesh', where)
        slots = renderer.get('materials', [])
        if not isinstance(slots, list) or not all(isinstance(m, str) for m in slots) or \
                type(renderer.get('skinned', False)) is not bool:
            diags.append(diag('INVALID_RENDERER', 'error', where, 'materials: list of ids; skinned: bool'))
            continue
        for mid in slots:
            if mid not in ids:
                diags.append(diag('DANGLING_MATERIAL', 'error', where, str(mid)))
    face = manifest['face']
    if face is not None:
        expressions = face.get('expressions') if isinstance(face, dict) else None
        if not isinstance(expressions, dict):
            diags.append(diag('INVALID_FACE', 'error', subject, 'face.expressions must map names to textures'))
        for name, rel in (expressions.items() if isinstance(expressions, dict) else []):
            need(rel, 'texture', f'{subject}:face.{name}')
    animations = manifest['animations']
    if not isinstance(animations, list):
        diags.append(diag('INVALID_ANIMATIONS', 'error', subject, 'animations must be a list'))
    for rel in animations if isinstance(animations, list) else []:
        need(rel, 'animation_clip', f'{subject}:animations')
    behaviour = manifest['behaviour']
    lists = [behaviour.get(k, []) for k in ('capabilities', 'exclude')] if isinstance(behaviour, dict) else []
    if not isinstance(behaviour, dict) or behaviour.get('compose') not in COMPOSE or \
            not all(isinstance(v, list) and all(isinstance(c, str) for c in v) for v in lists):
        diags.append(diag('INVALID_BEHAVIOUR', 'error', subject, f'compose in {COMPOSE}; capability lists of ids'))
    else:
        for cap in [*lists[0], *lists[1]]:
            if cap not in CATALOG:
                diags.append(diag('UNKNOWN_CAPABILITY', 'error', subject, str(cap)))
    targets = manifest['targets']
    if not isinstance(targets, dict) or any(realm not in REALMS for realm in targets):
        diags.append(diag('INVALID_TARGETS', 'error', subject, 'keys must be SR1/SR2'))


def compare_version(ledger_entry, validated):
    """Decide whether a validated package may replace what was imported before."""
    if ledger_entry is None:
        return {'decision': 'NEW', 'diagnostics': []}
    old = tuple(int(x) for x in ledger_entry['version'].split('.'))
    new = tuple(int(x) for x in validated['manifest']['version'].split('.'))
    same_content = ledger_entry['package_hash'] == validated['package_hash']
    cid = validated['manifest']['canonical_largo_id']
    if new == old:
        if same_content:
            return {'decision': 'UNCHANGED', 'diagnostics': []}
        return {'decision': 'REFUSED', 'diagnostics': [diag('VERSION_NOT_BUMPED', 'error', cid,
                                                            'content changed without a version bump')]}
    if new < old:
        return {'decision': 'REFUSED', 'diagnostics': [diag('VERSION_REGRESSION', 'error', cid,
                                                            f"{validated['manifest']['version']} < {ledger_entry['version']}")]}
    if same_content:
        return {'decision': 'UPGRADE', 'diagnostics': [diag('VERSION_BUMP_WITHOUT_CHANGE', 'info', cid)]}
    return {'decision': 'UPGRADE', 'diagnostics': []}


def compose_capabilities(manifest, parent_capabilities):
    """Behaviour composition from parents' canonical capabilities (dict parent -> [ids])."""
    behaviour = manifest['behaviour']
    a, b = manifest['parents']
    pool = {'union': set(parent_capabilities.get(a, [])) | set(parent_capabilities.get(b, [])),
            'parent_a': set(parent_capabilities.get(a, [])), 'parent_b': set(parent_capabilities.get(b, [])),
            'explicit': set()}[behaviour['compose']]
    chosen = (pool | set(behaviour.get('capabilities', []))) - set(behaviour.get('exclude', []))
    chosen |= {'standard.vacpack'}
    return [{'id': cap, 'policy': CATALOG[cap]['policy'] if cap in CATALOG else 'UNKNOWN',
             'sources': ['largo-package'], 'evidence': f"compose={behaviour['compose']}"}
            for cap in sorted(chosen)]


def plan_import(validated, target_profile, parent_capabilities, *, target_native=frozenset(), target_parents=None):
    """Metadata conversion/registration plan for one valid package and one target realm.

    ``target_native``: canonical ids native in the target (a native Largo is never re-registered).
    ``target_parents``: base species available in the target (native or converted); None means
    unknown, which fails closed to PENDING_ADAPTER.
    """
    from bridge.conversion.capabilities import plan_behaviour
    manifest = validated['manifest']
    cid = manifest['canonical_largo_id'] if manifest else None
    if validated['status'] != 'VALID':
        return {'canonical_largo_id': cid, 'final': 'REJECTED', 'diagnostics': validated['diagnostics']}
    if cid in target_native:
        return {'canonical_largo_id': cid, 'final': 'REFUSED', 'target_realm': target_profile['realm'],
                'diagnostics': [diag('WOULD_DUPLICATE_NATIVE', 'error', cid, 'Largo is native in the target')]}
    reasons = []
    if target_parents is None:
        reasons.append('PARENT_AVAILABILITY_UNKNOWN')
    else:
        reasons += [f'PARENT_NOT_IN_TARGET:{p}' for p in manifest['parents'] if p not in target_parents]
    by_path = {f['path']: f for f in validated['files']}
    plans = []
    for material in manifest['materials']:
        channels = {}
        for semantic, channel in material['channels'].items():
            if SEMANTICS[semantic] == 'texture':
                channels[semantic] = {'kind': 'texture', 'texture': channel['asset'],
                                      'texture_hash': by_path[channel['asset']]['sha256'],
                                      'uv_set': channel.get('uv_set', 0), 'required': semantic.startswith('albedo.'),
                                      'source_property': 'largo-package'}
            else:
                channels[semantic] = {'kind': SEMANTICS[semantic], 'value': _value(SEMANTICS[semantic], channel['value']),
                                      'required': False, 'source_property': 'largo-package'}
        semantic = {'id': f"{cid}:{material['id']}", 'family': material['family'], 'channels': channels,
                    'render_state': {'alpha_mode': material.get('render_state', {}).get('alpha_mode', 'OPAQUE'),
                                     'cull': material.get('render_state', {}).get('cull', 'BACK'),
                                     'zwrite': material.get('render_state', {}).get('zwrite', True)},
                    'status': 'READY', 'diagnostics': []}
        plans.append(plan_target_material(semantic, target_profile['material_profile']))
    behaviour = plan_behaviour(compose_capabilities(manifest, parent_capabilities), target_profile['behaviour_profile'])
    rank = {'READY': 0, 'DEGRADED': 1, 'PENDING_ADAPTER': 2, 'QUARANTINED': 3}
    material_status = max((p['status'] for p in plans), key=rank.__getitem__, default='READY')
    if material_status in ('PENDING_ADAPTER', 'QUARANTINED'):
        reasons.append('MATERIAL_PENDING_ADAPTER')
    if behaviour['blocking']:
        reasons.append('BEHAVIOUR_PENDING_ADAPTER')
    final = 'PENDING_ADAPTER' if reasons else 'EXPERIMENTAL'
    artifacts = [{'artifact': f"user/{f['sha256']}", 'path': f['path'], 'kind': f['kind'], 'sha256': f['sha256'],
                  'read_only_source': True} for f in validated['files']]
    plan = {'schema': 'bridge-user-largo-plan', 'schema_version': 1, 'canonical_largo_id': cid,
            'parents': list(manifest['parents']), 'version': manifest['version'],
            'package_hash': validated['package_hash'], 'target_realm': target_profile['realm'],
            'artifacts': artifacts, 'materials': plans, 'behaviour': behaviour,
            'registration': [{'kind': 'REGISTER_SLIME_DEFINITION', 'role': 'largo', 'canonical_id': cid,
                              'parents': list(manifest['parents']), 'package_hash': validated['package_hash'],
                              'native_api': target_profile['registration']['REGISTER_SLIME_DEFINITION']['api'],
                              'local_query': target_profile['registration']['REGISTER_SLIME_DEFINITION'].get('local_query')}],
            'final': final, 'reasons': sorted(reasons), 'registrable': final == 'EXPERIMENTAL',
            'evidence': 'NEEDS_LOCAL_RUNTIME_PROOF',
            'diagnostics': sort_diagnostics([d for p in plans for d in p['diagnostics']] + behaviour['diagnostics'])}
    if 'import_metadata' in manifest:
        plan['import_metadata'] = manifest['import_metadata']
    plan['plan_hash'] = sha256_json(plan)
    return plan


def import_packages(root, *, ledger, known_base_species=None, target_profiles=(), parent_capabilities=None,
                    target_context=None):
    """Discover + validate + version-check + plan every package under ``root``.

    Pure with respect to the user's files: returns the updated ledger for the caller to persist
    (under ignored Lab/GeneratedContent), never writes into a package directory. Two packages
    declaring the same Largo are both refused (folder names must never choose what ships).
    ``target_context`` maps a realm to {'native': ids, 'parents': available base species}.
    """
    validated = [validate_package(d, known_base_species=known_base_species) for d in discover_packages(root)]
    declared = {}
    for item in validated:
        if item['status'] == 'VALID':
            declared.setdefault(item['manifest']['canonical_largo_id'], []).append(item['package_dir'])
    records, new_ledger = [], dict(ledger)
    for item in validated:
        cid = (item['manifest'] or {}).get('canonical_largo_id') if isinstance(item['manifest'], dict) else None
        record = {'package_dir': item['package_dir'], 'status': item['status'], 'canonical_largo_id': cid,
                  'package_hash': item['package_hash'], 'diagnostics': item['diagnostics'], 'decision': None,
                  'plans': []}
        if item['status'] == 'VALID' and len(declared[cid]) > 1:
            record['decision'] = 'REFUSED'
            record['diagnostics'] = sort_diagnostics(record['diagnostics'] + [
                diag('DUPLICATE_LARGO_PACKAGE', 'error', cid, ', '.join(sorted(declared[cid])))])
        elif item['status'] == 'VALID':
            version = compare_version(ledger.get(cid), item)
            record['decision'] = version['decision']
            record['diagnostics'] = sort_diagnostics(record['diagnostics'] + version['diagnostics'])
            if version['decision'] in ('NEW', 'UPGRADE', 'UNCHANGED'):
                new_ledger[cid] = {'version': item['manifest']['version'], 'package_hash': item['package_hash']}
                for profile in target_profiles:
                    context = (target_context or {}).get(profile['realm'])
                    try:
                        record['plans'].append(plan_import(
                            item, profile, parent_capabilities or {},
                            target_native=set(context['native']) if context else frozenset(),
                            target_parents=set(context['parents']) if context else None))
                    except Exception as exc:  # isolate a planning bug to this package/target
                        record['plans'].append({'canonical_largo_id': cid, 'final': 'REJECTED',
                                                'target_realm': profile['realm'], 'diagnostics': [diag(
                                                    'PLAN_EXCEPTION', 'error', cid, f'{type(exc).__name__}')]})
        records.append(record)
    return {'records': records, 'ledger': dict(sorted(new_ledger.items()))}


def target_context(classification, target_realm, batch_report=None):
    """Largo planning context: target natives plus base species available there (native or converted)."""
    native = {row['canonical_id'] for bucket in ('species', 'plorts') for row in classification[bucket]
              if row['present'].get(target_realm)}
    parents = {row['canonical_id'] for row in classification['species'] if row['present'].get(target_realm)}
    if batch_report is not None:
        parents |= {e['canonical_id'] for e in batch_report['entries']
                    if e['conversion'] == 'CONVERTED' and e['final'] == 'EXPERIMENTAL'}
    return {'native': sorted(native), 'parents': sorted(parents)}
