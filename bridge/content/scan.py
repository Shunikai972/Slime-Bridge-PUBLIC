"""Normalize runtime registry snapshots and index existing static game metadata.

Static exports/catalogues are evidence of assets, not proof of active registries.
"""

import hashlib
import json
import re
from pathlib import Path


SCHEMA_VERSION = 1
IR_FIELDS = ('display_name_key', 'model_manifest', 'meshes', 'skinned_meshes', 'bones',
             'transforms', 'textures', 'materials', 'shaders', 'face_definition',
             'face_expression_assets', 'animator', 'animation_clips', 'audio_assets',
             'icon', 'slimepedia_entry', 'diet', 'plort', 'behaviour_components',
             'behaviour_capabilities', 'spawn_metadata', 'asset_hashes',
             'source_prefab_signature', 'compatibility_state')
_REF = re.compile(r'\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]+),\s*type:\s*\d+\}')
_GUID = re.compile(r'^guid:\s*([0-9a-fA-F]+)', re.MULTILINE)
_SR2_BASE = re.compile(r'^Assets/Actor/Slime/Base/Definition/([^/]+)\.asset$')
_SR2_LARGO = re.compile(r'^Assets/Actor/Slime/Largo/Definition/([^/]+)\.asset$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(name):
    name = re.sub(r'^(SlimeDefinition\.|IdentifiableType\.)', '', name)
    name = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name)
    name = re.sub(r'[^a-zA-Z0-9]+', '_', name).strip('_').lower()
    return re.sub(r'_slime$', '', name)


def _error(code, source, target=None):
    return {'code': code, 'source': source, 'target': target}


def _refs(text, field):
    match = re.search(r'^  ' + re.escape(field) + r':(?:\s*\[\])?\s*\n((?:  - .*\n)*)', text, re.MULTILINE)
    return [f'{guid.lower()}:{file_id}' for file_id, guid in _REF.findall(match.group(1))] if match else []


def _scalar(text, key):
    match = re.search(r'^  ' + re.escape(key) + r':\s*(.*?)\s*$', text, re.MULTILINE)
    return match.group(1) if match else None


def _produces(text):
    match = re.search(r'^    Produces:\s*([0-9a-fA-F]*)\s*$', text, re.MULTILINE)
    if not match:
        return []
    raw = bytes.fromhex(match.group(1))
    if len(raw) % 4:
        raise ValueError('Diet.Produces is not a sequence of 32-bit native IDs')
    return [int.from_bytes(raw[i:i + 4], 'little') for i in range(0, len(raw), 4)]


def _definition(*, name, native_id=None, reference_id=None, object_ref, is_largo=None,
                can_largofy=None, base_slimes=(), produced_plorts=(), prefab_refs=(),
                appearance_refs=(), evidence=(), canonical_id=None, canonical_evidence=(), content=None,
                asset_dependencies=(), dedicated_prefab_identity=None):
    return {'canonical_id': canonical_id, 'canonical_proven': bool(canonical_id and canonical_evidence),
            'canonical_evidence': list(canonical_evidence), 'native_id': native_id,
            'reference_id': reference_id, 'object_ref': object_ref, 'name': name,
            'is_largo': is_largo, 'can_largofy': can_largofy,
            'base_slimes': list(base_slimes), 'produced_plorts': list(produced_plorts),
            'prefab_refs': list(prefab_refs), 'appearance_refs': list(appearance_refs),
            'evidence': list(evidence), 'content': content or {},
            'asset_dependencies': list(asset_dependencies),
            'dedicated_prefab_identity': dedicated_prefab_identity}


