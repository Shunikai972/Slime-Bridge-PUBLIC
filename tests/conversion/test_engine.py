"""Staged engine: per-species verdicts, stage isolation, plan safety, both directions."""
import copy
import unittest

from bridge.content.canonical import sha256_json
from bridge.content.discovery import discover
from bridge.conversion import targets
from bridge.conversion.engine import STAGES, SourceBundle, convert_species, species_row
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from bridge.registry.reconcile import classify_species
from conversion import world

WORLD = world.universe()


def bundle(realm, world_data=WORLD, mapper=None):
    provider = ManifestSource(world_data[realm])
    return SourceBundle(provider, mapper or AttributeMaterialMapper(realm), world.source_material_profile(realm),
                        world.source_behaviour_profile(realm))


CLASSIFICATION = classify_species(ManifestSource(WORLD['SR1']).read_registry(),
                                  ManifestSource(WORLD['SR2']).read_registry())


def convert(cid, source='SR2', target=None, **kw):
    target = target or world.target_profile('SR1' if source == 'SR2' else 'SR2')
    return convert_species(cid, classification=CLASSIFICATION, source=bundle(source, **kw), target_profile=target)


def stages(result):
    return {s['stage']: s['status'] for s in result['stages']}


class EngineTests(unittest.TestCase):
    def test_degradable_behaviour_species_matches_reported_example(self):
        result = convert('slime.gale')
        self.assertEqual(result['classification'], 'SR2_ONLY')
        self.assertEqual(result['components'], {'visual': 'READY', 'material': 'READY',
                                                'behaviour': 'PENDING_ADAPTER',
                                                'target_registration': 'NEEDS_LOCAL_RUNTIME_PROOF'})
        self.assertEqual(result['final'], 'EXPERIMENTAL')
        self.assertIn('BEHAVIOUR_DEGRADED', result['reasons'])
        self.assertEqual([s['stage'] for s in result['stages']], list(STAGES))
        self.assertTrue(all(s['status'] in ('OK', 'DEGRADED') for s in result['stages']))
        self.assertTrue(result['synthetic'])
        package = result['products']['package']
        self.assertEqual((package['target_realm'], package['target']['runtime']), ('SR1', 'Mono'))
        self.assertTrue(package['synthetic'])
        plan = result['products']['registration_plan']
        self.assertEqual(targets.validate_registration_plan(plan, package, set()), [])
        kinds = [op['kind'] for op in plan['operations']]
        self.assertLess(kinds.index('BUILD_MATERIALS'), kinds.index('BUILD_PREFAB'))
        self.assertLess(kinds.index('BUILD_PREFAB'), kinds.index('REGISTER_SLIME_DEFINITION'))
        self.assertIn('REGISTER_PLORT', kinds)
        self.assertIn('BIND_PERSISTENCE', kinds)

    def test_missing_critical_mesh_quarantines_and_skips_later_stages(self):
        result = convert('slime.hollow')
        self.assertEqual(result['final'], 'QUARANTINED')
        self.assertEqual(stages(result)['DEPENDENCY_RESOLUTION'], 'FAILED')
        self.assertEqual(stages(result)['PACKAGE_ASSEMBLY'], 'SKIPPED')
        self.assertEqual(result['components']['visual'], 'QUARANTINED')
        self.assertIsNone(result['products']['package'])

    def test_unknown_shader_and_unknown_behaviour_are_pending_adapter(self):
        murk = convert('slime.murk')
        self.assertEqual((murk['final'], murk['components']['material']), ('PENDING_ADAPTER', 'PENDING_ADAPTER'))
        flicker = convert('slime.flicker')
        self.assertEqual(flicker['final'], 'PENDING_ADAPTER')
        self.assertIn('BEHAVIOUR_PENDING_ADAPTER', flicker['reasons'])
        caps = [c['id'] for c in flicker['products']['ir']['gameplay']['capabilities']]
        self.assertIn('unknown.fixture_blink', caps)

    def test_degraded_inputs_stay_experimental(self):
        moss = convert('slime.moss')
        self.assertEqual((moss['final'], moss['components']['visual']), ('EXPERIMENTAL', 'DEGRADED'))
        opal = convert('slime.opal')
        self.assertEqual((opal['final'], opal['components']['material']), ('EXPERIMENTAL', 'DEGRADED'))
        unknown = opal['products']['ir']['visual']['materials'][0]['semantic']['unknown_properties']
        self.assertEqual([p['name'] for p in unknown], ['_Iridescence'])

    def test_multi_material_slot_order_and_optional_vfx(self):
        prism = convert('slime.prism')
        renderer = prism['products']['ir']['visual']['renderers'][0]
        self.assertEqual(renderer['materials'], [f'SR2:Fixture/Prism/Material{i}' for i in range(3)])
        ember = convert('slime.ember')
        self.assertEqual(ember['final'], 'EXPERIMENTAL')
        self.assertEqual(len(ember['products']['package']['prefab']['vfx']), 1)
        aura = [c for c in ember['products']['package']['behaviour']['capabilities'] if c['id'] == 'hazard.aura']
        self.assertEqual(aura[0]['mode'], 'NATIVE')

    def test_reverse_direction_skinned_species(self):
        quartz = convert('slime.quartz', source='SR1')
        self.assertEqual(quartz['direction'], 'SR1->SR2')
        self.assertEqual(quartz['final'], 'EXPERIMENTAL')
        package = quartz['products']['package']
        self.assertEqual((package['target']['runtime'], package['target']['unity']), ('IL2CPP', '2021.3.28f1'))
        renderer = quartz['products']['ir']['visual']['renderers'][0]
        self.assertEqual(renderer['kind'], 'skinned')
        self.assertEqual(renderer['bones'], [f'SR1:Fixture/Quartz/Bone{i}' for i in range(3)])
        self.assertEqual(renderer['root_bone'], 'SR1:Fixture/Quartz/Bone0')
        transforms = {a['transform'] for a in package['artifacts']}
        self.assertIn('rebuild_mesh_for_target_unity', transforms)
        cinder = convert('slime.cinder', source='SR1')
        self.assertEqual(cinder['final'], 'PENDING_ADAPTER')

    def test_cycle_is_reported_not_fatal(self):
        loop = convert('slime.loop', source='SR1')
        self.assertEqual(loop['final'], 'EXPERIMENTAL')
        codes = {d['code'] for s in loop['stages'] for d in s['diagnostics']}
        self.assertIn('GRAPH_CYCLE', codes)

    def test_shared_species_is_refused_not_converted(self):
        result = convert('slime.pink')
        self.assertEqual(stages(result)['VALIDATION'], 'FAILED')
        self.assertEqual(result['final'], 'QUARANTINED')
        self.assertIsNone(result['products']['registration_plan'])

    def test_stage_exception_is_contained(self):
        class Exploding(AttributeMaterialMapper):
            def describe(self, node, graph=None):
                raise RuntimeError('mapper bug')
        result = convert('slime.azure', mapper=Exploding('SR2'))
        self.assertEqual(stages(result)['NEUTRALIZATION'], 'FAILED')
        self.assertEqual(result['final'], 'QUARANTINED')
        self.assertIn('STAGE_EXCEPTION', {d['code'] for s in result['stages'] for d in s['diagnostics']})

    def test_deterministic_and_shared_discovery_equivalent(self):
        first, second = convert('slime.azure'), convert('slime.azure')
        self.assertEqual(sha256_json(first), sha256_json(second))
        provider = ManifestSource(WORLD['SR2'])
        roots = [provider.definition_root(r['native']['SR2']) for r in CLASSIFICATION['species']
                 if r['native'].get('SR2') and not r['native']['SR2']['malformed']]
        shared = discover(provider, roots)
        batch = convert_species('slime.azure', classification=CLASSIFICATION, source=bundle('SR2'),
                                target_profile=world.target_profile('SR1'), discovery=shared)
        self.assertEqual(batch['products']['package'], first['products']['package'])
        self.assertEqual(batch['final'], first['final'])

    def test_builtin_real_target_profile_never_claims_more_than_known(self):
        result = convert('slime.azure', target=targets.builtin_target_profile('SR1'))
        self.assertEqual(result['final'], 'PENDING_ADAPTER')
        self.assertEqual(result['components']['material'], 'PENDING_ADAPTER')
        self.assertEqual(result['components']['target_registration'], 'BLOCKED_ON_LOCAL_QUERY')
        self.assertIn('REGISTRATION_BLOCKED_ON_LOCAL_QUERY', result['reasons'])
        ops = result['products']['registration_plan']['operations']
        self.assertTrue(all(op['native_api'] is None and op['local_query'] == 'Q007' for op in ops))


