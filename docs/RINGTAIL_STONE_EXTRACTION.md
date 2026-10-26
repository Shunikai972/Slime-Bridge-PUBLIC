# SR2 : extraction opt-in de la forme statue Ringtail, `bridge-stone-form` v1

## Frontière de cette tranche

`bridge.content.sr2_stone_form.collect_stone_form(assets, appearance_id=… |
stone_form_id=… | statue_prefab_id=…)` est un lecteur auxiliaire **non branché** à
`extract_species`, au cache canonique Sloomber, au convertisseur ou au runtime. Il
ne crée aucun fichier. Sa sortie est une dataclass gelée `StoneForm` sérialisable
en JSON pur (`to_dict()`), sans objet UnityPy ; le schéma est `bridge-stone-form`
version 1.

Le seul point d'écriture est `export_stone_form(assets, form, out_dir)`, et
uniquement vers un dossier **neuf** fourni par l'appelant : refus si le chemin
existe déjà (dossier vide compris), refus de lien/reparse point, jamais
d'écrasement. Il produit les PNG albédo/normal via le `TextureExporter` existant,
`stone-form.json` et un reçu minimal. Cette tranche ne réalise **aucune**
adaptation de shader, conversion SR1, ni parité Unity/GPU/visuelle.

Prérequis : `assets` expose `_objects`, `parse_monobehaviour_head` et
`read_typetree` (le `Sr2Assets` du dépôt convient), et le générateur de TypeTrees
local doit être chargé sur `assets.env` — `env.typetree_generator` construit depuis
le dossier Cpp2IL `cpp2il_out` du jeu, la disposition qui fonctionne pour ces
classes (prouvé par les sondes existantes ; le dossier interop `Il2CppAssemblies`
échoue sur ces mêmes classes). Sans générateur, les MonoBehaviours ne sont pas
lisibles et le module refuse.

## Résolution et lecture

Trois entrées **exclusives** (exactement une, entier positif strict) :

- `appearance_id` : `SlimeAppearance` → `_stoneFormAppearance` →
  `StoneFormAppearance` ; puis `StoneFormPrefab` → composant `RingtailStatue` ;
- `stone_form_id` : `StoneFormAppearance` (ScriptableObject) → `StoneFormPrefab` ;
- `statue_prefab_id` : composant `RingtailStatue` directement (ids du rapport
  frozen `ringtail-petrif-source-v1`).

