# Rapport : preuves, limites techniques et points juridiques

Date des mesures : 14 octobre 2026. Installations : SR1 1.4.x (Unity 2019.4.29f1, Mono, copie patchée SRML), SR2 0.6.2 (Unity 2021.3.28f1, IL2CPP).
Les chiffres ci-dessous viennent des exécutions réelles (journal `output/logs/`, registre `output/status.json`).

## 1. Ce qui a réellement été exécuté

| Livrable demandé | Preuve |
|---|---|
| Détection automatique des versions | `sbconv scan` sur les deux installs (Unity, backend, pipeline HDRP/Built-in, espace Linéaire/Gamma, Addressables, empreintes) |
| Extraction ciblée dans les deux sens | 347 espèces extraites (SR1 : 22 slimes + 91 largos ; SR2 : 30 + 204), dont les animations (AssetRipper) et les icônes |
| Conversion dans les deux sens | 347 paquets runtime reconstruits ; 344 sans matériau de corps/visage manquant (3 `INCOMPLETE` volontaires, voir §2) |
| Vérification hors-ligne | `sbconv verify --all` → **344/347 passent** (3 `INCOMPLETE` : SR1 Glitch, SR1 GlitchTarr, SR2 Angler-Twin ; `status` donne le statut final, le plus haut atteint) : intégrité SHA-256, références, géométrie, **skinning au repos == mesh source (écart 0)**, cohérence contrôleur/clips, déformations finies, re-lecture du GLB |
| Cache SlimeBridge | `sbconv export --only-missing` : **163 des 164** espèces manquantes ont un cache `bridge-native-mesh` v2 valide (port du parseur C# + scanner Python du dépôt) ; seul GlitchTarr n'a pas d'icône. Les largos n'ont pas d'icône propre dans les jeux : elles reprennent celle de leur premier parent (`icon_fallback` dans le manifeste, désactivable) |
| C# | `csharp/` compilé (SDK .NET 8) : cœur Unity-free, builder SR1 (net472, assemblies Unity de SR1) et SR2 (net6.0, interop IL2CPP) ; **parité exacte** avec Python sur paquets réels |
| Unity | `sbconv unity-verify` dans l'éditeur **du jeu cible** (SR1 : 2019.4.29f1 ; SR2 : 2021.3.28f1, licence activée) : **232 paquets `UNITY_VERIFIED`** = 121 espèces SR2 absentes de SR1 (SR2→SR1) + 111 espèces SR1 (SR1→SR2). 0 erreur Unity, skinning de repos d'Unity = mesh source (≤1e-7), poses d'animation d'Unity = courbes (positions ≤1.7e-7, rotations ≤1.1e-6 rad). Les 3 paquets `INCOMPLETE` se chargent dans Unity mais ne sont jamais promus (la preuve Unity ne remplace pas la barrière hors-ligne). Cette vérification a trouvé un vrai défaut de mon builder C# (décalage d'édition des pièces skinnées, Batty/Cotton), corrigé |
| Tests automatisés | 41 tests Python (`unittest`) + harnais C# + test de parité (7 paquets réels : slimes et largos, deux sens) |

## 2. Statuts atteints (jamais gonflés)

* `OFFLINE_VERIFIED` / `UNITY_VERIFIED` : voir `sbconv status`. Les seuls `INCOMPLETE` sont les paquets dont un matériau de corps/visage n'a pas d'équivalent
  dans le jeu cible : **SR1 Glitch** et **SR1 GlitchTarr** (shader Glitch absent de SR2) et **SR2 Angler-Twin** (matériau multicolore sans masque).
  Tarr, Twin, etc. : à implémenter côté mod, comme prévu.
* `UNITY_VERIFIED` : atteint **dans les deux sens** (232 paquets, voir §1). Il n'est posé que si le paquet passe aussi la vérification hors-ligne.
  Limite connue : Unity 2021 refuse `AnimationClip.AddEvent` en mode édition (autorisé en jeu) ; ces messages sont comptés à part
  (`editor_only_event_notices`), donc les événements d'animation ne sont pas vérifiés dans l'éditeur.
* `GAME_RUNTIME_VERIFIED` : **jamais posé** — aucun des deux jeux n'a été lancé avec ces paquets.

## 3. Limites techniques restantes

1. **Shaders** : le contrôle Unity utilise un Standard teinté (les shaders du jeu ne sont pas dans un projet d'éditeur nu). La fidélité visuelle réelle se
   juge en jeu. Les corps multicolores SR2 sont **bakés** (modèle documenté dans `MATERIALS.md`) ; éclairage dépendant de la vue (SSS, rim), effets Twin/Vortex/Sloomber
   non reproduits. Chaque recette porte son niveau de fidélité (`exact/equivalent/approximate/degraded/unsupported`).
2. **Comportements** : les scripts natifs (IL2CPP/Mono) ne sont pas copiés ; ils sont listés dans `adapter.json` (ex. pétrification du Ringtail,
   `DaylightReactor`, `BunnyHop`). Les états d'animation SR2 sans équivalent SR1 (Roar, Sleep, Yawn…) sont listés dans le plan d'animation.
3. **Chargeur SlimeBridge actuel (C# du dépôt, non modifié)** : ids `slime.*` seulement (largos exportés `slime.largo.a.b`), icône obligatoire,
   espèces à enregistrer ; le sens SR1→SR2 n'a pas de chargeur côté dépôt (le cache est prêt, le lecteur SR2 reste à écrire — `csharp/` en fournit la base).
4. **IL2CPP** : le builder SR2 est compilé contre les vraies assemblies mais **jamais exécuté** (aucun jeu lancé).
5. **Version du jeu** : une build inconnue est refusée par défaut (`--allow-unvalidated`) ; les builds validées sont dans `registry/known_versions.json`.
   La copie SR1 est patchée SRML (`Assembly-CSharp.dll` + `_old`) : les assets sont ceux du jeu, mais l'empreinte n'est pas celle d'une install vanilla.
6. **Largos** : chaque largo est extrait depuis sa propre `SlimeAppearance` (explicite dans les deux jeux). Aucune composition inventée.
7. **Mémoire** : SR2 fait 2,5 Go de bundles ; le moteur charge à la demande (cache LRU). Machine testée : 32 Go de RAM, ~5 Go libres au pire moment.

## 4. Points juridiques

Ce n'est pas un avis juridique.

* **Autorisation de Monomi Park (message de modération transmis par toi, 14/10/2026)** : « zone grise » ; l'éditeur ne peut pas dire à l'avance si cela entre
  dans sa Fan Content Policy sans voir le produit final ; **tant qu'il n'y a pas d'autre propriété intellectuelle et pas de commercialisation, cela semble acceptable**.
  Conditions pratiques : ne rien vendre ni monétiser (pas de paywall, pas de dons liés au contenu, attention aux monétisations indirectes), n'inclure aucune autre IP,
  **montrer le produit final** à l'éditeur, et mentionner que le projet est non officiel / non approuvé par Monomi Park (déjà dans le README de SlimeBridge).
* **Architecture** : aucun asset du jeu n'est livré. Les paquets/caches sont générés **sur la machine du joueur** à partir de **ses** installations, sous `--output`
  (ignoré par Git), sans aucune requête réseau ni upload ; l'avis `LEGAL` figure dans chaque `package.json`. Ne distribue jamais `output/`.
  Option `--only-missing` : le joueur ne génère que ce qui manque dans son autre jeu.
* **Accord utilisateur GOG** (fichier `EULA.txt` de la copie SR1, §11 b) : la rétro-ingénierie, la modification et les œuvres dérivées du contenu d'un jeu
  « dépendent de ce que le détenteur des droits du contenu autorise » : d'où l'importance de l'accord de Monomi Park ci-dessus (à conserver par écrit).
* **Outils tiers** : UnityPy, TypeTreeGeneratorAPI, PyYAML (MIT), numpy/Pillow (BSD/HPND), pygltflib (vérifier la licence exacte avant distribution) ;
  **AssetRipper est GPL-3.0** : utilisé seulement comme processus externe, ni lié ni redistribué (l'utilisateur doit l'installer lui-même, ou on passe par
  l'export UnityPy sans animations). SRML (GPL-3.0) et MelonLoader (Apache-2.0) : respecter leurs obligations pour tout dérivé distribué ; Starlight-SAL
  interdit de copier son code ou de redistribuer sa DLL.
* **Unity** : la licence est à toi (Personal ; vérifier ses conditions, notamment le seuil de revenus). L'outil ne l'active ni ne la contourne ; les éditeurs ne
  se redistribuent pas. Aucun DRM/chiffrement n'est contourné (les bundles sont lus tels que livrés).

## 5. À faire ensuite

1. Étendre le chargeur SlimeBridge (regex d'id, enregistrement des espèces) et écrire le lecteur SR2 en t'appuyant sur `csharp/SbRuntime.Unity.SR2`.
2. Test en jeu (LAB, avec ton oracle de sauvegardes) : spawn, apparence, animations, vacpack, sauvegarde/rechargement → seulement alors `GAME_RUNTIME_VERIFIED`.
3. Implémenter à la main : Tarr/GlitchTarr, Glitch, Angler-Twin, et les comportements listés dans `adapter.json`.
