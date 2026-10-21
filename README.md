# SlimeBridge

A free, fan-made bridge between **Slime Rancher** and **Slime Rancher 2**: one launcher, a plugin for each game, a portal
island in SR1 that takes you to SR2 and back with your vacpack, and a local extractor that rebuilds the slimes and
Largos one game lacks **from your own installed games**.

> SlimeBridge ships **no Slime Rancher files**: no models, textures, sounds or game code. Everything that comes from the
> games is read from *your* installs, on *your* PC, and stays there.

*Français : voir [Installation (FR)](#installation-fr) plus bas.*

## What is in it

| Part | Folder | What it does |
|---|---|---|
| Launcher | `apps/launcher` | Window to find your games, install the plugins, extract content, build the island, play, travel, multiplayer settings, recovery tools |
| BridgeHost | `bridge/host` | Small local service (127.0.0.1 only) the two game plugins talk to: transfers, travel, portal crossing |
| SR1 plugin | `mods/sr1-bridge` | SRML mod: sanctuary island + plort statues + portal, travel, imported species |
| SR2 plugin | `mods/sr2-bridge` | MelonLoader mod: return portal, travel, imported species |
| Extractor | `tools/SlimeBridge-AssetConverter` + `bridge/content` | Reads your SR1/SR2 installs and builds the caches the plugins load |
| Online | launcher *Multi* page + SRMP Revival (`plugins/SRMP`) | Slime Rancher online through SRMP Revival (installed by the launcher), content fingerprint to compare with friends |

## Requirements

* Windows 10 or 11 (64-bit)
* **Slime Rancher** (1.4.4) with **[SRML](https://github.com/SlimeRancherModding/SRML/releases)** installed, and/or
  **Slime Rancher 2 (1.x, tested with 1.3.0)** with **[MelonLoader 0.7.3+](https://github.com/LavaGang/MelonLoader/releases)** installed (start SR2
  once after installing MelonLoader so it generates its files)
* Internet for the first install only (Python packages)

The installer takes care of Python 3.12 and the WebView2 runtime.

## Installation

1. Download `SlimeBridge-vX.Y.Z-win64.zip` from the [Releases](../../releases) page.
2. Extract it to a normal folder you own, e.g. `Documents\SlimeBridge` (**not** `Program Files`).
3. Double-click **`Install-SlimeBridge.cmd`**. It checks/installs Python 3.12 and WebView2, creates a private `.venv`,
   installs the dependencies, runs a self-check and puts a *SlimeBridge* shortcut on your desktop.
4. Start **SlimeBridge** (desktop shortcut or `SlimeBridge.cmd`).
5. In the launcher:
   * **Games**: your Steam/Epic games are found automatically; otherwise pick the game folders yourself.
   * Install the **SlimeBridge plugin** in each game (the button appears once SRML / MelonLoader is present).
   * **Extract**: reads your games and builds the missing slimes and Largos (a few minutes, runs in the background).
   * **Sanctuary**: reads your SR1 and places the island and portal.
   * **Play**.

Updating: extract the new zip over the old folder (your settings and extracted content in `Lab/` are kept) and run
`Install-SlimeBridge.cmd` again. Uninstalling: remove the plugins from the launcher's Games page, then delete the folder.

## Installation (FR)

1. Télécharge `SlimeBridge-vX.Y.Z-win64.zip` dans l'onglet [Releases](../../releases).
2. Dézippe-le dans un dossier à toi, par ex. `Documents\SlimeBridge` (**pas** dans `Program Files`).
3. Double-clique **`Install-SlimeBridge.cmd`** : il vérifie/installe Python 3.12 et WebView2, crée `.venv`, installe les
   dépendances, fait un auto-test et crée un raccourci *SlimeBridge* sur le bureau.
4. Lance **SlimeBridge** (raccourci ou `SlimeBridge.cmd`).
5. Dans le launcher : **Jeux** (détection auto Steam/Epic ou choix manuel du dossier) → installe le **plugin** dans chaque
   jeu (il faut SRML pour SR1, MelonLoader 0.7 pour SR2) → **Extraire** → **Sanctuaire** → **Jouer**.

Mise à jour : dézippe la nouvelle version par-dessus et relance `Install-SlimeBridge.cmd` (tes réglages et ton contenu
extrait dans `Lab/` sont conservés).

## Troubleshooting

* `Install-SlimeBridge.cmd` fails on Python: install Python 3.12 64-bit from python.org (tick *Add python.exe to PATH*)
  and run it again.
* The launcher window stays white: install the Microsoft Edge WebView2 runtime.
* `SlimeBridge.cmd --console` starts the launcher with a console so you can read errors.
* Plugin button greyed out: the mod loader is missing in that game folder (SRML for SR1, MelonLoader for SR2).
* The launcher's **Recovery** page collects a redacted diagnostics bundle you can attach to a bug report.

## Current state (honest)

Working on normal game installs (Steam, Epic or a copied folder): launcher, plugin install, extraction from your own
games, imported species and Largos in both games (registered in the real games: 122 in Slime Rancher, 40 in Slime
Rancher 2 1.3.0), SR1 sanctuary island with plort statues and portal, travel SR1 ⇄ SR2 with your vacpack, BridgeHost,
Slime Rancher online through SRMP Revival.

Not finished yet: Slime Rancher 2 online; creating a brand-new SR2 save from the portal (link an existing save); the new
SR2 1.x Hyper slime and its Largos are not extracted yet; a few Largo families still need diet decisions. Two-player
SRMP sessions with imported content are supported by design (shared ids and content fingerprint) but still need a real
two-PC test. See [CHANGELOG.md](CHANGELOG.md).

## For developers

```
Install-SlimeBridge.cmd -Dev                 # .venv + pytest
tools\run_tests.cmd                          # Python test suite (+ C# harnesses when a .NET SDK is present)
py -3.12 build_all.py --plugins-only         # build both plugins (needs the game assemblies, see below)
py -3.12 -m tools.release.make_release       # release zip in dist/ (see docs/RELEASING.md)
```

The plugins compile against the games' own assemblies, which are never committed: put a copy of each game under
`Lab/Games/SR1` and `Lab/Games/SR2` (`py -3.12 -m tools.lab.copy_game <install> Lab/Games/SR1`) or pass
`-p:SR1LabPath=<SR1 folder>` / `-p:SR2LabPath=<SR2 folder>` to `dotnet build`. Everything under `Lab/` is git-ignored.
Technical notes live in `docs/` (start with `docs/LAUNCHER_UI.md`, `docs/PACK_AND_PLAY.md`, `docs/SANCTUARY.md`,
`docs/TRAVEL.md`, `docs/LARGOS.md`).

Rules of the project: never commit game files, extracted assets, decompiled game code, saves or secrets; never modify an
original game folder from tooling; no DRM circumvention.

## Legal

SlimeBridge is unofficial Fan Content permitted under the
[Fan Content Policy](https://www.slimerancher.com/fan-content-policy). Not approved/endorsed by Monomi Park. Portions of
the materials used are property of Monomi Park, LLC. ©Monomi Park LLC. Please buy and support the games.

Third-party components and their licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [LICENSES.md](LICENSES.md).
