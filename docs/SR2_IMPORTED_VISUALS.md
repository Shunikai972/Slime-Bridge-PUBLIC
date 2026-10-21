# Visuels importés SR2 — jalon géométrie

## Niveau de preuve
- **COMPILED_INTEROP_SIGNATURES** : Release net6.0 contre les wrappers du LAB.
- **PURE_LOGIC_TESTED** : parseur strict, cache SHA256, courbes et politiques.
- **MANAGED_DOUBLES_TESTED** : le vrai loader C# est exercé avec doubles Unity.
- Harnesses : `Sr2ImportedMeshHarness` 125, `Sr2ImportedRuntimeHarness` 23,
  `Sr2ImportedLifecycleHarness` 18 : **166 contrôles, aucun échec**.
- **NEEDS_LOCAL_RUNTIME_PROOF** : aucun jeu lancé par cette piste ; pas de parité
  de shader, de graphe Animator, de comportement ou d'effet radioactif revendiquée.

## Cache et sécurité
`SLIMEBRIDGE_IMPORT_CACHE_DIR` est LAB-only ; le registrar doit déjà être autorisé
par l'oracle pour le binaire LAB exact. Dossier absolu existant strictement sous
`<racine détectée>/Lab/RuntimeArtifacts`, sans reparse point sur aucun ancêtre
(`LabSpecPathGuard`). Un cache dans le Lab d'un autre worktree doit être publié
par l'opérateur sous cette racine avant le run ; la variable ne contourne pas la garde.
`cache-manifest.json` v1 doit lier `canonical_id` et SHA256 du modèle et de chaque
texture utilisée. Les mêmes octets vérifiés sont consommés, pas une seconde lecture.
Limites : modèle 16 Mio, manifeste 1 Mio, texture 16 Mio, textures total 64 Mio ;
limites géométriques et animation dans `NativeMeshParser`. Aucun asset committé.
Cache absent/refusé/autre canonical : placeholder inchangé. Un défaut de construction
conserve/restaure les rendus natifs ; aucun shader générique ni maillage inventé.

## Cycle de vie et matériaux
Le registrar associe **la définition native importée** au cache et retargete seulement
son clone d'applicator. Il ne cache pas les rendus du prefab : chaque acteur construit
ses propres Mesh/Material/AnimationClip, détruits par son marqueur propriétaire.
Postfix Harmony `SlimeAppearanceApplicator.Awake`, `Initialize(bool)`,
`ApplyAppearance()` : premier acteur, relâchement Vacpack, reconstruction/région.
Postfix `SetExpression(SlimeExpression)` : supprime les passes natives recréées.
Prefixes `RecalculateBoundsHelper.RecalculateBounds(parent)` et `Update()` :
empêchent le helper natif de réécrire les bounds **uniquement après** application
réussie. Les espèces natives sont des no-op stricts (identité par pointeur).
Réapplication idempotente, racine privée hors des objets d'apparence natifs, LOD natif
inhibé seulement au commit ; échec de clip/matériau restaure les états originaux.
Matériaux clonés dans les familles natives corps/yeux/bouche de l'acteur ; toutes
les passes source conservées, palette et gloss transférés si propriétés disponibles.
`MaterialPropertyBlock` par slot + readback de la palette. Atlas visage obligatoire ;
texture obligatoire non portable = refus fermé. Propriétés d'effet inconnues
(ex. `_Noise`, `_Drips`) ignorées et listées au reçu, jamais synthétisées.
Parties `role=source-effect` ignorées : aura/core radioactifs hors jalon.
Animation legacy sur la seule hiérarchie source : **seul `default_clip` déclaré**
reconstruit par samples linéaires, fermeture de boucle et continuité quaternion.
La clé terminale est fermée aussi quand son temps float égale la durée ; aucun
sample de temps dupliqué n'est créé. Ce bord de boucle legacy remplace la valeur
terminale par la pose initiale alignée, pas les échantillons intermédiaires ni le cache.
Une passe supplémentaire réutilise le dernier sous-maillage (manuel Unity 2021.3
Skinned Mesh Renderer) : Rad conserve donc 1 sous-maillage et 3 matériaux.
https://docs.unity3d.com/2021.3/Documentation/Manual/class-SkinnedMeshRenderer.html
Aucune connexion à Animator, aucune sélection d'animation de gameplay devinée.

## Signatures vérifiées dans les wrappers (ilspycmd, pas des corps natifs)
- `Mesh.SetVertices/SetNormals(Il2CppStructArray<Vector3>)` ;
  `SetUVs(int, Il2CppStructArray<Vector2>)` ;
  `SetTriangles(Il2CppStructArray<int>, int, bool)` ; `subMeshCount`, `indexFormat`.
- `Mesh.bindposes` = `Il2CppStructArray<Matrix4x4>` ;
  `boneWeights` = `Il2CppStructArray<BoneWeight>` ; `tangents`, `colors` arrays struct.
- `SkinnedMeshRenderer.bones` = `Il2CppReferenceArray<Transform>` ;
  `rootBone`, `sharedMesh`, `updateWhenOffscreen` ; bones et weights relus après set.
- `Material(Material)`, `HasProperty`, `SetColor/Float/Texture/TextureScale/Offset` ;
  `Renderer.sharedMaterials` array reference, `Set/GetPropertyBlock(block,int)`.
- `Texture2D(int,int,TextureFormat,bool,bool)` ;
  `ImageConversion.LoadImage(Texture2D,Il2CppStructArray<byte>,bool)`.
