# Generic content pipeline (Cloud slice)

This is the generic, bidirectional base-slime conversion core. It holds pure logic, schemas, provider interfaces and synthetic fixtures only. It contains no game data and makes no runtime claim. Evidence for everything here: **IMPLEMENTED + UNIT_TESTED on synthetic data**. Any real-game step is **NEEDS_LOCAL_RUNTIME_PROOF** or **BLOCKED_ON_LOCAL_QUERY** (see `docs/LOCAL_QUERIES.md`).

```
user-owned SR1/SR2 install
  -> SourceRegistryProvider.read_registry()      bridge-registry-snapshot   (bridge/registry/snapshot.py)
  -> classify_species()                           bridge-species-classification (bridge/registry/reconcile.py)
  -> discover() via AssetDependencyProvider       bridge-dependency-graph    (bridge/content/discovery.py, graph.py)
  -> build_ir()                                   bridge-slime-ir            (bridge/conversion/neutralize.py, bridge/content/ir.py)
  -> plan_extraction() / AssetExtractionProvider  bridge-extraction-plan     (bridge/conversion/extraction.py)
  -> materials / capabilities (data profiles)     semantic material + capability plans
  -> assemble_package()                           bridge-target-package      (bridge/conversion/targets.py)
  -> plan_registration() + safety gate            bridge-registration-plan
  -> plan_verification()                          bridge-verification-plan
  -> TargetRegistrationProvider (local only)
```

The same code serves SR2→SR1 and SR1→SR2. Only data differs: realm facts, profiles and the registration table. No module branches on a species name. The only species-specific code the design allows is an isolated Bridge behaviour adapter, referenced by id from a target behaviour profile.

## Modules

| Path | Role |
|---|---|
| `bridge/content/canonical.py` | canonical JSON/hash; `Evidence`, `DataEvidence`, `Status`; diagnostics |
| `bridge/content/ir.py` | `bridge-slime-ir` v1: strict validation, canonicalization, explicit migrations, payload guard |
| `bridge/content/graph.py` | typed nodes/edges (`EDGE_RULES`), criticality, iterative SCC cycles, Merkle closure hashes |
| `bridge/content/discovery.py` | generic BFS from definition roots through provider references |
| `bridge/registry/snapshot.py` | `bridge-registry-snapshot` v1, `AnchoredSnapshot`, `snapshot_from_scan` (reuses `checked_runtime_proof`) |
| `bridge/registry/reconcile.py` | identity (alias > native evidence > `slug-v1`), classification, alias manifests |
| `bridge/conversion/providers.py` | provider protocols, `NativeInstallProvider` / `UnavailableRegistrationProvider` fail-closed stubs |
| `bridge/conversion/manifest.py` | `bridge-content-source` v1 manifest provider (synthetic now, local exporter later) |
| `bridge/conversion/extraction.py` | content-addressed extraction plans, receipt validation, synthetic extractor |
| `bridge/conversion/materials.py` | neutral semantic materials, source/target profiles, template planning |
| `bridge/conversion/capabilities.py` | capability catalog, behaviour profiles, realization planning |
| `bridge/conversion/neutralize.py` | graph closure + registry facts → IR |
| `bridge/conversion/targets.py` | realm facts, asset transforms, packages, registration plans + gate, verification plans |
| `bridge/conversion/engine.py` | staged per-species engine, cache inputs, metrics |
| `bridge/conversion/batch.py` | batch classification/conversion, plort ownership, JSON + text reports |
| `bridge/conversion/selection.py` | deterministic complexity score and first-candidate selection |
| `bridge/conversion/cli.py` | `slimebridge-content` CLI (`python -m bridge.conversion.cli`) |
| `bridge/cache/store.py`, `bridge/cache/fs.py` | content-addressed product cache; confined atomic writer |
| `bridge/sdk/largo.py` | read-only UserLargo package contract (validate/hash/version/plan) |

Existing modules are reused unchanged: `bridge/content/scan.py` (`checked_runtime_proof`, `_slug`), `bridge/registry/content.py` (`ContentRegistry`, through `aliases_from_content_registry`). No pre-existing file was modified.

## Status and evidence semantics

