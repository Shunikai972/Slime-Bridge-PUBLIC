"""Focused synthetic coordinator ownership/recovery regressions; no native runtime evidence."""
import tempfile
import unittest
from pathlib import Path

from bridge.transfer.binding import TransferBindingError
from bridge.transfer.coordinator import refund_after_receiver_loss, resume, transfer
from bridge.transfer.store import TransferStore
from test_coordinator import FakeGames
from transfer_binding_fixtures import PairFixture


class BoundFakeGames(FakeGames):
    def __init__(self, fixture, counts, **kwargs):
        super().__init__(counts, **kwargs)
        self.fixture = fixture

    def resolve_transfer_binding(self, profile_id, sender, sender_save, receiver, receiver_save):
        binding = self.fixture.resolver.resolve(profile_id, sender, sender_save, receiver, receiver_save)
        self._loaded(binding, (sender, receiver))
        return binding

    def _loaded(self, binding, realms):
        for realm in realms:
            if self.fixture.loaded.get(realm) != binding.endpoint(realm):
                raise TransferBindingError('synthetic current endpoint differs from bound pair')

    def validate_transfer(self, row, *, realms=None):
        binding = self.fixture.resolver.validate_row(row)
        self._loaded(binding, (binding.sender_realm, binding.receiver_realm) if realms is None else realms)
        return binding


class TransferBindingCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = PairFixture(self.root)
        self.profile = self.fixture.linked('1')
        self.other = self.fixture.linked('2')
        self.fixture.load(self.profile)
        self.store = TransferStore(self.root / 'ledger.db')
        self.addCleanup(self.store.close)

    def channel(self, **kwargs):
        return BoundFakeGames(self.fixture, {'SR1': {'slime.pink': 3}, 'SR2': {'slime.pink': 3}}, **kwargs)

    def start(self, games, **kwargs):
        return transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink',
                        bridge_profile_id=self.profile, idempotency_key='current-action', **kwargs)

    def test_missing_profile_is_rejected_before_row_or_effect(self):
        games = self.channel()
        with self.assertRaises(ValueError):
            transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', idempotency_key='missing')
        self.assertEqual(self.store.list_in_flight(), [])
        self.assertEqual(games.applied, [])

    def test_ledger_only_channel_cannot_bypass_pair_authority(self):
        class LedgerOnly:
            def bind_transfer_store(self, store): return True
            def enqueue_command(self, *args, **kwargs): raise AssertionError('unsafe command')
        with self.assertRaises(ValueError):
            self.start(LedgerOnly())
        self.assertEqual(self.store.list_in_flight(), [])

    def test_foreign_same_save_receiver_installation_blocks_prepare(self):
        games = self.channel()
        self.fixture.load(self.other, 'SR2')
        with self.assertRaises(ValueError):
            self.start(games)
        self.assertEqual(self.store.list_in_flight(), [])
        self.assertEqual(games.applied, [])

    def test_bidirectional_transfer_and_reopen_retry_preserve_exact_ownership(self):
        for sender, sender_save, receiver, receiver_save in (('SR1', 'a', 'SR2', 'b'), ('SR2', 'b', 'SR1', 'a')):
            with self.subTest(sender=sender):
                games = self.channel()
                key = 'direction-' + sender
                first = transfer(self.store, games, sender, sender_save, receiver, receiver_save, 'slime.pink',
                                 bridge_profile_id=self.profile, idempotency_key=key)
                self.assertEqual(first.status, 'COMMITTED')
                self.assertEqual((games.counts[sender]['slime.pink'], games.counts[receiver]['slime.pink']), (2, 4))
                self.store.close()
                self.store = TransferStore(self.root / 'ledger.db')
                self.addCleanup(self.store.close)
                repeated = transfer(self.store, games, sender, sender_save, receiver, receiver_save, 'slime.pink',
                                    bridge_profile_id=self.profile, idempotency_key=key)
                self.assertEqual(repeated.transfer_id, first.transfer_id)
                self.assertEqual(len(games.applied), 2)

    def test_restart_resume_foreign_sender_preserves_locked_and_original_op(self):
        games = self.channel(drop={':remove'})
        first = self.start(games)
        self.assertEqual(first.status, 'LOCKED')
        effects = list(games.applied)
        self.fixture.load(self.other, 'SR1')
        stopped = resume(self.store, games, first.transfer_id)
        self.assertEqual(stopped.status, 'LOCKED')
        self.assertIsNotNone(stopped.error)
        self.assertEqual(games.applied, effects)
        self.fixture.load(self.profile, 'SR1')
        done = resume(self.store, games, first.transfer_id)
        self.assertEqual(done.status, 'COMMITTED')
        self.assertEqual(sum(op.endswith(':remove') for op in games.applied), 1)

    def test_foreign_receiver_blocks_escrow_resume_without_credit(self):
        games = self.channel(drop={':add'})
        first = self.start(games)
        self.assertEqual(first.status, 'ESCROWED')
        effects = list(games.applied)
        self.fixture.load(self.other, 'SR2')
        stopped = resume(self.store, games, first.transfer_id)
        self.assertEqual(stopped.status, 'ESCROWED')
        self.assertIsNotNone(stopped.error)
        self.assertEqual(games.applied, effects)

    def test_foreign_sender_blocks_pending_refund_and_restore_replays_once(self):
        games = self.channel(refuse_add={'SR2'}, drop={':refund'})
        first = self.start(games)
        self.assertEqual(first.status, 'REFUND_PENDING')
        effects = list(games.applied)
        self.fixture.load(self.other, 'SR1')
        stopped = refund_after_receiver_loss(self.store, games, first.transfer_id, True, 'absent')
        self.assertEqual(stopped.status, 'REFUND_PENDING')
        self.assertIsNotNone(stopped.error)
        self.assertEqual(games.applied, effects)
        self.fixture.load(self.profile, 'SR1')
        done = resume(self.store, games, first.transfer_id)
        self.assertEqual(done.status, 'REFUNDED')
        self.assertEqual(sum(op.endswith(':refund') for op in games.applied), 1)

    def test_authority_change_after_native_result_blocks_ledger_transition(self):
        games = self.channel()
        original = games.wait_result
        def replace_after_remove(op, timeout):
            result = original(op, timeout)
            if op.endswith(':remove'):
                self.fixture.load(self.other, 'SR1')
            return result
        games.wait_result = replace_after_remove
        stopped = self.start(games)
        self.assertEqual(stopped.status, 'LOCKED')
        self.assertIsNotNone(stopped.error)
        self.assertEqual(len(games.applied), 1)


if __name__ == '__main__':
    unittest.main()
