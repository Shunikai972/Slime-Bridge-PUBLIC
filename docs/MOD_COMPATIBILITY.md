# Mod compatibility (`bridge/modcompat/`)

Static, metadata-only analysis of third-party SR1/SR2 mods: what a mod is, whether it can run on the other realm, what an adapter would need, and what must be asked locally first. Cloud never opens, loads, executes, decompiles or redistributes a mod binary. Everything binary-derived arrives as a LOCAL provider document produced by the local provider.

**Evidence:** IMPLEMENTED + UNIT_TESTED on the synthetic corpus `fixtures/cloud/mods/` (`tests/services/test_modcompat.py`). Every verdict about a real mod is NEEDS_LOCAL_RUNTIME_PROOF, or BLOCKED_ON_LOCAL_QUERY when the report lists a query. No real mod was analyzed.

## Flow

```
SRML modinfo.json ─┐
bridge-melon-metadata (LOCAL) ─┼─> bridge-mod-manifest ─┐
provider manifest ─┘                                  ├─> analyze() ─> bridge-mod-compat-report ─┬─> to_deploy_unit() ─> bridge-deploy-unit (kind MOD)
bridge-mod-static-evidence (LOCAL) ───────────────────┘                                     ├─> generate_adapter_skeleton() ─> templates
known_apis.json ──────────────────────────────────────┘    analyze_set() ─> bridge-mod-set-report (graph, cycles, conflicts, load order)
```

Loaders stay distinct: SR1 = SRML (Mono, `SRML/Mods/**`, `modinfo.json`); SR2 = MelonLoader (IL2CPP, `Mods/*.dll`, `[MelonInfo]`/`[MelonGame]`). A manifest whose loader contradicts its realm is rejected (`MANIFEST_LOADER_REALM_MISMATCH`).

## Documents

| Schema (v1) | Producer | Module | Notes |
|---|---|---|---|
| `bridge-mod-manifest` | Bridge parsers, or a provider | `manifest.py` | id, native id, name, version {text, parts}, realm, loader `SRML`/`MELONLOADER`/`UNKNOWN`, authors, dependencies {id, kind `MOD`/`ASSEMBLY`, constraint, optional}, load hints, declared game versions, provided assemblies, files {path, sha256, size}, source (`LOCAL_PATH` relative path or `PROVIDER`), `manifest_id` content hash |
| `bridge-melon-metadata` | LOCAL attribute reader (Q051) | `manifest.py` | extractor, synthetic flag, optional `declared_id`, assembly {name, file, target_framework}, `melon_info` {type_name, name, version, author}, `melon_games[]`, `platform_domain`, game versions, dependencies, additional files |
| `bridge-mod-static-evidence` | LOCAL static analyzer (Q050) | `evidence.py` | code assemblies, referenced assemblies with type/member names (strings only), Harmony patches (target string + prefix/postfix/transpiler/finalizer counts, PatchAll calls), native indicators (P/Invoke modules, detour indicators, function-pointer hooks), asset bundles, content files and content ids, config files, reflection flags, IL2CPP interop indicators |
| `bridge-mod-known-apis` | repository (`known_apis.json`), later the local provider | `analyzer.py` | loader/harmony/game/engine/interop assembly names per realm and a few loader API identifiers |
| `bridge-mod-compat-report` | `analyze()` | `analyzer.py` | classification + rule, portability, dependencies, APIs used, reference/Harmony/native summaries, capability rows, confidence + reasons, blockers, manual-review flags, local queries, diagnostics, `report_id` content hash |
| `bridge-mod-set-report` | `analyze_set()` | `analyzer.py` | per-mod reports, dependency graph, cycles, missing deps, duplicates, version conflicts, content-id/file conflicts, load order, summary, `set_id` |
| `bridge-mod-adapter-skeleton` | `generate_adapter_skeleton()` | `skeleton.py` | `adapter.json`, `<Class>Adapter.cs`, `CAPABILITIES.md`, `TEST_CHECKLIST.md`, `LOCAL_QUERIES.md` |

