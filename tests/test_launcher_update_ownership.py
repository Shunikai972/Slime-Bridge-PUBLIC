"""Fail-closed self-update preparation. Bytes and directories are synthetic, no helper runs."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from apps.launcher import updater
from test_launcher_updater import _opener, _zip_bytes


class UpdatePreparationRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='update-ownership-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        platform = patch.object(updater, 'WINDOWS', True)
        platform.start()
        self.addCleanup(platform.stop)

    def download(self, archive, *, version='9.9.9', sums='valid'):
        name = updater.asset_name(version)
        assets = [{'name': name, 'browser_download_url': 'https://fixture.invalid/archive'}]
        files = {'https://fixture.invalid/archive': archive}
        if sums is not None:
            assets.append({'name': 'SHA256SUMS.txt', 'browser_download_url': 'https://fixture.invalid/sums'})
            if sums == 'valid':
                sums = hashlib.sha256(archive).hexdigest() + '  ' + name + '\n'
            files['https://fixture.invalid/sums'] = sums.encode()
        return _opener({'tag_name':'v'+version,'assets':assets}, files)

    def test_archive_without_release_checksums_is_never_accepted(self):
        data = _zip_bytes('9.9.9')
        with self.assertRaises(updater.UpdateError):
            updater.prepare(self.download(data, sums=None), cache=self.cache)

    def test_checksums_must_cover_this_exact_platform_archive(self):
        data = _zip_bytes('9.9.9')
        with self.assertRaises(updater.UpdateError):
            updater.prepare(self.download(data, sums='0'*64+'  other.zip\n'), cache=self.cache)

    def test_checksum_row_must_have_a_real_sha256(self):
        data = _zip_bytes('9.9.9')
        with self.assertRaises(updater.UpdateError):
            updater.prepare(self.download(data, sums='not-a-digest  '+updater.asset_name('9.9.9')+'\n'), cache=self.cache)

    def test_version_cannot_expand_cache_deletion_scope(self):
        victim = self.root/'victim'
        victim.mkdir()
        original = victim/'existing-user-file.txt'
        original.write_bytes(b'not update staging')
        with self.assertRaises(updater.UpdateError):
            updater.prepare(self.download(b'not a zip',version='../victim'), cache=self.cache)
        self.assertEqual(original.read_bytes(), b'not update staging')

    def test_preparation_does_not_delete_an_existing_version_directory(self):
        previous = self.cache/'9.9.9'
        previous.mkdir()
        original = previous/'retained.txt'
        original.write_bytes(b'previous evidence')
        ready = updater.prepare(self.download(_zip_bytes('9.9.9')), cache=self.cache)
        self.assertEqual(original.read_bytes(), b'previous evidence')
        self.assertTrue((Path(ready['folder'])/'SlimeBridge.exe').is_file())

    def test_archive_cannot_supply_player_lab_data(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('SlimeBridge/SlimeBridge.exe', b'synthetic exe')
            zf.writestr('SlimeBridge/Lab/Launcher/installations.json', '{}')
        with self.assertRaises(updater.UpdateError):
            updater.prepare(self.download(buf.getvalue()), cache=self.cache)

    def test_archive_accepts_the_empty_lab_directory_and_distribution_readme(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('SlimeBridge/', b'')
            zf.writestr('SlimeBridge/SlimeBridge.exe', b'synthetic exe')
            zf.writestr('SlimeBridge/Lab/', b'')
            zf.writestr('SlimeBridge/Lab/README.md', b'distribution readme')
        ready = updater.prepare(self.download(buf.getvalue()), cache=self.cache)
        self.assertTrue((Path(ready['folder'])/'Lab'/'README.md').is_file())

    def test_malformed_release_fields_are_reported_as_update_errors(self):
        for bad in ({'assets':[None]}, {'assets':{}}, {'body':3}, {'assets':[{'name':'x'}]}):
            release = dict(tag_name='v9.9.9', assets=[], body='notes')
            release.update(bad)
            with self.subTest(release=release), self.assertRaises(updater.UpdateError):
                updater.latest(_opener(release, {}))

    def test_archive_plan_refuses_link_and_case_alias_entries(self):
        for members in (
            [('SlimeBridge/SlimeBridge.exe', b'a'), ('SlimeBridge/slimebridge.EXE', b'b')],
            [('SlimeBridge/SlimeBridge.exe', b'a'), ('SlimeBridge/link', b'target')],
        ):
            with self.subTest(members=[m[0]for m in members]):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, 'w') as zf:
                    for name, data in members:
                        info = zipfile.ZipInfo(name)
                        if name.endswith('/link'):
                            info.create_system=3
                            info.external_attr=(0o120777<<16)
                        zf.writestr(info,data)
                with self.assertRaises(updater.UpdateError):
                    updater.prepare(self.download(buf.getvalue()), cache=self.cache)

    def test_archive_refuses_names_windows_would_sanitize_to_another_entry(self):
        for character in ('<', '>', '"', '|', '?', '*', chr(1)):
            with self.subTest(character=repr(character)):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, 'w') as zf:
                    zf.writestr('SlimeBridge/SlimeBridge.exe', b'exe')
                    zf.writestr('SlimeBridge/a_b.txt', b'original')
                    zf.writestr('SlimeBridge/a'+character+'b.txt', b'alias')
                with self.assertRaises(updater.UpdateError):
                    updater.prepare(self.download(buf.getvalue()), cache=self.cache)
