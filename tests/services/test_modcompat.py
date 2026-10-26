"""Mod compatibility: untrusted manifests/evidence, classification, set analysis, deploy units, skeletons."""

import copy
import hashlib
import json
import random
import re
import tempfile
import unittest
from pathlib import Path

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D
from bridge.deploy.units import validate_unit
from bridge.modcompat import analyzer as A
from bridge.modcompat import evidence as E
from bridge.modcompat import manifest as M
from bridge.modcompat import skeleton as K
from portability import directory_link

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / 'fixtures' / 'cloud' / 'mods'
CASES = {data['case']: data for data in (json.loads(p.read_text(encoding='utf-8'))
                                         for p in sorted(CORPUS.glob('case-*.json')))}
SET_SR1 = json.loads((CORPUS / 'set-sr1.json').read_text(encoding='utf-8'))
DENY = ('SceneContext', 'GameContext', 'PlayerState', 'LookupDirector', 'Identifiable', 'SlimeDefinition',
        'AutoSaveDirector', 'SRSingleton', 'MonomiPark.', 'Il2Cpp', 'Assembly-CSharp', 'HarmonyLib', 'UnityEngine',
        'SRML.SR', 'Starlight', 'SR2MP', 'ApiHandlers', 'FixtureGame', 'Process.Start', 'DllImport', 'extern ')
CS_ALLOWED = [re.compile(p) for p in (
    r'', r'\s*//.*', r'\s*[{}]', r'using (SRML|MelonLoader);', r'namespace SlimeBridge\.Adapters\.\w+',
    r'\s*public sealed class \w+Adapter : (ModEntryPoint|MelonMod)',
    r'\s*public override void (PreLoad|Load|PostLoad|OnInitializeMelon|OnUpdate|OnDeinitializeMelon)\(\)',
    r'\[assembly: MelonInfo\(typeof\(SlimeBridge\.Adapters\.\w+\.\w+Adapter\), "[A-Za-z0-9 _-]+ \(SlimeBridge '
    r'adapter\)", "0\.0\.0", "SlimeBridge adapter skeleton"\)\]',
    r'\[assembly: MelonGame\("MonomiPark", "SlimeRancher2"\)\]')]


def codes(items):
    return {d['code'] for d in items}


def parse_case(case):
    data = CASES[case]['input']
    raw = data['raw_text'] if data['raw_text'] is not None else data['document']
    return M.parse_manifest(data['format'], raw, source=data['source'], files=data['files'])


def parsed(case):
    manifest, diags = parse_case(case)
    assert manifest is not None, (case, codes(diags))
    return manifest, CASES[case]['evidence']


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def blank_evidence(mod_id, **sections):
    doc = {'schema': E.EVIDENCE_SCHEMA, 'schema_version': 1, 'mod_id': mod_id, 'manifest_id': None,
           'extractor': {'id': 'fixture.static-extractor', 'version': '1.0.0'}, 'synthetic': True,
           'code': {'assemblies': []}, 'references': [], 'harmony': {'patch_all_calls': 0, 'patches': []},
           'native': {'pinvoke_modules': [], 'detour_indicators': [], 'function_pointer_hooks': False},
           'content': {'asset_bundles': [], 'content_files': [], 'content_ids': []}, 'config_files': [],
           'reflection': {'type_lookup_by_name': False, 'non_public_access': False, 'dynamic_invoke': False,
                          'emit': False},
           'il2cpp': {'interop_references': False, 'type_injection': False, 'il2cpp_type_references': 0}}
    doc.update(sections)
    return doc


def simple_manifest(mod_id, *, version=(1, 0, 0), deps=(), files=None, realm='SR1', loader='SRML', **extra):
    files = files if files is not None else [{'path': f'SRML/Mods/{mod_id}/modinfo.json', 'sha256': h(mod_id),
                                              'size': 10}]
    return M.make_manifest(id=mod_id, native_id=mod_id, name=mod_id, version_text='.'.join(map(str, version)),
                           version_parts=list(version), realm=realm, loader=loader,
                           source=M.local_source(files[0]['path'] if files else 'x/modinfo.json', synthetic=True),
                           dependencies=[{'id': d, 'kind': 'MOD', 'constraint': '*', 'optional': False} for d in deps],
                           files=files, **extra)


