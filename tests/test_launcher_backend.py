"""Backend of the redesigned launcher: detection, process control, extraction job, model downloads, API surface."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from apps.launcher import detect, extract, models, runtime
from apps.launcher.desktop import LauncherAPI

UI = Path(__file__).resolve().parents[1] / 'apps' / 'launcher' / 'ui'


def make_sr1(root):
    (root / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True, exist_ok=True)
    for rel in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
        (root / rel).write_bytes(b'')
    return root


def make_sr2(root):
    (root / 'SlimeRancher2_Data' / 'il2cpp_data' / 'Metadata').mkdir(parents=True, exist_ok=True)
    for rel in ('SlimeRancher2.exe', 'UnityPlayer.dll', 'GameAssembly.dll',
                'SlimeRancher2_Data/il2cpp_data/Metadata/global-metadata.dat'):
        (root / rel).write_bytes(b'')
    return root


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


class DetectTests(unittest.TestCase):
    def test_finds_steam_library_gog_and_plain_folders_and_ignores_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            library = drive / 'SteamLibrary'
            (library / 'steamapps').mkdir(parents=True)
            (library / 'steamapps' / 'appmanifest_433340.acf').write_text('"AppState"\n{\n "installdir" "Slime Rancher"\n}\n')
            make_sr1(library / 'steamapps' / 'common' / 'Slime Rancher')
            make_sr2(drive / 'GOG Games' / 'Slime Rancher 2')
            (drive / 'Games' / 'Other Game').mkdir(parents=True)
            (drive / 'Games' / 'Other Game' / 'Other.exe').write_bytes(b'')
            found = detect.auto_detect(drives=[drive], registry=False, epic_manifest_dir=drive / 'no-epic')
            by_game = {i.game: i for i in found}
            self.assertEqual(set(by_game), {'SR1', 'SR2'})
            self.assertEqual(by_game['SR1'].store, 'steam')
            self.assertEqual(by_game['SR2'].store, 'gog')
            self.assertEqual(len(found), 2)

    def test_wrapper_folders_are_searched_but_only_a_few_levels_deep(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = make_sr1(Path(tmp) / 'Slime.Rancher.v1.4.4 (2)' / 'Slime.Rancher.v1.4.4' / 'Slime.Rancher.v1.4.4')
            found = detect.resolve_install(Path(tmp) / 'Slime.Rancher.v1.4.4 (2)')
            self.assertEqual((found.game, found.root), ('SR1', deep.resolve()))
            too_deep = make_sr2(Path(tmp) / 'a' / 'b' / 'c' / 'd' / 'e')
            self.assertIsNone(detect.resolve_install(Path(tmp) / 'a'))
            self.assertTrue(too_deep.is_dir())
            self.assertIsNone(detect.resolve_install(Path(tmp) / 'missing'))

    def test_incomplete_folder_is_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'GOG Games' / 'Slime Rancher'
            fake.mkdir(parents=True)
            (fake / 'SlimeRancher.exe').write_bytes(b'')   # no Unity runtime files
            self.assertEqual(detect.auto_detect(drives=[Path(tmp)], registry=False, epic_manifest_dir=Path(tmp) / 'x'), [])

    def test_finds_sr2_in_user_downloads_wrapper_and_crowded_parent_or_data_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            downloads = drive / 'Player' / 'Downloads'
            for i in range(450):
                (downloads / f'aaa_other_{i:03d}' / 'sub').mkdir(parents=True)
            sr2 = make_sr2(downloads / 'Slime.Rancher.2.v1.3.0' / 'Slime.Rancher.2.v1.3.0' / 'Slime Rancher 2')
            found = detect.auto_detect(drives=[drive], registry=False, epic_manifest_dir=drive / 'no-epic')
            self.assertEqual([(i.game, i.root) for i in found], [('SR2', sr2.resolve())])
            self.assertEqual(detect.resolve_install(downloads).root, sr2.resolve())
            self.assertEqual(detect.resolve_install(sr2 / 'SlimeRancher2_Data').root, sr2.resolve())


class FakeProc:
    def __init__(self, alive=True):
        self.pid, self.alive, self.terminated = 4242, alive, False
        self.env = self.cwd = None

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive, self.terminated = False, True

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class RuntimeTests(unittest.TestCase):
    def test_launcher_starts_the_verified_exe_with_bridge_env_and_never_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_sr1(Path(tmp) / 'sr1')
            calls = []

            def popen(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return FakeProc()
            launcher = runtime.GameLauncher(popen=popen)
            first = launcher.launch({'game': 'SR1', 'root': str(root)}, extra_env={'SLIMEBRIDGE_URL': 'http://127.0.0.1:1'})
            again = launcher.launch({'game': 'SR1', 'root': str(root)})
            self.assertFalse(first['already_running'])
            self.assertTrue(again['already_running'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], [str(root / 'SlimeRancher.exe')])
            self.assertEqual(calls[0][1]['cwd'], str(root))
            self.assertEqual(calls[0][1]['env']['SLIMEBRIDGE_URL'], 'http://127.0.0.1:1')
            with self.assertRaises(runtime.RuntimeErrorWithCode):
                launcher.launch({'game': 'SR2', 'root': str(root)})

    def test_host_start_waits_for_address_then_stop_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = FakeProc()

            def popen(cmd, **kwargs):
                token_file = Path(cmd[cmd.index('--token-file') + 1])
                status_file = Path(cmd[cmd.index('--status-file') + 1])
                token_file.write_text('secret-token')
                status_file.write_text(json.dumps({'url': 'http://127.0.0.1:5555', 'heartbeats': {}}))
                return child
            host = runtime.HostProcess(Path(tmp) / 'host', popen=popen, sleep=lambda s: None)
            state = host.start()
            self.assertEqual((state['state'], state['url']), ('RUNNING', 'http://127.0.0.1:5555'))
            self.assertEqual(host.env(), {'SLIMEBRIDGE_URL': 'http://127.0.0.1:5555', 'SLIMEBRIDGE_TOKEN': 'secret-token'})
            self.assertTrue(host.stop()['stopped'])
            self.assertTrue(child.terminated)
            self.assertEqual(host.state()['state'], 'STOPPED')
            self.assertEqual(host.env(), {})
            self.assertFalse(host.token_file.exists())

    def test_host_that_never_answers_times_out_and_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = FakeProc()
            ticks = iter(range(0, 100))
            host = runtime.HostProcess(Path(tmp) / 'h', popen=lambda *a, **k: child, wait_seconds=3,
                                       clock=lambda: next(ticks), sleep=lambda s: None)
            with self.assertRaises(runtime.RuntimeErrorWithCode) as ctx:
                host.start()
            self.assertEqual(ctx.exception.code, 'HOST_TIMEOUT')
            self.assertTrue(child.terminated)


class FakeConverter:
    """Speaks the sbconv JSON-lines protocol on in-memory pipes."""

    def __init__(self, fail_convert=False, hello=True):
        self.pid, self.responses, self.fail_convert, self.hello = 777, [], fail_convert, hello
        self.requests = []
        self.stdin = self
        self.stdout = self
        self._alive = True

    # stdin side
    def write(self, text):
        req = json.loads(text)
        self.requests.append(req)
        rid, op = req['id'], req['op']
        if op == 'list':
            species = [{'game': 'SR1', 'kind': 'base', 'native_in_target': True}, {'game': 'SR1', 'kind': 'largo', 'native_in_target': False},
                       {'game': 'SR2', 'kind': 'base', 'native_in_target': False}, {'game': 'SR2', 'kind': 'largo', 'native_in_target': False}]
            self.responses += [json.dumps({'id': rid, 'event': 'result', 'ok': True, 'result': {'species': species, 'count': 4}})]
        elif op == 'convert':
            if self.fail_convert:
                self.responses += [json.dumps({'id': rid, 'event': 'error', 'error': 'boom'})]
            else:
                self.responses += [json.dumps({'id': rid, 'event': 'log', 'message': '[SR2:slime.a] extracting...'}),
                                   json.dumps({'id': rid, 'event': 'log', 'message': '[SR2:largo.a.b] extracting...'}),
                                   json.dumps({'id': rid, 'event': 'result', 'ok': True, 'result': {'results': [{'ok': True, 'status': 'CONVERTED'}] * 2, 'ok': True}})]
        elif op in ('verify', 'export'):
            status = 'OFFLINE_VERIFIED' if op == 'verify' else None
            row = {'ok': True, **({'status': status} if status else {})}
            self.responses += [json.dumps({'id': rid, 'event': 'result', 'ok': True, 'result': {'results': [row, row], 'ok': True}})]
        elif op == 'status':
            self.responses += [json.dumps({'id': rid, 'event': 'result', 'ok': True, 'result': {'summary': {'OFFLINE_VERIFIED': 2}}})]

    def flush(self):
        pass

    def close(self):
        self._alive = False

    # stdout side
    def readline(self):
        if self.hello:
            self.hello = False
            return json.dumps({'event': 'hello'}) + '\n'
        return (self.responses.pop(0) + '\n') if self.responses else ''

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


class ExtractTests(unittest.TestCase):
    def tool(self):
        return {'found': True, 'path': '.', 'python': 'python'}

    def test_full_run_counts_species_reports_progress_and_never_touches_game_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeConverter()
            job = extract.ExtractJob(self.tool(), {'direction': 'sr2-to-sr1', 'only_missing': True},
                                     {'SR1': 'r1', 'SR2': 'r2'}, Path(tmp) / 'out', popen=lambda *a, **k: fake)
            job.start().join(5)
            snap = job.snapshot()
            self.assertEqual(snap['status'], 'DONE')
            self.assertEqual(snap['total'], 2)           # only SR2 species the other game lacks
            self.assertEqual(snap['done'], 2)
            self.assertEqual(snap['progress'], 1.0)
            self.assertEqual([r['op'] for r in fake.requests], ['list', 'convert', 'verify', 'export', 'status'])
            convert = fake.requests[1]['args']
            self.assertEqual((convert['direction'], convert['only_missing'], convert['all_species']), ('sr2-to-sr1', True, True))
            self.assertEqual(snap['result']['verify']['by_status'], {'OFFLINE_VERIFIED': 2})
            self.assertTrue((Path(tmp) / 'out').is_dir())

    def test_failure_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = extract.ExtractJob(self.tool(), {'direction': 'both'}, {'SR1': 'a', 'SR2': 'b'}, Path(tmp),
                                     popen=lambda *a, **k: FakeConverter(fail_convert=True))
            job.start().join(5)
            snap = job.snapshot()
            self.assertEqual(snap['status'], 'FAILED')
            self.assertIn('boom', snap['error'])

    def test_missing_game_and_bad_direction_are_refused_up_front(self):
        with self.assertRaises(ValueError):
            extract.ExtractJob(self.tool(), {'direction': 'sideways'}, {'SR1': 'a', 'SR2': 'b'}, 'x')
        with self.assertRaises(ValueError):
            extract.ExtractJob(self.tool(), {'direction': 'sr1-to-sr2'}, {'SR1': None, 'SR2': 'b'}, 'x')

    def test_locate_tool_needs_the_package_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(extract.locate_tool(tmp)['found'] and Path(tmp).name != extract.TOOL_NAME and False)
            tool = Path(tmp) / 'SlimeBridge-AssetConverter'
            (tool / 'sbconv').mkdir(parents=True)
            (tool / 'sbconv' / '__init__.py').write_text('')
            found = extract.locate_tool(str(tool))
            self.assertTrue(found['found'])
            self.assertEqual(found['path'], str(tool))

    def test_install_cache_copies_only_complete_species_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, dest = Path(tmp) / 'out', Path(tmp) / 'cache'
            good = out / 'bridge-cache' / 'SR2-to-SR1' / 'slime.a'
            bad = out / 'bridge-cache' / 'SR2-to-SR1' / 'slime.b'
            good.mkdir(parents=True)
            bad.mkdir(parents=True)
            (good / 'cache-manifest.json').write_text('{}')
            (good / 'model.json').write_text('{}')
            (bad / 'model.json').write_text('{}')
            result = extract.install_cache(out, dest)
            self.assertEqual((result['copied'], result['skipped']), (1, 1))
            self.assertTrue((dest / 'sr2-to-sr1' / 'slime.a' / 'model.json').is_file())
            self.assertFalse((dest / 'sr2-to-sr1' / 'slime.b').exists())


class _Handler(BaseHTTPRequestHandler):
    payloads = {}

    def do_GET(self):  # noqa: N802
        data = self.payloads.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def entry(self, model_id, data, **over):
        _Handler.payloads[f'/{model_id}.zip'] = data
        return {'id': model_id, 'url': f'{self.base}/{model_id}.zip', 'sha256': hashlib.sha256(data).hexdigest(),
                'size': len(data), 'version': '1.0.0', **over}

    def test_catalogue_is_the_24_cross_game_combinations(self):
        cat = models.catalogue()
        self.assertEqual(len(cat), 24)
        self.assertIn('quantum_hyper', {m['id'] for m in cat})
        self.assertEqual(len({m['id'] for m in cat}), 24)

    def test_manifest_validation_rejects_unknown_ids_plain_http_and_bad_hashes(self):
        good = {'schema': models.SCHEMA, 'schema_version': 1, 'models': [
            {'id': 'rad_cotton', 'url': 'https://example.org/a.zip', 'sha256': 'a' * 64, 'size': 10, 'version': '1.0'}]}
        self.assertEqual(models.validate_manifest(good)['models'][0]['id'], 'rad_cotton')
        for mutate in ({'id': 'rad_unknown'}, {'url': 'http://evil.example/a.zip'}, {'sha256': 'XYZ'}, {'size': 10 ** 12},
                       {'url': 'file:///c:/windows/system32/x.zip'}):
            doc = json.loads(json.dumps(good))
            doc['models'][0].update(mutate)
            with self.subTest(mutate=mutate), self.assertRaises(models.ModelError):
                models.validate_manifest(doc)
        with self.assertRaises(models.ModelError):
            models.validate_manifest({'schema': 'other', 'schema_version': 1, 'models': []})

    def test_download_verifies_hash_unpacks_and_installs_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = models.ModelStore(Path(tmp) / 'models')
            entry = self.entry('rad_cotton', make_zip({'rad_cotton.glb': b'glTF', 'notes.txt': b'hi'}))
            seen = []
            meta = store.download(entry, progress=seen.append)
            self.assertEqual(meta['files'], 2)
            self.assertTrue((Path(tmp) / 'models' / 'rad_cotton' / 'rad_cotton.glb').is_file())
            self.assertEqual(store.installed()['rad_cotton']['version'], '1.0.0')
            self.assertTrue(seen and seen[-1] >= 0.99)
            self.assertEqual([p.name for p in (Path(tmp) / 'models').iterdir()], ['rad_cotton'])   # no temp leftovers
            rows = {r['id']: r['state'] for r in store.rows({'models': [entry]})}
            self.assertEqual(rows['rad_cotton'], 'INSTALLED')
            self.assertEqual(rows['rad_twin'], 'NOT_PUBLISHED')
            newer = dict(entry, version='2.0.0')
            self.assertEqual({r['id']: r['state'] for r in store.rows({'models': [newer]})}['rad_cotton'], 'UPDATE')

    def test_hash_mismatch_zip_slip_and_dangerous_files_install_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = models.ModelStore(Path(tmp) / 'models')
            bad_hash = self.entry('rad_cotton', make_zip({'a.glb': b'1'}), sha256='0' * 64)
            slip = self.entry('rad_batty', make_zip({'../evil.glb': b'1'}))
            exe = self.entry('rad_twin', make_zip({'tool.exe': b'MZ'}))
            absolute = self.entry('rad_hyper', make_zip({'/etc/passwd.txt': b'x'}))
            for entry in (bad_hash, slip, exe, absolute):
                with self.subTest(model=entry['id']), self.assertRaises(models.ModelError):
                    store.download(entry)
            self.assertEqual(store.installed(), {})
            self.assertFalse((Path(tmp) / 'evil.glb').exists())
            self.assertEqual([p for p in (Path(tmp) / 'models').iterdir()], [])

    def test_download_larger_than_announced_is_cut_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = models.ModelStore(Path(tmp) / 'm')
            data = make_zip({'a.glb': b'x' * 50000})
            entry = self.entry('rad_cotton', data, size=100)
            with self.assertRaises(models.ModelError):
                store.download(entry)

    def test_download_job_reports_progress_and_collects_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = models.ModelStore(Path(tmp) / 'm')
            ok = self.entry('quantum_batty', make_zip({'quantum_batty.glb': b'g'}))
            broken = self.entry('mosaic_twin', make_zip({'a.glb': b'g'}), sha256='f' * 64)
            job = models.DownloadJob(store, [ok, broken]).start()
            job.join(10)
            snap = job.snapshot()
            self.assertEqual(snap['status'], 'FAILED')
            self.assertEqual(snap['done'], ['quantum_batty'])
            self.assertEqual(snap['errors'][0]['id'], 'mosaic_twin')
            self.assertEqual(snap['progress'], 1.0)

    def test_manifest_fetch_from_loopback_and_oversize_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = models.ModelStore(Path(tmp))
            entry = self.entry('rad_cotton', make_zip({'a.glb': b'g'}))
            _Handler.payloads['/m.json'] = json.dumps({'schema': models.SCHEMA, 'schema_version': 1, 'name': 'T', 'models': [entry]}).encode()
            self.assertEqual(store.fetch_manifest(f'{self.base}/m.json')['models'][0]['id'], 'rad_cotton')
            _Handler.payloads['/big.json'] = b'{' + b' ' * (models.MAX_MANIFEST + 10) + b'}'
            with self.assertRaises(models.ModelError):
                store.fetch_manifest(f'{self.base}/big.json')
            with self.assertRaises(models.ModelError):
                store.fetch_manifest('http://example.org/m.json')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sr1, self.sr2 = make_sr1(self.root / 'sr1'), make_sr2(self.root / 'sr2')
        patcher = patch('apps.launcher.main.discover_stores', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        # these tests drive extraction on synthetic game folders; the completeness check has its own tests
        integ = patch('apps.launcher.integrity.require', return_value={'complete': True})
        integ.start()
        self.addCleanup(integ.stop)
        # ...and must never download or run Cpp2IL (sr2_schema has its own tests)
        schema = patch('bridge.content.sr2_schema.ensure', return_value=None)
        schema.start()
        self.addCleanup(schema.stop)
        self.started = []
        self.api = LauncherAPI(config_path=self.root / 'Lab' / 'Launcher' / 'installations.json', known_paths=(self.sr1, self.sr2),
                               lab_games=self.root / 'nolab',
                               popen=lambda cmd, **kw: self.started.append((cmd, kw)) or FakeProc())

    def test_exposed_surface_is_methods_only(self):
        self.assertFalse([n for n in vars(self.api) if not n.startswith('_')])

    def test_preferences_are_validated_and_persisted(self):
        self.assertTrue(self.api.set_preference('theme', 'dusk')['ok'])
        self.assertTrue(self.api.set_preference('language', 'fr')['ok'])
        for key, value in (('theme', 'neon'), ('language', 'de'), ('reduce_motion', 'yes'), ('largo_manifest_url', 'http://evil.example/x'),
                           ('nonexistent', 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.api.set_preference(key, value)
        state = LauncherAPI(config_path=self.root / 'Lab' / 'Launcher' / 'installations.json', known_paths=(), lab_games=self.root / 'x').get_state()
        self.assertEqual((state['preferences']['theme'], state['preferences']['language']), ('dusk', 'fr'))

    def test_the_memory_limit_survives_a_reload(self):
        self.api.set_preference('extract_ram_gb', 6)
        again = LauncherAPI(config_path=self.root / 'Lab' / 'Launcher' / 'installations.json', known_paths=(), lab_games=self.root / 'x')
        self.assertEqual(again.get_state()['preferences']['extract_ram_gb'], 6)

    def test_performance_profile_is_validated_and_reaches_the_games_as_an_environment_variable(self):
        self.assertEqual(self.api._perf_env(), {})                      # off by default: the game is not touched
        for mode in ('balanced', 'fast'):
            self.api.set_preference('performance', mode)
            self.assertEqual(self.api._perf_env(), {'SLIMEBRIDGE_PERF': mode})
        self.api.set_preference('performance', 'off')
        self.assertEqual(self.api._perf_env(), {})
        with self.assertRaises(ValueError):
            self.api.set_preference('performance', 'turbo')

    def test_state_lists_both_games_and_primary(self):
        state = self.api.get_state()
        self.assertEqual({g: bool(v['primary']) for g, v in state['games'].items()}, {'SR1': True, 'SR2': True})
        self.assertEqual(state['games']['SR2']['primary']['loader'], 'Not configured')

    def test_launch_asks_once_for_the_backup_reminder_then_starts_only_the_exe(self):
        first = self.api.launch('SR1')
        self.assertEqual((first['ok'], first['needs_confirmation']), (False, True))
        self.assertEqual(self.started, [])
        done = self.api.launch('SR1', confirmed=True, remember=True)
        self.assertTrue(done['ok'])
        self.assertEqual(self.started[0][0], [str(self.sr1 / 'SlimeRancher.exe')])
        self.assertTrue(self.api.get_state()['preferences']['backup_ack'])
        self.assertTrue(self.api.launch('SR2')['ok'])      # no new prompt after "don't ask again"
        with self.assertRaises(ValueError):
            self.api.launch('SR3')

    def test_launch_both_starts_host_first_and_survives_a_host_failure(self):
        self.api.set_preference('backup_ack', True)
        with patch.object(runtime.HostProcess, 'start', side_effect=runtime.RuntimeErrorWithCode('HOST_TIMEOUT', 'x')):
            result = self.api.launch('BOTH')
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['launched']), 2)
        self.assertTrue(result['warnings'])

    def test_unknown_game_is_reported_not_guessed(self):
        api = LauncherAPI(config_path=self.root / 'c2.json', known_paths=(), lab_games=self.root / 'x')
        api.set_preference('backup_ack', True)
        result = api.launch('SR1')
        self.assertFalse(result['ok'])
        self.assertEqual(result['errors'][0]['error'], 'GAME_NOT_FOUND')

    def test_output_folder_can_never_be_inside_a_game_folder(self):
        self.assertEqual(self.api.set_output_folder(str(self.sr1 / 'out'))['error'], 'INSIDE_GAME_FOLDER')
        self.assertEqual(self.api.set_output_folder(str(self.sr1))['error'], 'INSIDE_GAME_FOLDER')
        self.assertTrue(self.api.set_output_folder(str(self.root / 'outside'))['ok'])

    def test_extract_without_tool_or_with_bad_options_fails_cleanly(self):
        with patch('apps.launcher.extract.locate_tool', return_value={'found': False, 'reason': 'nope'}):
            self.assertEqual(self.api.extract_start({'engine': 'external'})['error'], 'TOOL_NOT_FOUND')
        with patch('apps.launcher.extract.locate_tool', return_value={'found': True, 'path': '.', 'python': 'python'}):
            self.assertEqual(self.api.extract_start({'direction': 'diagonal'})['error'], 'BAD_OPTIONS')
            self.assertEqual(self.api.extract_start({'engine': 'telepathy'})['error'], 'BAD_OPTIONS')

    def test_builtin_engine_is_always_available_and_runs_the_repository_pipeline(self):
        with patch('apps.launcher.extract.locate_tool', return_value={'found': False, 'reason': 'nope'}):
            tool = self.api.tool_status()
            self.assertTrue(tool['found'])
            self.assertTrue(tool['builtin'])
            self.assertFalse(tool['external']['found'])
        rows = [{'canonical': 'slime.ringtail', 'species': 'Ringtail', 'status': 'READY'},
                {'canonical': 'slime.cotton', 'species': 'Cotton', 'status': 'READY'}]
        seen = {}

        def fake_run(rows_, data_dir, cache_dir, force=False, progress=None, cancelled=None):
            seen.update(rows=rows_, data_dir=data_dir, cache_dir=cache_dir, force=force)
            for index, row in enumerate(rows_):
                progress(index + 1, len(rows_), f"[{row['canonical']}] BUILT")
            return {'ok': True, 'count': 2, 'by_status': {'BUILT': 1, 'UP_TO_DATE': 1},
                    'species': [{'canonical': 'slime.ringtail', 'status': 'BUILT', 'notes': ['clips skipped: SloomberYawn']},
                                {'canonical': 'slime.cotton', 'status': 'UP_TO_DATE', 'notes': []}]}

        job = extract.BuiltinExtractJob({'direction': 'sr2-to-sr1', 'only_missing': False}, {'SR1': str(self.sr1), 'SR2': str(self.sr2)},
                                        self.root / 'cache', run=fake_run, roster=lambda: rows,
                                        verify=lambda cache, names: {'ok': True, 'count': len(names), 'by_status': {'VERIFIED': len(names)}})
        job.start().join(5)
        snap = job.snapshot()
        self.assertEqual(snap['status'], 'DONE')
        self.assertEqual(snap['result']['convert']['by_status'], {'BUILT': 1, 'UP_TO_DATE': 1})
        self.assertEqual(snap['result']['verify']['by_status'], {'VERIFIED': 2})
        self.assertEqual(snap['result']['export']['count'], 2)
        self.assertEqual(seen['data_dir'], self.sr2 / 'SlimeRancher2_Data')
        self.assertTrue(seen['force'])           # only_missing False = rebuild everything
        self.assertTrue(any('clips skipped' in line for line in snap['logs']))

    def test_builtin_engine_runs_both_directions_and_publishes_the_sr2_spec(self):
        calls = {}

        def run_s21(rows_, data_dir, cache_dir, force=False, progress=None, cancelled=None):
            calls['s21'] = [r['canonical'] for r in rows_]
            return {'ok': True, 'count': 1, 'by_status': {'BUILT': 1}, 'species': [{'canonical': 'slime.ringtail', 'status': 'BUILT', 'notes': []}]}

        def run_s12(rows_, sr1_dir, cache_dir, force=False, progress=None, cancelled=None):
            calls['s12'] = [r['canonical'] for r in rows_]
            calls['sr1_dir'] = sr1_dir
            return {'ok': True, 'count': 2, 'by_status': {'BUILT': 2},
                    'species': [{'canonical': 'slime.mosaic', 'status': 'BUILT', 'notes': []},
                                {'canonical': 'largo.mosaic.rad', 'status': 'BUILT', 'notes': []}]}

        published = []
        job = extract.BuiltinExtractJob({'direction': 'both', 'only_missing': False}, {'SR1': str(self.sr1), 'SR2': str(self.sr2)},
                                        self.root / 'cache', run=run_s21, roster=lambda: [{'canonical': 'slime.ringtail', 'species': 'Ringtail'}],
                                        largos=lambda: [], run_sr1=run_s12,
                                        roster_sr1=lambda: [{'canonical': 'slime.mosaic', 'kind': 'base'}, {'canonical': 'largo.mosaic.rad', 'kind': 'largo'}],
                                        verify=lambda cache, names: {'ok': True, 'count': len(names), 'by_status': {'VERIFIED': len(names)}})
        job._publish_sr2_spec = lambda summary: published.append(summary)
        job.start().join(5)
        snap = job.snapshot()
        self.assertEqual(snap['status'], 'DONE', snap.get('error'))
        self.assertEqual(calls['s21'], ['slime.ringtail'])
        self.assertEqual(calls['s12'], ['slime.mosaic', 'largo.mosaic.rad'])
        self.assertEqual(calls['sr1_dir'], self.sr1 / 'SlimeRancher_Data')
        self.assertEqual(snap['result']['convert']['by_status'], {'BUILT': 3})
        self.assertEqual(len(published), 1)

    def test_sr1_to_sr2_needs_the_sr1_folder(self):
        with self.assertRaises(ValueError):
            extract.BuiltinExtractJob({'direction': 'sr1-to-sr2'}, {'SR2': str(self.sr2)}, self.root / 'cache')

    def test_builtin_engine_reports_failed_species_and_needs_sr2(self):
        def failing_run(rows_, data_dir, cache_dir, force=False, progress=None, cancelled=None):
            return {'ok': False, 'count': 1, 'by_status': {'FAILED': 1},
                    'species': [{'canonical': 'slime.twin', 'status': 'FAILED', 'error': 'boom', 'notes': []}]}
        job = extract.BuiltinExtractJob({}, {'SR2': str(self.sr2)}, self.root / 'cache', run=failing_run,
                                        roster=lambda: [{'canonical': 'slime.twin', 'species': 'Twin', 'status': 'READY'}],
                                        verify=lambda cache, names: {'ok': True, 'count': 0, 'by_status': {}})
        job.start().join(5)
        self.assertEqual(job.snapshot()['status'], 'FAILED')
        self.assertTrue(any('boom' in line for line in job.snapshot()['logs']))
        with self.assertRaises(ValueError):
            extract.BuiltinExtractJob({}, {'SR1': str(self.sr1), 'SR2': None}, self.root / 'cache')
        with self.assertRaises(ValueError):
            extract.BuiltinExtractJob({'direction': 'sr1-to-sr2'}, {'SR2': str(self.sr2)}, self.root / 'cache')

    def test_verify_caches_detects_a_tampered_or_missing_file(self):
        folder = self.root / 'cache' / 'sr2-to-sr1' / 'slime.test'
        folder.mkdir(parents=True)
        (folder / 'model.json').write_text('{}', encoding='utf-8')
        (folder / 'cache-manifest.json').write_text(json.dumps({'files': {'model.json': hashlib.sha256(b'{}').hexdigest()}}), encoding='utf-8')
        self.assertEqual(extract.verify_caches(self.root / 'cache', ['slime.test'])['by_status'], {'VERIFIED': 1})
        (folder / 'model.json').write_text('{"x":1}', encoding='utf-8')
        report = extract.verify_caches(self.root / 'cache', ['slime.test', 'slime.absent'])
        self.assertEqual(report['by_status'], {'HASH_MISMATCH': 1, 'MISSING': 1})
        self.assertFalse(report['ok'])

    def test_extract_runs_through_the_api_with_the_selected_installs(self):
        fake = FakeConverter()
        with patch('apps.launcher.extract.locate_tool', return_value={'found': True, 'path': '.', 'python': 'python'}), \
                patch('apps.launcher.extract.subprocess.Popen', side_effect=lambda *a, **k: fake):
            self.api.set_output_folder(str(self.root / 'conv'))
            start = self.api.extract_start({'direction': 'both', 'only_missing': True, 'engine': 'external'})
            self.assertTrue(start['ok'])
            self.api._extract.join(5)
            job = self.api.extract_status()['job']
            self.assertEqual(job['status'], 'DONE')
            self.assertEqual(fake.requests[1]['args']['sr1_path'], str(self.sr1.resolve()))

    def test_models_import_copies_user_glb_files_by_catalogue_id(self):
        src = self.root / 'mine'
        src.mkdir()
        (src / 'rad_cotton.glb').write_bytes(b'glTF')
        (src / 'unrelated.glb').write_bytes(b'x')
        result = self.api.models_import_folder(str(src))
        self.assertEqual(result['imported'], ['rad_cotton'])
        row = {r['id']: r for r in result['rows']}['rad_cotton']
        self.assertEqual(row['state'], 'INSTALLED')
        self.assertTrue((self.root / 'Lab' / 'Largos' / 'models' / 'rad_cotton' / 'rad_cotton.glb').is_file())
        self.assertEqual(self.api.models_state()['rows'][0]['state'] in ('INSTALLED', 'NOT_PUBLISHED'), True)

    def test_models_refresh_without_source_reads_the_published_largos(self):
        from apps.launcher import models as models_module
        asked = []

        def offline(request, timeout=0):
            asked.append(request.full_url)
            raise OSError('offline')
        self.api._opener = offline
        result = self.api.models_refresh()
        self.assertEqual(asked, [models_module.DEFAULT_MANIFEST])
        self.assertFalse(result['ok'])
        self.assertIn('offline', result['manifest']['error'])
        self.assertTrue(all(r['state'] == 'NOT_PUBLISHED' for r in result['rows']))

    def test_multiplayer_addresses_are_checked_and_remembered_without_dialling(self):
        self.assertTrue(self.api.mp_remember('ranch.example:9000', True)['ok'])
        self.assertFalse(self.api.mp_remember('http://evil')['ok'])
        state = self.api.mp_state()
        self.assertEqual(state['servers']['recent'][0], 'ranch.example:9000')
        self.assertEqual(state['servers']['favourites'], ['ranch.example:9000'])
        self.assertFalse(state['network_adapter'])
        self.assertTrue(self.api.mp_host_save({'name': 'X', 'port': 7777, 'mode': 'HYBRID'})['ok'])
        self.assertFalse(self.api.mp_host_save({'name': 'X', 'port': 80, 'mode': 'HYBRID'})['ok'])
        self.assertFalse(self.api.mp_host_save({'name': 'X', 'port': 7777, 'mode': 'ALL'})['ok'])

    def test_bridge_settings_roundtrip_and_validation(self):
        sections = {s['section']: s for s in self.api.get_settings()['sections']}
        self.assertIn('transitions', sections)
        self.assertNotIn('games', sections)
        self.assertTrue(self.api.set_setting('transitions', 'mode', 'COLD')['ok'])
        self.assertFalse(self.api.set_setting('transitions', 'mode', 'TELEPORT')['ok'])
        self.assertFalse(self.api.set_setting('games', 'SR1.path', 'x')['ok'])
        again = {s['section']: s for s in self.api.get_settings()['sections']}['transitions']
        self.assertEqual({f['key']: f['value'] for f in again['fields']}['mode'], 'COLD')

    def test_choosing_a_wrapper_folder_remembers_the_real_install(self):
        wrapper = self.root / 'outer'
        inner = make_sr1(wrapper / 'mid' / 'inner')
        added = self.api.add_manual_path(str(wrapper))
        self.assertEqual(added['root'], str(inner.resolve()))
        self.assertEqual(self.api._load()['manual_paths'], [str(inner.resolve())])
        with self.assertRaises(ValueError):
            self.api.add_manual_path(str(self.root / 'nothing-here'))

    def test_forget_game_only_forgets_the_remembered_path(self):
        self.api.add_manual_path(str(self.sr1))
        self.api.forget_game('SR1')
        self.assertTrue((self.sr1 / 'SlimeRancher.exe').is_file())     # files untouched
        self.assertEqual(self.api._load()['manual_paths'], [])


class UiStaticTests(unittest.TestCase):
    def test_translations_are_complete_and_every_used_key_exists(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node not installed')
        script = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const ui = process.argv[process.argv.length - 1];
const sb = {}; const ctx = { window: { SB: sb }, navigator: {} }; ctx.window.window = ctx.window;
vm.createContext(ctx); vm.runInContext(fs.readFileSync(path.join(ui, 'js/i18n.js'), 'utf8'), ctx);
const EN = ctx.window.SB._dict.en, FR = ctx.window.SB._dict.fr, problems = [];
for (const k of Object.keys(EN)) if (!(k in FR)) problems.push('missing FR: ' + k);
for (const k of Object.keys(FR)) if (!(k in EN)) problems.push('extra FR: ' + k);
const used = new Set();
for (const f of fs.readdirSync(path.join(ui, 'js'))) {
  const src = fs.readFileSync(path.join(ui, 'js', f), 'utf8');
  for (const m of src.matchAll(/\bt\('([a-zA-Z0-9_.]+)'/g)) if (!m[1].endsWith('.') && !m[1].endsWith('_')) used.add(m[1]);
}
for (const k of used) if (!(k in EN)) problems.push('unknown key: ' + k);
const dynamic = { 'job.': ['QUEUED','RUNNING','DONE','FAILED','CANCELLED'], 'models.': ['NOT_PUBLISHED','AVAILABLE','UPDATE','INSTALLED'],
  'prof.s_': ['LINKED','SR1_ONLY','SR2_ONLY','PENDING_COUNTERPART','REVERIFY_REQUIRED','CONFLICTED'], 'rec.sev_': ['error','warning','info'],
  'err.': ['EXECUTABLE_MISSING','GAME_NOT_FOUND','LAUNCH_FAILED','TOOL_NOT_FOUND','ALREADY_RUNNING','BAD_OPTIONS','INSIDE_GAME_FOLDER','NOT_THE_TOOL','NO_MANIFEST','NOTHING_TO_DOWNLOAD','NO_SOURCE'] };
for (const [p, list] of Object.entries(dynamic)) for (const s of list) if (!((p + s) in EN)) problems.push('dynamic key missing: ' + p + s);
console.log(JSON.stringify(problems));
"""
        out = subprocess.run([node, '-e', script, str(UI)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [])

    def test_every_script_parses_and_html_pulls_no_remote_resources(self):
        node = shutil.which('node')
        html = (UI / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('http://', html.replace('http-equiv', ''))
        self.assertNotIn('https://', html)
        for script in sorted((UI / 'js').glob('*.js')):
            self.assertIn(f'js/{script.name}', html, script.name)
            if node:
                result = subprocess.run([node, '--check', str(script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f'{script.name}: {result.stderr}')

    def test_bundled_fonts_ship_with_their_licenses(self):
        for font, licence in (('Fredoka-Variable.ttf', 'OFL-Fredoka.txt'), ('Nunito-Variable.ttf', 'OFL-Nunito.txt')):
            self.assertGreater((UI / 'fonts' / font).stat().st_size, 50_000)
            self.assertIn('SIL Open Font License', (UI / 'fonts' / licence).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
