"""SlimeBridge desktop launcher: pywebview shell + the Python API the page talks to.

Everything the page may do is a public method of ``LauncherAPI`` (state is kept in underscore attributes so pywebview
exposes methods only). The launcher never modifies a game folder, never touches a save, and only starts what the
player asks for: the game executables found by discovery, their own BridgeHost, and the local asset converter.
"""

import json
import os
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from apps.launcher import detect, extract, models, news, pack, plugins, sanctuary, savelist, travel, windows
from apps.launcher.installations import discover_manual
from apps.launcher.main import KNOWN_MANUAL_SR1, KNOWN_MANUAL_SR2, available_installations
from apps.launcher.runtime import EXECUTABLES, GameLauncher, HostProcess, RuntimeErrorWithCode
from tools.lab.paths import LAB_GAMES, refuse_reparse, validate_lab_destination

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / 'Lab' / 'Launcher' / 'installations.json'
UI = Path(__file__).resolve().parent / 'ui' / 'index.html'
VERSION = '0.2-ui'
STORE_PAGES = {'SR1': 'https://store.steampowered.com/app/433340/', 'SR2': 'https://store.steampowered.com/app/1657630/'}
CONFIG_DEFAULTS = {'manual_paths': [], 'detected_paths': [], 'legal_understood': False, 'language': 'auto', 'theme': 'day',
                   'reduce_motion': False, 'backup_ack': False, 'prefer_lab': False, 'selected': {},
                   'native_frame': False, 'play_target': 'BOTH', 'converter_path': None, 'converter_output': None, 'cache_dir': None,
                   'largo_manifest_url': None,
                   'models_dir': None, 'extract_ram_gb': None, 'performance': 'off', 'mp_host': {'name': 'My Ranch', 'port': 7777, 'mode': 'HYBRID'}}
_VALIDATORS = {
    'language': lambda v: v in ('auto', 'en', 'fr'),
    'theme': lambda v: v in ('day', 'dusk'),
    'reduce_motion': lambda v: isinstance(v, bool),
    'backup_ack': lambda v: isinstance(v, bool),
    'prefer_lab': lambda v: isinstance(v, bool),
    'native_frame': lambda v: isinstance(v, bool),
    'play_target': lambda v: v in ('SR1', 'SR2', 'BOTH'),
    'performance': lambda v: v is None or v in ('off', 'balanced', 'fast'),
    'converter_path': lambda v: v is None or (isinstance(v, str) and 0 < len(v) <= 1024),
    'converter_output': lambda v: v is None or (isinstance(v, str) and 0 < len(v) <= 1024),
    'cache_dir': lambda v: v is None or (isinstance(v, str) and 0 < len(v) <= 1024),
    'models_dir': lambda v: v is None or (isinstance(v, str) and 0 < len(v) <= 1024),
    'extract_ram_gb': lambda v: v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and extract.MIN_RAM_GB <= v <= extract.MAX_RAM_GB),
    'largo_manifest_url': lambda v: v is None or (isinstance(v, str) and len(v) <= 500 and models._url_allowed(v)),
}


GUI = 'edgechromium' if sys.platform == 'win32' else 'gtk'      # pywebview backend per platform

