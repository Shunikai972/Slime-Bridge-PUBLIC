import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.launcher import online

STARLIGHT, SR2MP = b'starlight 4.0.4 bytes', b'ranching together 0.3.9 bytes'
COMPONENTS = (
    {'id': 'starlight', 'name': 'Starlight', 'version': '4.0.4', 'file': 'Starlight.dll',
     'url': 'https://example.invalid/Starlight.dll', 'sha256': hashlib.sha256(STARLIGHT).hexdigest()},
    {'id': 'sr2mp', 'name': 'Ranching Together', 'version': 'Beta 0.3.9', 'file': 'SR2MP.dll',
     'url': 'https://example.invalid/SR2MP.dll', 'sha256': hashlib.sha256(SR2MP).hexdigest()},
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(payloads, calls):
    def opener(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(payloads[request.full_url])
    return opener


def make_sr2(root: Path) -> Path:
    game = root / 'SR2'
    (game / 'MelonLoader' / 'net6').mkdir(parents=True)
    (game / 'MelonLoader' / 'net6' / 'MelonLoader.dll').write_bytes(b'ml')
    (game / 'version.dll').write_bytes(b'proxy')     # a real MelonLoader install always has its proxy
    return game


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cache = self.base / 'cache'
        patcher = patch.object(online, 'SR2_COMPONENTS', COMPONENTS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sr2_downloads_once_verifies_installs_and_removes_only_its_files(self):
        game = make_sr2(self.base)
        (game / 'Mods').mkdir()
        (game / 'Mods' / 'SR2MP.dll').write_bytes(b'older multiplayer')
        (game / 'Mods' / 'Other.dll').write_bytes(b'someone else')
        calls = []
        opener = opener_for({COMPONENTS[0]['url']: STARLIGHT, COMPONENTS[1]['url']: SR2MP}, calls)
        self.assertEqual(online.sr2_status(game, self.cache)['state'], 'outdated')
        result = online.sr2_install(game, self.cache, opener=opener)
        self.assertEqual(result['changed'], ['Starlight', 'Ranching Together'])
        self.assertEqual((game / 'Mods' / 'Starlight.dll').read_bytes(), STARLIGHT)
        self.assertEqual((game / 'Mods' / 'SR2MP.dll').read_bytes(), SR2MP)
        self.assertEqual(online.sr2_status(game, self.cache)['state'], 'current')
        online.sr2_install(game, self.cache, opener=opener)                    # cached: no second download
        self.assertEqual(len(calls), 2)
        record = json.loads((game / 'SlimeBridge' / 'online.json').read_text(encoding='utf-8'))
        backup = Path(record['backups']['sr2mp'])
        self.assertEqual(backup.read_bytes(), b'older multiplayer')
        self.assertNotIn(backup.parent, [game / 'Mods'])                     # never a second copy inside Mods
        removed = online.sr2_remove(game)
        self.assertEqual(sorted(removed['removed']), ['Ranching Together', 'Starlight'])
        self.assertEqual((game / 'Mods' / 'SR2MP.dll').read_bytes(), b'older multiplayer')
        self.assertFalse((game / 'Mods' / 'Starlight.dll').exists())
        self.assertTrue((game / 'Mods' / 'Other.dll').exists())

    def test_a_tampered_download_is_refused_and_nothing_is_installed(self):
        game = make_sr2(self.base)
        opener = opener_for({COMPONENTS[0]['url']: STARLIGHT, COMPONENTS[1]['url']: b'tampered'}, [])
        with self.assertRaises(online.OnlineError) as err:
            online.sr2_install(game, self.cache, opener=opener)
        self.assertEqual(err.exception.code, 'DOWNLOAD_VERIFY_FAILED')
        self.assertFalse((game / 'Mods').exists())

    def test_ensure_never_raises_and_reports_a_missing_loader(self):
        game = self.base / 'bare'
        game.mkdir()
        self.assertEqual(online.ensure('SR2', game)['status'], 'LOADER_MISSING')


if __name__ == '__main__':
    unittest.main()
