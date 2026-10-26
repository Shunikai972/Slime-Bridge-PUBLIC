"""Provider SDK + conformance kit: healthy providers pass, broken ones cannot poison the core."""
import copy
import itertools
import unittest

from bridge.content.canonical import sha256_text
from bridge.content.discovery import discover
from bridge.conversion.extraction import SyntheticExtractionProvider
from bridge.conversion.manifest import ManifestSource
from bridge.sdk import providers as P
from bridge.sdk.conformance import run_provider_conformance
from conversion import world

WORLD = world.universe()


def adapter(universe=WORLD, realm='SR2'):
    return P.ManifestProviderAdapter(ManifestSource(copy.deepcopy(universe[realm])))


def failed(report):
    return {c['check'] for c in report['checks'] if c['status'] == 'FAILED'}


class ConformanceTests(unittest.TestCase):
    def test_well_behaved_provider_is_conformant(self):
        report = run_provider_conformance(adapter())
        self.assertTrue(report['conformant'], [c for c in report['checks'] if c['status'] != 'PASSED'])
        self.assertTrue(report['synthetic'])

    def test_nondeterministic_registry_fails(self):
        class Flaky(P.ManifestProviderAdapter):
            counter = itertools.count()

            def read_registry(self):
                data = super().read_registry()
                data['entries'][0]['definition_hash'] = sha256_text(str(next(self.counter)))
                return data
        self.assertIn('registry.deterministic', failed(run_provider_conformance(Flaky(ManifestSource(WORLD['SR2'])))))

    def test_wrong_realm_build_and_duplicate_entries_fail(self):
        class WrongBuild(P.ManifestProviderAdapter):
            def read_registry(self):
                data = super().read_registry()
                data['build']['fingerprint'] = 'some-other-build'
                return data
        report = run_provider_conformance(WrongBuild(ManifestSource(WORLD['SR2'])))
        self.assertIn('registry.build_binding', failed(report))
        self.assertIn('dependency.build_matches_registry', failed(report))

        class Duplicated(P.ManifestProviderAdapter):
            def read_registry(self):
                data = super().read_registry()
                data['entries'].append(copy.deepcopy(data['entries'][0]))
                data['traversal']['expected_count'] = data['traversal']['observed_count'] = len(data['entries'])
                return data
        self.assertIn('registry.entries_wellformed', failed(run_provider_conformance(Duplicated(ManifestSource(WORLD['SR2'])))))

        class WrongRealm(P.ManifestProviderAdapter):
            def describe(self):
                return {**super().describe(), 'realm': 'SR1'}
        report = run_provider_conformance(WrongRealm(ManifestSource(WORLD['SR2'])))
        self.assertFalse(report['conformant'])
        self.assertIn('descriptor', failed(report))

    def test_unproven_complete_claim_from_real_provider_fails(self):
        real = copy.deepcopy(WORLD['SR2'])
        for doc in (real, real['registry']):
            doc['data_evidence'] = 'STATIC'
        real['synthetic'] = False
        report = run_provider_conformance(P.ManifestProviderAdapter(ManifestSource(real)))
        self.assertIn('registry.completeness_semantics', failed(report))

    def test_dependency_misbehaviour_is_caught(self):
        class Fabricates(P.ManifestProviderAdapter):
            def inspect(self, ref):
                found = super().inspect(ref)
                return found or {'ref': ref, 'kind': 'mesh', 'hash': sha256_text(ref), 'attributes': None,
                                 'provenance': 'invented', 'references': []}
        self.assertIn('dependency.unknown_reference', failed(run_provider_conformance(Fabricates(ManifestSource(WORLD['SR2'])))))

        class WrongObject(P.ManifestProviderAdapter):
            def inspect(self, ref):
                found = super().inspect(ref)
                return dict(found, ref='Something.Else') if found and found['kind'] == 'slime_definition' else found
        self.assertIn('dependency.object_identity', failed(run_provider_conformance(WrongObject(ManifestSource(WORLD['SR2'])))))

        class UnstableHash(P.ManifestProviderAdapter):
            counter = itertools.count()

            def inspect(self, ref):
                found = super().inspect(ref)
                return dict(found, hash=sha256_text(str(next(self.counter)))) if found else found
        report = run_provider_conformance(UnstableHash(ManifestSource(WORLD['SR2'])))
        self.assertTrue({'dependency.object_identity', 'dependency.deterministic_graph'} & failed(report))

        class BadEdges(P.ManifestProviderAdapter):
            def inspect(self, ref):
                found = super().inspect(ref)
                if found and found['kind'] == 'texture':
                    found = dict(found, references=[{'relation': 'uses_material', 'target': 'x', 'slot': '',
                                                     'critical': None, 'target_kind': 'material',
                                                     'candidates': None, 'provenance': 'bad'}])
                return found
        self.assertIn('dependency.reference_graph', failed(run_provider_conformance(BadEdges(ManifestSource(WORLD['SR2'])))))

        class Raises(P.ManifestProviderAdapter):
            def build_fingerprint(self):
                raise RuntimeError('native call failed')
        self.assertIn('dependency', failed(run_provider_conformance(Raises(ManifestSource(WORLD['SR2'])))))

    def test_malformed_descriptors_are_rejected(self):
        for change in ({'provider_id': 'NoDots'}, {'provider_version': 'v1'}, {'sdk_schema_version': 99},
                       {'kinds': ['teleporter']}, {'features': {'extraction': ['mesh']}},
                       {'features': {'registry': ['warp_drive']}}, {'evidence': 'NEEDS_LOCAL_RUNTIME_PROOF'}):
            class Broken(P.ManifestProviderAdapter):
                def describe(self, change=change):
                    return {**super().describe(), **change}
            report = run_provider_conformance(Broken(ManifestSource(WORLD['SR2'])))
            self.assertFalse(report['conformant'], change)

    def test_extraction_and_registration_contracts(self):
        source = ManifestSource(WORLD['SR2'])
        graph = discover(source, ['SlimeDefinition.Azure'])['graph']

        class Extractor(P.BridgeProvider):
            realm = 'SR2'
            DESCRIPTOR = P.descriptor('test.extractor', '1.0.0', ['extraction'], realm='SR2',
                                      evidence='UNIT_TESTED', synthetic=True)

            def __init__(self, forge=False):
                self.inner, self.forge = SyntheticExtractionProvider('SR2'), forge

            def extract(self, request):
                if request['node'] not in graph.nodes:   # a real provider only extracts what exists
                    return {'node': request['node'], 'status': 'NOT_AVAILABLE', 'source_hash': request['source_hash'],
                            'synthetic': True}
                receipt = self.inner.extract(request)
                return dict(receipt, source_hash=sha256_text('x')) if self.forge else receipt
        ok = run_provider_conformance(Extractor(), sample_graph=graph, sample_root='SR2:SlimeDefinition.Azure')
        self.assertTrue(ok['conformant'], ok['checks'])
        bad = run_provider_conformance(Extractor(forge=True), sample_graph=graph, sample_root='SR2:SlimeDefinition.Azure')
        self.assertIn('extraction.receipts_bound', failed(bad))
        not_run = run_provider_conformance(Extractor())
        self.assertFalse(not_run['conformant'])     # NOT_RUN is never treated as passing

        class Registrar(P.BridgeProvider):
            realm = 'SR1'
            DESCRIPTOR = P.descriptor('test.registrar', '1.0.0', ['registration'], realm='SR1',
                                      evidence='UNIT_TESTED', synthetic=True)

            def __init__(self, reckless):
                self.reckless = reckless

            def capabilities(self):
                return {'realm': 'SR1', 'implemented_operations': []}

            def apply(self, plan):
                if not self.reckless and not plan.get('registrable'):
                    raise ValueError('refusing unregistrable plan')
                return {'status': 'APPLIED'}
        plan = {'registrable': False, 'operations': []}
        self.assertTrue(run_provider_conformance(Registrar(False), unregistrable_plan=plan)['conformant'])
        self.assertIn('registration.refuses_unregistrable',
                      failed(run_provider_conformance(Registrar(True), unregistrable_plan=plan)))


