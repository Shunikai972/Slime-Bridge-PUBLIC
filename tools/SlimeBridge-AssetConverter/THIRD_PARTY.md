# Composants tiers

Rien de ce qui suit n'embarque d'asset de jeu. Les dépendances Python sont installées par `setup.ps1` depuis PyPI.

| Composant | Licence | Usage |
|---|---|---|
| UnityPy 1.25.4 | MIT | lecture des fichiers sérialisés / bundles / meshes / textures |
| TypeTreeGeneratorAPI | MIT | génération de TypeTrees depuis les assemblies / métadonnées IL2CPP **de l'installation locale** |
| numpy, Pillow, pygltflib, texture2ddecoder, PyYAML | BSD / HPND / MIT | calcul, images, GLB, décodage de textures, YAML |
| AssetRipper 2.x | GPL-3.0 | **processus externe** piloté en HTTP local (export des animations) ; non redistribué, non lié |

Vérifier les licences exactes des versions installées (`pip show`) avant toute distribution de l'outil.
SlimeBridge-AssetConverter n'a pas encore de licence propre ; le code n'est pas publié.
