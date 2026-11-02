"""Bounded malformed numbers must produce diagnostics, not validator exceptions."""
import json
import struct

import pytest

from bridge.core.diagnostics import validate
from bridge.meshgen import mesh
from bridge.meshgen import export_gltf as gltf


@pytest.mark.parametrize('attribute', ['positions', 'normals', 'uvs'])
@pytest.mark.parametrize('number', [10 ** 200, 10 ** 400])
def test_mesh_rejects_oversized_integer_attributes(attribute, number):
    part = mesh.MeshPart('synthetic', 'stone', [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                         [(0, 0, 1)] * 3, [(0, 0), (1, 0), (0, 1)], [0, 1, 2])
    values = getattr(part, attribute)
    values[0] = (number, *values[0][1:])
    problems = mesh.validate_mesh(part)
    assert problems and any(p['severity'] == 'error' for p in problems)
    assert all(not validate(p) for p in problems)


def glb(doc):
    payload = json.dumps(doc).encode()
    payload += b' ' * (-len(payload) % 4)
    return (struct.pack('<III', gltf.GLB_MAGIC, 2, 20 + len(payload)) +
            struct.pack('<II', len(payload), gltf.JSON_CHUNK) + payload)


@pytest.mark.parametrize('key,tail', [('translation', [0, 0]), ('scale', [1, 1]), ('rotation', [0, 0, 1])])
def test_glb_rejects_overflowing_integer_transforms(key, tail):
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': 0}],
           'nodes': [{key: [10 ** 400, *tail]}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    problems = gltf.validate_glb(glb(doc))
    assert problems and all(not validate(p) for p in problems)


def test_small_valid_mesh_and_glb_controls_still_pass():
    part = mesh.MeshPart('synthetic', 'stone', [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                         [(0, 0, 1)] * 3, [(0, 0), (1, 0), (0, 1)], [0, 1, 2])
    assert mesh.validate_mesh(part) == []
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': 0}],
           'nodes': [{'translation': [0, 0, 0]}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    assert gltf.validate_glb(glb(doc)) == []
