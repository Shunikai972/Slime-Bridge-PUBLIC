# Local queries

Native facts the Cloud content pipeline  and the platform branch (Q019+) need from the user-owned games and the owner. Cloud did not guess any of them. Every entry names the exact interface that waits for it; everything else in the pipeline is implemented and synthetic-tested without it. Names that already appear in repository evidence (`docs/CONTENT_SCAN.md`, `docs/RECON.md`, `PROJECT_STATE.md`) are quoted only as starting points to confirm, never as verified API.

Answer format: append `Status / local answer:` with the realm, build hash, exact type/member signatures, evidence level and the LAB artifact path.

## Q001 — SR1 native base-slime registry traversal
Needed:
- The exact registry object(s) enumerating every SR1 slime definition at runtime in the lab build (`Assembly-CSharp.dll` 65eb0e11…), and per definition: stable reference (enum name or object ref), numeric `Identifiable.Id`, `IsLargo`, `CanLargofy`, `BaseSlimes`, `Diet` produces/food groups/favourites, and plort `Identifiable.Id`s.
- How to produce the independent traversal receipt `read_runtime_snapshot(..., traversal_anchor=...)` already requires (registry identity/hash, expected = observed counts, unique object refs).
Why:
- `classify_species` may only claim SR1_ONLY/SR2_ONLY when both registries are COMPLETE, and COMPLETE requires a runtime-anchored snapshot (`snapshot_from_scan` over a scan that passes `checked_runtime_proof`).
Blocks:
- Real `SourceRegistryProvider.read_registry()` for SR1 (`NativeInstallProvider('SR1', …)` currently raises `NotAvailableInCloud` with Q001–Q003).
Does NOT block:
- Reconciliation logic, graph, IR, conversion engine, cache, batch, selection, Largo import (all synthetic-tested).
Expected local evidence:
- A plugin-written runtime snapshot (schema of `bridge.content.scan.read_runtime_snapshot`) plus traversal receipt for the LAB build, and the member signatures it reads (starting points from prior scans: `MainSlimeDefinitions.Slimes`, `IsLargo`, `CanLargofy`, `BaseSlimes`, `Diet.Produces`).

Status / local answer:
- Realm: SR1 (Unity 2019.4.29f1, Mono, SRML v0.2.1.0).
- Build hash (CORRECTED 2026-10-05, recomputed with sha256sum on `Lab/Games/SR1/SlimeRancher_Data/Managed/Assembly-CSharp.dll`, equal to `Lab/Games/SR1/slimebridge-lab.json`): SHA256 `65eb0e111fd6aa4c9b2ea0c6e17758b17a4d91fc097c3423af443e21237e9a2b`, 2 940 928 bytes. The earlier value `…af4433d9c79f977` was a false subagent claim committed without verification. MVID `534ea7cb-…` and all TypeDef tokens/RIDs below: UNVERIFIED (subagent report only, not independently checked). Member names below were cross-checked only as present strings in the assembly (`slimeDefinitionsByIdentifiable`, `GetSlimeByIdentifiableId`, `IsPlort`, `PLORT_CLASS`, `identifiablePrefabDict`).
- Candidate member inventory (old subagent report, not independent proof; every token/RID in this inventory is UNVERIFIED):
  * `MainSlimeDefinitions` n'existe pas dans `Assembly-CSharp.dll` ni dans `SRML.dll` (0 occurrence dans TypeDef/Field).
  * Registre central : `SlimeDefinitions` (`TypeDef Token: 0x0200012D`, RID 301), sous-classe de `UnityEngine.ScriptableObject`.
  * Accès singleton : `GameContext.Instance.SlimeDefinitions` (`SRSingleton<GameContext>.Instance.SlimeDefinitions`).
  * Tableau de définitions : `public SlimeDefinition[] Slimes { get; }` (champ sérialisé `public SlimeDefinition[] slimes`, RID `0x04000843`).
  * Index interne : `private Dictionary<Identifiable.Id, SlimeDefinition> slimeDefinitionsByIdentifiable` (RID `0x04000844`), accesseur `public SlimeDefinition GetSlimeByIdentifiableId(Identifiable.Id id)`.
  * Identité numérique : `Identifiable.Id` (`TypeDef Token: 0x02000215`, RID 533) enum `System.Int32` (325 constantes statiques). Valeur numérique : `(int)definition.IdentifiableId`. Nom symbolique : `Enum.GetName(typeof(Identifiable.Id), definition.IdentifiableId)`.
  * Identité d'objet en mémoire : `$"SlimeDefinition:{definition.GetInstanceID()}"`.
  * Définition `SlimeDefinition` (RID 300) : `IdentifiableId`, `Name`, `IsLargo`, `CanLargofy`, `BaseSlimes`, `Diet`, `AppearancesDefault`.
  * Plorts : `Diet.Produces` (`Identifiable.Id[] produces`, RID `0x04000854`), validés avec `Identifiable.IsPlort(id)` (`Identifiable.PLORT_CLASS`).
  * Prefab : `GameContext.Instance.LookupDirector.GetPrefab(Identifiable.Id id)`.
