# SR2 : lecteur opt-in d'apparence LOD0, mesh-only v1

## Frontière de cette tranche

`bridge.content.sr2_mesh_scope.collect_appearance_mesh_scope(assets, resolved,
candidates, skeleton)` est un lecteur auxiliaire **non branché** à l'extracteur,
au convertisseur, au runtime ni à la publication de caches. Il ne crée aucun
fichier. Il retourne des objets source et de la géométrie **en mémoire**.

Entrées : `Sr2Assets`, résultat actuel de `resolve_species_source`, candidats de
`collect_species_meshes`, squelette de `collect_bone_tree`. Le générateur de
TypeTrees local doit déjà être chargé ; le helper existant
`collect_source_appearance` initialise ce prérequis. Aucun export propriétaire
n'est nécessaire pour appeler ce lecteur.

Le contrat est `extraction_scope = "appearance-lod0-mesh-only-v1"`,
`coverage = "partial"`. `source_particles_ported`,
`source_native_behaviour_ported`, `source_animation_ported`,
`source_shader_parity_verified` et `source_visual_parity_verified` valent tous
`false`, y compris lorsque la lecture des meshes réussit.

Le cache canonique Sloomber est **absent** dans la recherche fournie, pas
« invalide ». Cette tranche ne le crée pas et ne prétend pas produire un cache
complete-source ou un Sloomber complet.

## Lecture et résultat

La sélection suit les PPtrs locaux : apparence → `_structures` → `Element` →
`Prefabs[0]` → `SlimeAppearanceObject` → `GameObject` → renderer. Les noms de
meshes ne servent jamais à découvrir le nuage ou à choisir les matériaux.

- Un `SkinnedMeshRenderer` doit pointer sur un candidat existant, avec cohérence
  de `SupportsFaces` et du rôle face. `resolve_part_binding` conserve l'ordre de
  skin du Mesh, y compris la distinction jiggle-parent / skin-child.
- Un `MeshRenderer` doit avoir un unique `MeshFilter` local et un Mesh sans skin.
  Il devient `role = "attachment"`, jamais un body inventé. `ParentBone` est
  résolu via `applicator.Bones` et les noms uniques du squelette. `RootBone = 0`
  et `AttachedBones = []` sont requis pour cette tranche rigide.
- Les matériaux **effectifs** viennent des `DefaultMaterials` ou de
  `ElementMaterials[0]` lorsque `OverrideDefaults` est vrai. Le matériau générique
  sérialisé du renderer ne remplace pas la déclaration d'apparence. Pour la face,
  seules les passes initiales de l'expression exacte et les `FaceRules[0]` sont
  ajoutées ; aucune expression n'est devinée d'un nom de texture.
- Une structure particle-only est parcourue par ses Transform enfants, avec
  vérification des liens parent/enfant, ownership des composants, unicité et
  budgets. Chaque node doit contenir un unique couple
  `ParticleSystemRenderer` / `ParticleSystem` en mode billboard. Les deux IDs,
  matériaux/shaders, activité, TRS, chemin et raison d'exclusion sont consignés.
  Un renderer inconnu ou une structure mixte n'est **pas** une exclusion
  opportuniste : la lecture refuse explicitement le graphe.

`parts` conserve la forme des candidats (`mesh_obj`, `mesh`, `mesh_name`, `role`)
plus `source_material_slots`, `source_material_ids`, `renderer_kind`,
`geometry`, `appearance_binding` et les IDs de provenance. Les slots contiennent
les objets UnityPy des matériaux ; ce résultat n'est pas directement sérialisable
et n'est pas un manifest de cache. `inventory`, `exclusions` et `structures`
expliquent la couverture. Les MonoBehaviours rencontrés sont inventoriés avec
`ported = false`.

Chaque binding garde le TRS **local exact** lu dans le Transform et ses IDs,
les enums de bones source et, selon le cas, `skin_bones` / `root_bone` ou
`parent_bone` / `source_parent_bone_object_id`. `visual_transform` est séparé.
Aucun vertex n'est aplati approximativement sous un bone ; le consommateur futur
devra appliquer cette hiérarchie. La géométrie utilise le décodeur existant, qui
arrondit ses valeurs à six décimales : ce n'est pas une copie binaire lossless.

### PPtr externe inerte des particules

Toutes les références **du graphe de contenu traversées** (apparence, mesh,
matériau, shader, bone, Transform) doivent être locales avec IDs entiers exacts,
non négatifs ; bool, float, négatif, externe, nul obligatoire ou cible manquante
sont refusés. L'identification de classe utilise séparément le header MonoBehaviour
et son script via UnityPy, comme les helpers existants ; ce n'est pas une
permission de suivre des PPtrs de contenu externes.

