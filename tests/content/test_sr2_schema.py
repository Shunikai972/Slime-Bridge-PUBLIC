import hashlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.content import sr2_schema


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _game(root: Path):
    meta = root / 'SlimeRancher2_Data' / 'il2cpp_data' / 'Metadata'
    meta.mkdir(parents=True)
    (meta / 'global-metadata.dat').write_bytes(b'meta' * 100)
    (root / 'GameAssembly.dll').write_bytes(b'asm')
    return root


class Sr2SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.game, self.cache = _game(base / 'game'), base / 'cache'
        self.files = {sr2_schema.TOOL['url']: b'exe', sr2_schema.PLUGIN['url']: b'plugin'}
        self.pins = mock.patch.multiple(sr2_schema,
                                        TOOL={**sr2_schema.TOOL, 'sha256': hashlib.sha256(b'exe').hexdigest()},
                                        PLUGIN={**sr2_schema.PLUGIN, 'sha256': hashlib.sha256(b'plugin').hexdigest()})
        self.pins.start()
        self.files = {sr2_schema.TOOL['url']: b'exe', sr2_schema.PLUGIN['url']: b'plugin'}

    def tearDown(self):
        self.pins.stop()
        self.tmp.cleanup()

    def opener(self, req, timeout=0):
        return _Resp(self.files[req.full_url])

    def test_melonloader_schema_is_used_first(self):
        out = self.game / sr2_schema.MELON_OUT
        out.mkdir(parents=True)
        (out / 'Assembly-CSharp.dll').write_bytes(b'x')
        self.assertEqual(sr2_schema.ensure(self.game, log=lambda m: None, cache=self.cache, run=self.fail), out)

    @unittest.skipUnless(os.name == 'nt', 'Cpp2IL is run on Windows')
    def test_makes_the_schema_with_melonloaders_command(self):
        seen = {}

        def run(cmd, cwd, **kw):
            seen.update(cmd=cmd, cwd=cwd)
            out = Path(cwd) / 'cpp2il_out'
            out.mkdir()
            (out / 'Assembly-CSharp.dll').write_bytes(b'dll')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        out = sr2_schema.ensure(self.game, log=lambda m: None, cache=self.cache, opener=self.opener, run=run)
        self.assertEqual(seen['cmd'][1:], ['--game-path', str(self.game), '--exe-name', 'SlimeRancher2', '--output-as', 'dummydll',
                                           '--use-processor', 'attributeanalyzer', 'attributeinjector'])
        self.assertTrue((self.cache / 'tool' / 'Plugins' / 'Cpp2IL.Plugin.StrippedCodeRegSupport.dll').is_file())
        self.assertEqual(sr2_schema.schema_dir(self.game, self.cache), out)        # cached for this build

    @unittest.skipUnless(os.name == 'nt', 'Cpp2IL is run on Windows')
    def test_tampered_download_is_refused(self):
        self.files[sr2_schema.TOOL['url']] = b'not the pinned file'
        with self.assertRaises(sr2_schema.SchemaError):
            sr2_schema.ensure(self.game, log=lambda m: None, cache=self.cache, opener=self.opener, run=self.fail)

    @unittest.skipUnless(os.name == 'nt', 'Cpp2IL is run on Windows')
    def test_failed_run_reports_cpp2il_output(self):
        run = lambda cmd, cwd, **kw: subprocess.CompletedProcess(cmd, 1, 'boom line', '')
        with self.assertRaisesRegex(sr2_schema.SchemaError, 'boom line'):
            sr2_schema.ensure(self.game, log=lambda m: None, cache=self.cache, opener=self.opener, run=run)

    def test_a_different_build_does_not_reuse_the_schema(self):
        key = sr2_schema.build_key(self.game)
        (self.game / 'SlimeRancher2_Data' / 'il2cpp_data' / 'Metadata' / 'global-metadata.dat').write_bytes(b'other build')
        self.assertNotEqual(sr2_schema.build_key(self.game), key)


if __name__ == '__main__':
    unittest.main()
