# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org/); the release script reads the section
whose title matches `VERSION`.

## [0.1.0] - 2026-10-22

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
- Standalone `SlimeBridge.exe` (everything inside), one-click source installer (`Install-SlimeBridge.cmd`), self-check, release script.

- Slime Rancher 2 1.x support (Unity 6, MelonLoader 0.7.3): SR2 plugin ported to the 1.x save, vacpack, currency and
  cutscene APIs; extractor reads the Addressables 2 binary catalog (`catalog.bin`) and skips 1.x radiant appearances and
  3D noise textures.
- Normal game installs: the launcher writes `<game>/SlimeBridge/content.json` so the plugins load the extracted content
  even when the game is started from Steam or Epic (same path containment as the developer LAB).
- Online for both games, installed together with the plugin: SRMP Revival for Slime Rancher (shipped), Ranching Together
  Beta 0.3.9 + Starlight 4.0.4 for Slime Rancher 2 (downloaded from their official GitHub releases, SHA-256 pinned;
  verified loading on SR2 1.3.0). Multi page: status per game, content fingerprint. Imported species ids are fixed in
  `bridge/content/species_ids.json` so every install agrees.
- New SR2 1.x Hyper slime and its 18 Largos imported into Slime Rancher, with their sunglasses and fire aura (rebuilt with
  Slime Rancher's own fire particles, glow and a point light, since SR2's aura shader does not exist there).
- Base species specs ship with SlimeBridge (`bridge/content/specs`, data only) and are seeded on a fresh install; the
  Largo specs are rebuilt from the player's own game folders.

### Known limitations
- Two-player sessions with imported content still need a two-PC test (both games).
- Creating a brand-new SR2 save from the portal is not available on SR2 1.x: link an existing SR2 save.
