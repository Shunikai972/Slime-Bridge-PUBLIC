"""Streamed unpacking uses synthetic UnityPy-authored bundles, never game-derived bytes."""
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.content import bundle_unpack
from portability import directory_link


def bundle(path, files=None, packer='none'):
    from UnityPy.enums.BundleFile import ArchiveFlags
    from UnityPy.files import BundleFile
    from UnityPy.files.File import File
    value = BundleFile.__new__(BundleFile)
    File.__init__(value)
    value.signature, value.version = 'UnityFS', 7
    value.version_player = value.version_engine = '2021.3.2f1'
    value.dataflags = ArchiveFlags(64)
    value._uses_block_alignment = True
    value.files = {name: SimpleNamespace(flags=0, save=lambda data=data: data)
                   for name, data in (files or {'CAB-fixture': b'fixture data', 'CAB-fixture.resS': b'stream data'}).items()}
    path.write_bytes(value.save(packer))
    return path


@pytest.mark.parametrize('packer', ['none', 'lz4', 'lzma', (192, 64), (194, 66), (193, 65)])
def test_real_synthetic_bundle_round_trip_and_cached_read(tmp_path, monkeypatch, packer):
    source = bundle(tmp_path / 'fixture.bundle', packer=packer)
    out = tmp_path / 'out'
    paths = bundle_unpack.unpack_bundle(source, out)
    assert {p.name: p.read_bytes() for p in paths} == {'CAB-fixture': b'fixture data', 'CAB-fixture.resS': b'stream data'}
    def forbidden(*args):
        pytest.fail('a validated cache hit should not decompress blocks again')
    monkeypatch.setattr(bundle_unpack, '_read_fs_to_disk', forbidden)
    assert bundle_unpack.unpack_bundle(source, out) == paths


@pytest.mark.parametrize('state', ['stale', 'current'])
@pytest.mark.parametrize('name', ['../unrelated.bin', 'C:/unrelated.bin', 'nested/item', '.blocks-fixture.tmp',
                                  'file.', 'CON', 'a:stream', 'CAB-good\n'])
def test_invalid_marker_never_reads_or_unlinks_external_paths(tmp_path, state, name):
    source = bundle(tmp_path / 'fixture.bundle')
    out = tmp_path / 'out'
    out.mkdir()
    outside = tmp_path / 'unrelated.bin'
    outside.write_bytes(b'outside')
    marker = {'source': bundle_unpack._marker(source) if state == 'current' else {}, 'files': [name]}
    (out / '.unpacked-fixture.json').write_text(json.dumps(marker), encoding='utf8')
    with pytest.raises(ValueError):
        bundle_unpack.unpack_bundle(source, out)
    assert outside.read_bytes() == b'outside'
    assert sorted(p.name for p in out.iterdir()) == ['.unpacked-fixture.json']


@pytest.mark.parametrize('location', ['root', 'marker', 'node'])
def test_preexisting_junction_is_refused(tmp_path, location):
    source = bundle(tmp_path / 'fixture.bundle')
    out, outside = tmp_path / 'out', tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'keep.bin'
    sentinel.write_bytes(b'outside')
    if location == 'root':
        link = out
    else:
        out.mkdir()
        link = out / ('.unpacked-fixture.json' if location == 'marker' else 'CAB-fixture')
    with directory_link(link, outside):
        with pytest.raises(ValueError):
            bundle_unpack.unpack_bundle(source, out)
        assert sentinel.read_bytes() == b'outside'
        assert sorted(p.name for p in outside.iterdir()) == ['keep.bin']


def test_duplicate_flattened_node_names_are_rejected_before_any_node_is_written(tmp_path):
    source = bundle(tmp_path / 'fixture.bundle', {'one/CAB-same': b'one', 'two/cab-SAME': b'two'})
    out = tmp_path / 'out'
    with pytest.raises(ValueError):
        bundle_unpack.unpack_bundle(source, out)
    assert list(out.iterdir()) == []


def test_truncated_node_is_not_published_as_a_complete_cache(tmp_path, monkeypatch):
    source = bundle(tmp_path / 'fixture.bundle')
    original = bundle_unpack._read_fs_to_disk
    def oversized(self, reader, temp):
        nodes, result = original(self, reader, temp)
        nodes = [SimpleNamespace(path=n.path, offset=n.offset, size=n.size + 1000000) for n in nodes]
        return nodes, result
    monkeypatch.setattr(bundle_unpack, '_read_fs_to_disk', oversized)
    out = tmp_path / 'out'
    with pytest.raises(ValueError):
        bundle_unpack.unpack_bundle(source, out)
    assert not (out / '.unpacked-fixture.json').exists()
    assert list(out.iterdir()) == []


def test_case_only_node_refresh_keeps_the_new_file_and_a_valid_marker(tmp_path):
    source = bundle(tmp_path / 'fixture.bundle', {'CAB-x': b'old bytes', 'CAB-stale': b'remove me'})
    out = tmp_path / 'out'
    bundle_unpack.unpack_bundle(source, out)
    bundle(source, {'cab-x': b'new bytes longer'})
    paths = bundle_unpack.unpack_bundle(source, out)
    assert len(paths) == 1
    assert paths[0].is_file(), 'case-sensitive stale cleanup deleted the freshly published Windows alias'
    assert paths[0].read_bytes() == b'new bytes longer'
    assert not (out / 'CAB-stale').exists()
    marker = json.loads((out / '.unpacked-fixture.json').read_text(encoding='utf8'))
    assert marker['files'] == ['cab-x']
    assert all((out / name).is_file() for name in marker['files'])


def test_truncated_body_cannot_consume_trailing_bundle_metadata(tmp_path):
    source = bundle(tmp_path / 'tail.bundle', {'CAB-x': b'ABCDEF'}, packer=(192, 64))
    data = bytearray(source.read_bytes())
    version = b'2021.3.2f1\x00'
    header = data.index(version) + len(version)
    header = data.index(version, header) + len(version)
    info_size = struct.unpack_from('>I', data, header + 8)[0]
    metadata_start = len(data) - info_size
    assert data[metadata_start - 6:metadata_start] == b'ABCDEF'
    del data[metadata_start - 1]
    # Keep the total size internally consistent: only the payload extent is truncated.
    struct.pack_into('>q', data, header, len(data))
    source.write_bytes(data)
    out = tmp_path / 'out'
    with pytest.raises((ValueError, EOFError)):
        bundle_unpack.unpack_bundle(source, out)
    assert list(out.iterdir()) == []
    source.unlink()  # Reader must be closed even on this framing failure on Windows.


@pytest.mark.parametrize('section', ['block', 'metadata'])
def test_decompressed_sizes_must_match_bundle_declarations(tmp_path, section):
    source = bundle(tmp_path / 'sizes.bundle', {'CAB-x': b'ABCDEF'}, packer=(192, 64))
    data = bytearray(source.read_bytes())
    version = b'2021.3.2f1\x00'
    header = data.index(version) + len(version)
    header = data.index(version, header) + len(version)
    if section == 'block':
        info_size = struct.unpack_from('>I', data, header + 8)[0]
        field = len(data) - info_size + 20  # Hash(16), block count(4), first decoded size.
        assert struct.unpack_from('>I', data, field)[0] == 6
    else:
        field = header + 12
    declared = struct.unpack_from('>I', data, field)[0]
    struct.pack_into('>I', data, field, declared + 1)
    source.write_bytes(data)
    out = tmp_path / 'out'
    with pytest.raises((ValueError, EOFError)):
        bundle_unpack.unpack_bundle(source, out)
    assert list(out.iterdir()) == []
