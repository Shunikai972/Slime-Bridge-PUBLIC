"""The model publisher also imports and scrubs metadata in a public, code-only checkout."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import builtins
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
mode, local_path = sys.argv[2:]
original = builtins.__import__

def import_boundary(name, *args, **kwargs):
    if name == 'tools.release.sync_public':
        missing = name if mode == 'missing-private' else 'policy_dependency'
        raise ModuleNotFoundError('missing optional distribution policy', name=missing)
    return original(name, *args, **kwargs)

builtins.__import__ = import_boundary
try:
    from tools.release import models_branch
except ModuleNotFoundError as exc:
    if mode != 'broken-private' or exc.name != 'policy_dependency':
        raise
    sys.exit(0)
assert mode == 'missing-private', 'An unrelated import failure was swallowed'
builtins.__import__ = original

binary = b'\x01\x02\x03\x04'
def make_glb(doc):
    text = json.dumps(doc).encode()
    text += b' ' * (-len(text) % 4)
    return (struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary)) +
            struct.pack('<II', len(text), 0x4E4F534A) + text +
            struct.pack('<II', len(binary), 0x004E4942) + binary)

with tempfile.TemporaryDirectory() as tmp:
    src, dst = Path(tmp, 'input.glb'), Path(tmp, 'output.glb')
    doc = {'asset': {'version': '2.0', 'extras': {'source': local_path}}, 'nodes': [{'name': 'BODY'}]}
    raw = make_glb(doc)
    src.write_bytes(raw)
    assert models_branch.clean_glb(src, dst) == 1
    out = dst.read_bytes()
    length = struct.unpack_from('<I', out, 12)[0]
    cleaned = json.loads(out[20:20 + length])
    assert cleaned['asset']['extras'] == {}
    assert cleaned['nodes'] == [{'name': 'BODY'}]
    assert out.endswith(binary)
    assert struct.unpack_from('<I', out, 8)[0] == len(out)
    assert src.read_bytes() == raw

    # Unsafe names outside metadata are refused, rather than rewriting rig/node identity.
    dst.unlink()
    doc = {'asset': {'version': '2.0'}, 'nodes': [{'name': local_path}]}
    src.write_bytes(make_glb(doc))
    try:
        models_branch.clean_glb(src, dst)
    except ValueError:
        pass
    else:
        raise AssertionError('An unsafe node name was accepted')
    assert not dst.exists()

    doc = {'asset': {'version': '2.0', 'extras': {'label': 'released-model'}}, 'nodes': [{'name': 'BODY'}]}
    raw = make_glb(doc)
    src.write_bytes(raw)
    assert models_branch.clean_glb(src, dst) == 0
    assert dst.read_bytes() == raw
'''


@pytest.mark.parametrize('local_path', [
    'Z:/Artist/Downloads/source.blend',
    r'C:\Users\Artist\Documents\source.blend',
    '/home/artist/source.blend',
    '/Users/artist/source.blend',
])
def test_public_model_scrubber_without_private_distribution_tool(local_path):
    proc = subprocess.run([sys.executable, '-I', '-c', SCRIPT, str(ROOT), 'missing-private', local_path],
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_unrelated_private_policy_import_error_is_not_hidden():
    proc = subprocess.run([sys.executable, '-I', '-c', SCRIPT, str(ROOT), 'broken-private', 'unused'],
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
