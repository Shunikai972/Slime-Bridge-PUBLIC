import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def srml_version_ok(value: str) -> bool:
    """Mirror of SRML ModVersion.Parse (SRML/SRMod.cs): 2 or 3 dot-separated integers, nothing else."""
    parts = value.split('.')
    return 2 <= len(parts) <= 3 and all(re.fullmatch(r'-?\d+', part) for part in parts)


class Sr1ManifestTests(unittest.TestCase):
    def test_srml_parser_mirror(self):
        self.assertTrue(srml_version_ok('0.1.0'))
        self.assertTrue(srml_version_ok('1.2'))
        self.assertFalse(srml_version_ok('0.1.0-dev'))
        self.assertFalse(srml_version_ok('1'))
        self.assertFalse(srml_version_ok('1.2.3.4'))

    def test_sr1_modinfo_is_loadable_by_srml(self):
        info = json.loads((REPO / 'mods' / 'sr1-bridge' / 'modinfo.json').read_text(encoding='utf-8'))
        self.assertRegex(info['id'], r'^[a-z0-9_]+$')
        self.assertTrue(srml_version_ok(info['version']), info['version'])

    def test_sr1_plugin_version_matches_modinfo(self):
        info = json.loads((REPO / 'mods' / 'sr1-bridge' / 'modinfo.json').read_text(encoding='utf-8'))
        source = (REPO / 'mods' / 'sr1-bridge' / 'BridgeEntry.cs').read_text(encoding='utf-8')
        self.assertIn(f'PluginVersion = "{info["version"]}"', source)

    def test_sr2_plugin_version_matches_release_version(self):
        version = (REPO / 'VERSION').read_text(encoding='utf-8').strip()
        source = (REPO / 'mods' / 'sr2-bridge' / 'BridgeMod.cs').read_text(encoding='utf-8')
        self.assertIn(f'PluginVersion = "{version}"', source)

    def test_oversized_sr1_inventory_keeps_process_only_heartbeat(self):
        source = (REPO / 'mods' / 'sr1-bridge' / 'BridgeEntry.cs').read_text(encoding='utf-8')
        self.assertIn('snapshot.pink_slime_count > 1_000_000 || snapshot.pink_plort_count > 1_000_000', source)
        self.assertRegex(source, r'if \(s == null\) return head \+ "}";')

    def test_lab_autoload_requires_this_exact_lab_executable(self):
        oracle = (REPO / 'tools' / 'lab' / 'oracle.py').read_text(encoding='utf-8')
        self.assertIn('SLIMEBRIDGE_LAB_EXE', oracle)
        for realm, filename in (('sr1-bridge', 'BridgeEntry.cs'), ('sr2-bridge', 'BridgeMod.cs')):
            source = (REPO / 'mods' / realm / filename).read_text(encoding='utf-8')
            self.assertIn('SLIMEBRIDGE_LAB_EXE', source)
            self.assertIn('MainModule', source)

    def test_shared_journal_has_no_dependency_missing_from_unity_mono(self):
        # Real SR1 run (pid 19432): OpJournal.Open threw TypeLoadException because SMDiagnostics,
        # required by DataContractJsonSerializer, is absent from SR1's Unity Mono Managed folder.
        source = (REPO / 'mods' / 'shared' / 'BridgeCommands.cs').read_text(encoding='utf-8')
        self.assertNotIn('System.Runtime.Serialization', source)
        project = (REPO / 'mods' / 'sr1-bridge' / 'SlimeBridge.SR1.csproj').read_text(encoding='utf-8')
        self.assertNotIn('System.Runtime.Serialization', project)

    def test_sr1_status_payload_is_serialized_explicitly(self):
        # Runtime run 2 proved JsonUtility dropped the nested status; the payload is hand-built.
        source = (REPO / 'mods' / 'sr1-bridge' / 'BridgeEntry.cs').read_text(encoding='utf-8')
        self.assertNotIn('JsonUtility.ToJson(heartbeat)', source)
        for key in ('player_alive', 'save_id', 'pink_slime_count', 'pink_plort_count'):
            self.assertIn(f'\\"{key}\\"', source)


class Sr2StatusTests(unittest.TestCase):
    def test_unresolved_pink_mapping_does_not_emit_observed_zero(self):
        source = (REPO / 'mods' / 'sr2-bridge' / 'BridgeMod.cs').read_text(encoding='utf-8')
        for kind in ('pink_slime_count', 'pink_plort_count'):
            self.assertIn(f'public int? {kind}', source)
        # Counts start null and are only assigned after BOTH reference IDs resolve at runtime.
        self.assertIn('int? pinkSlimes = null, pinkPlorts = null;', source)
        self.assertRegex(source, r'TryFindIdentifiableTypeByReferenceId\(PinkSlimeRef[^;]*'
                                 r'TryFindIdentifiableTypeByReferenceId\(PinkPlortRef')
        self.assertIn('DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull', source)

    def test_sr2_registry_ids_match_plugin_reference_constants(self):
        registry = json.loads((REPO / 'bridge' / 'registry' / 'pink.json').read_text(encoding='utf-8'))
        source = (REPO / 'mods' / 'sr2-bridge' / 'BridgeMod.cs').read_text(encoding='utf-8')
        ids = {item['canonical_id']: item['native']['SR2']['id'] for item in registry['entries']}
        self.assertIn(f'PinkSlimeRef = "{ids["slime.pink"]}"', source)
        self.assertIn(f'PinkPlortRef = "{ids["plort.pink"]}"', source)


if __name__ == '__main__':
    unittest.main()
