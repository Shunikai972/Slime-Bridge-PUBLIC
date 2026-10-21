# Architecture

```
Jeu source ──scan──▶ install (versions, backend, pipeline, empreintes)
     │
     ├─ inventory (workers isolés, cache par taille+mtime)      sbconv/inventory.py
     ├─ catalog   (SlimeDefinition → espèces, prefab, apparence) sbconv/catalog.py
     │
     ▼  EXTRACT   sbconv/extract/{prefab,appearance,mesh,materials,animation,species}.py
  paquet IR  ir/<GAME>/<canonical_id>/species.json (+ meshes/*.sbmesh, textures/*.png, anim/clips/*.json)
     ▼  CONVERT   sbconv/convert.py + sbconv/materials/{translate,bake,raster,templates}.py
  converted/<SRC>-to-<TGT>/<id>/convert.json (+ textures bakées)
     ▼  BUILD     sbconv/build.py (+ export/gltf.py)
  runtime/<SRC>-to-<TGT>/<id>/ package.json, parts.json, rig.json, materials.json, face.json, animation/, adapter.json, preview.glb
     ▼  VERIFY    sbconv/verify/{offline,runtime,glb_roundtrip}.py
  status.json (registre des statuts)
```

## Principes

* **Piloté par les données.** Aucune liste d'espèces, aucune version d'Unity, aucune valeur de matériau n'est écrite à la
  main. Espèces, apparences, palettes et visages viennent des `SlimeDefinition` / `SlimeAppearance` des jeux ; les
  contrats de propriétés de shaders viennent de la « bibliothèque de templates » lue dans l'installation cible
  (`output/templates/`). Ajouter une espèce = rien à faire ; une exception de rendu = une entrée dans
  `registry/material_rules.json`.
* **Un moteur d'extraction remplaçable.** `engines/base.py` définit le contrat. Deux adaptateurs : UnityPy (objets,
  meshes, textures, matériaux, MonoBehaviour via TypeTrees générés) et AssetRipper (clips « muscle » et contrôleurs,
  exportés en YAML). Pas de décodeur Unity maison, sauf le strict nécessaire : l'en-tête des fichiers sérialisés
  (`detect/unity_serialized.py`, ~60 lignes, pour lire la version d'Unity sans dépendance) et la résolution CRC32 des
  chemins d'os d'AssetRipper.
* **Mémoire bornée.** SR2 = 115 bundles (2,5 Go). Le moteur charge les bundles à la demande selon leurs dépendances (CAB)
  avec un cache LRU ; l'inventaire tourne en processus isolés.
* **Reprise / incrémental.** Chaque étape écrit atomiquement un `params_key` (outil + empreintes des installs + entrées).
  Relancer sans changement = cache hit. Un crash laisse des fichiers complets ou absents, jamais à moitié écrits.
* **Honnêteté des statuts.** Chaque étape n'élève le statut que sur preuve enregistrée ; un problème bloquant donne
  `INCOMPLETE` avec la raison.

## Comparatif des moteurs étudiés

| Outil | Licence | Ce qu'on en fait | Raisons |
|---|---|---|---|
| UnityPy 1.25 | MIT | **Moteur principal** (Python, automatisable, lit 2019.4 *et* 2021.3, bundles, TypeTrees générés pour les MonoBehaviour) | Validé sur les deux installs réelles ; n'a pas de convertisseur de clips « muscle » |
| AssetRipper 2.x | GPL-3.0 | **Moteur d'animation** en processus externe (API HTTP locale) | Seul à décoder fiablement les clips/contrôleurs ; GPL → jamais intégré ni redistribué |
| AssetStudio (Perfare) | MIT, archivé | Écarté comme dépendance | Projet archivé, GUI orientée export manuel |
| AssetsTools.NET | MIT | Non retenu (alternative possible via l'interface d'engine) | Bibliothèque .NET ; n'ajoute rien pour les types nécessaires ici |

## Structure du code

`detect/` (découverte Steam/GOG/Epic/manuel + analyse d'installation) · `engines/` · `extract/` · `ir/` (stockage sans perte,
maths de rig, courbes) · `materials/` · `export/gltf.py` · `verify/` · `api.py` · `cli.py` · `protocol.py` · `registry/` (versions
connues, règles de matériaux, tout en JSON).
