"""Synthetic generic Unity clips only; no game files or asset APIs are needed."""

import copy
import importlib
import json
import math
import struct
import zlib

import pytest

PATH = "bone_root/Slime/éars"
PATH_HASH = zlib.crc32(PATH.encode("utf-8"))
PATHS = {PATH_HASH: PATH}


def _decode(tree, paths=None):
    decoder = importlib.import_module("bridge.content.unity_animation")
    return decoder.decode_generic_clip(tree, PATHS if paths is None else paths)


def _binding(attribute, path=PATH_HASH):
    return {"path": path, "attribute": attribute, "typeID": 4,
            "customType": 0, "isPPtrCurve": 0}


def _words(*frames):
    buffer = bytearray()
    for time, keys in frames:
        buffer.extend(struct.pack("<fi", time, len(keys)))
        for key in keys:
            buffer.extend(struct.pack("<iffff", *key))
    return list(struct.unpack(f"<{len(buffer) // 4}I", buffer))


def _tree(bindings=None, *, streamed=0, words=(), dense=0, frames=0,
          samples=(), constants=(1, 2, -3), rate=2, begin=0, start=0, stop=1):
    return {
        "m_Name": "Synthetic", "m_Legacy": False, "m_Compressed": False,
        "m_SampleRate": rate,
        "m_MuscleClip": {
            "m_StartTime": start, "m_StopTime": stop, "m_LoopTime": True,
            "m_Clip": {"data": {
                "m_StreamedClip": {"curveCount": streamed, "data": list(words)},
                "m_DenseClip": {"m_FrameCount": frames, "m_CurveCount": dense,
                                "m_SampleRate": rate, "m_BeginTime": begin,
                                "m_SampleArray": list(samples)},
                "m_ConstantClip": {"data": list(constants)},
            }},
        },
        "m_ClipBindingConstant": {
            "genericBindings": bindings if bindings is not None else [_binding(1)],
        },
    }


def _data(tree):
    return tree["m_MuscleClip"]["m_Clip"]["data"]


def test_constant_clip_preserves_full_paths_unity_coordinates_and_input():
    tree = _tree([_binding(1), _binding(2), _binding(3)],
                 constants=(1, 2, -3, 0, 0, 0, 2, 1, 1.5, 2))
    before = copy.deepcopy(tree)
    result = _decode(tree)
    assert result == {
        "name": "Synthetic", "duration": 1.0, "loop": True, "sample_rate": 2.0,
        "times": [0.0, 0.5, 1.0],
        "channels": [
            {"path": PATH, "property": "local_position", "values": [[1, 2, -3]] * 3},
            {"path": PATH, "property": "local_rotation", "values": [[0, 0, 0, 1]] * 3},
            {"path": PATH, "property": "local_scale", "values": [[1, 1.5, 2]] * 3},
        ],
    }
    assert tree == before
    json.dumps(result, allow_nan=False)


def test_dense_clip_uses_its_lattice_and_global_constant_offset():
    tree = _tree([_binding(1), _binding(3)], dense=3, frames=4,
                 samples=(0, 1, 2, 3, 4, 5, 6, 7, 8, 999, 999, 999),
                 constants=(1, 2, 3), stop=1.1)
    result = _decode(tree)
    assert result["duration"] == 1.1
    assert result["times"] == [0, 0.5, 1]
    assert result["channels"][0]["values"] == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    assert result["channels"][1]["values"] == [[1, 2, 3]] * 3


def test_dense_start_is_rebased_without_guessing_subframe_interpolation():
    tree = _tree(dense=3, frames=4, samples=range(12), constants=(),
                 start=0.5, stop=1.5, begin=0)
    result = _decode(tree)
    assert result["duration"] == 1
    assert result["times"] == [0, 0.5, 1]
    assert result["channels"][0]["values"] == [[3, 4, 5], [6, 7, 8], [9, 10, 11]]


def test_dense_lattice_does_not_replace_last_time_with_authored_stop():
    tree = _tree(dense=3, frames=4, samples=range(12), constants=(),
                 stop=1.0000002)
    assert _decode(tree)["times"] == [0, 0.5, 1]
    tree["m_MuscleClip"]["m_StopTime"] = 0.9999999
    assert _decode(tree)["times"] == [0, 0.5]


