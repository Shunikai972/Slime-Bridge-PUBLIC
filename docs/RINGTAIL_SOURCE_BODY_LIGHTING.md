# SR2 Body to SR1 Stripe: source lighting controls

Scope: a bounded parameter-loss correction, NOT a full Ringtail color-parity claim.
The regional Red/Green/Blue palettes and source color-mask composition remain an open,
separate rendering defect. No palette swap or blanket gamma conversion is justified.

## Verified local shader contract

Read-only inspection of the installed LAB assets used actual D3D11 bytecode, not the
AssetRipper dummy ShaderLab exporter. Source material `slimeRingtailBase` uses the
`SR/AMP/Slime/Body` shader with `_BODYCOLORING_MULTI`, `_LIGHTMODEL_DEFAULT`, `_STATIC`.
`_ENABLESTRIPES_ON` is NOT enabled; a texture being serialized does not prove it is sampled.
The analyzed source pass is vertex blob 10 / fragment blob 169. Compiled variants from
other material keywords must not be substituted for these evidence paths.

The source common parameter table and destination pass `m_NameIndices` plus fragment
blob 8 parameter metadata establish the following byte offsets:

| Control | Source UnityPerMaterial / cb3 | Destination $Globals / cb0 |
|---|---|---|
| `_LightingUVHorizontalAdjust` | 624 / [39].x | 160 / [10].x (name index 26) |
| `_LightingUVContribution` | 620 / [38].w | 164 / [10].y (name index 27) |
| `_BodyLightingContribution` | 616 / [38].z | 168 / [10].z (name index 28) |

Each source property is a scalar Range with bounds [0, 1]. Ringtail declares UV=0,
Body=1, Horizontal=0; the SR1 Tabby template declares UV=1, Body=1, Horizontal=0.
The old body-row export omitted these floats and shader identity, so the final rebind
could only keep template values. This correction carries the source declarations,
including numeric zero, into the final instance-owned Stripe clone.

## Mathematical bound

Both compiled shaders form the same UV-coordinate control:

    t = saturate((length(float2(u - 0.5, v)) - 0.25) * 1.333333)
    q = v + Horizontal * (t*t*(3 - 2*t) - v)
    z = 0.5 + UVContribution * (q - 0.5)

UVContribution=0 therefore fixes this intermediate to 0.5 in either shader. It does
NOT remove all lighting or texture masking. The downstream lighting branches differ
(e.g. source UV weight 0.6 versus destination 0.85, different view/specular terms).
Parameter-role compatibility is not an equivalence proof of HDRP and Built-in RP.

## Implementation boundary and validation

- Extractor v5 preserves the true shader name and the three unrounded source floats.
  Known Body controls must all be finite numeric values in [0, 1], without duplicates.
  A malformed declared contract is refused rather than guessed or clamped.
- `ImportedBodyLighting` accepts MiniJson integer (long/int) and fractional (double/float)
  storage, including exact zero. Old caches without floats keep their previous fallback.
- Only the measured destination `SR/AMP/Slime/Body/Stripe` receives this adapter. All three
  destination properties must exist before any mutation. Face and unrelated shaders are
  untouched. Existing gloss, palette, textures, geometry and animation are unchanged.
- The marker transports an explicit serialized presence flag and three `[SerializeField]`
  floats through Unity cloning. The immutable managed adapter is not serialized: each clone
  reconstructs and caches it from those validated scalars. Legacy absent controls stay absent,
  rather than becoming an implicit all-zero payload.
- LAB rest-pose diagnostics expose `source_body_lighting_matches` for declared controls,
  read from the final renderer material. A declared but invalid payload yields `false`, not
  an omitted field. The protected operator must require these booleans before claiming a
  successful parameter round trip. Material readback alone does not prove that a
  MaterialPropertyBlock or lighting/postprocessing yields color parity.
- Source-linked CPU harnesses test parsing, range bounds, unsupported/partial contracts,
  a scalar-field transfer model (source UV=0 versus template UV=1), repeated rebinding,
  absent-control preservation, and production rest-pose readback after an override or an
  invalid serialized payload. The field-transfer model is NOT a real `Object.Instantiate`
  execution. Unity cloning and GPU rendering still require the protected LAB run.
  Native compilation uses the exact LAB Mono assemblies.

The first independent frozen review rejected the original managed `BodyLighting` field
(P1): its non-serializable readonly payload could not survive template instantiation.
The corrected marker now follows Unity 2019.4's documented primitive-field serialization
rules (https://docs.unity3d.com/2019.4/Documentation/Manual/script-Serialization.html).
Review/test success must not be reported as real Unity clone or rendered color parity.

## Remaining source-material work

The SR2 parameter blob for fragment 169 is selected through player-program group 3,
position 54, whose parameter index is 87; such type-5 entries are parameter data, not DXBC.
A raw named `_ColorMaskUV1` record is present there, followed by a little-endian value
520; this supports, but without a full parameter-record parser does not independently
certify, the selector binding. The compiled selector at cb3[32].z (offset 520) chooses
TEXCOORD1 (Unity `uv2`) when nonzero and TEXCOORD0 when zero. The named `_ColorMask`
texture binding to t0 also remains unconfirmed; prefix string matches are insufficient.
Body and ears have distinct source uv/uv2 arrays; the imported SR1 mesh currently drops
uv2. The tail has no exported uv2. Renderer/property-block overrides and the tail's real
sampling behavior must be resolved before switching the derived Stripe mask's UV selector.
This lighting slice does NOT silently change mask UVs or pretend the monochrome mask can
represent all four source palettes.

A full source-composition port remains necessary for regional color fidelity. The currently
located Unity Editor is 6000.6.3f1, while SR1 runs Unity 2019.4.29f1; a compatible custom-shader
build pipeline is not established. Do not certify a backwards-incompatible bundle, the
fallback bake, the icon, or a dawn screenshot as the missing common rendering oracle.