Exception metadata-only explicite : le slot `m_Mesh` d'un
`ParticleSystemRenderer` billboard exclu est conservé comme donnée inerte, jamais
déréférencé. Le vrai root Sloomber contient `{m_FileID: 2, m_PathID: 10207}` dans
ce slot inutilisé. Refuser tous les bytes de cette metadata empêcherait
l'inventaire du cas vérifié ; les accepter ne donne aucun support à un mesh
externe actif. Les IDs du slot restent strictement typés/non négatifs, et un
mode mesh-particle non billboard est refusé.

## Bornes et refus

Bornes explicites : 100 000 objets dans le snapshot, 64 structures/candidats,
8 entrées LOD, 64 composants par GameObject, 128 nodes et profondeur 8 par
structure, 512 bones, 16 matériaux/submeshes/channels, 100 000 sommets,
600 000 indices et 32 000 000 octets vertex par mesh.

Avant `MeshHandler`, le lecteur vérifie les comptes et buffers source, les
slices contiguës de submeshes triangles, `baseVertex = 0`, la couverture exacte
index-buffer, et les budgets. Cette tranche accepte uniquement les meshes
inline non compressés à indices 16-bit, vérifiés sur cette source. Mesh compressé,
streaming externe, indices 32-bit, topologie différente, attache non résolue,
Transform imbriqué d'un mesh, renderer inconnu, doublons/cycle, matériau/shader
manquant, TRS non fini/singulier ou metadata malformée sont refusés. Après décodage,
comptes, indices, attributs présents, bindposes et skin weights sont contrôlés.

Ces budgets bornent le travail de ce lecteur **après le chargement UnityPy** ;
ils ne font pas d'UnityPy/TypeTreeGenerator un parseur hostile à mémoire bornée.
Le résultat vérifié concerne ce LAB local, pas un support universel de tous les
prefabs Unity ou de toutes les versions SR2.

## Preuve LAB réelle, offline et read-only

Source : `Lab/Games/SR2/SlimeRancher2_Data`,
Unity `2021.3.28f1`, snapshot 44 019 objets, squelette 38 bones. UnityPy `1.25.4`
et TypeTreeGeneratorAPI `0.0.10`, environnement uv existant en mode `--offline`.
Aucun jeu, Unity Editor, rendu GPU, export cache, texture ou animation n'a été
lancé/produit. La vraie géométrie a été décodée en mémoire puis seuls comptes,
bindings et provenance ont été écrits au scratch.

| Mesh source | Renderer | Rôle | Sommets | Indices |
|---|---|---|---:|---:|
| 1950 | SkinnedMeshRenderer 29422 | body | 288 | 1296 |
| 1884 | SkinnedMeshRenderer 29405 | face | 73 | 360 |
| 1747 | MeshRenderer 26029 + MeshFilter 27447 | attachment | 1086 | 6144 |
| **Total vérifié** | **3 meshes** | | **1447** | **7800** |

Nuage : SAO 37060, GameObject 8107, Transform 18909, `ParentBone = 3` →
`bone_slime`, GameObject bone 10498. TRS conservé : position zéro, quaternion
identité, scale `[1, 1.100000023841858, 1]`. Matériau effectif 531,
shader 2045 `SR/FX/Smoke Sloomber` : **provenance seulement**, aucune adaptation
SR1, aucun port FX, shader ou comportement yawn.

Exclusions exactes :

- `FX_SloomberCloudPuff` : renderer 30200 / ParticleSystem 29701,
  material 1152 / shader 2045.
- `FX_SloomberCloudPuff/Stars_Big` : renderer 30166 / ParticleSystem 29845,
  material 1104 / shader 2078.

Face : expression source 15, passes effectives 494 / 405 / 420 dans l'ordre.
Body et face conservent les six skin bones résolus par le helper strict existant.

`collect_declared_parts` refuse encore réellement Sloomber **avant et après**
ce lecteur avec `appearance LOD0 has no unique skinned renderer`. Les trois
modules partagés (`sr2_asset_extract.py`, `sr2_appearance.py`,
`sr2_source_cache.py`) restent identiques à HEAD ; aucun assouplissement de
`complete_source` ni intégration automatique.

SHA-256 réels, taille et mtime avant/après identiques pour les deux fichiers
source inspectés :

- `resources.assets` : `c0341bb3980e7a8de50c7df548d7f7c5bfa8ee00b47c4398e0af0665cf7a9d59`
- `resources.assets.resS` : `725fba9c705dd8b3d241675c090a46ccf62ff516bcc55aa2a4a09781ee51f88e`