class RegistrationSafetyTests(unittest.TestCase):
    def setUp(self):
        result = convert('slime.azure')
        self.package = result['products']['package']
        self.plan = result['products']['registration_plan']

    def test_plan_refuses_duplicates_foreign_writes_order_and_native_ids(self):
        self.assertEqual(targets.validate_registration_plan(self.plan, self.package, set()), [])
        codes = lambda plan, natives=frozenset(): {d['code'] for d in targets.validate_registration_plan(
            plan, self.package, set(natives))}
        self.assertIn('WOULD_DUPLICATE_NATIVE', codes(self.plan, {'slime.azure'}))
        self.assertIn('WOULD_DUPLICATE_NATIVE', codes(self.plan, {'plort.azure'}))
        reordered = copy.deepcopy(self.plan)
        reordered['operations'].reverse()
        self.assertIn('DEPENDENCY_ORDER', codes(reordered))
        numeric = copy.deepcopy(self.plan)
        numeric['operations'][0]['payload']['numeric_id'] = 41
        self.assertIn('TARGET_NATIVE_ID_IN_PLAN', codes(numeric))
        foreign = copy.deepcopy(self.plan)
        foreign['operations'][-1]['canonical_id'] = 'slime.pink'
        self.assertIn('FOREIGN_OPERATION', codes(foreign))
        other = copy.deepcopy(self.plan)
        other['package_id'] = '0' * 64
        self.assertIn('PACKAGE_MISMATCH', codes(other))

    def test_native_target_plort_is_referenced_not_registered(self):
        plan = targets.plan_registration(self.package, world.target_profile('SR1'), {'plort.azure'})
        kinds = [op['kind'] for op in plan['operations']]
        self.assertIn('REFERENCE_NATIVE_PLORT', kinds)
        self.assertNotIn('REGISTER_PLORT', kinds)

    def test_op_ids_are_stable(self):
        again = targets.plan_registration(self.package, world.target_profile('SR1'), set())
        self.assertEqual(again, self.plan)


