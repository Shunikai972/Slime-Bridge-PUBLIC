# Traduction des matériaux

Un modèle correctement texturé est le critère de réussite : un matériau n'est jamais « copié » d'un moteur à l'autre.
Chaque matériau devient une **recette** : shader *existant* dans le jeu cible, matériau natif à cloner (`template`),
surcharges de propriétés, textures (copiées ou bakées), et un niveau de **fidélité** avec ses notes.

## Faits mesurés sur les installations

| | SR1 1.4.x | SR2 0.6.x |
|---|---|---|
| Unity | 2019.4.29f1, Mono | 2021.3.28f1, IL2CPP |
| Pipeline / espace colorimétrique | Built-in / **Gamma** | **HDRP** / **Linéaire** |
| Corps | un shader par variante : `Body/Default, Stripe, Normal, Normal Stripe, Rad, Quantum, PhosCore, Attachment…` | un « uber-shader » `SR/AMP/Slime/Body` (≈208 floats : `_EnableStripes`, `_EnableNormals`, `_BODYCOLORING_*`, `_ColorMask`…) |
| Visage | `SR/AMP/Slime/Face/Eyes Default|Glow|Feral|Blush`, `Mouth Default|Glow` | **mêmes noms et mêmes propriétés sémantiques**, + paramètres d'infrastructure HDRP |
| Normal maps | DXT5(nm) | DXT5(nm) (aucune conversion de canaux nécessaire ; détectée par format si ça change) |

## Règles (registry/material_rules.json, tout est explicite)

1. **Propriétés d'infrastructure** (`_Stencil*`, `_Tess*`, `_DiffusionProfile`, `_ZTest*`, blends, `_DoubleSided*`,
   `_RenderQueueType`, `_Receives*`…) : jamais copiées vers Built-in ; vers HDRP elles viennent du matériau natif cloné.
2. **Propriétés sémantiques** : copiées seulement si le shader cible les connaît (contrat lu dans l'installation cible) ;
   le reste est listé dans `dropped` et abaisse la fidélité.
3. **Couleurs** : valeurs sRGB copiées telles quelles (elles sont éditées en sRGB dans les deux jeux). Propriétés **HDR**
   (`_GlowColor`, `_EmissionColor`…) : stockées linéaires dans un projet Linéaire mais en valeur d'affichage dans un projet
   Gamma → conversion sRGB dans le sens SR2→SR1 (inverse dans l'autre), en préservant l'intensité > 1.
4. **Textures** : octets conservés (PNG sans perte) ; indicateur sRGB/linéaire et format conservés.
5. **Visages** : correspondance directe shader pour shader (`face_direct`). `Mouth Sleep`/`Shadow` (SR2 seulement) →
   `Mouth Default` avec note.
6. **Corps SR1→SR2** (`template_uber`) : les jetons du nom de shader (`Stripe`, `Normal`, `Rad`, `Quantum`, `PhosCore`,
   `BoomCracks`) activent les interrupteurs de l'uber-shader (`_EnableStripes=1` + mot-clé `_ENABLESTRIPES_ON`, …) sur un
   modèle SR2 dont les interrupteurs sont les plus proches.
7. **Corps SR2→SR1** : `native_variant` si une variante SR1 existe pour l'ensemble de fonctionnalités (sinon la plus
   proche, avec les fonctions perdues listées) ; **`bake_multi`** pour `_BODYCOLORING_MULTI` / Sloomber / Twin, qui n'ont pas
   d'équivalent SR1.
8. **Effets** (auras, particules) : même shader si présent dans les deux jeux, fidélité `approximate` ; sinon `unsupported`
   (adaptateur manuel nécessaire).

## Le bake `bake_multi` (corps multicolores : Ringtail, Cotton, Batty, …)

Modèle documenté (`materials/bake.py`) :

* UV0 porte l'UV d'éclairage ; le dégradé vertical suit la commande publiée du shader :
  `t = saturate((|(u−0.5, v)| − 0.25)·4/3)`, `q = v + H·(smoothstep(t) − v)`, `z = 0.5 + C·(q − 0.5)` avec
  `C = _LightingUVContribution`, `H = _LightingUVHorizontalAdjust` (Ringtail : `C = 0` ⇒ couleur « Middle » uniforme).
* Palette : `z∈[0,½]` Bottom→Middle, `z∈[½,1]` Middle→Top.
* `_ColorMask` (UV1 si `_ColorMaskUV1`) : canaux R/G/B pondèrent les palettes `Red/Green/Blue × Top/Middle/Bottom` ;
  le poids restant garde la palette de base.
* Résultat : texture RGBA sRGB en espace UV → `_Diffuse` du shader SR1 `Body/Attachment`, `_Color` blanc.

C'est un **modèle**, pas une copie du shader : l'éclairage dépendant de la vue (SSS, rim), les effets Twin/Vortex et les
ombrages HDRP ne sont pas reproduits. La fidélité est donc marquée `approximate` et doit être confirmée en jeu.

## Pièges connus (Ringtail dans SR1)

* Le masque `_ColorMask` de SR2 (clair = marquage) est l'**inverse** de `_StripeTexture` de SR1 (clair = corps uni) :
  le simple copier-coller colore la partie non marquée. Le bake évite ce chemin ; si une règle « stripe » est utilisée,
  la note `mask convention not verified` est ajoutée à la recette.
* Les propriétés d'éclairage (`_Gloss`, `_BodyLightingContribution`, `_LightingUV*`) doivent suivre la source, pas le
  modèle SR1 cloné.
* Ne pas utiliser de conversion gamma globale : seules les propriétés HDR sont converties.

## Ajouter / corriger une règle

Éditer `registry/material_rules.json` (noms de props d'infrastructure, props HDR, listes de shaders, interrupteurs de
fonctionnalités). Les tests `tests/test_materials.py` documentent le comportement attendu.
