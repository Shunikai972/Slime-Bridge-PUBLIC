# Formats

Tout est généré localement sous `--output` (ignoré par Git). Espace de coordonnées : **Unity** (main gauche, +Y haut,
UV origine bas-gauche) partout, sauf `preview.glb` (glTF, converti « ReverseZ » comme UniGLTF).

## `.sbmesh` (géométrie sans perte)

`b'SBM1' | u32 header_len | header JSON | blob`. Le header liste les tableaux
`{name,dtype,shape,offset,nbytes}` (`positions`, `normals`, `tangents`, `uv0..uv7`, `colors`, `bone_indices`
(u16 ×4), `bone_weights`, `bind_poses` (B×4×4), `indices<i>` par sous-mesh), `bone_name_hashes`, bornes, `source`.
Lisible en C# (BinaryReader) et numpy (`ir/store.unpack_mesh`). Les LOD sont des meshes distincts.

## Paquet IR : `ir/<GAME>/<canonical_id>/species.json` (`sbconv-ir` v1)

`source` (jeu, Unity, backend, pipeline, empreintes) · `species` (nom, kind base|largo, `canonical_id`
`slime.<slug>` / `largo.<a>.<b>`, bases, définition) · `prefab` (nœuds avec TRS locaux, composants, `bone_table`
enum→nœud) · `appearance` (`parts` → `lods` → renderers/mesh/matériaux ; `face` : expression → œil/bouche ;
`palette`; extras : stone form, overrides d'animation, apparences dépendantes) · `animation` (contrôleur complet,
clips `anim/clips/*.json` en clés Unity : temps, valeur, pentes in/out, modes de tangente) · `gameplay`
(MonoBehaviour = **métadonnées**, colliders, rigidbodies) · `materials`/`textures`/`meshes` · `provenance`
(SHA-256 des fichiers sources de l'installation) · `problems` · `files` (SHA-256 de chaque fichier du paquet) · `status`.

Aucun horodatage : à entrées identiques, le paquet est identique (reproductible).

## Paquet runtime : `runtime/<SRC>-to-<TGT>/<id>/` (`sbconv-runtime` v1)

| Fichier | Contenu |
|---|---|
| `package.json` | index, statut, fidélité des matériaux, exigences d'adaptateur, `files` (SHA-256), avis légal |
| `parts.json` | pièces → LOD → renderers : `mesh_file`, `material_slots` (ids de recettes), `bones` (chemins), `root_bone`, `parent_bone` |
| `rig.json` | squelette relatif à la racine `Appearance` (TRS), table `bone_enum` |
| `materials.json` | **recettes** de matériaux (voir `MATERIALS.md`) |
| `face.json`, `palette.json` | expressions → recettes ; palette Top/Middle/Bottom/Ammo |
| `animation/controller.json` + `clips/*.json` | contrôleur source, clips filtrés au squelette, `plan` vs états natifs du jeu cible |
| `adapter.json` | comportements à réimplémenter côté jeu cible (avec présence du script dans la cible) |
| `preview.glb` | géométrie + squelette + skins + animations (échantillonnées 30 Hz) pour inspection |
| `provenance.json` | empreintes des installs, hashes sources, outil, avis légal |

Un chargeur en jeu (SR1/SRML, SR2/MelonLoader) : (1) lit `rig.json` et recrée la hiérarchie sous `Appearance` ;
(2) construit les `Mesh` depuis les `.sbmesh` (positions, normales, tangentes, UV, poids, bind poses, sous-meshes) ;
(3) pour chaque recette : clone le matériau natif `template`, applique `floats/colors/textures/keywords`, charge les
PNG ; (4) crée les `AnimationClip` depuis les courbes, les branche via les états natifs listés dans `plan.state_map`
et pilote lui-même ceux sans équivalent ; (5) implémente les comportements de `adapter.json`.
