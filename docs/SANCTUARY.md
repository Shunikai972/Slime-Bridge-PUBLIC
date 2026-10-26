# The SR1 sanctuary

An island at the end of the Starlight Strand wooden walkway in Slime Rancher 1 (anchor `(-91.5, 3.0, 287.8)`, heading 67.8 degrees):
the walkway is extended with the game's own planks, posts and torches, leads to a rock island with a ruin colonnade, one statue per
normal slime, a gold-plort gate at the start of the new walkway, and a portal in the middle.

**Nothing from the game is shipped.** The player's own SR1 is read by a local tool; the result is a *description* (names,
transforms, parameters). At runtime the SR1 plugin resolves meshes, materials and textures by name from the running game.

## Pipeline

```
SR1 install --(bridge/content/sr1_sanctuary.py, UnityPy + dnfile, read-only)--> Lab/AssetCache/sanctuary-kit-<stamp>/sanctuary-kit.json
kit + settings --(bridge/sanctuary/layout.py, deterministic)------------------> Lab/GeneratedContent/sanctuary/layout-<stamp>/sanctuary-layout.json
layout --(launcher "Install" or the LAB oracle)-------------------------------> <SR1>/SlimeBridge/sanctuary/sanctuary-layout.json
plugin (mods/sr1-bridge/Sanctuary*.cs) builds the world under SlimeBridge_SR1_WorldRoot
```

* **Kit.** Finds every native `PlortSwitch_*` construct (the game's `PuzzleSlot` statues: statue + eyes meshes, plort display, light,
  trigger sphere, catch id), reads `Identifiable.Id` names from `Assembly-CSharp.dll`, the Bridgeway cell's walkway pieces with their world
  transforms, and the ruin/wood/rock piece recipes. 11 slimes have a native statue; 6 (Crystal, Fire, Gold, Hunter, Puddle, Saber) get the
  Pink statue re-tinted from their own plort colours; Quicksilver, Glitch, Tarr, Lucky are excluded (`bridge/sanctuary/slime_statue_exclusions.json`).
* **Layout.** Frame along the walkway heading; two `woodPlat01` planks + posts + torches; `mtnBase02` rock base; 12 `ruinPillar01`;
  a 300-degree statue ring around the portal with an entrance gap toward the bridge; three hidden statues in side alcoves; a gold depositor
  and a barrier across the new walkway. The puzzle manifest (required slimes) is frozen in the progress sidecar unless
  `dynamic_statue_requirements` is on.
* **Plugin.** `SanctuaryBuilder` builds pieces (box/mesh colliders), statues (rebuilt from the template nodes), gate, portal and a `Region`
  registered with the SEA zone (without it plorts hibernate and nothing is caught). `SanctuaryPlortCatcher` follows the native `PuzzleSlot`
  rules. Progress (`gold`, filled statues, portal unlock) is a Bridge sidecar JSON, never written into the native save.
* **Host.** Entering an unlocked portal posts `portal_cross` to BridgeHost (`/v1/portal/cross`); the launcher hook decides.

## Settings (`bridge` section of the launcher settings, v4)
`sr1_bridge_access_mode` (ENDGAME default | ALWAYS), `sr1_gold_plort_gate_cost` (10), `dynamic_statue_requirements` (false),
`sr2_portal_gate_enabled` (true), `sr2_portal_cost` (250000), `currency_mode` (SEPARATE), `cross_realm_chat`, `transition_mode` (AUTO).

## Dev / oracle
Console: `sanctuary status` always; `sanctuary unlock|reset|gold [n]|fill <SLIME>|tp [target]` only with `SLIMEBRIDGE_DEV=1` or the LAB executable.
LAB ops (`tools/lab/sanctuary_oracle.py`): `wait:N`, `status`, `tp_check:<spawn|portal|gate|statue.x>`, `plort:<SLIME|GOLD>`, `unlock`, `reset`,
`gold:N`, `fill:<SLIME>`, `shots` (player camera), `fshots` / `fshot:<name>` (free camera; also switches the world root on).
The oracle backs up saves + HKCU key, starts a throw-away native new game, restores everything and the LAB plugin folder afterwards.

