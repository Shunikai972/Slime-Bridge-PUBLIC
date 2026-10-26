import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.lab.lab_identity import require_isolated_profile


class LabIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lab = self.root / 'Lab' / 'Games'
        game = self.lab / 'SR1'
        game.mkdir(parents=True)
        self.source = self.root / 'source-game'
        self.source.mkdir()
        (game / 'slimebridge-lab.json').write_text(json.dumps({'source': str(self.source)}))
        self.original = self.root / 'original-profile'
        (self.original / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher').mkdir(parents=True)
        self.profile = self.root / 'lab-profile'
        self.profile.mkdir()
        self.config = self.root / 'isolation.json'

    def configure(self, lab_sid='S-1-5-21-222', source_sid='S-1-5-21-111'):
        self.config.write_text(json.dumps({'source_user_sid': source_sid,
                                           'source_profile': str(self.original),
                                           'lab_user_sid': lab_sid,
                                           'lab_profile': str(self.profile)}))

    def check(self):
        return require_isolated_profile(['SR1'], lab_root=self.lab, config_path=self.config)

    def test_missing_configuration_refuses_game_launch(self):
        with self.assertRaisesRegex(RuntimeError, 'isolated'):
            self.check()

    def test_same_windows_identity_refused_even_if_profile_claims_otherwise(self):
        self.configure(lab_sid='S-1-5-21-111')
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-111', self.profile)):
            with self.assertRaisesRegex(RuntimeError, 'same Windows identity'):
                self.check()

    def test_source_profile_must_match_windows_sid_registration(self):
        self.configure()
        settings = json.loads(self.config.read_text())
        wrong_profile = self.root / 'other-profile'
        wrong_profile.mkdir()
        settings['source_profile'] = str(wrong_profile)
        self.config.write_text(json.dumps(settings))
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', return_value=self.original, create=True), \
             patch('tools.lab.lab_identity._path_owner_sid', return_value='S-1-5-21-111', create=True), \
             patch('tools.lab.lab_identity._can_write', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Windows profile registration'):
                self.check()

    def test_misdeclared_source_owner_refused_even_when_write_probes_deny(self):
        self.configure()
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', side_effect=lambda sid: self.original if sid.endswith('111') else self.profile, create=True), \
             patch('tools.lab.lab_identity._path_owner_sid', return_value='S-1-5-21-222', create=True), \
             patch('tools.lab.lab_identity._can_write', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'owner of original'):
                self.check()

    def test_registered_profile_directory_can_be_system_owned(self):
        self.configure()
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', side_effect=lambda sid: self.original if sid.endswith('111') else self.profile), \
             patch('tools.lab.lab_identity._path_owner_sid', side_effect=lambda path: 'S-1-5-18' if path == self.original else 'S-1-5-21-111'), \
             patch('tools.lab.lab_identity._can_write', return_value=False):
            self.assertIsNone(self.check())

    def test_writable_nested_save_folder_refused(self):
        self.configure()
        nested = self.original / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher' / 'nested'
        nested.mkdir()
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', side_effect=lambda sid: self.original if sid.endswith('111') else self.profile), \
             patch('tools.lab.lab_identity._path_owner_sid', return_value='S-1-5-21-111'), \
             patch('tools.lab.lab_identity._can_write', side_effect=lambda path: path == nested):
            with self.assertRaisesRegex(RuntimeError, 'writable original'):
                self.check()

    def test_inaccessible_save_root_fails_closed(self):
        self.configure()
        save_root = self.original / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher'
        original_stat = Path.stat
        def stat(path, *args, **kwargs):
            if path == save_root:
                raise PermissionError('blocked')
            return original_stat(path, *args, **kwargs)
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', side_effect=lambda sid: self.original if sid.endswith('111') else self.profile), \
             patch('tools.lab.lab_identity._path_owner_sid', return_value='S-1-5-21-111'), \
             patch('tools.lab.lab_identity._can_write', return_value=False), \
             patch('tools.lab.lab_identity.Path.stat', stat):
            with self.assertRaisesRegex(RuntimeError, 'save root'):
                self.check()

    def test_probe_checks_delete_and_metadata_rights_individually(self):
        from tools.lab.lab_identity import _can_write
        path = self.root / 'directory'
        path.mkdir()
        rights = []
        with patch('tools.lab.lab_identity._try_access', side_effect=lambda p, mask: rights.append(mask) or mask == 0x10000, create=True):
            self.assertTrue(_can_write(path))
        self.assertIn(0x00010000, rights)
        self.assertIn(0x00000100, rights)
        self.assertIn(0x00000002, rights)
        self.assertIn(0x00000040, rights)

    def test_writable_original_install_refused(self):
        self.configure()
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', side_effect=lambda sid: self.original if sid.endswith('111') else self.profile, create=True), \
             patch('tools.lab.lab_identity._path_owner_sid', return_value='S-1-5-21-111', create=True):
            with patch('tools.lab.lab_identity._can_write', return_value=True):
                with self.assertRaisesRegex(RuntimeError, 'writable original'):
                    self.check()

    def test_distinct_identity_with_inaccessible_sources_can_launch(self):
        self.configure()
        with patch('tools.lab.lab_identity._current_identity', return_value=('S-1-5-21-222', self.profile)), \
             patch('tools.lab.lab_identity._profile_for_sid', side_effect=lambda sid: self.original if sid.endswith('111') else self.profile, create=True), \
             patch('tools.lab.lab_identity._path_owner_sid', return_value='S-1-5-21-111', create=True):
            with patch('tools.lab.lab_identity._can_write', return_value=False):
                self.assertIsNone(self.check())


if __name__ == '__main__':
    unittest.main()
