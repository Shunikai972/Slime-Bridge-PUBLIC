"""Canonical Bridge slime IR (``bridge-slime-ir``): one realm-neutral description per species.

The IR is metadata only: identities, typed references and hashes. Asset bytes, geometry,
pixels and localized text never enter it. Validation is strict (unknown fields are errors);
forward compatibility goes through namespaced ``extensions`` and explicit, versioned
migrations. ``canonicalize_ir`` sorts set-like lists so hashing is order independent, but
never reorders semantic sequences (material slots, bone order).
"""

import copy
import re

from .canonical import REALMS, DataEvidence, Status, canonical_json, diag, is_sha256, sha256_json, sort_diagnostics

IR_SCHEMA = 'bridge-slime-ir'
IR_SCHEMA_VERSION = 1
MIGRATIONS: dict = {}  # {from_version: callable(doc) -> doc at from_version + 1}

IDENTITY_BASES = ('NATIVE_EVIDENCE', 'ALIAS_MANIFEST', 'DERIVED')
CAPABILITY_POLICIES = ('REQUIRED', 'DEGRADABLE', 'UNKNOWN')
MAX_STRING = 512
# Keys that would carry extracted proprietary payloads rather than metadata.
PAYLOAD_KEYS = frozenset({'vertices', 'triangles', 'indices', 'normals', 'tangents', 'uvs', 'colors',
                          'pixels', 'bytes', 'raw', 'payload', 'data_base64', 'bindposes',
                          'weights', 'curves', 'samples', 'text', 'synthetic_geometry',
                          'synthetic_pixels'})
_SLUG = r'[a-z0-9]+(?:_[a-z0-9]+)*'
_CANONICAL = {'base': re.compile(rf'^slime\.{_SLUG}$'), 'largo': re.compile(rf'^largo\.{_SLUG}\.{_SLUG}$')}
_PLORT = re.compile(rf'^plort\.{_SLUG}$')
_EXTENSION_KEY = re.compile(r'^[a-z0-9_]+\.[a-z0-9_.]+$')


class IRSchemaError(ValueError):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


# --- tiny declarative checker -------------------------------------------------------------

def _str(v, p, e):
    if not isinstance(v, str) or not v:
        e.append(diag('INVALID_TYPE', 'error', p, 'non-empty string required'))


def _opt(check):
    def inner(v, p, e):
        if v is not None:
            check(v, p, e)
    return inner


def _sha(v, p, e):
    if not is_sha256(v):
        e.append(diag('INVALID_HASH', 'error', p, 'lowercase SHA-256 hex required'))


def _bool(v, p, e):
    if type(v) is not bool:
        e.append(diag('INVALID_TYPE', 'error', p, 'boolean required'))


def _int(v, p, e):
    if type(v) is not int:
        e.append(diag('INVALID_TYPE', 'error', p, 'integer required'))


def _enum(*values):
    def inner(v, p, e):
        if v not in values:
            e.append(diag('INVALID_ENUM', 'error', p, f'one of {sorted(values)}'))
    return inner


def _list(check):
    def inner(v, p, e):
        if not isinstance(v, list):
            e.append(diag('INVALID_TYPE', 'error', p, 'list required'))
            return
        for i, item in enumerate(v):
            check(item, f'{p}[{i}]', e)
    return inner


def _json_dict(v, p, e):
    if not isinstance(v, dict):
        e.append(diag('INVALID_TYPE', 'error', p, 'object required'))


def _obj(spec):
    def inner(v, p, e):
        if not isinstance(v, dict):
            e.append(diag('INVALID_TYPE', 'error', p, 'object required'))
            return
        for key in sorted(set(v) - set(spec)):
            e.append(diag('UNKNOWN_FIELD', 'error', f'{p}.{key}', 'not part of this schema version'))
        for key, check in spec.items():
            if key not in v:
                e.append(diag('MISSING_FIELD', 'error', f'{p}.{key}'))
            else:
                check(v[key], f'{p}.{key}', e)
    return inner