def _sha(value):
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def checked_runtime_proof(scan):
    """Check recorded proof consistency; this does not attest a running game."""
    build = scan.get('build_proof') or {}
    traversal = scan.get('traversal_proof') or {}
    count = len(scan.get('definitions', []))
    refs = [row.get('object_ref') for row in scan.get('definitions', [])]
    return (scan.get('source_kind') == 'runtime_registry' and
            scan.get('completeness') == 'COMPLETE' and
            scan.get('registry_traversed') is True and not scan.get('errors') and
            scan.get('build_match') == 'VERIFIED' and bool(scan.get('game_build')) and
            _sha(build.get('active_sha256')) and
            build.get('active_sha256') == build.get('scanned_sha256') == build.get('verified_active_sha256') and
            isinstance(build.get('active_path'), str) and bool(build['active_path']) and
            isinstance(build.get('provenance'), str) and bool(build['provenance']) and
            isinstance(traversal.get('registry_identity'), str) and
            bool(traversal['registry_identity']) and _sha(traversal.get('registry_sha256')) and
            traversal.get('registry_sha256') == traversal.get('verified_registry_sha256') and
            traversal.get('registry_identity') == traversal.get('verified_registry_identity') and
            sorted(refs) == traversal.get('verified_object_refs') and
            type(traversal.get('expected_count')) is int and
            traversal['expected_count'] == traversal.get('observed_count') == count and
            len(set(refs)) == count and all(isinstance(ref, str) and ref for ref in refs))


def valid_dedicated_prefab(row, assets=()):
    identity = row.get('dedicated_prefab_identity') or {}
    matches = [asset for asset in assets if asset.get('object_ref') == identity.get('object_ref')]
    return (len(matches) == 1 and matches[0].get('kind') == 'prefab' and
            matches[0].get('sha256') == identity.get('sha256') and
            identity.get('kind') == 'prefab' and
            identity.get('definition_ref') == row.get('object_ref') and
            identity.get('object_ref') in row.get('prefab_refs', []) and
            isinstance(identity.get('object_ref'), str) and bool(identity['object_ref']) and
            _sha(identity.get('sha256')) and
            isinstance(identity.get('provenance'), str) and bool(identity['provenance']))


def _dependencies(value):
    if not isinstance(value, list):
        raise ValueError('asset_dependencies must be a list')
    for edge in value:
        if not isinstance(edge, dict) or any(not isinstance(edge.get(key), str) or not edge[key]
            for key in ('source_field', 'target_ref', 'target_kind', 'provenance')):
            raise ValueError('Asset dependency needs an explicit source field, typed target and provenance')
    return value


