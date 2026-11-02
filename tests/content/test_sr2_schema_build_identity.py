"""Schema cache identity binds both local input files, not just DLL size."""
from pathlib import Path
from unittest import mock

from bridge.content import sr2_schema


def game_fixture(tmp_path):
    game = tmp_path / 'synthetic-game'
    metadata = game / 'Synthetic_Data' / 'il2cpp_data' / 'Metadata' / 'global-metadata.dat'
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(b'synthetic unchanged metadata')
    assembly = game / 'GameAssembly.dll'
    assembly.write_bytes(b'synthetic assembly A')
    return game, assembly, metadata


def test_key_changes_when_same_size_assembly_changes(tmp_path):
    game, assembly, _ = game_fixture(tmp_path)
    first = sr2_schema.build_key(game)
    assert sr2_schema.build_key(game) == first
    assembly.write_bytes(b'synthetic assembly B')
    assert sr2_schema.build_key(game) != first


def test_lookup_refuses_schema_from_another_equal_size_build(tmp_path):
    game, assembly, _ = game_fixture(tmp_path)
    cache = tmp_path / 'schema-cache'
    old = cache / sr2_schema.build_key(game) / 'cpp2il_out'
    old.mkdir(parents=True)
    (old / 'Assembly-CSharp.dll').write_bytes(b'synthetic old schema')
    assert sr2_schema.schema_dir(game, cache) == old
    assembly.write_bytes(b'synthetic assembly B')
    assert sr2_schema.schema_dir(game, cache) is None
    assert (old / 'Assembly-CSharp.dll').read_bytes() == b'synthetic old schema'


def test_key_changes_when_same_size_metadata_changes(tmp_path):
    game, _, metadata = game_fixture(tmp_path)
    first = sr2_schema.build_key(game)
    original = metadata.read_bytes()
    metadata.write_bytes(b'X' + original[1:])
    assert sr2_schema.build_key(game) != first


def test_key_frames_input_file_boundaries(tmp_path):
    game, assembly, metadata = game_fixture(tmp_path)
    assembly.write_bytes(b'AB')
    metadata.write_bytes(b'C')
    first = sr2_schema.build_key(game)
    assembly.write_bytes(b'A')
    metadata.write_bytes(b'BC')
    assert sr2_schema.build_key(game) != first


def test_key_streams_both_files_and_sees_changes_after_first_chunk(tmp_path):
    game, assembly, metadata = game_fixture(tmp_path)
    payload = b'x' * (1024 * 1024 + 3)
    assembly.write_bytes(payload)
    metadata.write_bytes(payload)
    with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('must stream input files')):
        first = sr2_schema.build_key(game)
        with assembly.open('r+b') as stream:
            stream.seek(-1, 2)
            stream.write(b'y')
        second = sr2_schema.build_key(game)
        with metadata.open('r+b') as stream:
            stream.seek(-1, 2)
            stream.write(b'y')
        third = sr2_schema.build_key(game)
    assert len({first, second, third}) == 3