class CorpusTests(unittest.TestCase):
    def test_corpus_covers_the_required_shapes_with_fictional_names(self):
        self.assertGreaterEqual(len(CASES), 10)
        expected = {c['expect']['classification'] for c in CASES.values() if c['expect']['parse'] == 'OK'}
        self.assertEqual(expected, set(A.CLASSIFICATIONS))
        formats = {c['input']['format'] for c in CASES.values()}
        self.assertEqual(formats, set(M.FORMATS))
        self.assertTrue(any(c['expect']['parse'] == 'REJECTED' for c in CASES.values()))
        realms = set()
        for case, data in CASES.items():
            text = json.dumps(data)
            for mod_id in re.findall(r'"(?:id|mod_id|declared_id)": "([^"]+)"', text):
                self.assertTrue(mod_id.lower().startswith('fixture.'), (case, mod_id))
            if data['expect']['parse'] == 'OK':
                realms.add(parsed(case)[0]['realm'])
        self.assertEqual(realms, {'SR1', 'SR2'})
        for path in CORPUS.iterdir():
            self.assertIn(path.suffix, ('.json', '.md'), path.name)
            self.assertLess(path.stat().st_size, 16 * 1024, path.name)

    def test_every_case_parses_as_expected(self):
        for case, data in CASES.items():
            with self.subTest(case=case):
                manifest, diags = parse_case(case)
                self.assertTrue(set(data['expect']['diagnostic_codes']) <= codes(diags), codes(diags))
                if data['expect']['parse'] == 'REJECTED':
                    self.assertIsNone(manifest)
                    self.assertTrue(D.has_errors(diags))
                else:
                    self.assertIsNotNone(manifest, codes(diags))
                    self.assertEqual(M.validate_manifest(manifest), [])
                    for item in diags:
                        self.assertEqual(D.validate(item), [])

    def test_every_case_classifies_ports_and_deploys_as_expected(self):
        for case, data in CASES.items():
            if data['expect']['parse'] != 'OK':
                continue
            manifest, evidence = parsed(case)
            for target in ('SR1', 'SR2'):
                with self.subTest(case=case, target=target):
                    report = A.analyze(manifest, evidence, target_realm=target)
                    self.assertEqual(A.validate_report(report), [])
                    self.assertEqual((report['classification'], report['classification_rule']),
                                     (data['expect']['classification'], data['expect']['rule']))
                    self.assertEqual(report['portability'], data['expect']['portability'][target])
                    unit, problems = A.to_deploy_unit(manifest, report)
                    self.assertEqual(problems, [])
                    self.assertEqual(validate_unit(unit), [])
                    self.assertEqual(unit['status'], data['expect']['unit_status'][target])
                    self.assertEqual(unit['key'], f"mod:{M.deploy_key_id(manifest['id'])}:{target}")
                    self.assertEqual(report['data_evidence'], 'SYNTHETIC')
                    self.assertIn(report['evidence'], ('NEEDS_LOCAL_RUNTIME_PROOF', 'BLOCKED_ON_LOCAL_QUERY'))
                    self.assertEqual(report['evidence'] == 'BLOCKED_ON_LOCAL_QUERY', bool(report['local_queries']))
                    total = round(sum(r['delta'] for r in report['confidence_reasons']), 6)
                    self.assertAlmostEqual(max(total, 0.0), report['confidence'])
                    self.assertLessEqual(report['confidence'], A.CONFIDENCE_CAP / 100)
                    if report['classification'] == 'UNKNOWN':
                        self.assertLessEqual(report['confidence'], A.UNKNOWN_CONFIDENCE_CAP / 100)
                        self.assertIn('CLASSIFICATION_UNKNOWN', codes(report['blockers']))

    def test_reports_are_deterministic(self):
        for case in ('harmony_heavy', 'melon_lantern', 'glimmer_pack'):
            manifest, evidence = parsed(case)
            again = json.loads(json.dumps(manifest))
            reordered = dict(reversed(list(again.items())))
            first = A.analyze(manifest, evidence, target_realm='SR2')
            self.assertEqual(first, A.analyze(reordered, copy.deepcopy(evidence), target_realm='SR2'))
            self.assertEqual(first['report_id'], sha256_json({k: v for k, v in first.items() if k != 'report_id'}))