def read_runtime_snapshot(path: Path, *, active_build: Path | None = None,
                          traversal_anchor: dict | None = None) -> dict:
    """Validate recorded evidence; COMPLETE also needs a caller-selected build hash anchor."""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != SCHEMA_VERSION or data.get('realm') not in ('SR1', 'SR2'):
        raise ValueError('Unsupported runtime snapshot schema or realm')
    if data.get('completeness') not in ('COMPLETE', 'PARTIAL', 'FAILED'):
        raise ValueError('Invalid traversal completeness')
    if not isinstance(data.get('definitions'), list):
        raise ValueError('Definitions must be a list')
    if data['completeness'] == 'COMPLETE' and (data.get('registry_traversed') is not True or
        not data.get('game_build') or data.get('errors')):
        raise ValueError('COMPLETE requires traversal proof, build and no errors')
    assets = data.get('assets', [])
    if not isinstance(assets, list):
        raise ValueError('Assets must be a list')
    asset_refs = set()
    for asset in assets:
        if not isinstance(asset, dict) or any(not isinstance(asset.get(key), str) or not asset[key]
            for key in ('object_ref', 'kind', 'sha256')) or not _sha(asset['sha256']):
            raise ValueError('Asset needs typed identity and SHA-256')
        if asset['object_ref'] in asset_refs:
            raise ValueError('Duplicate asset identity')
        asset_refs.add(asset['object_ref'])
        _dependencies(asset.get('dependencies', []))
    definitions = []
    seen = set()
    required = {'native_id', 'reference_id', 'object_ref', 'name', 'is_largo',
                'can_largofy', 'base_slimes', 'produced_plorts', 'prefab_refs', 'evidence'}
    for row in data['definitions']:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError('Missing definition relationship fields')
        if not isinstance(row['object_ref'], str) or not row['object_ref'] or row['object_ref'] in seen:
            raise ValueError('Missing or duplicate object reference')
        seen.add(row['object_ref'])
        for field in ('base_slimes', 'produced_plorts', 'prefab_refs', 'evidence'):
            if not isinstance(row[field], list):
                raise ValueError(f'{field} must be a list')
        for field in ('base_slimes', 'produced_plorts', 'prefab_refs'):
            if any(type(ref) not in (int, str) or ref == '' for ref in row[field]):
                raise ValueError(f'{field} references must be native IDs or reference strings')
        if row['is_largo'] is not None and type(row['is_largo']) is not bool:
            raise ValueError('is_largo must be boolean or unknown')
        if row['can_largofy'] is not None and type(row['can_largofy']) is not bool:
            raise ValueError('can_largofy must be boolean or unknown')
        if data['completeness'] == 'COMPLETE' and (row['is_largo'] is None or
            (row['is_largo'] is False and row['can_largofy'] is None) or
            (row['is_largo'] is True and len(row['base_slimes']) != 2)):
            raise ValueError('COMPLETE has unresolved Largo relationships or eligibility')
        canonical = row.get('canonical_id')
        proof = row.get('canonical_evidence', [])
        if not isinstance(proof, list) or any(not isinstance(item, str) or not item for item in proof):
            raise ValueError('Canonical mapping evidence must be nonempty strings')
        if canonical is not None and (not isinstance(canonical, str) or
            not canonical.startswith('slime.')):
            raise ValueError('Invalid canonical slime ID')
        dependencies = _dependencies(row.get('asset_dependencies', []))
        prefab_identity = row.get('dedicated_prefab_identity')
        if prefab_identity is not None and not valid_dedicated_prefab(row, assets):
            raise ValueError('Dedicated prefab identity does not resolve to this definition')
        definitions.append(_definition(**{k: row[k] for k in required},
            canonical_id=canonical, canonical_evidence=proof,
            appearance_refs=row.get('appearance_refs', []),
            asset_dependencies=dependencies, dedicated_prefab_identity=prefab_identity,
            content={key: row[key] for key in IR_FIELDS if key in row}))
    proof = dict(data.get('build_proof') or {})
    # Never accept an attestation injected by the snapshot itself.
    proof.pop('verified_active_sha256', None)
    if active_build is not None:
        anchor = Path(active_build).resolve(strict=True)
        proof['verified_active_sha256'] = _hash(anchor)
        proof['active_path'] = str(anchor)
    traversal = dict(data.get('traversal_proof') or {})
    for field in ('verified_registry_sha256', 'verified_registry_identity', 'verified_object_refs'):
        traversal.pop(field, None)
    if traversal_anchor is not None:
        if (traversal_anchor.get('realm') != data['realm'] or
                traversal_anchor.get('active_sha256') != proof.get('verified_active_sha256')):
            raise ValueError('Traversal receipt is not bound to the selected realm/build')
        refs = traversal_anchor.get('object_refs')
        if (not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs) or
                len(set(refs)) != len(refs)):
            raise ValueError('Traversal receipt needs unique native object references')
        traversal.update(verified_registry_sha256=traversal_anchor.get('registry_sha256'),
                         verified_registry_identity=traversal_anchor.get('registry_identity'),
                         verified_object_refs=sorted(refs))
    result = {'schema_version': SCHEMA_VERSION, 'realm': data['realm'],
            'source_kind': 'runtime_registry', 'source_hash': _hash(path),
            'game_build': data.get('game_build'), 'build_match': data.get('build_match', 'UNVERIFIED'),
            'build_proof': proof, 'traversal_proof': traversal,
            'asset_inventory': data.get('asset_inventory'),
            'registry_traversed': data.get('registry_traversed', False),
            'completeness': data['completeness'],
            'definitions': sorted(definitions, key=lambda r: r['object_ref']),
            'assets': sorted(assets, key=lambda a: a['object_ref']),
            'errors': data.get('errors', [])}
    if result['completeness'] == 'COMPLETE' and not checked_runtime_proof(result):
        raise ValueError('COMPLETE requires matching active build hashes and checked registry identity/count')
    return result


