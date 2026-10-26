# Rad : cache source SR1 → SR2 (extraction hors ligne)

## Contrat
- CLI limitée à `Rad` / `slime.rad`, Unity 2019.4 Mono, UnityPy 1.25.4.
- `model.json` : `bridge-native-mesh` v2, même structure neutre que SR2.
- Sélection : `slimeRad` → applicator → SlimeDefinition → unique apparence
  classique → structures/éléments/SlimeAppearanceObject LOD0 ; aucune
  recherche approximative de maillage, palette ou matériau par nom.
- TRS Unity locaux conservés, quaternion XYZW, matrices bind-pose 16 nombres,
  poids `[w0,w1,w2,w3,i0,i1,i2,i3]`, triangles par sous-maillage.
- SR1 et SR2 utilisent tous deux le repère Unity gauche et les unités Unity :
  aucun miroir, changement d'axe ou changement d'échelle à ce maillon.
- Palette : `appearance-colors.json`, `bridge-appearance-colors` v1 ;
  `palette_legacy` = Top/Middle/Bottom/Ammo RGBA, `palette_new` = null.
- Icône : PNG RGBA natif entier, sans rééchantillonnage ;
  `icon-descriptor.json` conserve rect/pivot/PPU/mesh serré et UV source.
- `cache-manifest.json` : convention `bridge-asset-cache-manifest` v1,
  converter_version 4, canonical_id slime.rad ; sha256 de chaque autre fichier.
  Le manifeste ne peut se hacher lui-même ; son hash est retourné par la CLI.
- `dump-receipt.json` : sha256 des fichiers réellement chargés, y compris
  dépendances, flux resS et DLL de génération de schémas, et hash du modèle.
- Publication : dossier frère temporaire puis renommage atomique ; destination
  nouvelle obligatoire, privée et sans reparse point dans le Lab du worktree.
  `--allow-nonlab` n'autorise que la lecture d'une source hors Lab.

## Données réelles de Rad
- Corps `slime_default` : 239 sommets, 1 sous-maillage, 6 os pondérés.
  Visage = passes eyeHappy (Eyes Glow) / mouthHappy sur le même maillage ;
  pas de maillage distinct d'oreilles ou de visage dans l'apparence LOD0.
- Hiérarchie : 21 transforms sous bone_root (dont Shadow), parentage conservé.
- Aura : `sf_sphere_lowpoly` (249 sommets), core Quad (4 sommets),
  matériaux/shaders/textures authentiques, rendus statiques `enabled=false`.
- Icône `iconSlimeRad` : texture 512×512, PPU 50, pivot (0.5, 0.5).
- 9 clips uniques référencés par NewRigAnimator, défaut = Idle via l'état
  sérialisé de la couche de base : Idle, Rock Windup/Recover/Fire, Pounce,
  Bite, Digest, FlapWings_All, Dervish Spinner. Les clips communs inutilisés
  par certaines actions de Rad ne prouvent pas ces actions ; ils restent
  référencés par son contrôleur. Aucun nom de clip n'est deviné.
- Échantillons à la grille source, pas reconstruction du contrôleur/blending.
  Rock Recover contient des premières clés à 4.768e-7 s : un maintien exact
  de leur valeur sentinelle initiale est permis seulement si elle correspond
  exactement à la première clé, dans une tolérance ≤1e-6 s ; aucune clé réelle
  ni origine de polynôme n'est déplacée. Toute autre absence est refusée.

## Effets et limites
- `source-effects.json` est DESCRIPTIF : aura/core, attache, matériaux,
  CameraFacingBillboard, RadExpandMarker, RadSource, RadSlimeExpand et
  RubberBoneEffect, avec paramètres sérialisés. Particules/lumières attachées
  sont collectées si présentes ; aucune n'a été trouvée dans ces hiérarchies.
- Extension optionnelle additive `source_effects` ; pas de rendu d'effet,
  glow, billboard, jiggle procédural, shader parity ou logique radioactive.
- Os résolus par CRC32 des chemins et déclaration applicator, jamais par
  les m_Bones null-strippés ni un choix arbitraire entre matrices identiques.
  Hash absent, matrice singulière, LOD0/default/clip ambigu : échec fermé.
- L'installation SRML a une Assembly-CSharp_old.dll de même identité : elle
  est exclue ; le schéma vient de la DLL installée patchée, hachée au reçu.
- Preuve : OFFLINE_EXTRACTED_HASH_VERIFIED / TESTS_PASSED uniquement.
  Aucun jeu lancé ; import/rendu/animation native SR2 restent à vérifier.

## Commandes (depuis le worktree)
```bash
uv run --python 3.12 --with UnityPy==1.25.4 --with TypeTreeGeneratorAPI==0.0.10 \
 --with numpy --with Pillow python -m bridge.content.sr1_asset_extract \
 --species Rad --canonical slime.rad \
 --sr1-dir Lab/Games/SR1/SlimeRancher_Data \
 --out Lab/RuntimeArtifacts/source-cache-v4/Rad-extract-v1
uv run --python 3.12 --with pytest --with UnityPy==1.25.4 \
 --with TypeTreeGeneratorAPI==0.0.10 --with numpy --with Pillow \
 python -m pytest tests/content/test_sr1_*.py -q -p no:cacheprovider
# Ajouter SLIMEBRIDGE_REAL_SR1_CACHE=1 pour l'oracle local explicite.
# Chaque oracle crée un cache ignoré nouveau, jamais d'écriture dans le jeu.
```
