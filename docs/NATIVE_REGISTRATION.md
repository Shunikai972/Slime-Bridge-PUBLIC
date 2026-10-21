# Native imported-species registration — verified API maps (Q007 SR1, Q008 SR2)

Status: **runtime-proven in the LAB for BOTH realms** (see "Evidence" below). This document
records the exact native APIs each registration backend uses, how each was verified, and the
deliberate placeholder limits.

## Product path (distributable)

`bridge-imported-species-spec` v1 (built by `tools/content/build_import_spec.py`) is Bridge-owned
data consumed by the SRML plugin at startup. The plugin only arms itself when an oracle targets
the exact LAB executable and passes `SLIMEBRIDGE_IMPORT_SPEC` / `SLIMEBRIDGE_IMPORT_OUT` /
`SLIMEBRIDGE_IMPORT_RUN` (and optionally `SLIMEBRIDGE_IMPORT_OPS`). In a normal install the same
spec path comes from the Bridge profile directory; no game binary is ever modified.

## Identity model

`Identifiable.Id` is a C# enum and cannot be extended at runtime. Imported species therefore use
reserved numeric values (900000–999999, allocated deterministically by the spec builder and
stable across regenerations via `--previous-spec`) cast to the enum type. Every id the game
dereferences was verified to treat the value as a plain int:

- `Identifiable.SLIME_CLASS` / `PLORT_CLASS` / `NON_SLIMES_CLASS` / `EATERS_CLASS` are public
  mutable `HashSet<Id>`; membership is what `Identifiable.IsSlime/IsPlort` consult.
- `Identifiable.GetName(Id)` builds its localization key via `Enum.GetName`, which returns null
  for non-enum values and NREs. A Harmony prefix answers registry-reserved ids from the display
  map (and any ≥900000 id with a "Bridge <n>" fallback), never reaching the original body.
- `PlayerState.GetPotentialAmmo()` returns the potential-ammo set built from the serialized
  `PlayerState.potentialAmmo` prefab array (asset data; runtime-registered prefabs can never be
  in it). A Harmony postfix unions the imported ids so `Ammo.CouldAddToSlot` /
  `MaybeAddToSpecificSlot` accept them (`PLAYER_AMMO_PREDS` slots 0-3 are `NO_LIQUID`, so
  non-liquid imported ids pass).

## Definitions and registries

| Operation | Verified API |
| --- | --- |
| Define slime | `ScriptableObject.CreateInstance<SlimeDefinition>()` (plain fields: `Name`, `IdentifiableId`, `BaseSlimes`, `IsLargo`, `CanLargofy`, `PrefabScale`, `AppearancesDefault`, `Sounds`, `Diet`, `FavoriteToys`) |
| Define diet | `new SlimeDiet { MajorFoodGroups, Favorites, AdditionalFoods, Produces, FavoriteProductionCount }`; `SlimeDefinitions.RefreshDefinitions()` rebuilds every `Diet.EatMap` (imported ids included) |
| Register definition | `SlimeDefinitions.Slimes` is a public field — replaced with a longer copy; `slimeDefinitionsByIdentifiable` (private dict) gains the id via reflection; read-back via `GetSlimeByIdentifiableId` |
| Register prefabs | `LookupDirector.GetPrefab(id)` reads the private `identifiablePrefabDict`; the registrar adds cloned target-native placeholder prefabs (prefab-asset semantics: activeSelf=true under the deactivated `SlimeBridge_RegisteredPrefabs` container, so native `Object.Instantiate` callers get ACTIVE copies) and re-reads through the native getter |
| Plort item identity | `VacItemDefinition.CreateVacItemDefinition(id, color, icon)` (public factory) + private `vacItemDict` / `vacItemDefinitionsDynamic`; clone color/icon come from the template plort |
| Placeholder visuals | The slime prefab is a runtime clone of a target-native species prefab; every component field of type `SlimeDefinition` that referenced the template definition is retargeted to the imported definition (reflection sweep), and `Identifiable.id` on the clone is set to the imported id. Spawned instances additionally receive the source realm's canonical palette through per-instance `MaterialPropertyBlock` tinting (`_TopColor`/`_MiddleColor`/`_BottomColor`, mirroring `RecolorSlimeMaterial.SetColors`); geometry and face remain template placeholders |
| Appearance selection | `SlimeAppearanceDirector.UnlockAppearance` + `UpdateChosenSlimeAppearance` on every director referenced by the prefab and by `LookupDirector.slimeAppearanceDirector`; read-back via `GetChosenSlimeAppearance` |

