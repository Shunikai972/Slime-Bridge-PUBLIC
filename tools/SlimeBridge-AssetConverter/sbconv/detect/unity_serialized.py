"""Minimal, read-only Unity SerializedFile header reader (stdlib only).

This is NOT an asset decoder. It only reads the fixed header fields needed to identify the engine
version / build target / TypeTree availability from files such as ``globalgamemanagers``; object
decoding is delegated to a real library (see ``sbconv.engines``). Layout follows the public
SerializedFile format (v9..v22).
"""

import struct
from dataclasses import dataclass, asdict

# UnityEngine.BuildTarget values (subset relevant to desktop builds)
BUILD_TARGETS = {
    1: 'StandaloneOSX', 2: 'StandaloneOSX', 5: 'StandaloneWindows', 9: 'StandaloneLinux',
    13: 'StandaloneLinux64', 19: 'StandaloneWindows64', 21: 'Android', 25: 'iOS', 27: 'WebGL',
    24: 'StandaloneLinuxUniversal', 38: 'Switch', 39: 'PS4', 40: 'XboxOne',
}


class NotSerializedFile(ValueError):
    pass


@dataclass
class SerializedHeader:
    format_version: int
    unity_version: str
    target_platform: int
    platform_name: str
    has_type_tree: bool | None
    big_endian: bool
    file_size: int

    def as_dict(self):
        return asdict(self)


def _cstr(buf, pos):
    end = buf.index(b'\x00', pos)
    return buf[pos:end].decode('ascii', 'replace'), end + 1


def read_header(path, max_bytes=4096):
    with open(path, 'rb') as f:
        buf = f.read(max_bytes)
    if len(buf) < 32:
        raise NotSerializedFile(f'{path}: too short')
    metadata_size, file_size, version, data_offset = struct.unpack('>IIII', buf[:16])
    if not 9 <= version <= 40:
        raise NotSerializedFile(f'{path}: implausible serialized-file version {version}')
    pos = 16
    big_endian = buf[pos] != 0
    pos += 4
    if version >= 22:
        metadata_size, file_size, data_offset, _ = struct.unpack('>IqqQ', buf[pos:pos + 28])
        pos += 28
    try:
        unity_version, pos = _cstr(buf, pos)
    except ValueError:
        raise NotSerializedFile(f'{path}: unterminated unity version')
    endian = '>' if big_endian else '<'
    target, = struct.unpack(endian + 'i', buf[pos:pos + 4])
    pos += 4
    has_tt = (buf[pos] != 0) if version >= 13 and pos < len(buf) else None
    return SerializedHeader(version, unity_version, target, BUILD_TARGETS.get(target, f'Unknown({target})'),
                            has_tt, big_endian, int(file_size))


def looks_like_bundle(path):
    with open(path, 'rb') as f:
        return f.read(8)[:7] in (b'UnityFS', b'UnityWe', b'UnityRa')


def read_bundle_header(path):
    """UnityFS bundle header: signature, format version, min player version, engine revision."""
    with open(path, 'rb') as f:
        buf = f.read(256)
    sig_end = buf.index(b'\x00')
    sig = buf[:sig_end].decode('ascii', 'replace')
    pos = sig_end + 1
    fmt, = struct.unpack('>I', buf[pos:pos + 4])
    pos += 4
    player, pos = _cstr(buf, pos)
    engine, pos = _cstr(buf, pos)
    return {'signature': sig, 'format_version': fmt, 'min_player_version': player, 'engine_version': engine}
