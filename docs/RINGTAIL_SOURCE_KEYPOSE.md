# Ringtail source AnimationClip keypose checkpoint

This is a verified single authored keypose, **not** native SR2 Animator/jiggle
parity, continuous animation or final visual/gameplay acceptance.

## Implemented contract

`bridge/content/unity_animation.py::decode_generic_clip` accepts a decoded
Unity AnimationClip typetree and a CRC-validated source path map. It returns
unchanged Unity-space local position/rotation/scale samples for bounded generic
streamed/dense/constant tracks. It rejects unsupported curves, malformed IDs,
missing storage/path coverage, nonfinite values and oversized expansions.
Streamed tracks use authored cubic coefficients; dense tracks remain on their
source lattice. Quaternion normalization/sign continuity is explicit.

Real private clips 2170 (`Idle_Cotton_Ringtail`), 2165 (`Pounce v4`), 2187
(`Idle v4`) and 2188 (`Bite v4`) decoded with 60/90/60/60 frames and
51/48/51/48 channels. Input hashes remained unchanged; all output scalars were
finite. Those source trees and sampled payloads remain local and ignored.

`mods/sr1-bridge/ImportedAnimationProbe.cs` is a LAB-only single-pose diagnostic.
The loader calls it after constructing the model; without the exact existing
LAB rest-pose gate and optional hash-verified `lab_source_clip` document field,
it does nothing. It reconstructs a temporary legacy AnimationClip using
Transform curves, samples a source-lattice key once, and destroys the clip in
`finally`. Imported procedural jiggle remains disabled for this measurement.
Invalid data fails closed before sampling and the loader discards that model.
This probe is not a production animation playback adapter.

## Actual protected SR1 execution

- Runtime: `Lab/RuntimeArtifacts/species-import-20261009-000829-afb8e52a17bc`.
- Operator/deployment/rollback evidence:
  `Lab/RuntimeArtifacts/ringtail-native-pose-run-bd5181a39443`.
- SR1 PID 6956 was checked against the exact `Lab/Games/SR1/SlimeRancher.exe`.
- Tested plugin SHA-256:
  `681f8d0a863ac63c99f6d9202fcf5aad0bf41baf9b777edab8933f2bd6e1407f`.
- Source clip `Idle_Cotton_Ringtail`, key 15 at 0.5 seconds.
- One spawn requested; diagnostics contain 9 active proof entries and 36
  renderer records. Every entry retains 38 bones and four skinned renderers.
- Ordered skin bones and rootBone match the source appearance mapping.
- All 877 vertices per proof entry were compared with source-derived CPU
  skinning at that key. Maximum Euclidean renderer-local error:
  `1.7754183356109053e-5`; predeclared tolerance `2e-5` Unity units.

| Part | Vertices | Largest runtime vertex error |
|---|---:|---:|
| Body | 288 | 1.739105770940997e-5 |
| Face | 73 | 1.4126743615900913e-5 |
| Ears | 160 | 1.5004803604472024e-5 |
| Tail | 356 | 1.7754183356109053e-5 |

This is not a rest-pose no-op: source-key versus rest maximum displacement is
approximately 0.168 / 0.126 / 0.209 / 1.923 units for body/face/ears/tail.
The oracle verified registration, spawn, owned process, cleanup, untouched
original-install fingerprints and exact native-save restoration. The operator
also restored the exact prior LAB plugin and canonical cache hashes; the SR2
plugin remained unchanged. No game was left running. Post-operation NRE count
was zero; this does not imply absence of all warning types.

Live diagnostics were hashed and confirmed unchanged after cleanup. Evidence:

| Artifact | SHA-256 |
|---|---|
| `source-pose-geometry-live.json` | 2951d4daa07fac5bbb45a604c637797fed5ee7190de2d33af688480bfd74422b |
| `visual-diagnostics-slime.ringtail.json` | 3ff0da91468669ac2ea83603f0ea4d07162903731aedc5de6c3b83a95bcd231e |
| `sr1-source-clip-pose.png` | 6e4878dad8f1cdd3f2a4638de7dc1b2f0b2bb5d0741cfad087bfabef9569f158 |

## Screenshot and fidelity limits

The PNG is an actual captured game frame and was inspected. The foreground
slime is clipped by the bottom edge/HUD; the Vacpack occludes another actor and
the faces are not legible enough. This framing **does not pass** visual parity.
No matched SR2 runtime screenshot or continuous source playback was produced.

`GEOMETRY_VERIFIED` / `SKINNING_VERIFIED` apply only to the decoded source key
and preceding rest-pose proof. `ANIMATION_VERIFIED`, `VISUALLY_VERIFIED`,
`GAMEPLAY_VERIFIED` and `TRANSFER_VERIFIED` remain unpassed for full Ringtail
fidelity. Source Animator blends, procedural skin/jiggle behavior, correct face
material slots/expressions, native icon and save/capture/release are separate
remaining gates. CPU/GPU playback performance has not been measured.

## Builds, tests and review

- SR1 Release build against exact LAB assemblies: passed, 59 warnings and
  zero errors; no warning in the new probe.
- Production-linked probe harness: red first, then passed. Its Unity classes
  are test shims; only the real run above establishes native sampling.
- Combined content/probe tests: 275 passed, 2 skipped, 83 subtests passed.
- Project suite: 1196 passed, 6 failed, 12 skipped, 2158 subtests passed.
  All six failed nodes are in the seven-node failure set previously reproduced
  on clean `6a625bb`; no new failing node appeared. The suite is not green.
- Independent decoder review: APPROVE, 125 scoped tests passed and all four
  real clips independently reproduced. Exact hashes in
  `docs/reviews/unity-animation-decoder.json`.
- Independent frozen C# sampler/loader/harness review: APPROVE, two tests
  independently passed; exact hashes in
  `docs/reviews/ringtail-source-keypose.json`.

No proprietary asset, private clip payload, screenshot, cache or user Largo
change belongs to this milestone.
