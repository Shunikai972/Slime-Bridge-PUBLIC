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
import re
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from .cache_publication import PUBLICATION_LOCK, checked_path, checked_tree

CHUNK = 8 * 1024 * 1024


def _marker(bundle: Path) -> dict:
    info = bundle.stat()
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'name': bundle.name}


def unpack_bundle(bundle: Path, out_dir: Path) -> list[Path]:
    """Write every node of `bundle` into `out_dir` (named by its path inside the archive); returns the files.
    A bundle already unpacked (same size and time) is not unpacked again."""
    with PUBLICATION_LOCK:
        return _unpack_bundle(bundle, out_dir)


def _filename(name):
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name) or
            name.endswith('.') or PureWindowsPath(name).is_reserved()):
        raise ValueError('unsafe unpacked bundle filename')
    return name


def _unpack_bundle(bundle, out_dir):
    from UnityPy.files import BundleFile
    from UnityPy.streams import EndianBinaryReader

    bundle, out_dir = Path(bundle), checked_tree(out_dir)
    # one flat folder for every bundle: a stream (`archive:/CAB-x/CAB-x.resS`) can point into another bundle's node, and UnityPy finds it
    # by file name next to the serialized file
    out_dir.mkdir(parents=True, exist_ok=True)
    done = out_dir / ('.unpacked-' + bundle.stem[:60] + '.json')
    checked_path(done)
    old_names = []
    if done.exists():
        if not done.is_file() or done.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('invalid unpacked bundle marker')
        state = json.loads(done.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or not isinstance(state.get('files'), list) or len(state['files']) > 20000:
            raise ValueError('invalid unpacked bundle file list')
        old_names = [_filename(name) for name in state['files']]
        if len({name.casefold() for name in old_names}) != len(old_names):
            raise ValueError('duplicate unpacked bundle filename')
        for name in old_names:
            checked_path(out_dir / name)
        if state.get('source') == _marker(bundle) and all((out_dir / name).is_file() for name in old_names):
            return [out_dir / name for name in old_names]
    descriptor, temp_name = tempfile.mkstemp(prefix='.blocks-', suffix='.tmp', dir=out_dir)
    os.close(descriptor)
    temp = Path(temp_name)
    names: list[str] = []

    class DiskBundle(BundleFile):
        """BundleFile whose archive body goes to a file block by block instead of one bytes object."""

        def read_fs(self, reader):                                     # noqa: D401 - mirrors BundleFile.read_fs
            return _read_fs_to_disk(self, reader, temp)

        def read_files(self, reader, files):
            plan, seen = [], set()
            length = temp.stat().st_size
            for node in files:
                path = PurePosixPath(node.path.replace('\\', '/'))
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('unsafe bundle node path')
                name = _filename(path.name)
                if name.casefold() in seen:
                    raise ValueError('duplicate flattened bundle node filename')
                seen.add(name.casefold())
                if (type(node.offset) is not int or type(node.size) is not int or node.offset < 0 or
                        node.size < 0 or node.offset > length or node.size > length - node.offset):
                    raise ValueError('bundle node extends beyond decompressed blocks')
                target = checked_path(out_dir / name)
                if target.exists() and not target.is_file():
                    raise ValueError('bundle node target is not a file')
                plan.append((node, target))
            with open(temp, 'rb') as blocks:
                for node, target in plan:
                    blocks.seek(node.offset)
                    left = node.size
                    pending = None
                    try:
                        with tempfile.NamedTemporaryFile(prefix='.node-', dir=out_dir, delete=False) as out:
                            pending = Path(out.name)
                            while left > 0:
                                piece = blocks.read(min(CHUNK, left))
                                if not piece:
                                    raise ValueError('truncated decompressed bundle node')
                                out.write(piece)
                                left -= len(piece)
                        checked_path(target)
                        os.replace(pending, target)
                    finally:
                        if pending is not None:
                            pending.unlink(missing_ok=True)
                    names.append(target.name)

    try:
        with open(bundle, 'rb') as stream:
            reader = EndianBinaryReader(stream)
            try:
                DiskBundle(reader, None)
            finally:
                reader.dispose()
    finally:
        temp.unlink(missing_ok=True)
    published = {name.casefold(): out_dir / name for name in names}
    for stale in old_names:
        path = checked_path(out_dir / stale)
        current = published.get(stale.casefold())
        # On case-insensitive filesystems an old spelling can name the new node itself.
        if current is None or not path.exists() or not path.samefile(current):
            path.unlink(missing_ok=True)
    marker_temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.marker-', dir=out_dir, delete=False) as stream:
            marker_temp = Path(stream.name)
            json.dump({'source': _marker(bundle), 'files': names}, stream)
        checked_path(done)
        os.replace(marker_temp, done)
    finally:
        if marker_temp is not None:
            marker_temp.unlink(missing_ok=True)
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
    data_end = reader.Length
    if self.dataflags & ArchiveFlags.BlocksInfoAtTheEnd:
        data_end = reader.Length - compressed_size
        if data_end < start:
            raise ValueError('bundle metadata overlaps its header')
        reader.Position = data_end
        info_bytes = reader.read_bytes(compressed_size)
        reader.Position = start
    else:
        info_bytes = reader.read_bytes(compressed_size)
    info_bytes = self.decompress_data(info_bytes, uncompressed_size, self.dataflags)
    if len(info_bytes) != uncompressed_size:
        raise ValueError('decoded bundle metadata size differs from its declaration')
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
            if block.compressedSize > data_end - reader.Position:
                raise ValueError('bundle payload exceeds its data region')
            encoded = reader.read_bytes(block.compressedSize)
            if len(encoded) != block.compressedSize:
                raise ValueError('truncated compressed bundle block')
            decoded = self.decompress_data(encoded, block.uncompressedSize, block.flags, index)
            if len(decoded) != block.uncompressedSize:
                raise ValueError('decoded bundle block size differs from its declaration')
            out.write(decoded)
    return nodes, None


def unpack_all(bundles: list[Path], root: Path) -> dict[str, list[Path]]:
    """{bundle file name: unpacked files}; each bundle gets its own folder under `root`."""
    result = {}
    for bundle in bundles:
        result[Path(bundle).name] = unpack_bundle(Path(bundle), Path(root))
    return result
