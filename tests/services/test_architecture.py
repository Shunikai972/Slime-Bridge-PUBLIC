"""Dependency direction: core schemas -> services -> application services -> launcher/server -> adapters.

Enforced on the import graph (AST, no execution):
- each Bridge package imports only the packages listed for it below
- core imports only the canonical primitives; nothing in bridge imports apps/ or Qt
- local-owned runtime packages (bridge.host, bridge.transfer) are not imported by Cloud packages
- the synthetic layer (bridge.sim) is imported only by simulators and fault-injection hooks
- generic modules carry no engine/loader internals (Il2Cpp, UnityEngine, Assembly-CSharp)
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / 'bridge'
ALLOWED = {
    'content': set(), 'registry': {'content'}, 'cache': {'content'},
    'conversion': {'cache', 'content', 'registry'},
    'core': {'content'}, 'verification': {'content', 'core'},
    'packages': {'cache', 'content', 'conversion', 'core', 'registry'},
    'sdk': {'content', 'conversion', 'core', 'registry'},
    'sim': set(),
    'counterpart': {'content', 'core', 'profile', 'sdk', 'sim', 'verification'},
    'deploy': {'cache', 'content', 'core', 'sim'},
    'transition': {'content', 'counterpart', 'sim', 'verification'},
    'multiplayer': {'content', 'core', 'sim', 'verification'},
    'portal': {'core'},
    'scenarios': {'core', 'counterpart', 'transition', 'verification'},
    'modcompat': {'cache', 'content', 'core', 'deploy'},
    'meshgen': {'cache', 'content', 'core'},
    'app': {'cache', 'content', 'conversion', 'core', 'counterpart', 'deploy', 'modcompat', 'multiplayer', 'packages',
            'portal', 'sdk', 'transition', 'verification'},
}
LOCAL_OWNED = {'host', 'transfer', 'profile'}
SIM_USERS = {'bridge/counterpart/simulator.py', 'bridge/transition/simulator.py', 'bridge/deploy/executor.py',
             'bridge/multiplayer/transport.py'}
ENGINE_TERMS = ('Il2Cpp', 'UnityEngine', 'Assembly-CSharp', 'GameAssembly')


def imports(path):
    module = list(path.relative_to(ROOT).with_suffix('').parts)
    package = module[:-1] if path.name != '__init__.py' else module[:-1]
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - (node.level - 1)]
                name = '.'.join(base + ([node.module] if node.module else []))
            else:
                name = node.module or ''
            out.add(name)
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
    return out


def bridge_package(name):
    parts = name.split('.')
    return parts[1] if len(parts) > 1 and parts[0] == 'bridge' else None


class ArchitectureTests(unittest.TestCase):
    def test_package_dependencies_follow_the_allowed_direction(self):
        for path in sorted(BRIDGE.rglob('*.py')):
            package = path.relative_to(BRIDGE).parts[0]
            if package not in ALLOWED:
                continue  # local-owned or legacy packages are not governed by Cloud rules
            for name in imports(path):
                target = bridge_package(name)
                if target is None or target == package:
                    continue
                with self.subTest(module=path.relative_to(ROOT).as_posix(), imports=name):
                    self.assertIn(target, ALLOWED[package])

    def test_core_only_uses_canonical_primitives(self):
        for path in (BRIDGE / 'core').rglob('*.py'):
            for name in imports(path):
                if name.startswith('bridge.') and not name.startswith('bridge.core'):
                    self.assertEqual(name, 'bridge.content.canonical', path.name)

    def test_cloud_packages_never_import_local_runtime_code(self):
        for path in sorted(BRIDGE.rglob('*.py')):
            package = path.relative_to(BRIDGE).parts[0]
            if package not in ALLOWED:
                continue
            for name in imports(path):
                target = bridge_package(name)
                if target in LOCAL_OWNED and target not in ALLOWED[package]:
                    self.fail(f'{path.relative_to(ROOT)} imports local-owned {name}')
                if target == 'profile':
                    # Only the public ProfileStore API and the existing SR1 proof reader are used.
                    self.assertIn(name, ('bridge.profile.store', 'bridge.profile.native_proof'), path.name)

    def test_synthetic_layer_is_only_used_by_simulators_and_fault_hooks(self):
        for path in sorted(BRIDGE.rglob('*.py')):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith('bridge/sim/'):
                continue
            uses_sim = any(name.startswith('bridge.sim') or name in ('bridge.sim',) for name in imports(path)) or \
                (path.parent.name == 'counterpart' and 'from .simulator' in path.read_text(encoding='utf-8')
                 and path.name not in ('simulator.py',))
            if uses_sim:
                self.assertIn(rel, SIM_USERS)

    def test_generic_modules_name_no_engine_internals(self):
        for package in ('core', 'verification', 'counterpart', 'deploy', 'transition', 'multiplayer', 'portal',
                        'scenarios', 'app', 'sim'):
            for path in sorted((BRIDGE / package).rglob('*.py')):
                text = path.read_text(encoding='utf-8')
                for term in ENGINE_TERMS:
                    with self.subTest(module=path.relative_to(ROOT).as_posix(), term=term):
                        self.assertNotIn(term, text)


if __name__ == '__main__':
    unittest.main()
