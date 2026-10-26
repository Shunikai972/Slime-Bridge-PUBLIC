"""Demo-owned simulator databases must close before temporary trees are removed."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bridge.app.demo import DemoWorld


class DemoResourceTests(unittest.TestCase):
    def test_close_releases_active_and_completed_transition_databases(self):
        world = DemoWorld()
        self.addCleanup(world.close)
        active = world.transition_simulation()
        completed = world.transition_simulation('sr2_to_sr1')
        # Keep both simulators alive; GC must not be what releases their handles.
        self.addCleanup(active.close)
        self.addCleanup(completed.close)
        completed.run()
        database = active.root / 'transitions.sqlite3'
        self.assertTrue(database.is_file())
        if os.name == 'nt':
            # Exercise the same Windows file-sharing rule behind the reported WinError 32.
            with self.assertRaises(PermissionError) as locked:
                database.unlink()
            self.assertEqual(locked.exception.winerror, 32)
        world.close()
        self.assertFalse(world.root.exists())
        for simulation in (active, completed):
            with self.subTest(simulation=simulation.root.name):
                with self.assertRaises(sqlite3.ProgrammingError):
                    simulation.current()
        world.close()  # repeated shutdown remains safe

    def test_close_preserves_caller_root_but_releases_transition_handles(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        sentinel = root / 'caller-owned.txt'
        sentinel.write_text('keep')
        world = DemoWorld(root)
        self.addCleanup(world.close)
        simulation = world.transition_simulation()
        self.addCleanup(simulation.close)
        database = simulation.root / 'transitions.sqlite3'
        world.close()
        self.assertEqual(sentinel.read_text(), 'keep')
        with self.assertRaises(sqlite3.ProgrammingError):
            simulation.current()
        # Windows refuses this rename while SQLite still owns the database handle.
        moved = database.with_suffix('.closed')
        database.rename(moved)
        self.assertTrue(moved.is_file())


if __name__ == '__main__':
    unittest.main()
