# Performance notes

What the base games do, what SlimeBridge costs, and what was changed. Measurements marked LAB come from the isolated copies
(`tools.lab.sanctuary_oracle ... perf:` ops); the rest is read from the games' own data or the plugins' source.

## Slime Rancher 1 (Unity, Mono)

Read from `globalgamemanagers` (UnityPy):

* Fixed timestep 0.025 s (40 Hz physics), solver iterations 6, `autoSyncTransforms` on, vSync **on** in every quality tier.
  With vSync a game that misses 16.7 ms by a hair drops to 33 ms frames: a 60 fps machine under load shows ~50 fps and a "12 % of frames
  >= 33 ms" window, which is what `FrameWatch` writes to `Player.log` (`frame watch: ... | SLOW`).
* The default tiers carry 2x MSAA, shadow distance 50 m, 2 cascades, soft particles on the `*_softParticles` tiers.
* A ranch scene holds ~2 700 enabled renderers and 9-12 lights; the sanctuary island is not the cost (LAB: 124-158 fps).

Observed in the player's real session: windows at 100 ms+ coincided with **our own background jobs** (tests, builds, extraction). Everything
heavy that SlimeBridge runs next to a game is therefore low priority (the Extract worker starts at below-normal priority and under the
memory limit) and the repository's tests are not run while a game is open.

## What the plugins do per frame

| Where | Before | Now |
|---|---|---|
| SR1 `BridgeEntry.Update` | `FrameWatch.Tick` (one histogram bucket), registrar/ops/sanctuary/travel ticks that return at once when idle | unchanged (already cheap) |
| SR1 `ImportedPetrification.Update` (per Ringtail) | full step every frame (time director + cave volumes) | five steps a second; duration is measured by the world clock anyway |
| SR1 `SanctuaryPortalGlow.Update` | re-applied a dormant portal's ring every frame | applies the dormant look once, then idles until the portal wakes up |
| SR2 bounds-helper / appearance hooks | hierarchy walk (`GetComponentInParent`, `Find`, `GetComponent`) across the IL2CPP boundary for **every slime, every frame** | per-wrapper `ConditionalWeakTable`: a native actor costs one lookup, an imported actor reuses its marker |
| SR2 return portal | save-name string + player transform + currency every frame; three full `FindObjectsOfTypeAll<MeshRenderer>` passes at build | ten checks a second; one pass |

## Optional profile (Settings -> Performance)

`off` (default) touches nothing. `balanced` caps the shadow distance at 30 m, turns soft particles off, raises the async texture upload slice
(fewer streaming hitches) and runs one garbage collection once the game has settled. `fast` adds no multisampling, low shadow resolution, at
most two cascades and one pixel light. It only moves quality knobs the games' own menus expose, on the player's machine, and re-applies them
when the quality level changes (`mods/shared/PerformanceTuner.cs`, env `SLIMEBRIDGE_PERF`). Both plugins log one line with the before/after.

## Still to measure (needs a game-free machine)

* LAB `perf:` runs of the sanctuary with the profile off / balanced / fast.
* First-spawn cost of a species (template build: JSON ~0.8-1.1 MB + textures) and of loading a save with many imported actors.
* SR2: the cost of the imported-visual hooks with 20+ imported actors alive.
