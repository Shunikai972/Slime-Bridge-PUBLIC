import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from apps.launcher import updater


def _zip_bytes(version):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('SlimeBridge/SlimeBridge.exe' if updater.WINDOWS else 'SlimeBridge/SlimeBridge', b'exe')
        zf.writestr('SlimeBridge/VERSION', version)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(release, files):
    def open_(req, timeout=0):
        url = req.full_url
        if url == updater.API:
            return _Resp(json.dumps(release).encode())
        return _Resp(files[url])
    return open_


class UpdaterTests(unittest.TestCase):
    def _release(self, version, archive, sums=None):
        name = updater.asset_name(version)
        assets = [{'name': name, 'browser_download_url': 'https://x/' + name}]
        files = {'https://x/' + name: archive}
        if sums is not None:
            assets.append({'name': 'SHA256SUMS.txt', 'browser_download_url': 'https://x/sums'})
            files['https://x/sums'] = sums.encode()
        return {'tag_name': 'v' + version, 'body': 'notes', 'assets': assets}, files

    def test_check_compares_versions_numerically(self):
        rel, files = self._release('0.1.10', b'')
        with mock.patch.object(updater, 'current_version', return_value='0.1.9'):
            self.assertTrue(updater.check(_opener(rel, files))['update'])
        with mock.patch.object(updater, 'current_version', return_value='0.1.10'):
            self.assertFalse(updater.check(_opener(rel, files))['update'])

    def test_prepare_downloads_verifies_and_unpacks(self):
        if not updater.WINDOWS:
            self.skipTest('zip asset is the Windows build')
        data = _zip_bytes('9.9.9')
        sums = f'{hashlib.sha256(data).hexdigest()}  {updater.asset_name("9.9.9")}\n'
        rel, files = self._release('9.9.9', data, sums)
        with tempfile.TemporaryDirectory() as tmp:
            ready = updater.prepare(_opener(rel, files), cache=Path(tmp))
            self.assertEqual((Path(ready['folder']) / 'VERSION').read_text(), '9.9.9')

    def test_prepare_refuses_a_corrupted_download(self):
        if not updater.WINDOWS:
            self.skipTest('zip asset is the Windows build')
        rel, files = self._release('9.9.9', _zip_bytes('9.9.9'), f'{"0" * 64}  {updater.asset_name("9.9.9")}\n')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(updater.UpdateError) as cm:
            updater.prepare(_opener(rel, files), cache=Path(tmp))
        self.assertEqual(cm.exception.code, 'VERIFY_FAILED')

    def test_source_install_does_not_self_update(self):
        with mock.patch.object(sys, 'frozen', False, create=True), self.assertRaises(updater.UpdateError) as cm:
            updater.restart_into('x')
        self.assertEqual(cm.exception.code, 'SOURCE_INSTALL')

    def test_helper_waits_copies_without_deleting_and_relaunches(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(updater, 'install_dir', return_value=Path(tmp)):
            info = updater.restart_into(Path(tmp) / 'new', pid=4242, popen=lambda *a, **k: calls.append(a))
            script = Path(info['helper']).read_text()
        self.assertEqual(len(calls), 1)
        self.assertIn('4242', script)
        if updater.WINDOWS:
            self.assertIn('/E /IS /IT', script)
            self.assertNotIn('/MIR', script)          # never deletes the player's files
            self.assertIn('SlimeBridge.exe', script)


if __name__ == '__main__':
    unittest.main()