## Tests et reproduction

Fixtures synthétiques, aucun vertex/index/pixel propriétaire dans Git. Le graphe
source est réellement parcouru ; seul le parseur de géométrie binaire Unity est
doublé pour ces fixtures. Cycles RED→GREEN enregistrés : absence du lecteur,
inventaire particle/unknown renderer, matériaux/bindings/refus de graphes,
préflight avant décodeur et attributs géométriques. Résultat final : **108 tests
nouveaux passent** ; la régression des six modules SR2 passe **251 tests**.

Depuis le worktree :

```bash
PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest \
 tests/content/test_sr2_mesh_scope.py \
 tests/content/test_sr2_source_cache.py tests/content/test_sr2_appearance.py \
 tests/content/test_sr2_source_skin_binding.py tests/content/test_sr2_source_bones.py \
 tests/content/test_sr2_asset_extract.py -q -p no:cacheprovider
```

Le probe LAB a été exécuté avec :

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.12 \
 --with UnityPy==1.25.4 --with TypeTreeGeneratorAPI==0.0.10 --with numpy --with Pillow \
 python -c '
from pathlib import Path
import json
from bridge.content.sr2_asset_extract import Sr2Assets, collect_species_meshes, collect_bone_tree
from bridge.content.sr2_appearance import collect_source_appearance
from bridge.content.sr2_source_cache import resolve_species_source, collect_declared_parts
from bridge.content.sr2_mesh_scope import collect_appearance_mesh_scope
assets = Sr2Assets(Path("Lab/Games/SR2/SlimeRancher2_Data"))
candidates = collect_species_meshes(assets, "Sloomber")
skeleton = collect_bone_tree(assets, "Sloomber")
collect_source_appearance(assets, "Sloomber", candidates, skeleton)
resolved = resolve_species_source(assets, "Sloomber")
result = collect_appearance_mesh_scope(assets, resolved, candidates, skeleton)
totals = {"meshes": len(result["parts"]),
          "vertices": sum(p["vertex_count"] for p in result["parts"]),
          "indices": sum(p["index_count"] for p in result["parts"])}
assert totals == {"meshes": 3, "vertices": 1447, "indices": 7800}
try:
    collect_declared_parts(assets, resolved, candidates)
except ValueError as ex:
    assert str(ex) == "appearance LOD0 has no unique skinned renderer"
else:
    raise AssertionError("strict complete-source path unexpectedly accepted Sloomber")
print(json.dumps({"scope": result["extraction_scope"], "totals": totals,
                  "exclusions": [(r["source_renderer_id"], r["source_particle_system_id"])
                                 for r in result["exclusions"]]}))
'
```

Placer `TEMP`, `TMP` et `TMPDIR` dans un scratch privé avant cette commande.
Ce probe ne publie que des compteurs et des IDs. Ne jamais sérialiser les champs
`mesh_obj`, `mesh`, `geometry` ou `source_material_slots` dans un artefact public.

Preuves de cette exécution :
a local scratch folder
contient `red-01.log` à `red-04.log`, `green-01.log`, `green-02.log`,
`green-03-attempt.log`, `green-04.log`, `sr2-regression.log`,
`lab-final.log` et `lab-final-result.json` (metadata-only, avec module importé,
hash du lecteur et hashes source avant/après).

Worktree distinct vérifié : `.worktrees/sloomber-mesh-only-v1`, branche
`bridge/sloomber-mesh-only-v1`, HEAD
`c6e55e99cb19afe57dc43d0be1cf9434ff0178e7`. Seuls ce module, ses tests et cette doc
sont des deltas candidats. Revue indépendante/intégration par le parent restant
nécessaires ; aucun commit/push ni validation Unity/GPU dans cette tranche.

## Wiring runtime opt-in (`--appearance-mesh-only`, tranche `sloomber-runtime-v1`)

Le lecteur ci-dessus reste non-publicateur ; l'extracteur sait désormais produire un cache
`bridge-native-mesh` v2 réel depuis ce scope, en mode opt-in strictement séparé du
complete-source (le refus strict n'est jamais affaibli, le cache canonique jamais une cible) :

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.12 \
 --with UnityPy==1.25.4 --with TypeTreeGeneratorAPI==0.0.10 --with numpy --with Pillow \
 python -m bridge.content.sr2_asset_extract --species Sloomber --canonical slime.sloomber \
 --sr2-dir <SlimeRancher2_Data> --out <scratch>/extraction/slime.sloomber \
 --allow-nonlab --appearance-mesh-only
```

