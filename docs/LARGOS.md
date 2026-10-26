# SR2 Largos in Slime Rancher 1

Every Largo Slime Rancher 2 has and Slime Rancher 1 lacks (113 of the 204 SR2 Largos, all extracted: the 20 Flutter ones came with the Flutter species) is imported the same way the SR2 species are:
the **plugin only reads a cache and a spec**, the **extractor builds them from the player's own SR2 install**. Nothing of the game's art is in the
repository or in the mod.

## Pieces

| Piece | Where | Does |
|---|---|---|
| Roster | `bridge/content/largos.py` (`largo_roster`) | SR2 Largo names come from the addressables catalog, SR1's own Largos from its `Identifiable.Id` enum (`*_LARGO`); the difference is the roster. A Largo is READY when both parents exist in SR1 (a native slime or an imported species), PENDING_DATA otherwise. |
| Extraction | `bridge/content/species_pipeline.py` (rows with `kind: largo`), `sr2_asset_extract.py`, `sr2_mesh_scope.py` | Same converter as the species: model (skinned parts, rigid attachments), the SR2 clips of its prefab, face expressions, and an icon composed from the two parents' icons. A Largo has no plort of its own. Largos the game keeps in `resources.assets` are read from there, the others from the addressables bundle (`BUNDLE_SOURCE`). |
| Spec | `largos.publish_spec` | Writes `species-import/<stamp>-largos/import-spec-SR1.json` = the newest species spec + one entry per Largo **that has a cache** (`largo` block: the two parents). Native ids come from the **committed table `bridge/content/largo_ids.json`** (range 960000+, every pair of 23 species pre-assigned; a local ledger only holds species added after it), so a Largo has the same id on every install: saves and online play agree on what `96xxxx` is. Never renumber; append only. |
| Registration | `mods/sr1-bridge/SpeciesRegistrar.cs` (`entry.largo`) | `IsLargo`, `BaseSlimes`, diet = union of the parents' diets, produces both plorts, native Largo prefab as template, `LARGO_CLASS`, and the entries of `SlimeDefinitions` that map "slime eats the other's plort" to the Largo (`largoDefinitionByBasePlorts`). Imported parents get `CanLargofy`. Imported slimes/plorts also join the Tarr/Largo food groups. |
| Launcher | `apps/launcher/extract.py`, `pack.py` | The Extract job lists species and Largos (`kind`: all / base / largo), then publishes the spec. `pack.prepare` re-publishes the spec when the extracted Largo caches differ from the newest spec, so a finished Extract is picked up on the next start. |

## Commands

```
py -3.12 -m bridge.content.largos                      # what is missing from SR1
py -3.12 -m bridge.content.species_pipeline            # species (the launcher's Extract does species + Largos)
py -3.12 -m bridge.content.largos --publish            # spec for the caches that exist
```

In game console: `slimebridge largos` (native Largos), `slimebridge spawn <ID> [n]` (any native id), `slimebridge spawn largo.angler.batty`.

## Look

A Largo's SR2 body masks are authored for its parents' own meshes and UV layouts; baked onto the shared Largo body they turned into marbled
noise, so the cache keeps only the Largo's palette (top/middle/bottom) and the plugin paints it on the plain Pink body material with the bottom
color pulled toward the middle one (`ImportedSpawner.RebindNativeMaterials`). Particle effects are not ported. A Saber body carries its own eyes/mouth passes: the extractor splits them into a face part on the same geometry
(`embedded_face`), so the six Saber Largos extract like any other.

## Face expressions

An expression with no mouth (blink ...) used to write an empty atlas to the mouth, which the mouth shader answers by painting its whole
area dark red: the enormous mouth that flashed on blinks and chews. SR1's own `SetExpression` leaves a role alone when the expression has none;
`ImportedFaceState` now does the same.

## LAB

`tools.lab.sanctuary_oracle ... --species-ops '["spawn:largo.angler.batty:1"]' --ops '["wait:40","actors:tag:3.0"]'` spawns Largos and
takes a close-up of every imported actor (`actors:`), `toggle:part:<text>:<0|1>` hides parts, `toggle:matinfo:0` lists materials, and
`orient` lines in the receipt give each face's direction.

## Online (hybrid servers)

`bridge/content/content_manifest.py` fingerprints the imported content a player has registered (canonical ids, native ids, plort ids, Largo
parents; no paths, no art). It is the shape of `HELLO.content` for a Bridge server: a player missing one species gets `CONTENT_INCOMPATIBLE` for that
realm instead of a crash. The launcher's multiplayer state shows it (`mp_state().content`). Run `py -3.12 -m bridge.content.content_manifest`.

# SR1 content in Slime Rancher 2 (the other way)

`bridge/content/sr1_pipeline.py` lists what SR2 lacks (its `resources.assets` has no `slime<Name>` prefab): Glitch, Mosaic, Quantum,
Quicksilver, Rad and 36 Largos mixing Mosaic/Quantum/Rad. The launcher's Extract (direction SR1 -> SR2 or both) runs it in the same capped worker, from the
player's own Slime Rancher; caches land in `Lab/AssetCache/sr1-to-sr2/<canonical>`, the spec in `species-import/<stamp>-sr1/import-spec-SR2.json`.
The SR2 plugin (`Sr2SpeciesRegistrar`, `Sr2ImportedSpawner`) reads `SLIMEBRIDGE_IMPORT_CACHE_ROOT`, registers species and Largos (parents = imported or
native, diet = union, `_largoDefinitionByBasePlorts/_largoDefinitionByBaseDefinitions` indexed by hand) and binds each cache by canonical id. Glitch and
Quicksilver wait for a diet mapping (`PENDING_DATA`). `py -3.12 -m tools.cache_audit` checks every extracted cache.

## Flutter, Shadow, Yolky (curated diets)

Their diets cannot be translated from the captures, so `bridge/content/diet_overrides.py` states them (sources: the Slime Rancher wiki and the
Slimepedia text): Flutter eats Nectar only in SR2 and SR1 has no nectar group, so it eats FRUIT (the closest); Shadow and Yolky have no diet in SR2
(Yolky's plorts come from the Hen Hen, a Shadow slime drops plorts when hit with food), so they are imported as non-feeding species (an empty eat map
is accepted for them). `py -3.12 -m bridge.content.diet_overrides --publish` writes the `*-diets` base spec; the spec builder applies the same table.
The extractor accepts their shader variants (`Eyes Shadow`, `Mouth Shadow`, a face with no mouth, a particle trail that follows no bone, a static aura
on the root), and a species with its own `SR2_<name>_body/face_LOD0` meshes. A body with no baked markings is painted on the plain Pink body
material, so it no longer inherits the placeholder species' (Tabby's) stripes as a hard patch.

