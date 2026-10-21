"""UserLargo external import: read-only, hash-verified, versioned, planned. No Largo authoring."""
import hashlib
import json
import unittest
from contextlib import ExitStack

from bridge.conversion.targets import builtin_target_profile
from bridge.sdk import largo
from conversion import world
from conversion.test_cache import scratch
from portability import directory_link, file_symlink

FILES = {'mesh/body.bin': b'user mesh bytes (opaque to Bridge)', 'tex/body.bin': b'user texture bytes',
         'tex/face_neutral.bin': b'user face bytes', 'anim/idle.bin': b'user clip bytes'}
KINDS = {'mesh/body.bin': 'mesh', 'tex/body.bin': 'texture', 'tex/face_neutral.bin': 'texture',
         'anim/idle.bin': 'animation_clip'}


def write_package(root, *, parents=('slime.alpha', 'slime.beta'), version='1.0.0', files=FILES, edit=None):
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        'schema': 'bridge-user-largo-package', 'schema_version': 1,
        'canonical_largo_id': 'largo.' + '.'.join(sorted(p.split('.', 1)[1] for p in parents)),
        'parents': list(parents), 'version': version,
        'assets': [{'path': rel, 'kind': KINDS[rel], 'sha256': hashlib.sha256(data).hexdigest(), 'role': 'body'}
                   for rel, data in sorted(files.items())],
        'materials': [{'id': 'body', 'family': 'slime_body',
                       'render_state': {'alpha_mode': 'BLEND', 'cull': 'BACK', 'zwrite': False},
                       'channels': {'albedo.texture': {'asset': 'tex/body.bin'},
                                    'gradient.top': {'value': [1, 0.5, 0.5, 1]}}}],
        'renderers': [{'id': 'body', 'mesh': 'mesh/body.bin', 'materials': ['body'], 'skinned': False}],
        'face': {'expressions': {'neutral': 'tex/face_neutral.bin'}}, 'animations': ['anim/idle.bin'],
        'behaviour': {'compose': 'union', 'capabilities': [], 'exclude': ['hazard.aura']},
        'targets': {'SR1': {}, 'SR2': {}}, 'provenance': {'author': 'user', 'license': 'user-owned'}}
    if edit:
        edit(manifest)
    (root / largo.PACKAGE_FILE).write_text(json.dumps(manifest))
    return root


def snapshot(root):
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob('*')) if p.is_file()}


def codes(result):
    return {d['code'] for d in result['diagnostics']}