- Cycle de vie : Exécution sur Main Thread obligatoire. Point d'ancrage recommandé : `SRModLoader.CurrentLoadingStep == SRModLoader.LoadingStep.FINISHED && !GameContext.Instance.AutoSaveDirector.IsLoadingGame()`.
- Evidence/status CORRECTED: prior subagent IL/MVID/token reports are not independently verified. The repaired plugin compiles against the exact LAB assemblies and uses the member paths above; compilation verifies usable API signatures, not runtime completeness. Runtime: NEEDS_LOCAL_RUNTIME_PROOF; no snapshot has been collected by this correction slice.

## Q002 — SR1 definition → asset dependency walk
Needed:
- Exact runtime/serialized path from an SR1 slime definition to: prefab (`LookupDirector.GetPrefab` is the recorded candidate), appearance objects (`AppearancesDefault`), their structures, meshes (incl. `SkinnedMeshRenderer` bones/rootBone order), materials, textures, face/expression assets, animator controller/clips, VFX, audio, icon sprite, pedia/localization keys, spawn data, plort prefab/icon/market value.
Why:
- `AssetDependencyProvider.inspect(ref)` must report typed references (`EDGE_RULES` in `bridge/content/graph.py`) so `discover()` reaches every asset from one definition without filenames.
Blocks:
- Real SR1 `AssetDependencyProvider` / a local exporter writing `bridge-content-source` manifests.
Does NOT block:
- Generic discovery, graph analysis, extraction planning (synthetic-tested).
Expected local evidence:
- Field/member names per hop with the LAB build hash, one exported `bridge-content-source` manifest for one SR1 species (kept under ignored `Lab/GeneratedContent`).

## Q003 — SR1 slime material/shader inventory (source and target profile)
Needed:
- Shader identities used by SR1 slime, face, VFX and icon materials, each shader's property names/types, which properties are required, UV set per texture, render state (alpha mode, cull, zwrite), and which of them can be instantiated for new content in SRML.
Why:
- `bridge-material-profile` (source) and the `material_profile.templates` of `bridge-target-profile` (target) are data; native names are never written in code. The built-in SR1 target profile has no templates, so every SR1-target material is PENDING_ADAPTER today.
Blocks:
- SR1 source material mapping; SR2→SR1 material construction plans.
Does NOT block:
- Semantic material model, template selection, invalidation scoping.
Expected local evidence:
- Profile JSON (validated by `validate_source_profile` / `validate_target_profile`) with each entry citing the material/shader asset it came from.

