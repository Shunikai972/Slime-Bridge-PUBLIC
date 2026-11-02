"""Both extraction directions retain the old active cache until a staged replacement commits."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.content import species_pipeline, sr1_pipeline
from portability import directory_link

CANONICAL = 'largo.cotton.rad'


def cache(folder, content=b'cache'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'model.json').write_bytes(content)
    (folder / 'cache-manifest.json').write_text(json.dumps({'files': {}}), encoding='utf8')


def build(pipeline, tmp_path, root, monkeypatch):
    if pipeline == 'SR2':
        built = tmp_path / 'built'
        cache(built, b'new cache')
        species_pipeline._promote(built, root, CANONICAL)
    else:
        from bridge.content import sr1_asset_extract
        def extract(_species, _canonical, _source, out, **kwargs):
            cache(out, b'new cache')
            return {'vertex_total': 0, 'clips': []}
        monkeypatch.setattr(sr1_asset_extract, 'extract_species', extract)
        sr1_pipeline.build_row({'canonical': CANONICAL, 'species': 'Fixture', 'prefab': 'fixture', 'kind': 'largo'},
                               tmp_path / 'unused-source', root, object(), force=True)


@pytest.mark.parametrize('pipeline', ['SR1', 'SR2'])
@pytest.mark.parametrize('fault', ['publish', 'backup'])
def test_failed_publication_restores_active_cache(tmp_path, monkeypatch, pipeline, fault):
    module = sr1_pipeline if pipeline == 'SR1' else species_pipeline
    root = tmp_path / 'caches'
    direction = 'sr1-to-sr2' if pipeline == 'SR1' else 'sr2-to-sr1'
    target = root / direction / CANONICAL
    cache(target, b'old cache')
    archives = tmp_path / 'previous'
    monkeypatch.setattr(module, 'REPLACED_ROOT', archives)
    move, rename = module.shutil.move, Path.rename
    def refused(source, destination):
        return ((Path(destination) == target and Path(source) != target and Path(source).parent != archives) if fault == 'publish'
                else Path(source) == target)
    def fail_move(source, destination, *args, **kwargs):
        if refused(source, destination):
            raise OSError('injected cache move failure')
        return move(source, destination, *args, **kwargs)
    def fail_rename(source, destination):
        if refused(source, destination):
            raise OSError('injected cache move failure')
        return rename(source, destination)
    monkeypatch.setattr(module.shutil, 'move', fail_move)
    monkeypatch.setattr(Path, 'rename', fail_rename)
    with pytest.raises(OSError, match='injected cache move failure'):
        build(pipeline, tmp_path, root, monkeypatch)
    assert (target / 'model.json').read_bytes() == b'old cache'
    assert not list(target.parent.glob('.*-incoming'))


@pytest.mark.parametrize('pipeline', ['SR1', 'SR2'])
def test_successful_repeated_publications_keep_distinct_backups(tmp_path, monkeypatch, pipeline):
    module = sr1_pipeline if pipeline == 'SR1' else species_pipeline
    monkeypatch.setattr(module, 'time', SimpleNamespace(strftime=lambda _format: '20260101-000000'))
    root = tmp_path / 'caches'
    target = root / ('sr1-to-sr2' if pipeline == 'SR1' else 'sr2-to-sr1') / CANONICAL
    cache(target, b'old cache')
    archives = tmp_path / 'previous'
    monkeypatch.setattr(module, 'REPLACED_ROOT', archives)
    for _ in range(2):
        build(pipeline, tmp_path, root, monkeypatch)
    assert (target / 'model.json').read_bytes() == b'new cache'
    assert len(list(archives.iterdir())) == 2
    assert sorted(p.read_bytes() for p in archives.glob('*/model.json')) == [b'new cache', b'old cache']


@pytest.mark.parametrize('pipeline', ['SR1', 'SR2'])
def test_destination_junction_is_rejected_without_changing_external_data(tmp_path, monkeypatch, pipeline):
    root, outside = tmp_path / 'caches', tmp_path / 'outside'
    root.mkdir()
    cache(outside, b'outside')
    direction = 'sr1-to-sr2' if pipeline == 'SR1' else 'sr2-to-sr1'
    with directory_link(root / direction, outside):
        with pytest.raises(ValueError):
            build(pipeline, tmp_path, root, monkeypatch)
        assert (outside / 'model.json').read_bytes() == b'outside'
        assert not (outside / CANONICAL).exists()