class ManifestTests(unittest.TestCase):
    SOURCE = M.local_source('SRML/Mods/Fx/modinfo.json', synthetic=True)

    def srml(self, doc, files=()):
        return M.parse_srml_modinfo(doc, source=self.SOURCE, files=list(files))

    def assert_rejected(self, result, code=None):
        manifest, diags = result
        self.assertIsNone(manifest)
        self.assertTrue(D.has_errors(diags))
        for item in diags:
            self.assertEqual(D.validate(item), [])
        if code is not None:
            self.assertIn(code, codes(diags))

    def test_malformed_json_becomes_diagnostics(self):
        cases = {
            b'\xff\xfe not utf-8': 'MOD_JSON_ENCODING_INVALID',
            '{"id": "a", "id": "b"}': 'MOD_JSON_DUPLICATE_KEY',
            '{"id": NaN}': 'MOD_JSON_MALFORMED',
            '{"version": 1e999}': 'MOD_JSON_MALFORMED',
            '{"id": "\\ud800"}': 'MOD_JSON_ENCODING_INVALID',
            '[' * 50000 + ']' * 50000: 'MOD_JSON_TOO_DEEP',
            '{"a":' * 40 + '1' + '}' * 40: 'MOD_JSON_TOO_DEEP',
            '["not", "an", "object"]': 'MOD_JSON_NOT_OBJECT',
            '{"id": "fixture.x", ': 'MOD_JSON_MALFORMED',
            '': 'MOD_JSON_MALFORMED',
        }
        for raw, code in cases.items():
            with self.subTest(raw=raw[:30]):
                self.assert_rejected(self.srml(raw), code)
        self.assert_rejected(self.srml({'id': float('nan')}), 'MOD_JSON_VALUE_INVALID')
        self.assert_rejected(self.srml({1: 'x'}), 'MOD_JSON_VALUE_INVALID')

    def test_oversized_documents_are_refused_before_parsing(self):
        doc = {'id': 'fixture.big', 'name': 'Big', 'version': '1.0', 'description': 'x' * (M.MAX_DOCUMENT_BYTES + 1)}
        self.assert_rejected(self.srml(doc), 'MOD_DOCUMENT_TOO_LARGE')
        self.assert_rejected(self.srml(json.dumps(doc).encode()), 'MOD_DOCUMENT_TOO_LARGE')
        deps = [f'fixture.dep{i}' for i in range(M.MAX_DEPENDENCIES + 1)]
        self.assert_rejected(self.srml({'id': 'fixture.many', 'version': '1.0', 'dependencies': deps}),
                             'MODINFO_FIELD_INVALID')
        evidence = blank_evidence('fixture.big', references=[{'assembly': 'A', 'types': ['T' * 200] * 1,
                                                              'members': []}] * 1)
        evidence['padding'] = 'x' * (E.MAX_EVIDENCE_BYTES + 1)
        self.assertIn('MOD_DOCUMENT_TOO_LARGE', codes(E.load_evidence(json.dumps(evidence))[1]))

    def test_srml_rules(self):
        ok = {'id': 'fixture.ok', 'name': 'Ok', 'version': '1.2'}
        manifest, diags = self.srml(ok)
        self.assertEqual((manifest['version'], manifest['realm'], manifest['loader']),
                         ({'text': '1.2', 'parts': [1, 2]}, 'SR1', 'SRML'))
        for bad in ('1', '1.2.3.4', '0.1.0-dev', 'v1.0', '-1.0'):
            self.assert_rejected(self.srml(dict(ok, version=bad)), 'MODINFO_VERSION_INVALID')
        self.assert_rejected(self.srml(dict(ok, version=3)), 'MODINFO_FIELD_INVALID')
        self.assert_rejected(self.srml({'name': 'x', 'version': '1.0'}), 'MODINFO_FIELD_MISSING')
        self.assert_rejected(self.srml(dict(ok, id='has space')), 'MOD_ID_INVALID')
        manifest, diags = self.srml(dict(ok, id='Fixture.Upper', extra_field=1))
        self.assertEqual(manifest['id'], 'fixture.upper')
        self.assertEqual(manifest['native_id'], 'Fixture.Upper')
        self.assertTrue({'MOD_ID_NORMALIZED', 'MODINFO_UNKNOWN_FIELD'} <= codes(diags))
        manifest, diags = self.srml(dict(ok, dependencies=['fixture.a', 'fixture.b 1.2', 'Fixture.C@2.0.1'],
                                         load_after=['fixture.a']))
        self.assertEqual([(d['id'], d['constraint']) for d in manifest['dependencies']],
                         [('fixture.a', '*'), ('fixture.b', '>=1.2'), ('fixture.c', '>=2.0.1')])
        self.assertTrue({'SRML_DEPENDENCY_SEMANTICS_UNVERIFIED', 'SRML_LOAD_ORDER_SEMANTICS_UNVERIFIED'}
                        <= codes(diags))
        for dep in ('fixture.a@', 'fixture.a 1.x', 7, '../x', 'fixture.a 1 2'):
            self.assert_rejected(self.srml(dict(ok, dependencies=[dep])), 'MODINFO_DEPENDENCY_INVALID')
        self.assert_rejected(self.srml(dict(ok, dependencies=['fixture.ok'])), 'MANIFEST_SELF_DEPENDENCY')

    def test_paths_and_source_are_confined(self):
        ok = {'id': 'fixture.ok', 'name': 'Ok', 'version': '1.0'}
        for path in ('../escape.dll', '/abs/x.dll', 'C:/x.dll', 'a\\b.dll', 'a//b.dll', './a.dll', ''):
            with self.subTest(path=path):
                self.assert_rejected(self.srml(ok, [{'path': path, 'sha256': h(path), 'size': 1}]))
        clash = [{'path': 'Mods/A.dll', 'sha256': h('1'), 'size': 1},
                 {'path': 'mods/a.DLL', 'sha256': h('2'), 'size': 1}]
        self.assert_rejected(self.srml(ok, clash), 'MANIFEST_FILE_DUPLICATE')
        self.assert_rejected(M.parse_srml_modinfo(ok, source=M.local_source('../x'), files=[]),
                             'MANIFEST_SOURCE_INVALID')
        self.assert_rejected(M.parse_srml_modinfo(ok, source=None, files=[]), 'MANIFEST_SCHEMA_INVALID')
        self.assert_rejected(self.srml(ok, [{'path': 'a.dll', 'sha256': 'ABC', 'size': 1}]), 'MANIFEST_SCHEMA_INVALID')

    def test_melon_metadata_rules(self):
        doc = CASES['melon_lantern']['input']['document']
        manifest, _ = M.parse_melon_metadata(doc)
        self.assertEqual((manifest['realm'], manifest['loader'], manifest['provides_assemblies']),
                         ('SR2', 'MELONLOADER', ['FixtureMelonLantern']))
        self.assertEqual(manifest['source']['kind'], 'PROVIDER')
        universal = dict(doc, melon_games=[])
        self.assertIn('MELON_GAME_UNDECLARED', codes(M.parse_melon_metadata(universal, realm_hint='SR2')[1]))
        self.assert_rejected(M.parse_melon_metadata(universal, realm_hint='SR1'), 'MANIFEST_LOADER_REALM_MISMATCH')
        self.assert_rejected(M.parse_melon_metadata(dict(doc, melon_games=[{'developer': 'Fixture Studio',
                                                                            'name': 'Fixture Game'}])),
                             'MELON_GAME_NOT_SUPPORTED')
        self.assert_rejected(M.parse_melon_metadata(dict(doc, platform_domain='WASM')), 'MELON_METADATA_SCHEMA_INVALID')
        self.assert_rejected(M.parse_melon_metadata(dict(doc, surprise=True)), 'MELON_METADATA_SCHEMA_INVALID')
        derived, diags = M.parse_melon_metadata(dict(doc, declared_id=None))
        self.assertEqual(derived['id'], 'fixture_melon_lantern')
        self.assertIn('MOD_ID_DERIVED', codes(diags))
        odd, diags = M.parse_melon_metadata(dict(doc, melon_info=dict(doc['melon_info'], version='beta')))
        self.assertEqual((odd['version']['parts'], odd['version']['text']), ([], 'unknown'))
        self.assertIn('MELON_VERSION_UNPARSEABLE', codes(diags))
        self.assert_rejected(M.parse_manifest('BEPINEX_CFG', '{}'), 'MOD_FORMAT_UNKNOWN')

    def test_bridge_manifest_integrity(self):
        manifest = simple_manifest('fixture.plain')
        self.assertEqual(M.load_manifest(manifest), (manifest, []))
        self.assert_rejected(M.load_manifest(dict(manifest, name='Tampered')), 'MANIFEST_ID_MISMATCH')
        self.assert_rejected(M.load_manifest(dict(manifest, realm='SR3')), 'MANIFEST_SCHEMA_INVALID')

    def test_constraints(self):
        self.assertEqual(M.parse_constraint(' >= 1.02 , <2 '), '>=1.2,<2')
        self.assertEqual(M.parse_constraint('1.0', bare='>='), '>=1.0')
        self.assertIsNone(M.parse_constraint('>=1.x'))
        cases = [([1, 2], '>=1.2,<2', True), ([2, 0], '>=1.2,<2', False), ([1, 4, 0], '^1.2', True),
                 ([2, 0, 0], '^1.2', False), ([1, 2, 9], '~1.2', True), ([1, 3], '~1.2', False),
                 ([1, 0], '=1.0.0', True), ([], '>=1.0', None), ([1], '*', True), ([1], '>=1.x', None)]
        for parts, constraint, expected in cases:
            self.assertEqual(M.satisfies(parts, constraint), expected, (parts, constraint))

    def test_hostile_mutations_never_raise(self):
        rng = random.Random(1729)
        weird = [None, True, -1, 2 ** 70, 1.5, '', 'x' * 5000, '\x00', '../..', [], [None], {}, {'a': [1]},
                 'Fixture.Ünïcode', '1.0', 'SR2', 'MELONLOADER']
        seeds = [('SRML_MODINFO', CASES['cozy_config']['input']['document']),
                 ('MELON_METADATA', CASES['melon_lantern']['input']['document']),
                 ('BRIDGE_MANIFEST', CASES['provider_pack']['input']['document'])]
        evidence_seed = CASES['harmony_heavy']['evidence']
        for _ in range(400):
            fmt, seed = rng.choice(seeds)
            doc = copy.deepcopy(seed)
            target = doc
            while isinstance(target, dict) and target and rng.random() < 0.5:
                key = rng.choice(sorted(target))
                if not isinstance(target[key], dict):
                    break
                target = target[key]
            if isinstance(target, dict) and target:
                target[rng.choice(sorted(target))] = rng.choice(weird)
            manifest, diags = M.parse_manifest(fmt, doc, source=self.SOURCE, files=[], realm_hint='SR2')
            self.assertTrue(manifest is None or M.validate_manifest(manifest) == [])
            evidence = copy.deepcopy(evidence_seed)
            section = rng.choice(sorted(evidence))
            evidence[section] = rng.choice(weird)
            self.assertIsInstance(E.validate_evidence(evidence), list)
            if manifest is not None:
                report = A.analyze(manifest, evidence, target_realm=rng.choice(('SR1', 'SR2')))
                self.assertEqual(A.validate_report(report), [])
        for garbage in (None, 'text', 5, [], {'schema': 'bridge-mod-manifest'}):
            report = A.analyze(garbage)
            self.assertEqual((report['classification'], report['confidence']), ('UNKNOWN', 0.0))
            self.assertIn('MANIFEST_INVALID', codes(report['blockers']))
            self.assertEqual(A.validate_report(report), [])


