"""Capabilities: unknown behaviour propagates as PENDING_ADAPTER; degradable ones do not block."""
import copy
import unittest

from bridge.content.canonical import sha256_text
from bridge.content.graph import GraphBuilder
from bridge.conversion import capabilities as c

SOURCE = {'schema': 'bridge-behaviour-profile', 'schema_version': 1, 'realm': 'SR2', 'synthetic': True,
          'components': {'Fixture.Wings': {'capability': 'movement.flight'},
                         'Fixture.Glow': {'capability': None},
                         'Fixture.Embers': {'capability': 'diet.fire_feeding'}}}
TARGET = {'schema': 'bridge-target-behaviour-profile', 'schema_version': 1, 'realm': 'SR1', 'synthetic': True,
          'capabilities': {'standard.vacpack': {'mode': 'NATIVE'}, 'standard.diet': {'mode': 'NATIVE'},
                           'standard.plort_production': {'mode': 'NATIVE'},
                           'movement.flight': {'mode': 'BRIDGE_ADAPTER', 'adapter': 'bridge.flight',
                                               'implemented': False}}}
NATIVE = {'role': 'base', 'produces': ['plort.x']}


def species(*behaviours):
    b = GraphBuilder()
    b.add_node('SR2', 'Def', 'slime_definition', sha256_text('Def'), provenance='p')
    b.add_node('SR2', 'Prefab', 'prefab', sha256_text('Prefab'), provenance='p')
    b.add_edge('SR2', 'Def', 'has_prefab', 'Prefab', provenance='p')
    for ref, native_type in behaviours:
        b.add_node('SR2', ref, 'behaviour', sha256_text(ref), provenance='p',
                   attributes={'type': native_type} if native_type else None)
        b.add_edge('SR2', 'Prefab', 'has_behaviour', ref, provenance='p')
    graph = b.freeze()
    return graph, graph.analyze('SR2:Def')


def ids(rows):
    return [r['id'] for r in rows]


class CapabilityTests(unittest.TestCase):
    def test_core_capabilities_come_from_registry_and_plain_species_is_ready(self):
        graph, analysis = species()
        caps = c.source_capabilities(graph, analysis, NATIVE, SOURCE)
        self.assertEqual(ids(caps['capabilities']), ['standard.diet', 'standard.plort_production', 'standard.vacpack'])
        plan = c.plan_behaviour(caps['capabilities'], TARGET)
        self.assertEqual((plan['status'], plan['blocking']), ('READY', False))

    def test_degradable_capability_without_adapter_does_not_block(self):
        graph, analysis = species(('Wings', 'Fixture.Wings'), ('Glow', 'Fixture.Glow'))
        caps = c.source_capabilities(graph, analysis, NATIVE, SOURCE)
        self.assertIn('movement.flight', ids(caps['capabilities']))
        self.assertIn('BEHAVIOUR_DECLARED_INERT', {d['code'] for d in caps['diagnostics']})
        plan = c.plan_behaviour(caps['capabilities'], TARGET)
        self.assertEqual((plan['status'], plan['blocking']), ('PENDING_ADAPTER', False))
        implemented = copy.deepcopy(TARGET)
        implemented['capabilities']['movement.flight']['implemented'] = True
        self.assertEqual(c.plan_behaviour(caps['capabilities'], implemented)['status'], 'READY')

    def test_unknown_behaviour_propagates_and_blocks(self):
        graph, analysis = species(('Mystery', 'Fixture.Teleporter'), ('Untyped', None))
        caps = c.source_capabilities(graph, analysis, NATIVE, SOURCE)
        unknown = [r for r in caps['capabilities'] if r['id'].startswith('unknown.')]
        self.assertEqual(sorted(r['id'] for r in unknown), ['unknown.fixture_teleporter', 'unknown.untyped'])
        self.assertTrue(all(r['policy'] == 'UNKNOWN' for r in unknown))
        plan = c.plan_behaviour(caps['capabilities'], TARGET)
        self.assertEqual((plan['status'], plan['blocking']), ('PENDING_ADAPTER', True))

    def test_required_special_capability_blocks(self):
        graph, analysis = species(('Embers', 'Fixture.Embers'))
        caps = c.source_capabilities(graph, analysis, NATIVE, SOURCE)
        self.assertTrue(c.plan_behaviour(caps['capabilities'], TARGET)['blocking'])

    def test_profile_hash_scopes_to_used_entries(self):
        graph, analysis = species(('Wings', 'Fixture.Wings'))
        first = c.source_capabilities(graph, analysis, NATIVE, SOURCE)['profile_entry_hash']
        other = copy.deepcopy(SOURCE)
        other['components']['Fixture.Embers']['capability'] = 'hazard.aura'
        self.assertEqual(c.source_capabilities(graph, analysis, NATIVE, other)['profile_entry_hash'], first)
        other['components']['Fixture.Wings']['capability'] = 'movement.teleport'
        self.assertNotEqual(c.source_capabilities(graph, analysis, NATIVE, other)['profile_entry_hash'], first)

    def test_profiles_validated(self):
        with self.assertRaises(ValueError):
            c.validate_behaviour_profile({**SOURCE, 'components': {'X': {'capability': 'made.up'}}})
        with self.assertRaises(ValueError):
            c.validate_target_behaviour_profile({**TARGET, 'capabilities': {
                'movement.flight': {'mode': 'BRIDGE_ADAPTER'}}})


if __name__ == '__main__':
    unittest.main()