_hashed = _obj({'id': _str, 'hash': _sha})
_SPEC = _obj({
    'schema': _str, 'schema_version': _int, 'canonical_id': _str,
    'content_kind': _enum('slime'), 'role': _enum('base', 'largo'),
    'identity': _obj({
        'source_realm': _enum(*REALMS), 'source_build_fingerprint': _str,
        'native_definition': _obj({'ref': _str, 'numeric_id': _opt(_int), 'hash': _sha}),
        'native_prefab': _opt(_obj({'ref': _str, 'hash': _sha})),
        'identity_basis': _enum(*IDENTITY_BASES), 'identity_evidence': _list(_str)}),
    'visual': _obj({
        'root': _opt(_str),
        'hierarchy': _list(_obj({'id': _str, 'parent': _opt(_str), 'kind': _enum('prefab', 'transform')})),
        'meshes': _list(_obj({'id': _str, 'hash': _sha, 'skinned': _bool, 'attributes': _json_dict})),
        'renderers': _list(_obj({'id': _str, 'kind': _enum('mesh', 'skinned'), 'node': _opt(_str),
                                 'mesh': _opt(_str), 'materials': _list(_opt(_str)),
                                 'bones': _list(_str), 'root_bone': _opt(_str)})),
        'materials': _list(_obj({'id': _str, 'hash': _sha,
                                 'shader': _opt(_obj({'ref': _str, 'hash': _opt(_sha)})),
                                 'semantic': _opt(_json_dict), 'status': _enum(*Status)})),
        'textures': _list(_hashed),
        'face': _opt(_obj({'id': _str, 'hash': _sha, 'expressions': _list(_str)})),
        'animator': _opt(_obj({'id': _str, 'hash': _sha, 'clips': _list(_str)})),
        'animation_clips': _list(_hashed), 'vfx': _list(_hashed), 'audio': _list(_hashed),
        'unknown_references': _list(_obj({'id': _str, 'kind': _str, 'via': _str}))}),
    'gameplay': _obj({
        'diet': _opt(_obj({'food_groups': _list(_str), 'favourite_foods': _list(_str),
                           'produces': _list(_str)})),
        'capabilities': _list(_obj({'id': _str, 'policy': _enum(*CAPABILITY_POLICIES),
                                    'sources': _list(_str), 'evidence': _str})),
        'native_behaviours': _list(_obj({'id': _str, 'type': _str, 'hash': _sha,
                                         'capability': _opt(_str)}))}),
    'associated': _obj({
        'plorts': _list(_obj({'canonical_id': _opt(_str), 'native_ref': _str, 'hash': _opt(_sha)})),
        'icon': _opt(_hashed), 'market': _opt(_json_dict), 'pedia': _opt(_json_dict),
        'localization': _opt(_json_dict), 'spawn': _opt(_json_dict)}),
    'provenance': _obj({
        'source_refs': _list(_str), 'asset_hashes': _json_dict, 'dependency_closure_hash': _sha,
        'scanner_schema_version': _int, 'converter_version': _str, 'synthetic': _bool,
        'data_evidence': _enum(*DataEvidence)}),
    'status': _enum(*Status), 'status_reasons': _list(_str),
    'extensions': _json_dict,
})


def _optional_extensions(doc):
    return {**doc, 'extensions': doc.get('extensions', {})} if isinstance(doc, dict) else doc


def _payload_guard(value, path, errors):
    stack = [(value, path)]
    while stack:
        item, where = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in PAYLOAD_KEYS:
                    errors.append(diag('PAYLOAD_NOT_ALLOWED', 'error', f'{where}.{key}',
                                       'IR holds metadata and hashes, never asset payloads'))
                else:
                    stack.append((child, f'{where}.{key}'))
        elif isinstance(item, list):
            stack.extend((child, f'{where}[{i}]') for i, child in enumerate(item))
        elif isinstance(item, str) and len(item) > MAX_STRING:
            errors.append(diag('PAYLOAD_NOT_ALLOWED', 'error', where, f'string longer than {MAX_STRING}'))


def _ids(rows):
    return [row['id'] for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)]


