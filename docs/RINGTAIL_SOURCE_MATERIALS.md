# Ringtail source materials and color fidelity (SR2 to SR1)

Status: the two discovered destination-mismatch causes are FIXED, runtime-loaded with a same-save
restart, and CONFIRMED as a visible improvement by independent delegated image inspection with
quantified pixel A/B. Residual face details (mouth tone, cheek mark) remain open for a human eye;
this is not a claim of full rendering parity.

## Evidence chain for the fix

The user's report (wrong dark patches, over-bright highlights) was reproduced as code-level
mismatches against the SR1 native material contract, extracted read-only from the local game
(`sharedassets2.assets`, resources.assets SHA c0341bb3...):

- The destination body shader is `SR/AMP/Slime/Body/Stripe` (shader object 3226) with
  `_TopColor/_MiddleColor/_BottomColor`, `_Gloss/_GlossPower`, `_StripeTexture` (NoScaleOffset),
  `_BodyLightingContribution` and lighting-UV floats. Its shipped masks define the
  `_StripeTexture` convention: BRIGHT = plain body, DARK = the marked region
  (`body_stripes` Tabby: 94.5% white, 179 floor; `body_stripes_hunter`: 0 floor).
- The SR2 source `_ColorMask` uses the OPPOSITE convention (bright = marking; R = dark markings
  incl. ear pattern, B = tail ring bands). The previous converter wrote `max(R,B)` straight
  into `_StripeTexture`, so the stripe treatment covered the unmarked body and the real
  markings stayed untreated - the reported wrong color patches. The destination mask is now
  the inverted union (`destination_stripe_mask`, measured correlation -0.997 with `max(R,B)`).
- The rebind clone previously kept the TEMPLATE species' gloss (Tabby `_Gloss` 1.2), far
  shinier than the source (`_GlossRGB` mean ~0.35), which produced the over-bright highlight
  look. The loader now carries the source `_GlossRGB`/`_GlossPowerRGB` means and the rebind
  applies them to the native clone (`SetMaterialFloat`, `HasProperty`-gated; NaN = source
  declares none, template value untouched).

## Verification so far

- TDD: `tests/content/test_stripe_mask_convention.py`, `tests/test_imported_gloss_contract.py`
  (RED before the change, GREEN after).
- Extractor regeneration of the complete v4 cache from the real local SR2 source
  (`Ringtail-overnight-colorfix`, 877 vertices, 10 textures, icon + 4 clips + face passes).
- Protected LAB run `ringtail-colorfix-run-6320c34cc931` (session
  `species-import-20261009-043205-6e257bee6001`): native spawn, Vacpack add/release, count 1
  saved, real process restart 47484 -> 52144 with the count preserved, zero post-marker NRE,
  sources untouched, saves restored, exact plugin/cache rollback verified. Captures:
  `sr1-ringtail-colorfix-before-reload.png` / `reload/sr1-ringtail-colorfix-after-reload.png`.
- Visual acceptance: CONFIRMED as an improvement (deleg_85938b3c): gloss adouci (quasi-blancs corps
  ~4-5% -> ~1%; teinte de zone 139/113/89 -> 99/73/55 vs icone 114/78/57), aucune grande plaque sombre
  sur la capture apres redemarrage, capuchon/oreilles/bandes brunes plausibles, le "vert" etait le
  reticule du Vacpack. Ouverts : bouche rouge vs brun fonce de l'icone, yeux en points a distance,
  petite tache rouge de joue non presente sur l'icone (oeil humain conseille).

## Limits

- The stripe treatment magnitude/color on the native shader (how dark/which ramp tone the
  marked areas take) is fixed by the destination shader, not configurable here; still images
  from one lighting/camera context are the acceptance evidence.
- The save-heavy scenario failed its supplementary animation clock gate (recorded separately,
  no thresholds loosened); uninterrupted idle timing remains the earlier milestone's proof.
- GPU squash/wobble parity, SR2 side-by-side comparison and mouth atlas completeness remain
  unverified.

Receipt: `docs/reviews/ringtail-source-materials.json`.