class UserLargoTests(unittest.TestCase):
    def test_valid_package_is_validated_read_only(self):
        with scratch() as root:
            package = write_package(root / 'pkg')
            before = snapshot(package)
            result = largo.validate_package(package, known_base_species={'slime.alpha', 'slime.beta'})
            self.assertEqual(result['status'], 'VALID', result['diagnostics'])
            self.assertEqual(len(result['files']), 4)
            self.assertEqual(snapshot(package), before)
            again = largo.validate_package(package, known_base_species={'slime.alpha', 'slime.beta'})
            self.assertEqual(again['package_hash'], result['package_hash'])
            self.assertIn('PARENT_ELIGIBILITY_UNVERIFIED', codes(result))

    def test_tampering_and_unsafe_paths_are_rejected(self):
        with scratch() as root:
            package = write_package(root / 'pkg')
            (package / 'tex/body.bin').write_bytes(b'changed after manifest')
            self.assertIn('ASSET_HASH_MISMATCH', codes(largo.validate_package(package)))
            for bad in ('../outside.bin', '/etc/passwd', 'tex\\body.bin', 'C:/x', 'tex/./body.bin',
                        'tex//body.bin', 'tex/body.bin/'):
                pkg = write_package(root / f'bad{abs(hash(bad))}',
                                    edit=lambda m, b=bad: m['assets'].append({'path': b, 'kind': 'texture',
                                                                              'sha256': '0' * 64}))
                result = largo.validate_package(pkg)
                self.assertEqual(result['status'], 'REJECTED')
                self.assertIn('UNSAFE_PATH', codes(result))

    def test_directory_link_asset_is_rejected(self):
        with scratch() as root:
            package = write_package(root / 'pkg')
            outside = root / 'outside-textures'
            (package / 'tex').rename(outside)
            before = snapshot(outside)
            with directory_link(package / 'tex', outside):
                self.assertIn('UNSAFE_PATH', codes(largo.validate_package(package)))
                self.assertEqual(snapshot(outside), before)

    def test_file_symlink_asset_is_rejected(self):
        with scratch() as root:
            linked = write_package(root / 'linked')
            (root / 'secret.bin').write_bytes(b'not part of the package')
            (linked / 'tex/body.bin').unlink()
            file_symlink(linked / 'tex/body.bin', root / 'secret.bin')
            self.assertIn('UNSAFE_PATH', codes(largo.validate_package(linked)))

    def test_file_symlink_manifest_is_rejected(self):
        with scratch() as root:
            package = write_package(root / 'pkg')
            manifest_link = root / 'mlink'
            manifest_link.mkdir()
            file_symlink(manifest_link / largo.PACKAGE_FILE, package / largo.PACKAGE_FILE)
            self.assertIn('MANIFEST_UNREADABLE', codes(largo.validate_package(manifest_link)))

    def test_identity_and_reference_rules(self):
        with scratch() as root:
            swapped = write_package(root / 'a', edit=lambda m: m.update(canonical_largo_id='largo.beta.alpha'))
            self.assertIn('LARGO_ID_MISMATCH', codes(largo.validate_package(swapped)))
            unknown = write_package(root / 'b', parents=('slime.alpha', 'slime.gamma'))
            self.assertIn('PARENT_UNKNOWN', codes(largo.validate_package(unknown, known_base_species={'slime.alpha'})))
            dangling = write_package(root / 'c', edit=lambda m: m['renderers'][0].update(materials=['missing']))
            self.assertIn('DANGLING_MATERIAL', codes(largo.validate_package(dangling)))
            semantic = write_package(root / 'd', edit=lambda m: m['materials'][0]['channels'].update(
                {'albedo.sparkle': {'value': 1}}))
            self.assertIn('UNKNOWN_SEMANTIC', codes(largo.validate_package(semantic)))
            extra = write_package(root / 'e', edit=lambda m: m.update(model_edits=True))
            self.assertIn('UNKNOWN_FIELD', codes(largo.validate_package(extra)))
            undeclared = write_package(root / 'f')
            (undeclared / 'notes.txt').write_text('user notes')
            result = largo.validate_package(undeclared)
            self.assertEqual(result['status'], 'VALID')
            self.assertIn('UNDECLARED_FILE', codes(result))

    def test_versioning(self):
        with scratch() as root:
            first = largo.validate_package(write_package(root / 'v1'))
            self.assertEqual(largo.compare_version(None, first)['decision'], 'NEW')
            entry = {'version': '1.0.0', 'package_hash': first['package_hash']}
            self.assertEqual(largo.compare_version(entry, first)['decision'], 'UNCHANGED')
            changed = dict(FILES, **{'tex/body.bin': b'retextured by the user'})
            same_version = largo.validate_package(write_package(root / 'v1b', files=changed))
            decision = largo.compare_version(entry, same_version)
            self.assertEqual(decision['decision'], 'REFUSED')
            self.assertEqual(decision['diagnostics'][0]['code'], 'VERSION_NOT_BUMPED')
            bumped = largo.validate_package(write_package(root / 'v2', files=changed, version='1.1.0'))
            self.assertEqual(largo.compare_version(entry, bumped)['decision'], 'UPGRADE')
            older = largo.validate_package(write_package(root / 'v0', version='0.9.0'))
            self.assertEqual(largo.compare_version(entry, older)['diagnostics'][0]['code'], 'VERSION_REGRESSION')

    def test_discovery_import_and_planning(self):
        with scratch() as root, ExitStack() as links:
            library = root / 'library'
            write_package(library / 'user' / 'alpha_beta')
            write_package(library / 'other' / 'alpha_gamma', parents=('slime.alpha', 'slime.gamma'))
            links.enter_context(directory_link(library / 'linked', library / 'user'))
            self.assertEqual([p.name for p in largo.discover_packages(library)], ['alpha_gamma', 'alpha_beta'])
            before = {p: snapshot(p) for p in largo.discover_packages(library)}
            parents = {'slime.alpha': ['standard.vacpack', 'standard.diet', 'hazard.aura'],
                       'slime.beta': ['standard.vacpack', 'movement.flight'], 'slime.gamma': []}
            context = {'SR1': {'native': set(), 'parents': {'slime.alpha', 'slime.beta'}},
                       'SR2': {'native': set(), 'parents': {'slime.alpha', 'slime.beta', 'slime.gamma'}}}
            out = largo.import_packages(library, ledger={}, known_base_species=set(parents),
                                        target_profiles=[world.target_profile('SR1'), builtin_target_profile('SR2')],
                                        parent_capabilities=parents, target_context=context)
            self.assertEqual({p: snapshot(p) for p in largo.discover_packages(library)}, before)
            records = {r['canonical_largo_id']: r for r in out['records']}
            self.assertEqual({cid: r['decision'] for cid, r in records.items()},
                             {'largo.alpha.beta': 'NEW', 'largo.alpha.gamma': 'NEW'})
            self.assertEqual(out['ledger']['largo.alpha.beta']['version'], '1.0.0')
            gamma_sr1 = records['largo.alpha.gamma']['plans'][0]
            self.assertEqual(gamma_sr1['final'], 'PENDING_ADAPTER')
            self.assertIn('PARENT_NOT_IN_TARGET:slime.gamma', gamma_sr1['reasons'])
            sr1_plan, sr2_plan = records['largo.alpha.beta']['plans']
            caps = [c['id'] for c in sr1_plan['behaviour']['capabilities']]
            self.assertEqual(caps, ['movement.flight', 'standard.diet', 'standard.vacpack'])
            self.assertEqual(sr1_plan['final'], 'EXPERIMENTAL')
            self.assertTrue(all(a['read_only_source'] for a in sr1_plan['artifacts']))
            self.assertEqual(sr1_plan['registration'][0]['role'], 'largo')
            self.assertEqual(sr2_plan['final'], 'PENDING_ADAPTER')  # builtin target: no verified templates
            self.assertEqual(sr2_plan['registration'][0]['local_query'], 'Q008')


