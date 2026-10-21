"""Deployment planner and journaled executor: conflicts, rollback, crash recovery, no partial success."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from bridge.deploy.executor import DeployError, DeployExecutor, DeployJournal, DirectoryFS, MemoryFS
from bridge.deploy.planner import plan_deploy
from bridge.deploy.units import make_unit, unit_from_generated_package, validate_unit
from bridge.packages import package as P
from bridge.sim.faults import Fault, FaultPlan
from portability import directory_link


def blob(text):
    data = text.encode()
    return data, hashlib.sha256(data).hexdigest()


ARTIFACTS = {}


def unit(name, version=(1, 0, 0), *, files=None, realm='SR1', **kw):
    files = files if files is not None else {f'{name}/mesh.bin': f'{name}-mesh-{version}',
                                             f'{name}/tex.png': f'{name}-tex-{version}'}
    entries = []
    for path, text in files.items():
        data, digest = blob(text)
        ARTIFACTS[digest] = data
        entries.append({'path': path, 'sha256': digest})
    kind = kw.pop('kind', 'GENERATED')
    return make_unit(key=f'{kind.lower()}:{name}:{realm}', kind=kind, realm=realm, version=version, files=entries,
                     provides=kw.pop('provides', [f'slime.{name}']), **kw)


def codes(plan):
    return {d['code'] for d in plan['blockers']}


class Deployment:
    def __init__(self, tmp, fs=None, faults=()):
        self.journal = DeployJournal(Path(tmp) / 'deploy.sqlite3')
        self.fs = fs or MemoryFS()
        self.faults = FaultPlan(faults)
        self.executor = DeployExecutor(self.journal, self.fs, ARTIFACTS.get, faults=self.faults)

    def plan(self, desired, **kw):
        return plan_deploy(self.journal.inventory('SR1'), desired, history=self.journal.history(), **kw)

    def apply(self, desired, **kw):
        plan = self.plan(desired, **kw)
        assert plan['executable'], plan['blockers']
        return self.executor.apply(plan)


class UnitTests(unittest.TestCase):
    def test_unit_validation(self):
        good = unit('pink')
        self.assertEqual(validate_unit(good), [])
        bad_path = unit('pink', files={'../escape.bin': 'x'})
        self.assertIn('UNIT_PATH_UNSAFE', {d['code'] for d in validate_unit(bad_path)})
        collide = unit('pink', files={'pink/A.png': 'a', 'pink/a.png': 'b'})
        self.assertIn('UNIT_PATH_COLLISION', {d['code'] for d in validate_unit(collide)})
        tampered = dict(good, version=[9, 9, 9])
        self.assertIn('UNIT_ID_MISMATCH', {d['code'] for d in validate_unit(tampered)})
        wrong_key = dict(good, key='mod:pink:SR1')
        self.assertTrue(validate_unit(wrong_key))
        self.assertEqual(validate_unit({'schema': 'x'})[0]['code'], 'UNIT_SCHEMA_INVALID')

    def test_generated_package_becomes_a_unit(self):
        from services.test_packages import package_for, receipts_for
        pkg = package_for('slime.azure')
        pkg = P.materialize(pkg, receipts_for(pkg))
        u = unit_from_generated_package(pkg)
        self.assertEqual(validate_unit(u), [])
        self.assertEqual((u['key'], u['realm']), ('generated:slime.azure:SR1', 'SR1'))
        self.assertTrue(all(f['path'].startswith('generated/slime.azure/') for f in u['files']))
        self.assertEqual(u['source_ref'], pkg['package_id'])


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Deployment(self._tmp.name)

    def tearDown(self):
        self.d.journal.close()
        self._tmp.cleanup()

    def test_install_update_and_downgrade_rules(self):
        v1, v2 = unit('pink'), unit('pink', (1, 1, 0))
        self.assertEqual(self.d.apply([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}])['state'], 'COMMITTED')
        update = self.d.plan([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])
        self.assertEqual([op['op'] for op in update['operations']], ['UPDATE'])
        self.d.executor.apply(update)
        down = self.d.plan([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}])
        self.assertIn('DOWNGRADE_REFUSED', codes(down))
        rollback = self.d.plan([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1, 'rollback': True}])
        self.assertEqual([op['op'] for op in rollback['operations']], ['ROLLBACK'])
        self.assertEqual(self.d.executor.apply(rollback)['state'], 'COMMITTED')
        self.assertEqual(self.d.fs.read('live/pink/mesh.bin'), b'pink-mesh-(1, 0, 0)')
        never = unit('pink', (0, 1, 0))
        self.assertIn('ROLLBACK_TARGET_UNKNOWN',
                      codes(self.d.plan([{'key': never['key'], 'state': 'ENABLED', 'unit': never, 'rollback': True}])))

    def test_dependencies_order_and_orphan_protection(self):
        base = unit('base')
        child = unit('child', requires=[base['key']])
        missing = self.d.plan([{'key': child['key'], 'state': 'ENABLED', 'unit': child}])
        self.assertIn('DEPENDENCY_MISSING', codes(missing))
        plan = self.d.plan([{'key': child['key'], 'state': 'ENABLED', 'unit': child},
                            {'key': base['key'], 'state': 'ENABLED', 'unit': base}])
        self.assertEqual([op['key'] for op in plan['operations']], [base['key'], child['key']])
        self.d.executor.apply(plan)
        orphan = self.d.plan([{'key': base['key'], 'state': 'REMOVED'}])
        self.assertIn('DEPENDENCY_MISSING', codes(orphan))
        both = self.d.plan([{'key': base['key'], 'state': 'REMOVED'}, {'key': child['key'], 'state': 'REMOVED'}])
        self.assertEqual([op['key'] for op in both['operations']], [child['key'], base['key']])

    def test_conflicts_block_the_whole_plan(self):
        a = unit('a', provides=['slime.same'])
        b = unit('b', provides=['slime.same'])
        self.assertIn('PROVIDES_CONFLICT', codes(self.d.plan([{'key': a['key'], 'state': 'ENABLED', 'unit': a},
                                                               {'key': b['key'], 'state': 'ENABLED', 'unit': b}])))
        c = unit('c', files={'shared/File.bin': 'c'})
        e = unit('e', files={'shared/file.bin': 'e'})
        self.assertIn('FILE_CONFLICT', codes(self.d.plan([{'key': c['key'], 'state': 'ENABLED', 'unit': c},
                                                           {'key': e['key'], 'state': 'ENABLED', 'unit': e}])))
        f = unit('f', conflicts=['generated:g:SR1'])
        g = unit('g')
        plan = self.d.plan([{'key': f['key'], 'state': 'ENABLED', 'unit': f}, {'key': g['key'], 'state': 'ENABLED', 'unit': g}])
        self.assertIn('DECLARED_CONFLICT', codes(plan))
        self.assertFalse(plan['executable'])
        with self.assertRaises(DeployError):
            self.d.executor.apply(plan)

    def test_status_realm_and_key_checks(self):
        pending = unit('p', status='PENDING_ADAPTER')
        self.assertIn('UNIT_NOT_DEPLOYABLE', codes(self.d.plan([{'key': pending['key'], 'state': 'ENABLED',
                                                                 'unit': pending}])))
        self.assertTrue(self.d.plan([{'key': pending['key'], 'state': 'DISABLED', 'unit': pending}])['executable'])
        sr2 = unit('q', realm='SR2')
        self.assertIn('UNIT_WRONG_REALM', codes(self.d.plan([{'key': sr2['key'], 'state': 'ENABLED', 'unit': sr2}])))
        u = unit('r')
        self.assertIn('UNIT_KEY_MISMATCH', codes(self.d.plan([{'key': 'generated:other:SR1', 'state': 'ENABLED',
                                                               'unit': u}])))
        self.assertIn('DESIRED_INVALID', codes(self.d.plan([{'key': u['key'], 'state': 'DELETE_EVERYTHING'}])))
        warn = self.d.plan([{'key': u['key'], 'state': 'ENABLED', 'unit': u}], build='b9')
        self.assertIn('UNIT_REVERIFY_REQUIRED', {d['code'] for d in warn['diagnostics']})

    def test_disable_enable_and_quarantine_move_files_never_delete(self):
        u = unit('pink')
        self.d.apply([{'key': u['key'], 'state': 'ENABLED', 'unit': u}])
        self.d.apply([{'key': u['key'], 'state': 'DISABLED'}])
        self.assertIsNone(self.d.fs.read('live/pink/mesh.bin'))
        self.assertIsNotNone(self.d.fs.read('disabled/pink/mesh.bin'))
        self.d.apply([{'key': u['key'], 'state': 'ENABLED'}])
        self.d.apply([{'key': u['key'], 'state': 'QUARANTINED', 'reasons': ['VERIFICATION_FAILED']}])
        self.assertIsNotNone(self.d.fs.read('quarantine/pink/mesh.bin'))
        self.assertEqual(self.d.journal.inventory('SR1')['units'][u['key']]['state'], 'QUARANTINED')
        self.assertIn('QUARANTINED_NEEDS_NEW_UNIT', codes(self.d.plan([{'key': u['key'], 'state': 'ENABLED'}])))

    def test_stale_plan_is_refused(self):
        a, b = unit('a'), unit('b')
        plan_a = self.d.plan([{'key': a['key'], 'state': 'ENABLED', 'unit': a}])
        self.d.apply([{'key': b['key'], 'state': 'ENABLED', 'unit': b}])
        with self.assertRaises(DeployError) as ctx:
            self.d.executor.apply(plan_a)
        self.assertEqual(ctx.exception.code, 'STALE_PLAN')


class ExecutorFailureTests(unittest.TestCase):
    def deployment(self, faults=()):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Deployment(self._tmp.name, faults=faults)
        self.addCleanup(d.journal.close)
        return d

    def seeded(self, faults=()):
        d = self.deployment(faults)
        v1 = unit('pink')
        self.assertEqual(d.apply([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}])['state'], 'COMMITTED')
        return d, v1, unit('pink', (2, 0, 0), files={'pink/mesh.bin': 'v2-mesh', 'pink/new.bin': 'v2-new'})

    def live(self, d):
        return {k: v for k, v in d.fs.files.items() if not k.startswith('.bridge/')}

    def test_commit_leaves_no_staging_or_backup_files(self):
        d, v1, v2 = self.seeded()
        self.assertEqual(d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])['state'], 'COMMITTED')
        self.assertEqual([k for k in d.fs.files if k.startswith('.bridge/')], [])

    def test_write_failure_rolls_back_to_the_exact_previous_state(self):
        d, v1, v2 = self.seeded()
        before, inventory = self.live(d), d.journal.inventory('SR1')
        d.executor.faults = FaultPlan([Fault('deploy.write', on_hit=1)])
        result = d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])
        self.assertEqual(result['state'], 'ROLLED_BACK')
        self.assertEqual(self.live(d), before)
        self.assertEqual(d.journal.inventory('SR1'), inventory)

    def test_crash_mid_apply_is_rolled_back_on_recovery(self):
        for point in ('deploy.crash_after_step', 'deploy.crash_before_write'):
            for hit in (1, 2, 3):
                with self.subTest(point=point, hit=hit):
                    d, v1, v2 = self.seeded()
                    before = self.live(d)
                    d.executor.faults = FaultPlan([Fault(point, on_hit=hit)])
                    self.assertEqual(d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])['state'], 'APPLYING')
                    restarted = DeployExecutor(d.journal, d.fs, ARTIFACTS.get)
                    with self.assertRaises(DeployError):
                        restarted.apply(d.plan([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}]))
                    self.assertEqual(set(restarted.recover().values()), {'ROLLED_BACK'})
                    self.assertEqual(self.live(d), before)
                    self.assertEqual(restarted.apply(d.plan([{'key': v2['key'], 'state': 'ENABLED',
                                                              'unit': v2}]))['state'], 'COMMITTED')

    def test_failed_rollback_reports_failed_with_exact_paths(self):
        d, v1, v2 = self.seeded()
        d.executor.faults = FaultPlan([Fault('deploy.postcondition'), Fault('deploy.rollback_write')])
        result = d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])
        self.assertEqual(result['state'], 'FAILED')
        self.assertIn('unrestored', d.journal.txn(result['txn_id'])['detail'])

    def test_drift_untracked_and_corrupt_artifacts_refuse_before_any_change(self):
        d, v1, v2 = self.seeded()
        d.fs.write('live/pink/mesh.bin', b'edited by hand')
        before = self.live(d)
        result = d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])
        self.assertEqual((result['state'], result['diagnostics'][0]['code']), ('ROLLED_BACK', 'TARGET_DRIFT'))
        self.assertEqual(self.live(d), before)
        d2 = self.deployment()
        other = unit('blue')
        d2.fs.write('live/blue/mesh.bin', b'not ours')
        result = d2.apply([{'key': other['key'], 'state': 'ENABLED', 'unit': other}])
        self.assertEqual(result['diagnostics'][0]['code'], 'UNTRACKED_FILE')
        self.assertEqual(d2.fs.read('live/blue/mesh.bin'), b'not ours')
        d3 = self.deployment()
        bad = unit('green')
        d3.executor.artifacts = lambda digest: b'tampered'
        result = d3.apply([{'key': bad['key'], 'state': 'ENABLED', 'unit': bad}])
        self.assertEqual(result['diagnostics'][0]['code'], 'ARTIFACT_UNAVAILABLE')
        self.assertEqual(self.live(d3), {})

    def test_directory_fs_is_confined(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = DirectoryFS(Path(tmp) / 'content')
            fs.write('live/a/b.bin', b'x')
            self.assertEqual(fs.read('live/a/b.bin'), b'x')
            for rel in ('../escape', '/abs/path', 'live/../../x', 'C:/x', 'live\\x', '.bridge/../x'):
                with self.subTest(rel=rel), self.assertRaises(OSError):
                    fs.write(rel, b'x')
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            sentinel = outside / 'file.bin'
            sentinel.write_bytes(b'outside must stay unchanged')
            with directory_link(Path(tmp) / 'content' / 'link', outside):
                with self.assertRaises(OSError):
                    fs.write('link/file.bin', b'x')
                with self.assertRaises(OSError):
                    fs.read('link/file.bin')
                with self.assertRaises(OSError):
                    fs.delete('link/file.bin')
                self.assertIsNotNone(fs.check('link/file.bin'))
                self.assertEqual(sentinel.read_bytes(), b'outside must stay unchanged')
                self.assertEqual(list(outside.iterdir()), [sentinel])



class _ValueErrorOnRollback(MemoryFS):
    """A target that starts refusing writes (as atomic_write does for a hardlinked file) mid-transaction."""

    def __init__(self):
        super().__init__()
        self.refuse = set()

    def write(self, path, data):
        if path in self.refuse:
            raise ValueError(f'Refusing to replace a hardlinked file: {path}')
        super().write(path, data)


class RollbackRobustnessTests(unittest.TestCase):
    """F1: a non-OSError during rollback ends in FAILED with exact paths; recover() never loops."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_value_error_during_rollback_fails_cleanly_and_recovery_terminates(self):
        d = Deployment(self._tmp.name, fs=_ValueErrorOnRollback())
        self.addCleanup(d.journal.close)
        v1 = unit('teal')
        self.assertEqual(d.apply([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}])['state'], 'COMMITTED')
        v2 = unit('teal', (2, 0, 0))
        d.executor.faults = FaultPlan([Fault('deploy.crash_after_step', on_hit=2)])
        self.assertEqual(d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])['state'], 'APPLYING')
        d.fs.refuse.add('live/teal/mesh.bin')   # e.g. someone hardlinked the file while the launcher was down
        restarted = DeployExecutor(d.journal, d.fs, ARTIFACTS.get)
        outcome = restarted.recover()
        self.assertEqual(list(outcome.values()), ['FAILED'])
        txn_id = next(iter(outcome))
        self.assertIn('live/teal/mesh.bin', d.journal.txn(txn_id)['detail'])
        self.assertEqual(restarted.recover(), {})   # FAILED is terminal: no endless recovery
        self.assertEqual(d.journal.open_txns(), [])

    def test_resumed_rollback_skips_paths_already_in_their_before_state(self):
        d = Deployment(self._tmp.name)
        self.addCleanup(d.journal.close)
        v1 = unit('plum')
        d.apply([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}])
        before = {k: v for k, v in d.fs.files.items() if not k.startswith('.bridge/')}
        v2 = unit('plum', (2, 0, 0))
        d.executor.faults = FaultPlan([Fault('deploy.crash_before_write', on_hit=1)])
        result = d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])
        for step in d.journal.steps(result['txn_id']):   # the backup vanished, but nothing was written yet
            if step['backup']:
                d.fs.delete(step['backup'])
        self.assertEqual(set(DeployExecutor(d.journal, d.fs, ARTIFACTS.get).recover().values()), {'ROLLED_BACK'})
        self.assertEqual({k: v for k, v in d.fs.files.items() if not k.startswith('.bridge/')}, before)

    def test_hardlinked_target_is_refused_before_any_change(self):
        root = Path(self._tmp.name) / 'content'
        d = Deployment(self._tmp.name, fs=DirectoryFS(root))
        self.addCleanup(d.journal.close)
        v1 = unit('jade')
        self.assertEqual(d.apply([{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}])['state'], 'COMMITTED')
        live = root / 'live' / 'jade' / 'mesh.bin'
        original = live.read_bytes()
        twin = Path(self._tmp.name) / 'twin.bin'
        try:
            twin.hardlink_to(live)
        except OSError:
            self.skipTest('hardlinks unavailable on this filesystem')
        v2 = unit('jade', (2, 0, 0))
        result = d.apply([{'key': v2['key'], 'state': 'ENABLED', 'unit': v2}])
        self.assertEqual((result['state'], result['diagnostics'][0]['code']), ('ROLLED_BACK', 'TARGET_UNWRITABLE'))
        self.assertEqual((live.read_bytes(), twin.read_bytes()), (original, original))
        self.assertEqual(d.journal.steps(result['txn_id']), [])
        self.assertEqual(DeployExecutor(d.journal, d.fs, ARTIFACTS.get).recover(), {})


if __name__ == '__main__':
    unittest.main()