Contrat du mode :

- Mutuellement exclusif avec `--complete-source` ; exige un `--out` neuf explicite et aucune
  sélection de clip. Fraîcheur : `sr2_icon._fresh_private_output` réutilisée dans l'arbre
  `Lab/`, garde externe équivalente `_fresh_external_output` (refus `sr2-to-sr1`, reparse,
  existant) pour `--allow-nonlab`.
- Corps/face → rangées `skinned_renderers` (matériaux effectifs par PPtr
  `source_material_slots`, jamais de guessing par nom ; face via le producteur 3 passes
  existant). Nuage → rangée `static_renderers` : `path = <chemin osseux du parent>/attachment_<mesh>`
  (`bone_root/bone_slime/...` — le loader retrouve les transforms du squelette par nom via
  `EnsurePath`), TRS authored, `parent_bone` conservé, aucune donnée de skin.
- `model.json` porte `source_extraction_scope: appearance-lod0-mesh-only-v1` (clé ignorée
  par le loader SR1) ; particules et animation NON portées (coverage `partial`).

Preuves d'exécution (scratch `sloomber-runtime-impl-v1/`) : `red_run.log` (RED, 12 échecs),
`green_run3.log` (539 passed, 3 skipped), `extraction_run2.log` (extraction réelle, exit 0),
`validation_out.log` + `validation_report.json` (42 contrôles, 0 échec). Cache : 3 meshes /
1447 vertices / 7800 indices, manifest auto-cohérent (sha256 par fichier), nuage attaché à
`bone_slime` (scale Y `1.100000023841858`). Manques loader SR1 pour ce cache
(`parent_bone` non consommé, rebind natif du nuage sur Slime/Body, masques
`_SloomberStarMask`/`_SloomberColorOverlay` non mappés) : liste précise fichier:ligne dans
`RAPPORT.md` du même scratch.

## Contrat loader implémenté (tranche `sloomber-runtime-loader-v1`)

Le loader SR1 consomme désormais le cache mesh-only sans jamais inventer d'attache ni de
matériau. Statut honnête : **mesh-only partiel** — les particules et l'animation FX restent hors
périmètre (`source_particles_ported = false`, les deux `ParticleSystemRenderer` source sont
exclus par conception) et rien ici ne prétend à une parité visuelle source.

- **Rangées `static_renderers`** : le loader consomme `parent_bone` explicitement. Le chemin doit
  se résoudre sur des transforms **déjà existants** (squelette reconstruit ou hiérarchie
  authored) — aucun container n'est plus créé pour ces rangées — et `parent_bone`, quand il est
  déclaré, doit être résolu par `FindChildByName` **et** désigner le même transform que le parent
  du chemin. Toute divergence refuse la rangée (fail-closed par rangée, problème remonté ; le
  document entier retombe alors sur le placeholder). Les rangées historiques sans `parent_bone`
  (batty, twin) gardent l'attache actuelle : elles ne bénéficient que de l'interdiction de
  création silencieuse.
- **Rebind natif** : `ImportedPartColors` transporte `role` et le shader source de la rangée
  (champs `[SerializeField]`, donc présents sur le clone `Instantiate`). Le rebind palette corps
  (`_TopColor`/`_MiddleColor`/`_BottomColor`/`_StripeTexture`) est **sauté** pour toute partie
  dont le shader source est déclaré hors des familles `SR/AMP/Slime/...` : le nuage Sloomber
  (`SR/FX/Smoke Sloomber`, role `attachment`) garde son matériau convertisseur et n'est plus
  peint comme un blob corps. Un shader inconnu (caches héritées sans clé `shader`) garde le
  comportement historique ; body/face et accessoires des espèces existantes (ringtail, batty,
  twin) restent inchangés.
- **Squelette** : un os dont `parent` vaut `>= i` (index non construit), `< -1`, ou pointe vers
  une ligne ignorée refuse le document entier, au lieu d'un rattachement silencieux à la racine.
- **Diagnostics LAB rest-pose (lecture seule)** : chaque renderer expose `source_role`,
  `source_shader` et `native_palette_rebind_skipped`, et l'évidence de rebind compte les parties
  sautées (`rebind_skipped_parts`) séparément des corps rebindés.

Vérification : contrats statiques + harness CPU lié à la production
(`tests/test_imported_sloomber_parts.py`, `tests/ImportedSloomberPartsHarness`) et lecture
read-only du cache réel ; aucune preuve Unity/GPU — l'A/B runtime (nuage non repeint, attache
au bone animé) reste à faire au LAB.
