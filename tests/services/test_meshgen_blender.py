"""Standalone Blender helper: parsed (never imported), parameter schema parity with PortalParams."""

import ast
import unittest
from pathlib import Path

from bridge.meshgen.palettes import SEMANTIC_MATERIALS
from bridge.meshgen.portal import PARAM_NAMES

SCRIPT = Path(__file__).resolve().parents[2] / 'bridge' / 'meshgen' / 'blender' / 'portal_blender.py'


def _constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f'{name} not found')


class BlenderScript(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text(encoding='utf-8')
        self.tree = ast.parse(self.source, filename=str(SCRIPT))

    def test_parses_and_never_imports_bpy_at_module_level(self):
        top = [n for n in self.tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {alias.name.split('.')[0] for n in top for alias in n.names} | \
                {n.module.split('.')[0] for n in top if isinstance(n, ast.ImportFrom) and n.module}
        self.assertNotIn('bpy', names)
        self.assertNotIn('bridge', names)  # the generator is only imported inside Blender when asked to
        main = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        self.assertTrue(any(isinstance(n, ast.Import) and n.names[0].name == 'bpy' for n in ast.walk(main)))

    def test_parameter_schema_matches_portal_params(self):
        self.assertEqual(tuple(_constant(self.tree, 'PORTAL_PARAM_KEYS')), PARAM_NAMES)
        self.assertEqual(tuple(_constant(self.tree, 'SEMANTIC_MATERIALS')), SEMANTIC_MATERIALS)

    def test_product_code_does_not_import_the_script(self):
        package = SCRIPT.parents[1]
        self.assertFalse((SCRIPT.parent / '__init__.py').exists())
        for path in package.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ''] + [alias.name for alias in node.names]
                else:
                    continue
                self.assertFalse([m for m in modules if 'blender' in m or m == 'bpy'], path.name)


if __name__ == '__main__':
    unittest.main()