- Registry classification: `SHARED_NATIVE` (use destination-native content, never convert), `SR1_ONLY`, `SR2_ONLY`, `BLOCKED_UNKNOWN` (exclusivity unproven: incomplete traversal, unidentified entries, unknown role or cross-realm plort overlap) and `QUARANTINED` (ambiguous identity, duplicate native ref, malformed entry, invalid id, alias conflict, quarantined source).
- Conversion final: `QUARANTINED` (any stage failed), `PENDING_ADAPTER` (an essential material or a REQUIRED/UNKNOWN capability lacks a realization, or a plort ownership conflict), `EXPERIMENTAL` (fully planned, never runtime-proven). `AVAILABLE` is reserved for runtime-anchored native content; a conversion never produces it.
- Components per species: `visual`, `material`, `behaviour` (`READY` / `DEGRADED` / `PENDING_ADAPTER` / `QUARANTINED` / `NOT_EVALUATED`) and `target_registration` (`NEEDS_LOCAL_RUNTIME_PROOF` when every operation names a verified API, `BLOCKED_ON_LOCAL_QUERY` otherwise, `REFUSED` if the safety gate fails).
- Data evidence: `RUNTIME_ANCHORED` survives only in an intact in-process `AnchoredSnapshot` from `snapshot_from_scan` over a scan passing `checked_runtime_proof`. JSON (and any self-declared claim) is downgraded to `STATIC`, and `STATIC` is never a COMPLETE traversal. A source provider's own `data_evidence` or `synthetic` can only lower the evidence. Synthetic inputs mark every product `synthetic: true`.

## Provider contract conventions (the local provider must follow these)

- Refs are native references inside one realm. Node ids are `<realm>:<ref>`.
- `definition_root(entry)` receives a classification `native` row (`native_ref`, `numeric_id`, `role`, `definition_hash`, `display_name`, `produces`, `quarantine`, `malformed`) and returns the object ref of that definition. It is also used to map plort registry refs to object refs.
- `inspect(ref)` returns `{ref, kind, hash, attributes, provenance, references}` or `None`. It raises `ProviderError` for read failures. `hash` is a locally computed SHA-256 of the object's serialized metadata/bytes. `attributes` must be canonical-JSON hashable: finite numbers and string keys only.
- Relations are the keys of `EDGE_RULES`. An unknown relation is preserved as `references`. Providers may promote an edge to critical, never demote one.
- Material slots: `uses_material` slot = renderer material index as a decimal string, in native order.
- Skinning: `uses_bone` slots must be exactly `"0".."n-1"` (bone index = weight index), plus at most one `"root"` slot. Anything else quarantines (`BONE_ORDER_INVALID`).
- Material attributes for `AttributeMaterialMapper`: `{"shader": <native shader identity>, "properties": [{"name", "type": texture|color|float|int|vector, "value"}]}`. Texture values are native refs.
- Behaviour attributes: `{"type": <native component type>}`. Definition attributes may carry `food_groups`. Favourites are `favourite` edges to `food` nodes.
- Associated content edges: `produces_plort`, `has_icon` (icons extract as sprites), `has_pedia` → `has_localization`, `has_market` on the plort, and `has_spawn`. Localized text never enters metadata: keys only, strings ≤ 512 characters, and payload keys are refused.

## Schemas (all versioned; newer versions are refused, older ones need explicit migrations)

`bridge-slime-ir` v1 · `bridge-dependency-graph` v1 · `bridge-registry-snapshot` v1 · `bridge-species-classification` v1 · `bridge-identity-aliases` v1 · `bridge-content-source` v1 · `bridge-material-profile` v1 · `bridge-target-material-profile` v1 · `bridge-behaviour-profile` v1 · `bridge-target-behaviour-profile` v1 · `bridge-target-profile` v1 · `bridge-extraction-plan` v1 · `bridge-target-package` v1 · `bridge-registration-plan` v1 · `bridge-verification-plan` v1 · `bridge-conversion-result` v1 · `bridge-batch-report` v1 · `bridge-cache-entry` v1 · `bridge-user-largo-package` v1 · `bridge-user-largo-plan` v1.

Python validators are authoritative. Synthetic examples of the input schemas are in `fixtures/cloud/pipeline/`.

## Determinism, caching, performance

- Every document is hashed via `canonical_json`. Set-like lists are sorted, and semantic sequences (material slots, bones) keep native order. Graph construction, discovery under truncation, classification and batch reports are independent of input order (tests use shuffles).
- Product key (`engine.cache_inputs`) covers the realms, the source build and evidence, the destination registry build, the target build/facts/profiles/registration table, the species' classification row and definition hash, its Merkle closure hash (every reachable node's kind, hash, resolution and attributes, plus edge typing), the source profile entries it actually uses, its plort identity mapping, its target-native plorts, the synthetic flag and converter/component versions. One changed texture rebuilds only the species that reach it. A new game build, converter version or target profile rebuilds everything for that target.
- Desktop-scale design: one shared discovery walk per batch, metadata-only hashing (asset bytes are hashed locally by providers), lazy conversion (`--only`) and a per-species cache. Species are independent after discovery, so a local implementation may parallelize them. The core stays single-threaded for determinism.
