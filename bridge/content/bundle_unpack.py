"""Unpack a Unity asset bundle to loose files on disk with constant memory.

UnityPy opens a bundle by decompressing the whole archive into RAM (the SR2 identifiable-types bundle: 332 MB packed, ~5 GB in memory) and
then copying every node again. Here the blocks are decompressed one at a time straight into a temporary file and each node (serialized
file, `.resS` stream ...) is copied out to its own file, so the extractor can open the serialized files from disk (UnityPy streams plain
files) and needs about a gigabyte instead of five.

The unpacked files are scratch data derived from the player's game: they live under the caches folder and may be deleted at any time.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

CHUNK = 8 * 1024 * 1024


def _marker(bundle: Path) -> dict:
    info = bundle.stat()
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'name': bundle.name}


def unpack_bundle(bundle: Path, out_dir: Path) -> list[Path]:
    """Write every node of `bundle` into `out_dir` (named by its path inside the archive); returns the files.
    A bundle already unpacked (same size and time) is not unpacked again."""
    from UnityPy.files import BundleFile
    from UnityPy.streams import EndianBinaryReader

    bundle, out_dir = Path(bundle), Path(out_dir)
    # one flat folder for every bundle: a stream (`archive:/CAB-x/CAB-x.resS`) can point into another bundle's node, and UnityPy finds it
    # by file name next to the serialized file
    out_dir.mkdir(parents=True, exist_ok=True)
    done = out_dir / ('.unpacked-' + bundle.stem[:60] + '.json')
    if done.is_file():
        try:
            state = json.loads(done.read_text(encoding='utf-8'))
            if state.get('source') == _marker(bundle) and all((out_dir / n).is_file() for n in state['files']):
                return [out_dir / n for n in state['files']]
        except (OSError, ValueError, KeyError):
            pass
    if done.is_file():
        try:
            for stale in json.loads(done.read_text(encoding='utf-8')).get('files', []):
                (out_dir / stale).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    temp = out_dir / ('.blocks-' + bundle.stem[:40] + '.tmp')
    names: list[str] = []

    class DiskBundle(BundleFile):
        """BundleFile whose archive body goes to a file block by block instead of one bytes object."""

        def read_fs(self, reader):                                     # noqa: D401 - mirrors BundleFile.read_fs
            return _read_fs_to_disk(self, reader, temp)

        def read_files(self, reader, files):
            with open(temp, 'rb') as blocks:
                for node in files:
                    target = out_dir / Path(node.path).name
                    blocks.seek(node.offset)
                    left = node.size
                    with open(target, 'wb') as out:
                        while left > 0:
                            piece = blocks.read(min(CHUNK, left))
                            if not piece:
                                break
                            out.write(piece)
                            left -= len(piece)
                    names.append(target.name)

    reader = EndianBinaryReader(open(bundle, 'rb'))
    try:
        DiskBundle(reader, None)
    finally:
        reader.dispose()
        try:
            temp.unlink()
        except OSError:
            pass
    done.write_text(json.dumps({'source': _marker(bundle), 'files': names}), encoding='utf-8')
    return [out_dir / n for n in names]


def _read_fs_to_disk(self, reader, temp: Path):
    """BundleFile.read_fs with the block join replaced by a streamed write (same header handling)."""
    from UnityPy.enums.BundleFile import ArchiveFlags, ArchiveFlagsOld
    from UnityPy.files.BundleFile import BlockInfo, DirectoryInfoFS
    from UnityPy.streams import EndianBinaryReader

    reader.read_long()                                                  # total size
    compressed_size = reader.read_u_int()
    uncompressed_size = reader.read_u_int()
    flags_value = reader.read_u_int()
    if self.signature != 'UnityFS':
        reader.read_byte()
    version = self.parse_version()
    if (version < (2020,) or (version[0] == 2020 and version < (2020, 3, 34)) or
            (version[0] == 2021 and version < (2021, 3, 2)) or (version[0] == 2022 and version < (2022, 1, 1))):
        self.dataflags = ArchiveFlagsOld(flags_value)
    else:
        self.dataflags = ArchiveFlags(flags_value)
    if self.dataflags & self.dataflags.UsesAssetBundleEncryption:
        raise ValueError('encrypted bundles are not supported')
    if self.version >= 7 or (version[0] == 2019 and version >= (2019, 4, 15)):
        reader.align_stream(16)
        self._uses_block_alignment = True
    start = reader.Position
    if self.dataflags & ArchiveFlags.BlocksInfoAtTheEnd:
        reader.Position = reader.Length - compressed_size
        info_bytes = reader.read_bytes(compressed_size)
        reader.Position = start
    else:
        info_bytes = reader.read_bytes(compressed_size)
    info_bytes = self.decompress_data(info_bytes, uncompressed_size, self.dataflags)
    info = EndianBinaryReader(info_bytes, offset=start)
    info.read_bytes(16)
    blocks = [BlockInfo(info.read_u_int(), info.read_u_int(), info.read_u_short()) for _ in range(info.read_int())]
    nodes = [DirectoryInfoFS(info.read_long(), info.read_long(), info.read_u_int(), info.read_string_to_null())
             for _ in range(info.read_int())]
    if blocks:
        self._block_info_flags = blocks[0].flags
    if isinstance(self.dataflags, ArchiveFlags) and self.dataflags & ArchiveFlags.BlockInfoNeedPaddingAtStart:
        reader.align_stream(16)
    with open(temp, 'wb') as out:
        for index, block in enumerate(blocks):
            out.write(self.decompress_data(reader.read_bytes(block.compressedSize), block.uncompressedSize, block.flags, index))
    return nodes, None


def unpack_all(bundles: list[Path], root: Path) -> dict[str, list[Path]]:
    """{bundle file name: unpacked files}; each bundle gets its own folder under `root`."""
    result = {}
    for bundle in bundles:
        result[Path(bundle).name] = unpack_bundle(Path(bundle), Path(root))
    return result
