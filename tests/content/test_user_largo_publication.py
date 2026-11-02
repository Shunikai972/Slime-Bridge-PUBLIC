"""Downloaded model metadata is untrusted; failed publication must preserve the active cache."""
import json
from pathlib import Path

import pytest

from bridge.content import user_largos
from portability import directory_link

CANONICAL = 'largo.cotton.rad'


def cache(folder, source='USER', canonical=CANONICAL):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'model.json').write_text(json.dumps({'prefab_scale': 1.0}), encoding='utf8')
    (folder / 'cache-manifest.json').write_text(json.dumps({
        'source_game': source, 'canonical_id': canonical, 'files': {}}), encoding='utf8')


def model(root, name='fixture', canonical=CANONICAL):
    folder = root / name
    cache(folder / 'cache', canonical=canonical)
    (folder / 'largo.json').write_text(json.dumps({'id': name, 'canonical': canonical}), encoding='utf8')
    return folder


@pytest.mark.parametrize('canonical', ['largo.fake/../../../victim', 'largo.a.b\\..\\victim',
                                       'largo.a.b:stream', 'largo.a.b.', 'largo.A.b', 'largo.a.b\n', 42])
def test_invalid_canonical_is_refused_before_any_cache_mutation(tmp_path, canonical):
    models, caches = tmp_path / 'models', tmp_path / 'caches'
    model(models, canonical=canonical)
    cache(caches / 'sr2-to-sr1' / CANONICAL)
    sentinel = caches / 'sr2-to-sr1' / CANONICAL / 'keep.bin'
    sentinel.write_bytes(b'old cache')
    with pytest.raises(ValueError):
        user_largos.sync(models, caches)
    assert sentinel.read_bytes() == b'old cache'
    assert not (caches / 'sr1-to-sr2').exists()


def test_duplicate_canonical_is_refused_before_publication(tmp_path):
    models, caches = tmp_path / 'models', tmp_path / 'caches'
    model(models, 'one')
    model(models, 'two')
    with pytest.raises(ValueError):
        user_largos.sync(models, caches)
    assert not caches.exists()


@pytest.mark.parametrize('location', ['models-root', 'source-cache', 'destination-root', 'second-direction'])
def test_real_junctions_are_rejected_before_either_direction_is_written(tmp_path, location):
    models, caches, outside = tmp_path / 'models', tmp_path / 'caches', tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'keep.bin'
    sentinel.write_bytes(b'outside')
    if location == 'models-root':
        model(outside)
        link, target = models, outside
    elif location == 'source-cache':
        folder = model(models)
        import shutil
        shutil.rmtree(folder / 'cache')
        cache(outside)
        link, target = folder / 'cache', outside
    elif location == 'destination-root':
        model(models)
        link, target = caches, outside
    else:
        model(models)
        caches.mkdir()
        link, target = caches / 'sr1-to-sr2', outside
    with directory_link(link, target):
        with pytest.raises(ValueError):
            user_largos.sync(models, caches)
        assert not (caches / 'sr2-to-sr1' / CANONICAL).exists()
        assert sentinel.read_bytes() == b'outside'


@pytest.mark.parametrize('source', ['USER', 'SR2'])
def test_failed_incoming_rename_restores_the_previous_active_cache(tmp_path, monkeypatch, source):
    models, caches = tmp_path / 'models', tmp_path / 'caches'
    model(models)
    target = caches / 'sr2-to-sr1' / CANONICAL
    cache(target, source)
    sentinel = target / 'old.bin'
    sentinel.write_bytes(b'previous active cache')
    rename = Path.rename
    def fail_incoming(self, destination):
        if self.name.endswith('-incoming') and Path(destination) == target:
            raise OSError('injected incoming failure')
        return rename(self, destination)
    monkeypatch.setattr(Path, 'rename', fail_incoming)
    with pytest.raises(OSError, match='injected incoming failure'):
        user_largos.sync(models, caches)
    assert sentinel.read_bytes() == b'previous active cache'
    assert not list(target.parent.glob('.*-incoming'))


def test_adaptation_failure_does_not_move_the_old_native_cache(tmp_path):
    models, caches = tmp_path / 'models', tmp_path / 'caches'
    folder = model(models)
    (folder / 'cache/model.json').write_text('[]', encoding='utf8')
    target = caches / 'sr2-to-sr1' / CANONICAL
    cache(target, 'SR2')
    before = (target / 'cache-manifest.json').read_bytes()
    with pytest.raises((ValueError, AttributeError)):
        user_largos.sync(models, caches)
    assert (target / 'cache-manifest.json').read_bytes() == before
    assert not target.with_name(CANONICAL + '-native').exists()
