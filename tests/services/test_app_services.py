"""Launcher application services: settings, installations, saves, content, Largos, recovery, updates."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from bridge.app import content as C
from bridge.app import installations as I
from bridge.app import largos as L
from bridge.app import recovery as R
from bridge.app import settings as ST
from bridge.app import updates as U
from bridge.app.saves import ProfileStoreCatalog, SaveManager, StaticCatalog
from bridge.conversion.targets import builtin_target_profile
from bridge.core import diagnostics as D
from bridge.deploy.executor import DeployExecutor, DeployJournal, MemoryFS
from bridge.deploy.planner import plan_deploy
from conversion import world
from conversion.test_batch import run
from services.test_counterpart_core import SESSION_A, Harness, receipts_for, wire


class SettingsTests(unittest.TestCase):
    def test_defaults_validation_and_update(self):
        settings, diags = ST.normalize(ST.DEFAULTS)
        self.assertEqual((settings, diags), (ST.DEFAULTS, []))
        changed = ST.update(settings, 'transitions', {'mode': 'COLD'})
        self.assertEqual(changed['transitions']['mode'], 'COLD')
        for section, change in (('transitions', {'mode': 'TELEPORT'}), ('transitions', {'evil': 1}),
                                ('nonexistent', {}), ('updates', {'channel': 'nightly'})):
            with self.subTest(section=section, change=change), self.assertRaises(ValueError):
                ST.update(settings, section, change)
        self.assertNotIn('advanced', ST.visible_sections(settings))
        dev = ST.update(settings, 'advanced', {'show_developer_tools': True})
        self.assertIn('advanced', ST.visible_sections(dev))

    def test_migration_and_bad_values_fall_back(self):
        old = {'schema': 'bridge-launcher-settings', 'schema_version': 1, 'warm_mode': False, 'overlay': False,
               'updates': {'channel': 'nightly', 'check_on_start': True}, 'mystery': 1}
        settings, diags = ST.normalize(old)
        self.assertEqual(settings['transitions']['mode'], 'COLD')
        self.assertFalse(settings['transitions']['overlay'])
        self.assertEqual(settings['updates']['channel'], 'stable')
        self.assertEqual({d['code'] for d in diags},
                         {'SETTINGS_MIGRATED', 'SETTINGS_UNKNOWN_KEY', 'SETTINGS_INVALID_VALUE'})
        self.assertEqual(ST.normalize('garbage')[0], ST.DEFAULTS)

    def test_excessive_json_nesting_is_preserved_as_corrupt(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            store = ST.SettingsStore(tmp)
            depth = max(sys.getrecursionlimit() * 4, 10000)
            contents = '[' * depth + '0' + ']' * depth
            store.path.write_text(contents, encoding='utf-8')
            settings, diags = store.load()
            self.assertEqual(settings, ST.DEFAULTS)
            self.assertEqual([d['code'] for d in diags], ['SETTINGS_UNREADABLE'])
            self.assertFalse(store.path.exists())
            self.assertEqual(store.path.with_name('settings.json.corrupt').read_text(encoding='utf-8'), contents)
            store.save(ST.update(settings, 'ui', {'language': 'fr'}))
            self.assertEqual(store.load()[0]['ui']['language'], 'fr')

    def test_store_roundtrip_and_corrupt_file_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ST.SettingsStore(tmp)
            self.assertEqual(store.load(), (ST.DEFAULTS, []))
            store.save(ST.update(ST.DEFAULTS, 'ui', {'language': 'fr'}))
            self.assertEqual(store.load()[0]['ui']['language'], 'fr')
            store.path.write_text('{not json')
            settings, diags = store.load()
            self.assertEqual(settings, ST.DEFAULTS)
            self.assertTrue((Path(tmp) / 'settings.json.corrupt').is_file())
            with self.assertRaises(ValueError):
                store.save({'schema': 'bad'})
            store.path.write_text('{"padding": "' + 'x' * (ST.MAX_BYTES + 10) + '"}')
            self.assertEqual(store.load()[0], ST.DEFAULTS)  # oversized: treated as corrupt, preserved aside


class InstallationTests(unittest.TestCase):
    def test_readiness_and_source_installs_are_read_only(self):
        provider = I.StaticInstallations([
            I.record('SR1', '/games/sr1', role='SOURCE', store='steam'),
            I.record('SR1', '/lab/sr1', fingerprint='a' * 64, build='sr1-1.4.4'),
            I.record('SR2', '/lab/sr2', plugin='MISSING'),
            {'realm': 'SR2', 'root': 7}])
        view = I.overview(provider)
        self.assertEqual(view['SR1']['readiness'], 'READY')
        self.assertEqual([a['readiness'] for a in view['SR1']['all']], ['READY', 'NEEDS_LAB_COPY'])
        self.assertEqual(view['SR2']['readiness'], 'NEEDS_PLUGIN')
        self.assertTrue(all(not a['destructive'] for r in view.values() for a in r['actions']))
        self.assertEqual(I.overview(I.StaticInstallations([]))['SR1']['readiness'], 'NOT_FOUND')
        wrong = dict(I.record('SR1', '/x'), runtime='il2cpp')
        self.assertEqual(I.assess(wrong)[0], 'INVALID')


class SaveManagerTests(unittest.TestCase):
    def test_pairs_actions_and_read_only_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            try:
                pid, cid = h.created('SR1', 'SR2')
                env = lambda realm: {'health': 'HEALTHY', 'slot_probe': {'complete': True, 'free_slots': [1]},
                                     'runtime_proven': True, 'build': 'build-1'}
                catalog = ProfileStoreCatalog(Path(tmp) / 'profiles.sqlite3')
                manager = SaveManager(h.svc, catalog, env)
                self.assertEqual(catalog.profile_ids(), [pid])
                detail = manager.detail(pid)
                self.assertEqual(detail['pair']['status'], 'PENDING_COUNTERPART')
                self.assertEqual(detail['decision']['decision'], 'WAIT_FOR_NATIVE_RESULT')
                h.svc.timeout(cid)
                self.assertEqual([a['id'] for a in manager.actions(pid)][0], 'RECONCILE')
                h.svc.observe_live(pid, 'SR1', build_fingerprint='build-1')
                self.assertEqual([a['id'] for a in manager.actions(pid)], ['RECONCILE'])
                op = h.svc.reconcile(cid, SESSION_A)['operation_id']
                h.svc.on_wire_result('SR2', wire('SR2', cid, op, kind='reconcile_save', applied=False), session=SESSION_A)
                self.assertIn('VERIFY_CANDIDATE', [a['id'] for a in manager.actions(pid)])
                h.svc.plan_verification(cid, build_fingerprint='build-1')
                h.svc.verify(cid, receipts_for(h.svc, cid, realm='SR2'))
                h.svc.link(cid, build_fingerprint='build-1')
                pairs = manager.pairs()
                self.assertEqual(pairs[0]['status'], 'LINKED')
                self.assertTrue(all(not a['destructive'] for a in manager.actions(pid)))
                self.assertEqual(ProfileStoreCatalog(Path(tmp) / 'missing.sqlite3').profile_ids(), [])
                self.assertEqual(SaveManager(h.svc, StaticCatalog([]), env).pairs(), [])
            finally:
                h.close()


class ContentManagerTests(unittest.TestCase):
    def test_catalogue_groups_and_plans(self):
        batch = run()
        doc = C.catalogue({'a': batch})
        rows = {r['canonical_id']: r for r in doc['rows']}
        self.assertIn('SHARED_NATIVE', rows['slime.pink']['groups'])
        self.assertIn('SR2_ONLY', rows['slime.azure']['groups'])
        self.assertIn('QUARANTINED', rows['slime.hollow']['groups'])
        self.assertIn('PENDING_ADAPTER', rows['slime.gale']['groups'])
        self.assertEqual(rows['slime.azure']['directions']['SR2->SR1']['verification'], 'NOT_RUN')
        self.assertGreater(doc['counts']['CONVERTED'], 0)
        self.assertEqual([r['canonical_id'] for r in C.filter_rows(doc, text='AZU')], ['slime.azure'])
        self.assertEqual(C.rebuild_plan(['slime.b', 'slime.a', 'slime.a'], 'SR2->SR1')['targets'],
                         ['package:slime.a', 'package:slime.b'])

    def test_state_change_and_rollback_plans(self):
        from services.test_deploy import ARTIFACTS, unit
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeployJournal(Path(tmp) / 'd.sqlite3')
            try:
                executor = DeployExecutor(journal, MemoryFS(), ARTIFACTS.get)
                v1, v2 = unit('slime.azure'), unit('slime.azure', (2, 0, 0))
                for u in (v1, v2):
                    executor.apply(plan_deploy(journal.inventory('SR1'), [{'key': u['key'], 'state': 'ENABLED',
                                                                           'unit': u}], history=journal.history()))
                disable = C.state_change_plan(journal, 'SR1', 'slime.azure', 'DISABLED')
                self.assertEqual([op['op'] for op in disable['operations']], ['DISABLE'])
                back = C.rollback_plan(journal, 'SR1', 'slime.azure')
                self.assertEqual([op['op'] for op in back['operations']], ['ROLLBACK'])
                self.assertIsNone(C.rollback_plan(journal, 'SR1', 'slime.unknown'))
            finally:
                journal.close()


class LargoServiceTests(unittest.TestCase):
    def test_library_rows_and_enable_plan_never_touch_user_files(self):
        from content.test_user_largo import snapshot, write_package
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / 'library'
            write_package(library / 'alpha_beta')
            before = snapshot(library)
            parents = {'slime.alpha': ['standard.vacpack'], 'slime.beta': ['standard.vacpack']}
            out = L.library(library, ledger={}, known_base_species=set(parents),
                            target_profiles=[world.target_profile('SR1'), builtin_target_profile('SR2')],
                            parent_capabilities=parents,
                            target_context={'SR1': {'native': set(), 'parents': set(parents)},
                                            'SR2': {'native': set(), 'parents': set(parents)}})
            row = out['rows'][0]
            self.assertEqual((row['canonical_largo_id'], row['version'], row['decision']),
                             ('largo.alpha.beta', '1.0.0', 'NEW'))
            self.assertEqual(row['targets']['SR1']['final'], 'EXPERIMENTAL')
            self.assertEqual(row['targets']['SR2']['final'], 'PENDING_ADAPTER')
            journal = DeployJournal(Path(tmp) / 'deploy.sqlite3')
            try:
                plan = L.enable_plan(journal, row, 'SR1')
                self.assertTrue(plan['executable'], plan['blockers'])
                self.assertEqual(plan['operations'][0]['to_state'], 'ENABLED')
                self.assertEqual(L.enable_plan(journal, row, 'SR2')['operations'][0]['to_state'], 'DISABLED')
            finally:
                journal.close()
            self.assertEqual(snapshot(library), before)


class RecoveryTests(unittest.TestCase):
    def test_secret_key_containment_redacts_nested_values(self):
        keys = ('access_token_hint', 'credentials_info', 'AuthorizationHeader', 'password_hash',
                'client_secret_value', 'cookie_header', 'api_key_id')
        for key in keys:
            with self.subTest(key=key):
                field = 'prefix_' + key + '_suffix'
                original = {'safe': [{field: 'private-' + key}], 'ordinary': 'kept'}
                self.assertEqual(R.redact(original), {'safe': [{field: '<redacted>'}], 'ordinary': 'kept'})
                self.assertEqual(original, {'safe': [{field: 'private-' + key}], 'ordinary': 'kept'})

    def test_generic_absolute_paths_are_redacted_without_hiding_urls(self):
        paths = ('/etc/bridge.conf', '/data/private/file', '/usr/local/bridge', '/private/path', '/file',
                 r'\\server\share\private.log', '//server/share/private.log', '~/private/file',
                 '~other/private/file', r'~\private\file', r'C:\Users\private\file')
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(R.redact('failed at ' + path), 'failed at <path>')
        for ordinary in ('relative/file', 'Lab/GeneratedContent', 'https://example.com/path',
                         'http://localhost:7777/v1/health', 'sr1->sr2', 7, None):
            with self.subTest(ordinary=ordinary):
                self.assertEqual(R.redact(ordinary), ordinary)

    def test_bearer_and_jwt_strings_are_redacted_in_exported_fields(self):
        jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.synthetic_signature'
        for text in ('Bearer synthetic-token_1/+==', 'bEaReR\tsynthetic-token_2', jwt):
            with self.subTest(text=text):
                self.assertEqual(R.redact('before ' + text + ' after'), 'before <redacted> after')
        diag = D.make('PROBE_FAILED', 'warning', 'core', 'Bearer synthetic-message',
                      subject=jwt, details={'notes': ['bearer synthetic-detail', '/data/private/file']})
        with tempfile.TemporaryDirectory() as tmp:
            path = R.export_bundle(R.issues([diag]), tmp, name='sensitive-fields',
                                   context={'notes': [jwt, 'Bearer synthetic-context', '~/private/file']})
            text = path.read_text()
            for private in (jwt, 'synthetic-message', 'synthetic-detail', 'synthetic-context', '/data/private', '~/private'):
                self.assertNotIn(private, text)
        self.assertEqual(R.redact('ranch.example.com'), 'ranch.example.com')

    def test_issues_are_friendly_safe_and_bundles_redacted(self):
        diags = [D.make('COUNTERPART_RECOVERED_UNCERTAIN', 'warning', 'counterpart', 'uncertain', subject='c'),
                 D.make('LOADER_NOT_READY', 'warning', 'launcher', 'SRML missing', realm='SR1', action='INSTALL_LOADER'),
                 D.make('TARGET_DRIFT', 'error', 'deploy', 'file changed at /home/user/secret/file.bin'),
                 D.make('MOD_UNKNOWN', 'info', 'modcompat', 'unknown mod')]
        items = R.issues(diags + [{'not': 'a diagnostic'}])
        self.assertEqual([i['category'] for i in items], ['PACKAGES', 'COUNTERPART', 'LOADERS', 'MODS'])
        self.assertTrue(all(not a['destructive'] for i in items for a in i['actions']))
        self.assertEqual(items[2]['actions'][0]['id'], 'INSTALL_LOADER')
        self.assertEqual(R.summary(items)['status'], 'error')
        with tempfile.TemporaryDirectory() as tmp:
            path = R.export_bundle(items, tmp, name='bundle-1', context={'auth_token': 'abc', 'root': 'C:\\Games\\SR'})
            text = path.read_text()
            self.assertNotIn('/home/user', text)
            self.assertNotIn('abc', text)
            self.assertNotIn('C:\\\\Games', text)
            with self.assertRaises(ValueError):
                R.export_bundle(items, tmp, name='../escape')


class Verifier(U.SignatureVerifier):
    def verify(self, signed_bytes, signature):
        return signature['value'] == hashlib.sha256(signed_bytes).hexdigest()


def manifest(**over):
    data = b'launcher-binary-bytes'
    doc = {'schema': 'bridge-update-manifest', 'schema_version': 1, 'channel': 'stable', 'sequence': 5,
           'components': {'launcher': {'version': '1.2.0', 'min_from': None, 'restart': 'LAUNCHER', 'requires': {},
                                       'migrations': ['settings-v3'],
                                       'artifacts': [{'name': 'launcher.bin', 'sha256': hashlib.sha256(data).hexdigest(),
                                                      'size': len(data)}]}},
           'signature': {'scheme': 'test', 'key_id': 'k', 'value': ''}}
    doc.update(over)
    doc['signature'] = {'scheme': 'test', 'key_id': 'k', 'value': hashlib.sha256(U.signed_payload(doc)).hexdigest()}
    return doc, data


class UpdateTests(unittest.TestCase):
    def test_signed_monotonic_plans_and_verified_staging(self):
        doc, data = manifest()
        plan = U.plan_updates({'launcher': '1.1.0'}, doc, verifier=Verifier(), last_sequence=4)
        self.assertTrue(plan['executable'], plan['blockers'])
        self.assertEqual(plan['steps'][0]['actions'], ['DOWNLOAD', 'VERIFY_HASH', 'STAGE', 'MIGRATE', 'SWAP_ON_RESTART',
                                                       'CONFIRM_OR_ROLLBACK'])
        self.assertEqual(plan['restart'], 'LAUNCHER')
        codes = lambda p: {d['code'] for d in p['blockers']}
        self.assertIn('UPDATE_SIGNATURE_UNVERIFIED', codes(U.plan_updates({}, doc, verifier=U.RejectAll())))
        self.assertIn('UPDATE_REPLAYED_MANIFEST', codes(U.plan_updates({}, doc, verifier=Verifier(), last_sequence=5)))
        self.assertIn('UPDATE_DOWNGRADE', codes(U.plan_updates({'launcher': '2.0.0'}, doc, verifier=Verifier())))
        self.assertIn('UPDATE_WRONG_CHANNEL', codes(U.plan_updates({}, doc, verifier=Verifier(), channel='beta')))
        tampered = dict(doc, sequence=99)
        self.assertIn('UPDATE_SIGNATURE_UNVERIFIED', codes(U.plan_updates({}, tampered, verifier=Verifier())))
        bad, _ = manifest(components={'launcher': dict(doc['components']['launcher'],
                                                       artifacts=[{'name': '../evil', 'sha256': 'a' * 64, 'size': 1}])})
        self.assertIn('UPDATE_MANIFEST_INVALID', codes(U.plan_updates({}, bad, verifier=Verifier())))
        needs, _ = manifest(components={'launcher': dict(doc['components']['launcher'], requires={'bridgehost': '2.0.0'})})
        self.assertIn('UPDATE_REQUIREMENT_UNMET', codes(U.plan_updates({}, needs, verifier=Verifier())))

        class Downloads(U.DownloadProvider):
            def __init__(self, payload):
                self.payload = payload

            def fetch(self, component, artifact):
                return self.payload
        with tempfile.TemporaryDirectory() as tmp:
            good = U.stage(plan, Downloads(data), tmp)
            self.assertEqual((good['complete'], good['staged']), (True, ['launcher/1.2.0/launcher.bin']))
            evil = U.stage(plan, Downloads(b'tampered-bytes!!!!!!!'), Path(tmp) / 'other')
            self.assertEqual(evil['failures'][0]['reason'], 'HASH_OR_SIZE')
            with self.assertRaises(ValueError):
                U.stage(U.plan_updates({}, doc, verifier=U.RejectAll()), Downloads(data), tmp)


if __name__ == '__main__':
    unittest.main()