class EvidenceTests(unittest.TestCase):
    def test_strict_validation(self):
        good = CASES['harmony_heavy']['evidence']
        self.assertEqual(E.validate_evidence(good), [])
        mutations = {
            'EVIDENCE_SCHEMA_INVALID': lambda e: e.update(extra=1),
            'EVIDENCE_MEMBER_TYPE_UNLISTED': lambda e: e['references'][1]['members'].append('Other.Type::Thing'),
            'EVIDENCE_HARMONY_PATCH_EMPTY': lambda e: e['harmony']['patches'].append(
                {'target': 'X::Y', 'prefix': 0, 'postfix': 0, 'transpiler': 0, 'finalizer': 0}),
            'EVIDENCE_PATH_UNSAFE': lambda e: e['config_files'].append(
                {'path': '../x.ini', 'sha256': h('x'), 'size': 1}),
            'EVIDENCE_FILE_DUPLICATE': lambda e: e['config_files'].append(
                {'path': e['code']['assemblies'][0]['path'].upper(), 'sha256': h('copy'), 'size': 1}),
            'EVIDENCE_REFERENCE_DUPLICATE': lambda e: e['references'].append(dict(e['references'][0])),
        }
        for code, mutate in mutations.items():
            with self.subTest(code=code):
                evidence = copy.deepcopy(good)
                mutate(evidence)
                self.assertIn(code, codes(E.validate_evidence(evidence)))
                self.assertIsNone(E.load_evidence(evidence)[0])
        bare = blank_evidence('fixture.x', harmony={'patch_all_calls': 1, 'patches': []})
        self.assertIn('EVIDENCE_CODE_INDICATORS_WITHOUT_CODE', codes(E.validate_evidence(bare)))
        self.assertIn('EVIDENCE_SCHEMA_INVALID', codes(E.validate_evidence('nope')))

    def test_no_evidence_is_honest(self):
        manifest, evidence = parsed('ranch_helper')
        for given in (None, {'schema': 'junk'}):
            report = A.analyze(manifest, given)
            self.assertEqual((report['classification'], report['portability']), ('UNKNOWN', 'UNKNOWN'))
            self.assertLessEqual(report['confidence'], 0.2)
            self.assertIn('Q050', report['local_queries'])
            self.assertEqual(report['evidence'], 'BLOCKED_ON_LOCAL_QUERY')
            self.assertEqual(A.unit_status(report), 'PENDING_ADAPTER')
        self.assertEqual(A.analyze(manifest)['evidence_state'], 'ABSENT')
        self.assertEqual(A.analyze(manifest, {'schema': 'junk'})['evidence_state'], 'INVALID')
        with_evidence = A.analyze(manifest, evidence)
        self.assertGreater(with_evidence['confidence'], A.analyze(manifest)['confidence'])

    def test_unbound_evidence_is_not_used(self):
        manifest, evidence = parsed('ranch_helper')
        stale = copy.deepcopy(evidence)
        stale['code']['assemblies'][0]['sha256'] = h('rebuilt')
        other = copy.deepcopy(evidence)
        other['mod_id'] = 'fixture.someone_else'
        pinned = dict(copy.deepcopy(evidence), manifest_id=h('another manifest'))
        incomplete = copy.deepcopy(evidence)
        incomplete['code']['assemblies'] = []
        incomplete['references'] = []
        expected = {'EVIDENCE_STALE': stale, 'EVIDENCE_MOD_MISMATCH': other, 'EVIDENCE_MANIFEST_MISMATCH': pinned,
                    'EVIDENCE_INCOMPLETE': incomplete}
        for code, doc in expected.items():
            with self.subTest(code=code):
                report = A.analyze(manifest, doc)
                self.assertEqual((report['evidence_state'], report['classification']), ('UNBOUND', 'UNKNOWN'))
                self.assertIn(code, codes(report['diagnostics']))
        bound = dict(copy.deepcopy(evidence), manifest_id=manifest['manifest_id'])
        self.assertEqual(A.analyze(manifest, bound)['evidence_state'], 'BOUND')


