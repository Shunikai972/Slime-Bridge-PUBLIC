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

### Known limitations
- In-game multiplayer networking is not connected yet (server core, list and fingerprint only).
- Imported species are armed only in a developer LAB copy of the game (`Lab/Games/<SR1|SR2>`); player installs get the
  plugin, the island, the portal and travel.
- Creating a brand-new SR2 save from the portal is not available: link an existing SR2 save.
