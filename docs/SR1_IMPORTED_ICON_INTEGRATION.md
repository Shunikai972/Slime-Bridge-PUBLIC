# SR1 imported slime icons: verified static integration contract

Status: source diagnosis verified; implementation and game/reload validation pending.
This does not claim icon pixel parity, UI rendering or save compatibility.

## Evidence boundary

The parent independently reran targeted ILSpy decompilation against the LAB SR1
`SlimeRancher_Data/Managed/Assembly-CSharp.dll` and recomputed its SHA-256:
`65eb0e111fd6aa4c9b2ea0c6e17758b17a4d91fc097c3423af443e21237e9a2b`.
Private decompilation stays outside the tracked repository. Only signatures and
behavioral findings are recorded here.

## Root cause and native UI path

`mods/sr1-bridge/SpeciesRegistrar.cs:339` assigns the template's
`AppearancesDefault` array directly to the imported definition. Lines 399–403
unlock/select its first appearance. The Ringtail definition therefore uses a
shared Tabby appearance; changing that shared object's `Icon` would affect Tabby.

Verified calls in this exact assembly:

- `AmmoSlotUI.Update()` resolves the sprite through private
  `Sprite GetCurrentIcon(Identifiable.Id id)`.
- For slimes this delegates to public
  `Sprite SlimeAppearanceDirector.GetCurrentSlimeIcon(Identifiable.Id slimeId)`.
- That getter reads `GetChosenSlimeAppearance(slimeId).Icon` and falls back to
  `missingIcon` when the sprite is null.
- `LookupDirector.GetIcon(Identifiable.Id id)` also delegates slime icons to the
  appearance director. A slime `VacItemDefinition` is not needed for this path;
  non-slime/plort icons follow the separate vac-item lookup.
- Public `UnlockAppearance(SlimeDefinition, SlimeAppearance)` and
  `UpdateChosenSlimeAppearance(SlimeDefinition, SlimeAppearance)` are available.
  The latter invokes the selection path that raises `onSlimeAppearanceChanged`.
- `AmmoSlotUI.OnSlimeAppearanceChanged(SlimeDefinition, SlimeAppearance)`
  refreshes icons/colors only for slots with the corresponding identifiable ID.
  A late `Icon` field assignment alone does not trigger this event.

## Ownership-safe implementation boundary

`SlimeAppearance` is a `ScriptableObject` with public `Sprite Icon`,
`Palette ColorPalette` and `AppearanceSaveSet SaveSet` fields.
`SlimeDefinition` has `SlimeAppearance[] AppearancesDefault`,
`List<SlimeAppearance> AppearancesDynamic`, and
`GetAppearanceForSet(SlimeAppearance.AppearanceSaveSet)`.

Create an import-owned appearance copy, set only its icon, and install it in a
new import-owned default-appearance array. Do not mutate the template array,
native appearance, structures, face or material references. Keep the copy,
sprite and texture alive for the registered definition's lifetime; defer cleanup
until no registry, director, actor or UI references them. This ownership/lifecycle
must be verified in runtime tests, not inferred from the static API.

Choose a CLASSIC appearance explicitly. `SlimeAppearanceDirector.InitModel`
requires CLASSIC for every non-largo definition and throws when it is missing.
`SetModel` resolves persisted selections/unlocks by identifiable ID and SaveSet
via `GetAppearanceForSet`; sprite data is not stored in those selections.
Registration must recreate the owned definition/appearance before that resolution.

## Source image caveat

`Lab/RuntimeArtifacts/ringtail-icon-source-proof-747e14c2/icon-receipt.json`
identifies source Sprite `iconSlimeRingtail`, path ID `3052`, decoded crop
`1007 x 952`, PNG SHA-256
`29b1abee0df942b730dde064d5626da5c9c83b2306236adbb82b78e28a589675`.
Its `reference_pixels_match` is false. The historical reference PNG must not be
called an exact copy of the source Sprite.

Before importing it, verify and preserve the source logical rect, pivot,
packing orientation and texture-rect offset. Do not center the tight crop and
assume that it reconstructs the source Sprite. Keep extraction and derived
images local/ignored, with source and output receipts.

## Required runtime gates

1. Native Tabby icon/appearance/array references remain unchanged.
2. Ringtail has a distinct owned CLASSIC appearance; chosen-icon getter and
   actual occupied Vacpack slot use its source-derived sprite.
3. Real screenshot shows correct orientation, framing, alpha and shape.
4. Save/reload resolves the import-owned CLASSIC appearance and restores the icon.
5. Repeated registration/scene lifecycle does not leak owned textures/sprites or
   destroy objects still referenced by the native directors/UI.

None of these runtime gates has been run for this icon correction yet.
