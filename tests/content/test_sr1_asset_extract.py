"""Only synthetic fixtures, plus a separately gated local installation oracle."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import zlib

import pytest


def module():
    try:
        return importlib.import_module('bridge.content.sr1_asset_extract')
    except ModuleNotFoundError:
        pytest.fail('SR1 extraction boundary is not implemented')


def matrix():
    return {f'e{r}{c}': int(r == c) for r in range(4) for c in range(4)}


def bones():
    return [{'name': 'bone_root', 'parent': -1, 'p': [0, 0, 0], 'q': [0, 0, 0, 1], 's': [1, 1, 1]},
            {'name': 'skin', 'parent': 0, 'p': [0, 0, 0], 'q': [0, 0, 0, 1], 's': [1, 1, 1]}]


def test_mesh_binding_uses_mesh_hash_order_not_null_renderer_bones():
    tree = {'m_BoneNameHashes': [zlib.crc32(b'bone_root/skin')], 'm_BindPose': [matrix()]}
    assert module().resolve_skin(tree, bones(), ['skin']) == ['skin']


@pytest.mark.parametrize('mutate', [
    lambda t: t.update(m_BoneNameHashes=[999]),
    lambda t: t.update(m_BoneNameHashes=[]),
    lambda t: t['m_BindPose'][0].update(e00=0),
])
def test_unresolved_or_invalid_binding_is_refused(mutate):
    tree = {'m_BoneNameHashes': [zlib.crc32(b'bone_root/skin')], 'm_BindPose': [matrix()]}
    mutate(tree)
    with pytest.raises(ValueError):
        module().resolve_skin(tree, bones(), ['skin'])


def test_palette_matches_shared_flat_rgba_order():
    appearance = {'SaveSet': 1, 'ColorPalette': {k: dict(r=i/10, g=0, b=0, a=1)
                  for i, k in enumerate(('Top', 'Middle', 'Bottom', 'Ammo'))}}
    entry = module().palette_entry(appearance)
    assert entry['native_ref'] == 'SlimeDefinition.Rad'
    assert entry['palette_legacy'] == [0, 0, 0, 1, .1, 0, 0, 1, .2, 0, 0, 1, .3, 0, 0, 1]
    assert entry['palette_new'] is None


def test_geometry_refuses_truncated_skin_and_bad_triangles():
    geometry = dict(vertices=[[0, 0, 0]]*3, normals=[], tangents=[], uv=[], uv2=[], colors=[],
                    triangles=[[0, 1, 2]], bind_poses=[[1 if r == c else 0 for r in range(4) for c in range(4)]],
                    bone_weights=[[1, 0, 0, 0, 0, 0, 0, 0]]*2)
    with pytest.raises(ValueError, match='weight'):
        module().validate_geometry(geometry, ['skin'])
    geometry['bone_weights'].append(geometry['bone_weights'][0])
    module().validate_geometry(geometry, ['skin'])
    geometry['triangles'][0][2] = 3
    with pytest.raises(ValueError, match='triangle'):
        module().validate_geometry(geometry, ['skin'])


def test_atomic_publication_hashes_all_files_and_does_not_replace(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'REPO_ROOT', tmp_path)
    out = tmp_path/'Lab/RuntimeArtifacts/test-cache'
    def producer(stage):
        (stage/'model.json').write_text('{}')
        (stage/'dump-receipt.json').write_text('{}')
        return {'icon': None, 'vertex_total': 0}
    result = m.publish_cache(out, producer)
    manifest = json.loads((out/'cache-manifest.json').read_text())
    assert set(manifest['files']) == {'model.json', 'dump-receipt.json'}
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest() == digest
    assert result['cache_directory'] == str(out)
    with pytest.raises(ValueError, match='exists'):
        m.publish_cache(out, producer)


def test_atomic_failure_leaves_no_published_cache(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'REPO_ROOT', tmp_path)
    out = tmp_path/'Lab/RuntimeArtifacts/test-cache'
    def producer(stage):
        (stage/'model.json').write_text('partial')
        raise ValueError('ambiguous asset')
    with pytest.raises(ValueError, match='ambiguous'):
        m.publish_cache(out, producer)
    assert not out.exists()
    assert list(out.parent.iterdir()) == []
    with pytest.raises(ValueError, match='Lab'):
        m.publish_cache(tmp_path/'outside', producer)


@pytest.mark.skipif(os.environ.get('SLIMEBRIDGE_REAL_SR1_CACHE') != '1', reason='explicit real SR1 opt-in required')
def test_real_rad_cache(tmp_path):
    m = module()
    source = Path(os.environ.get('SLIMEBRIDGE_SR1_DATA',
        'Lab/Games/SR1/SlimeRancher_Data'))
    import uuid
    out = m.REPO_ROOT/'Lab/RuntimeArtifacts'/('rad-test-' + uuid.uuid4().hex)
    result = m.extract_species('Rad', 'slime.rad', source, out, allow_nonlab=True)
    doc = json.loads((out/'model.json').read_text())
    assert doc['schema'] == 'bridge-native-mesh' and doc['schema_version'] == 2
    assert doc['source_game'] == 'SR1'
    assert doc['skeleton']['bone_count'] == len(doc['skeleton']['bones']) > 0
    assert result['vertex_total'] > 0
    assert doc['source_animation']['default_clip'] in [c['name'] for c in doc['source_animation']['clips']]
    assert len(doc['skinned_renderers']) > 0
    for row in doc['skinned_renderers']:
        m.validate_geometry(row, row['skin_bones'])
    manifest = json.loads((out/'cache-manifest.json').read_text())
    assert set(manifest['files']) == {p.name for p in out.iterdir() if p.name != 'cache-manifest.json'}
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest() == digest
    receipt = json.loads((out/'dump-receipt.json').read_text())
    assert receipt['source_files']
    for name, digest in receipt['source_files'].items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest
    effects = json.loads((out/'source-effects.json').read_text())
    assert effects['rendered'] is False and effects['entries']


def _rotation_matrix(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


@pytest.mark.parametrize('q', [(0, 0, 0, 1), (0.7071068, 0, 0, 0.7071068), (0, 0.7071068, 0, 0.7071068), (0, 0, 1, 0), (0.5, 0.5, 0.5, 0.5),
                               (0.18257419, 0.36514837, 0.54772256, 0.73029674)])
def test_quaternion_from_matrix_round_trips(q):
    m = module()
    got = m._quaternion(_rotation_matrix(q))
    sign = 1 if sum(a * b for a, b in zip(got, q)) >= 0 else -1                 # q and -q are the same rotation
    assert all(abs(sign * a - b) < 1e-5 for a, b in zip(got, q))


def test_rotated_static_attachments_keep_their_rotation():
    m = module()
    skeleton = bones()
    skeleton[1]['q'] = [0.7071068, 0, 0, 0.7071068]                                # the parent bone is turned 90 degrees about X
    result = m.static_attachment(skeleton, 'skin', {'local_position': [0, 1, 0], 'local_rotation': [0, 0, 0, 1], 'local_scale': [1, 1, 1]})
    assert abs(abs(result['local_rotation'][0]) - 0.7071068) < 1e-5 and abs(abs(result['local_rotation'][3]) - 0.7071068) < 1e-5
    assert abs(result['local_position'][1]) < 1e-6 and abs(abs(result['local_position'][2]) - 1) < 1e-6   # +Y offset turned onto Z