- `AnimationClip.SetCurve(string,Il2CppSystem.Type,string,AnimationCurve)` ;
  `legacy`, `frameRate`, `wrapMode`, `EnsureQuaternionContinuity()`.
- `AnimationCurve(Il2CppStructArray<Keyframe>)`, `Keyframe(float,float,float,float)` ;
  `Animation.AddClip(clip,string)`, `clip`, `Play(string,PlayMode)`, `isPlaying`.
- `ClassInjector.RegisterTypeInIl2Cpp<T>()`, `IsTypeRegisteredInIl2Cpp<T>()` ;
  `GameObject.AddComponent<T>()`, TRS locaux et `Transform.SetParent(Transform,bool)`.
Référence `UnityEngine.AnimationModule.dll` ajoutée au csproj ; module image déjà présent.
Build : `dotnet build mods/sr2-bridge/SlimeBridge.SR2.csproj -c Release --no-restore
-p:SR2LabPath=Lab/Games/SR2` (0 erreur).
Hashes des wrappers consultés (préfixes SHA256) : Assembly-CSharp `bd531a73dd25`,
CoreModule `7d3a7ab4f8a7`, AnimationModule `0f3cfc79d08b`, ImageConversion `450638bcd54f`.

## Recette LAB protégée (à exécuter uniquement par l'orchestrateur parent)
1. Déployer la DLL Release via le mécanisme protégé habituel, publier le cache Rad
   valide sous le `Lab/RuntimeArtifacts` de la racine du binaire SR2 LAB.
2. Fournir les gardes habituelles `SLIMEBRIDGE_LAB_EXE`, `SLIMEBRIDGE_IMPORT_SPEC`,
   `SLIMEBRIDGE_IMPORT_OUT`, `SLIMEBRIDGE_IMPORT_RUN` et `SLIMEBRIDGE_IMPORT_CACHE_DIR`.
3. `SLIMEBRIDGE_IMPORT_OPS` = JSON exact :
   `{"ops":["spawn:slime.rad","query:slime.rad","vac_add:slime.rad:1","release:slime.rad:1","query:slime.rad","save_roundtrip:slime.rad","query:slime.rad"]}`.
4. Au reçu `bridge-imported-ops-receipt`, lire `spawn.details.imported_visual`, puis
   `query.details.imported_visuals` (lecture seule, **ne répare pas** le visuel).
   Exiger `applied`, `active_in_hierarchy`, palette > 0, `UnityEngine.SkinnedMeshRenderer`,
   239 sommets, 6 `bound_bones`/`bone_slots`, 239 weights, rootBone non vide,
   `default_clip=Idle`, `animation_playing=true`. Vérifier trois passes corps/yeux/bouche.
5. Après vraie transition/reload et vrai redémarrage protégé : query seul + captures.
   Les ops d'un même run ne prouvent pas un reload ni un cycle Vacpack spontané.

## Preuves acquises — Rad natif SR2 (2026-10-09)
Runs LAB protégés `rad-native-visual-fix-34cd2797348c` et `fa21feab96f1` (v4/v5, `passed=true`,
cleanup/sources/saves conformes) :
- spawn/query/release/save_roundtrip : SkinnedMeshRenderer 239 sommets, 239 bone weights, 6 bones
  liés, rootBone `bone_skin_top`, `default_clip=Idle`, `animation_playing=true`, palette vérifiée.
- matériaux liés : `SR/AMP/Slime/Body`, `SR/AMP/Slime/Face/Eyes Glow`, `SR/AMP/Slime/Face/Mouth Default`
  (trois familles sur un sous-maillage, passe multipass Unity 2021.3).
- complétion des familles manquantes : le spawn complète yeux/bouche depuis l'apparence template de
  l'espèce native (DefaultMaterials, ElementMaterials, prefabs d'élément, `SlimeFace.ExpressionFaces`) ;
  diagnostic receipt `native_materials_before_drive` (2, env seuls) vs `native_materials` (5, dont
  `eyeHappy__SR2Imported`, `mouthHappy__SR2Imported`). Fusion `7ff953f` ; revues indépendantes
  sr2-spawn-template-materials-review-v1/v2 APPROVE (le chemin template est couvert par le harness,
  22 contrôles).
- Limites : la capture v6 (`rad-native-visual-fix-5bc460f3b413`, après relèvement du cadrage de spawn
  `fe83e82`) montre l'acteur importé comme un blob vert lisse et lumineux au bord bas-gauche (corps
  visible, face non identifiable dans ce cadrage ; les passes yeux/bouche restent attestées par le
  readback du receipt). Le delta de position v5→v6 (fwd×0.5 + up×0.6, joueur identique) prouve que le
  binaire du run exécutait bien le nouveau code ; l'opérateur restaure le fichier déployé après le run
  (rollback_verified), donc le fichier Mods actuel n'est pas la DLL du run.

## Hypothèses ouvertes
Activation du clone SR2, ordre réel Awake/Initialize/ApplyAppearance, construction
précoce des matériaux et maintenance des bounds restent à vérifier dans le LAB.
Le jeu peut ne pas accepter `SetCurve` legacy sur cette version IL2CPP : alors refus
fermé, placeholder natif ; compilation et doubles ne prouvent pas cette portabilité.
Les shaders SR2 doivent offrir les familles de face attendues ; atlas/UV et palette
requièrent inspection visuelle. Jiggle procédural et transitions de gameplay absents.