if __name__ == '__main__':
    unittest.main()


class EvidencePropagationTests(unittest.TestCase):
    def test_self_declared_runtime_manifest_never_yields_runtime_ir(self):
        universe = copy.deepcopy(WORLD)
        for doc in (universe['SR2'], universe['SR2']['registry']):
            doc['data_evidence'] = 'RUNTIME_ANCHORED'
        universe['SR2']['synthetic'] = False
        classification = classify_species(ManifestSource(universe['SR1']).read_registry(),
                                          ManifestSource(universe['SR2']).read_registry())
        self.assertEqual(classification['data_evidence']['SR2'], 'STATIC')
        result = convert_species('slime.azure', classification=classification, source=bundle('SR2', universe),
                                 target_profile=world.target_profile('SR1'))
        ir = result['products']['ir']
        self.assertEqual(ir['provenance']['data_evidence'], 'STATIC')
        self.assertNotEqual(ir['status'], 'AVAILABLE')

    def test_cache_inputs_track_source_evidence_and_synthetic_consistently(self):
        from bridge.conversion.engine import cache_inputs
        static_world = copy.deepcopy(WORLD)
        for doc in (static_world['SR2'], static_world['SR2']['registry']):
            doc['data_evidence'] = 'STATIC'
        static_world['SR2']['synthetic'] = False
        provider = ManifestSource(static_world['SR2'])
        graph = discover(provider, ['SlimeDefinition.Azure'])['graph']
        static_bundle = bundle('SR2', static_world)
        base = cache_inputs('slime.azure', CLASSIFICATION, static_bundle, world.target_profile('SR1'), graph)
        changed = copy.deepcopy(CLASSIFICATION)
        changed['data_evidence']['SR2'] = 'STATIC'
        other = cache_inputs('slime.azure', changed, static_bundle, world.target_profile('SR1'), graph)
        self.assertEqual((base['source_evidence'], other['source_evidence']), ('SYNTHETIC', 'STATIC'))
        synthetic_provider = cache_inputs('slime.azure', changed, bundle('SR2'), world.target_profile('SR1'), graph)
        self.assertEqual(synthetic_provider['source_evidence'], 'SYNTHETIC')  # a provider can only lower it
        builtin = targets.builtin_target_profile('SR1')
        early = convert_species('slime.hollow', classification=CLASSIFICATION, source=bundle('SR2'),
                                target_profile=builtin)
        inputs = cache_inputs('slime.hollow', CLASSIFICATION, bundle('SR2'), builtin, graph)
        self.assertEqual(early['final'], 'QUARANTINED')
        self.assertIs(early['synthetic'], True)
        self.assertEqual(early['synthetic'], inputs['synthetic'])