## Persistence

`Ammo` slots (and therefore `GameV12` saves) store plain `Identifiable.Id` ints. The registrar
re-runs at every startup from the same spec before any save loads, so saved imported ids always
re-resolve to the same definition. The `save_roundtrip` LAB op uses the reviewed
`InventoryRules.VerifyPersistence` + `InventoryAgent.ReadbackNew` path: native `SaveAllNow()`,
the exact new revision, `GameV12` parse, count equality, and a disk sha256 — proven with the
imported id in the vacpack.

## Lab ops edge (Q033 SR1; SR2 ops edge)

`LabImportedOps` (SR1) and `Sr2LabImportedOps` (SR2) execute one-shot oracle-provided ops after
the world is ready, only for the exact LAB executable: `spawn:<canonical>`,
`vac_add:<canonical>[:n]`, `vac_remove:<canonical>`, `release:<canonical>`,
`query:<canonical>`, `save_roundtrip:<canonical>`. Each op records native read-back; REFUSED is
distinct from EXECUTED. This is a LAB boundary only; normal gameplay keeps native-world hunting
and `NATIVE_ONLY` spawning.

SR2 specifics (runtime-proven end-to-end in
`Lab/RuntimeArtifacts/species-import-20261008-011843-f63c60515e67`: spawn + vac_add 0→1 +
save_roundtrip with disk revision/sha256 and saved_count 1 + release 1→0 + query, run passed):
- World-time preparation before the one-shot ops: membership is re-asserted into the live
  `IdentifiableTypeGroup` runtime caches (some caches only exist after world load and are built
  from serialized member lists), imported types are (re)inserted into
  `LookupDirector._identifiableTypeByRefId`, and `AutoSaveDirector.ResetLookupTables()` rebuilds
  the persistence-id tables so imported types serialize (before this, saving with an imported
  vacpack item returned false / threw, and disk readback failed with "unresolved native saved
  ammo type").
- The slot HUD (`AmmoSlotViewHolder.UpdateAmmoDisplay`) resolves per-slime state and must see
  consistent group membership; the same world-time preparation is what makes both `vac_add` and
  `save_roundtrip` pass.
- Receipt rows carry the requested count, actual before/after native counts, revert/restore
  state on failure, plus diagnostics (slot table with predicate verdicts, membership, save
  preflight) so a reviewer can re-derive every claim.

## SR1 native spawn path for imported species (Q033)

Runtime spawn does NOT use a bare `Object.Instantiate` on the registered prefab clones: the
clones keep prefab-asset semantics (activeSelf=true under the deactivated
`SlimeBridge_RegisteredPrefabs` container), so `Object.Instantiate(template, pos, rot)` yields an
ACTIVE copy and Unity runs Awake at instantiation (cached references such as
`Identifiable.member`) exactly like the game's own `SRBehaviour.InstantiateActor` callers. The
actor model is then registered through the public save-loader entry point
(`GameModel.RegisterStartingActor`), creating the SlimeModel and initializing participants
(SlimeEmotions and friends). Without this ordering, spawned placeholders produce the
`SlimeEmotions`/`SlimeFaceAnimator` NullReferenceExceptions (GetEmotion/UpdateToTime on a null
model) that the game's `ActorRegistry` catches every frame, and the game's OWN production path
(`SlimeEat` -> InstantiateActor on the imported plort prefab) fails the same way: an inactive
clone makes native instantiation skip Awake, so `Identifiable.SetModel` NREs before the produced
plort can register. Runtime-proven with `spawn_path=native_instantiate_actor` and zero
`SlimeEmotions`/`SlimeFaceAnimator`/`SlimeBridgeImport` log lines after the ops marker (canary
runs on the container build; final-bytes protected run recorded under Evidence).

In-game manual invocation (LAB/dev): the SR1 bridge registers the SRML console command
`slimebridge list|spawn <canonical>`; execution is registrar-gated (non-LAB sessions fail closed
with a message). `tools/lab/species_dev_session` arms the spec on the exact LAB executable,
launches it after a verified save backup, reports receipts and a log scan, and leaves the game
RUNNING for manual play; `--restore-backup <snapshot>` restores the current-user saves after the
game is closed.

Canonical placeholder palette (no geometry work): the source realm's default appearance palette
is captured as data by the SR2 collector (`appearance-colors.json`, bridge-appearance-colors v1:
per-species top/middle/bottom/ammo RGBA from the CLASSIC save-set appearance, legacy and new
palette properties) and embedded into the target spec as the optional `appearance` block. On
spawn the palette is applied per-instance through the same MaterialPropertyBlock mechanism the
game's own `RecolorSlimeMaterial` uses (`_TopColor`/`_MiddleColor`/`_BottomColor`), so an
imported placeholder shows the species' canonical colors instead of the template species' colors
(receipt details: `palette_renderers` visited, `palette_supported` material-capable,
`palette_verified` exact read-back; `palette_applied` is true only when every supported renderer
verified). No shared material, appearance asset or prefab asset is modified; faces and geometry
stay placeholder until the visuals phase.

Spec-path hardening: `tools/lab/paths.validate_spec_destination` validates Bridge-owned spec
documents with LEXICAL containment and reparse checks BEFORE resolution (a junction on the root
or an ancestor would otherwise disappear behind `resolve()` and let the check pass while the real
target lives outside the root); both the protected oracle and the dev session use it, covered by
junction regression tests.

Runtime-helper hygiene: registered prefab clones can accumulate runtime-added components (the
appearance bounds recalculation helper) whose cross-object references copy as null into spawned
instances and NRE every frame. The spawner strips such copied helpers (matched by type name
`RecalculateBoundsHelper`) before activation; the persistent dev session then shows zero
NullReferenceExceptions of the emotion, face-animator and appearance-bounds classes after the
ops marker (receipt `nre_scan`).

## SR2 (Q008) — object-identity registration model

SR2's identity model is object-based, not enum-based: `IdentifiableType` is a `ScriptableObject`
class, `SlimeDefinition` **derives from it** (`class SlimeDefinition : IdentifiableType`), and
`referenceId` strings carry their type prefix (e.g. `SlimeDefinition.Tabby`,
`IdentifiableType.PinkPlort`, `IdentifiableTypeGroup` GUID hashes). Identity is therefore the
imported definition object itself; nothing numeric is cast.

| Operation | Verified API |
| --- | --- |
| Define slime | `ScriptableObject.CreateInstance(Il2CppType.Of<SlimeDefinition>())`; plain fields: `Name`, `IsLargo`, `CanLargofy`, `PrefabScale`, `BaseSlimes`, `AppearancesDefault`, `AppearancesDynamic`, `Diet`, `FavoriteToyIdents`, `SlimeModules`, `NativeZones` + inherited `referenceId`, `stableHashedId`, `initializedHashId`, `groupType`, `Categories`, `prefab` |
| Define plort type | `ScriptableObject.CreateInstance(Il2CppType.Of<IdentifiableType>())`; `IsPlort=true`, inherited fields as above, `prefab` cloned from the template plort prefab |
| Register definition | `SlimeDefinitions.Slimes` (`Il2CppReferenceArray<SlimeDefinition>`) replaced with a longer copy; `_slimeDefinitionsByIdentifiable[def] = def`; read-back via `GetSlimeByIdentifiableId(def)` semantics and direct index checks; `RefreshDefinitions()` runs once after all entries |
| Diet | `new SlimeDiet()` with `MajorFoodIdentifiableTypeGroups` (resolved by group `ReferenceId` from `LookupDirector._allIdentifiableTypeGroups.items`), `FavoriteIdents`, `AdditionalFoodIdents`, `ProduceIdents`, `FavoriteProductionCount` |
| Diet eat map | `SlimeDiet.RefreshEatMap(definitions, def)` works for native definitions but throws a native `NullReferenceException` on runtime-created definitions; the registrar composes entries through the public `SlimeDiet.EatMapEntry` field surface instead (`EatsIdent`, `IsFavorite`, `ProductionCount`, `FavoriteProductionCount`, `ProducesIdent`, `BecomesIdent`, `Driver`, `ExtraDrive`, `MinDrive`), copying entry shapes from the template's native map where the food type matches. Receipts record `eat_map_source` (`native_refresh`/`manual_fallback`) honestly |
| Group membership | Slime: template's `groupType` gains the imported def in `MemberTypes`. Plort: the registrar mirrors **every group whose member list directly contains the template plort** (template `groupType` as fallback) — this is what the vacpack slot predicates (`AmmoSlotDefinition._slotTypeGroup` / `_slotBlockList`) consult |
| Placeholder visuals | Slime and plort prefabs are clones of the target-native template prefabs, kept inactive + `DontDestroyOnLoad`, with every `Identifiable.identType` on the clone retargeted to the imported type/definition |
| Persistence direction | Saves store the object identity; the registrar re-runs at every startup from the same spec before any save loads, so saved imported ids re-resolve to the same definitions (runtime canary; save/load read-back ops are the next SR2 milestone) |

## Placeholder policy (honest states)

- Visuals: `PLACEHOLDER_TEMPLATE` — imported species render with the target-native template's
  meshes/materials while carrying their own logical identity. Final visuals belong to the later
  visual phase.
- Largos: `can_largofy=false` for all imported entries for now (largos reserved); recorded in the
  spec blockers.
- Icons: imported slimes reuse the template appearance icon; imported plorts clone the template
  plort's `VacItemDefinition` color/icon.

## Evidence

- SR2 runtime (protected LAB run, saves restored, fingerprints unchanged):
  `Lab/RuntimeArtifacts/species-import-20261008-001304-d9e239d4af7d` — 4 READY species
  registered (`glitch_tarr` eat_map 21, `mosaic` 5, `quantum` 7, `rad` 5) with read-backs all
  true (`definition_in_array`, `definition_indexed`, `group_member`, `plort_group_member`,
  `prefab_retargeted`, `plort_prefab_retargeted`, post-refresh array/index) and `eat_map_source`
  `manual_fallback`; `glitch`/`quicksilver` refused `PENDING_DATA` fail-closed. The SR1-path
  regression canary (`slime.ringtail`, same shared oracle) is recorded beside it.
- SR1 runtime (protected LAB run, saves restored, fingerprints unchanged):
  `Lab/RuntimeArtifacts/species-import-20261007-224718-2e3bf1f6179c` — 6 READY species
  registered with native read-backs all true (`definition_indexed`, `prefab_registered`,
  `plort_prefab_registered`, `slime_class`, `plort_class`, `appearance_chosen`,
  `eat_map_entries` 6–19 per species) and zero problems; ops `spawn`/`vac_add`(0→1)/
  `save_roundtrip`(disk count=1, sha256)/`release`(1→0 + world instance)/`query` all executed;
  plugin sha256 `40d5ac95…`, spec sha256 `2c162f55…`. Earlier passes on earlier spec revisions:
  `species-import-20261007-222801-9dfd6cd9ca47` and `…-223122-17be047aaf9e`.
- Static: `tests/SpecParserHarness` (32 checks) against the exact generated spec files;
  `tests/content/test_diet_capture.py`, `tests/content/test_build_import_spec.py`.
- Decompilation evidence for every API above: local LAB `Assembly-CSharp.dll` (2019.4.29f1
  build), see `docs/RECON.md` for the environment.

## Deliberate limits (not blockers)

- Three species in this capture stay `PENDING_DATA` and are refused by the spec (fail-closed,
  never silently registered): flutter (NECTAR group has no SR1 analogue), shadow and yolky
  (their source diets captured no food groups — recorded as "unsupported or uncaptured" until
  the feeding behavior is properly captured or modeled as non-feeding).
- Economy/market values for imported plorts are not registered yet; selling behavior stays a
  recorded local query.
- SR2 LAB ops edge (spawn/vacpack/save/release/query) is NOT implemented yet: the SR2 oracle
  path is registration-only (`ops=[]`) and refuses ops requests explicitly. That is the next
  milestone after this registration backend.
- `SlimeDiet.RefreshEatMap` NREs on runtime-created definitions (native-only preconditions);
  imported SR2 diets therefore use the documented public-field composition path and record
  `eat_map_source=manual_fallback`.
