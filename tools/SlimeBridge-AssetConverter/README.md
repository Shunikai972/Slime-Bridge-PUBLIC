# SlimeBridge-AssetConverter (`sbconv`)

Convertisseur **local** et **automatique** des ressources de slimes entre **Slime Rancher 1 et 2**, dans les deux sens
(slimes de base, largos, ...). Il lit *tes* installations (lecture seule), reconstruit pour l'autre jeu un **paquet
runtime** (géométrie, squelette, matériaux traduits, visages, animations, métadonnées pour les adaptateurs) et le
vérifie. Aucun asset des jeux n'est inclus dans l'outil ; tout ce qui est généré reste sous le dossier `--output`
(ignoré par Git) et ne quitte jamais la machine.

> Livré dans SlimeBridge sous `tools/SlimeBridge-AssetConverter` ; le launcher l'utilise pour la page « Extraire »
> (nom du projet : **SlimeBridge-AssetConverter**, paquet Python **`sbconv`**).

## Installation (Windows)

```powershell
cd tools\SlimeBridge-AssetConverter
.\setup.ps1            # crée .venv et installe requirements.txt (UnityPy, numpy, Pillow, pygltflib, ...)
.\sbconv.cmd scan --sr1-path "D:\...\SR1" --sr2-path "D:\...\SR2"
```

AssetRipper (moteur externe pour les animations) : détecté via `SBCONV_ASSETRIPPER` (dossier d'AssetRipper 2.x, optionnel).
Il est piloté en processus séparé (GPL-3.0, jamais lié ni redistribué). `sbconv engines` montre ce qui est disponible.

## Utilisation rapide

```powershell
sbconv scan    --sr1-path <dossier SR1> --sr2-path <dossier SR2>          # versions Unity, backend, pipeline, bundles...
sbconv list    --sr1-path ... --sr2-path ... --game SR2 --kind base       # espèces + statut
sbconv convert --sr1-path ... --sr2-path ... --direction sr2-to-sr1 --slime Ringtail --slime Cotton --dry-run
sbconv convert --sr1-path ... --sr2-path ... --direction both --all --kind base      # tout, automatiquement
sbconv convert --sr1-path ... --sr2-path ... --direction both --all --kind largo     # tous les largos
sbconv verify  --sr1-path ... --sr2-path ... --direction both --all
sbconv status
```

**Seulement ce qui manque dans l'autre jeu** (largos inclus), pour la version distribuée aux joueurs ; sans cette option tout est converti
(version complète pour tes tests) :

```powershell
sbconv convert --direction both --all --only-missing --sr1-path ... --sr2-path ...
sbconv export  --direction both --all --only-missing ...        # caches SlimeBridge (bridge-native-mesh) validés
sbconv unity-verify --direction sr2-to-sr1 --slime Ringtail ...  # charge dans l'éditeur Unity de la bonne version
```

« Manquant » = non jouable dans le jeu cible : côté SR2, un slime sans entrée de pédia (Rad, Quantum, Mosaic, Glitch… sont des
définitions inachevées) compte comme absent ; un largo est présent seulement si ses deux parents le sont. `sbconv list` l'indique.

`--direction sr1-to-sr2 | sr2-to-sr1 | both`, `--output DIR` (défaut `./output`), `--slime NOM` (répétable),
`--all`, `--kind base|largo|all`, `--dry-run` (n'écrit rien), `--force`, `--json` (sortie machine).
Les dossiers « enveloppes » (archive extraite avec plusieurs niveaux) sont acceptés : l'installation réelle est
retrouvée automatiquement. Si les chemins ne sont pas fournis, Steam / GOG / Epic et `SBCONV_SEARCH_PATHS` sont essayés.

## Ce qui est fait automatiquement, sans réglage par slime

1. **Détection** : version Unity exacte (`globalgamemanagers`), backend Mono/IL2CPP, plateforme, architecture, TypeTrees,
   Addressables, pipeline graphique (SR1 : Built-in/Gamma, SR2 : HDRP/Linéaire), empreintes SHA-256. Une version non
   reconnue produit un diagnostic, jamais une compatibilité supposée.
2. **Catalogue** : toutes les espèces sont découvertes depuis les `SlimeDefinition` des jeux eux-mêmes
   (SR1 : 22 + 91 largos ; SR2 : 30 + 204 largos), via des TypeTrees générés depuis les assemblies / les métadonnées IL2CPP.
3. **Extraction** : prefab, squelette, pièces par LOD, meshes, matériaux, textures, visage (toutes expressions), palette,
   contrôleur d'animation (états, transitions, paramètres), clips (courbes par os), colliders, comportements (métadonnées).
4. **Conversion** : traduction explicite des matériaux (voir `docs/MATERIALS.md`), plan d'animation vs états natifs du
   jeu cible, liste des comportements à réimplémenter côté jeu.
5. **Reconstruction** : paquet runtime autonome + `preview.glb`.
6. **Vérification** hors-ligne : intégrité SHA-256, références, géométrie, **skinning au repos == mesh d'origine**,
   cohérence contrôleur/clips, re-lecture du GLB.

## Statuts (jamais gonflés)

`EXTRACTED` → `CONVERTED` → `REBUILT` → `OFFLINE_VERIFIED` → `UNITY_VERIFIED` → `GAME_RUNTIME_VERIFIED`, ou `INCOMPLETE`.
`UNITY_VERIFIED` est posé par `sbconv unity-verify` quand l'éditeur de la **version exacte du jeu cible** (licence activée par toi) a chargé
le paquet, skinné et animé sans erreur. `GAME_RUNTIME_VERIFIED` exige une session de jeu réelle : jamais posé automatiquement. Un paquet qui se charge
n'est pas « intégré au jeu » : voir `docs/REPORT_LIMITS.md`.

## Sécurité / intégrité

Installations en lecture seule (le dossier de sortie ne peut pas être dans/contenir un dossier de jeu) ; écritures
atomiques confinées à `--output` (contrôle de chemins) ; SHA-256 de tous les fichiers et sources ; caches incrémentaux
(`params_key`) et reprise après interruption ; journaux sous `output/logs/` ; aucune requête réseau, aucun upload,
aucun téléchargement d'asset. Pas de contournement de DRM, de chiffrement ni de licence Unity.

## Intégration au launcher

API Python stable (`sbconv.api`), JSON de la CLI (`--json`) et protocole JSON-lines (`sbconv serve`) : `docs/LAUNCHER_INTEGRATION.md`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Documentation : `docs/ARCHITECTURE.md`, `docs/FORMATS.md`, `docs/MATERIALS.md`, `docs/LOADERS.md` (SRML / MelonLoader / SlimeBridge), `docs/LAUNCHER_INTEGRATION.md`, `docs/REPORT_LIMITS.md`, `csharp/README.md`, `THIRD_PARTY.md`.
