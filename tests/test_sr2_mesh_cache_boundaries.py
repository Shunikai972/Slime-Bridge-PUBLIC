"""Private synthetic native-dump integrity and rollback contracts; no native execution."""
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from tools.lab import sr2_mesh_dump as dump


def put(directory, *, missing=False, failed=False):
    directory.mkdir(parents=True, exist_ok=True)
    texture = directory / 'tex-synthetic.png'
    texture.write_bytes(b'synthetic texture bytes, not a native image')
    rows = [{'file': texture.name}]
    if missing:
        rows.append({'file': 'tex-uncovered.png'})
    if failed:
        rows.append({'property': '_Failed', 'error': 'synthetic export error'})
    model = {'schema': dump.DUMP_SCHEMA, 'schema_version': dump.DUMP_SCHEMA_VERSION,
             'reference_id': 'SlimeDefinition.Synthetic', 'skinned_renderers': [],
             'static_renderers': [{'vertices': [[0, 0, 0]], 'materials': [{'textures': rows}]}]}
    (directory / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    receipt = {'schema': dump.RECEIPT_SCHEMA, 'schema_version': dump.RECEIPT_SCHEMA_VERSION,
               'target': 'Synthetic', 'reference_id': model['reference_id'],
               'model_sha256': dump._sha(directory / 'model.json'),
               'textures': [{'file': texture.name, 'sha256': dump._sha(texture)}]}
    (directory / 'dump-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return receipt


@pytest.mark.parametrize('kind', ['missing', 'failed'])
def test_dump_rejects_uncovered_or_failed_model_texture_dependencies(tmp_path, kind):
    directory = tmp_path / 'dump'
    put(directory, **{kind: True})
    _, problems = dump._validate_dump(directory, 'Synthetic')
    assert problems


def test_post_cleanup_binding_includes_receipt_and_actual_texture_bytes(tmp_path):
    directory = tmp_path / 'dump'
    receipt = put(directory)
    first, problems = dump._validate_dump(directory, 'Synthetic')
    assert problems == []
    texture = directory / receipt['textures'][0]['file']
    texture.write_bytes(b'different synthetic texture')
    receipt['textures'][0]['sha256'] = dump._sha(texture)
    (directory / 'dump-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    second, problems = dump._validate_dump(directory, 'Synthetic')
    assert problems == [] and second != first


def test_texture_path_escape_is_rejected_before_file_hashing(tmp_path, monkeypatch):
    directory = tmp_path / 'dump'
    receipt = put(directory)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'synthetic outside marker')
    receipt['textures'][0] = {'file': '../outside.png', 'sha256': dump._sha(outside)}
    (directory / 'dump-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    sha = dump._sha

    def confined(path):
        assert path.parent == directory, 'must reject an external name before touching it'
        return sha(path)

    monkeypatch.setattr(dump, '_sha', confined)
    _, problems = dump._validate_dump(directory, 'Synthetic')
    assert problems


def test_failed_cache_copy_preserves_previous_active_cache(tmp_path, monkeypatch):
    directory = tmp_path / 'dump'
    put(directory)
    root = tmp_path / 'repo'
    target = root.joinpath(*dump.CACHE_ROOT) / 'slime.synthetic'
    target.mkdir(parents=True)
    (target / 'previous.bin').write_bytes(b'previous complete cache')
    monkeypatch.setattr(dump.oracle, 'REPO_ROOT', root)
    with mock.patch.object(dump.shutil, 'copyfile', side_effect=OSError('synthetic copy failure')):
        with pytest.raises(OSError):
            dump.publish_cache(directory, 'slime.synthetic')
    assert (target / 'previous.bin').read_bytes() == b'previous complete cache'
    assert (directory / 'model.json').is_file()


def test_successful_cache_publication_has_valid_hash_manifest(tmp_path, monkeypatch):
    directory = tmp_path / 'dump'
    put(directory)
    monkeypatch.setattr(dump.oracle, 'REPO_ROOT', tmp_path / 'repo')
    target = dump.publish_cache(directory, 'slime.synthetic')
    manifest = json.loads((target / 'cache-manifest.json').read_text())
    assert set(manifest['files']) == {'model.json', 'dump-receipt.json', 'tex-synthetic.png'}
    assert all(hashlib.sha256((target / p).read_bytes()).hexdigest() == h for p, h in manifest['files'].items())
    assert manifest['published_utc'].endswith('Z')


def test_failed_incoming_rename_rolls_back_previous_cache(tmp_path, monkeypatch):
    directory = tmp_path / 'dump'
    put(directory)
    root = tmp_path / 'repo'
    target = root.joinpath(*dump.CACHE_ROOT) / 'slime.synthetic'
    target.mkdir(parents=True)
    (target / 'previous.bin').write_bytes(b'previous complete cache')
    monkeypatch.setattr(dump.oracle, 'REPO_ROOT', root)
    rename = Path.rename
    attempts = []

    def fail_incoming(path, destination):
        if path.name.endswith('-incoming') and destination == target:
            attempts.append(path.name)
            raise OSError('synthetic publication rename failure')
        return rename(path, destination)

    with mock.patch.object(Path, 'rename', fail_incoming):
        with pytest.raises(OSError, match='publication rename'):
            dump.publish_cache(directory, 'slime.synthetic')
    assert attempts
    assert (target / 'previous.bin').read_bytes() == b'previous complete cache'
    assert (directory / 'model.json').is_file()


def test_identical_duplicate_receipt_rows_are_valid_but_case_aliases_are_not(tmp_path):
    directory = tmp_path / 'dump'
    receipt = put(directory)
    receipt['textures'].append(dict(receipt['textures'][0]))
    (directory / 'dump-receipt.json').write_text(json.dumps(receipt))
    assert dump._validate_dump(directory, 'Synthetic')[1] == []
    receipt['textures'][1]['file'] = receipt['textures'][1]['file'].upper().replace('TEX-', 'tex-').replace('.PNG', '.png')
    (directory / 'dump-receipt.json').write_text(json.dumps(receipt))
    assert dump._validate_dump(directory, 'Synthetic')[1]


@pytest.mark.parametrize('canonical', ['slime.synthetic.', 'slime.synthetic-', 'slime..synthetic', 'x' * 128])
def test_cache_identifier_must_be_canonical_without_windows_aliases(canonical):
    with pytest.raises(ValueError):
        dump.validate_canonical(canonical)