Chaque classe est vérifiée via le header MonoBehaviour → `m_Script` →
`m_ClassName` (jamais via un nom d'objet). Le prefab résout `m_GameObject` → GO
racine dont le Transform doit être racine (`m_Father = 0`). Composants autorisés
sur la racine : Transform, LODGroup, colliders, Rigidbody, MonoBehaviours ; tout
composant inconnu est refusé. Le **LODGroup est l'autorité LOD** : exactement 3
niveaux (`m_LODs`), exactement 1 renderer `MeshRenderer` chacun, distincts ; les
noms d'objets ne décident jamais d'un index LOD.

Le sous-arbre est parcouru depuis le Transform racine avec liens réciproques
(parent/enfant), propriété des composants, absence de cycle, budgets nœuds/
profondeur/enfants. Les nœuds porteurs de mesh doivent être les **enfants directs**
du root et correspondre **exactement** aux 3 renderers du LODGroup ; chaque LOD
doit déclarer un unique Transform + MeshFilter + MeshRenderer, sans enfant. Les
enfants sans mesh (le `DelaunchTrigger` réel, inactif) restent autorisés avec
Transform + collider + MonoBehaviour uniquement.

Avant tout décodage, chaque mesh LOD passe un préflight source : non compressé,
indices 16-bit, sous-mesh unique en liste de triangles couvrant exactement le
buffer d'index (`firstByte`/`firstVertex`/`baseVertex` nuls), pas de
bindpose/bones, budgets ; le streaming n'est accepté que comme sidecar canonique
`<fichier>.assets.resS` du même dossier, avec tranche cohérente (offset ≥ 0,
taille dans le budget, stride 4–64 octets). Le décodeur existant
(`sr2_asset_extract.decode_mesh`) est ensuite appelé, puis sa sortie est
re-validée : longueurs de canaux (positions obligatoires, normales/tangentes/uv
optionnelles), finitude, index entiers bornés, comptes identiques au header
source, pas de skin décodé.

Le matériau effectif est **unique et partagé** par les 3 LOD. Sous-ensemble
documenté v1 (les autres propriétés du matériau sont ignorées, jamais devinées) :

- floats : `_Gloss`, `_GlossPower`, `_Outline`, `_RimPower`, `_RimStrength`,
  `_Shininess` ;
- couleurs (RGBA) : `_TopColor`, `_MiddleColor`, `_BottomColor`,
  `_RedTopColor/_RedMiddleColor/_RedBottomColor`,
  `_GreenTopColor/_GreenMiddleColor/_GreenBottomColor`,
  `_BlueTopColor/_BlueMiddleColor/_BlueBottomColor` ;
- nom, `m_CustomRenderQueue`, shader source (`m_ParsedForm.m_Name` + path_id) et
  les deux textures `_MainTex` / `_Normal` résolues (path_id, nom, dimensions,
  fichier PNG). Tiling/offset non identitaires refusés ; tout slot texture
  supplémentaire non nul refusé ; slots nuls tolérés.

Sortie `to_dict()` : `schema`, `version`, `entry`, `provenance` (appearance/
stone_form/statue/GO racine/Transform/LODGroup/renderers/meshes/matériau),
`root` (nom, TRS local exact, inventaire des composants), `meshes[3]` (lod,
path_id, nom, GO/Transform/MeshFilter/MeshRenderer, `renderer_enabled`, TRS local
exact, `vertices`/`normals`/`tangents`/`uv`/`triangles` + comptes), `material`
(sous-ensemble ci-dessus, textures avec `file: null` avant export).

## Bornes et refus

Budgets v1 explicites (constantes du module, abaissables en test) : 100 000 objets
du snapshot, 64 composants par GameObject, 16 enfants par Transform, 32 nœuds et
profondeur 4 du sous-arbre, 3 LOD, 2 048 sommets et 4 096 triangles par mesh,
16 canaux, 131 072 octets vertex, 4 sous-meshes, 512 entrées de propriété, 8 192
px par texture, chemin de sidecar ≤ 128.

Refus fail-closed : entrée absente, double ou non entier positif ; référence
externe (`m_FileID ≠ 0`), négative, booléenne/flottante/chaîne, non résolue, ou
nulle lorsqu'obligatoire ; classe attendue absente ; composant non possédé par son
GameObject ; doublons (composant, enfant, renderer, GameObject, mesh, texture,
propriété) ; LOD manquant ou surnuméraire ; mesh compressé, 32-bit, multi-slice
incohérent, streamed hors sidecar canonique, avec bindpose/bones ou hors budget ;
géométrie décodée incohérente (longueurs, NaN/Inf, index hors bornes, non-triangle,
skin) ; TRS non fini, quaternion non unitaire, scale ≤ 0 ; texture requise
manquante/externe, slot supplémentaire non nul ; sous-ensemble matériau incomplet ;
export vers un dossier existant.

## Preuve LAB réelle, offline et read-only

Source : `Lab/Games/SR2/SlimeRancher2_Data`, Unity `2021.3.28f1`, snapshot
44 019 objets, générateur TypeTrees depuis le dossier Cpp2IL `cpp2il_out`.
Environnement uv existant en `--offline` (UnityPy `1.25.4`, TypeTreeGeneratorAPI
`0.0.10`). Aucun jeu, aucun éditeur, aucun rendu. Seuls des compteurs, hashes et
fichiers de scratch ont été écrits ; la source LAB est intacte (voir plus bas).

Chaîne résolue par le module (entrée `appearance_id=31303`) :

`RingtailDefault 31303` → `_stoneFormAppearance` `StoneFormDefaultBase 31283` →
`StoneFormPrefab` `RingtailStatue 42591` → GO `4430 ringtailStatueSmall_v1`
(Transform `15633`) → LODGroup `30337` → renderers `25633 / 25638 / 25749` →
meshes `1903 / 1764 / 1890` → matériau `563`.

| LOD | Mesh | Sommets | Indices | Triangles | TRS local |
|---|---|---:|---:|---:|---|
| 0 | 1903 `LF_ringtail_stone_LOD0` | 503 | 2454 | 818 | pos `(0,-0.5,0)`, rot identité, scale `1` |
| 1 | 1764 `LF_ringtail_stone_LOD1` | 327 | 1470 | 490 | idem |
| 2 | 1890 `LF_ringtail_stone_LOD2` | 197 | 825 | 275 | idem |
| **Total** | **3 meshes** | **1027** | **4749** | **1583** | |

Cohérent avec le rapport frozen (`ringtail-petrif-source-v1`) : 503/327/197
sommets, 2454 indices LOD0 (1470 et 825 pour LOD1/LOD2). TRS racine `(0,0,0)`,
identité, scale `1` ; `renderer_enabled` vrai sur les trois.

Matériau `563 slimeRingtailStoneForm`, shader `2009` =
`SR/AMP/Paintlight/StaticUV/Base NormalMap` (chemin de provenance seulement),
`m_CustomRenderQueue = 2225`, floats vérifiés : `_Gloss 0.3000000119`,
`_GlossPower 0.4000000060`, `_Outline 0.0049999999`, `_RimPower 2.375`,
`_RimStrength 0.2399999946`, `_Shininess 0.5954557061` ; les 12 couleurs du
sous-ensemble sont présentes et finies.

Textures exportées (PNG réels encodés par le `TextureExporter` existant, tiling/
offset identité) :

| Slot | path_id | Nom | Dimensions | PNG (octets) | SHA-256 |
|---|---|---|---:|---:|---|
| `_MainTex` | 1483 | `alb_ringtail_statue` | 1024×1024 RGBA | 1 029 022 | `5f35de7adb35b14c2405430f6ca9dd32fba7a0275690bc81bda1874bdd5cb85b` |
| `_Normal` | 1607 | `nrm_ringtail_statue` | 256×256 RGBA | 59 589 | `bacd001b71541ccb6c7f0168f9a584c45faa17bae265d74debb70815b70e6c0a` |

Reçu minimal (`stone-form-receipt.json`) : schéma, version, `source_read_only:
true`, liste des fichiers avec taille + SHA-256, comptes (3 meshes, 1027 sommets,
1583 triangles). `stone-form.json` : 308 785 octets, SHA-256
`de0240ba2afda52eaa129b31233736ea028b964bc437466044ecf340a733f4d6`.

La géométrie du module a été croisée avec un décodage **indépendant** des mêmes
meshes (mêmes tableaux exacts pour les 3 LOD, comptes de canaux inclus) et
empreinte SHA-256 canonique par LOD (`[vertices, normals, tangents, uv,
triangles]`) : LOD0 `6d6c38f94f8ed8a6f01b9d81cfd1a2489389050ceb3d85d16f6d0800db04b34b`,
LOD1 `fec155d913289c244fbc1b7905b6ec8fce7ec04eb309605ff6fd30cc54d70477`,
LOD2 `043db861a9e6f548c4bb673067593228a6d8b2f0a51fc541dd61ba49b0b83efb`.

Contrôle secondaire : `statue_prefab_id=42590` (prefab largo) résout
`ringtailStatueLarge` à l'échelle 2, mêmes meshes 1903/1764/1890 — le contrat
couvre aussi la variante largo par les mêmes règles.

État source avant/après identique (taille, `mtime_ns`, SHA-256 partiels premier +
dernier 16 Mo), SHA-256 complets de référence : `resources.assets`
`c0341bb3980e7a8de50c7df548d7f7c5bfa8ee00b47c4398e0af0665cf7a9d59` (27 626 004
octets) et `resources.assets.resS`
`725fba9c705dd8b3d241675c090a46ccf62ff516bcc55aa2a4a09781ee51f88e` (329 387 760
octets) — identiques aux valeurs du rapport mesh-scope antérieur, aucun octet
touché par cette tranche.

Preuves de cette exécution :
a local scratch folder
contient `real-run-01.py`, `real-run-01.log`, `run-report.json` (metadata-only) et
`extraction/stone-form-01/` (dossier neuf : 2 PNG + `stone-form.json` +
`stone-form-receipt.json`). Sondes de contrat préalables : `probe-a-contract.py`,
`probe-b-mb.py`, `probe-c-inventory.py` (+ logs et JSON) dans le même dossier.

## Tests et reproduction

Fixtures synthétiques : aucun vertex/index/pixel propriétaire dans Git. Le graphe
source est réellement parcouru (objets factices avec `read_typetree`/header MB) ;
seul le décodeur binaire de géométrie est doublé, comme dans les tests existants.
Cycle RED→GREEN : exécution initiale **141 échecs** (lecteur absent), puis
**141 passent** (`pytest-green-05.log`). Couverture : happy path 3 LOD (matériau,
textures, TRS, provenance, JSON), trois entrées exclusives, classes inattendues,
LOD manquant/dupliqué/mal référencé, mesh compressé/32-bit/slices/streaming,
trésor de refus PPtr (externe, négatif, bool, float, chaîne, nul requis),
doublons, TRS singuliers, budgets (constantes monkeypatchées), canaux et
géométrie décodée invalides (y compris refus **avant** décodeur), sous-ensemble
matériau, textures, et export (dossier neuf uniquement, reçu, non-mutation).

Depuis le worktree :

```bash
PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest \
  tests/content/test_sr2_stone_form.py -q -p no:cacheprovider        # 141 passent
```

Régression ciblée (fichier + `test_sr2_source_cache.py`, `test_sr2_appearance.py`,
`test_sr2_source_skin_binding.py`, `test_sr2_source_bones.py`,
`test_sr2_asset_extract.py`) : **294 passent**. Suite complète du worktree : 1 581
passent, 15 ignorés ; 3 échecs **préexistants hors périmètre** — des builds C#
`dotnet` d'autres modules (`tests/app/test_conversion_ui.py`,
`tests/test_sr2_background_intro.py`, `tests/test_sr2_world_readiness.py`,
symboles `_focus*` absents) sans aucun lien avec ce module Python.