## Known limits
Statue meshes/materials depend on the installed SR1 build (the kit records the build's file hashes). Mesh colliders need readable meshes; the
builder falls back to box colliders. The portal membrane uses the game's glass-barrier material and shows scan lines; the original
procedural gateway (`docs/PORTAL_3D.md`) is not used in game yet.

## Update 2026-10-17 (after the first real play session)
* **The island only exists once the gate is paid.** With the Gold gate enabled (ENDGAME), the long walkway, the island, the
  colonnade, the statues and the portal live under one `Extension` object that is inactive until the Gold plorts are deposited
  (access mode ALWAYS has no gate and shows everything from the start). Only the depositor and its pad stand at the dead end, so
  there is no barrier to jump around any more.
* **Short walkway, island clear of the cliff:** one plank, plaza centre 28.5 m from the anchor (was two planks, 44 m; the plaza
  ran into the mountain). Statues are on two rings inside the plaza (main ring 7.6 m, hidden ring 10.6 m in the gaps between them):
  the rock niches that put statues inside the cliff are gone. LAB shots: `Lab/RuntimeArtifacts/sanctuary-20261017-*`.
* **The island Region now has a `CellDirector`** (no spawners, zero targets, ambiance zone copied from the surrounding SEA cells).
  Without one, `FindConsumable` (any slime inside the region) and `AmbianceDirector.UpdateZoneSetting` (the player inside it)
  threw a NullReferenceException every frame.

## Update 2026-10-17 (round 3: art direction, reveal animation, portal, gold statue, prices)

**The garden.** The island is a moss garden around an ancient portal court (`bridge/sanctuary/island.py`, deterministic from a `seed`):
a flat paved court with a henge of ten pillars (gap toward the bridge), then three clearings out to ~23 m. The 17 statues are spread
over the whole island with best-candidate sampling (>= 6.4 m apart, nearest ring = required species, outermost = hidden ones framed
by two mossy rocks), each on a low plinth with a stone path to the court. Native flora from the game itself, resolved by name like
every other piece: reef / moss / pogo trees (a tree is a *compound*: trunk + branches + leaf clouds, described in the kit by
`bridge/content/sr1_flora.py`), flower beds, glowing flowers, giant mushrooms, mossy rocks, grass tufts. The ground is the game's own
moss ground material `envMossNav01` on the Moss zone's rock (`island_ground`: `moss` | `native`, `island_mesh`: `mtnBase01` | `mtnBase02`); a
second, smaller piece of the same rock lands the island at the dead end. (`env_voxel *` materials are the Glitch zone's voxel shader and
show a neon pixel grid up close: not used.) Nothing grows through a statue, a pillar, a path or the entrance corridor: the
generator records a footprint for every solid thing and refuses to emit a layout where a statue overlaps one (`tests/sanctuary/test_layout.py`).
The kit must be re-read from SR1 once to carry the flora (`Lab/AssetCache/sanctuary-kit-002`); the pack flow regenerates the layout
whenever the kit or the generator code changed (`apps/launcher/sanctuary.py: refresh_layout`, layout field `generator`).

**No poles in front of the depositor.** The walkway posts used to stand 6 m above the deck; they are now supports under the deck with a short
bollard on top. The depositor stands on a plinth and is a *real gold slime*: the game's own Gold slime body and face meshes with their
native materials and palette (`SanctuaryBuilder.BuildFigure`), frozen, replacing the stone statue. Beside it stands the "H": a clone of the game's own
Hobson journal device (yellow H beacon on its orange pod, "Press E to activate", `SanctuaryJournal`) whose entry reads *Pay the price, and a
new universe opens its door.* (a stone tablet is the fallback when no native journal exists in the scene).

**The island appears when the gate is paid** (`SanctuaryReveal`): the rock base rises out of the sea, the walkway and the court follow, then the
garden grows, the statues rise and the portal ring assembles (about 7 s). Colliders only come back when each piece has arrived. The island is
built lazily the first time the gate opens, so a locked sanctuary costs nothing; a save that is already open shows it at once.

**The portal is dark until every required statue is filled.** Dormant: the stone ring only (no membrane, no light). When the last plort lands a
wave of light runs around the ring, the membrane unfolds from its centre and starts turning, ending in a flash and the native fill FX.

**Prices.** Imported plorts are sold at their own SR2 base price and saturation (`bridge/content/sr2_market.py` reads SR2's
`Plort Market Default Values`; the value rides in the species' `plort.json`), and the Plort Market's price board lists a row for each of them
(`ImportedEconomyPatch`).

**LAB probes.** `perf:<s>:<label>` records frame times (avg / p95 / max) with the renderer / light / collider / actor counts; `toggle:<flora|lights|shadows|extension>:<0|1>`
isolates a cost driver. `fshots:<tag>` shots wait for nothing: the island is built and shown at once for free-camera shots.

**Measured (LAB, 2026-10-17, `perf:` probe).** Locked sanctuary 7.5 ms / frame (132 fps), island open 8.1 ms (124 fps), island open with 36 imported
slimes 8.0 ms, on the player's own save 6.3 ms (158 fps); switching flora, point lights, shadows, imported renderers or imported scripts off changes
the average by less than 1 ms each. The island is not a frame-rate cost. A permanent, almost free watchdog (`FrameWatch`) now writes one line to
`Player.log` (`[SlimeBridge] frame watch: ... | SLOW`) when a 30 s window averages above 20 ms, with the actor / renderer / light / rigidbody counts,
so a report of lag comes with numbers. Per-actor material copies of imported slimes are now destroyed with the actor (`ImportedMaterialOwner`).
