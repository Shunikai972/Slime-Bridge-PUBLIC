# Mod loaders et intégration (SRML / MelonLoader / SlimeBridge)

Les deux jeux n'ont **pas** le même loader ; ni l'un ni l'autre ne définit de format d'asset de slime : chaque mod apporte son
propre chargeur. Faits recoupés avec les sources du dépôt (`vendor/upstream`, `docs/RECON.md`) :

| | SR1 | SR2 |
|---|---|---|
| Loader | **SRML** (Slime Rancher Mod Loader, 0.2.1.0) | **MelonLoader** 0.7.x (+ **MelonSRML**) |
| Runtime | Unity 2019.4.29f1, Mono, .NET Framework 4.7.2 | Unity 2021.3.28f1, IL2CPP, mods en net6.0 (Il2CppInterop) |
| Dossier des mods | `SRML/Mods/<mod>/` (`modinfo.json`) | `Mods/` (`[MelonInfo]`, `[MelonGame("MonomiPark","SlimeRancher2")]`) |
| Assets custom | à charger soi-même (AssetBundle ou objets construits à l'exécution) | idem, via les wrappers Il2Cpp (`UnityEngine.AssetBundleModule`, `ImageConversionModule` disponibles) |

SlimeBridge fournit déjà un plugin par jeu (SR1 sous SRML, SR2 sous MelonLoader). Le sens SR2→SR1 est lu par
`ImportedModelLoader` à partir d'un cache `bridge-native-mesh` v2 ; le sens SR1→SR2 n'a pas encore de chargeur côté dépôt.

## Trois façons de consommer ce que produit sbconv

1. **Cache SlimeBridge (`sbconv export`)** — le format que lisent déjà tes plugins. Dossier
   `output/bridge-cache/<SRC>-to-<TGT>/<id>/` à copier dans le dossier d'asset-cache du plugin
   (`Lab/AssetCache/<sens>/<id>/` en LAB). Validé hors-ligne par un port du parseur C# (limites, quaternions unitaires, noms
   d'os uniques, palette de skin, poids, noms de textures, échantillonnage d'animation, hachages) et par le scanner Python du
   dépôt. Contraintes connues du plugin actuel (ton C#) :
   * `canonical_id` doit valider `^slime\.[a-z0-9_.-]{1,80}$` : les largos sont exportés en `slime.largo.a.b`
     (`--largo-id-style native` pour `largo.a.b` si tu étends cette regex) ;
   * l'icône est obligatoire (`bridge-source-sprite` v1) et les espèces doivent être enregistrées (identités, spec d'import) ;
   * le SR1 attend une partie `face` : absente quand la source est SR1 (le visage SR1 passe par les matériaux d'expression).
2. **Paquet runtime + bibliothèque C# (`csharp/`)** — plus riche (LOD, recettes de matériaux, tous les clips, plan d'animation,
   métadonnées d'adaptateur), chargée par du C# qui compile pour les deux loaders :
   * `SbRuntime.Core` (netstandard2.0, sans Unity) : JSON strict, `.sbmesh`, modèle du paquet, vérification SHA-256 ;
   * `SbRuntime.Unity.SR1` (net472, Mono/SRML) et `SbRuntime.Unity.SR2` (net6.0, IL2CPP/MelonLoader, `SB_IL2CPP`) :
     `SbBuilder.Build(pkg, opts)` construit hiérarchie, `Mesh`/`SkinnedMeshRenderer`, matériaux (clone du matériau natif
     `template` + surcharges + PNG), `AnimationClip`, et `SbBuilder.ApplyOverrides` branche les clips sur le contrôleur natif.
   Les deux variantes sont **compilées contre les vraies assemblies** des jeux (Managed SR1, Il2CppAssemblies SR2).
3. **AssetBundle Unity** — optionnel : possible avec l'éditeur de la version exacte (`unity-verify` charge déjà le paquet dans
   l'éditeur ; construire un bundle en plus demande des shaders du jeu dans le projet, qui ne sont pas redistribuables).

## Ce qui est vérifié, ce qui ne l'est pas

* Vérifié par la machine : cœur C# = Python (parité exacte sur paquets réels) ; builder compilé pour SR1 et SR2 ;
  **chargement + skinning + animation dans Unity 2019.4.29f1** (version de SR1) via `sbconv unity-verify`
  (BakeMesh de repos = mesh source ; poses d'animation évaluées par Unity = courbes Python).
* **Non vérifié** : exécution dans les jeux eux-mêmes (SRML / MelonLoader), shaders réels du jeu (le contrôle Unity utilise un
  matériau Standard teinté), chemin IL2CPP du builder (compilé seulement), sauvegarde/rechargement, vacpack, comportements.
  Aucun jeu n'a été lancé : le statut `GAME_RUNTIME_VERIFIED` n'est donc jamais posé.

## Exemple (SR1, plugin SRML)

```csharp
var pkg = SbRuntime.SbPackage.Load(@"...\runtime\SR2-to-SR1\slime.ringtail");           // vérifie tous les SHA-256
var built = SbRuntime.UnityBuild.SbBuilder.Build(pkg, new SbRuntime.UnityBuild.SbBuildOptions {
    FindTemplate = name => Resources.FindObjectsOfTypeAll<Material>().FirstOrDefault(m => m.name == name) });
// built.Root / built.Appearance / built.Clips ; puis SbBuilder.ApplyOverrides(animator, built) sur l'Animator du prefab natif
```
`pkg.StateMap` indique les états du contrôleur source sans équivalent natif (Roar, Sleep, Yawn pour SR2→SR1) et
`pkg.MissingScripts` les comportements à réimplémenter (pétrification du Ringtail, etc.) : c'est le travail de l'adaptateur.
