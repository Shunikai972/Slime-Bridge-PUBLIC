# Pack and play: everything linked

One command builds the project, makes every piece ready and starts the game:

```
py -3.12 build_all.py --play            # SR1 (add BOTH to start SR2 too)
```

It runs, in order: Python syntax check, both plugins (Release), the C# test harnesses, the deploy into `Lab/Games`, the species
caches (`bridge.content.species_pipeline`), then `apps.launcher.headless`, which is the launcher's Play flow without a window.
Logs: `Lab/LauncherDev/play.log`. Other switches: `--plugins-only`, `--deploy`, `--tests`, `--launch-sr1` (developer dev session).

## What "linked" means

For a LAB copy of SR1 the launcher backend (`apps/launcher/pack.py`) now does, on every Play:

| Piece | What happens |
|---|---|
| Plugin | copied when missing/outdated (`plugins.install`) |
| Island + portal to SR2 | the newest sanctuary layout is installed next to the game (`<SR1>/SlimeBridge/sanctuary`), the plugin builds the island at the end of the Starlight Strand walkway |
| BridgeHost + crossing broker | started by the launcher backend; `apps.launcher.headless` keeps polling the broker, so the sanctuary portal can start SR2 and bring the player back |
| Imported SR2 species | armed through `SLIMEBRIDGE_IMPORT_SPEC` (+ LAB exe/out/run) for the exact LAB executable; models, icons, clips and plorts come from the species caches |

Real installs keep the explicit flow (the launcher does not copy anything into them behind the player's back).

## Extraction is part of the launcher

The Extract page runs the repository pipeline in-process (`apps/launcher/extract.py: BuiltinExtractJob` ->
`bridge/content/species_pipeline.py`); the external `sbconv` converter is only used for the SR1 -> SR2 direction
(`engine: external`). A fix to the extractor is therefore a fix to the launcher. For each READY species the pipeline:

* picks the source animation clips from the species' own Animator controller / appearance overrides and trial-decodes
  each one (a clip the decoder refuses is skipped and reported, e.g. `SloomberYawn`);
* extracts complete-source (model, textures, icon, clips) and falls back to the mesh-only scope with the same clips for
  species the strict reader refuses (Sloomber);
* writes the species' own plort look (`plort.json`: palette, optional stripe texture, icon);
* upgrades older caches in place (`bridge/content/cache_upgrade.py`) and re-hashes every manifest file.

`py -3.12 -m bridge.content.species_pipeline [--species Ringtail ...] [--force]` runs it from the command line.

## Fixes made through this pipeline (2026-10-17)

* **Angler / Cotton rendered as the Tabby placeholder:** they had no converted model cache. Built; hotbar icons are the real ones
  (seen in game for Angler, Cotton, Sloomber, Ringtail).
* **Ringtail textures:** SR2 body shaders sample their colour mask / stripe / baked albedo with the unique `uv2` atlas, not
  UV0 (a tiling set spanning -0.5..1.5). The cache now records `albedo_uv_set` per renderer and the loader uses it.
* **Deformation (hop squash, vacpack pull, thrown slimes):** SR1 springs the jiggle bones through `RubberBoneEffect` on the
  appearance root; the imported visual had none. `ImportedRubberBone` copies the placeholder's effect onto the imported rig.
* **Plorts:** imported plorts were Pink-plort clones. `ImportedPlortAppearance` applies the species palette / stripe texture
  and icon (SR1's `SR/Slime/Plort` uses the same colour properties as SR2's).
* Dev aids: `slimebridge spawn plort.<species>` and `spawn:` / `vac_add:` ops accept plort ids.

## Second round (2026-10-17, after the first real play session)

* Eating motion: a source bite clip now plays to its end (native completion arrives earlier, quick chomps almost at once, and
  cutting to idle there hid the motion); the source `Digest v4` clip plays during digestion (hook on `SlimeEat.EatAndProduce`).
  `Digest v4` is part of the baked clips and is added to existing caches in place (`species_pipeline.add_missing_clips`).
* Island: see `docs/SANCTUARY.md` (appears when the gate is paid, short walkway, statues on the plaza, Region with a CellDirector).
* LAB readback: op `feed:<species>` spawns the species with native carrots and logs a `feed report` 25 s later.
* Console auto-complete: the command's usage string must stay `slimebridge <list|spawn|diag> [canonical]` (SRML parses it).

## Third round (2026-10-17)

* **Plort prices:** every imported plort is sold at its own SR2 price relative to SR2's Pink plort, applied to SR1's Pink price (so it keeps
  its rank: Cotton lowest, Sloomber / Twin highest), with its own saturation; the Plort Market's board shows a row per imported plort.
  Source: `bridge/content/sr2_market.py` (SR2's `Plort Market Default Values`), stored in each species' `plort.json` (`market`).
* **Facial expressions:** `face-expressions.json` (+ atlas PNGs) per species, extracted by the pipeline from SR2's `SlimeFace`; the SR1 face
  animator's `SetExpression` (scared, hungry, alarmed, awed, chewing, blinking, happy ...) now changes the imported face
  (`ImportedFaceExpressions`). Each actor owns its face materials.
* The island / portal / gold statue / tablet: see `docs/SANCTUARY.md`.
* `species_dev_session` no longer turns the diagnostics traces on by default (`--diag` opts in).

## Honest status

Compiled and unit-tested. Seen in game (LAB readbacks and shots): the species icons, the Tabby fix, Ringtail and Cotton eating
native carrots and producing their own plort ids with no exception, the island locked (only the depositor at the dead end) and
unlocked (short walkway, two statue rings on the plaza). **Not yet seen by me:** the Ringtail texture fix, the plort look, the
deformation of imported slimes and the new bite/digest motion (the player reports back). The Sloomber yawn clip is not decoded.
Species registration remains a LAB-only boundary of the plugin.
