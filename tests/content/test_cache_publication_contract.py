"""Lifecycle and ownership contracts for the cache publication seam exercised by both pipelines."""
from pathlib import Path

import pytest

from bridge.content.cache_publication import cache_path, publish_directory
from portability import directory_link


@pytest.mark.parametrize('fault', ['copy', 'adapt', 'backup', 'incoming'])
def test_failures_preserve_source_and_old_target_and_clean_owned_staging(tmp_path, monkeypatch, fault):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'payload').write_bytes(b'new')
    (target / 'payload').write_bytes(b'old')
    from bridge.content import cache_publication as module
    copy, rename = module.shutil.copytree, Path.rename
    def fail_copy(*args, **kwargs):
        if fault == 'copy':
            raise OSError('injected copy failure')
        return copy(*args, **kwargs)
    def fail_rename(path, destination):
        if (fault == 'backup' and path == target) or (fault == 'incoming' and path.name.endswith('-incoming')):
            raise OSError('injected rename failure')
        return rename(path, destination)
    def adapt(stage):
        (stage / 'payload').write_bytes(b'adapted')
        if fault == 'adapt':
            raise ValueError('injected adaptation failure')
    monkeypatch.setattr(module.shutil, 'copytree', fail_copy)
    monkeypatch.setattr(Path, 'rename', fail_rename)
    with pytest.raises((OSError, ValueError), match='injected'):
        publish_directory(source, target, adapt=adapt)
    assert (source / 'payload').read_bytes() == b'new'
    assert (target / 'payload').read_bytes() == b'old'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['source', 'target']


def test_existing_backup_is_never_removed_or_overwritten(tmp_path):
    source, target, backup = (tmp_path / name for name in ('source', 'target', 'backup'))
    for directory in (source, target, backup):
        directory.mkdir()
        (directory / 'payload').write_bytes(directory.name.encode())
    with pytest.raises(ValueError, match='backup already exists'):
        publish_directory(source, target, backup=backup)
    for directory in (source, target, backup):
        assert (directory / 'payload').read_bytes() == directory.name.encode()


@pytest.mark.parametrize('overlap', ['same', 'source-parent', 'target-parent'])
def test_overlapping_source_and_target_are_refused(tmp_path, overlap):
    source = tmp_path / 'cache'
    source.mkdir()
    nested = source / 'nested'
    nested.mkdir()
    pair = {'same': (source, source), 'source-parent': (source, nested), 'target-parent': (nested, source)}[overlap]
    with pytest.raises(ValueError, match='overlap'):
        publish_directory(*pair)
    assert nested.is_dir()


def test_nested_source_junction_is_refused_without_following_it(tmp_path):
    source, outside = tmp_path / 'source', tmp_path / 'outside'
    source.mkdir()
    outside.mkdir()
    (outside / 'payload').write_bytes(b'outside')
    with directory_link(source / 'redirect', outside):
        with pytest.raises(ValueError, match='linked'):
            publish_directory(source, tmp_path / 'target')
        assert (outside / 'payload').read_bytes() == b'outside'
        assert not (tmp_path / 'target').exists()


@pytest.mark.parametrize('canonical', ['../escape', 'slime.x/../escape', 'slime.x:stream', 'slime.x\n', 'slime..x'])
def test_pipeline_cache_path_never_repairs_unsafe_identifiers(tmp_path, canonical):
    with pytest.raises(ValueError):
        cache_path(tmp_path, 'sr1-to-sr2', canonical)
    assert list(tmp_path.iterdir()) == []
