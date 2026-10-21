# Cross-game base-species parity (non-visual)

Goal: **every legitimate BASE slime species that exists only in SR1 can exist in SR2 through
SlimeBridge, and every species that exists only in SR2 can exist in SR1.** The intended
gameplay loop is hunting, not a menu: the player travels to the species' native game, captures
the slime there, carries it through SlimeBridge and releases it in the other world. Imported
species **never spawn naturally in the foreign world by default**.

## The derived roster

The roster is derived from the reviewed classification capture (`classification.json`),
never hardcoded. The current captures produce **31 base species rows: 16 SHARED_NATIVE,
6 SR1_ONLY, 9 SR2_ONLY**, zero excluded rows.

| class | count | species |
|---|---|---|
| SHARED_NATIVE | 16 | boom, crystal, dervish, fire, gold, honey, hunter, lucky, phosphor, pink, puddle, rock, saber, tabby, tangle, tarr |
| SR1_ONLY (need SR1 -> SR2) | 6 | glitch, glitch_tarr, mosaic, quantum, quicksilver, rad |
| SR2_ONLY (need SR2 -> SR1) | 9 | angler, batty, cotton, flutter, ringtail, shadow, sloomber, twin, yolky |

Rules enforced while deriving (each violation lands in `parity.json` -> `roster.excluded`
with an exact reason instead of being silently dropped or included):

- classification must be `SHARED_NATIVE` / `SR1_ONLY` / `SR2_ONLY`;
- both realms' presence must be captured (0/1 or true/false) and must match the class;
- every present realm needs a `role: base`, non-quarantined source row with a native ref and
  a sha256 definition hash; an absent realm must carry no native block;
- duplicate canonical ids exclude all copies; largos, debug/internal/temporary and
  quarantined definitions can never enter (reasons `NATIVE_ROLE_NOT_BASE`, `NATIVE_QUARANTINED`, ...).

Shared species get **no adapter**: adapting a natively shared species would create an
imported duplicate, and both the roster and the matrix validator refuse it.

## Artifacts and how to build them

```
py -3.12 -m tools.content.build_species_adapter
# inputs:  Lab/RuntimeArtifacts/classification-20261006-runtime/{classification,SR1-snapshot,SR2-snapshot}.json
# outputs: Lab/GeneratedContent/species-adapters/<stamp>/
#            adapter.<source>-to-<target>.<canonical_id>.json    (15 descriptors, v2)
#            parity.json                                          (bridge-species-parity v1)
#            summary.json                                         (bridge-species-adapter-run v2)
```

Everything is written atomically under the ignored `Lab/GeneratedContent` tree and never
touches game installs, saves or the network. The run `passed` flag is true only when every
descriptor is `EXPERIMENTAL` with zero validation problems, no adapter is missing, and the
parity matrix validates clean (including zero target-identity collisions).

## The parity matrix (`bridge-species-parity` v1)

One row per included species, sorted by canonical id, and a strictly validated shape:

- `canonical_id`, `display_name`, `classification`, `required_adapter_direction`
  (`SR1->SR2`, `SR2->SR1`, or null for shared);
- `presence` and `native` per realm (native ref, numeric id, definition hash) plus
  `record_hashes` binding each present realm to its captured snapshot row;
- `plort`: mode `PLORT` / `NONE` / `AMBIGUOUS`, canonical plort id, source ref, target status
  (`PENDING_CAPTURE`, `CLASSIFIED_ABSENT`, `NONE`, `NATIVE_BOTH_REALMS`), presence evidence
  and the pending local query - an uncaptured target presence is never claimed as absence;
