# Ringtail source face (SR2 to SR1)

Status: three-pass source face renders in the protected SR1 LAB. This is NOT a claim that the
Ringtail is finished: animation, icon, gameplay, save/reload and transfer are separate gates.

## What changed
- Exporter (converter 4) resolves the unique default appearance, LOD0 material overrides, FaceRules
  and the serialized initial expression. Ringtail exports ONE 73-vertex submesh with three ordered
  passes: 494 Null Render, 405 eyeHappy, 426 mouthTabby (expression 15).
- Fail-closed export contract: roles exactly [base-null, eyes, mouth]; integer render queue in
  [-1, 5000]; only _FaceAtlas on eyes/mouth; no textured null pass.
- Runtime builds the three passes from native SR1 shaders, with source colors, floats, queue,
  keywords, atlas scale/offset and raw atlas pixels. Missing shader or metadata fails visibly;
  there is no Standard fallback, no synthetic atlas and no collapse to material 0.
- Face passes are template-owned (SourceFaceResourceOwner); actors borrow them.
- LAB-only framing (LabRestPosePin) holds the actor in front of the camera so a screenshot is not
  hidden by the HUD, the Vacpack or the actor's back. Gated on the exact LAB exe and the rest-pose flag.

## Proof levels
- COMPILED, RUNTIME_LOADED, GEOMETRY_VERIFIED: 9 faces and 36 renderers read back in SR1, max vertex error
  1.85e-05 against the 2e-05 tolerance, 0 actor-owned resources, 0 NRE, exact plugin/cache rollback.
- VISUALLY_VERIFIED: partial. Real screenshot shows the face upright and unobstructed with eyes and a mouth,
  but there is a hard-edged dark region beside the eyes. It may be the source atlas (G channel
  steps from about 172 to 38 across three rows) or a shader difference. Needs an SR2 reference capture.
- Not verified: continuous animation, blinking/expressions, mouth palette completeness, GPU color space.

Receipt: docs/reviews/ringtail-source-face.json