class RegistrationReviewRegressions(unittest.TestCase):
    def test_ambiguous_target_plort_still_counts_as_native(self):
        from bridge.conversion.engine import target_native_canonicals
        universe = copy.deepcopy(WORLD)
        dup = copy.deepcopy(next(e for e in universe['SR1']['registry']['entries'] if e['native_ref'] == 'PINK_PLORT'))
        dup['native_ref'] = 'Pink_Plort'
        universe['SR1']['registry']['entries'].append(dup)
        for doc in (universe['SR1']['registry']['traversal'],):
            doc['expected_count'] = doc['observed_count'] = len(universe['SR1']['registry']['entries'])
        classification = classify_species(ManifestSource(universe['SR1']).read_registry(),
                                          ManifestSource(universe['SR2']).read_registry())
        pink = next(r for r in classification['plorts'] if r['canonical_id'] == 'plort.pink')
        self.assertEqual(pink['classification'], 'QUARANTINED')
        self.assertIn('plort.pink', target_native_canonicals(classification, 'SR1'))

    def test_plort_identity_follows_registry_aliases_through_mapped_roots(self):
        from bridge.registry.reconcile import load_aliases
        universe = copy.deepcopy(WORLD)
        sr2 = universe['SR2']
        for obj in sr2['objects']:
            if obj['ref'] == 'IdentifiableType.AzurePlort':
                obj['ref'] = 'Obj/AzurePlort'
            for ref in obj['references']:
                if ref['target'] == 'IdentifiableType.AzurePlort':
                    ref['target'] = 'Obj/AzurePlort'
        sr2['roots'] = {'IdentifiableType.AzurePlort': 'Obj/AzurePlort'}
        aliases = load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': [
            {'realm': 'SR2', 'native_ref': 'IdentifiableType.AzurePlort', 'canonical_id': 'plort.sky',
             'evidence': 'fixture alias'}]})
        classification = classify_species(ManifestSource(universe['SR1']).read_registry(),
                                          ManifestSource(sr2).read_registry(), aliases=aliases)
        result = convert_species('slime.azure', classification=classification, source=bundle('SR2', universe),
                                 target_profile=world.target_profile('SR1'))
        self.assertEqual([p['canonical_id'] for p in result['products']['ir']['associated']['plorts']], ['plort.sky'])
        ops = [op for op in result['products']['registration_plan']['operations'] if op['kind'] == 'REGISTER_PLORT']
        self.assertEqual([op['payload']['plort'] for op in ops], ['plort.sky'])

    def test_missing_icon_is_not_a_null_registration(self):
        universe = copy.deepcopy(WORLD)
        for obj in universe['SR2']['objects']:
            if obj['ref'] == 'SlimeDefinition.Azure':
                obj['references'] = [r for r in obj['references'] if r['relation'] != 'has_icon']
        result = convert_species('slime.azure', classification=CLASSIFICATION, source=bundle('SR2', universe),
                                 target_profile=world.target_profile('SR1'))
        kinds = [op['kind'] for op in result['products']['registration_plan']['operations']]
        self.assertNotIn('REGISTER_ICON', kinds)
        codes = {d['code'] for s in result['stages'] for d in s['diagnostics']}
        self.assertIn('NO_ICON', codes)
        plan = copy.deepcopy(convert('slime.azure')['products']['registration_plan'])
        for op in plan['operations']:
            if op['kind'] == 'REGISTER_ICON':
                op['payload']['artifact'] = None
        self.assertIn('NULL_ARTIFACT', {d['code'] for d in targets.validate_registration_plan(
            plan, convert('slime.azure')['products']['package'], set())})

    def test_unready_adapters_and_materials_are_deferred_not_planned(self):
        gale = convert('slime.gale')['products']['registration_plan']
        attached = [op['payload']['capability'] for op in gale['operations'] if op['kind'] == 'ATTACH_BEHAVIOUR']
        self.assertNotIn('movement.flight', attached)
        self.assertIn('movement.flight', [d['capability'] for d in gale['deferred']])
        self.assertTrue(gale['registrable'])
        murk = convert('slime.murk')['products']['registration_plan']
        self.assertFalse(murk['registrable'])
        build = next(op for op in murk['operations'] if op['kind'] == 'BUILD_MATERIALS')
        self.assertEqual(build['payload']['materials'], [])
        self.assertTrue(murk['blockers'])