def test_sparse_stream_cubics_steps_sentinels_and_storage_regions():
    startup = -3.4028234663852886e38
    words = _words(
        (startup, [(i, 0, 0, 0, 999) for i in range(3)]),
        (0, [(0, 1, 2, 3, 4), (1, 0, 0, 0, 1), (2, 0, 0, -1, 0)]),
        (0.5, [(2, 0, 0, 2, -0.5)]),
        (1, [(0, 0, 0, 0, 10), (1, 0, 0, 0, 2), (2, 0, 0, 0, 0.5)]),
        (math.inf, []),
    )
    tree = _tree([_binding(1), _binding(2), _binding(3)], streamed=3, words=words,
                 dense=4, frames=3, samples=(0, 0, 0, 2, 0, 0, 0, -3, 0, 0, 0, 4),
                 constants=(1, 1, 1))
    result = _decode(tree)
    assert result["channels"][0]["values"] == [
        [4, 1, 0], [6.125, 1, -0.5], [10, 2, 0.5],
    ]
    assert result["channels"][1]["values"] == [[0, 0, 0, 1]] * 3
    assert result["channels"][2]["values"] == [[1, 1, 1]] * 3


def test_stream_without_sentinels_evaluates_relative_to_each_key():
    words = _words(
        (0, [(0, 0, 0, 1, 0), (1, 0, 0, 0, 2), (2, 0, 0, 0, -3)]),
        (0.5, [(0, 0, 0, 2, 0.5)]),
    )
    result = _decode(_tree(streamed=3, words=words, constants=()))
    assert result["channels"][0]["values"] == [[0, 2, -3], [0.5, 2, -3], [1.5, 2, -3]]


@pytest.mark.parametrize("field,value", [
    ("attribute", 4), ("attribute", True), ("typeID", 137),
    ("customType", 1), ("isPPtrCurve", 1), ("path", -1),
    ("path", 2**32), ("path", True), ("typeID", "4"),
])
def test_rejects_unsupported_or_invalid_binding_fields(field, value):
    binding = _binding(1)
    binding[field] = value
    with pytest.raises(ValueError):
        _decode(_tree([binding]))


@pytest.mark.parametrize("changes", [
    ("m_SampleRate", 0), ("m_SampleRate", -1), ("m_SampleRate", math.nan),
    ("m_SampleRate", math.inf), ("m_SampleRate", True), ("m_SampleRate", "2"),
    ("m_SampleRate", 2000), ("m_Name", None), ("m_Name", "x" * 4097),
    ("m_Legacy", True), ("m_Compressed", True),
    ("m_PositionCurves", [{}]), ("m_HasMotionFloatCurves", True),
])
def test_rejects_invalid_or_unsupported_clip_metadata(changes):
    tree = _tree()
    tree[changes[0]] = changes[1]
    with pytest.raises(ValueError):
        _decode(tree)


@pytest.mark.parametrize("field,value", [
    ("m_StartTime", -0.5), ("m_StartTime", 2), ("m_StartTime", True),
    ("m_StopTime", math.inf), ("m_StopTime", math.nan),
    ("m_StopTime", 3601), ("m_LoopTime", "false"),
])
def test_rejects_invalid_time_interval_or_loop(field, value):
    tree = _tree()
    tree["m_MuscleClip"][field] = value
    with pytest.raises(ValueError):
        _decode(tree)


@pytest.mark.parametrize("tree", [None, {}, {"m_MuscleClip": []}])
def test_rejects_invalid_typetree_shape_with_value_error(tree):
    with pytest.raises(ValueError):
        _decode(tree)


def test_rejects_missing_data_nesting_instead_of_guessing():
    tree = _tree()
    tree["m_MuscleClip"]["m_Clip"] = _data(tree)
    with pytest.raises(ValueError):
        _decode(tree)


@pytest.mark.parametrize("constants", [
    (1, 2), (1, 2, 3, 4), (math.nan, 2, 3), (1, math.inf, 3),
    (1, "2", 3), (1, True, 3),
])
def test_rejects_incomplete_extra_or_nonfinite_constant_scalars(constants):
    with pytest.raises(ValueError):
        _decode(_tree(constants=constants))


def test_rejects_binding_crossing_storage_region_boundary():
    words = _words((0, [(0, 0, 0, 0, 1)]))
    with pytest.raises(ValueError):
        _decode(_tree(streamed=1, words=words, constants=(2, 3)))


def test_rejects_duplicate_path_property_bindings():
    with pytest.raises(ValueError):
        _decode(_tree([_binding(1), _binding(1)], constants=(1, 2, 3, 4, 5, 6)))


@pytest.mark.parametrize("paths", [{}, None, [], {PATH_HASH: 123},
                                  {PATH_HASH: "different_path"}])
def test_rejects_unresolved_invalid_or_hash_mismatched_paths(paths):
    decoder = importlib.import_module("bridge.content.unity_animation")
    with pytest.raises(ValueError):
        decoder.decode_generic_clip(_tree(), paths)


@pytest.mark.parametrize("path", ["/absolute", "a//b", "a/../b", "a/./b",
                                  "a\\b", "a/", "a\x00b", "a" * 4097])