Input formats for `parse_manifest(fmt, raw, ...)`: `SRML_MODINFO`, `MELON_METADATA`, `BRIDGE_MANIFEST`. Every parser returns `(manifest | None, diagnostics)` and never raises on content. Any error diagnostic means no manifest.

SRML facts used (repository evidence only): `modinfo.json` keys `id`/`name`/`version`/`author`/`dependencies` (`mods/sr1-bridge/modinfo.json`); `version` must be `X.Y[.Z]` integers or SRML rejects the mod at PreLoad (`docs/RECON.md`). The dependency string syntax, `load_after`/`load_before` and id case rules are **not** established here (Q052): `"<id> <version>"` and `"<id>@<version>"` are read as a minimum version and flagged `SRML_DEPENDENCY_SEMANTICS_UNVERIFIED`. Ids are lower-cased for comparison (`MOD_ID_NORMALIZED`). A deploy-unit key replaces `_` with `-` (unit keys do not allow `_`); set analysis reports ids that collide this way (`DEPLOY_KEY_COLLISION`).

MelonLoader facts used: SR2's `MelonGame` identity is `("MonomiPark", "SlimeRancher2")` (`docs/RECON.md`, `mods/sr2-bridge/BridgeMod.cs`). No other game identity is accepted. A universal Melon (no `MelonGame`) needs an explicit `realm_hint` and is flagged `MELON_GAME_UNDECLARED`. Without `declared_id` the id is derived from the MelonInfo name (`MOD_ID_DERIVED`).

Version constraints use one Bridge grammar: `*`, or comma-joined clauses `<op><X[.Y[.Z[.W]]]>` with `op` in `= > >= < <= ^ ~`.

## Classification (first matching rule wins)

Rules R1 to R7 only look at evidence that is schema-valid **and** bound to the manifest. Bound means the same mod id (and `manifest_id` when set), every described file is a manifest file with the same sha256, and every manifest `.dll`/`.exe` was analyzed.

| Rule | Condition | Classification |
|---|---|---|
| R0 | no evidence, invalid evidence, or unbound evidence | `UNKNOWN` |
| R1 | any native indicator: P/Invoke module, detour indicator, function-pointer hook | `NATIVE_DEEP_PATCH` |
| R2 | any Harmony patch entry or `PatchAll` call | `HARMONY_PATCH` |
| R3 | own code; every loader/game/interop reference is catalogued; no foreign-loader or undeclared third-party assembly; no reflection flag | `KNOWN_API_GAMEPLAY` |
| R4 | own code, anything else | `UNKNOWN` |
| R5 | no code; asset bundles, content files or content ids | `CONTENT_ONLY` |
| R6 | no code, no content; config files | `CONFIG_ONLY` |
| R7 | nothing | `UNKNOWN` |