class LauncherAPI:
    def __init__(self, config_path: Path = CONFIG, known_paths: tuple[Path, ...] = (KNOWN_MANUAL_SR1, KNOWN_MANUAL_SR2),
                 lab_games: Path = LAB_GAMES, *, popen=None, host_popen=None, opener=None, news_opener=None):
        self._config_path = config_path
        self._known_paths = known_paths
        self._lab_games = lab_games
        self._window = None
        self._launcher = GameLauncher(**({'popen': popen} if popen else {}))
        self._host = HostProcess(Path(config_path).parent / 'host', **({'popen': host_popen} if host_popen else {}))
        self._opener = opener
        self._feed = news.NewsFeed(Path(config_path).parent / 'cache', **({'opener': news_opener} if news_opener else {}))
        self._maximized = False
        self._extract = None
        self._sanctuary = None
        self._travel = travel.TravelCoordinator(self._host, self._launcher, self._primary, lambda: bool(self._load()['backup_ack']),
                                                self._window_control(), travel.TravelLinks(Path(config_path).parent / 'travel-links.json'),
                                                settings=lambda: self._settings()[1]['bridge'], list_saves=savelist.list_groups,
                                                notify=lambda _transition: self._raise_window())
        self._downloads = None
        self._manifest = {'doc': None, 'error': None}
        self._lock = threading.Lock()

    # ---- configuration (installations.json: game folders, legal notice, preferences) -----------------------------
    def _load(self) -> dict:
        if not self._config_path.exists():
            return json.loads(json.dumps(CONFIG_DEFAULTS))
        data = json.loads(self._config_path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('manual_paths'), list) or not all(
                isinstance(path, str) for path in data['manual_paths']):
            raise ValueError('Invalid launcher configuration; original file preserved')
        merged = json.loads(json.dumps(CONFIG_DEFAULTS))
        merged.update({key: value for key, value in data.items() if key in CONFIG_DEFAULTS or key == 'legal_understood'})
        return merged

    def _save(self, data: dict) -> None:
        refuse_reparse(self._config_path)
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._config_path.with_suffix('.tmp')
        refuse_reparse(temp)
        temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        refuse_reparse(self._config_path)
        os.replace(temp, self._config_path)

    def _lab_root(self) -> Path:
        return Path(self._config_path).parent.parent

    def _models_dir(self, data=None) -> Path:
        configured = (data or self._load()).get('models_dir')
        return Path(configured) if configured else self._lab_root() / 'Largos' / 'models'

    def _output_dir(self, data=None) -> Path:
        configured = (data or self._load()).get('converter_output')
        return Path(configured) if configured else self._lab_root() / 'Converter'

    def _cache_dir(self, data=None) -> Path:
        configured = (data or self._load()).get('cache_dir')
        return Path(configured) if configured else self._lab_root() / 'AssetCache'

    def manual_paths(self) -> list[Path]:
        return [Path(path) for path in self._load()['manual_paths']]

    def set_preference(self, key: str, value) -> dict:
        """Validated launcher preference (language, theme, converter folder, largo manifest URL, ...)."""
        if key not in _VALIDATORS:
            raise ValueError(f'unknown preference {key!r}')
        if value == '':
            value = None
        if not _VALIDATORS[key](value):
            raise ValueError(f'invalid value for {key}')
        data = self._load()
        data[key] = value
        self._save(data)
        if key == 'largo_manifest_url':
            self._manifest = {'doc': None, 'error': None}
        return {'ok': True, 'key': key, 'value': value}

    # ---- installations --------------------------------------------------------------------------------------------
    @staticmethod
    def _installation_info(item, role: str = 'source') -> dict:
        root = item.root
        from apps.launcher import loaders
        loader = loaders.describe(item.game, root)
        if item.game == 'SR1':
            bridge = (root / 'SRML' / 'Mods' / 'SlimeBridge' / 'SlimeBridge.SR1.dll').is_file()
        else:
            bridge = (root / 'Mods' / 'SlimeBridge.SR2.dll').is_file()
        from apps.launcher import integrity
        return {'game': item.game, 'root': str(root), 'store': item.store, 'runtime': item.unity_runtime,
                'integrity': LauncherAPI._integrity(item.game, root),
                'loader': loader, 'bridge_plugin': bridge, 'plugin_state': plugins.status(item.game, root)['state'],
                'version': 'Not fingerprinted', 'role': role}

    def _perf_env(self) -> dict:
        """The player's performance profile (Settings): passed to the plugin, which only touches quality knobs when it is not 'off'."""
        mode = self._load().get('performance')
        return {'SLIMEBRIDGE_PERF': mode} if mode in ('balanced', 'fast') else {}

    def _lab_installations(self) -> list[dict]:
        """Isolated lab copies are where Bridge plugins are deployed; report them distinctly."""
        result = []
        for realm in ('SR1', 'SR2'):
            exe = self._lab_games / realm / ('SlimeRancher.exe' if realm == 'SR1' else 'SlimeRancher2.exe')
            try:
                validate_lab_destination(exe, self._lab_games)
            except ValueError:
                continue  # Never label a junction into an original install as a lab copy.
            item = discover_manual(self._lab_games / realm, store='lab')
            if item is not None and item.game == realm:
                result.append(self._installation_info(item, role='lab'))
        return result

    def _installations(self) -> list[dict]:
        data = self._load()
        paths = [*(Path(p) for p in data['manual_paths']), *(Path(p) for p in data['detected_paths']), *(p for p in self._known_paths if p)]
        lab_root = self._lab_games.resolve()
        installations = [item for item in available_installations(paths)
                         if item.root != lab_root and lab_root not in item.root.parents]
        return [self._installation_info(item) for item in installations] + self._lab_installations()

    def get_overview(self) -> dict:
        data = self._load()
        return {'installations': self._installations(),
                'legal_understood': bool(data.get('legal_understood', False)),
                'lab_isolation': 'Not configured — game launch disabled',
                'phase': 'Foundation: both lab plugins reached one BridgeHost; no portal or transfer yet'}

    def add_manual_path(self, path: str) -> dict:
        item = detect.resolve_install(path)
        if item is None:
            raise ValueError('Not a compatible SR1/SR2 executable or game folder')
        data = self._load()
        paths = data['manual_paths']
        if str(item.root).casefold() not in {str(p).casefold() for p in paths}:
            paths.append(str(item.root))
        data['selected'][item.game] = str(item.root)
        self._save(data)
        return self._installation_info(item)

    @staticmethod
    def _integrity(game, root):
        from apps.launcher import integrity
        state = integrity.check(game, root)
        state['repairable'] = sum(c['missing'] for c in integrity.repair_candidates(game, root)) if state['missing_count'] else 0
        return state

    def update_check(self) -> dict:
        """Is there a newer release on GitHub? {'current','latest','update','frozen'}."""
        from apps.launcher import updater
        try:
            return {'ok': True, **updater.check()}
        except updater.UpdateError as exc:
            return {'ok': False, 'error': exc.code, 'detail': exc.detail}

    def update_apply(self) -> dict:
        """Download + verify the latest standalone build, then restart into it (the window closes, the new one opens)."""
        from apps.launcher import updater
        if any(self._launcher.running(r) for r in ('SR1', 'SR2')):
            return {'ok': False, 'error': 'GAME_RUNNING'}
        try:
            ready = updater.prepare()
            helper = updater.restart_into(ready['folder'])
        except updater.UpdateError as exc:
            return {'ok': False, 'error': exc.code, 'detail': exc.detail}
        import threading

        def _quit():
            import time
            time.sleep(1.0)                    # let the page show "restarting"
            try:
                self.shutdown()
            finally:
                if self._window is not None:
                    try:
                        self._window.destroy()
                    except Exception:  # noqa: BLE001
                        pass
                os._exit(0)
        threading.Thread(target=_quit, daemon=True).start()
        return {'ok': True, 'version': ready['version'], **helper}

    def diagnostics(self) -> dict:
        """Plain-text report of everything that matters (Debug page, 'Copy' button). Read-only."""
        from apps.launcher import diagnostics
        logs = None
        if self._extract is not None:
            logs = self._extract.snapshot(log_tail=400).get('logs')
        if not logs and extract.LAST_LOG.is_file():
            logs = extract.LAST_LOG.read_text(encoding='utf-8', errors='replace').splitlines()
        return {'ok': True, 'text': diagnostics.report(self, logs)}

    def game_repair(self, realm: str) -> dict:
        """One click: put back missing game files from the game's own zips found on this PC, or move a too-deep
        install to a short folder on the same drive (and point the launcher at it)."""
        if realm not in ('SR1', 'SR2'):
            raise ValueError('realm must be SR1 or SR2')
        primary = self._primary(realm)
        if primary is None:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        if self._launcher.running(realm):
            return {'ok': False, 'error': 'GAME_RUNNING'}
        from apps.launcher import integrity
        root = primary['root']
        state = integrity.check(realm, root)
        done = {}
        try:
            if state['missing_count']:
                done = integrity.repair(realm, root)
                state = done['state']
            if state.get('too_long') and not state['missing_count']:
                new_root = integrity.move_short(realm, root)
                self.add_manual_path(str(new_root))
                done['moved_to'] = str(new_root)
                state = integrity.check(realm, new_root)
        except OSError as exc:
            return {'ok': False, 'error': 'REPAIR_FAILED', 'detail': str(exc)}
        return {'ok': state['complete'], 'state': state, **{k: v for k, v in done.items() if k != 'state'},
                'error': None if state['complete'] else ('STILL_INCOMPLETE' if state['missing_count'] else 'STILL_TOO_LONG')}

    def select_manual(self, kind: str = 'folder') -> dict | None:
        if self._window is None:
            raise RuntimeError('File picker requires the desktop window')
        import webview
        if kind not in ('folder', 'exe'):
            raise ValueError('Select a folder or executable')
        dialog = webview.FileDialog.FOLDER if kind == 'folder' else webview.FileDialog.OPEN
        chosen = self._window.create_file_dialog(dialog, file_types=('Game executable (*.exe)',) if kind == 'exe' else ())
        return self.add_manual_path(chosen[0]) if chosen else None

    def autodetect(self) -> dict:
        """Look for the games in Steam / Epic / GOG / common folders; remember what was verified."""
        found = detect.auto_detect()
        data = self._load()
        known = {str(p).casefold() for p in data['detected_paths'] + data['manual_paths']}
        added = []
        for item in found:
            if str(item.root).casefold() not in known:
                data['detected_paths'].append(str(item.root))
                known.add(str(item.root).casefold())
                added.append(self._installation_info(item))
        if added:
            self._save(data)
        return {'ok': True, 'found': len(found), 'added': added, 'installations': self._installations()}

    def forget_game(self, realm: str) -> dict:
        """Stop using the remembered folders for one game (files on disk are never touched)."""
        if realm not in EXECUTABLES:
            raise ValueError('realm must be SR1 or SR2')
        data = self._load()
        def keep(path):
            item = discover_manual(path) or detect.resolve_install(path)
            return item is None or item.game != realm
        data['manual_paths'] = [p for p in data['manual_paths'] if keep(p)]
        data['detected_paths'] = [p for p in data['detected_paths'] if keep(p)]
        data['selected'] = {k: v for k, v in data['selected'].items() if k != realm}
        self._save(data)
        return {'ok': True, 'installations': self._installations()}

    def acknowledge_legal(self) -> dict:
        data = self._load()
        data['legal_understood'] = True
        self._save(data)
        return {'understood': True}

    def _primary(self, realm: str):
        rows = [r for r in self._installations() if r['game'] == realm]
        data = self._load()
        selected_root = str(data['selected'].get(realm) or '').casefold()
        chosen = [r for r in rows if str(r['root']).casefold() == selected_root] if selected_root else []
        wanted_role = 'lab' if data['prefer_lab'] else 'source'
        manual_roots = {str(Path(p).resolve()).casefold() for p in data['manual_paths']}
        role_rows = [r for r in rows if r['role'] == wanted_role] or rows
        role_rows = sorted(
            role_rows,
            key=lambda r: (
                0 if str(r['root']).casefold() in manual_roots else 1,
                0 if (realm != 'SR2' or (Path(r['root']) / 'SlimeRancher2_Data' / 'StreamingAssets' / 'aa' / 'catalog.bin').is_file()) else 1,
            ),
        )
        return next(iter(chosen or role_rows), None)

    def choose_install(self, realm: str, root: str) -> dict:
        matched = next((r['root'] for r in self._installations()
                        if r['game'] == realm and str(r['root']).casefold() == str(root).casefold()), None)
        if realm not in EXECUTABLES or matched is None:
            raise ValueError('unknown installation')
        data = self._load()
        data['selected'][realm] = matched
        self._save(data)
        return {'ok': True}

    # ---- the page's whole state -----------------------------------------------------------------------------------
    def get_state(self) -> dict:
        data = self._load()
        rows = self._installations()
        games = {}
        for realm in EXECUTABLES:
            primary = self._primary(realm)
            games[realm] = {'primary': primary, 'all': [r for r in rows if r['game'] == realm],
                            'running': self._launcher.running(realm), 'store_page': STORE_PAGES[realm]}
        return {'version': VERSION, 'installations': rows, 'games': games, 'legal_understood': bool(data['legal_understood']),
                'preferences': {k: data[k] for k in _VALIDATORS if k in data},
                'host': self._host.state(), 'tool': self._tool(data),
                'paths': {'models': str(self._models_dir(data)), 'output': str(self._output_dir(data)),
                          'cache': str(self._cache_dir(data))},
                'lab_isolation': 'Not configured — game launch disabled', 'platform': sys.platform}

    # ---- launching -------------------------------------------------------------------------------------------------
    def launch(self, target: str, confirmed: bool = False, remember: bool = False) -> dict:
        """``target``: SR1, SR2 or BOTH. Needs the backup reminder to be confirmed once."""
        if target not in ('SR1', 'SR2', 'BOTH'):
            raise ValueError('target must be SR1, SR2 or BOTH')
        data = self._load()
        if not data['backup_ack'] and not confirmed:
            return {'ok': False, 'needs_confirmation': True}
        if confirmed and remember and not data['backup_ack']:
            data['backup_ack'] = True
            self._save(data)
        realms = ('SR1', 'SR2') if target == 'BOTH' else (target,)
        warnings, errors, launched = [], [], []
        # The bridge (portals, travel, shared inventory) needs BridgeHost whichever game is started first.
        try:
            self._host.start()
        except RuntimeErrorWithCode as exc:
            warnings.append(f'BridgeHost not started ({exc.code}): the games will start without the bridge link')
        for realm in realms:
            primary = self._primary(realm)
            if primary is None:
                errors.append({'realm': realm, 'error': 'GAME_NOT_FOUND'})
                continue
            # LAB copies are made ready here (plugin, island + portal layout, imported species) so a normal Play has
            # everything linked; real installs keep the explicit install flow (nothing is copied behind the player's back).
            lab_env = {}
            if pack.is_lab_copy(realm, primary['root']):
                installs = {r: (self._primary(r) or {}).get('root') for r in ('SR1', 'SR2')}
                prepared = pack.prepare(realm, primary['root'], installs=installs)
                warnings += [f'{realm}: {w}' for w in prepared['warnings']]
                lab_env = pack.lab_env(realm, primary['root'])
            try:
                launched.append(self._launcher.launch(primary, extra_env={**self._host.realm_env(realm), **travel.bridge_env(self._settings()[1]['bridge'], realm), **self._perf_env(), **lab_env}))
            except RuntimeErrorWithCode as exc:
                errors.append({'realm': realm, 'error': exc.code, 'detail': exc.detail})
        return {'ok': bool(launched) and not errors, 'launched': launched, 'errors': errors, 'warnings': warnings,
                'host': self._host.state()}

    def host_start(self) -> dict:
        try:
            return {'ok': True, 'host': self._host.start()}
        except RuntimeErrorWithCode as exc:
            return {'ok': False, 'error': exc.code, 'detail': exc.detail, 'host': self._host.state()}

    def host_stop(self) -> dict:
        return {'ok': True, **self._host.stop(), 'host': self._host.state()}

    def _window_control(self):
        """Window control restricted to PIDs whose image is still a verified game executable."""
        if os.name != 'nt':
            return windows.NullWindows()
        from tools.lab.process_guard import process_image_path

        def verified(pid):
            image = process_image_path(pid)
            return image is not None and image.name in EXECUTABLES.values()
        try:
            return windows.Win32Windows(verify=verified)
        except windows.WindowError:
            return windows.NullWindows()

    def _raise_window(self) -> None:
        """Bring the launcher in front of the games (a crossing needs the player to choose a save)."""
        window = self._window
        if window is None:
            return
        for step in ('restore', 'show'):
            try:
                getattr(window, step)()
            except Exception:                                          # noqa: BLE001 - not every pywebview backend has both
                pass
        try:
            window.on_top = True
            window.on_top = False
        except Exception:                                              # noqa: BLE001
            pass

    def travel_link(self, transition_id: str, save_group: str | None) -> dict:
        """Answer "which save of the other game belongs to this one?" (``None`` cancels the crossing)."""
        if save_group is not None and not isinstance(save_group, str):
            raise ValueError('save_group must be a string or null')
        return self._travel.choose_link(str(transition_id), save_group)

    def travel_links(self) -> dict:
        links = self._travel._links.all()
        return {'ok': True, 'links': [{'sr1': key.split(':', 1)[1], 'sr2': value} for key, value in sorted(links.items())]}

    def travel_unlink(self, realm: str, save_group: str) -> dict:
        if realm not in ('SR1', 'SR2') or not isinstance(save_group, str):
            raise ValueError('realm must be SR1 or SR2')
        return {'ok': True, 'was_linked_to': self._travel._links.unlink(realm, save_group)}

    def plugin_install(self, realm: str) -> dict:
        """Put (or update) the SlimeBridge plugin in the chosen installation's mod folder; installs the mod loader first if needed."""
        if realm not in ('SR1', 'SR2'):
            raise ValueError('realm must be SR1 or SR2')
        primary = self._primary(realm)
        if primary is None:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        if self._launcher.running(realm):
            return {'ok': False, 'error': 'GAME_RUNNING'}
        from apps.launcher import loaders
        try:
            loaders.install(realm, primary['root'])
        except loaders.LoaderError as exc:
            return {'ok': False, 'error': 'LOADER_' + exc.code, 'detail': exc.detail}
        try:
            result = plugins.install(realm, primary['root'])
        except plugins.PluginError as exc:
            return {'ok': False, 'error': exc.code, 'detail': exc.detail}
        from apps.launcher import online
        island = None
        if realm == 'SR1':                     # the island (sanctuary + portal) comes with the SR1 plugin
            try:
                sanctuary.refresh_layout()
                latest = sanctuary.latest_layout()
                current = sanctuary.installed(primary['root'])
                if latest and not current.get('matches_latest'):
                    sanctuary.install_layout(latest['path'], primary['root'])
                island = sanctuary.installed(primary['root']).get('present')
            except Exception as exc:  # noqa: BLE001 - the plugin is installed either way
                island = f'failed: {exc}'
        return {**result, 'state': plugins.status(realm, primary['root'])['state'], 'online': online.ensure(realm, primary['root']),
                'island': island}

    def loader_install(self, realm: str) -> dict:
        """Download (official release, pinned SHA-256) and install the game's mod loader: SRML for SR1, MelonLoader for SR2."""
        if realm not in ('SR1', 'SR2'):
            raise ValueError('realm must be SR1 or SR2')
        primary = self._primary(realm)
        if primary is None:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        if self._launcher.running(realm):
            return {'ok': False, 'error': 'GAME_RUNNING'}
        from apps.launcher import loaders
        try:
            return loaders.install(realm, primary['root'])
        except loaders.LoaderError as exc:
            return {'ok': False, 'error': exc.code, 'detail': exc.detail}

    def crossings_poll(self) -> dict:
        """Drive and report portal crossings: a plugin asked BridgeHost to cross, the launcher moves the player between the two games."""
        return {'ok': True, 'crossings': self._travel.poll()}

    def host_state(self) -> dict:
        return self._host.state()

    def shutdown(self) -> None:
        """Called when the window closes: stop the child processes this launcher started (never the games)."""
        try:
            if self._extract is not None:
                self._extract.cancel()
        finally:
            self._host.stop()

    # ---- asset extraction ------------------------------------------------------------------------------------------
    def _tool(self, data=None) -> dict:
        """The extraction engine the Extract page uses: the built-in pipeline (always available), plus the optional
        external converter (only needed for the SR1 -> SR2 direction)."""
        data = data or self._load()
        return {**extract.builtin_tool(), 'external': extract.locate_tool(data.get('converter_path'))}

    def choose_tool_folder(self) -> dict:
        if self._window is None:
            raise RuntimeError('File picker requires the desktop window')
        import webview
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {'ok': False, 'cancelled': True}
        if not (Path(chosen[0]) / 'sbconv' / '__init__.py').is_file():
            return {'ok': False, 'error': 'NOT_THE_TOOL'}
        self.set_preference('converter_path', chosen[0])
        return {'ok': True, 'tool': self._tool()}

    def choose_output_folder(self) -> dict:
        if self._window is None:
            raise RuntimeError('File picker requires the desktop window')
        import webview
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {'ok': False, 'cancelled': True}
        return self.set_output_folder(chosen[0])

    def set_output_folder(self, path: str) -> dict:
        folder = Path(path).resolve()
        for row in self._installations():
            root = Path(row['root']).resolve()
            if folder == root or root in folder.parents:
                return {'ok': False, 'error': 'INSIDE_GAME_FOLDER'}
        self.set_preference('converter_output', str(folder))
        return {'ok': True, 'output': str(folder)}

    def tool_status(self) -> dict:
        return self._tool()

    def extract_start(self, options: dict | None = None) -> dict:
        options = dict(options or {})
        with self._lock:
            if self._extract is not None and self._extract.snapshot()['status'] in ('QUEUED', 'RUNNING'):
                return {'ok': False, 'error': 'ALREADY_RUNNING', 'job': self._extract.snapshot()}
        engine = options.get('engine', 'builtin')
        if engine not in extract.ENGINES:
            return {'ok': False, 'error': 'BAD_OPTIONS', 'detail': f'engine must be one of {extract.ENGINES}'}
        installs = {realm: (self._primary(realm) or {}).get('root') for realm in EXECUTABLES}
        data = self._load()
        if engine == 'builtin':
            output = self._cache_dir(data)           # the species caches the SR1 plugin reads
            if options.get('ram_gb') is None:        # the memory limit slider (Extract page); a fixed default when never moved
                options['ram_gb'] = data.get('extract_ram_gb') or extract.DEFAULT_RAM_GB
            try:
                job = extract.BuiltinExtractJob(options, installs, output)
            except ValueError as exc:
                return {'ok': False, 'error': 'BAD_OPTIONS', 'detail': str(exc)}
            with self._lock:
                self._extract = job.start()
            return {'ok': True, 'job': job.snapshot()}
        tool = self._tool(data)['external']
        if not tool['found']:
            return {'ok': False, 'error': 'TOOL_NOT_FOUND', 'detail': tool['reason']}
        output = self._output_dir(data)
        try:
            job = extract.ExtractJob(tool, options, installs, output)
        except ValueError as exc:
            return {'ok': False, 'error': 'BAD_OPTIONS', 'detail': str(exc)}
        for root in installs.values():
            if root and (Path(root).resolve() in output.resolve().parents or output.resolve() == Path(root).resolve()):
                return {'ok': False, 'error': 'INSIDE_GAME_FOLDER'}
        with self._lock:
            self._extract = job.start()
        return {'ok': True, 'job': job.snapshot()}

    def extract_status(self) -> dict:
        return {'ok': True, 'job': self._extract.snapshot() if self._extract else None}

    def extract_cancel(self) -> dict:
        if self._extract is not None:
            self._extract.cancel()
        return {'ok': True}

    def extract_install_cache(self) -> dict:
        data = self._load()
        job = self._extract
        if isinstance(job, extract.BuiltinExtractJob):
            # The built-in pipeline writes straight into the cache the plugin reads: nothing to copy.
            result = (job.snapshot().get('result') or {}).get('export') or {}
            return {'ok': True, 'copied': result.get('count', 0), 'skipped': 0,
                    'destination': str(self._cache_dir(data) / 'sr2-to-sr1')}
        return {'ok': True, **extract.install_cache(self._output_dir(data), self._cache_dir(data))}

    # ---- SR1 sanctuary (statues, island, gate, portal) -----------------------------------------------------------
    def _sr1_root(self):
        primary = self._primary('SR1')
        return primary['root'] if primary else None

    def sanctuary_state(self) -> dict:
        root = self._sr1_root()
        _, settings = self._settings()
        with self._lock:
            job = self._sanctuary.snapshot() if self._sanctuary else None
        return {'ok': True, 'sr1_root': root, 'dependencies': sanctuary.dependencies(), 'kit': sanctuary.latest_kit(),
                'layout': sanctuary.latest_layout(), 'installed': sanctuary.installed(root), 'plugin': sanctuary.plugin_installed(root),
                'settings': dict(settings['bridge']), 'job': job}

    def sanctuary_build(self) -> dict:
        with self._lock:
            if self._sanctuary is not None and self._sanctuary.snapshot()['status'] in ('QUEUED', 'RUNNING'):
                return {'ok': False, 'error': 'ALREADY_RUNNING'}
        root = self._sr1_root()
        if not root:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        deps = sanctuary.dependencies()
        if not deps['ok']:
            return {'ok': False, 'error': 'MISSING_DEPENDENCIES', 'detail': deps['hint']}
        _, settings = self._settings()
        from bridge.sanctuary import layout as layout_module
        job = sanctuary.SanctuaryJob(Path(root) / 'SlimeRancher_Data', layout_module.config_from_settings(settings))
        with self._lock:
            self._sanctuary = job.start()
        return {'ok': True, 'job': job.snapshot()}

    def sanctuary_status(self) -> dict:
        with self._lock:
            return {'ok': True, 'job': self._sanctuary.snapshot() if self._sanctuary else None}

    def sanctuary_install(self) -> dict:
        root, layout = self._sr1_root(), sanctuary.latest_layout()
        if not root:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        if layout is None:
            return {'ok': False, 'error': 'NOT_BUILT'}
        try:
            return {**sanctuary.install_layout(layout['path'], root), 'installed': sanctuary.installed(root)}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': 'INSTALL_FAILED', 'detail': str(exc)}

    def sanctuary_remove(self) -> dict:
        root = self._sr1_root()
        if not root:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        return {**sanctuary.remove_installed(root), 'installed': sanctuary.installed(root)}

    # ---- cross-game Largo models ----------------------------------------------------------------------------------
    def _store(self):
        return models.ModelStore(self._models_dir(), **({'opener': self._opener} if self._opener else {}))

    def models_state(self) -> dict:
        data = self._load()
        store = self._store()
        return {'ok': True, 'rows': store.rows(self._manifest['doc']), 'dir': str(self._models_dir(data)),
                'manifest': {'url': data.get('largo_manifest_url'), 'loaded': self._manifest['doc'] is not None,
                             'name': (self._manifest['doc'] or {}).get('name'),
                             'updated': (self._manifest['doc'] or {}).get('updated'), 'error': self._manifest['error']},
                'download': self._downloads.snapshot() if self._downloads else None}

    def models_refresh(self) -> dict:
        url = self._load().get('largo_manifest_url')
        if not url:
            return {'ok': False, 'error': 'NO_SOURCE', **self.models_state()}
        try:
            self._manifest = {'doc': self._store().fetch_manifest(url), 'error': None}
        except (models.ModelError, OSError, ValueError) as exc:
            self._manifest = {'doc': None, 'error': str(exc)[:300]}
        return {'ok': self._manifest['error'] is None, **self.models_state()}

    def models_download(self, ids=None) -> dict:
        doc = self._manifest['doc']
        if not doc:
            return {'ok': False, 'error': 'NO_MANIFEST'}
        wanted = {m['id']: m for m in doc['models']}
        installed = self._store().installed()
        if ids in (None, 'all'):
            chosen = [m for m in doc['models'] if installed.get(m['id'], {}).get('version') != m['version']]
        else:
            chosen = [wanted[i] for i in ids if i in wanted]
        if not chosen:
            return {'ok': False, 'error': 'NOTHING_TO_DOWNLOAD'}
        if self._downloads is not None and self._downloads.snapshot()['status'] in ('QUEUED', 'RUNNING'):
            return {'ok': False, 'error': 'ALREADY_RUNNING'}
        self._downloads = models.DownloadJob(self._store(), chosen).start()
        return {'ok': True, 'download': self._downloads.snapshot()}

    def models_cancel(self) -> dict:
        if self._downloads is not None:
            self._downloads.cancel()
        return {'ok': True}

    def models_remove(self, model_id: str) -> dict:
        return {'ok': True, **self._store().remove(model_id), **self.models_state()}

    def models_import_folder(self, folder: str | None = None) -> dict:
        """Copy the player's own ``<id>.glb`` files (e.g. ``rad_cotton.glb``) into the models folder."""
        if folder is None:
            if self._window is None:
                raise RuntimeError('File picker requires the desktop window')
            import webview
            chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not chosen:
                return {'ok': False, 'cancelled': True}
            folder = chosen[0]
        source = Path(folder)
        if not source.is_dir():
            return {'ok': False, 'error': 'NOT_A_FOLDER'}
        import shutil
        imported = []
        for item in models.catalogue():
            for suffix in ('.glb', '.json'):
                file = source / f'{item["id"]}{suffix}'
                if file.is_file() and not file.is_symlink() and file.stat().st_size <= models.MAX_DOWNLOAD:
                    target = self._models_dir() / item['id']
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, target / file.name)
                    (target / '.sb-install.json').write_text(json.dumps(
                        {'id': item['id'], 'version': 'local', 'source': 'local-import', 'files': 1}), encoding='utf-8')
                    if item['id'] not in imported:
                        imported.append(item['id'])
        return {'ok': bool(imported), 'imported': imported, **self.models_state()}

    # ---- multiplayer -----------------------------------------------------------------------------------------------
    def _settings(self):
        from bridge.app.settings import SettingsStore
        store = SettingsStore(Path(self._config_path).parent)
        settings, _ = store.load()
        return store, settings

    def mp_state(self) -> dict:
        _, settings = self._settings()
        try:
            from bridge.content import content_manifest
            content = content_manifest.hello_content()          # what this install would present when joining (imported species + Largos)
        except (OSError, ValueError, KeyError):
            content = {}
        from apps.launcher import online
        states = {}
        for realm in ('SR1', 'SR2'):
            primary = self._primary(realm)
            if primary is None:
                states[realm] = {'state': 'no_game', 'loader': False}
                continue
            try:
                states[realm] = online.status(realm, primary['root'])
            except OSError as exc:
                states[realm] = {'state': 'error', 'loader': False, 'error': str(exc)}
        return {'ok': True, 'servers': settings['servers'], 'host': self._load()['mp_host'], 'content': content,
                'online': states, 'network_adapter': any(s.get('state') == 'current' for s in states.values())}

    def mp_online(self, realm: str, action: str) -> dict:
        """Install/update (``install``) or remove (``remove``) the online components of one game."""
        from apps.launcher import online, srmp
        if realm not in ('SR1', 'SR2') or action not in ('install', 'remove'):
            raise ValueError('realm must be SR1/SR2 and action install/remove')
        primary = self._primary(realm)
        if primary is None:
            return {'ok': False, 'error': 'GAME_NOT_FOUND'}
        if self._launcher.running(realm):
            return {'ok': False, 'error': 'GAME_RUNNING'}
        try:
            result = online.install(realm, primary['root']) if action == 'install' else online.remove(realm, primary['root'])
        except (online.OnlineError, srmp.SrmpError) as exc:
            return {'ok': False, 'error': exc.code, 'detail': exc.detail}
        return {**result, 'online': online.status(realm, primary['root'])}

    def mp_remember(self, address: str, favourite: bool | None = None) -> dict:
        from bridge.app import settings as ST
        from bridge.multiplayer.directory import parse_address, remember
        try:
            host, port = parse_address(address)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        text = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
        store, settings = self._settings()
        servers = dict(settings['servers'])
        servers['recent'] = remember(servers['recent'], text)
        favourites = [f for f in servers['favourites'] if f != text]
        if favourite:
            favourites.insert(0, text)
        elif favourite is None:
            favourites = servers['favourites']
        servers['favourites'] = favourites[:50]
        store.save(ST.update(settings, 'servers', servers))
        return {'ok': True, 'address': text}

    def mp_host_save(self, options: dict) -> dict:
        name = str(options.get('name', '')).strip()[:48] or 'My Ranch'
        mode = options.get('mode', 'HYBRID')
        try:
            port = int(options.get('port', 7777))
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'port must be a number'}
        if mode not in ('HYBRID', 'SR1_ONLY', 'SR2_ONLY') or not 1024 <= port <= 65535:
            return {'ok': False, 'error': 'mode or port out of range'}
        data = self._load()
        data['mp_host'] = {'name': name, 'port': port, 'mode': mode}
        self._save(data)
        return {'ok': True, 'host': data['mp_host']}

    # ---- SlimeBridge options (shared settings schema) --------------------------------------------------------------
    def get_settings(self) -> dict:
        from bridge.app import settings as ST
        _, settings = self._settings()
        sections = []
        for name in ST.visible_sections(settings):
            value = settings[name]
            if name in ('games',) or not isinstance(value, dict):
                continue
            fields = [{'key': key, 'value': current, 'kind': 'bool' if isinstance(current, bool) else
                       ('list' if isinstance(current, list) else 'text'), 'choices': _choices(name, key)}
                      for key, current in value.items()]
            sections.append({'section': name, 'fields': fields})
        return {'ok': True, 'sections': sections}

    def set_setting(self, section: str, key: str, value) -> dict:
        from bridge.app import settings as ST
        store, settings = self._settings()
        if section == 'games':
            return {'ok': False, 'error': 'game folders are managed on the Games page'}
        try:
            updated = ST.update(settings, section, {key: value})
            store.save(updated)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'section': section, 'key': key, 'value': value}

    # ---- official news and artwork (loaded from Monomi Park's sites at every start) ---------------------------------
    def news_cached(self) -> dict:
        """Last successful feed from disk, instantly (empty when there is none yet)."""
        return self._feed.cached()

    def news_refresh(self) -> dict:
        """Fetch the official pages now; falls back to the cached feed (``offline: true``) when the site is unreachable."""
        return self._feed.refresh()

    def image_data(self, url: str, width: int = 640, height: int = 360, fit: bool = True):
        """``data:`` URI of an official picture (only the official CDN is ever contacted), or ``None``."""
        try:
            return self._feed.image_data(str(url), int(width), int(height), bool(fit))
        except (TypeError, ValueError):
            return None

    def images_data(self, requests: list) -> list:
        """Batch of ``[url, width, height, fit]`` (at most 12) fetched in parallel."""
        wanted = []
        for entry in list(requests or [])[:12]:
            try:
                wanted.append((str(entry[0]), int(entry[1]), int(entry[2]), bool(entry[3]) if len(entry) > 3 else True))
            except (TypeError, ValueError, IndexError):
                wanted.append(('', 16, 16, True))
        return self._feed.images_data(wanted)

    def open_url(self, url: str) -> dict:
        """Open an official link (Monomi Park, its shop/support/Discord, Steam) in the default browser; nothing else."""
        if not news.link_allowed(url):
            return {'ok': False, 'error': 'URL_NOT_ALLOWED'}
        webbrowser.open(url)
        return {'ok': True, 'url': url}

    # ---- custom window frame ----------------------------------------------------------------------------------------
    def window_minimize(self) -> dict:
        if self._window is not None:
            self._window.minimize()
        return {'ok': self._window is not None}

    def window_toggle_maximize(self) -> dict:
        if self._window is None:
            return {'ok': False}
        (self._window.restore if self._maximized else self._window.maximize)()
        self._maximized = not self._maximized
        return {'ok': True, 'maximized': self._maximized}

    def window_close(self) -> dict:
        if self._window is not None:
            self._window.destroy()
        return {'ok': self._window is not None}

    # ---- misc ------------------------------------------------------------------------------------------------------
    def open_folder(self, which: str) -> dict:
        data = self._load()
        if which in EXECUTABLES:
            primary = self._primary(which)
            target = Path(primary['root']) if primary else None
        else:
            target = {'models': self._models_dir(data), 'output': self._output_dir(data), 'cache': self._cache_dir(data),
                      'lab': self._lab_root()}.get(which)
        if target is None:
            return {'ok': False, 'error': 'UNKNOWN_FOLDER'}
        if which in ('models', 'output', 'cache'):
            target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            return {'ok': False, 'error': 'MISSING_FOLDER'}
        if hasattr(os, 'startfile'):
            os.startfile(str(target))  # noqa: S606 - a folder this launcher resolved itself, never a UI-supplied path
        return {'ok': True, 'path': str(target)}

    def open_store_page(self, realm: str) -> dict:
        if realm not in STORE_PAGES:
            raise ValueError('realm must be SR1 or SR2')
        webbrowser.open(STORE_PAGES[realm])
        return {'ok': True, 'url': STORE_PAGES[realm]}

    # ---- profiles & saves, recovery center (read-only; carried over from the retired Qt window) ---------------------
    def profiles_state(self) -> dict:
        from apps.launcher import recovery
        try:
            return {'ok': True, 'pairs': recovery.pairs()}
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            return {'ok': False, 'error': 'PROFILES_UNREADABLE', 'detail': str(exc)[:200], 'pairs': []}

    def recovery_state(self) -> dict:
        from apps.launcher import recovery
        return {'ok': True, **recovery.recovery_state(self._installations())}

    def recovery_export(self) -> dict:
        from apps.launcher import recovery
        path = recovery.export(self._installations())
        return {'ok': True, 'name': path.name, 'folder': str(path.parent)}

    def about(self) -> dict:
        return {'ok': True, 'version': VERSION, 'python': sys.version.split()[0], 'repo': str(REPO_ROOT),
                'config': str(self._config_path), 'fonts': ['Fredoka (SIL OFL 1.1)', 'Nunito (SIL OFL 1.1)']}


