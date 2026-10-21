# Ringtail source animation (SR2 to SR1)

Status: the decoded SR2 `Idle_Cotton_Ringtail` clip plays continuously on the imported Ringtail in the
protected SR1 LAB. This is NOT a claim that Ringtail animation is finished.

## What changed
- `ImportedSourceAnimation` builds shared legacy clips from the cache `source_animation` descriptor
  (idle override, idle, pounce, bite) and plays them through Unity `Animation` with one independent
  clock per actor. Clips are template-owned; clones borrow them and never destroy them.
- A declared descriptor is authoritative: if it is malformed the whole visual is refused. When it is
  present, the procedural `SlimeBridgeJiggle` approximation is NOT added.
- A clone whose `Animation` lacks runtime states re-registers the same shared clip (tested).
- `TryPlay` / `TryCrossFade` exist by exact source clip name. No gameplay state is wired to them yet.
- LAB-only `LabSourceAnimationTrace` records bone locals every 0.1 s for the proof; it exists only on
  the exact LAB executable in rest-pose mode.

## Proof (SR1 LAB, real Unity)
- 39 timed samples over about 3.96 s, the 1.97 s loop wrapped once, no jiggle component.
- Clip time follows the wall clock: cumulative drift 0.019 s, worst frame-pair deviation 0.048 s.
- 663 bone samples against the decoded clip: max position error 1.6e-7, max rotation error 3.0e-3.
- Exact plugin/cache rollback, 0 NRE, saves unchanged.

## Honest limits
- The reference is a linear reconstruction of 30 Hz decoded samples, not SR2's own interpolation.
- Two thresholds were relaxed after the first live trace (see the receipt `threshold_history`); the
  residual 3e-3 rotation error is unexplained.
- No Animator graph, gameplay states, blinking, SR2 side-by-side or GPU squash/wobble.
- Screenshots show the slime visible and unoccluded, but one frame shows a second slime tipped on its
  side that I have not explained. Visual acceptance of the motion is not claimed.

Receipt: docs/reviews/ringtail-source-animation.json
