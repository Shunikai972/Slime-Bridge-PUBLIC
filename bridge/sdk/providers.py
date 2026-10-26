"""Provider SDK: what a native adapter declares and implements, without knowing conversion internals.

Every provider exposes ``describe()`` returning a ``bridge-provider-descriptor`` v1 and ``health()``.
Kind-specific methods are the Phase 1 protocols (``bridge/conversion/providers.py``) plus the
Phase 2 runtime-verification and counterpart realm-adapter contracts. A ``ProviderRegistry``
validates descriptors and selects deterministically by kind, realm, build and health; an
unavailable or undeclared capability is reported, never assumed.

Kinds and the methods they require:
- registry:            read_registry()
- dependency:          build_fingerprint(), definition_root(entry), inspect(ref)
- extraction:          extract(request)
- source_material:     describe(material_node, graph)        (SourceMaterialMapper)
- target_material:     profile()                             (bridge-target-material-profile)
- source_behaviour:    profile()                             (bridge-behaviour-profile)
- target_behaviour:    profile()                             (bridge-target-behaviour-profile)
- registration:        capabilities(), apply(plan)
- runtime_verification: run_check(requirement, context) -> verification receipt
- counterpart:         see bridge.counterpart.adapters.RealmCounterpartAdapter
- process:             status(session), launch(realm, installation, save), attach(pid, expected_image_sha256,
                       expected_executable), park(session), stop(session)   -- must verify PID AND image
- window:              show(session), hide(session), focus(session), set_opacity(session, value)
- input:               transfer_to(session), return_to(session)
- world_ready:         report(session, endpoint) -> bridge-world-ready | None
- gameplay_lock:       lock_and_sync(session), unlock(session)
- audio:               duck(session), restore(session)
- economy:             balance(session, resource), reserve(session, resource, amount, intent_id),
                       commit(intent_id), release(intent_id)                 -- portal costs (Q030)
- realm_network:       describe_sync(), apply_remote_event(event), drain_local_events()  -- multiplayer realm sync

Windows/process/currency implementations are local; Cloud defines contracts and checks.
"""

from bridge.content.canonical import Evidence
from bridge.core import diagnostics as D
from bridge.core import schema as S

DESCRIPTOR_SCHEMA = 'bridge-provider-descriptor'
SDK_SCHEMA_VERSION = 1
SUPPORTED_SDK_VERSIONS = (1,)
KIND_METHODS = {
    'registry': ('read_registry',),
    'dependency': ('build_fingerprint', 'definition_root', 'inspect'),
    'extraction': ('extract',),
    'source_material': ('describe',),
    'target_material': ('profile',),
    'source_behaviour': ('profile',),
    'target_behaviour': ('profile',),
    'registration': ('capabilities', 'apply'),
    'runtime_verification': ('run_check',),
    'counterpart': ('describe_slots', 'build_command', 'interpret_result', 'expected_identity'),
    'process': ('status', 'launch', 'attach', 'park', 'stop'),
    'window': ('show', 'hide', 'focus', 'set_opacity'),
    'input': ('transfer_to', 'return_to'),
    'world_ready': ('report',),
    'gameplay_lock': ('lock_and_sync', 'unlock'),
    'audio': ('duck', 'restore'),
    'economy': ('balance', 'reserve', 'commit', 'release'),
    'realm_network': ('describe_sync', 'apply_remote_event', 'drain_local_events'),
}
KNOWN_FEATURES = {
    'registry': ('complete_traversal', 'runtime_anchor', 'plort_entries', 'food_entries', 'numeric_ids',
                 'largo_relations'),
    'dependency': ('skinned_meshes', 'faces', 'animators', 'vfx', 'audio', 'pedia', 'spawn', 'market'),
    'extraction': ('mesh', 'texture', 'animation_clip', 'audio', 'animator_controller', 'vfx', 'sprite'),
    'registration': ('identity', 'plort', 'icon', 'materials', 'prefab', 'definition', 'diet', 'behaviour',
                     'localization', 'pedia', 'market', 'persistence'),
    'runtime_verification': ('spawn', 'render', 'vacpack', 'diet', 'plort', 'market', 'icon', 'pedia', 'save',
                             'reload', 'world_ready', 'counterpart_readback'),
    'counterpart': ('create', 'reconcile', 'slot_probe', 'full_readback', 'save_hash', 'world_ready'),
    'process': ('launch', 'attach_existing', 'park', 'pid_verification', 'image_verification'),
    'window': ('fade', 'borderless_overlay', 'focus_guard'),
    'input': ('keyboard_mouse', 'controller'),
    'world_ready': ('scene_settled', 'player_usable', 'bridge_synchronized'),
    'gameplay_lock': ('pause', 'save_flush', 'inventory_freeze'),
    'audio': ('duck', 'mute'),
    'economy': ('balance_read', 'escrow'),
    'realm_network': ('presence_avatar', 'chat_bridge', 'transaction_sync'),
}
HEALTH = ('HEALTHY', 'DEGRADED', 'UNAVAILABLE', 'UNKNOWN')
_ID = r'[a-z0-9]+(?:[._-][a-z0-9]+)+'
_VERSION = r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'