- `adapter`: descriptor id, direction, status and validation-problem count (null for shared);
- `states`: `runtime_registration`, `inventory`, `release`, `save_load`, `diet`, `behavior`,
  `economy`, `plort`, `visual` - every native step that has no verified API stays
  `BLOCKED_ON_LOCAL_QUERY` with the exact query id (`Q007`/`Q008` for target registration,
  `Q009`/`Q010` for behaviour capture); `save_load` is schema-ready with its native binding
  still blocked; `visual` stays `VISUAL_ASSET_BLOCKED` / `NEEDS_LOCAL_VISUAL_PROOF`;
  shared rows carry the single marker `{'import': {'state': 'NOT_REQUIRED_NATIVE_BOTH_REALMS'}}`;
- `blockers`: the exact blocker vocabulary with local queries;
- `foreign_world_spawn` per species (see below);
- `evidence`: data evidence, identity basis/evidence, and the sha256 of the source
  classification row.

Top level: input hashes, captured builds, traversal, roster (ids/names/counts/excluded),
`collisions` (must be empty), adapter index, policy hashes, `blocked_queries`, and the rows.

## Foreign-world natural spawning policy (`bridge-foreign-spawn-policy` v1)

```json
{"schema": "bridge-foreign-spawn-policy", "schema_version": 1,
 "mode": "NATIVE_ONLY", "overrides": {"slime.ringtail": true}}
```

- `mode`: `NATIVE_ONLY` (**default**) or `ALL_IMPORTED`.
- `overrides`: per-species booleans; only verified imported species may be named - anything
  else is refused/removed by validation and normalization (fail-closed toward NATIVE_ONLY).
- The per-species effective view (from `bridge.conversion.foreign_spawn.effective_policy`)
  separates `requested_natural_spawn_foreign` (the player's setting) from
  `active_natural_spawn_foreign` (runtime truth).
- **The active switch is false for every species until Q034 is answered**: the configuration,
  schema and diagnostics exist now, but wiring an imported species into a foreign realm's
  spawn systems is native work that is not implemented. Foreign-world presence of an imported
  species never implies natural spawning.

## LAB dev spawn is a test tool, not progression (`bridge-dev-spawn` v1)

`py -3.12 -m tools.lab.dev_spawn --species slime.ringtail --realm SR1` authorizes one
one-shot test spawn against the derived roster and the exact LAB installation, then writes a
deterministic receipt under `Lab/RuntimeArtifacts/dev-spawn/`. It refuses:

- unknown species (`DEV_SPAWN_UNKNOWN_SPECIES`), species outside the roster or without a
  clean prepared adapter (`DEV_SPAWN_UNREGISTERED_SPECIES`),
- native collisions (`DEV_SPAWN_NATIVE_COLLISION`, `DEV_SPAWN_NATIVE_REALM`),
- every non-LAB or fingerprint-mismatched installation (`DEV_SPAWN_NOT_LAB_INSTALL`,
  `DEV_SPAWN_BUILD_MISMATCH`) - original/reference installs are always refused.

A receipt is marked `test_only: true`, `one_shot: true`, `spawn_tables_modified: false`; it
cannot become normal natural spawning. Native execution stays `BLOCKED_ON_LOCAL_QUERY`
(Q033) until the LAB handler exists, so an authorized receipt never claims a spawned slime.

## Native next steps (blocked, not guessed)

1. **Q007 / Q008** - register the imported species in the target realm (identity, prefab,
   plort, diet, behaviour, market, persistence) through SRML / MelonLoader.
2. **Q033** - LAB-only one-shot dev-spawn handler to prove registration end to end.
3. **Q034** - foreign-realm natural spawn integration for `ALL_IMPORTED` to become real.
4. Then, and only then, per-species runtime evidence can move past
   `PLAN_READY` / `BLOCKED_ON_LOCAL_QUERY` toward `RUNTIME_REGISTERED`, `DEV_SPAWN_PROVEN`,
   `VACPACK_PROVEN`, `RELEASE_PROVEN`, `PLORT_PROVEN`, `SAVE_RELOAD_PROVEN`.

Final meshes/materials/icons/statue visuals are reserved for the later Opus + Sol visual
phase; nothing in this pipeline attaches visual assets (`VISUAL_ASSET_BLOCKED`).
