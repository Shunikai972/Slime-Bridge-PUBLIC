"""Only synthetic credentials; exported remote URLs must not retain URL-embedded secrets."""
import json

import pytest

from bridge.app import recovery
from bridge.core import diagnostics


@pytest.mark.parametrize('url',[
    'https://fixture-user:fixture-password@example.invalid/path?api_key=fixture-key',
    'https://example.invalid/path?%61pi_key=fixture-key&safe=yes',
    'https://example.invalid/path?ACCESS_TOKEN=fixture-key&access_token=fixture-password',
    'https://example.invalid/path#access_token=fixture-key',
    'https://fixture-user:fixture-password@[::1]/path',
])
def test_url_credentials_are_removed_without_mutating_the_input(url):
    original={'details':{'endpoint':url}}
    result=recovery.redact(original)
    exported=json.dumps(result)
    assert 'fixture-password' not in exported
    assert 'fixture-key' not in exported
    assert 'fixture-user' not in exported
    assert original['details']['endpoint']==url


def test_safe_remote_url_and_query_are_preserved():
    url='https://example.invalid/path?user=synthetic&limit=3'
    assert recovery.redact(url)==url


def test_bundle_removes_credentials_in_nested_prose_and_url_keys(tmp_path):
    url='https://fixture-user:fixture-password@example.invalid/path?api_key=fixture-key'
    diagnostic=diagnostics.make('PROBE_FAILED','warning','core','failed at '+url,
                                details={'notes':('endpoint '+url,),url:'value'})
    path=recovery.export_bundle(recovery.issues([diagnostic]),tmp_path,name='url-privacy')
    text=path.read_text(encoding='utf8')
    for secret in ('fixture-user','fixture-password','fixture-key'):
        assert secret not in text
    assert 'example.invalid' in text
