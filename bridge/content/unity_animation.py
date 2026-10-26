"""Bounded, offline sampler for generic Unity 2021 AnimationClip Transform streams.

Format reference (MIT AssetStudio, commit d158e864b556b5970709c2a52e47944d53aa98a2):
https://github.com/Perfare/AssetStudio/blob/d158e864b556b5970709c2a52e47944d53aa98a2/AssetStudio/Classes/AnimationClip.cs
https://github.com/Perfare/AssetStudio/blob/d158e864b556b5970709c2a52e47944d53aa98a2/AssetStudioUtility/ModelConverter.cs
The layout and polynomial mathematics below are independently implemented; no
AssetStudio or GPL AssetRipper implementation is copied.

This samples authored local TRS, not Animator blends, humanoid retargeting,
root-motion extraction or arbitrary-time dense interpolation. Input is the
already-read typetree, never a file or an asset API. No coordinate reflection.
"""

# Keep a single ValueError contract for every malformed/unsupported typetree.
# ruff: noqa: TRY004

import bisect
import math
import struct
import zlib

MAX_BINDINGS = 4096
MAX_SCALARS = 16384
MAX_STREAM_WORDS = 4_000_000
MAX_STREAM_FRAMES = 200_000
MAX_FRAMES = 100_000
MAX_DENSE_VALUES = 2_000_000
MAX_OUTPUT_SCALARS = 2_000_000
MAX_PATHS = 16384
MAX_TEXT_LENGTH = 4096
MAX_TIME = 3600.0
MAX_SAMPLE_RATE = 1000.0

_PROPERTIES = {1: ("local_position", 3), 2: ("local_rotation", 4),
               3: ("local_scale", 3)}
_STARTUP_TIME = -3.4028234663852886e38  # Exact IEEE-754 binary32 -FLT_MAX.
_HEADER = struct.Struct("<fi")
_KEY = struct.Struct("<iffff")


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a dictionary")
    return value


def _array(value, label, limit):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if len(value) > limit:
        raise ValueError(f"{label} exceeds limit {limit}")
    return value


def _integer(value, label, limit):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    if value > limit:
        raise ValueError(f"{label} exceeds limit {limit}")
    return value