def _semantic_checks(doc, errors):
    visual, provenance = doc['visual'], doc['provenance']
    role = doc['role']
    if not isinstance(doc['canonical_id'], str) or role not in _CANONICAL or \
            not _CANONICAL[role].fullmatch(doc['canonical_id']):
        errors.append(diag('INVALID_CANONICAL_ID', 'error', 'canonical_id', f'{role} canonical pattern'))
    for key in doc.get('extensions', {}):
        if not _EXTENSION_KEY.fullmatch(key):
            errors.append(diag('INVALID_EXTENSION_KEY', 'error', f'extensions.{key}',
                               'extensions need a namespace.name key'))
    groups = {name: _ids(visual[name]) for name in
              ('hierarchy', 'meshes', 'renderers', 'materials', 'textures', 'animation_clips', 'vfx', 'audio')}
    for name, ids in groups.items():
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            errors.append(diag('DUPLICATE_ID', 'error', f'visual.{name}', dup))
    keyed = (('gameplay.capabilities', [r['id'] for r in doc['gameplay']['capabilities']]),
             ('gameplay.native_behaviours', [r['id'] for r in doc['gameplay']['native_behaviours']]),
             ('associated.plorts', [r['native_ref'] for r in doc['associated']['plorts']]))
    for where, ids in keyed:
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            errors.append(diag('DUPLICATE_ID', 'error', where, dup))
    nodes = {row['id']: row for row in visual['hierarchy']}
    roots = [row['id'] for row in visual['hierarchy'] if row['parent'] is None]
    tree_ok = len(roots) == (1 if nodes else 0) and (not nodes or roots[0] == visual['root'])
    for start in nodes:
        seen, cursor = set(), start
        while cursor is not None and tree_ok:
            if cursor in seen or cursor not in nodes:
                tree_ok = False
                break
            seen.add(cursor)
            cursor = nodes[cursor]['parent']
    if not tree_ok:
        errors.append(diag('HIERARCHY_NOT_TREE', 'error', 'visual.hierarchy',
                           'exactly one root equal to visual.root and no cycles/dangling parents'))
    meshes = {row['id']: row for row in visual['meshes']}
    materials = set(groups['materials'])
    for row in visual['renderers']:
        where = f"visual.renderers[{row['id']}]"
        refs = [(row['mesh'], meshes), (row['node'], nodes), (row['root_bone'], nodes),
                *((bone, nodes) for bone in row['bones']), *((m, materials) for m in row['materials'])]
        for ref, pool in refs:
            if ref is not None and ref not in pool:
                errors.append(diag('DANGLING_REFERENCE', 'error', where, ref))
        if row['mesh'] in meshes and (row['kind'] == 'skinned') != meshes[row['mesh']]['skinned']:
            errors.append(diag('SKINNING_MISMATCH', 'error', where, 'renderer kind vs mesh skinning'))
        if row['kind'] == 'skinned' and not row['bones']:
            errors.append(diag('SKINNING_MISMATCH', 'error', where, 'skinned renderer without bones'))
    if visual['animator'] is not None:
        for clip in visual['animator']['clips']:
            if clip not in groups['animation_clips']:
                errors.append(diag('DANGLING_REFERENCE', 'error', 'visual.animator', clip))
    hashed = [*visual['meshes'], *visual['materials'], *visual['textures'], *visual['animation_clips'],
              *visual['vfx'], *visual['audio'], *(r for r in (visual['face'], visual['animator'],
                                                                doc['associated']['icon']) if r)]
    declared = provenance['asset_hashes']
    for row in hashed:
        if row['id'] in declared and declared[row['id']] != row['hash']:
            errors.append(diag('HASH_MISMATCH', 'error', row['id'], 'asset_hashes disagrees with node'))
    for key, value in declared.items():
        if not is_sha256(value):
            errors.append(diag('INVALID_HASH', 'error', f'provenance.asset_hashes.{key}'))
    for plort in doc['associated']['plorts']:
        if plort['canonical_id'] is not None and not _PLORT.fullmatch(plort['canonical_id']):
            errors.append(diag('INVALID_CANONICAL_ID', 'error', 'associated.plorts', plort['canonical_id']))
    synthetic = provenance['synthetic']
    if synthetic != (provenance['data_evidence'] == DataEvidence.SYNTHETIC):
        errors.append(diag('EVIDENCE_CONTRADICTION', 'error', 'provenance',
                           'synthetic flag and data_evidence disagree'))
    if doc['status'] == Status.AVAILABLE and provenance['data_evidence'] != DataEvidence.RUNTIME_ANCHORED:
        errors.append(diag('EVIDENCE_CONTRADICTION', 'error', 'status',
                           'AVAILABLE requires runtime-anchored, non-synthetic source evidence'))
    renderable = any(r['mesh'] in meshes for r in visual['renderers'])
    if doc['status'] in (Status.AVAILABLE, Status.EXPERIMENTAL) and (not renderable or visual['root'] is None):
        errors.append(diag('NOT_RENDERABLE', 'error', 'visual',
                           'usable status needs a root and a renderer with a resolved mesh'))
    if doc['status'] != Status.AVAILABLE and not doc['status_reasons']:
        errors.append(diag('MISSING_STATUS_REASON', 'error', 'status_reasons'))