class AnalyzerTests(unittest.TestCase):
    def test_rule_details(self):
        manifest, evidence = parsed('harmony_heavy')
        report = A.analyze(manifest, evidence, target_realm='SR2')
        self.assertEqual(report['harmony']['patch_count'], 6)
        self.assertEqual((report['harmony']['transpiler'], report['harmony']['patch_all_calls']), (1, 1))
        self.assertTrue({'HARMONY_TRANSPILERS', 'REFLECTION_NON_PUBLIC_ACCESS', 'HARMONY_TARGETS_UNVERIFIED'}
                        <= set(report['manual_review']))
        self.assertTrue({'Q053', 'Q055'} <= set(report['local_queries']))
        self.assertIn('harmony.patch_retarget', {r['source_capability'] for r in report['required_capabilities']})
        hook = A.analyze(*parsed('deep_hook'), target_realm='SR1')
        self.assertIn('CROSS_REALM_NOT_PORTABLE', codes(hook['blockers']))
        self.assertIn('Q057', hook['local_queries'])
        native_home = A.analyze(*parsed('deep_hook'))
        self.assertIn('NATIVE_HOOKS_UNREVIEWED', codes(native_home['blockers']))
        lantern = A.analyze(*parsed('melon_lantern'), target_realm='SR1')
        rows = {r['source_capability']: r for r in lantern['required_capabilities']}
        self.assertEqual(rows['mod.lifecycle']['target_api'], 'srml.mod_entry_point')
        self.assertEqual(rows['runtime.retarget']['target_api'], 'net472')
        self.assertEqual({a['api_id'] for a in lantern['apis_used']},
                         {'melon.melon_mod', 'melon.melon_info_attribute', 'melon.melon_game_attribute'})
        tool = A.analyze(*parsed('uncatalogued_tool'))
        self.assertEqual(tool['references']['uncatalogued_types'], 2)
        self.assertIn('Q053', tool['local_queries'])
        manifest, evidence = parsed('ranch_helper')
        foreign = copy.deepcopy(evidence)
        foreign['references'].append({'assembly': 'MelonLoader', 'types': ['MelonLoader.MelonMod'], 'members': []})
        report = A.analyze(manifest, foreign)
        self.assertEqual(report['classification_rule'], 'R4')
        self.assertIn('FOREIGN_LOADER_REFERENCED', report['manual_review'])

    def test_dependency_resolution(self):
        lib = simple_manifest('fixture.lib', version=(1, 4))
        sr2_lib = simple_manifest('fixture.sr2lib', realm='SR2', loader='MELONLOADER', provides_assemblies=['Sr2Lib'])
        user = simple_manifest('fixture.user')
        user = M.make_manifest(**{**{k: user[k] for k in ('id', 'native_id', 'name', 'realm', 'loader', 'source',
                                                          'files')},
                                  'version_text': '1.0', 'version_parts': [1, 0], 'dependencies': [
                                      {'id': 'fixture.lib', 'kind': 'MOD', 'constraint': '>=1.2,<2', 'optional': False},
                                      {'id': 'fixture.gone', 'kind': 'MOD', 'constraint': '*', 'optional': True},
                                      {'id': 'fixture.sr2lib', 'kind': 'MOD', 'constraint': '*', 'optional': False},
                                      {'id': 'SRML', 'kind': 'ASSEMBLY', 'constraint': '*', 'optional': False},
                                      {'id': 'Sr2Lib', 'kind': 'ASSEMBLY', 'constraint': '*', 'optional': False}]})
        unchecked = A.analyze(user)
        self.assertEqual({r['status'] for r in unchecked['dependencies']}, {'UNCHECKED', 'PLATFORM'})
        report = A.analyze(user, available=[lib, sr2_lib, {'garbage': True}])
        status = {r['id']: r['status'] for r in report['dependencies']}
        self.assertEqual(status, {'fixture.lib': 'RESOLVED', 'fixture.gone': 'OPTIONAL_ABSENT',
                                  'fixture.sr2lib': 'REALM_MISMATCH', 'SRML': 'PLATFORM', 'Sr2Lib': 'REALM_MISMATCH'})
        self.assertIn('DEPENDENCY_REALM_MISMATCH', codes(report['blockers']))
        old = A.analyze(user, available=[simple_manifest('fixture.lib', version=(2, 0))])
        self.assertIn('DEPENDENCY_VERSION_CONFLICT', codes(old['blockers']))
        self.assertIn('DEPENDENCY_PROBLEM', {r['code'] for r in old['confidence_reasons']})

    def test_program_inputs_are_checked(self):
        manifest, _ = parsed('cozy_config')
        with self.assertRaises(ValueError):
            A.analyze(manifest, target_realm='SR3')
        with self.assertRaises(ValueError):
            A.analyze_set([manifest], None, 'SR9')
        catalog = copy.deepcopy(A.default_known_apis())
        catalog['capabilities']['mod.lifecycle']['SR2'] = 'srml.mod_entry_point'
        with self.assertRaises(ValueError):
            A.analyze(manifest, known_apis=catalog)
        extended = copy.deepcopy(A.default_known_apis())
        extended['apis'].append({'id': 'srml.fixture_registry', 'realm': 'SR1', 'kind': 'TYPE', 'assembly': 'SRML',
                                 'type': 'SRML.FixtureRegistry', 'members': None, 'capability': 'mod.lifecycle',
                                 'source': 'test', 'evidence': 'NEEDS_LOCAL_RUNTIME_PROOF'})
        tool_manifest, tool_evidence = parsed('uncatalogued_tool')
        before = A.analyze(tool_manifest, tool_evidence)['references']['uncatalogued_types']
        after = A.analyze(tool_manifest, tool_evidence, known_apis=extended)['references']['uncatalogued_types']
        self.assertEqual((before, after), (2, 1))

    def test_known_api_catalogue_is_small_labelled_and_sourced(self):
        catalog, problems = A.load_known_apis()
        self.assertEqual(problems, [])
        self.assertEqual(catalog['evidence'], 'NEEDS_LOCAL_RUNTIME_PROOF')
        self.assertLessEqual(len(catalog['apis']), 16)
        for api in catalog['apis']:
            self.assertEqual(api['evidence'], 'NEEDS_LOCAL_RUNTIME_PROOF')
            self.assertNotEqual(api['assembly'], 'Assembly-CSharp')
            cited = re.findall(r'((?:docs|mods)/[\w./-]+\.\w+)', api['source'])
            self.assertTrue(cited, api['id'])
            for path in cited:
                self.assertTrue((REPO / path).is_file(), path)
        for realm in catalog['realms'].values():
            for path in realm['sources']:
                self.assertTrue((REPO / path).is_file(), path)