Probe LAB réel (read-only) :

```bash
cd <worktree> && PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.12 \
  --with UnityPy==1.25.4 --with TypeTreeGeneratorAPI==0.0.10 \
  python <scratch>/ringtail-stone-extract-impl-v1/real-run-01.py
```

`TEMP`, `TMP` et `TMPDIR` doivent pointer vers un scratch privé avant la commande.
Le probe n'écrit que dans le scratch ; il ne publie que des identifiants, comptes
et hashes.

## Limites explicites

- **Aucune adaptation shader** : le shader source `SR/AMP/Paintlight/StaticUV/Base
  NormalMap` (Paintlight/StaticUV, absent de SR1) est conservé en provenance
  seulement ; la conversion mesh + matériaux vers SR1 est une **étape séparée**,
  non commencée ici. Aucune parité visuelle, Unity ou GPU n'est revendiquée.
- Le LODGroup est requis et exactement 3 LOD : un prefab statue sans LODGroup ou
  avec un autre découpage serait refusé, pas adapté.
- Le streaming n'est supporté que pour le sidecar canonique `<X>.assets.resS` du
  même dossier ; un mesh pointant vers un autre SerializedFile est refusé.
- Tiling/offset de textures identitaires uniquement ; les PNG exportés sont les
  textures source décodées par l'outil existant (pas de dépaquetage de masques).
- La géométrie passe par le décodeur existant (arrondi 6 décimales) : ce n'est pas
  une copie binaire lossless des buffers source.
- Contrat vérifié sur ce LAB local (2021.3.28f1) : ce n'est pas un support
  universel de tous les prefabs Unity ou de toutes les versions SR2.
- Aucun commit/push. Worktree `.worktrees/ringtail-stone-extract-v1`, branche
  `bridge/ringtail-stone-extract-v1`, HEAD/base
  `0df3b806639ad3ac1423697218630f4e0664ad48`. Seuls ce module, ses tests et cette
  doc sont des deltas candidats ; les modules existants (`sr2_asset_extract.py`,
  `sr2_appearance.py`, `extract_species`, cache canonique) restent identiques à
  HEAD — la revue et l'intégration restent au parent.