def scan_sr1_export(assets_root: Path, game_build: str | None = None) -> dict:
    """Index the registry-linked AssetRipper YAML; build match remains unverified."""
    root = Path(assets_root)
    mono = root / 'MonoBehaviour'
    registry = mono / 'MainSlimeDefinitions.asset'
    text = registry.read_text(encoding='utf-8-sig')
    slimes = re.search(r'^  Slimes:\s*\n((?:  - .*\n)*)', text, re.MULTILINE)
    if not slimes:
        raise ValueError('MainSlimeDefinitions.Slimes missing')
    registered = [f'{guid.lower()}:{file_id}' for file_id, guid in _REF.findall(slimes.group(1))]
    by_guid = {}
    for meta in sorted(mono.glob('*.asset.meta')):
        found = _GUID.search(meta.read_text(encoding='utf-8-sig'))
        if found:
            by_guid[found.group(1).lower()] = meta.with_suffix('')
    definitions, errors = [], []
    fingerprint = hashlib.sha256(registry.read_bytes())
    for object_ref in registered:
        guid, _ = object_ref.split(':', 1)
        path = by_guid.get(guid)
        if path is None or not path.is_file():
            errors.append(_error('UNRESOLVED_DEFINITION', object_ref))
            continue
        body = path.read_text(encoding='utf-8-sig')
        fingerprint.update(object_ref.encode('ascii'))
        fingerprint.update(_hash(path).encode('ascii'))
        name = _scalar(body, 'm_Name') or path.stem
        raw_id = _scalar(body, 'IdentifiableId')
        is_largo = _scalar(body, 'IsLargo')
        can_largofy = _scalar(body, 'CanLargofy')
        bases = _refs(body, 'BaseSlimes')
        appearances = _refs(body, 'AppearancesDefault')
        try:
            produced = _produces(body)
        except ValueError:
            produced = []
            errors.append(_error('INVALID_PLORT_IDS', object_ref))
        definitions.append(_definition(name=name, native_id=int(raw_id) if raw_id is not None else None,
            object_ref=object_ref, is_largo=bool(int(is_largo)) if is_largo is not None else None,
            can_largofy=bool(int(can_largofy)) if can_largofy is not None else None,
            base_slimes=bases, produced_plorts=produced, appearance_refs=appearances,
            canonical_id=None if is_largo == '1' else 'slime.' + _slug(name),
            evidence=[f'AssetRipper:{path.relative_to(root).as_posix()}#sha256={_hash(path)}']))
    found_refs = {d['object_ref'] for d in definitions}
    appearance_refs = {ref for row in definitions for ref in row['appearance_refs']}
    assets = []
    for ref in sorted(appearance_refs):
        guid, _ = ref.split(':', 1)
        path = by_guid.get(guid)
        if path is None or not path.is_file():
            errors.append(_error('UNRESOLVED_APPEARANCE', ref))
            continue
        asset_hash = _hash(path)
        assets.append({'object_ref': ref, 'kind': 'appearance',
                       'asset_path': path.relative_to(root).as_posix(), 'sha256': asset_hash})
        fingerprint.update(ref.encode('ascii'))
        fingerprint.update(asset_hash.encode('ascii'))
    for row in definitions:
        for ref in row['base_slimes']:
            if ref not in found_refs:
                errors.append(_error('UNRESOLVED_BASE', row['object_ref'], ref))
        if row['is_largo'] and len(row['base_slimes']) != 2:
            errors.append(_error('INVALID_LARGO_PARENTS', row['object_ref']))
    return {'schema_version': SCHEMA_VERSION, 'realm': 'SR1', 'source_kind': 'asset_ripper_yaml',
            'source_hash': fingerprint.hexdigest(), 'game_build': game_build,
            'build_match': 'UNVERIFIED', 'registry_traversed': False,
            'completeness': 'PARTIAL', 'definitions': sorted(definitions, key=lambda r: r['object_ref']),
            'assets': assets,
            'registry_entry_count': len(registered), 'errors': sorted(errors, key=lambda e: (e['code'], e['source'], e['target'] or ''))}