class SetTests(unittest.TestCase):
    def corpus(self):
        manifests, evidences = [], []
        for case, data in CASES.items():
            if data['expect']['parse'] == 'OK':
                manifest, evidence = parsed(case)
                manifests.append(manifest)
                if evidence is not None:
                    evidences.append(evidence)
        return manifests, evidences

    def test_corpus_set_findings(self):
        manifests, evidences = self.corpus()
        result = A.analyze_set(manifests, evidences, SET_SR1['target_realm'])
        expect = SET_SR1['expect']
        self.assertEqual(result['cycles'], expect['cycles'])
        self.assertEqual(result['missing'], expect['missing'])
        self.assertEqual([d['id'] for d in result['duplicates']], expect['duplicates'])
        self.assertEqual(result['version_conflicts'], expect['version_conflicts'])
        self.assertEqual([c['content_id'] for c in result['conflicts']['content_ids']], expect['content_conflicts'])
        self.assertEqual([c['path'] for c in result['conflicts']['files']], expect['file_conflicts'])
        self.assertTrue(set(expect['diagnostic_codes']) <= codes(result['diagnostics']))
        for first, second in expect['load_before']:
            self.assertLess(result['load_order'].index(first), result['load_order'].index(second))
        for member in expect['cycles'][0]:
            self.assertIn(member, result['unordered'])
        self.assertEqual(len(result['reports']), len(manifests))
        self.assertIn('SET_EVIDENCE_DUPLICATE', codes(result['diagnostics']))  # both twins carry fixture.twin evidence
        shuffled_m, shuffled_e = list(reversed(manifests)), list(reversed(evidences))
        self.assertEqual(A.analyze_set(shuffled_m, shuffled_e, 'SR1')['set_id'], result['set_id'])
        self.assertEqual(sum(result['summary']['classification'].values()), len(manifests))

    def test_collisions_hints_and_invalid_members(self):
        a = simple_manifest('fixture.a_b')
        b = simple_manifest('fixture.a-b')
        c = simple_manifest('fixture.c', load_after=['fixture.d'])
        d = simple_manifest('fixture.d', load_after=['fixture.c'])
        e = simple_manifest('fixture.e', load_before=['fixture.a_b'])
        shared = [{'path': 'SRML/Mods/Shared/lib.dll', 'sha256': h('same'), 'size': 3}]
        f = simple_manifest('fixture.f', files=shared)
        g = simple_manifest('fixture.g', files=shared)
        result = A.analyze_set([a, b, c, d, e, f, g, {'not': 'a manifest'}], {}, 'SR1')
        found = codes(result['diagnostics'])
        self.assertTrue({'DEPLOY_KEY_COLLISION', 'LOAD_ORDER_UNRESOLVED', 'SET_MANIFEST_INVALID', 'FILE_CONFLICT'}
                        <= found)
        conflict = result['conflicts']['files'][0]
        self.assertTrue(conflict['identical'])
        self.assertEqual(result['cycles'], [])
        self.assertEqual(result['unordered'], ['fixture.c', 'fixture.d'])
        self.assertLess(result['load_order'].index('fixture.e'), result['load_order'].index('fixture.a_b'))
        self.assertIn({'from': 'fixture.a_b', 'to': 'fixture.e', 'kind': 'LOAD_BEFORE'}, result['graph']['edges'])
        loop = simple_manifest('fixture.loop', deps=['fixture.loop2'])
        loop2 = simple_manifest('fixture.loop2', deps=['fixture.loop3'])
        loop3 = simple_manifest('fixture.loop3', deps=['fixture.loop'])
        self.assertEqual(A.analyze_set([loop, loop2, loop3], None, 'SR2')['cycles'],
                         [['fixture.loop', 'fixture.loop2', 'fixture.loop3']])