## Q004 — SR2 native base-slime registry traversal (IL2CPP)
Needed:
- As Q001 for SR2 v0.6.2 (`GameAssembly.dll` 91f63654…): registry object(s), stable reference id (e.g. `SlimeDefinition.<Name>` strings already observed in `LookupDirector`), role/eligibility/base slimes/diet/plort relations, through Il2CppInterop.
Why:
- Same exclusivity gate as Q001; Addressables catalogue paths are explicitly not registry proof (`CATALOG_NOT_ACTIVE_REGISTRY`).
Blocks:
- Real SR2 `SourceRegistryProvider` (`NativeInstallProvider('SR2', …)` raises with Q004–Q006).
Does NOT block:
- Everything listed under Q001.
Expected local evidence:
- Runtime snapshot + traversal receipt for the SR2 LAB build and the interop signatures used.

Status / local answer:
- Realm: SR2 (Unity 2021.3.28f1 x64, MelonLoader v0.7.3, .NET 6).
- Build hash (CORRECTED 2026-10-05, recomputed with sha256sum, equal to `Lab/Games/SR2/slimebridge-lab.json`): `GameAssembly.dll` SHA256 `91f636542e76fdc2904228bc2ccef74bb77eda1229d83b4d82713d392bc40465` (57 852 416 bytes); `global-metadata.dat` SHA256 `50962da11e0f533c742335d2931f9b0f100883660a4be1c385e30c2c841aefe9` (17 701 080 bytes). The earlier values `9558ad9f…` (109 242 368 bytes) and `a937a544…` were false subagent claims committed without verification. The snapshot must bind to native `GameAssembly.dll`, not the regenerated MelonLoader interop `Assembly-CSharp.dll`. All tokens, native offsets and RVAs below are UNVERIFIED (subagent report only).
- Candidate IL2CPP member inventory (old subagent report; all tokens/offsets/RVAs and behavioral claims below remain UNVERIFIED until checked against runtime):
  * Registres centraux : `Il2Cpp.GameContext.Instance.SlimeDefinitions` (`Il2Cpp.SlimeDefinitions`) et `Il2Cpp.GameContext.Instance.LookupDirector` (`Il2Cpp.LookupDirector`).
  * Tableau ordonné : `SlimeDefinitions.Slimes` (`Il2CppReferenceArray<SlimeDefinition>`, Token `0x6001faf`, offset natif `0x18`).
  * Dictionnaires : `SlimeDefinitions._slimeDefinitionsByIdentifiable` (`Dictionary<IdentifiableType, SlimeDefinition>`) et `LookupDirector._identifiableTypeByRefId` (`Dictionary<string, IdentifiableType>`).
  * Règle d'accès Identifiant : Lire directement le champ sérialisé `referenceId` sur `IdentifiableType` (Token `0x60008e9`, offset `0x18`). Interdiction d'appeler l'accesseur de propriété `ReferenceId` qui déclenche un hachage paresseux avec mutation d'état natif (`initializedHashId`).
  * Détection Largo : `IsLargo == true` impose strictement 2 `BaseSlimes` résolus dans le registre global.
  * Plorts : `Diet.ProduceIdents` partitionné avec `IsPlort == true`.
  * Triple ancrage d'intégrité de traversée : Égalité stricte des décomptes entre `Slimes.Length`, `_slimeDefinitionsByIdentifiable.Count` et `_identifiableTypeByRefId` (filtré sur `SlimeDefinition`).
- Cycle de vie : Main Thread obligatoire.
- Evidence/status CORRECTED: previous Cpp2IL/token/native-behavior claims were not independently verified. The repaired plugin compiles against this LAB's interop assemblies and reads the direct referenceId field; compilation is not IL2CPP execution proof. Actual collector checks Slimes length against the native definition index and each indexed object, not the reported third lookup count. Runtime: NEEDS_LOCAL_RUNTIME_PROOF.

## Q005 — SR2 definition → asset dependency walk
Needed:
- As Q002 for SR2 (appearance/structure/mesh/material/texture/face/animator/VFX/icon/pedia/spawn/plort), including whether assets must be loaded through Addressables at runtime and how to obtain metadata hashes without redistributing bytes.
Why / Blocks / Does NOT block:
- As Q002, for the SR2 provider.
Expected local evidence:
- One exported SR2 `bridge-content-source` manifest for one SR2 species (ignored Lab path) plus the member chain.