_SPEC = S.obj({
    'schema': S.enum(DESCRIPTOR_SCHEMA), 'sdk_schema_version': S.integer(minimum=1),
    'provider_id': S.string(_ID, max_length=128), 'provider_version': S.string(_VERSION),
    'kinds': S.list_of(S.enum(*KIND_METHODS), unique=True), 'realm': S.optional(S.enum('SR1', 'SR2')),
    'supported_builds': S.list_of(S.string(), unique=True),
    'features': S.map_of(S.enum(*KIND_METHODS), S.list_of(S.string(r'[a-z_]+'), unique=True)),
    'unsupported': S.map_of(S.enum(*KIND_METHODS), S.list_of(S.string(r'[a-z_]+'), unique=True)),
    'evidence': S.enum(*map(str, Evidence)), 'synthetic': S.boolean(),
    'local_queries': S.list_of(S.string(r'Q\d{3}'), unique=True),
})


def descriptor(provider_id, provider_version, kinds, *, realm, supported_builds=('*',), features=None,
               unsupported=None, evidence=Evidence.NEEDS_LOCAL_RUNTIME_PROOF, synthetic=False, local_queries=()):
    return {'schema': DESCRIPTOR_SCHEMA, 'sdk_schema_version': SDK_SCHEMA_VERSION, 'provider_id': provider_id,
            'provider_version': provider_version, 'kinds': sorted(kinds), 'realm': realm,
            'supported_builds': sorted(supported_builds), 'features': {k: sorted(v) for k, v in (features or {}).items()},
            'unsupported': {k: sorted(v) for k, v in (unsupported or {}).items()}, 'evidence': str(evidence),
            'synthetic': synthetic, 'local_queries': sorted(local_queries)}


def validate_descriptor(desc, provider=None):
    """Unified diagnostics for an untrusted descriptor (and, if given, the object behind it)."""
    issues = S.check(_SPEC, desc)
    subject = desc.get('provider_id') if isinstance(desc, dict) else None
    if issues:
        return [D.make('PROVIDER_DESCRIPTOR_INVALID', 'error', 'provider', 'descriptor schema violation',
                       provider=subject if isinstance(subject, str) else None, details={'problems': issues[:20]})]
    out = []
    if desc['sdk_schema_version'] not in SUPPORTED_SDK_VERSIONS:
        out.append(D.make('PROVIDER_SDK_UNSUPPORTED', 'error', 'provider', 'SDK schema version not supported',
                          provider=subject, details={'version': desc['sdk_schema_version']}))
    for kind, features in [*desc['features'].items(), *desc['unsupported'].items()]:
        if kind not in desc['kinds']:
            out.append(D.make('PROVIDER_FEATURE_FOR_UNDECLARED_KIND', 'error', 'provider', kind, provider=subject))
        unknown = sorted(set(features) - set(KNOWN_FEATURES.get(kind, ())))
        if unknown:
            out.append(D.make('PROVIDER_FEATURE_UNKNOWN', 'error', 'provider', 'undefined feature names',
                              provider=subject, details={'kind': kind, 'features': unknown}))
    for kind in desc['kinds']:
        overlap = set(desc['features'].get(kind, [])) & set(desc['unsupported'].get(kind, []))
        if overlap:
            out.append(D.make('PROVIDER_FEATURE_CONTRADICTION', 'error', 'provider', 'feature both supported and not',
                              provider=subject, details={'kind': kind, 'features': sorted(overlap)}))
    if desc['synthetic'] and desc['evidence'] not in (Evidence.IMPLEMENTED, Evidence.UNIT_TESTED):
        out.append(D.make('PROVIDER_EVIDENCE_CONTRADICTION', 'error', 'provider',
                          'a synthetic provider cannot claim native evidence', provider=subject))
    if provider is not None:
        for kind in desc['kinds']:
            missing = [m for m in KIND_METHODS[kind] if not callable(getattr(provider, m, None))]
            if missing:
                out.append(D.make('PROVIDER_METHOD_MISSING', 'error', 'provider', f'{kind} requires {missing}',
                                  provider=subject))
        realm = getattr(provider, 'realm', None)
        if desc['realm'] is not None and realm is not None and realm != desc['realm']:
            out.append(D.make('PROVIDER_REALM_MISMATCH', 'error', 'provider', 'object realm differs from descriptor',
                              provider=subject, details={'object': realm, 'descriptor': desc['realm']}))
    return D.sort(out)


