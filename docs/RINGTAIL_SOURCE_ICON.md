# Ringtail source Vacpack icon in SR1

Status: source Sprite integration, native inventory API smoke, real same-save process restart
and delegated screenshot inspection are verified. This is not a declaration that the animated
Ringtail milestone is functionally complete.

## Implementation

- `bridge/content/sr2_icon.py` resolves the genuine SR2 Ringtail appearance Icon: appearance 31303,
  Sprite 3052, Texture2D 1324. It exports the full, unresampled 1024 x 1024 source RGBA texture,
  logical rect/pivot/units and original tight Sprite mesh (255 vertices, 759 indices).
- `ImportedIconLoader` accepts a hash-covered source Sprite descriptor in the cache manifest,
  preserves its framing/geometry and rejects unsupported packing or invalid paths/metadata.
  Ringtail without a valid source icon fails registration instead of silently using a Tabby icon.
- `SpeciesRegistrar` creates an import-owned CLASSIC appearance, new default array and new dynamic
  list. It never changes the template's icon, face, structures, material references or arrays.
  Unpublished owned assets are cleaned up; published assets remain alive while native registries
  and UI may reference them. Failed published identities cannot be retried in the same process.

The extractor returns an `icon` + `files` fragment for a NEW private v4 manifest. This milestone
has not yet integrated that merge into automatic launcher/full-converter execution.

## Real LAB test

Protected SR1 run: `species-import-20261009-030319-a8805a735d45`.

- Source face/continuous-animation model bytes were retained unchanged; only the new manifest/PNG
  provide the icon.
- Spawned the native imported Ringtail, added 2 to Vacpack, released 1 through native APIs,
  saved the remaining 1 and read it back.
- Stopped the owned PID 25356, restarted the exact LAB executable as PID 49920 and loaded the
  same save. The query found the remaining Ringtail in slot 0 with count 1.
- Registration readbacks before and after reload report a chosen import-owned CLASSIC appearance
  with source icon loaded. Native Tabby reference identity was NOT inspected in the live process.
- Two real screenshots were captured from the focused owned game window, before and after restart.
  Independent read-only visual inspector `deleg_408e888e`, using the source PNG and both captures
  attached natively, confirms the genuine Ringtail head/ears/muzzle/banded tail, matching colors,
  orientation and framing, and `Ringtail x 1` in both first slots. No obvious clipping or opaque
  rectangle surrounds the HUD icon. Parent direct vision remained quota-blocked (429); this
  observation is explicitly delegated, not a claim that the parent saw the pixels.
  The inspector also notes lighter/shinier world actors; this is NOT full 3D visual acceptance.
- No NullReferenceException appears in either captured game log. Source installs stayed unchanged;
  saves and exact previous LAB plugin/cache state were restored. SR2 plugin stayed unchanged.

The extra idle-clock gate failed in this save-heavy run (0.252 s maximum frame-pair deviation).
No tolerance was loosened and this run does not certify uninterrupted animation timing.
See the separate source-animation receipt for its earlier uninterrupted-idle proof.

## Checks and limits

- Native plugin build: 0 errors, 59 pre-existing warnings.
- Focused suite: 363 passed, 2 skipped, 90 subtests passed.
- Production-linked icon harness: 56 checks, failures=0; Unity boundary is synthetic.
- Whole suite: 1284 passed, 12 skipped, 2166 subtests passed, 6 pre-existing failures.
- Independent frozen review: APPROVE, no findings. Its initial Windows sandbox cleanup failure
  was resolved with the identical frozen files and hash-verified raw parent test logs; no
  production code was changed to satisfy the sandbox.

Still unproven: manual suction/shoot controls, live native-Tabby reference isolation, full 3D
visual parity, animation/gameplay state hooks and full automatic conversion/launcher integration.
The source PNG's white background alone cannot certify alpha, and still images cannot establish
animation or technical save identity; the restart receipt provides the latter separately.

Receipt: `docs/reviews/ringtail-source-icon.json`.
No source PNG, Sprite mesh dump, save, native DLL or decompilation is tracked in this milestone.