## Q006 — SR2 slime material/shader inventory (source and target profile)
Needed:
- As Q003 for SR2 (URP/2021.3 shaders used by slimes): property names/types/required/UV/render state, and which shaders a MelonLoader mod can instantiate.
Blocks:
- SR2 source material mapping; SR1→SR2 material construction plans (built-in SR2 target has no templates).
Does NOT block / Expected local evidence:
- As Q003.

## Q007 — SR1 registration operations (SRML 0.2.1.0)
**STATUS: RESOLVED for SR1 (runtime-proven 2026-10-07).** The verified API map, placeholder
policy, receipts and evidence live in `docs/NATIVE_REGISTRATION.md`; the protected run
`Lab/RuntimeArtifacts/species-import-20261007-222801-9dfd6cd9ca47` registered 8 imported species
with native read-backs on every index and executed spawn / vacpack / save-roundtrip / release ops.
Deliberately not yet resolved inside Q007: market/economy registration for imported plorts
(recorded local query) and final visuals (later visual phase).
Needed:
- Exact verified calls (or a decision that a Bridge-side registry is required) for each `REGISTRATION_OPS` entry on SR1: reserve identity / new `Identifiable.Id`, plort, icon, material construction, prefab, slime definition, diet, behaviour attach, localization, pedia, market, and save persistence binding.
Why:
- Registration plans carry `native_api: None` + `local_query: Q007` for every SR1 operation; nothing native is invented.
Blocks:
- `TargetRegistrationProvider` for SR1 and the SR2→SR1 runtime proof.
Does NOT block:
- Package assembly, plan ordering/safety gate, verification planning.
Expected local evidence:
- SRML source/decompiled-signature references (not committed), a minimal LAB mod registering one synthetic test identity, and a save/reload observation.

## Q008 — SR2 registration operations (MelonLoader 0.7.3 / IL2CPP, v0.6.2) — RESOLVED (registration); SR2 LAB ops edge still open

Resolved evidence: runtime-created `IdentifiableType`/`SlimeDefinition` ScriptableObjects CAN be
created and registered on v0.6.2 without Starlight (Starlight not required for this path):
`Sr2SpeciesRegistrar` registered 4/4 READY species with all native read-backs true in protected
LAB run `Lab/RuntimeArtifacts/species-import-20261008-001304-d9e239d4af7d` (see
`docs/NATIVE_REGISTRATION.md`). SR2 needs no injected MonoBehaviour adapter for registration;
the eat-map composition uses the public `SlimeDiet.EatMapEntry` surface because the native
`SlimeDiet.RefreshEatMap` NREs on runtime-created definitions. Still open: the SR2 LAB ops edge
(spawn/vacpack/save/release/query) and Largo registration on SR2.

## Q009 — SR1 slime behaviour component inventory
Needed:
- Native component types present on SR1 slime prefabs and what each does, mapped to the canonical capabilities in `bridge/conversion/capabilities.py` (or `null` for inert/cosmetic), plus which capabilities SR1 can realize natively for a new species.
Why:
- `bridge-behaviour-profile` (source) and the target `behaviour_profile` are data. Unmapped components become `unknown.<type>` → PENDING_ADAPTER, never dropped.
Blocks:
- Accurate SR1 behaviour mapping; SR1 target capability support beyond core vacpack/diet/plort.
Does NOT block:
- Capability model and degradation policy.
Expected local evidence:
- Profile JSON with component type names from the LAB build and one observation per mapped capability.

## Q010 — SR2 slime behaviour component inventory
Needed / Why / Blocks / Does NOT block / Expected local evidence:
- As Q009 for SR2.

