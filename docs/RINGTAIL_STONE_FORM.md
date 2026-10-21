# Ringtail — pétrification solaire SR2 → SR1 (contrat v1)

Statut : recherche source terminée et recoupée (paquet privé `ringtail-petrif-source-v1`,
78 preuves SHA-256) ; implémentation v1 réalisée dans la tranche `ringtail-stone-form-v1`
(revue frozen v1 APPROVE, intégrée dans `14b3f92`). Correctif horloge/reconstruction en
validation LAB : voir §7. L'implémentation n'est pas une preuve de gameplay complet. Ce document fige les
décisions de mapping v1 et les limites. Toute affirmation « SR2 » ci-dessous cite une preuve
du paquet ; toute décision « SR1 » est une adaptation bornée, pas une équivalence.

## 1. Faits source SR2 (vérifiés, lecture seule)

- Déclencheur : `TurnToStoneInDaylight : SRBehaviour`, 19 instances (module + 18 prefabs
  ringtail/hybrides). Champ sérialisé : `RingtailMetadata` SO 32409
  (`MinMinutesToTransform=1.0`, `MaxMinutesToTransform=10.0`, tooltip « minutes that a
  ringtail can be in sunlight before transforming »). Runtime : `_daylightReactor`,
  `_slimeAppearanceApplicator`, `_colliders`, `_willBecomeStatue`, `_stoneFormPrefab`,
  `_statueFx` ; méthodes `BecomeStatue`, `SetCollidersEnabled`.
- Exposition : AUCUN raycast solaire propre au ringtail (fait négatif). Modèle réel =
  `DaylightReactor` fenêtre `DaylightStartHour=18.0` / `DaylightEndHour=6.0` (39 instances,
  toutes identiques) + événements `OnEnterLight/OnEnterDarkness` + zones grottes
  (`OnCaveEnter/Exit`, zone CAVE=10000). La fenêtre 18→6 est la fenêtre sombre ; le jour
  (6→18) est le « sunlight » (cohérent avec `DestroyOutsideHoursOfDay` SR1 : destruction
  HORS de la fenêtre 18→6). Incertitude U1 : sémantique exacte borne/endurance non lue.
- Forme pierre : `SlimeAppearance._stoneFormAppearance` → `StoneFormAppearance` 31283
  (base) / 31284 (largo) → prefab `RingtailStatue` 42591 (`ringtailStatueSmall_v1` GO 4430,
  scale 1) / 42590 (`ringtailStatueLarge` GO 4429, scale 2), FX `FX RockSlimeTransform`.
  Meshes dédiés `LF_ringtail_stone_LOD0/1/2` (503/327/197 vertices, indices LOD0 2454),
  matériau dédié `slimeRingtailStoneForm` (563) ; shader source
  `SR/AMP/Paintlight/StaticUV/Base NormalMap` (2009) ; textures `alb_ringtail_statue`
  (1483, 1024²) / `nrm_ringtail_statue` (1607). Statue = `Vacuumable` (pick-up type jouet),
  colliders solides, `IdentifiableActor` 32306/32305, `DaylightReactor`, `SyncLODMaterials`.
- Suralimentation (mécanique distincte, déjà spécifiée par le propriétaire) :
  `KeepEatingWhenFull` (`_showMischievousFace=1`), `SlimeEat.TimePerEatWhenFull=3.0`,
  expression MISCHIEVOUS → `eyeSneaky` 407 + `mouthTabby` 426, texture
  `face_ringtail_sneaky` 1643.

## 2. Décisions d'adaptation SR1 v1

1. Fenêtre lumière : jour = `TimeDirector.CurrHour()` ∈ [6,18) (dérivé du champ 18→6 et des
   fractions aube/crépuscule SR1 0.25/0.75). Aucun raycast.
