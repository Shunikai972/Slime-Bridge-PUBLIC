# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org/); the release script reads the section
whose title matches `VERSION`.

## [0.1.0] - unreleased

First public preview.

### Added
- Launcher (pywebview): game detection (Steam, Epic, manual), plugin install/update/removal, extraction page,
  sanctuary card, Largos, multiplayer server list and host settings, options, recovery and redacted diagnostics,
  English/French interface, loading screens.
- BridgeHost local service with per-realm credentials: inventory transfers with crash recovery, travel mailbox, portal
  crossing broker.
- SR1 plugin (SRML): sanctuary island at the end of the Starlight Strand walkway, native-style plort statues recovered
  from the player's own game, gold gate, portal to SR2, travel overlay and vacpack carry.
- SR2 plugin (MelonLoader 0.7): return portal, travel, imported SR1 species and Largos.
- Extractor: SR2 → SR1 species and Largos (Saber, Batty, Flutter/Shadow/Yolky with curated diets) and SR1 → SR2
  (Glitch, Mosaic, Quantum, Quicksilver, Rad and their Largos), stable Largo ids shared by every install, content
  fingerprint for mixed servers. Bundled SlimeBridge-AssetConverter (`tools/SlimeBridge-AssetConverter`).
- Static GLB reader for user Largo imports.
- One-click Windows installer (`Install-SlimeBridge.cmd`), self-check, release script.

- Slime Rancher 2 1.x support (Unity 6, MelonLoader 0.7.3): SR2 plugin ported to the 1.x save, vacpack, currency and
  cutscene APIs; extractor reads the Addressables 2 binary catalog (`catalog.bin`) and skips 1.x radiant appearances and
  3D noise textures.
- Normal game installs: the launcher writes `<game>/SlimeBridge/content.json` so the plugins load the extracted content
  even when the game is started from Steam or Epic (same path containment as the developer LAB).
- Online: the Multi page installs/updates/removes SRMP Revival (bundled, GPL-3.0) and shows the content fingerprint;
  imported species ids are fixed in `bridge/content/species_ids.json` so every install agrees.

### Known limitations
- Slime Rancher 2 online is not available yet; SRMP two-player sessions with imported content still need a two-PC test.
- Creating a brand-new SR2 save from the portal is not available on SR2 1.x: link an existing SR2 save.
- The SR2 1.x Hyper slime and its Largos are not extracted yet (new renderer components).