## Q011 — Asset transform feasibility across Unity versions/runtimes
Needed:
- How each artifact kind is rebuilt for the target: mesh (incl. skin weights, bone order, bindposes), texture (formats supported by 2019.4 vs 2021.3), animation clip rebinding, animator controller, sprite, audio and optional VFX; whether AssetBundles must be built with the exact target editor version (2019.4.29f1 / 2021.3.28f1) or runtime construction suffices on Mono vs IL2CPP.
Why:
- `asset_transforms` names a transform per artifact with `evidence: BLOCKED_ON_LOCAL_QUERY`.
Blocks:
- Materializing target packages; any rendering proof.
Does NOT block:
- Package metadata, registration/verification planning.
Expected local evidence:
- One mesh + one texture round-tripped per direction in LAB with screenshots/hashes (outputs ignored).

## Q012 — Canonical identity evidence for shared species
Needed:
- Confirmation on both complete registries that the deterministic slug rule (`slug-v1`, from `bridge.content.scan._slug`) maps every shared base species to one canonical id, and an alias list for any mismatch (renames, different spellings).
Why:
- A wrong identity either duplicates a shared species (converted copy) or hides an exclusive. `CROSS_REALM_PLORT_OVERLAP` blocks suspected renames until resolved.
Blocks:
- Trusting the first real classification report.
Does NOT block:
- Any code path; aliases are data (`bridge-identity-aliases`).
Expected local evidence:
- `classify` output on real anchored registries plus an alias manifest whose rows carry evidence strings.

## Q013 — Plort registry entries in both realms
Needed:
- Plort definitions as registry entries (reference id, numeric id, produced-by relation, market data) in each runtime snapshot.
Why:
- Plort identity drives target-native detection (`REFERENCE_NATIVE_PLORT` vs `REGISTER_PLORT`), the cross-realm overlap guard and batch plort ownership.
Blocks:
- Accurate plort classification; the existing scanner indexes only slime definitions.
Does NOT block:
- Slime classification (plorts fall back to derived ids with diagnostics).
Expected local evidence:
- Runtime snapshots including plort rows.

## Q014 — Localization and Slimepedia key systems
Needed:
- How each game keys species names/descriptions/pedia pages, how a mod adds keys, and what may be copied (keys only; text is proprietary and never enters manifests).
Blocks:
- `REGISTER_LOCALIZATION` / `REGISTER_PEDIA` operations.
Does NOT block:
- Everything else (optional operations).
Expected local evidence:
- Key formats and table names from the LAB builds.