Reference assemblies are categorized per source realm from `known_apis.json`: own, system, loader, harmony, interop, game, engine, foreign loader (the other realm's loader), declared dependency, third party. Game types are deliberately absent from the catalogue, so any game-type reference makes a code mod `UNKNOWN` (R4) until the local provider supplies a verified catalogue (Q053/Q054). That is intended: the analysis is never optimistic.

## Portability, adapter rows and deploy status

| Classification | Same realm | Cross realm |
|---|---|---|
| `CONFIG_ONLY` | `PORTABLE` | `ADAPTER_REQUIRED` (`config.schema_mapping`, Q056) |
| `CONTENT_ONLY` | `PORTABLE` | `ADAPTER_REQUIRED` (`content.asset_conversion` Q011; `content.registration` Q007 for SR1 / Q008 for SR2) |
| `KNOWN_API_GAMEPLAY` | `PORTABLE` | `ADAPTER_REQUIRED`, or `MANUAL_PORT` if any row is MANUAL |
| `HARMONY_PATCH` | `PORTABLE`, flagged `HARMONY_TARGETS_UNVERIFIED` (Q055) | `MANUAL_PORT` |
| `NATIVE_DEEP_PATCH` | `PORTABLE` but blocked by `NATIVE_HOOKS_UNREVIEWED` | `NOT_PORTABLE` |
| `UNKNOWN` | `UNKNOWN` | `UNKNOWN` |

A loader of `UNKNOWN` always gives `UNKNOWN` portability (`LOADER_UNKNOWN` blocker). A cross-realm verdict is never better than its worst capability row: `UNSUPPORTED` gives `NOT_PORTABLE` and `MANUAL` gives `MANUAL_PORT`.

Capability rows (`required_capabilities`) map a source capability to a target capability with status `MAPPED` (a catalogue counterpart exists; still NEEDS_LOCAL_RUNTIME_PROOF), `LOCAL_QUERY`, `MANUAL` or `UNSUPPORTED`. The Bridge vocabulary is `config.schema_mapping`, `content.asset_conversion`, `content.registration`, `game.uncatalogued_api`, `harmony.patch_retarget`, `il2cpp.interop`, `native.hook`, `runtime.retarget` (net472 ↔ net6.0 from the repository's csproj files), `unity.engine_api`, plus the catalogue's `mod.lifecycle` and `mod.metadata`.

`to_deploy_unit(manifest, report)` produces a `bridge-deploy-unit` with key `mod:<id with _→->:<target realm>` and kind `MOD`:

| Unit status | When |
|---|---|
| `QUARANTINED` | `NATIVE_DEEP_PATCH`, `NOT_PORTABLE`, or an invalid manifest |
| `READY` | `PORTABLE`, same realm, no blocker, no open local query |
| `EXPERIMENTAL` | `PORTABLE`, same realm, no blocker, but open local queries (for example a same-realm Harmony mod) |
| `PENDING_ADAPTER` | everything else (cross realm, `UNKNOWN`, missing or conflicting dependencies) |

A cross-realm unit carries no files, because the adapter does not exist yet. `requires` lists required mod dependencies, so the deploy planner refuses a missing one.

## Confidence

Confidence is integer points divided by 100, and every applied rule appears in `confidence_reasons`: `MANIFEST_VALID` +10, `STATIC_EVIDENCE_BOUND` +40, `ALL_REFERENCES_CATALOGUED` +10, `NO_REFLECTION` +10 / `REFLECTION_USED` −15, `SAME_REALM` +10, `HARMONY_REFERENCED_WITHOUT_PATCHES` −10, `HARMONY_TRANSPILERS` −10, `THIRD_PARTY_ASSEMBLY_UNDECLARED` −5, `DEPENDENCY_PROBLEM` −10, `LOADER_UNKNOWN` −20. Static analysis is capped at 0.85 and an `UNKNOWN` classification at 0.30. With no evidence the result is `UNKNOWN` at 0.10, and an invalid manifest scores 0.00.

## Set analysis

`analyze_set(manifests, evidences, target_realm)` excludes invalid manifests (`SET_MANIFEST_INVALID`) and reports the following:

- Duplicate ids, case-insensitive (`MOD_ID_DUPLICATE`, plus `MOD_VERSION_CONFLICT` when versions differ). The highest version is used for the graph.
- Deploy-key collisions.
- Missing required dependencies (optional ones are info).
- Version conflicts against the present version.
- Dependency cycles (deterministic iterative Tarjan).
- Two mods claiming one content id or one file path, case-insensitive. `FILE_CONFLICT` is a warning when the bytes are identical.
- A deterministic load order: prerequisites first, then `load_after`/`load_before` hints, ties broken by id. `unordered` lists cycle members and mods blocked only by hints (`LOAD_ORDER_UNRESOLVED`).

Evidence can be given as a list (matched by `mod_id`; two documents for one id are both ignored) or as a mapping keyed by mod id.

## Adapter skeletons

`generate_adapter_skeleton(report, out_root=..., subdir='adapters')` writes `<subdir>/<id>.<sr1|sr2>/`. It skips a same-realm `PORTABLE` mod and refuses an invalid or tampered report. The skeleton is deliberately inert:

- The C# class uses only loader lifecycle names that the repository establishes: SRML `ModEntryPoint.PreLoad/Load/PostLoad` (net472), and MelonLoader `MelonMod.OnInitializeMelon/OnUpdate/OnDeinitializeMelon` with `[MelonInfo]` and `[MelonGame("MonomiPark", "SlimeRancher2")]` (net6.0).
- Every binding is a `// TODO(LOCAL_QUERY Qxxx)` comment. Q056, which confirms MAPPED rows on the exact lab build, is always present.
- Nothing from the source mod is reproduced. Only the id, a display name reduced to `[A-Za-z0-9 _-]` and the numeric version reach the output. Type, member, Harmony-target, file and config names from the evidence never do; Harmony patches appear only as a count.
- Output is byte-deterministic.
- Files are written through `bridge.cache.fs.atomic_write`. `subdir` must be a safe relative path. A symlinked root or a symlink or hard link on any output path refuses the whole write before any file is written.

## Trust boundaries

- **Untrusted:** `modinfo.json`, provider manifests, `bridge-melon-metadata`, `bridge-mod-static-evidence`, compat reports handed back to the skeleton generator, and the evidence lists passed to `analyze_set`.
  - Inputs are bounded: 256 KiB per manifest document, 1 MiB per evidence document, nesting depth 32, and per-list limits.
  - Strings must be printable, and lone surrogates are rejected.
  - Duplicate JSON keys, NaN and infinite numbers are rejected.
  - Paths must be safe relative POSIX paths, and a case-insensitive collision is an error.
  - Hostile content produces diagnostics, never exceptions. A randomized mutation test covers this.
- **Trusted program inputs:** `target_realm` and the `known_apis` catalogue. Invalid values raise `ValueError`. A the local provider catalogue is validated with `load_known_apis()` before use.
- **Synthetic vs real:** reports carry `data_evidence` (`SYNTHETIC`, `STATIC` or `NONE`). Synthetic input can never support a native claim.
- **Never in Git:** mod binaries, extracted assets, decompiled or reconstructed code, saves and secrets. Fixtures are small fictional JSON documents only.

## What the local provider must provide locally

1. **A static evidence extractor (Q050).** It reads each mod's own assemblies with a metadata reader, without executing or loading them into a game, and writes `bridge-mod-static-evidence` v1. It must also hash every file, so that binding works against the manifest's file list.
2. **A MelonLoader attribute reader (Q051).** It writes `bridge-melon-metadata` v1 from `[MelonInfo]`, `[MelonGame]` and the dependency attributes of the lab's MelonLoader 0.7.3.
3. **Real API catalogues (Q053/Q054).** These are verified SRML 0.2.1.0 and SR2 MelonLoader/community API identifiers (with signatures and the lab build hash) that extend `known_apis.json`. Each entry stays `NEEDS_LOCAL_RUNTIME_PROOF` until a LAB observation exists.
4. **Answers to the queries below**, in the `docs/LOCAL_QUERIES.md` answer format.

## LOCAL_QUERY list (block Q050–Q057, reserved for mod compatibility)

These are not yet consolidated into `docs/LOCAL_QUERIES.md`; the integrator owns that file and may renumber the block. The analyzer also reuses Q007, Q008 and Q011 from that file.

### Q050: Local static evidence extractor for mod assemblies
- **Needed:** A tool that turns one mod folder into `bridge-mod-static-evidence` v1. It covers referenced assemblies and type/member names, Harmony usage (attribute- and call-based patches with targets and patch-method counts, `PatchAll`), P/Invoke modules, detour and function-pointer hook indicators, reflection flags, IL2CPP interop and type-injection indicators, asset bundles, content files and ids, and config files. Each item comes with sha256 and size.
- **Why:** Without bound evidence every mod is `UNKNOWN` (R0).
- **Blocks:** Any real classification.
- **Does NOT block:** Manifest parsing, set analysis on manifests, the deploy-unit shape, skeleton generation.
- **Expected local evidence:** The extractor version, one evidence document per lab mod (kept in the ignored `Lab/`), and a statement that no mod code was executed.

### Q051: Local MelonLoader attribute reader
- **Needed:** `bridge-melon-metadata` v1 from the lab's MelonLoader 0.7.3 attribute set. This includes how optional, additional and incompatible dependency attributes map to `dependencies` entries, and how `platform_domain` is determined.
- **Blocks:** Real SR2 manifests.
- **Expected local evidence:** One metadata document per SR2 lab mod and the attribute type names read.

### Q052: SRML `modinfo.json` dependency and load-order semantics
- **Needed:** The exact SRML 0.2.1.0 dependency string syntax and version comparison, `load_after`/`load_before` behaviour, and id case sensitivity.
- **Why:** These are currently read as a minimum version and treated as hints (flagged).
- **Blocks:** Trusting SRML dependency and version verdicts.
- **Expected local evidence:** SRML source or signature references (not committed) and one LAB load with two synthetic test mods.

### Q053: SR1 SRML and game API catalogue
- **Needed:** Verified mod-facing SRML API identifiers (types and members) and any Assembly-CSharp members that Bridge adapters may bind, for the lab build hash.
- **Blocks:** `KNOWN_API_GAMEPLAY` for any SR1 mod that touches more than the lifecycle.
- **Expected local evidence:** A catalogue JSON validated by `load_known_apis()`, with sources per entry.

### Q054: SR2 MelonLoader and community API catalogue
- **Needed:** The same as Q053 for SR2 v0.6.2, including whether Starlight or SR2MP surfaces (`ApiHandlers.RegisterMod` per `docs/RECON.md`) run on this build at all. Licence boundaries must be respected: the Starlight SAL restricts reuse of its source, so only public API documentation may be used.
- **Blocks:** `KNOWN_API_GAMEPLAY` for SR2 mods beyond the lifecycle and attributes.
- **Expected local evidence:** A catalogue JSON, plus a LAB compatibility observation for any community API.

### Q055: Harmony patch target verification
- **Needed:** For each Harmony target listed in the evidence, whether that member exists with the same signature in the exact lab build of the realm in question.
- **Why:** Same-realm Harmony mods are `EXPERIMENTAL` until this is answered. Cross-realm Harmony patches are always a manual port.
- **Expected local evidence:** A per-target present/absent/signature-changed list with the build hash.

### Q056: Cross-realm adapter capability bindings
- **Needed:** For each capability row (`config.schema_mapping`, `unity.engine_api`, `il2cpp.interop`, and every `MAPPED` row), the verified target-side binding or a decision that it is impossible.
- **Blocks:** Turning any adapter skeleton into code.
- **Expected local evidence:** A LAB observation per binding.

### Q057: Native hook and detour policy
- **Needed:** Whether a native-hook mod may run in the lab at all, and under what isolation (separate lab copy, save isolation, process verification).
- **Why:** Bridge never enables these automatically; they are `QUARANTINED`.
- **Expected local evidence:** A written policy, and the lab isolation used.

## Corpus and tests

`fixtures/cloud/mods/` contains 22 fictional cases (`case-*.json`, all ids `fixture.*`) and `set-sr1.json`:

- Every classification is covered, across both loaders and realms and all three input formats.
- Rejected inputs: malformed JSON, an SRML `-dev` version, an unknown loader enum, a loader/realm mismatch, and a Melon with no game.
- Set-level cases: a missing dependency, a dependency cycle, a duplicate id differing only in case, a version conflict, and a case-insensitive content-id and file conflict.
- Specific mods: a Harmony-heavy mod, a native-detour mod, and content-only packs.

`tests/services/test_modcompat.py` checks each case's expected parse result, classification, rule, portability and unit status for both targets. It also checks:

- Hostile, oversized and mutated input.
- Evidence binding.
- Determinism.
- Set findings.
- Deploy units.
- Skeleton safety: TODO markers, a deny-list of game namespaces, a line-by-line allow-list for the C# file, no copied evidence text, escape and symlink refusal, and byte-identical output.