def test_rejects_nonportable_paths_without_normalizing(path):
    key = zlib.crc32(path.encode("utf-8"))
    with pytest.raises(ValueError):
        _decode(_tree([_binding(1, key)]), {key: path})


def test_empty_root_path_and_zero_duration_are_valid():
    result = _decode(_tree([_binding(1, 0)], stop=0), {0: ""})
    assert result["times"] == [0]
    assert result["channels"][0]["path"] == ""


def test_rejects_zero_length_quaternion():
    with pytest.raises(ValueError, match="quaternion"):
        _decode(_tree([_binding(2)], constants=(0, 0, 0, 0)))


@pytest.mark.parametrize("field,value", [
    ("m_FrameCount", 0), ("m_FrameCount", -1), ("m_FrameCount", 2.0),
    ("m_CurveCount", -1), ("m_CurveCount", True), ("m_SampleRate", 0),
    ("m_BeginTime", 0.1), ("m_BeginTime", math.inf),
    ("m_SampleArray", list(range(8))),
    ("m_SampleArray", [math.nan] + list(range(1, 9))),
])
def test_rejects_bad_dense_shape_counts_lattice_or_values(field, value):
    tree = _tree(dense=3, frames=3, samples=range(9), constants=())
    _data(tree)["m_DenseClip"][field] = value
    with pytest.raises(ValueError):
        _decode(tree)


def test_rejects_missing_dense_frame_for_last_sample():
    with pytest.raises(ValueError):
        _decode(_tree(dense=3, frames=2, samples=range(6), constants=()))


def test_rejects_nan_even_in_unused_guard_dense_frame():
    with pytest.raises(ValueError):
        _decode(_tree(dense=3, frames=4, samples=list(range(11)) + [math.nan],
                      constants=()))


_VALID_KEYS = [(i, 0, 0, 0, i + 1) for i in range(3)]
_STARTUP = -3.4028234663852886e38


@pytest.mark.parametrize("words", [
    _words((0, _VALID_KEYS))[:-1],
    _words((0, _VALID_KEYS)) + [0],
    [0, 0xFFFFFFFF], [0, 4],
    _words((0, [_VALID_KEYS[0], _VALID_KEYS[0], _VALID_KEYS[2]])),
    _words((0, [(-1, 0, 0, 0, 1)])),
    _words((0, [(3, 0, 0, 0, 1)])),
    _words((0, [(0, math.nan, 0, 0, 1)])),
    _words((0, [(0, 0, math.inf, 0, 1)])),
    _words((math.nan, _VALID_KEYS)),
    _words((-math.inf, _VALID_KEYS)),
    _words((math.inf, _VALID_KEYS)),
    _words((0.5, _VALID_KEYS), (0, _VALID_KEYS)),
    _words((0, _VALID_KEYS), (0, _VALID_KEYS)),
    _words((0, _VALID_KEYS), (math.inf, []), (1, _VALID_KEYS)),
    _words((0, _VALID_KEYS), (_STARTUP, _VALID_KEYS)),
    _words((_STARTUP, _VALID_KEYS), (math.inf, [])),
    _words((0.5, _VALID_KEYS)),
    _words((-0.5, _VALID_KEYS)),
    _words((0, _VALID_KEYS), (2, _VALID_KEYS)),
    _words((0, _VALID_KEYS[:2])),
    [-1], [2**32], [0.0], [True],
])
def test_rejects_malformed_unordered_nonfinite_or_incomplete_stream(words):
    with pytest.raises(ValueError):
        _decode(_tree(streamed=3, words=words, constants=()))


@pytest.mark.parametrize("count", [-1, True, 3.0])
def test_rejects_invalid_streamed_curve_count(count):
    tree = _tree()
    _data(tree)["m_StreamedClip"]["curveCount"] = count
    with pytest.raises(ValueError):
        _decode(tree)


@pytest.mark.parametrize("cap,limit,tree", [
    ("MAX_BINDINGS", 0, _tree()),
    ("MAX_SCALARS", 2, _tree()),
    ("MAX_STREAM_WORDS", 1, _tree(streamed=3, words=_words((0, _VALID_KEYS)), constants=())),
    ("MAX_STREAM_FRAMES", 0, _tree(streamed=3, words=_words((0, _VALID_KEYS)), constants=())),
    ("MAX_FRAMES", 2, _tree()),
    ("MAX_DENSE_VALUES", 8, _tree(dense=3, frames=3, samples=range(9), constants=())),
    ("MAX_OUTPUT_SCALARS", 8, _tree()),
    ("MAX_PATHS", 0, _tree()),
])
def test_caps_are_checked_before_large_expansion(monkeypatch, cap, limit, tree):
    decoder = importlib.import_module("bridge.content.unity_animation")
    monkeypatch.setattr(decoder, cap, limit)
    with pytest.raises(ValueError, match="limit"):
        decoder.decode_generic_clip(tree, PATHS)


