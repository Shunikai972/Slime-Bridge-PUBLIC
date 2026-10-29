# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org/); the release script reads the section
whose title matches `VERSION`.

## [0.1.8] - 2026-10-29

### Fixed
- The portal said "SlimeBridge is not answering" and no portal appeared in Slime Rancher 2 when the launcher window had
  been closed after pressing Play: closing it stopped BridgeHost. While a game runs, closing the window now only hides it
  (the launcher closes by itself when the games are closed), and starting SlimeBridge again brings that window back.
- The Slime Rancher 2 portal is built in every game with the plugin (it explains how to travel when SR2 was not started
  from SlimeBridge), and a failed crossing request from SR2 now says so instead of nothing.
- A portal crossing was refused ("confirm the backup reminder") when that reminder had not been ticked: accepting the
  welcome card counts, and Play no longer shows a second reminder.
- With several copies of a game and none chosen, the newest version is used (an old Slime Rancher 2 0.6 copy no longer
  wins over 1.x).

### Added
- Launch both games with the one you want in front (the other waits minimized): "Both · SR1 in front" / "Both · SR2 in
  front" in the Play choice, and "+ SR2 in the background" under each game on the home page.
- Help & console page: what needs attention, a live console of both games and the launcher (filters, pause, copy), and
  the full check to copy. It replaces the Recovery and Debug pages.
- New welcome screen on first start (artist drawing, three short points, one button); the sidebar art is now an artist's
  drawing of the pink and cotton slimes.
- Island statue names use the game's own font with an outline and turn towards the player.

### Changed
- Simpler launcher: the home page no longer repeats the side menu, the bottom bar keeps only the game choice and Play,
  recovery messages are translated.

## [0.1.7] - 2026-10-28

### Fixed
- Travelling through the portal did nothing:
  - the crossing only advanced while the launcher window ran its timers, which Windows slows to about once a minute when
    the window is hidden behind a game. It now runs on its own thread, several times a second;
  - Slime Rancher 2 1.x, started as the destination, crashed on its main menu while checking for a current game
    (an empty value the game's interop cannot read), so it never loaded the save;
  - the first trip of a save waited for a choice in the hidden launcher window.
- A trip that cannot happen now says why in the game (for example "start the game from SlimeBridge" or "Slime Rancher 2 is
  open on another game"), and walking into the portal answers at once ("The portal opens…", or how many statues are filled).
- The portal back to Slime Rancher is open for a player who arrived through it (it no longer asks for 250,000 newbucks first).

### Added
- No Slime Rancher 2 save for this ranch: one is created automatically (the game's own New Game, default settings, first
  free slot), its opening story is played through by itself once the world is loaded, the player comes out at the
  portal, and the two saves stay linked. The other game is minimized, muted and slowed down while you are away.
  Setting "First trip with a save": AUTO (default) or ASK (the old dialog).
- Launcher home: pick the game to start (Slime Rancher or Slime Rancher 2), and "Continue where you were" starts the game
  you were last in, straight on the save you were playing.
- Island statues: a small copy of the slime each statue wants stands in front of it, with its name in the game's language.
- Launcher layout: nothing hidden or cut at the default and minimum window sizes (the bottom bar no longer covers the page,
  the side menu fits, the Fan Content notice is shown in full).

## [0.1.6] - 2026-10-27

### Fixed
- Extracting from Slime Rancher 2 failed for every species ("failed to dump nodes raw") when the game had never been
  started with MelonLoader: the extractor needs the code schema MelonLoader makes on the first start. Extract now makes
  it itself when it is missing, exactly like MelonLoader (same Cpp2IL release from its official GitHub page, sha256
  pinned, same plugin and command line), in about 15 seconds, cached per game build in `Lab/Cache/cpp2il`.
- A failed species shows its reason on the same log line ("FAILED: ...") instead of only at the end.

## [0.1.5] - 2026-10-27

### Added
- Debug page: one button checks everything SlimeBridge needs on this PC (versions, Windows long-path setting, disk space,
  detected and selected games, missing game files and repair archives, mod loader, plugin, online, the SR2 code schema,
  island, MelonLoader and game logs, antivirus detections touching a Slime folder, network, last extraction log) and
  gives a text to copy and send. Read-only.
- Updates: Settings > Updates checks the latest GitHub release (also at start-up) and "Update and restart" downloads
  the standalone build, verifies it against the release's SHA256SUMS.txt, replaces the program files (your data in
  Lab/ is kept) and reopens the launcher. `SlimeBridge.exe --self-update` does the same from a terminal.
- Linux build: every published release also gets `SlimeBridge-vX.Y.Z-linux-x64-standalone.tar.gz`, built on GitHub
  Actions. The launcher window uses GTK/WebKit there and Steam libraries under `~/.steam` / Flatpak are searched.

### Fixed
- "Details" under an extraction (and the download / island boxes) did not open: the box is redrawn several
  times a second while a job runs and closed it again. Opened sections now stay open and follow the newest lines
  (last 200 instead of 40).
- The island (sanctuary + portal) was "not present" on every PC but the developer's: its kit (a description of native
  Slime Rancher objects - names and positions, no game data) now ships with SlimeBridge, so the island is built on the
  player's PC and installed with the SR1 plugin and before Play.

