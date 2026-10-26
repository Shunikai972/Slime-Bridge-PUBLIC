"""``bridge-deploy-unit`` v1: the realm-specific, installable form of a package, Largo plan or mod.

A unit names its logical ``key`` (stable across versions), the realm it targets, an orderable
``version`` tuple, the canonical ids it provides, the keys it requires/conflicts with, its build
compatibility and the exact files it places (relative path + sha256). ``unit_id`` is the content
hash of everything else, so two units with the same id are byte-for-byte the same install.
"""

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D
from bridge.core import schema as S
from bridge.core.paths import safe_relative

UNIT_SCHEMA = 'bridge-deploy-unit'
KINDS = ('GENERATED', 'LARGO', 'MOD')
UNIT_STATUS = ('READY', 'EXPERIMENTAL', 'PENDING_ADAPTER', 'QUARANTINED')
DEPLOYABLE_STATUS = ('READY', 'EXPERIMENTAL')
MAX_FILES = 4096
_KEY = r'(generated|largo|mod):[a-z0-9][a-z0-9._-]{0,127}:(SR1|SR2)'

_SPEC = S.obj({
    'schema': S.enum(UNIT_SCHEMA), 'schema_version': S.enum(1), 'unit_id': S.sha256(),
    'key': S.string(_KEY, max_length=160), 'kind': S.enum(*KINDS), 'realm': S.enum('SR1', 'SR2'),
    'version': S.list_of(S.integer(minimum=0, maximum=2 ** 31), max_items=8),
    'provides': S.list_of(S.string(r'[a-z0-9][a-z0-9._-]{0,159}', max_length=160), max_items=256, unique=True),
    'requires': S.list_of(S.string(_KEY, max_length=160), max_items=256, unique=True),
    'conflicts': S.list_of(S.string(_KEY, max_length=160), max_items=256, unique=True),
    'files': S.list_of(S.obj({'path': S.string(max_length=1024), 'sha256': S.sha256(),
                              'size': S.optional(S.integer(minimum=0))}), max_items=MAX_FILES),
    'build_compatibility': S.obj({'policy': S.enum('EXACT', 'UNBOUND'),
                                  'fingerprints': S.list_of(S.string(max_length=256), max_items=64)}),
    'status': S.enum(*UNIT_STATUS), 'source_ref': S.string(max_length=256, allow_empty=True),
    'evidence': S.string(max_length=64),
})


def make_unit(*, key, kind, realm, version, files, provides=(), requires=(), conflicts=(), build_policy='UNBOUND',
              build_fingerprints=(), status='READY', source_ref='', evidence='NEEDS_LOCAL_RUNTIME_PROOF'):
    unit = {'schema': UNIT_SCHEMA, 'schema_version': 1, 'key': key, 'kind': kind, 'realm': realm,
            'version': list(version), 'provides': sorted(set(provides)), 'requires': sorted(set(requires)),
            'conflicts': sorted(set(conflicts)),
            'files': sorted(({'path': f['path'], 'sha256': f['sha256'], 'size': f.get('size')} for f in files),
                            key=lambda f: f['path']),
            'build_compatibility': {'policy': build_policy, 'fingerprints': sorted(set(build_fingerprints))},
            'status': status, 'source_ref': source_ref, 'evidence': evidence}
    unit['unit_id'] = sha256_json({k: v for k, v in unit.items()})
    return unit


def validate_unit(unit):
    problems = S.check(_SPEC, unit)
    if problems:
        return [D.make('UNIT_SCHEMA_INVALID', 'error', 'deploy', 'deploy unit schema violation',
                       subject=unit.get('key') if isinstance(unit, dict) else None, details={'problems': problems[:20]})]
    out = []

    def bad(code, message, **kw):
        out.append(D.make(code, 'error', 'deploy', message, subject=unit['key'], **kw))
    if sha256_json({k: v for k, v in unit.items() if k != 'unit_id'}) != unit['unit_id']:
        bad('UNIT_ID_MISMATCH', 'unit_id is not the content hash')
    if not unit['key'].startswith(unit['kind'].lower() + ':') or not unit['key'].endswith(':' + unit['realm']):
        bad('UNIT_KEY_INCONSISTENT', 'key must encode kind and realm')
    folded = {}
    for f in unit['files']:
        if not safe_relative(f['path']):
            bad('UNIT_PATH_UNSAFE', 'unsafe file path', details={'path': f['path'][:200]})
        folded.setdefault(f['path'].casefold(), []).append(f['path'])
    for paths in folded.values():
        if len(paths) > 1:
            bad('UNIT_PATH_COLLISION', 'paths collide (duplicate or case-insensitive)', details={'paths': paths[:4]})
    if unit['key'] in unit['requires'] or unit['key'] in unit['conflicts']:
        bad('UNIT_SELF_REFERENCE', 'a unit cannot require or conflict with itself')
    return D.sort(out)


def build_compatible(unit, build):
    """COMPATIBLE / REVERIFY_REQUIRED for the installed game build (never INCOMPATIBLE by build alone)."""
    policy = unit['build_compatibility']
    if policy['policy'] == 'EXACT' and build in policy['fingerprints']:
        return 'COMPATIBLE'
    return 'REVERIFY_REQUIRED'


def unit_from_generated_package(pkg, *, version=None):
    """Deploy unit for a materialized Phase 2 generated package (files under its target realm)."""
    cid = pkg['lineage']['canonical_id']
    realm = pkg['target']['realm']
    files = [{'path': f"generated/{cid}/{a['path'].split('/', 1)[1]}", 'sha256': a['sha256']}
             for a in pkg['artifacts'] if a['sha256'] is not None]
    status = {'EXPERIMENTAL': 'EXPERIMENTAL', 'PENDING_ADAPTER': 'PENDING_ADAPTER'}.get(pkg['status'], 'QUARANTINED')
    if version is None:
        try:
            version = [int(x) for x in str(pkg['converter']['converter_version']).split('.')]
        except (KeyError, TypeError, ValueError):
            version = [0]
    return make_unit(key=f'generated:{cid}:{realm}', kind='GENERATED', realm=realm, version=version,
                     files=files, provides=[cid, *pkg['canonical_ids']['plorts']],
                     build_policy=pkg['target']['build_compatibility']['policy'],
                     build_fingerprints=pkg['target']['build_compatibility']['fingerprints'], status=status,
                     source_ref=pkg['package_id'])


def unit_from_largo_plan(plan):
    """Deploy unit for a ``bridge-user-largo-plan``; files are referenced by hash, never rewritten."""
    cid = plan['canonical_largo_id']
    realm = plan['target_realm']
    files = [{'path': f"largos/{cid}/{a['path']}", 'sha256': a['sha256']} for a in plan['artifacts']]
    status = 'EXPERIMENTAL' if plan['final'] == 'EXPERIMENTAL' else 'PENDING_ADAPTER'
    return make_unit(key=f'largo:{cid}:{realm}', kind='LARGO', realm=realm,
                     version=[int(x) for x in plan['version'].split('.')], files=files, provides=[cid],
                     requires=[f'generated:{p}:{realm}' for p in plan.get('converted_parents', [])],
                     status=status, source_ref=plan['plan_hash'])