class TargetContextTests(unittest.TestCase):
    def test_parents_available_in_target_include_natives_and_converted(self):
        from conversion.test_batch import run
        out = run()
        context = largo.target_context(out['classification'], 'SR1', out['report'])
        self.assertIn('slime.pink', context['parents'])        # shared native
        self.assertIn('slime.amber', context['parents'])       # SR1 native
        self.assertIn('slime.azure', context['parents'])       # converted, EXPERIMENTAL
        self.assertNotIn('slime.hollow', context['parents'])   # quarantined conversion
        self.assertIn('plort.pink', context['native'])
        self.assertNotIn('slime.azure', largo.target_context(out['classification'], 'SR1')['parents'])


class UserLargoReviewRegressions(unittest.TestCase):
    def test_malformed_values_reject_instead_of_crashing(self):
        edits = [lambda m: m['materials'][0].pop('channels'),
                 lambda m: m['renderers'][0].update(mesh=['mesh/body.bin']),
                 lambda m: m['renderers'][0].update(materials=5),
                 lambda m: m['behaviour'].update(capabilities=1),
                 lambda m: m.update(face={'expressions': ['x']}),
                 lambda m: m.update(animations=[['nested']])]
        with scratch() as root:
            for index, edit in enumerate(edits):
                result = largo.validate_package(write_package(root / f'bad{index}', edit=edit))
                self.assertEqual(result['status'], 'REJECTED', index)
            write_package(root / 'zz_good', parents=('slime.alpha', 'slime.delta'))
            out = largo.import_packages(root, ledger={}, target_profiles=[world.target_profile('SR1')],
                                        target_context={'SR1': {'native': set(), 'parents': {'slime.alpha',
                                                                                               'slime.delta'}}})
            good = [r for r in out['records'] if r['canonical_largo_id'] == 'largo.alpha.delta']
            self.assertEqual(good[0]['decision'], 'NEW')
            self.assertIn('PARENTS_UNCHECKED', codes(good[0]))

    def test_target_native_largo_and_unknown_parent_availability(self):
        with scratch() as root:
            validated = largo.validate_package(write_package(root / 'p'))
            native = largo.plan_import(validated, world.target_profile('SR1'), {},
                                       target_native={'largo.alpha.beta'}, target_parents={'slime.alpha', 'slime.beta'})
            self.assertEqual(native['final'], 'REFUSED')
            self.assertIn('WOULD_DUPLICATE_NATIVE', {d['code'] for d in native['diagnostics']})
            unknown = largo.plan_import(validated, world.target_profile('SR1'), {})
            self.assertEqual(unknown['final'], 'PENDING_ADAPTER')
            self.assertIn('PARENT_AVAILABILITY_UNKNOWN', unknown['reasons'])

    def test_duplicate_largo_packages_are_all_refused(self):
        with scratch() as root:
            write_package(root / 'a_old')
            write_package(root / 'b_new', version='2.0.0')
            out = largo.import_packages(root, ledger={'largo.alpha.beta': {'version': '0.1.0', 'package_hash': 'x'}},
                                        target_profiles=[world.target_profile('SR1')])
            self.assertEqual([r['decision'] for r in out['records']], ['REFUSED', 'REFUSED'])
            self.assertTrue(all('DUPLICATE_LARGO_PACKAGE' in codes(r) for r in out['records']))
            self.assertEqual(out['ledger'], {'largo.alpha.beta': {'version': '0.1.0', 'package_hash': 'x'}})


if __name__ == '__main__':
    unittest.main()