class BoneOrderTests(unittest.TestCase):
    def _with_bone_slots(self, slots):
        universe = copy.deepcopy(WORLD)
        for obj in universe['SR2']['objects']:
            if obj['ref'] == 'Fixture/Azure/Renderer':
                obj['references'] = [r for r in obj['references'] if r['relation'] != 'uses_bone']
                for slot, bone in slots:
                    obj['references'].append({'relation': 'uses_bone', 'target': bone, 'slot': slot,
                                              'critical': None, 'target_kind': None, 'candidates': None,
                                              'provenance': 'test'})
        return convert_species('slime.azure', classification=CLASSIFICATION, source=bundle('SR2', universe),
                               target_profile=world.target_profile('SR1'))

    def test_bone_slots_must_be_a_contiguous_unique_sequence(self):
        b0, b1 = 'Fixture/Azure/Bone0', 'Fixture/Azure/Bone1'
        ok = self._with_bone_slots([('root', b0), ('0', b0), ('1', b1)])
        self.assertEqual(ok['final'], 'EXPERIMENTAL')
        for slots in ([('root', b0), ('0', b0), ('2', b1)],          # gap
                      [('root', b0), ('0', b0), ('0', b1)],          # duplicate index
                      [('root', b0), ('0', b0), ('spine', b1)]):     # unknown slot
            result = self._with_bone_slots(slots)
            self.assertEqual(result['final'], 'QUARANTINED', slots)
            codes = {d['code'] for s in result['stages'] for d in s['diagnostics']}
            self.assertIn('BONE_ORDER_INVALID', codes)


class SecondReviewRegressions(unittest.TestCase):
    def test_synthetic_source_with_anchored_registry_stays_synthetic(self):
        from test_largo_coverage import snapshot, definition
        from bridge.registry.snapshot import snapshot_from_scan
        anchored = snapshot_from_scan(snapshot('SR2', [definition('azure', 'SR2')]))
        universe = copy.deepcopy(WORLD)
        sr2 = universe['SR2']
        sr2['build']['fingerprint'] = sr2['registry']['build']['fingerprint'] = anchored['build']['fingerprint']
        sr2['roots'] = {'azure-ref': 'SlimeDefinition.Azure'}
        provider = ManifestSource(sr2, registry=anchored)
        classification = classify_species(ManifestSource(universe['SR1']).read_registry(), provider.read_registry())
        self.assertEqual(classification['data_evidence']['SR2'], 'RUNTIME_ANCHORED')
        source = SourceBundle(provider, AttributeMaterialMapper('SR2'), world.source_material_profile('SR2'),
                              world.source_behaviour_profile('SR2'))
        result = convert_species('slime.azure', classification=classification, source=source,
                                 target_profile=targets.builtin_target_profile('SR1'))
        self.assertIs(result['synthetic'], True)
        self.assertEqual(result['products']['ir']['provenance']['data_evidence'], 'SYNTHETIC')
        self.assertNotEqual(result['products']['ir']['status'], 'AVAILABLE')

    def test_icon_without_artifact_is_a_blocker_not_silent(self):
        package = copy.deepcopy(convert('slime.azure')['products']['package'])
        package['associated']['icon']['artifact'] = None
        plan = targets.plan_registration(package, world.target_profile('SR1'), set())
        self.assertFalse(plan['registrable'])
        self.assertTrue(any(b.startswith('ICON_ARTIFACT_MISSING') for b in plan['blockers']))
        self.assertIn('icon', [d.get('asset') for d in plan['deferred']])
