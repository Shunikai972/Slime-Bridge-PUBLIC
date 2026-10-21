# Cross-game species adapters (non-visual preparation, v2)

One realm-neutral, metadata-only descriptor per prepared species, so the later visual phase only
attaches meshes, materials and icons without rewriting behaviour, registration, economy or
persistence logic. The **full exclusive base-species roster** is prepared: every captured
`SR1_ONLY` species becomes one SR1->SR2 descriptor, every `SR2_ONLY` species one SR2->SR1
descriptor. The roster is derived from the reviewed classification by
`bridge.conversion.species_parity` - never hardcoded. Species whose captured definition produces
no plort use `plort.mode == 'NONE'` instead of inventing one; natively shared species are never
adapted (see `docs/SPECIES_PARITY.md` for the full parity system).

## The prepared pipelines (current captures)

| Direction | Species |
|---|---|
| SR1 -> SR2 (6) | `slime.glitch`, `slime.glitch_tarr`, `slime.mosaic`, `slime.quantum`, `slime.quicksilver`, `slime.rad` |
| SR2 -> SR1 (9) | `slime.angler`, `slime.batty`, `slime.cotton`, `slime.flutter`, `slime.ringtail`, `slime.shadow`, `slime.sloomber`, `slime.twin`, `slime.yolky` |

Descriptor ids follow `adapter.<source>-to-<target>.<canonical_id>`, e.g.
`adapter.sr2-to-sr1.slime.ringtail`.

## How to build them

```
py -3.12 -m tools.content.build_species_adapter
# inputs:  Lab/RuntimeArtifacts/classification-20261006-runtime/{classification,SR1-snapshot,SR2-snapshot}.json
# outputs: Lab/GeneratedContent/species-adapters/<stamp>/
#            adapter.*.json      (one descriptor per exclusive species)
#            parity.json         (bridge-species-parity v1)
#            summary.json        (bridge-species-adapter-run v2)
```

The builder reads only reviewed local captures, writes metadata-only JSON atomically under the
ignored `Lab/GeneratedContent` tree, and never touches game installs, saves or the network.

## What each descriptor contains (schema v2)

- `identity` - canonical id, source realm/native ref/numeric id/definition hash/display name,
  target realm with `native_present` and the reserved canonical identity. `RUNTIME_ANCHORED`.
- `plort` - `mode: 'PLORT' | 'NONE'`. For a plort species: canonical plort id, source native
  ref, `produces` relation and target presence. When the plorts bucket was not captured the
  descriptor keeps `presence_evidence: PENDING_CAPTURE` and a `local_query` instead of claiming
  absence. For a no-plort species: `canonical_id: null`, `production_relation: 'none'`,
  `presence_evidence: 'NOT_APPLICABLE'`, no local query - valid only when the captured species
  row and the definition both produce nothing (and, when the plorts bucket exists, no plort row
  matches the species; any conflict quarantines).
- `diet`, `economy`, `spawn`, `save` - behavioural/economy/persistence slots. Without a verified
  native API these stay `BLOCKED_ON_LOCAL_QUERY` with the exact query id from the target
  profile (`Q007` for SR1, `Q008` for SR2) plus the deeper capture queries for diet/favourites.
- `inventory` - canonical item ids only (`slime.*` plus `plort.*` for plort species; a no-plort
  species lists the slime only). The validator enforces that the item list mirrors the plort
  mode exactly.
- `target_registration` - every operation from the reviewed target profile
  (`bridge.conversion.targets.REGISTRATION_OPS`), each mapped to its adapter section. Native
  APIs stay `api: None` with a `local_query` until verified locally.
- `visual` - always `status: VISUAL_ASSET_BLOCKED` with `evidence:
  NEEDS_LOCAL_VISUAL_PROOF` and the asset checklist the later phase must attach. Prefer
  **reusing local SR1 decompiled assets** (AssetRipper export) where suitable instead of
  authoring new visuals.
- `hashes`/`versions` - deterministic input hashes and schema versions for compatibility
  manifests and migrations. Floats and asset payloads are forbidden (validator-enforced).

## Fail-closed rules (validator-enforced)

- Only `*_ONLY` species with `role: base` and a clean (non-quarantined) source definition.
- Target absence must be **explicitly captured** (present map `0`/`false`); a missing or null
  target entry quarantines (`ADAPTER_TARGET_PRESENCE_UNCAPTURED`) instead of implying absence.
- Plort cardinality: exactly one produced plort, or a verified no-plort species. More than one
  produced plort quarantines (`ADAPTER_PLORT_CARDINALITY`); a "no plort" capture that conflicts
  with the definition refs or a slug-matching plorts-bucket row quarantines
  (`ADAPTER_PLORT_PRESENCE_MISMATCH`). A natively-present target plort can never be adapted.
- The canonical plort id and the native plort ref must identify the same item: the slug
  crosswalk (`plort.ringtail` ~ `IdentifiableType.RingtailPlort`, `plort.rad` ~ `RAD_PLORT`)
  is required even when the plorts bucket was not captured, and a mismatch quarantines
  (`ADAPTER_PLORT_CROSSWALK_MISMATCH`).
- Plort presence evidence is coherent: `false` requires `CLASSIFIED`; an unproven presence is
  `null` and must carry `PENDING_CAPTURE` plus a local query; a no-plort descriptor carries
  `NOT_APPLICABLE` and no query.
- Source numeric ids are allowed (captured truth); targets must never carry native identifiers
  (`numeric_id`, `native_id`, `slot_index`, `save_id`, `instance_id`).
- No payload keys (`vertices`, `pixels`, ...), no floats, no unknown fields at the top level or
  in any section; lookalike sections do not inherit the source-id exemption; the inventory item
  list must mirror the plort mode.
- Status is capped at `EXPERIMENTAL` (`QUARANTINED` on hard errors), and quarantined
  descriptors never validate clean.

## Regenerating after a game update or a new capture

1. Re-run the realm registry capture and the classification (see `docs/LOCAL_QUERIES.md`)
   and store the artifacts as a new reviewed directory.
2. `build_species_adapter --classification <dir>`; the roster, adapters and parity matrix all
   regenerate deterministically.
3. If a previously exclusive species becomes shared (or disappears) the summary reports it
   through the roster diff; descriptors never silently carry stale identity - a changed
   definition hash flows through the input hashes and the parity `record_hashes`.

## Visual phase handoff

The later visual phase consumes a descriptor and attaches, per `visual.required_assets`:
slime mesh/materials/face/icon and plort mesh/materials/icon, plus the registration operations
mapped to `section: visual` (`BUILD_MATERIALS`, `BUILD_PREFAB`, `REGISTER_ICON`). Until those
operations carry verified APIs and real assets, every descriptor remains EXPERIMENTAL and no
native registration may run. The LAB dev-spawn receipt (`tools/lab/dev_spawn.py`) is the test
hook that will prove registration once Q007/Q008/Q033 are answered.