def _number(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be numeric (not bool)")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _flag(value, label):
    if type(value) not in (int, bool) or value not in (0, 1):
        raise ValueError(f"{label} must be a boolean or 0/1")
    return bool(value)


def _text(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if len(value) > MAX_TEXT_LENGTH:
        raise ValueError(f"{label} exceeds text limit {MAX_TEXT_LENGTH}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8 text") from exc
    return value


def _rate(value, label):
    rate = _number(value, label)
    if not 0 < rate <= MAX_SAMPLE_RATE:
        raise ValueError(f"{label} must be positive and within rate limit {MAX_SAMPLE_RATE}")
    return rate


def _jitter(time, rate):
    # A small, bounded float32 timing allowance, never a material frame fraction.
    return min(1e-5, 0.001 / rate, max(1e-7, abs(time) * 2**-22))


def _resolve_path(path_hash, paths):
    if path_hash not in paths:
        raise ValueError(f"unresolved binding path hash {path_hash}")
    path = _text(paths[path_hash], "binding path")
    if ("\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or (path and any(part in ("", ".", "..") for part in path.split("/")))):
        raise ValueError("binding path must be an unchanged root-relative Unity path")
    if zlib.crc32(path.encode("utf-8")) != path_hash:
        raise ValueError(f"binding path CRC32 does not match hash {path_hash}")
    return path


def _bindings(raw, paths, streamed_count, dense_count, total):
    result = []
    seen = set()
    cursor = 0
    for raw_binding in _array(raw, "genericBindings", MAX_BINDINGS):
        binding = _mapping(raw_binding, "generic binding")
        if (_integer(binding.get("typeID"), "binding typeID", 2**31 - 1) != 4
                or _integer(binding.get("customType"), "binding customType", 255) != 0
                or _flag(binding.get("isPPtrCurve"), "binding isPPtrCurve")):
            raise ValueError("only Transform typeID=4, customType=0, isPPtrCurve=0 is supported")
        attribute = _integer(binding.get("attribute"), "binding attribute", 2**32 - 1)
        if attribute not in _PROPERTIES:
            raise ValueError(f"unsupported Transform attribute {attribute}")
        prop, width = _PROPERTIES[attribute]
        path_hash = _integer(binding.get("path"), "binding path hash", 2**32 - 1)
        path = _resolve_path(path_hash, paths)
        identity = (path, prop)
        if identity in seen:
            raise ValueError(f"duplicate path/property binding {identity!r}")
        seen.add(identity)
        if cursor < streamed_count:
            boundary = streamed_count
        elif cursor < streamed_count + dense_count:
            boundary = streamed_count + dense_count
        else:
            boundary = total
        if cursor + width > boundary:
            raise ValueError("binding crosses a scalar storage boundary or exceeds storage")
        result.append((path, prop, width, cursor))
        cursor += width
    if cursor != total:
        raise ValueError(f"binding scalar coverage {cursor} does not match storage {total}")
    return result


def _stream_keys(words, count, start, stop, rate):
    # Uint words are bit patterns, not numeric float conversions. Validate before
    # packing so struct never receives out-of-range words or an unbounded splat.
    words = _array(words, "streamed data", MAX_STREAM_WORDS)
    buffer = bytearray(4 * len(words))
    for index, word in enumerate(words):
        struct.pack_into("<I", buffer, index * 4,
                         _integer(word, "streamed uint32 word", 2**32 - 1))
    curves = [[] for _ in range(count)]
    offset = 0
    frame_count = 0
    previous_time = None
    while offset < len(buffer):
        frame_count += 1
        if frame_count > MAX_STREAM_FRAMES:
            raise ValueError(f"streamed frames exceed limit {MAX_STREAM_FRAMES}")
        if len(buffer) - offset < _HEADER.size:
            raise ValueError("truncated streamed frame header")
        time, key_count = _HEADER.unpack_from(buffer, offset)
        offset += _HEADER.size
        if not 0 <= key_count <= count:
            raise ValueError("streamed keyCount exceeds scalar bounds")
        if key_count * _KEY.size > len(buffer) - offset:
            raise ValueError("truncated streamed key records")
        startup = time == _STARTUP_TIME
        terminal = time == math.inf
        if startup:
            if frame_count != 1:
                raise ValueError("startup -FLT_MAX sentinel must be first")
        elif terminal:
            if key_count != 0 or offset != len(buffer):
                raise ValueError("final +inf sentinel must be last and have no keys")
        elif not math.isfinite(time) or time < 0 or time > stop + _jitter(stop, rate):
            raise ValueError("streamed frame time is nonfinite or outside clip bounds")
        if previous_time is not None and time <= previous_time:
            raise ValueError("streamed frame times must be strictly increasing")
        previous_time = time
        seen = set()
        for _ in range(key_count):
            index, a, b, c, d = _KEY.unpack_from(buffer, offset)
            offset += _KEY.size
            if not 0 <= index < count:
                raise ValueError("streamed scalar index out of bounds")
            if index in seen:
                raise ValueError("duplicate streamed scalar index in frame")
            seen.add(index)
            if not all(math.isfinite(v) for v in (a, b, c, d)):
                raise ValueError("nonfinite streamed key coefficient")
            if not startup:
                curves[index].append((time, a, b, c, d))
    for index, curve in enumerate(curves):
        if not curve or curve[0][0] > start + _jitter(start, rate):
            raise ValueError(f"streamed scalar {index} lacks initial real-key coverage")
    return curves


def _normalize_quaternion(vector, previous):
    # Scaling before hypot also handles finite numbers too large/small to square.
    scale = max(abs(v) for v in vector)
    if scale == 0:
        raise ValueError("zero-length quaternion")
    scaled = [v / scale for v in vector]
    norm = math.hypot(*scaled)
    result = [v / norm for v in scaled]
    if previous is not None and sum(a * b for a, b in zip(previous, result)) < 0:
        result = [-v for v in result]
    return result


def decode_generic_clip(tree: dict, path_by_hash: dict[int, str]) -> dict:
    """Return a portable {name,duration,loop,sample_rate,times,channels} clip.

    Only complete, uncompressed generic local-position/rotation/scale bindings
    are accepted. Invalid, unsupported or oversized input raises ValueError.
    Quaternions are normalized XYZW with adjacent-sample sign continuity.

    Output times are relative to m_StartTime, at i/sample_rate <= duration;
    an off-lattice stop is never appended or substituted for the final sample.
    With dense curves, use their rate and require clip start to lie on their
    BeginTime lattice (up to bounded float32 jitter). Stream-only/constant-only
    clips use m_SampleRate. No dense interpolation is invented. Path-map keys
    are uint32 UTF-8 CRC32; full, unchanged root-relative paths are required.
    The function never mutates the input or performs I/O.
    """
    tree = _mapping(tree, "AnimationClip typetree")
    paths = _mapping(path_by_hash, "path_by_hash")
    if len(paths) > MAX_PATHS:
        raise ValueError(f"path map exceeds limit {MAX_PATHS}")
    for path_hash in paths:
        _integer(path_hash, "path map hash", 2**32 - 1)
    name = _text(tree.get("m_Name"), "m_Name")
    rate = _rate(tree.get("m_SampleRate"), "m_SampleRate")
    for flag in ("m_Legacy", "m_Compressed", "m_HasMotionFloatCurves"):
        if _flag(tree.get(flag, False), flag):
            raise ValueError(f"unsupported {flag}")
    for field in ("m_RotationCurves", "m_CompressedRotationCurves", "m_EulerCurves",
                  "m_PositionCurves", "m_ScaleCurves", "m_FloatCurves", "m_PPtrCurves"):
        if _array(tree.get(field, []), field, MAX_BINDINGS):
            raise ValueError(f"unsupported nonempty {field}")
    muscle = _mapping(tree.get("m_MuscleClip"), "m_MuscleClip")
    start = _number(muscle.get("m_StartTime"), "m_StartTime")
    stop = _number(muscle.get("m_StopTime"), "m_StopTime")
    if not 0 <= start <= stop <= MAX_TIME:
        raise ValueError(f"clip interval outside time limit [0, {MAX_TIME}]")
    duration = stop - start
    loop = _flag(muscle.get("m_LoopTime"), "m_LoopTime")
    clip = _mapping(_mapping(muscle.get("m_Clip"), "m_Clip").get("data"), "m_Clip.data")
    stream = _mapping(clip.get("m_StreamedClip"), "m_StreamedClip")
    dense = _mapping(clip.get("m_DenseClip"), "m_DenseClip")
    constant = _mapping(clip.get("m_ConstantClip"), "m_ConstantClip")
    streamed_count = _integer(stream.get("curveCount"), "streamed curveCount", MAX_SCALARS)
    dense_count = _integer(dense.get("m_CurveCount"), "dense m_CurveCount", MAX_SCALARS)
    dense_frames = _integer(dense.get("m_FrameCount"), "dense m_FrameCount", MAX_FRAMES)
    begin = _number(dense.get("m_BeginTime"), "dense m_BeginTime")
    if not 0 <= begin <= MAX_TIME:
        raise ValueError("dense BeginTime outside time bounds")
    dense_rate = _number(dense.get("m_SampleRate"), "dense m_SampleRate")
    if dense_count:
        rate = _rate(dense_rate, "dense m_SampleRate")
    elif not 0 <= dense_rate <= MAX_SAMPLE_RATE:
        raise ValueError("inactive dense rate outside rate bounds")
    constants_raw = _array(constant.get("data"), "constant data", MAX_SCALARS)
    total = streamed_count + dense_count + len(constants_raw)
    if total > MAX_SCALARS:
        raise ValueError(f"total scalar storage exceeds limit {MAX_SCALARS}")
    last_sample = math.floor(duration * rate)
    # Correct either direction of product rounding without admitting a sample
    # past stop or dropping an exactly representable lattice endpoint.
    if last_sample / rate > duration:
        last_sample -= 1
    elif (last_sample + 1) / rate <= duration:
        last_sample += 1
    frame_count = last_sample + 1
    if frame_count > MAX_FRAMES:
        raise ValueError(f"sample frames exceed limit {MAX_FRAMES}")
    if frame_count * total > MAX_OUTPUT_SCALARS:
        raise ValueError(f"sample scalar output exceeds limit {MAX_OUTPUT_SCALARS}")
    binding_constant = _mapping(tree.get("m_ClipBindingConstant"), "m_ClipBindingConstant")
    bindings = _bindings(binding_constant.get("genericBindings"), paths,
                         streamed_count, dense_count, total)
    samples_raw = _array(dense.get("m_SampleArray"), "dense samples", MAX_DENSE_VALUES)
    if dense_frames * dense_count > MAX_DENSE_VALUES:
        raise ValueError(f"dense sample storage exceeds limit {MAX_DENSE_VALUES}")
    if len(samples_raw) != dense_frames * dense_count:
        raise ValueError("dense sample array must equal frameCount * curveCount")
    dense_first = 0
    if dense_count:
        dense_first = round((start - begin) * rate)
        if abs(begin + dense_first / rate - start) > _jitter(start, rate):
            raise ValueError("clip start is not on dense BeginTime/sampleRate lattice")
        if dense_first < 0 or dense_first + last_sample >= dense_frames:
            raise ValueError("dense frame coverage does not span clip samples")
    samples = [_number(v, "dense sample") for v in samples_raw]
    constants = [_number(v, "constant sample") for v in constants_raw]
    curves = _stream_keys(stream.get("data"), streamed_count, start, stop, rate)
    curve_times = [[key[0] for key in curve] for curve in curves]
    times = [i / rate for i in range(frame_count)]
    channels = [{"path": path, "property": prop, "values": []}
                for path, prop, _, _ in bindings]
    for frame, time in enumerate(times):
        source_time = start + time
        streamed_values = []
        for curve, key_times in zip(curves, curve_times):
            index = bisect.bisect_right(key_times, source_time + _jitter(source_time, rate)) - 1
            key_time, a, b, c, d = curve[index]
            delta = max(0.0, source_time - key_time)
            value = ((a * delta + b) * delta + c) * delta + d
            if not math.isfinite(value):
                raise ValueError("nonfinite evaluated streamed value")
            streamed_values.append(value)
        dense_offset = (dense_first + frame) * dense_count
        for channel, (_, prop, width, offset) in zip(channels, bindings):
            if offset < streamed_count:
                vector = streamed_values[offset:offset + width]
            elif offset < streamed_count + dense_count:
                base = dense_offset + offset - streamed_count
                vector = samples[base:base + width]
            else:
                base = offset - streamed_count - dense_count
                vector = constants[base:base + width]
            if prop == "local_rotation":
                previous = channel["values"][-1] if channel["values"] else None
                vector = _normalize_quaternion(vector, previous)
            channel["values"].append(vector)
    return {"name": name, "duration": duration, "loop": loop,
            "sample_rate": rate, "times": times, "channels": channels}

