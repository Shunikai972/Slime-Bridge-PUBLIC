import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from apps.launcher import loaders


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'

    def _sr2(self):
        game = self.root / 'SR2'
        game.mkdir()
        (game / 'SlimeRancher2.exe').write_bytes(b'x')
        (game / 'GameAssembly.dll').write_bytes(b'x')
        return game

    def _pin(self, realm, payload):
        spec = dict(loaders.LOADERS[realm], sha256=hashlib.sha256(payload).hexdigest())
        return mock.patch.dict(loaders.LOADERS, {realm: spec})

    def test_melonloader_layouts_are_recognised(self):
        game = self._sr2()
        self.assertFalse(loaders.present('SR2', game))
        (game / 'winhttp.dll').write_bytes(b'x')                 # installer may use another proxy name
        (game / 'MelonLoader' / 'net35').mkdir(parents=True)
        (game / 'MelonLoader' / 'net35' / 'MelonLoader.dll').write_bytes(b'x')
        self.assertTrue(loaders.present('SR2', game))
        self.assertEqual(loaders.describe('SR2', game), 'MelonLoader')

    def test_sr2_install_extracts_the_verified_release(self):
        game = self._sr2()
        payload = _zip({'version.dll': b'proxy', 'MelonLoader/net6/MelonLoader.dll': b'core'})
        opener = lambda req, timeout: _Response(payload)
        with self._pin('SR2', payload):
            result = loaders.install('SR2', game, cache=self.cache, opener=opener)
        self.assertEqual(result['state'], 'installed')
        self.assertEqual((game / 'version.dll').read_bytes(), b'proxy')
        self.assertTrue((game / 'SlimeBridge' / 'loader.json').is_file())
        self.assertEqual(loaders.install('SR2', game, cache=self.cache)['state'], 'current')

    def test_tampered_download_is_refused(self):
        game = self._sr2()
        good = _zip({'version.dll': b'proxy'})
        with self._pin('SR2', good):
            with self.assertRaises(loaders.LoaderError) as ctx:
                loaders.install('SR2', game, cache=self.cache, opener=lambda req, timeout: _Response(b'evil'))
        self.assertEqual(ctx.exception.code, 'DOWNLOAD_VERIFY_FAILED')
        self.assertFalse((game / 'version.dll').exists())

    def test_sr1_runs_the_official_installer_unattended(self):
        game = self.root / 'SR1'
        (game / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
        (game / 'SlimeRancher.exe').write_bytes(b'x')
        (game / 'SlimeRancher_Data' / 'Managed' / 'Assembly-CSharp.dll').write_bytes(b'x')
        payload = _zip({'SRMLInstaller.exe': b'installer'})
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            (game / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll').write_bytes(b'srml')
            return mock.Mock(returncode=0, stdout='Installation complete!', stderr='')
        with self._pin('SR1', payload):
            result = loaders.install('SR1', game, cache=self.cache, opener=lambda req, timeout: _Response(payload), run=fake_run)
        self.assertEqual(result['loader'], 'SRML')
        self.assertEqual(calls[0][1:], ['-v', str(game)])            # silent "Vortex" mode, no prompts

    def test_not_a_game_folder_is_refused_before_downloading(self):
        with self.assertRaises(loaders.LoaderError) as ctx:
            loaders.install('SR2', self.root, cache=self.cache, opener=lambda *a, **k: self.fail('downloaded'))
        self.assertEqual(ctx.exception.code, 'NOT_A_GAME_FOLDER')

    def test_ensure_never_raises(self):
        out = loaders.ensure('SR2', self.root / 'nowhere')
        self.assertEqual(out['status'], 'FAILED')


if __name__ == '__main__':
    unittest.main()
