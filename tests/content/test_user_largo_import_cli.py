"""Read-only Largo readiness acceptance tests; synthetic assets only."""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBJ = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
CATALOG = {'schema': 'bridge-user-largo-catalog', 'schema_version': 1,
           'species': [{'canonical_id': 'slime.alpha', 'aliases': ['alpha']},
                       {'canonical_id': 'slime.beta', 'aliases': ['beta']}]}
META = {'transform': {'scale': [1.0, 1.0, 1.0], 'orientation_xyzw': [0, 0, 0, 1], 'pivot': [0, 0, 0]},
        'collision': {'mode': 'parent_derived'}, 'face_anchors': [], 'animation_binding': {'mode': 'static'},
        'source_realms': [], 'intended_realms': ['SR1', 'SR2'],
        'target_parent_availability': {'SR1': ['slime.alpha', 'slime.beta']}}


def snapshot(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


class ImportCLITests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / 'Lab' / 'GeneratedContent'
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='user-import-test-', dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.models = self.base / 'models'
        self.models.mkdir()
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_text(json.dumps(CATALOG))

    def command(self, verb='scan', *extra, root=True, catalog=True):
        args = [sys.executable, '-m', 'bridge.sdk.largo_cli', verb]
        if root:
            args += ['--root', str(self.models)]
        if catalog:
            args += ['--catalog', str(self.catalog)]
        before = snapshot(self.models)
        result = subprocess.run(args + list(extra), cwd=ROOT, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(snapshot(self.models), before, 'every CLI command must preserve source hashes/mtimes')
        if os.environ.get('LARGO_TEST_COMMAND_LOG'):
            log = Path(os.environ['LARGO_TEST_COMMAND_LOG'])
            with log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'argv': args + list(extra), 'exit_code': result.returncode,
                                         'stdout_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
                                         'stderr': result.stderr}) + '\n')
        try:
            report = json.loads(result.stdout)
        except ValueError:
            self.fail(f'non-JSON exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}')
        return result.returncode, report

    def package(self, name='package', metadata=True):
        package = self.models / name
        package.mkdir()
        (package / 'body.obj').write_bytes(OBJ)
        manifest = {'schema': 'bridge-user-largo-package', 'schema_version': 1,
                    'canonical_largo_id': 'largo.alpha.beta', 'parents': ['slime.alpha', 'slime.beta'],
                    'version': '1.0.0', 'assets': [{'path': 'body.obj', 'kind': 'mesh',
                    'sha256': hashlib.sha256(OBJ).hexdigest(), 'role': 'body'}],
                    'materials': [{'id': 'body', 'family': 'slime_body',
                                   'channels': {'gradient.top': {'value': [1, 0, 0, 1]}}}],
                    'renderers': [{'id': 'body', 'mesh': 'body.obj', 'materials': ['body'], 'skinned': False}],
                    'face': None, 'animations': [], 'behaviour': {'compose': 'union', 'capabilities': [], 'exclude': []},
                    'targets': {'SR1': {}, 'SR2': {}},
                    'provenance': {'author': 'synthetic test', 'license': 'user-owned', 'user_owned': True}}
        if metadata:
            manifest['import_metadata'] = json.loads(json.dumps(META))
        self.write_manifest(package, manifest)
        return package, manifest

    @staticmethod
    def write_manifest(package, manifest):
        (package / 'largo-package.json').write_text(json.dumps(manifest))

    def test_scan_explicit_catalog_deterministic_and_read_only(self):
        (self.models / 'Beta-Alpha.obj').write_bytes(OBJ)
        before = snapshot(self.models)
        code, report = self.command()
        self.assertEqual(code, 0, report)
        entry = report['entries'][0]
        self.assertEqual(entry['match_status'], 'MATCHED')
        self.assertEqual(entry['parents'], ['slime.alpha', 'slime.beta'])
        self.assertEqual(entry['canonical_largo_id'], 'largo.alpha.beta')
        self.assertEqual(entry['manifest_proposal']['parents'], entry['parents'])
        self.assertIsNone(entry['manifest_proposal']['provenance']['user_owned'])
        self.assertEqual(report['registration_receipt']['status'], 'NOT_EXECUTED')
        self.assertEqual(self.command()[1], report)
        self.assertEqual(snapshot(self.models), before)

    def test_duplicate_ambiguous_unknown_and_same_parent(self):
        catalog = json.loads(self.catalog.read_text())
        catalog['species'] += [{'canonical_id': 'slime.gamma', 'aliases': ['alpha', 'gamma']}]
        self.catalog.write_text(json.dumps(catalog))
        for name in ('Alpha-Beta.obj', 'Beta-Gamma.obj', 'Gamma-Beta.stl', 'Beta-Beta.obj', 'Beta-Missing.obj'):
            (self.models / name).write_bytes(OBJ)
        before = snapshot(self.models)
        code, report = self.command()
        self.assertEqual(code, 4, report)
        self.assertEqual({e['path']: e['match_status'] for e in report['entries']},
                         {'Alpha-Beta.obj': 'AMBIGUOUS', 'Beta-Gamma.obj': 'DUPLICATE',
                          'Gamma-Beta.stl': 'DUPLICATE', 'Beta-Beta.obj': 'INVALID', 'Beta-Missing.obj': 'UNKNOWN'})
        self.assertEqual(snapshot(self.models), before)

    def test_root_explicit_or_configured_no_settings_writes(self):
        from bridge.app.settings import DEFAULTS, normalize
        (self.models / 'Alpha-Beta.obj').write_bytes(OBJ)
        code, report = self.command(root=False)
        self.assertEqual(code, 2, report)
        self.assertIn('ROOT_REQUIRED', {d['code'] for d in report['diagnostics']})
        self.assertIsNone(DEFAULTS['content']['user_largos_root'])
        settings = json.loads(json.dumps(DEFAULTS))
        settings['content']['user_largos_root'] = str(self.models)
        self.assertEqual(normalize(settings)[0], settings)
        path = self.base / 'settings.json'
        path.write_text(json.dumps(settings))
        before = snapshot(self.base)
        self.assertEqual(self.command('scan', '--settings', str(path), root=False)[0], 0)
        self.assertEqual(snapshot(self.base), before)
        path.write_text('{bad')
        before = snapshot(self.base)
        self.assertEqual(self.command('scan', '--settings', str(path), root=False)[0], 4)
        self.assertEqual(snapshot(self.base), before)
        for path in ('.', str(self.models / '..'), str(self.models / 'absent')):
            self.assertEqual(self.command('scan', '--root', path)[0], 4)

    def test_safe_exclusive_output_outside_root(self):
        (self.models / 'Alpha-Beta.obj').write_bytes(OBJ)
        before = snapshot(self.models)
        for path in (self.models / 'report.json', self.models, self.base):
            code, report = self.command('scan', '--out', str(path))
            self.assertEqual(code, 5, report)
        output = self.base / 'report.json'
        code, report = self.command('scan', '--out', str(output))
        self.assertEqual(code, 0, report)
        self.assertEqual(json.loads(output.read_text()), report)
        preserved = output.read_bytes()
        self.assertEqual(self.command('scan', '--out', str(output))[0], 5)
        self.assertEqual(output.read_bytes(), preserved)
        hardlink = self.base / 'linked.obj'
        os.link(self.models / 'Alpha-Beta.obj', hardlink)
        self.assertEqual(self.command('scan', '--out', str(hardlink))[0], 5)
        hardlink.unlink()
        self.assertEqual(snapshot(self.models), before)

    def test_reparse_and_hardlinks_are_rejected_without_traversal(self):
        from portability import directory_link
        (self.models / 'Alpha-Beta.obj').write_bytes(OBJ)
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'Beta-Alpha.obj').write_bytes(OBJ)
        before = snapshot(outside)
        with directory_link(self.base / 'root-link', self.models):
            self.assertEqual(self.command('scan', '--root', str(self.base / 'root-link'))[0], 4)
        with directory_link(self.models / 'child-link', outside):
            code, report = self.command()
            self.assertEqual(code, 4, report)
            self.assertIn('UNSAFE_SOURCE', {d['code'] for d in report['diagnostics']})
            self.assertFalse(report['entries'])
        with directory_link(self.base / 'output-link', outside):
            self.assertEqual(self.command('scan', '--out', str(self.base / 'output-link' / 'report.json'))[0], 5)
        os.link(self.models / 'Alpha-Beta.obj', self.models / 'hardlink.obj')
        self.assertEqual(self.command()[0], 4)
        (self.models / 'hardlink.obj').unlink()
        self.assertEqual(snapshot(outside), before)

    def test_validate_nonempty_finite_and_malformed_geometry(self):
        model = self.models / 'Alpha-Beta.obj'
        model.write_bytes(OBJ)
        before = snapshot(self.models)
        code, report = self.command('validate')
        self.assertEqual(code, 0, report)
        geometry = report['entries'][0]['geometry'][0]
        self.assertEqual(geometry['status'], 'GEOMETRY_VALID')
        self.assertEqual((geometry['vertices'], geometry['triangles']), (3, 1))
        self.assertEqual(geometry['bounds'], {'min': [0.0, 0.0, 0.0], 'max': [1.0, 1.0, 0.0]})
        self.assertFalse(geometry['runtime_checked'])
        self.assertEqual(snapshot(self.models), before)
        for data, expected in [(b'', 'EMPTY_GEOMETRY'), (b'v nan 0 0\nf 1 1 1\n', 'NONFINITE_GEOMETRY'),
                               (b'v 0 0 0\nf 1 2 3\n', 'MALFORMED_GEOMETRY'),
                               (OBJ + b'mtllib ../outside.mtl\n', 'UNSAFE_REFERENCE'),
                               (OBJ + b'mtllib missing.mtl\n', 'REFERENCE_UNREADABLE'),
                               (OBJ + b'usemtl missing\n', 'MATERIAL_UNDEFINED')]:
            with self.subTest(expected=expected):
                model.write_bytes(data)
                before = snapshot(self.models)
                code, report = self.command('validate')
                self.assertEqual(code, 4, report)
                self.assertIn(expected, {d['code'] for d in report['entries'][0]['geometry'][0]['diagnostics']})
                self.assertEqual(snapshot(self.models), before)

    def test_validate_material_texture_references_confined_hashed(self):
        (self.models / 'Alpha-Beta.obj').write_bytes(b'mtllib body.mtl\nusemtl body\n' + OBJ)
        material = self.models / 'body.mtl'
        material.write_text('newmtl body\nKd 1 0 0\nmap_Kd body.png\n')
        (self.models / 'body.png').write_bytes(b'synthetic reference only, no image decoding')
        before = snapshot(self.models)
        code, report = self.command('validate')
        self.assertEqual(code, 0, report)
        geometry = report['entries'][0]['geometry'][0]
        self.assertEqual(geometry['materials'], ['body'])
        self.assertEqual([r['path'] for r in geometry['external_references']], ['body.mtl', 'body.png'])
        self.assertTrue(all(len(r['sha256']) == 64 for r in geometry['external_references']))
        self.assertEqual(snapshot(self.models), before)
        material.write_text('newmtl body\nmap_Kd https://invalid.example/body.png\n')
        self.assertEqual(self.command('validate')[0], 4)

    def test_stl_and_unsupported_formats_report_truthfully(self):
        model = self.models / 'Alpha-Beta.stl'
        model.write_bytes(b'solid binary header'.ljust(80, b' ') + struct.pack('<I', 1) +
                          struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0))
        code, report = self.command('validate')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['entries'][0]['geometry'][0]['triangles'], 1)
        model.write_bytes(model.read_bytes()[:-1])
        self.assertEqual(self.command('validate')[0], 4)
        model.unlink()
        for suffix in ('.fbx', '.blend', '.gltf'):  # GLB now has a bounded reader: see test_user_largo_import_glb
            path = self.models / ('Alpha-Beta' + suffix)
            path.write_bytes(b'opaque synthetic input')
            before = snapshot(self.models)
            code, report = self.command('validate')
            self.assertEqual(code, 3, report)
            geometry = report['entries'][0]['geometry'][0]
            self.assertEqual(geometry['status'], 'NEEDS_FORMAT_ADAPTER')
            self.assertIsNone(geometry['bounds'])
            self.assertFalse(geometry['geometry_checked'])
            self.assertEqual(snapshot(self.models), before)
            path.unlink()

    def test_manifest_metadata_optional_partial_and_nonfinite(self):
        from bridge.sdk import largo
        package, manifest = self.package()
        before = snapshot(self.models)
        self.assertEqual(largo.validate_package(package, known_base_species={'slime.alpha', 'slime.beta'})['status'], 'VALID')
        code, report = self.command('validate')
        self.assertEqual(code, 0, report)
        self.assertEqual(len(report['entries']), 1)
        self.assertEqual(report['entries'][0]['kind'], 'manifested_package')
        self.assertEqual(report['entries'][0]['metadata_readiness']['missing'], [])
        self.assertEqual(snapshot(self.models), before)
        manifest['import_metadata'] = {'transform': {'scale': [1.0, 1.0, 1.0]}}
        self.write_manifest(package, manifest)
        validated = largo.validate_package(package)
        self.assertEqual(validated['status'], 'VALID', validated)
        self.assertIn('IMPORT_METADATA_INCOMPLETE', {d['code'] for d in validated['diagnostics']})
        code, report = self.command('plan', '--target', 'SR1')
        self.assertEqual(code, 3, report)
        self.assertIn('transform.pivot', report['entries'][0]['metadata_readiness']['missing'])
        manifest['import_metadata']['transform']['scale'] = [float('inf'), 1.0, 1.0]
        self.write_manifest(package, manifest)
        validated = largo.validate_package(package)
        self.assertEqual(validated['status'], 'REJECTED', validated)
        self.assertIn('INVALID_IMPORT_METADATA', {d['code'] for d in validated['diagnostics']})
        manifest.pop('import_metadata')
        self.write_manifest(package, manifest)
        self.assertEqual(largo.validate_package(package)['status'], 'VALID')

    def test_plan_parent_rules_profiles_pending_and_duplicates(self):
        self.package()
        catalog = json.loads(self.catalog.read_text())
        catalog['parent_capabilities'] = {'slime.alpha': ['standard.diet'], 'slime.beta': ['standard.plort_production']}
        catalog['target_context'] = {'SR1': {'native': [], 'parents': ['slime.alpha']},
                                     'SR2': {'native': [], 'parents': ['slime.alpha', 'slime.beta']}}
        self.catalog.write_text(json.dumps(catalog))
        before = snapshot(self.models)
        code, report = self.command('plan', '--target', 'SR1', '--target', 'SR2')
        self.assertEqual(code, 3, report)
        self.assertEqual(report['registration_receipt']['status'], 'PLANNED')
        self.assertFalse(report['registration_receipt']['runtime_proven'])
        plans = report['entries'][0]['plans']
        self.assertEqual([p['target_realm'] for p in plans], ['SR1', 'SR2'])
        self.assertTrue(all(p['final'] == 'PENDING_ADAPTER' and not p['registrable'] for p in plans))
        self.assertIn('PARENT_NOT_IN_TARGET:slime.beta', plans[0]['reasons'])
        self.assertIn('MODEL_LOADER_UNVERIFIED', plans[1]['reasons'])
        self.assertIn('REGISTRATION_ADAPTER_UNVERIFIED', plans[1]['reasons'])
        self.assertEqual({c['id'] for c in plans[1]['behaviour']['capabilities']},
                         {'standard.diet', 'standard.plort_production', 'standard.vacpack'})
        self.assertEqual(snapshot(self.models), before)
        (self.models / 'Beta-Alpha.obj').write_bytes(OBJ)
        code, report = self.command('plan', '--target', 'SR1')
        self.assertEqual(code, 4, report)
        self.assertTrue(all(e['match_status'] == 'DUPLICATE' for e in report['entries']))
        self.assertTrue(all(not e.get('plans') for e in report['entries']))

    def test_install_lab_fail_closed_no_lab_io(self):
        self.package()
        lab = self.base / 'lab'
        lab.mkdir()
        (lab / 'sentinel.txt').write_text('synthetic lab, not a game')
        before = snapshot(self.base)
        code, report = self.command('install', '--target', 'SR1', '--lab', str(lab))
        self.assertEqual(code, 6, report)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('MODEL_LOADER_UNVERIFIED', report['blockers'])
        self.assertIn('REGISTRATION_ADAPTER_UNVERIFIED', report['blockers'])
        self.assertEqual(report['registration_receipt']['status'], 'NOT_EXECUTED')
        self.assertEqual(report['registration_receipt']['executed_operations'], [])
        self.assertFalse(report['registration_receipt']['runtime_proven'])
        self.assertEqual(snapshot(self.base), before)
        self.assertEqual(self.command('install', '--target', 'SR1')[0], 2)


if __name__ == '__main__':
    unittest.main()
