# Launcher interface (v0.3)

`python -m apps.launcher.desktop` (or `SlimeBridge.cmd`) opens the SlimeBridge launcher: a pywebview window (WebView2) showing
`apps/launcher/ui/`. The page is plain HTML/CSS/JS with no build step; the Python side is `LauncherAPI` in `apps/launcher/desktop.py`.

## Layout

A logo header (profile chip with BridgeHost status and menu, custom window controls), a dark sidebar, one bright container and a persistent
bottom dock: **GAME** selector (Slime Rancher / Slime Rancher 2 / Both · Bridge), **INSTALLATION** path with a folder button, a big **PLAY**
button (menu: play either game or both, open the Games page) and a status line (readiness, loader, progress of extraction/downloads).

| Page | What it does |
|---|---|
| Home | carousel (official key art + the latest news), **Latest News** box, three shortcut tiles |
| News | every news card of the official site |
| Games | auto-detect (Steam registry + every library, Epic manifests, GOG and usual folders on every drive; wrapper folders up to 3 levels), choose a folder or `.exe`, loader/plugin state, store links ("you must own the games") |
| Extract | drives the local SlimeBridge-AssetConverter (`sbconv serve`): direction, **only what is missing** (recommended) or everything, slimes/largos, live progress, install into the plugin asset cache |
| Largos | the 24 cross-game Largo models: manifest URL, check, download one/all, import your own `<id>.glb` folder |
| Multi | join (address checked and remembered, favourites), host settings, BridgeHost start/stop (in-game networking is **not connected yet**, and the page says so) |
| Settings | launcher preferences (language auto/en/fr, window frame, calmer animations, backup reminder), folders, and every SlimeBridge setting from `bridge/core/launcher_settings.py` |
| About (profile menu) | non-affiliation notice, what the launcher does with files, Fan Content Policy, credits |

## Loading screens

`js/loading.js` + `css/loading.css`: one Slime-Rancher-2-style screen used twice. At start-up it shows the cached official key art under a dark
veil, the wordmark, a chunky green bar with a real slime hopping on it, steps ("Reading your installations…") and rotating tips. When the player
presses PLAY (after the one-time backup reminder) it comes back as **Launching Slime Rancher 2** (checking → BridgeHost → game), then "Have fun,
rancher!" and closes by itself; errors stay on screen with a Close button. It carries the Fan Content Policy notice, like the footer.
This is the only SlimeBridge launcher: the older Qt window (`apps/slimebridge`) was removed and its two real features, **Saves** (profiles and linked save pairs, read-only) and **Recovery** (plain-language issues + a redacted diagnostics export), now live here (`apps/launcher/recovery.py`, `ui/js/pages_recovery.js`). Its Content/Mods/Updates/Convert pages only ever showed "not connected" placeholders; extraction and Largos are the Extract and Largos pages. Demo URL switches: `?open=splash|launching|launch`.

## Official news and artwork (loaded at every start)

* `apps/launcher/news.py` reads the news cards of **https://www.slimerancher.com/** (title, date, summary, picture, link) and discovers key art,
  screenshots and slime cut-outs on that page and on monomipark.com (`/`, `/games/`, `/press`). It runs once per start in the background
  (`news_refresh`); the last good result is shown instantly from the cache (`news_cached`) and is used offline (a notice says so).
* Pictures come from the sites' own CDN (`a.storyblok.com` / `img2.storyblok.com`), resized and WebP-compressed by the CDN, and are cached under
  `Lab/Launcher/cache/` (git-ignored). **Nothing from Monomi Park is bundled or committed.** If the site cannot be reached, the launcher falls back to
  its own code-drawn slimes and landscape.
* Only those two page hosts and two CDN hosts are contacted (https, short timeout, size caps, a clear User-Agent). Page text is stripped of
  markup; links open only on an allowlist (Monomi Park sites, its shop/support/Discord, Steam).
* Their logos are never used, and no art that has the game logo baked in.

### Fan Content Policy compliance (https://www.slimerancher.com/fan-content-policy)

* The required notice is shown permanently in the footer, next to every use of their materials: "SlimeBridge is unofficial Fan Content permitted
  under the Fan Content Policy. Not approved/endorsed by Monomi Park. Portions of the materials used are property of Monomi Park, LLC. ©Monomi Park LLC."
* The product name is **SlimeBridge** (the brand name is not the first word), the launcher is free and non-commercial, and it never implies approval.
* Re-read the policy before publishing anything: Monomi Park may change it at any time, and asked to see the final product.

## State and settings

* `Lab/Launcher/installations.json` : `manual_paths`, `detected_paths`, `legal_understood`, `language`,
  `reduce_motion`, `backup_ack`, `native_frame`, `play_target`, `prefer_lab`, `selected`, `converter_path`, `converter_output`, `cache_dir`,
  `models_dir`, `largo_manifest_url`, `mp_host`. Written atomically; links/junctions are refused; preferences are validated one by one.
* `Lab/Launcher/settings.json`: the shared SlimeBridge settings schema (`bridge.app.settings`), edited from Settings.
* Largo models: `Lab/Largos/models/<id>/`; extraction output: `Lab/Converter/` (never inside a game folder).

## Safety rules (tested in `tests/test_launcher_backend.py` and `tests/test_launcher_news.py`)

* The launcher only starts the game executables found by discovery, plus its own BridgeHost and the converter. It never touches saves;
  the first launch shows a backup reminder (can be turned off).
* Game folders are read-only; the extraction output folder is refused when it is inside any known game folder.
* Model downloads: manifest `slimebridge-largo-models` v1, https only, size caps, SHA-256 checked before unpacking, file-type allowlist, no
  `..`/absolute/link entries, atomic install. Nothing is downloaded until the player clicks, and no source is configured by default.
* No telemetry, no external script or font: the page's CSP is `default-src 'self'` (pictures arrive as `data:` URIs through the Python side).

## Developing the UI

* Open `apps/launcher/ui/index.html` in a browser: without pywebview the page runs on a labelled **DEMO** backend (`js/api.js`) with generated placeholder
  pictures; nothing is read or written. URL switches: `?job=running|done|failed`, `?models=1`, `?games=none`, `?host=1`, `?open=legal|launch|menu|playmenu`, `?shot`.
* `tools/ui_shot.sh <page> <out.png> [w h theme lang switches]` renders the demo page with headless Edge.
* `python tools/ui_snap.py OUT_DIR [pages...] [--lang fr] [--tag name] [--eval "js"]` opens the **real** window with the real backend (real news and art)
  and screenshots it (Windows + Pillow; uses a throw-away config folder, starts no game).
* `python -m apps.launcher.desktop --smoke-ui` opens the window, checks the game cards and visits every page, failing on any JavaScript error.

Art: the header logo, slimes/largos/plorts used offline, and the landscape are code-drawn originals (`js/art.js`, `js/scene.js`); typography is
Fredoka and Nunito (OFL, bundled with their licenses).