## [0.1.4] - 2026-10-27

### Fixed
- An incomplete game folder no longer fails deep inside the extraction ("FileNotFoundError ... resources.assets"). The
  launcher checks the game's core files first: the game card shows "Game folder incomplete" with what is missing, and
  the extraction stops with a clear reason. A folder named like a Google Drive split download
  (`...-20261027T155201Z-1-001`) is recognised and the message says to extract ALL the zip parts into the same folder.
- One-click repair: when the game's own archives are on the PC (the other parts of a split download, or the original
  zip in Downloads), the missing files are extracted from them automatically - when the extraction starts, or with
  "Repair the game" on the game card. Only missing files are written, long paths included (what Windows' extractor skips).
- A game folder too deep for Windows (files beyond 260 characters while long paths are off) is reported, with a
  "Move the game to <drive>:\Jeux\..." button: a rename on the same drive, and the launcher follows the new folder.

## [0.1.3] - 2026-10-27

### Added
- The launcher installs the mod loader itself: SRML 0.2.1c for Slime Rancher (its official installer, run unattended) and
  MelonLoader 0.7.3 for Slime Rancher 2, downloaded from the projects' official GitHub releases and checked against a
  pinned SHA-256. It happens automatically with "Install the plugin" and before Play, and the game card has an
  "Install SRML" / "Install MelonLoader" button while the loader is missing.

### Fixed
- A MelonLoader installed under another proxy name (`winhttp.dll`, `winmm.dll`, ...) or layout is now recognised.

## [0.1.2] - 2026-10-27

### Fixed
- Game detection and manual folder selection for Slime Rancher 2:
  - Auto-detection now scans Windows redirected shell folders (`Downloads`, `Desktop`, `Documents`, `Games`, `Jeux`) and
    user folders on secondary drives, as well as nested archive wrapper folders (`Slime.Rancher.2.v1.3.0/.../Slime Rancher 2`).
  - Manual folder selection now prioritizes Slime Rancher folders inside crowded parent folders (such as `Downloads`),
    recognizes the game when a subfolder (`SlimeRancher2_Data`, `D3D12`, etc.) is selected, and immediately sets the
    chosen folder as the active installation for that game.
  - Recognized Slime Rancher 2 1.3.0 (`Unity 6000.0.73f1`) in `SlimeBridge-AssetConverter` and seeded the shipped
    species import specs automatically when running extraction on a fresh install.

## [0.1.1] - 2026-10-26

### Fixed
- The standalone `SlimeBridge.exe` did not open on PCs that downloaded the zip ("Failed to resolve
  Python.Runtime.Loader.Initialize"): Windows marks extracted files as downloaded and .NET refused to load the launcher
  window's DLLs. The exe now clears that mark on its own files at start-up; the source installer unblocks its folder.
- A start-up failure now shows a readable message and writes `SlimeBridge-crash.log` instead of a raw traceback.
- The self-check also tests the .NET bridge and the WebView2 window backend, so this class of error shows up there.

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