def _choices(section, key):
    return {('transitions', 'mode'): ['WARM', 'COLD'], ('transitions', 'overlay_style'): ['aurora', 'minimal'],
            ('transitions', 'background_audio'): ['DUCK', 'MUTE', 'KEEP'],
            ('servers', 'default_mode'): ['HYBRID', 'SR1_ONLY', 'SR2_ONLY'],
            ('updates', 'channel'): ['stable', 'beta', 'dev'], ('diagnostics', 'level'): ['NORMAL', 'VERBOSE'],
            ('ui', 'language'): ['en', 'fr'], ('ui', 'theme'): ['auto', 'light', 'dark']}.get((section, key))


def main() -> None:
    import argparse
    import webview
    parser = argparse.ArgumentParser(description='SlimeBridge desktop launcher')
    parser.add_argument('--smoke-ui', action='store_true', help='Open and close the GUI for a short startup check')
    args = parser.parse_args()
    if not UI.is_file():
        raise FileNotFoundError(UI)
    api = LauncherAPI()
    frameless = not api._load().get('native_frame', False)
    window = webview.create_window('SlimeBridge', url=UI.as_uri(), js_api=api,
                                   width=1320, height=840, min_size=(1040, 680), frameless=frameless, easy_drag=False,
                                   background_color='#12354d', text_select=False)
    api._window = window
    window.events.closed += api.shutdown
    if args.smoke_ui:
        import time
        result = {'error': 'WebView did not become ready'}

        def close_after_load():
            try:
                if not window.events._pywebviewready.wait(12):
                    return
                for _ in range(40):
                    window.evaluate_js("window.SB && SB.app && SB.app.state && SB.app.go('games')")
                    count = window.evaluate_js("document.querySelectorAll('#games .game-card').length")
                    if count == 2:
                        print('Desktop UI rendered SR1 and SR2 cards', flush=True)
                        break
                    time.sleep(0.25)
                else:
                    result['error'] = 'Desktop UI did not render the game cards'
                    return
                # every page must render with the real backend and without a JavaScript error
                for name in ('news', 'games', 'assets', 'models', 'multiplayer', 'debug', 'options', 'about', 'home'):
                    window.evaluate_js(f"SB.app.go('{name}')")
                    time.sleep(0.6)
                    rendered = window.evaluate_js("!!document.querySelector('#view .page')")
                    if not rendered:
                        result['error'] = f'page {name} did not render'
                        return
                errors = window.evaluate_js('SB.errors')
                if errors:
                    result['error'] = 'JavaScript errors: ' + '; '.join(errors[:5])
                    return
                print('Desktop UI rendered every page without JavaScript errors', flush=True)
                result.clear()
            finally:
                window.destroy()
        webview.start(close_after_load, gui=GUI)
        if result:
            raise RuntimeError(result['error'])
    else:
        webview.start(gui=GUI)


if __name__ == '__main__':
    main()
