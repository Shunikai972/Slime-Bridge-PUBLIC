# SR2 baked imported colours

## Scope

The SR2 renderer now applies measured linear RGB gains to the cloned HDRP/Lit
materials of 24 exact authored Largo cache revisions. The source PNGs, shared
native materials and SR1 rendering path are not recoloured.

`Sr2BakedColourProfiles.Resolve` requires an ordinal match on canonical ID,
model JSON SHA-256 and cache-manifest SHA-256. `NativeMeshCache.ManifestSha256`
is calculated from the manifest bytes already read by the guarded loader, not
from a second read of the live cache directory. A texture-only postpass therefore
invalidates the profile even if model JSON stays unchanged. Unknown/revised
caches and non-HDRP/Lit shaders receive a neutral gain. Updating a profile
requires new ungraded game captures; do not reuse a profile for another revision.

The gains are passed with `Material.SetVector`, with alpha fixed at one. Existing
texture colour-space settings, alpha clipping and texture ownership are retained.
The resolver performs one small dictionary lookup per baked material construction;
there is no new per-frame colour pass, texture rewrite or shader variant.

## Verification on November 1, 2026

The tested game was SR2 1.3.0, Unity 6000.0.73f1, HDRP, MelonLoader 0.7.3.
Only an independent LAB copy was launched. Original game fingerprints remained
unchanged and the protected native save/profile backup was restored after each
run.

The production build (no calibration environment variable or probe code) produced
24 operation receipts and 24 foreground/background capture pairs. The difference
mask selects foreground pixels without exposure matching, channel fitting or RGB
normalization. Against the authored reference renders:

- Baseline average per-model mean-colour Delta E: 21.0667.
- Production average: 6.8375.
- All 24 per-model mean-colour distances decreased.
- Largest remaining mean-colour distance: 14.2 (Quantum Twin).

These are image comparison measurements, not artistic acceptance or a guarantee
of identical lighting. The original comparator incorrectly adjusted RGB channels
using the image border; that transformation was removed and pinned by regression
tests. In particular, a measured green pixel must remain its original RGB value.

Local evidence is retained under ignored
`Lab/Reports/colour-resume-20261101/colour-production-v1-24-*` and
`Lab/RuntimeArtifacts/sr2-newgame-20261101-032657/`.
The tested plugin SHA-256 is
`28138158f69ad02c58a1727c6711d605f58588f6d6a7733ae913dda35c8acc30`.
Game-derived screenshots and caches are not distributed with the source.

A subsequent cleanup correction was exercised with 28 production-finally fault
scenarios: every renderer/player/camera/target/resource restoration is attempted
independently, even if a native boundary throws. Cleanup failures are reported,
and an original capture error is retained when cleanup also fails. The test does
not simulate a renderer destruction inside real Unity callbacks.

The corrected production build then completed another 24-model LAB run:
`Lab/RuntimeArtifacts/sr2-newgame-20261101-050040/`, raw mean-colour Delta E 6.8167,
worst 14.2, eight native Daze Awake exceptions still present. The plugin SHA-256 is
`15e45eb209c0712a5c873d2e1d82ae2c0dc08b6d5afc698f26f25c23a569d385`.
Original game fingerprints remained unchanged and protected saves were restored.

Production-linked managed checks cover PNG filters 0-4, asymmetric RGBA and row
orientation, exact uploads, sRGB/mipmap settings, clone ownership and cleanup,
HDRP and fallback material configuration, invalid/nonfinite floor configuration,
profile invalidation and the actual BakedMaterial call site. Managed doubles are
not substitutes for Unity/IL2CPP or GPU checks.

## Related lifecycle and evidence corrections

Reference prefabs must be instantiated under an already-inactive persistent
parent. Deactivating after Instantiate cannot undo native Awake callbacks. Both
slime and plort registration use the same helper; the original prefab is not
mutated. A managed control reproduces the old ordering failure.

The explicitly requested new-game intro may advance only after Start returned
successfully and while its exact AutoSaveDirector identity remains unchanged.
Its own native input/transition gates drive progression; requiring gameplay
readiness first would deadlock the introduction. General gameplay readiness
checks were not relaxed.

LAB captures frame imported renderers, temporarily hide first-person player
renderers, preserve the main camera's exposure history and restore camera,
renderer and render-target state. They do not disable player colliders or
behaviours. Isolated captures reject mixed non-spawn operations.

## Remaining limits

- Calibration is measured in one common scene/exposure setup, not across every
  biome, time of day or player graphics configuration.
- Mean-colour distance does not establish spatial palette/contrast equality.
  Camera angle and source animation pose still differ on some models; Quantum
  Twin remains a conspicuous higher-distance case.
- The original Quantum Hyper procedural/cosmic shader is not reproduced losslessly
  by the baked HDRP fallback.
- The final production run still logged eight native DazeAndGotoConsumable.Awake /
  DazePeriodically.Awake NullReferenceExceptions. Their cause has not been proved;
  this is not a zero-exception runtime claim.
- No comparative GPU timing was captured. Recorded process CPU/memory includes
  boot, loading and captures and is not a steady-state rendering benchmark.
- SR1 mask behaviour is regression-tested; this colour change does not constitute
  a new full SR1 in-game visual validation.