class DeployUnitTests(unittest.TestCase):
    def test_unit_contents_and_refusals(self):
        manifest, evidence = parsed('needs_missing')
        report = A.analyze(manifest, evidence)
        unit, _ = A.to_deploy_unit(manifest, report)
        self.assertEqual(unit['requires'], ['mod:fixture.not-present:SR1'])
        self.assertEqual([f['path'] for f in unit['files']], sorted(f['path'] for f in manifest['files']))
        self.assertEqual(unit['source_ref'], report['report_id'])
        cross = A.analyze(manifest, evidence, target_realm='SR2')
        cross_unit, _ = A.to_deploy_unit(manifest, cross)
        self.assertEqual((cross_unit['files'], cross_unit['status']), ([], 'PENDING_ADAPTER'))
        pack, pack_evidence = parsed('glimmer_pack')
        pack_unit, _ = A.to_deploy_unit(pack, A.analyze(pack, pack_evidence))
        self.assertEqual(pack_unit['provides'], ['plort.fixture_glimmer', 'slime.fixture_glimmer'])
        other, _ = parsed('cozy_config')
        self.assertEqual(A.to_deploy_unit(other, report)[0], None)
        self.assertIn('REPORT_MANIFEST_MISMATCH', codes(A.to_deploy_unit(other, report)[1]))
        tampered = dict(report, portability='PORTABLE', cross_realm=False, target_realm='SR2')
        self.assertIn('REPORT_ID_MISMATCH', codes(A.to_deploy_unit(manifest, tampered)[1]))
        blocked = A.analyze(manifest, evidence, available=[])
        self.assertEqual(A.to_deploy_unit(manifest, blocked)[0]['status'], 'PENDING_ADAPTER')


