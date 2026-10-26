# Local reconnaissance — October 3, 2026

Source installs are READ-ONLY. The old remaster test copy `<local folder>` is outside this project and untouched. The AssetRipper export `<AssetRipper export>` is reference only.

## Detection (verified with `py -3.12 -m apps.launcher.main --known-local`)

| Game | Source | Store | Unity | Runtime | Notes |
|---|---|---|---|---|---|
| SR1 | `C:\Program Files\Epic Games\SlimeRancher` | Epic (`Corydalis`) | 2019.4.29f1 | Mono | no loader detected |
| SR1 | `<SR1 install>` | manual | 2019.4.29f1 | Mono | NOT vanilla: patched `Assembly-CSharp.dll`, SRML 0.2.1.0, SRMP Revival in `SRML/Mods`; `globalgamemanagers` app version `1.4.3` |
| SR2 | `<SR2 install>` | manual | 2021.3.28f1 | IL2CPP | app version `0.6.2`; no loader before the lab install |

No Steam SR1/SR2 and no Epic SR2 install exists on this machine.

## Fingerprints (SHA-256; full values in each lab's `slimebridge-lab.json`)

- SR1 manual: `SlimeRancher.exe` `ebea48bc…c587`; patched `Assembly-CSharp.dll` `65eb0e11…9a2b` (Epic's unpatched copy is `75c67a45…8a43`).
- SR2: `SlimeRancher2.exe` `f600f01e…b03b`, `GameAssembly.dll` `91f63654…0465`, `global-metadata.dat` `50962da1…efe9`.

## Labs (`Lab/Games`, Git-ignored, full independent copies made by `tools/lab/copy_game.py`)

- `Lab/Games/SR1`: copy of the manual SR1 (SRML already present). SlimeBridge is deployed to `SRML/Mods/SlimeBridge/`; SRML scans `SRML/Mods` recursively (`SRML/FileSystem.cs` `ModPath`).
- `Lab/Games/SR2`: copy of the manual SR2 plus MelonLoader v0.7.3 (`version.dll` + `MelonLoader/`). SlimeBridge is deployed to `Mods/`.

## Shared profile folders (isolation caveat)

The lab copies still use the per-user Unity folders `%USERPROFILE%\AppData\LocalLow\Monomi Park\Slime Rancher` (SR1) and `...\LocalLow\MonomiPark\SlimeRancher2` (SR2), plus `HKCU\Software\Monomi Park\Slime Rancher` and `HKCU\Software\MonomiPark\SlimeRancher2`. Snapshots taken before any lab launch: `um backup` sets `slimebridge-prelab-sr1` and `slimebridge-prelab-sr2` in a local backup folder. After the SR1 oracle runs, the backup diff showed only `Player.log`/`Player-prev.log` changes and Unity analytics churn; no save file changed. Redirecting saves for the lab is still a TODO before any test that loads or writes a save.

## Loader/API facts used so far

- SRML 0.2.1.0 (`SRML/IModEntryPoint.cs`): `ModEntryPoint` with `PreLoad/Load/PostLoad/Update/Unload`. `Update` runs only after loading finishes (`SRModLoader.UpdateMods`). The `modinfo.json` `version` must be `X.Y[.Z]` integers only (`SRMod.cs` `ModVersion.Parse`); `0.1.0-dev` made SRML reject the mod at PreLoad (seen in `Player.log`, fixed and covered by `tests/test_plugin_manifests.py`).
- MelonLoader 0.7.3: IL2CPP mods target `net6.0` and reference `MelonLoader/net6/MelonLoader.dll`; `[MelonInfo]` + `[MelonGame("MonomiPark","SlimeRancher2")]`; `MelonMod.OnInitializeMelon/OnUpdate/OnDeinitializeMelon`. The first boot downloads and installs a .NET runtime, then generates Il2Cpp interop assemblies (slow, one time).
- Starlight v4.0.4 (2026-09-24) is an optional SR2 expansion API, not the bootstrap loader. The public developer guide documents its extension model. Its Starlight-SAL restricts source and DLL redistribution and reuse of its source; work against public API docs, not copied Starlight implementation. It is not installed; the heartbeat plugin does not need it. Its guide describes SR2 1.2.0 moving to Unity 6, whereas this lab's v0.6.2 runs Unity 2021.3.28f1. Compatibility of current Starlight with this older game is UNPROVEN.
- Ranching Together (SR2MP) README pins MelonLoader 0.7.3 and Starlight 4.0.3; its source `BuildInfoConstants.cs` targets SR2 1.3.0. This is NOT evidence that the present local v0.6.2 can run its current release. Its `ApiHandlers.RegisterMod(MelonMod, ModSide)` is a potential network integration surface for a future matching game version, not a validated dependency for the lab. Do not install it in the v0.6.2 lab without an explicit compatibility test.
- Current references: https://github.com/ThatFinn/Starlight/blob/v4.0.4/LICENSE.md ; https://starlight.sr2.dev/dev/getting-started ; https://github.com/pyeight/SlimeRancher2Multiplayer/blob/619bc4cf871a13dbaee2aef3309f77219efa68dc/SR2MP/BuildInfoConstants.cs .

## Phase-94 API findings (not runtime-validated in a loaded save)

- SR1 exact lab Mono assembly: `SceneContext.PlayerState.GetAmmo(PlayerState.AmmoMode.DEFAULT)` and usable slots; sum all slots matching `PINK_SLIME=3` or `PINK_PLORT=6`. `GetCount(id)` alone sees only the first matching slot. Loaded internal save identity: `GameContext.AutoSaveDirector.SavedGame.GetName()` after `IsLoadingGame()` becomes false. Health-positive is only an approximation of player alive, not a death-state guarantee.
- SR2 v0.6.2 local Il2Cpp interop: `SceneContext.PlayerState.Ammo.Slots` has `Id.ReferenceId`, `Count`, `IsUnlocked`; `GameContext.AutoSaveDirector.loadedGame` and `_currentGameId` are available. The `SlimeDefinition.Pink` / `IdentifiableType.PinkPlort` reference strings and save-ID behavior require a loaded-save check before they can become verified mappings. SR2MP/Starlight current releases target newer game versions and are not installed in this lab.
- Both plugins sample only on the Unity/Melon update thread and send primitive status over the already-proven loopback heartbeat. SR2 deliberately omits Pink counts while the native mappings remain unverified; a missing count is not zero. Source-signature verification and compilation are **not** evidence that loaded-game status is correct.

## SR1 multiplayer reference

`<local folder>` is read-only. Branch `main` at `f6765f4`, origin `https://github.com/Shunikai972/Slime-rancher-multiplayer-public-mode.git`; remote branches `ci/github-actions`, `fix/ci-game-references`, `fix/runtime-disconnect-guards`, `revival/2026-modernize-installer`. Uncommitted local changes there (`.gitignore`, `.serena/project.yml`, untracked remaster folders) are left alone. The lab SR1 also carries SRMP runtime data: `SRMP/autohost.json` is a headless autohost config (`PauseWhenNoPlayers`, `IdleTargetFrameRate`, `ShutdownRequestFile`, autosave interval). Detailed diff: `docs/SR1_LOCAL_SRMP_DIFF.md`. The SRMP host remains a full Unity game process, not a standalone world simulator; actual `-batchmode -nographics` success is still unverified.