def scan_sr2_catalog(path: Path) -> dict:
    """Index addressable definition paths only; names imply no parent or active status."""
    path = Path(path)
    catalog = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(catalog.get('m_InternalIds'), list):
        raise ValueError('Addressables m_InternalIds missing')
    definitions = []
    for asset in sorted(set(catalog['m_InternalIds'])):
        if not isinstance(asset, str):
            continue
        base, largo = _SR2_BASE.fullmatch(asset), _SR2_LARGO.fullmatch(asset)
        if not base and not largo:
            continue
        name = (base or largo).group(1)
        definitions.append(_definition(name=name, object_ref=asset, is_largo=bool(largo),
            canonical_id='slime.' + _slug(name) if base else None,
            evidence=[f'Addressables:{asset}']))
    return {'schema_version': SCHEMA_VERSION, 'realm': 'SR2', 'source_kind': 'addressables_catalog',
            'source_hash': _hash(path), 'game_build': catalog.get('m_BuildResultHash'),
            'build_match': 'UNVERIFIED', 'registry_traversed': False,
            'completeness': 'PARTIAL', 'definitions': definitions,
            'registry_entry_count': None,
            'errors': [_error('CATALOG_NOT_ACTIVE_REGISTRY', 'm_InternalIds')]}


def _content_dependencies(content):
    """Do not hide reference-bearing content when an older snapshot omits explicit edges."""
    kinds = {'model_manifest': 'model', 'meshes': 'mesh', 'skinned_meshes': 'mesh',
             'bones': 'transform', 'transforms': 'transform', 'textures': 'texture',
             'materials': 'material', 'shaders': 'shader', 'face_definition': 'face',
             'face_expression_assets': 'face_expression', 'animator': 'animator',
             'animation_clips': 'animation_clip', 'audio_assets': 'audio',
             'icon': 'texture', 'slimepedia_entry': 'slimepedia', 'plort': 'plort_definition'}
    kinds.update(mesh='mesh', skinned_mesh='mesh', material='material', texture='texture',
                 shader='shader', bone='transform', transform='transform', animation_clip='animation_clip')
    stack: list[tuple[object, str, str | None]] = [(content, 'content', None)]
    while stack:
        value, path, kind = stack.pop()
        if isinstance(value, dict):
            target = value.get('object_ref')
            if kind and isinstance(target, str) and target:
                yield {'source_field': path, 'target_ref': target, 'target_kind': kind,
                       'provenance': 'snapshot content reference; extraction unverified'}
            for key, child in sorted(value.items(), reverse=True):
                if key != 'object_ref':
                    child_kind = kinds.get(key, kind if isinstance(child, (dict, list)) else None)
                    stack.append((child, path + '.' + key, child_kind))
        elif isinstance(value, list):
            for index, child in reversed(list(enumerate(value))):
                stack.append((child, f'{path}[{index}]', kind))
        elif kind and isinstance(value, str) and value:
            yield {'source_field': path, 'target_ref': value, 'target_kind': kind,
                   'provenance': 'snapshot content reference; extraction unverified'}