2. Protection : composant du mod implémentant `CaveTrigger.Listener`, posé sur la RACINE de
   l'acteur importé — le `SphereCollider` du slime est sur la racine (sonde
   `probe_sr1_actor_components.py`), et `GetInterfaceComponent` = `GetComponent` même
   GameObject. Suivre l'idiome natif `DestroyOutsideHoursOfDay` : `HashSet<GameObject>` de
   volumes, ajout/retrait sur OnCaveEnter/OnCaveExit, purge des nulls dans Update.
   VÉRIFIÉ : le bouclier solaire SR1 natif porte un `CaveTrigger` (zone 10000,
   `affectsLighting=0`) sur ses objets « Base/High SolarShield » (actifs seulement si
   l'upgrade `SOLAR_SHIELD` est possédé) → le corral protégé fonctionne sans code spécial.
3. Timer : tirage uniforme [1,10] **minutes de jeu** (valeurs source) au début de chaque
   épisode d'exposition ; accumulation via les différences de `TimeDirector.WorldTime()`
   (secondes de jeu divisées par 60), uniquement si jour ET non protégé. Une pause de
   l'horloge ne consomme pas l'exposition ; l'accélération native est respectée. Retour
   à zéro à la protection (U1 documenté). À l'échéance → pierre.
4. Gel comportemental pendant la pierre : désactiver `SlimeRandomMove`, `SlimeEat`,
   `GotoConsumable`, `FleeThreats`, `SlimeFeral` ; réactiver à l'inverse. Physique,
   `Vacuumable` et colliders conservés (statue ramassable). Approximation v1 documentée :
   les micro-mouvements résiduels (anim/hover) ne sont pas garantis nuls.
5. Visuel : swap vivant ↔ pierre via le modèle pierre dédié (LOD0 ; voir §3). Matériau SR1
   borné (correctif `ringtail-stone-material-v1`) : `Standard` + `_MainTex` albédo pierre en
   sRGB (chargement `linear:false`) + `_BumpMap` normal source chargée linéaire
   (`linear:true`, `_NORMALMAP`, `_BumpScale=1`) + queue source 2225 reprise dans [0, 5000]
   (absente ⇒ défaut Standard 2000 ; valeur invalide ou shader sans `_BumpMap` ⇒ refus).
   Le shader source Paintlight/StaticUV n'existe pas dans SR1 — PAS de parité revendiquée ;
   mips non régénérés. Vivant masqué pendant la pierre. Échelle small (1.0) pour le
   ringtail simple.
6. Non-goals v1 : FX de transformation (`FX RockSlimeTransform`) et sons non portés ;
   persistance save non portée (état recalculé au chargement — `StatueFormModel` SR2 non
   reproduit) ; variantes largo non traitées ; parité multicolore/hdrp inchangée.

## 3. Contrats techniques

- Extraction : nouveau module python opt-in (schéma `bridge-stone-form` v1) — meshes
  LOD0/1/2 décodés, matériau pierre (floats/colors subset + textures), TRS source, IDs ;
  fail-closed ; revue frozen avant fusion ; PAS de wiring dans le cache canonique sans
  revue. Worktree : `ringtail-stone-extract-v1`.
- Mod : extension de cache côté SR1 (lecture `stone_form` fail-closed : absence ⇒ aucune
  pétrification, jamais d'invention), composant état + listener (racine acteur), swap
  visuel, diagnostics LAB (état, protection, minutes accumulées) derrière le gate
  rest-pose comme les autres preuves. Worktree : `ringtail-stone-form-v1`.
- Le composant ne s'active que pour les espèces dont le cache déclare `stone_form`
  (ringtail) — aucune autre espèce affectée.

## 4. Matrice de tests

CPU (TDD, sans Unity) : fenêtre jour/nuit (bornes 6/18), accumulation/seuil [1,10] borné,
protection entrée/sortie (comptage multi-volumes + purge), gel/restauration exact
(disable/enable symétriques, idempotence), cache absent ⇒ inerte, cache invalide ⇒ refus,
NaN/duplicats refusés.

LAB (harnais existants + run protégé) : jour non protégé → statue après timer (captures +
diagnostics) ; grotte → retour vivant ; corral avec bouclier solaire → reste vivant en
jour ; nuit → vivant ; statue : pas de nourrissage, aspiration/rejet OK, 0 NRE post-marker ;
rollback saves/plugin exact. Aucune preuve GPU/parité revendiquée par ces tests.

## 5. Incertitudes ouvertes (à ne pas masquer)

U1 sémantique exacte fenêtre/endurance ; U2 endurance grottes multi-niveaux ; U3 FX/sons ;
U4 endurance visuelle (le timer est-il par épisode ou cumulatif ?) — décision v1 ci-dessus ;
U5 le comportement quand l'acteur pétrifié est aspiré puis relâché en plein jour (le timer
repart) ; U6 statue + save/chargement (recalcul v1).

## 6. Notes d'implémentation (tranche `ringtail-stone-form-v1`, revue v1 APPROVE)

- Mod : `mods/sr1-bridge/ImportedStoneForm.cs` (chargement fail-closed du cache
  `bridge-stone-form` v1, template pierre inactif, `AttachTo`, marqueur
  `ImportedStoneFormMarker`) ; `mods/sr1-bridge/ImportedPetrification.cs` (machine d'état,
  `CaveTrigger.Listener`, gel/dégel). Wiring : `ImportedSpawner.ApplyImportedVisualToRoot`
  attache la machine après `ImportedGameplayAnimation.EnsureAttached`, derrière
  `ImportedStoneForm.TryGet` (espèce sans cache = no-op strict), et le spawn rapporte
  `petrification_attached`. Diagnostics : champ `petrification` par instance
  (`state`, `exposure_minutes`, `protected`, `daylight`, `frozen_count`), rest-pose gate
  uniquement, lecture seule.
- Prérequis du cache avant un run LAB (semé par le parent, extraction revue d'abord) :
  `<repo>/Lab/AssetCache/sr2-to-sr1/<canonical>/` doit contenir `stone-form.json`,
  `stone-form-receipt.json` et les deux PNG référencés par le matériau (noms de fichiers
  sûrs, contenus sous le dossier), chaque fichier listé dans le receipt avec taille et
  SHA-256 correspondants. Le receipt est l'ancre de confiance : absent ⇒ aucune
  pétrification. Aucun fichier mesh sur disque (les meshes sont embarqués dans le JSON).
  Canonical : jeton sûr minuscule (ex. `ringtail`).
- Décisions d'implémentation bornées (pas de parité revendiquée) : RNG =
  `UnityEngine.Random.Range(1, 10)` (tirage uniforme unique par épisode) ; masquage du
  vivant = désactivation des `Renderer` du sous-arbre `ImportedVisualMarker` (le GameObject
  reste actif), ré-affirmée à chaque tick (auto-guérison après reconstruction d'apparence) ;
  capture des cinq contrôles gelés UNE fois au premier `Petrify` (restauration exacte,
  composants absents/détruits tolérés) ; visuel pierre = nœud racine (TRS source) + enfant
  LOD0 (TRS source, `MeshFilter`+`MeshRenderer`) sous `StoneVisual`, inactif par défaut ;
  matériau `Standard` blanc + `_MainTex` (albédo pierre sRGB) + `_BumpMap` (normal source
  chargée linéairement — `LoadTexture(path, linear)` — avec `_NORMALMAP`, `_BumpScale=1`
  et garde fail-closed `HasProperty("_BumpMap")`) + queue source bornée [0, 5000] (2225
  exportée ; absente ⇒ défaut Standard 2000 ; invalide ⇒ refus) ; horloge incertaine
  (TimeDirector absent) ⇒ ne jamais pétrifier, retour vivant si une statue existait ;
  purge des volumes de protection détruits uniquement (les sorties de zone viennent de
  `OnCaveExit`).
- Harnais CPU (sans Unity/GPU) : `tests/ImportedStoneFormHarness` (65 checks : cache valide,
  absent, hash, traversal, JSON malformé, TRS singulier, budgets, PNG non décodable, contrat
  matériau `_BumpMap`/`_NORMALMAP`/`_BumpScale`/espaces couleur, reprise bornée de la queue
  et garde `_BumpMap` manquant) et
  `tests/ImportedPetrificationHarness` (77 checks initiaux, étendus par le correctif §7 :
  bornes 5.99/6/17.99/18, tirage [1, 10]
  borné, protection multi-volumes + purge + guérison, gel/restauration exacts + composant
  détruit, idempotence, horloge native/`WorldTime`, `OnDestroy`). Tests :
  `tests/test_imported_stone_form.py`, `tests/test_imported_petrification.py` (contrats
  statiques + exécution des harnais).
- Réparations de test de la même tranche (nécessaires aux suites vertes) : stubs
  `ImportedStoneForm`/`ImportedPetrification` dans `tests/ImportedPaletteScopeHarness/Program.cs`
  (même pattern que le stub `ImportedGameplayAnimation` du correctif couleur) ; rattrapage de
  la régression pré-existante de `test_diagnostics.py` (le `DiagnosticsHarness` ne compilait
  plus depuis le correctif couleur) via `MaterialPropertyBlock`, `Renderer.GetPropertyBlock`/
  `HasPropertyBlock`, `Material.GetColor(int)`, un stub `ImportedPetrification` et un check
  rest-pose du champ `petrification`.
- Limites : U1–U6 ci-dessus restent ouverts ; persistance save non portée (état recalculé
  au chargement). Le premier run LAB a acquis des captures mais n'a pas validé les
  transitions ; sa phase de recyclage a échoué sur la perte de focus. Les mesures de
  couleurs/ASCII ne sont pas des preuves visuelles.

## 7. Correction horloge et reconstruction — en validation

Deux régressions reproduites RED contre `14b3f92` : durée calculée en minutes réelles
au lieu de minutes de jeu ; suppression du mesh pierre comme un placeholder lors d'une
reconstruction d'apparence. Correction : différences de `WorldTime`, retour vivant sur
horloge invalide/reculée, exclusion du sous-arbre `ImportedStoneFormMarker` et
réaffirmation des renderers pierre (pas seulement de `GameObject.activeSelf`). Les
couleurs du Ringtail vivant ne sont pas modifiées.

Pour un LAB long, `SLIMEBRIDGE_DIAG_TIMELINE_SECONDS=5` (entier strict 5–900) active un
sampler lecture seule seulement avec le vrai exécutable LAB, le rest-pose, les opérations
LAB et une cible `SLIMEBRIDGE_DIAG` sûre. Le fichier
`visual-diagnostics-slime.ringtail.timeline.jsonl` contient jusqu'à 256 relevés sur les
64 racines importées les plus récentes. Chaque entrée porte un identifiant d'instance
Unity, l'état, l'exposition, la protection, le gel et la visibilité des renderers pierre/
vivant. Ce relevé atteste l'état moteur ; il ne remplace pas la lecture des captures ni
les tests de Vacpack, alimentation et sauvegarde/rechargement.