class SkeletonTests(unittest.TestCase):
    def report(self, case, target):
        manifest, evidence = parsed(case)
        return A.analyze(manifest, evidence, target_realm=target)

    def test_skeletons_are_deterministic_and_template_only(self):
        cases = [('harmony_heavy', 'SR2'), ('melon_lantern', 'SR1'), ('deep_hook', 'SR1'), ('mystery_box', 'SR2'),
                 ('glimmer_pack', 'SR2'), ('cozy_config', 'SR2'), ('uncatalogued_tool', 'SR1')]
        for case, target in cases:
            with self.subTest(case=case, target=target):
                report = self.report(case, target)
                files, problems = K.render_adapter_skeleton(report)
                self.assertEqual(problems, [])
                self.assertEqual(files, K.render_adapter_skeleton(copy.deepcopy(report))[0])
                self.assertEqual(sorted(files), sorted(json.loads(files['adapter.json'])['files']))
                source = next(text for name, text in files.items() if name.endswith('.cs'))
                self.assertIn('// TODO(LOCAL_QUERY Q', source)
                for query in report['local_queries']:
                    self.assertIn(f'TODO(LOCAL_QUERY {query})', source)
                    self.assertIn(f'- {query}:', files['LOCAL_QUERIES.md'])
                for word in DENY:
                    self.assertNotIn(word, source, word)
                for line in source.splitlines():
                    self.assertTrue(any(p.fullmatch(line) for p in CS_ALLOWED), line)
                base = 'ModEntryPoint' if target == 'SR1' else 'MelonMod'
                self.assertIn(f'Adapter : {base}', source)
                meta = json.loads(files['adapter.json'])
                self.assertTrue(meta['template_only'])
                self.assertEqual(meta['evidence'], 'BLOCKED_ON_LOCAL_QUERY')

    def test_no_input_text_is_copied_beyond_ids_and_names(self):
        manifest, evidence = parsed('harmony_heavy')
        sentinel = 'Zq7Sentinel'
        evidence = copy.deepcopy(evidence)
        evidence['references'][3]['types'].append(f'{sentinel}.Type')
        evidence['references'][3]['members'].append(f'{sentinel}.Type::{sentinel}Member')
        evidence['harmony']['patches'][0]['target'] = f'{sentinel}.Target::Run'
        evidence['native']['detour_indicators'] = [f'{sentinel}_detour']
        evidence['content']['content_ids'] = [f'slime.{sentinel.lower()}']
        hostile = M.make_manifest(**{**{k: manifest[k] for k in ('id', 'native_id', 'realm', 'loader', 'source',
                                                                 'files', 'dependencies')},
                                     'name': '*/ "evil"; System.Diagnostics.Process.Start("x"); //',
                                     'version_text': '1.0.0', 'version_parts': [1, 0, 0],
                                     'authors': [f'{sentinel} Author']})
        report = A.analyze(hostile, evidence, target_realm='SR2')
        self.assertEqual(report['classification_rule'], 'R1')  # native indicators win over Harmony patches
        self.assertIn(f'{sentinel}.Target::Run', report['harmony']['targets'])  # the report keeps evidence names
        files, _ = K.render_adapter_skeleton(report)
        for name, text in files.items():
            self.assertNotIn(sentinel, text, name)
            self.assertNotIn(sentinel.lower(), text, name)
            self.assertNotIn('Process.Start', text, name)
            self.assertNotIn('*/', text, name)
            self.assertNotIn('"evil"', text, name)

    def test_generation_writes_confined_atomic_identical_files(self):
        report = self.report('harmony_heavy', 'SR2')
        with tempfile.TemporaryDirectory() as tmp:
            first = K.generate_adapter_skeleton(report, out_root=Path(tmp) / 'one')
            second = K.generate_adapter_skeleton(report, out_root=Path(tmp) / 'two', subdir='adapters')
            self.assertEqual((first['status'], second['status']), ('WRITTEN', 'WRITTEN'))
            self.assertEqual(first['files'], second['files'])
            self.assertEqual(first['directory'], 'adapters/fixture.harmony-heavy.sr2')
            for entry in first['files']:
                data = (Path(tmp) / 'one' / entry['path']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
            again = K.generate_adapter_skeleton(report, out_root=Path(tmp) / 'one')
            self.assertEqual(again['files'], first['files'])
            leftovers = [p.name for p in (Path(tmp) / 'one').rglob('*') if p.name.endswith('.tmp')]
            self.assertEqual(leftovers, [])

    def test_escape_attempts_and_symlinks_are_refused(self):
        report = self.report('melon_lantern', 'SR1')
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root, outside = tmp / 'root', tmp / 'outside'
            outside.mkdir()
            for subdir in ('../outside', '/abs', 'a/../b', 'a\\b', '', '.', 'adapters/'):
                with self.subTest(subdir=subdir):
                    result = K.generate_adapter_skeleton(report, out_root=root, subdir=subdir)
                    self.assertEqual(result['status'], 'REFUSED')
                    self.assertIn('SKELETON_PATH_UNSAFE', codes(result['diagnostics']))
            escape = copy.deepcopy(report)
            escape['mod']['id'] = '../../outside'
            escape['report_id'] = sha256_json({k: v for k, v in escape.items() if k != 'report_id'})
            self.assertEqual(K.generate_adapter_skeleton(escape, out_root=root)['status'], 'REFUSED')
            self.assertEqual(K.generate_adapter_skeleton(dict(report, report_id=h('x')), out_root=root)['status'],
                             'REFUSED')
            link = tmp / 'link'
            with directory_link(link, outside):
                result = K.generate_adapter_skeleton(report, out_root=link)
                self.assertEqual(result['status'], 'REFUSED')
                self.assertIn('SKELETON_WRITE_REFUSED', codes(result['diagnostics']))
            root.mkdir()
            with directory_link(root / 'adapters', outside):
                self.assertEqual(K.generate_adapter_skeleton(report, out_root=root)['status'], 'REFUSED')
                self.assertEqual(list(outside.iterdir()), [])
                self.assertFalse(any(p.is_file() for p in root.rglob('*')))

    def test_skip_and_unusable_reports(self):
        same = self.report('cozy_config', 'SR1')
        with tempfile.TemporaryDirectory() as tmp:
            result = K.generate_adapter_skeleton(same, out_root=tmp)
            self.assertEqual((result['status'], result['files']), ('SKIPPED', []))
            self.assertEqual(list(Path(tmp).iterdir()), [])
            invalid = A.analyze({'schema': 'nope'})
            self.assertEqual(K.generate_adapter_skeleton(invalid, out_root=tmp)['status'], 'REFUSED')
            self.assertEqual(K.generate_adapter_skeleton('not a report', out_root=tmp)['status'], 'REFUSED')


if __name__ == '__main__':
    unittest.main()
