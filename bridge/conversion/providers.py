"""Integration boundary between the generic conversion core and realm-native implementations.

The core never touches game files, Unity objects or loader APIs. the local provider implements
these protocols against real SR1 (Mono/SRML), SR2 (IL2CPP/MelonLoader), AssetRipper exports
or plugin-written registry dumps; Cloud ships only synthetic/manifest-backed implementations
and fail-closed native stubs. All provider outputs are plain JSON-compatible dicts and are
validated by the core at the boundary, so a provider bug becomes a diagnostic, not a crash.

Contracts (all refs are native references within the provider's realm):

* SourceRegistryProvider.read_registry() -> ``bridge-registry-snapshot`` dict.
* AssetDependencyProvider.inspect(ref) -> object descriptor or None (not found). Raise
  ProviderError for a failed read. Descriptor::

      {'ref': str, 'kind': NodeKind value, 'hash': sha256 | None, 'attributes': dict | None,
       'provenance': str,
       'references': [{'relation': EDGE_RULES key (unknown kept as references), 'target': str,
                       'slot': str, 'critical': bool | None, 'target_kind': str | None,
                       'candidates': [str] | None, 'provenance': str}]}

  ``hash`` is a hash of the native object's serialized metadata/bytes computed locally; the
  bytes themselves never leave the local machine.
* AssetDependencyProvider.definition_root(entry) -> object ref of the definition node.
* AssetExtractionProvider.extract(request) -> receipt (see extraction.py). Writes only into a
  caller-provided ignored directory.
* SourceMaterialMapper.describe(material_node, graph) -> native material description used by
  materials.neutralize (shader identity + raw property list).
* TargetMaterialProvider.profile() -> target material profile (materials.py).
* NativeBehaviourMapper.profile() -> behaviour mapping profile (capabilities.py).
* TargetRegistrationProvider.capabilities() / apply(plan) -> registration receipts.
"""

from typing import Protocol, runtime_checkable

from bridge.content.canonical import Evidence


class ProviderError(RuntimeError):
    """A provider failed to read something it should be able to read."""


class NotAvailableInCloud(ProviderError):
    """A native operation that only the local machine with user-owned games can perform."""

    def __init__(self, operation, local_queries=()):
        self.operation = operation
        self.local_queries = tuple(local_queries)
        self.evidence = Evidence.NEEDS_LOCAL_RUNTIME_PROOF if not local_queries else Evidence.BLOCKED_ON_LOCAL_QUERY
        super().__init__(f'{operation}: {self.evidence} {",".join(self.local_queries)}'.strip())


@runtime_checkable
class SourceRegistryProvider(Protocol):
    realm: str

    def read_registry(self) -> dict: ...


@runtime_checkable
class AssetDependencyProvider(Protocol):
    realm: str

    def build_fingerprint(self) -> str: ...

    def definition_root(self, entry: dict) -> str: ...

    def inspect(self, ref: str) -> dict | None: ...


@runtime_checkable
class AssetExtractionProvider(Protocol):
    realm: str

    def extract(self, request: dict) -> dict: ...


@runtime_checkable
class SourceMaterialMapper(Protocol):
    realm: str

    def describe(self, material_node: dict, graph) -> dict: ...


@runtime_checkable
class TargetMaterialProvider(Protocol):
    realm: str

    def profile(self) -> dict: ...


@runtime_checkable
class NativeBehaviourMapper(Protocol):
    realm: str

    def profile(self) -> dict: ...


@runtime_checkable
class TargetRegistrationProvider(Protocol):
    realm: str

    def capabilities(self) -> dict: ...

    def apply(self, plan: dict) -> dict: ...


class NativeInstallProvider:
    """Placeholder for the real local provider over a user-owned install. Fails closed in Cloud.

    The local provider replaces this with an implementation that reads the plugin registry dump / AssetRipper
    export for ``game_root``; until then every call says exactly which local facts are missing.
    """

    QUERIES = {'SR1': ('Q001', 'Q002', 'Q003'), 'SR2': ('Q004', 'Q005', 'Q006')}

    def __init__(self, realm, game_root):
        self.realm = realm
        self.game_root = game_root

    def _refuse(self, operation):
        raise NotAvailableInCloud(f'{self.realm} {operation} on {self.game_root}', self.QUERIES[self.realm])

    def read_registry(self):
        self._refuse('registry traversal')

    def build_fingerprint(self):
        self._refuse('build fingerprint')

    def definition_root(self, entry):
        self._refuse('definition root lookup')

    def inspect(self, ref):
        self._refuse('dependency inspection')

    def extract(self, request):
        self._refuse('asset extraction')


class UnavailableRegistrationProvider:
    """Registration stub: describes nothing as implemented and applies nothing."""

    def __init__(self, realm):
        self.realm = realm

    def capabilities(self):
        return {'realm': self.realm, 'implemented_operations': [],
                'evidence': str(Evidence.NEEDS_LOCAL_RUNTIME_PROOF)}

    def apply(self, plan):
        raise NotAvailableInCloud(f'{self.realm} registration', ('Q007',) if self.realm == 'SR1' else ('Q008',))
