"""Synthetic Ringtail authorization/ledger tests; never native gameplay evidence."""
from __future__ import annotations

from pathlib import Path

import pytest

from bridge.transfer.store import TransferStore
from transfer_binding_fixtures import synthetic_binding
from transfer_host_fixture import SyntheticTransferHost


ITEM = 'slime.ringtail'


def test_ringtail_ledger_preserves_identity_and_idempotency_after_reopen(tmp_path: Path):
    path = tmp_path / 'transfers.db'
    binding = synthetic_binding('SR2', 'b', 'SR1', 'a')
    with TransferStore(path) as store:
        transfer_id = store.prepare_transfer('SR2', 'b', 'SR1', 'a', ITEM, 1,
                                             'ringtail-action', binding=binding)
        assert store.get(transfer_id).canonical_item_id == ITEM
    with TransferStore(path) as store:
        assert store.prepare_transfer('SR2', 'b', 'SR1', 'a', ITEM, 1,
                                      'ringtail-action', binding=binding) == transfer_id
        with pytest.raises(ValueError):
            store.prepare_transfer('SR2', 'b', 'SR1', 'a', 'slime.pink', 1,
                                   'ringtail-action', binding=binding)


def test_ringtail_uses_the_existing_bound_native_receipt_authority(tmp_path: Path):
    store = TransferStore(tmp_path / 'transfers.db')
    with SyntheticTransferHost(tmp_path, store) as fixture:
        transfer_id = fixture.prepare(item=ITEM, sender='SR2', sender_save='b',
                                      receiver='SR1', receiver_save='a')
        row = store.get(transfer_id)
        assert row.canonical_item_id == ITEM
        with pytest.raises(ValueError):
            fixture.enqueue(transfer_id, 'remove', 'SR2', 'remove', ITEM, 1, 'b')
        fixture.query_pair(transfer_id, item=ITEM, sender='SR2', sender_save='b',
                           receiver='SR1', receiver_save='a')
        fixture.action('lock', transfer_id)
        with pytest.raises(ValueError):
            fixture.enqueue(transfer_id, 'remove', 'SR2', 'remove', ITEM, 1, 'wrong-save')
        with pytest.raises(ValueError):
            fixture.enqueue(transfer_id, 'remove', 'SR2', 'remove', 'slime.pink', 1, 'b')
        fixture.remove_receipt(transfer_id, item=ITEM, sender='SR2', sender_save='b')
        fixture.action('escrow', transfer_id)
        fixture.add_receipt(transfer_id, item=ITEM, receiver='SR1', receiver_save='a')
        fixture.action('commit', transfer_id)
        assert store.get(transfer_id).status == 'COMMITTED'
        assert fixture.prepare(item=ITEM, sender='SR2', sender_save='b',
                               receiver='SR1', receiver_save='a') == transfer_id
        assert fixture.poll('SR1') is None
        assert fixture.poll('SR2') is None


@pytest.mark.parametrize('item', ['slime.rad', 'slime.ringtail.extra', 'plort.ringtail', 'SLIME.RINGTAIL'])
def test_unimplemented_identities_remain_refused(tmp_path: Path, item: str):
    with TransferStore(tmp_path / 'transfers.db') as store:
        with pytest.raises(ValueError):
            store.prepare_transfer('SR2', 'b', 'SR1', 'a', item, 1,
                                   'unsupported-action', binding=synthetic_binding('SR2', 'b', 'SR1', 'a'))