def test_dense_lattice_accepts_only_bounded_float32_begin_jitter():
    tree = _tree(dense=3, frames=3, samples=range(9), constants=(), begin=1e-8)
    assert _decode(tree)["channels"][0]["values"] == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]


def test_stream_lattice_accepts_only_bounded_float32_key_jitter():
    words = _words((1e-8, _VALID_KEYS), (0.50000001, [(0, 0, 0, 0, 9)]))
    assert _decode(_tree(streamed=3, words=words, constants=()))["channels"][0]["values"] == [
        [1, 2, 3], [9, 2, 3], [9, 2, 3],
    ]


def test_quaternion_normalization_handles_finite_large_components():
    result = _decode(_tree([_binding(2)], constants=(1e300, 0, 0, 1e300)))
    vector = result["channels"][0]["values"][0]
    assert vector == pytest.approx([math.sqrt(0.5), 0, 0, math.sqrt(0.5)])
    assert math.hypot(*vector) == pytest.approx(1)


def test_exact_lattice_endpoint_is_not_lost_to_multiplication_rounding():
    result = _decode(_tree(rate=30, stop=4.1))
    assert len(result["times"]) == 124
    assert result["times"][-1] == 123 / 30
    assert result["times"][-1] == result["duration"]


def test_multiplication_rounding_cannot_admit_time_after_stop():
    stop = math.nextafter(23 / 30, -math.inf)
    result = _decode(_tree(rate=30, stop=stop))
    assert len(result["times"]) == 23
    assert all(time <= stop for time in result["times"])


@pytest.mark.parametrize("change", ["array", "overflow", "unicode", "begin", "inactive_rate"])
def test_extra_malformed_scalar_and_text_guards(change):
    tree = _tree()
    if change == "array":
        _data(tree)["m_ConstantClip"]["data"] = (1, 2, 3)
    elif change == "overflow":
        _data(tree)["m_ConstantClip"]["data"] = [10**1000, 2, 3]
    elif change == "unicode":
        tree["m_Name"] = "\ud800"
    elif change == "begin":
        _data(tree)["m_DenseClip"]["m_BeginTime"] = -1
    else:
        _data(tree)["m_DenseClip"]["m_SampleRate"] = -1
    with pytest.raises(ValueError):
        _decode(tree)


def test_caps_apply_to_combined_scalar_counts_and_declared_dense_product(monkeypatch):
    decoder = importlib.import_module("bridge.content.unity_animation")
    monkeypatch.setattr(decoder, "MAX_SCALARS", 5)
    with pytest.raises(ValueError, match="limit"):
        _decode(_tree([_binding(1), _binding(3)], dense=3, frames=3,
                      samples=range(9), constants=(1, 1, 1)))
    monkeypatch.setattr(decoder, "MAX_SCALARS", 16384)
    monkeypatch.setattr(decoder, "MAX_DENSE_VALUES", 8)
    with pytest.raises(ValueError, match="limit"):
        _decode(_tree(dense=3, frames=3, samples=(), constants=()))


def test_dense_rate_is_authoritative_and_inactive_dense_rate_can_be_zero():
    tree = _tree(dense=3, frames=3, samples=range(9), constants=())
    tree["m_SampleRate"] = 30
    assert _decode(tree)["sample_rate"] == 2
    tree = _tree()
    _data(tree)["m_DenseClip"]["m_SampleRate"] = 0
    assert _decode(tree)["sample_rate"] == 2


def test_dense_binding_cannot_cross_into_constants():
    with pytest.raises(ValueError, match="boundary"):
        _decode(_tree(dense=1, frames=3, samples=(1, 2, 3), constants=(4, 5)))


def test_streamed_quaternion_uses_component_cubic_then_normalizes():
    keys = [(0, 0, 0, 1, 0), (1, 0, 0, 0, 0),
            (2, 0, 0, 0, 0), (3, 0, 0, 0, 1)]
    result = _decode(_tree([_binding(2)], streamed=4,
                          words=_words((0, keys)), constants=()))
    assert result["channels"][0]["values"][1] == pytest.approx(
        [0.5 / math.sqrt(1.25), 0, 0, 1 / math.sqrt(1.25)])


def test_samples_do_not_alias_each_other_or_input_vectors():
    tree = _tree()
    result = _decode(tree)
    result["channels"][0]["values"][0][0] = 999
    assert result["channels"][0]["values"][1] == [1, 2, -3]
    assert _data(tree)["m_ConstantClip"]["data"] == [1, 2, -3]