def build_ir(scans: list[dict]) -> dict:
    """Neutral metadata graph with explicit unresolved dependency edges."""
    nodes, edges, errors = [], [], []
    for scan in scans:
        realm = scan['realm']
        assets = scan.get('assets', [])
        known = {row['object_ref']: 'slime_definition' for row in scan['definitions']}
        known.update({a['object_ref']: a['kind'] for a in assets})
        base_refs = {}
        for base in scan['definitions']:
            if base['is_largo'] is False:
                for ref in (base['object_ref'], base['native_id'], base['reference_id']):
                    if ref is not None:
                        bucket = base_refs.setdefault((type(ref).__name__, ref), [])
                        if base['object_ref'] not in bucket:
                            bucket.append(base['object_ref'])
        for asset in assets:
            node = {'id': realm + ':' + asset['object_ref'], 'kind': asset['kind'],
                    'realm': realm, 'sha256': asset['sha256']}
            if 'asset_path' in asset:
                node['asset_path'] = asset['asset_path']
            nodes.append(node)
            for dep in asset.get('dependencies', []):
                target_ref = dep['target_ref']
                target_id = realm + ':' + target_ref
                resolved = known.get(target_ref) == dep['target_kind']
                edges.append({'from': realm + ':' + asset['object_ref'],
                              'relation': dep['source_field'],
                              'to': target_id, 'resolved': resolved,
                              'target_kind': dep['target_kind'],
                              'provenance': dep['provenance']})
                if not resolved:
                    errors.append(_error('UNRESOLVED_EDGE', realm + ':' + asset['object_ref'], target_id))
                    if target_ref not in known and not any(n['id'] == target_id for n in nodes):
                        nodes.append({'id': target_id, 'kind': dep['target_kind'],
                                      'realm': realm, 'unresolved': True})
        for row in scan['definitions']:
            source = realm + ':' + row['object_ref']
            nodes.append({'id': source, 'kind': 'slime_definition', 'realm': realm,
                          'canonical_id': row['canonical_id'], 'canonical_proven': row['canonical_proven'],
                          'native_id': row['native_id'], 'reference_id': row['reference_id'],
                          'is_largo': row['is_largo'], 'can_largofy': row['can_largofy'],
                          'evidence': row['evidence'], 'content': row.get('content', {})})
            for field, relation in (('base_slimes', 'has_base'), ('produced_plorts', 'produces_plort'),
                                    ('prefab_refs', 'has_prefab'), ('appearance_refs', 'has_appearance')):
                for ref in row.get(field, []):
                    matches = base_refs.get((type(ref).__name__, ref), []) if field == 'base_slimes' else []
                    target = matches[0] if len(matches) == 1 else ref
                    target_kind = {'base_slimes': 'slime_definition', 'produced_plorts': 'plort_definition',
                                   'prefab_refs': 'prefab', 'appearance_refs': 'appearance'}[field]
                    resolved = len(matches) == 1 if field == 'base_slimes' else known.get(ref) == target_kind
                    edges.append({'from': source, 'relation': relation,
                                  'to': realm + ':' + str(target), 'resolved': resolved})
                    if not resolved:
                        errors.append(_error('UNRESOLVED_EDGE', source, realm + ':' + str(ref)))
            for dep in [*row.get('asset_dependencies', []),
                        *_content_dependencies(row.get('content', {}))]:
                target_ref = dep['target_ref']
                target_id = realm + ':' + target_ref
                resolved = known.get(target_ref) == dep['target_kind']
                edges.append({'from': source,
                              'relation': dep['source_field'],
                              'to': target_id, 'resolved': resolved,
                              'target_kind': dep['target_kind'],
                              'provenance': dep['provenance']})
                if not resolved:
                    errors.append(_error('UNRESOLVED_EDGE', source, target_id))
                    if target_ref not in known and not any(n['id'] == target_id for n in nodes):
                        nodes.append({'id': target_id, 'kind': dep['target_kind'],
                                      'realm': realm, 'unresolved': True})
        errors.extend(scan['errors'])
    return {'schema_version': SCHEMA_VERSION,
            'nodes': sorted(nodes, key=lambda n: n['id']),
            'edges': sorted(edges, key=lambda e: (e['from'], e['relation'], e['to'])),
            'errors': sorted(errors, key=lambda e: (e['code'], e['source'], e.get('target') or ''))}