def health(status, *diagnostics):
    if status not in HEALTH:
        raise ValueError(status)
    return {'status': status, 'diagnostics': D.sort(list(diagnostics))}


class BridgeProvider:
    """Optional base class. Subclasses set DESCRIPTOR (or override describe) and the kind methods."""
    DESCRIPTOR = None

    def describe(self):
        return dict(self.DESCRIPTOR)

    def health(self):
        return health('UNKNOWN')


class ProviderRegistry:
    def __init__(self):
        self._entries = {}

    def register(self, provider):
        desc = provider.describe()
        problems = validate_descriptor(desc, provider)
        if D.has_errors(problems):
            raise ValueError(f"provider rejected: {[p['code'] for p in problems]}")
        key = (desc['provider_id'], desc['provider_version'])
        if key in self._entries:
            raise ValueError(f'provider {key} already registered')
        self._entries[key] = (desc, provider)
        return desc

    def select(self, kind, *, realm, build=None, require=()):
        """Deterministic choice; returns (provider|None, diagnostics)."""
        candidates, diags = [], []
        for (pid, version), (desc, provider) in sorted(self._entries.items()):
            if kind not in desc['kinds'] or desc['realm'] not in (realm, None):
                continue
            if build is not None and '*' not in desc['supported_builds'] and build not in desc['supported_builds']:
                diags.append(D.make('PROVIDER_BUILD_UNSUPPORTED', 'info', 'provider', 'build not declared',
                                    provider=pid, realm=realm, details={'build': build}))
                continue
            missing = sorted(set(require) - set(desc['features'].get(kind, [])))
            if missing:
                diags.append(D.make('PROVIDER_FEATURE_MISSING', 'info', 'provider', 'required feature not declared',
                                    provider=pid, realm=realm, details={'features': missing}))
                continue
            state = _safe_health(provider)
            if state['status'] == 'UNAVAILABLE':
                diags.append(D.make('PROVIDER_UNAVAILABLE', 'warning', 'provider', 'provider reports unavailable',
                                    provider=pid, realm=realm))
                continue
            exact = build is not None and build in desc['supported_builds']
            rank = {'HEALTHY': 0, 'DEGRADED': 1, 'UNKNOWN': 2}[state['status']]
            candidates.append(((rank, not exact, tuple(-int(x) for x in version.split('.')), pid), provider))
        if not candidates:
            diags.append(D.make('PROVIDER_NONE_AVAILABLE', 'error', 'provider', f'no {kind} provider',
                                realm=realm, details={'build': build, 'require': sorted(require)}))
            return None, D.sort(diags)
        return sorted(candidates, key=lambda c: c[0])[0][1], D.sort(diags)

    def health_report(self):
        return [{'provider_id': desc['provider_id'], 'provider_version': desc['provider_version'],
                 'realm': desc['realm'], 'kinds': desc['kinds'], 'health': _safe_health(provider)}
                for (_, _), (desc, provider) in sorted(self._entries.items())]


def _safe_health(provider):
    try:
        state = provider.health()
        if isinstance(state, dict) and state.get('status') in HEALTH:
            return state
    except Exception as exc:  # a crashing health probe is UNAVAILABLE, not a crash of the registry
        return health('UNAVAILABLE', D.make('PROVIDER_HEALTH_EXCEPTION', 'error', 'provider',
                                            f'{type(exc).__name__}: {str(exc)[:160]}'))
    return health('UNKNOWN', D.make('PROVIDER_HEALTH_INVALID', 'warning', 'provider', 'malformed health report'))


class ManifestProviderAdapter(BridgeProvider):
    """SDK view of a Phase 1 ManifestSource (registry + dependency kinds)."""

    def __init__(self, source, provider_id='bridge.manifest-source', version='1.0.0'):
        self.source, self.realm = source, source.realm
        self._desc = descriptor(provider_id, version, ['registry', 'dependency'], realm=source.realm,
                                supported_builds=[source.build_fingerprint()],
                                features={'registry': ['complete_traversal'] if source.read_registry()['traversal']
                                          ['completeness'] == 'COMPLETE' else []},
                                evidence=Evidence.UNIT_TESTED if source.synthetic else Evidence.NEEDS_LOCAL_RUNTIME_PROOF,
                                synthetic=source.synthetic)

    def describe(self):
        return dict(self._desc)

    def health(self):
        return health('HEALTHY')

    def read_registry(self):
        return self.source.read_registry()

    def build_fingerprint(self):
        return self.source.build_fingerprint()

    def definition_root(self, entry):
        return self.source.definition_root(entry)

    def inspect(self, ref):
        return self.source.inspect(ref)
