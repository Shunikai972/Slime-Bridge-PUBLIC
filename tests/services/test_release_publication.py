"""Publication gates: synthetic archives, never a remote write."""
import hashlib
import sys
from unittest.mock import Mock

import pytest

from tools.release import publish_github as publisher

VERSION = '0.2.6'
NAMES = ('SlimeBridge-v0.2.6-win64.zip', 'SlimeBridge-v0.2.6-win64-standalone.zip',
         'SlimeBridge-v0.2.6-linux-x64-standalone.tar.gz')


def setup_release(tmp_path, monkeypatch):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (tmp_path / 'VERSION').write_text(VERSION, encoding='utf-8')
    monkeypatch.setattr(publisher, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['publish_github', '--draft'])
    git = Mock(side_effect=lambda *args: {
        ('remote', 'get-url', 'origin'): 'https://github.com/Shunikai972/Slime-Bridge-PUBLIC.git',
        ('status', '--porcelain'): '', ('push', 'origin', 'HEAD'): '', ('rev-parse', 'HEAD'): '0123456789abcdef',
    }[args])
    monkeypatch.setattr(publisher, 'git', git)
    monkeypatch.setattr(publisher, 'token', Mock(return_value='test-token'))
    return dist, git


def _remote(uploads):
    def remote(_secret, method, url, data=None, content_type='application/json', **kwargs):
        if method == 'GET':
            return None
        if '/uploads?' not in url:
            return {'upload_url': 'https://fixture.invalid/uploads', 'html_url': 'fixture'}
        uploads[url.split('?name=')[1]] = (data, content_type)
        return {}
    return remote


def test_missing_windows_archive_refuses_before_remote_access(tmp_path, monkeypatch):
    dist, git = setup_release(tmp_path, monkeypatch)
    (dist / NAMES[0]).write_bytes(b'fixture')
    remote = Mock()
    monkeypatch.setattr(publisher, 'api', remote)
    with pytest.raises(SystemExit):
        publisher.main()
    remote.assert_not_called()
    assert ('push', 'origin', 'HEAD') not in [call.args for call in git.call_args_list]
    assert not (dist / 'SHA256SUMS.txt').exists()


def test_without_a_local_linux_build_the_windows_archives_are_published(tmp_path, monkeypatch):
    # The Linux tarball is built by the tag's CI run, after the release exists.
    dist, _ = setup_release(tmp_path, monkeypatch)
    for name in NAMES[:2]:
        (dist / name).write_bytes(name.encode())
    uploads = {}
    monkeypatch.setattr(publisher, 'api', _remote(uploads))
    publisher.main()
    assert set(uploads) == {*NAMES[:2], 'SHA256SUMS.txt'}


def test_publisher_includes_all_three_archives_and_checksums(tmp_path, monkeypatch):
    dist, _ = setup_release(tmp_path, monkeypatch)
    for name in NAMES:
        (dist / name).write_bytes(name.encode())
    uploads = {}

    def remote(_secret, method, url, data=None, content_type='application/json', **kwargs):
        if method == 'GET':
            return None
        if '/uploads?' not in url:
            return {'upload_url': 'https://fixture.invalid/uploads', 'html_url': 'fixture'}
        uploads[url.split('?name=')[1]] = (data, content_type)
        return {}

    monkeypatch.setattr(publisher, 'api', remote)
    publisher.main()
    assert set(uploads) == {*NAMES, 'SHA256SUMS.txt'}
    payload = uploads['SHA256SUMS.txt'][0].decode().splitlines()
    assert {name: digest for digest, name in map(str.split, payload)} == {
        name: hashlib.sha256(name.encode()).hexdigest() for name in NAMES}
    assert uploads[NAMES[-1]][1] == 'application/gzip'