## Q015 — Save persistence of mod-registered content
Needed:
- How each game serializes an identity that does not exist in vanilla, what happens when the mod is absent on load, and how to bind persistence safely (the plan's `BIND_PERSISTENCE` step).
Why:
- A converted species in a save must never corrupt or orphan native save data; this interacts with the existing protected-save rules in `PROJECT_STATE.md`.
Blocks:
- Any persistent use of converted species.
Does NOT block:
- Planning, packaging, LAB render/vacpack proof without saving.
Expected local evidence:
- LAB save/reload with and without the mod, hashed before/after under the existing save guard.

## Q016 — Extraction route for user-owned installs
Needed:
- Which local route produces asset files for a plan item (AssetRipper export of the user's install, runtime export from the plugin, or Addressables loading), the output format per `extract_as`, and how the local `AssetExtractionProvider` reports `source_hash`/`sha256`/`location` receipts.
Why:
- `plan_extraction` and `validate_receipt` are implemented; actual extraction is local only (`extract` CLI refuses non-synthetic sources with exit 3).
Blocks:
- Real artifacts for packages.
Does NOT block:
- Planning, receipt validation, cache keys.
Expected local evidence:
- One receipt set for one species validated by `validate_receipt`, outputs under ignored `Lab/GeneratedContent`.

## Q017 — Food and food-group identity across realms
Needed:
- Native food/food-group identities in both games and their mapping to canonical `food.<slug>` / `food_group.<slug>` ids used in packages.
Why:
- Diet registration needs target-native food identities; packages currently carry derived ids with `identity_evidence: DERIVED`.
Blocks:
- `REGISTER_DIET` on real targets.
Does NOT block:
- Visual conversion and every other registration step.
Expected local evidence:
- Food registry rows (like Q013) from both runtime snapshots.

## Q018 — Largo eligibility and native Largo registration
Needed:
- Runtime-anchored `CanLargofy`/`BaseSlimes` per base species and how a user-authored Largo is registered on each target (relationship to Q007/Q008).
Why:
- `bridge/sdk/largo.py` validates and plans user packages but reports `PARENT_ELIGIBILITY_UNVERIFIED` until this exists.
Blocks:
- Registering user Largos (never modelling them; models remain the user's).
Does NOT block:
- Package validation, hashing, versioning, planning.
Expected local evidence:
- Eligibility fields in the Q001/Q004 snapshots and one LAB registration of a synthetic test Largo.

## Q019 — SR2 native save file name format
Needed:
- The exact native name `CounterpartCreator` reports as `save_file` for a created SR2 game (summary/group naming under the storage provider), and whether it is stable across sessions.
Why:
- `bridge/counterpart/realms.py` bounds SR2 filenames only by `[A-Za-z0-9_.-]{1,128}`; SR1 has an exact `{save_id}_<n>` pattern from the runtime-proven oracle.
Blocks:
- A strict SR2 filename check in the realm convention and in a future SR2 host matcher.
Does NOT block:
- The generic journal, policy, verification, simulator (synthetic-tested).
Expected local evidence:
- One LAB creation receipt + the storage listing proving the name, with the build hash.

## Q020 — SR2 counterpart creation end-to-end (runtime) and lifting the BridgeHost SR2 fence
Needed:
- A protected LAB run where an SR1-source profile gets an SR2 counterpart through `CounterpartCreator` (Begin/Step/Verify), native readback of the created save and its hash, then link.
- The local decision to allow SR2 in `BridgeHost.enqueue_creation_command` (today refused by design, see `tests/test_counterpart.py::test_sr2_creation_and_malformed_ids_are_refused`) with realm-aware provenance matching (`sb2_` marker, Q019 filename).
Why:
- `BridgeHostTransport` (`bridge/counterpart/hosttransport.py`) preflights only realms the host accepts; until then SR2 targets report BLOCK_PROVIDER_UNAVAILABLE and nothing is fenced.
Blocks:
- SR1 -> SR2 automatic counterparts; `runtime_proven('SR2')` for the policy.
Does NOT block:
- SR2 -> SR1 (host path tested end-to-end over HTTP in `tests/services/test_counterpart_host.py`).
Expected local evidence:
- Oracle output + host log + readback receipt under `Lab/RuntimeArtifacts/`.

## Q021 — Abandoning a failed creation id in ProfileStore
Needed:
- An owner-approved ProfileStore operation (local-owned file) to retire a creation id whose creation is proven FAILED/FAILED_ABSENT, so a new intent can be reserved.
Why:
- `reserve_counterpart` returns the same creation id forever; the journal therefore blocks with `CREATION_NOT_ABANDONABLE` and the UI lists ABANDON_CREATION as operator-only.
Blocks:
- Automatic retry after a definitive native refusal or proven absence.
Does NOT block:
- Duplicate safety (the block is the safe default).
Expected local evidence:
- The API, its tests, and a migration note for existing CREATING rows.

## Q022 — WORLD_READY signals per realm
Needed:
- For each realm: which native signals prove "scene settled", "player usable" and "Bridge synchronized" for the exact loaded save, and how the plugin reports them in a `bridge-world-ready` v1 report (session, native save identity, build fingerprint).
Why:
- `bridge/counterpart/world_ready.py` and the transition machine accept only a trusted RUNTIME_OBSERVATION report bound to the destination session and save.
Blocks:
- Warm transitions and `require_world_ready` counterpart verification.
Does NOT block:
- The transition machine, overlay and simulators.
Expected local evidence:
- Plugin reports for both realms with timings from protected LAB runs.

## Q023 — Process, window and input adapters (Windows)
Needed:
- Launch/attach with PID **and** executable image verification, park/throttle a background realm, borderless overlay placement above the game window, focus/input transfer and back, for both games.
Why:
- `bridge/sdk/providers.py` kinds `process`, `window`, `input` (conformance probes in `bridge/sdk/conformance.py`) drive `bridge/transition/coordinator.py`.
Blocks:
- Real warm switching and the PLAY buttons.
Does NOT block:
- Everything state-related (machine, simulator, overlay UI).
Expected local evidence:
- Conformance report for the local process adapter + a protected LAB switch recording.

## Q024 — Native save slot model per realm
Needed:
- How each realm enumerates free/used save slots or groups (SR1 save groups, SR2 `SaveSlotIndex`), so an adapter can answer `describe_slots` with a complete probe, and how the plugin revalidates the slot right before writing.
Why:
- The policy needs FREE/OCCUPIED/UNKNOWN/CONFLICT; slot selection is never a reservation.
Blocks:
- BLOCK_NO_CAPACITY decisions before dispatch (the plugin refusal after dispatch already works).
Does NOT block:
- Slot-race safety (simulated).
Expected local evidence:
- Probe output for a full and a non-full profile per realm.

## Q025 — Independent native readback of a created save (both realms)
Needed:
- SR1: confirmed by `verify_sr1_creation` (disk readback). SR2: the equivalent independent readback (storage provider listing + content hash definition) usable as a `NATIVE_READBACK`/`DISK_READBACK` receipt bound to `details.native_save_id`.
Why:
- Linking requires independent evidence from a provider trusted for the target realm.
Blocks:
- Linking SR2 counterparts.
Does NOT block:
- SR1 readback (implemented wrapper `sr1_disk_readback_receipt`).
Expected local evidence:
- Readback receipts for one created SR2 save.

## Q026 — Gameplay lock / state sync before a transition
Needed:
- How to pause gameplay, flush the active save and freeze inventory in each realm (and how to undo it), as `gameplay_lock` provider methods.
Why:
- The transition machine requires `source_locked` + `source_synced` before starting the destination.
Blocks:
- Real transitions; transfer escrow integration.
Does NOT block:
- The machine and its compensations.
Expected local evidence:
- Plugin log of a lock/flush/unlock cycle without save changes.

## Q027 — Audio duck/restore per realm
Needed:
- A safe way to duck and restore each game's audio (process audio session or in-game mixer).
Why:
- `audio` provider kind and the RESTORE_SOURCE_AUDIO compensation.
Blocks:
- Polished transitions only.
Does NOT block:
- Correctness.
Expected local evidence:
- Before/after audio session levels.

## Q028 — Multiplayer realm-network adapters
Needed:
- Which native state each realm can show for remote players (presence markers, chat UI, transaction metadata) and how the plugin consumes `bridge-net` events.
Why:
- `realm_network` provider kind; the Bridge server core is native-agnostic.
Blocks:
- In-game multiplayer visuals.
Does NOT block:
- Server core, protocol, launcher multiplayer pages.
Expected local evidence:
- A LAB session with two clients in different realms.

## Q029 — Production authentication provider
Needed:
- The owner's choice of authentication (platform account, invite tokens, self-hosted accounts) for public servers.
Why:
- `bridge/multiplayer/auth.py` ships only DenyAll and a development-only token provider by design.
Blocks:
- Public servers.
Does NOT block:
- LAN/dev testing with DevTokenAuth.
Expected local evidence:
- An `AuthProvider` implementation and its threat notes.

## Q030 — Native currency and portal costs
Needed:
- How to read and escrow/charge/refund SR1 Gold Plorts and SR2 Newbucks safely (with the transfer ledger) for portal unlocks.
Why:
- `bridge/portal/contracts.py` plans charges (`settlement: LOCAL_ESCROW`) and reports BLOCKED_ON_LOCAL_QUERY without balances.
Blocks:
- Portal unlocking with real costs.
Does NOT block:
- Portal UI states and requirements.
Expected local evidence:
- One escrowed charge + refund on rollback in a protected LAB run.

## Q031 — Canonical build fingerprint per realm
Needed:
- The fingerprint definition used everywhere a build is compared (endpoint re-verification, package compatibility, receipts): which files are hashed and how.
Why:
- `endpoint_compatibility`, `target_compatibility` and receipts compare opaque fingerprint strings.
Blocks:
- Correct REVERIFY_REQUIRED decisions after a game update.
Does NOT block:
- The comparison logic.
Expected local evidence:
- Fingerprints for the current LAB builds.

## Q032 — Release signing and update source
Needed:
- Signing scheme, key custody and the update source for launcher/BridgeHost/plugins/schema.
Why:
- `bridge/app/updates.py` plans only manifests a `SignatureVerifier` accepts; Cloud ships `RejectAll`.
Blocks:
- Self-update.
Does NOT block:
- Manual installs.
Expected local evidence:
- Verifier implementation + a signed test manifest.

## Q033 — Native dev-spawn handler for imported species (LAB proof)
**STATUS: RESOLVED for SR1 (runtime-proven 2026-10-07).** `LabImportedOps` + the protected
`tools/lab/species_import_oracle.py` executed spawn / vac_add / vac_remove / release / query /
save_roundtrip against a natively registered imported species with per-op receipts (see
`docs/NATIVE_REGISTRATION.md`). SR2's native execution edge arrives with Q008.
Needed:
- A LAB-only, one-shot native handler that spawns one registered imported species into the foreign realm (after the realm registration operations of Q007/Q008 make its canonical identity a real content item) and emits a native receipt (spawned instance identity, canonical id, plugin/build hashes).
Why:
- `bridge/conversion/dev_spawn.py` authorizes requests and writes receipts, but the execution edge (`execution.state == BLOCKED_ON_LOCAL_QUERY`) has no native implementation to call.
Blocks:
- `DEV_SPAWN_PROVEN` / `RUNTIME_REGISTERED` evidence for imported species; the LAB import proof loop.
Does NOT block:
- Authorization/refusal logic, the parity matrix, adapter generation, the capture+travel gameplay design.
Expected local evidence:
- One authorized decision receipt plus a plugin log and save readback showing the imported species spawned, captured and reloaded in the foreign realm.

## Q034 — Foreign-realm natural spawn integration
Needed:
- How each realm's spawn systems (spawners, region/biome tables, time-of-day gates) can accept an imported species: exact native entry points, biome/region semantics, and how to keep every species off by default.
Why:
- The `species.foreign_spawn` policy (`NATIVE_ONLY` default, `ALL_IMPORTED`, per-species overrides in `bridge/conversion/foreign_spawn.py`) records the player's request, but the effective runtime switch stays false until this integration exists; foreign-world presence of an imported species must never imply natural spawning.
Blocks:
- `active_natural_spawn_foreign` for any species; `ALL_IMPORTED` becoming runtime-real.
Does NOT block:
- NATIVE_ONLY default gameplay, policy documents/diagnostics, the capture+travel import loop.
Expected local evidence:
- A LAB run proving an imported species with the mode enabled appears only in the intended spawn tables and disappears again with the mode off, bound to the exact build.

## Q050–Q057 — Mod compatibility (block reserved by `bridge/modcompat`)
The full entries (Needed / Why / Blocks / Does NOT block / Expected local evidence) live in `docs/MOD_COMPATIBILITY.md` § "LOCAL_QUERY list":
- Q050 local static evidence extractor for mod assemblies (metadata reader, never executes a mod)
- Q051 local MelonLoader attribute reader (`bridge-melon-metadata` v1)
- Q052 SRML `modinfo.json` dependency and load-order semantics
- Q053 SR1 SRML and game API catalogue (verified identifiers for `known_apis.json`)
- Q054 SR2 MelonLoader and community API catalogue (licence boundaries respected)
- Q055 Harmony patch target verification on the lab builds
- Q056 cross-realm adapter capability bindings
- Q057 native hook and detour policy

## Template
```
Qxxx — short title
Needed:
Why:
Blocks:
Does NOT block:
Expected local evidence:
Status / local answer:
```
