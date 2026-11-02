import pytest
from tools.lab import oracle


@pytest.mark.parametrize('verified', [False, None])
def test_cleanup_rejects_failed_or_unknown_registry_receipt(verified):
    result = {'passed': False, 'saves_match_backup_after_restore': True,
              'registry_restoration_verified': verified,
              'registry_restore': {'SR1': 'restore failed: unknown presence', 'SR2': 'reimported'}}
    oracle.cleanup_steps(result, [])
    assert result['cleanup_completed'] is False