class RegistryTests(unittest.TestCase):
    def test_selection_is_deterministic_and_health_aware(self):
        registry = P.ProviderRegistry()

        def make(pid, version, status, builds=('*',), features=()):
            class Prov(P.BridgeProvider):
                realm = 'SR1'
                DESCRIPTOR = P.descriptor(pid, version, ['registry'], realm='SR1', supported_builds=builds,
                                          features={'registry': list(features)})

                def read_registry(self):
                    return {}

                def health(self):
                    if status == 'RAISE':
                        raise RuntimeError('probe crashed')
                    return P.health(status)
            return Prov()
        registry.register(make('vendor.old', '1.0.0', 'HEALTHY'))
        registry.register(make('vendor.new', '2.0.0', 'HEALTHY'))
        registry.register(make('vendor.exact', '1.0.0', 'HEALTHY', builds=('b1',)))
        registry.register(make('vendor.down', '9.0.0', 'UNAVAILABLE'))
        registry.register(make('vendor.crash', '9.0.0', 'RAISE'))
        chosen, _ = registry.select('registry', realm='SR1', build='b1')
        self.assertEqual(chosen.describe()['provider_id'], 'vendor.exact')
        chosen, _ = registry.select('registry', realm='SR1', build='b2')
        self.assertEqual(chosen.describe()['provider_id'], 'vendor.new')
        none, diags = registry.select('registry', realm='SR1', require=['complete_traversal'])
        self.assertIsNone(none)
        self.assertIn('PROVIDER_NONE_AVAILABLE', {d['code'] for d in diags})
        self.assertIsNone(registry.select('registry', realm='SR2')[0])
        with self.assertRaises(ValueError):
            registry.register(make('vendor.old', '1.0.0', 'HEALTHY'))
        statuses = {r['provider_id']: r['health']['status'] for r in registry.health_report()}
        self.assertEqual(statuses['vendor.crash'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()


class RuntimeAdapterConformanceTests(unittest.TestCase):
    def provider(self, *, honest=True):
        from bridge.sdk.providers import descriptor

        class Process:
            realm = 'SR2'

            def describe(self):
                return descriptor('local.process-sr2', '1.0.0', ['process', 'world_ready'], realm='SR2',
                                  features={'process': ['pid_verification', 'image_verification']})

            def health(self):
                return {'status': 'UNKNOWN', 'diagnostics': []}

            def status(self, session):
                return {'running': not honest, 'pid': session['pid']}

            def launch(self, realm, installation, save):
                raise NotImplementedError

            def attach(self, pid, *, expected_image_sha256, expected_executable):
                return None if honest else {'pid': pid, 'session_id': 'attached'}

            def park(self, session):
                return False

            def stop(self, session):
                return not honest

            def report(self, session, endpoint):
                if honest:
                    return None
                return {'session': session, 'scene_settled': True}
        return Process()

    def test_runtime_adapters_must_verify_before_controlling(self):
        from bridge.sdk.conformance import run_provider_conformance
        good = run_provider_conformance(self.provider())
        self.assertTrue(good['conformant'], good)
        bad = run_provider_conformance(self.provider(honest=False))
        failed = {c['check'] for c in bad['checks'] if c['status'] == 'FAILED'}
        self.assertEqual(failed, {'process.unknown_session', 'process.attach_verifies_image',
                                  'process.stop_refuses_unknown', 'world_ready.unknown_session'})
