"""Manifest-backed source provider (``bridge-content-source`` v1).

One JSON document per realm carries a registry snapshot plus metadata-only object
descriptors (kind, hash, typed references). Synthetic fixtures use it in Cloud; a local
exporter (plugin registry dump + AssetRipper/runtime dependency walk) can write the same
format so real data flows through the generic core without code changes. Object hashes are
computed locally; no asset bytes appear here.
"""

import copy

from bridge.content.canonical import REALMS, DataEvidence, diag, sha256_json

SOURCE_SCHEMA = 'bridge-content-source'
SOURCE_SCHEMA_VERSION = 1


class ManifestSource:
    """Implements SourceRegistryProvider and AssetDependencyProvider over a validated manifest."""

    def __init__(self, data: dict, registry=None):
        """``registry`` optionally replaces the embedded one, e.g. an AnchoredSnapshot produced by
        ``snapshot_from_scan(read_runtime_snapshot(...))`` so runtime evidence survives (JSON cannot carry it)."""
        if not isinstance(data, dict) or data.get('schema') != SOURCE_SCHEMA:
            raise ValueError(f'Expected {SOURCE_SCHEMA}')
        if type(data.get('schema_version')) is not int or data['schema_version'] != SOURCE_SCHEMA_VERSION:
            raise ValueError(f'Unsupported {SOURCE_SCHEMA} version {data.get("schema_version")!r}')
        if data.get('realm') not in REALMS:
            raise ValueError('Manifest realm must be SR1 or SR2')
        if data.get('data_evidence') not in tuple(DataEvidence) or type(data.get('synthetic')) is not bool:
            raise ValueError('Manifest needs data_evidence and a synthetic flag')
        if data['synthetic'] != (data['data_evidence'] == DataEvidence.SYNTHETIC):
            raise ValueError('synthetic flag contradicts data_evidence')
        build = data.get('build') or {}
        if not isinstance(build.get('fingerprint'), str) or not build['fingerprint']:
            raise ValueError('Manifest needs a build fingerprint')
        embedded = registry is None
        registry = data.get('registry') if embedded else registry
        if not isinstance(registry, dict) or registry.get('realm') != data['realm'] or \
                (registry.get('build') or {}).get('fingerprint') != build['fingerprint'] or \
                (embedded and registry.get('data_evidence') != data['data_evidence']):
            raise ValueError('Registry realm/build/evidence must match the manifest')
        objects = data.get('objects')
        if not isinstance(objects, list):
            raise ValueError('objects must be a list')
        self.diagnostics = []
        self._objects = {}
        for index, row in enumerate(objects):
            ref = row.get('ref') if isinstance(row, dict) else None
            if not isinstance(ref, str) or not ref:
                self.diagnostics.append(diag('UNADDRESSABLE_OBJECT', 'warning', f"{data['realm']}:#{index}",
                                             'object without a native ref skipped'))
                continue
            if ref in self._objects:
                raise ValueError(f'Duplicate object ref {ref}')
            self._objects[ref] = row
        roots = data.get('roots', {})
        if not isinstance(roots, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in roots.items()):
            raise ValueError('roots must map native refs to object refs')
        self.realm = data['realm']
        self.synthetic = data['synthetic']
        self.data_evidence = DataEvidence(data['data_evidence'])
        self._build = build['fingerprint']
        self._registry = copy.deepcopy(registry)
        self._roots = dict(roots)
        try:
            self.manifest_hash = sha256_json(data)
        except (TypeError, ValueError, RecursionError):
            self.manifest_hash = None  # unhashable objects are isolated per node during discovery
        self.extensions = copy.deepcopy(data.get('extensions', {}))

    def read_registry(self):
        return copy.deepcopy(self._registry)

    def build_fingerprint(self):
        return self._build

    def definition_root(self, entry):
        return self._roots.get(entry['native_ref'], entry['native_ref'])

    def inspect(self, ref):
        found = self._objects.get(ref)
        return copy.deepcopy(found) if found is not None else None