def validate_ir(doc) -> list:
    """Return sorted diagnostics; an empty list means the document is valid. Never raises."""
    errors = []
    _SPEC(_optional_extensions(doc), 'ir', errors)
    if errors:
        return sort_diagnostics(errors)
    if doc['schema'] != IR_SCHEMA or doc['schema_version'] != IR_SCHEMA_VERSION:
        errors.append(diag('WRONG_SCHEMA', 'error', 'schema', f'{IR_SCHEMA} v{IR_SCHEMA_VERSION} only'))
    _payload_guard(doc, 'ir', errors)
    _semantic_checks(_optional_extensions(doc), errors)
    return sort_diagnostics(errors)


_SET_LISTS = (('visual', 'hierarchy'), ('visual', 'meshes'), ('visual', 'renderers'),
              ('visual', 'materials'), ('visual', 'textures'), ('visual', 'animation_clips'),
              ('visual', 'vfx'), ('visual', 'audio'), ('gameplay', 'capabilities'),
              ('gameplay', 'native_behaviours'))


def canonicalize_ir(doc) -> dict:
    """Deterministic form: sort set-like lists; keep slot/bone sequences in native order."""
    out = copy.deepcopy(doc)
    out.setdefault('extensions', {})
    for section, key in _SET_LISTS:
        out[section][key] = sorted(out[section][key], key=lambda row: (row['id'], canonical_json(row)))
    visual = out['visual']
    visual['unknown_references'] = sorted(visual['unknown_references'],
                                          key=lambda r: (r['id'], r['kind'], r['via']))
    for row in (visual['face'],):
        if row:
            row['expressions'] = sorted(row['expressions'])
    if visual['animator']:
        visual['animator']['clips'] = sorted(visual['animator']['clips'])
    for cap in out['gameplay']['capabilities']:
        cap['sources'] = sorted(cap['sources'])
    diet = out['gameplay']['diet']
    if diet:
        for key in ('food_groups', 'favourite_foods', 'produces'):
            diet[key] = sorted(set(diet[key]))
    out['associated']['plorts'] = sorted(out['associated']['plorts'], key=lambda r: (r['native_ref'], canonical_json(r)))
    out['provenance']['source_refs'] = sorted(set(out['provenance']['source_refs']))
    out['identity']['identity_evidence'] = sorted(set(out['identity']['identity_evidence']))
    out['status_reasons'] = sorted(set(out['status_reasons']))
    return out


def ir_hash(doc) -> str:
    return sha256_json(canonicalize_ir(doc))


def load_ir(doc, migrations=None) -> dict:
    """Validate (migrating older versions explicitly). Raises IRSchemaError; never mutates input."""
    steps = MIGRATIONS if migrations is None else migrations
    if not isinstance(doc, dict) or doc.get('schema') != IR_SCHEMA or type(doc.get('schema_version')) is not int:
        raise IRSchemaError('WRONG_SCHEMA', f'expected {IR_SCHEMA} with an integer schema_version')
    version = doc['schema_version']
    if version > IR_SCHEMA_VERSION:
        raise IRSchemaError('SCHEMA_TOO_NEW', f'v{version} > supported v{IR_SCHEMA_VERSION}; upgrade the reader')
    current = copy.deepcopy(doc)
    while version < IR_SCHEMA_VERSION:
        if version not in steps:
            raise IRSchemaError('MIGRATION_MISSING', f'no migration from v{version}')
        current = steps[version](current)
        if not isinstance(current, dict) or current.get('schema_version') != version + 1:
            raise IRSchemaError('MIGRATION_INVALID', f'v{version} migration did not produce v{version + 1}')
        version += 1
    problems = validate_ir(current)
    if problems:
        raise IRSchemaError('INVALID_IR', '; '.join(f"{d['code']}@{d['subject']}" for d in problems[:8]))
    return canonicalize_ir(current)
