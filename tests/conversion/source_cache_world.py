"""Synthetic source-cache world for conversion tests: fake SR2 data + a deterministic extractor.

``FakeExtractor`` is a drop-in for the uv/UnityPy subprocess used by the real
``bridge.content.sr2_asset_extract --complete-source`` CLI: it writes the same published cache
files with the same schemas (model.json, source-animation-provenance.json, icon descriptor,
dump receipt, manifest written last), refuses a clip selection the fake species does not
reference, refuses a non-fresh output directory, and returns the same JSON summary shape.
It never reads real game data. Everything this module creates lives under the worktree's
ignored ``Lab/RuntimeArtifacts`` scratch area.
"""

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from bridge.content.canonical import sha256_text

REPO_ROOT = Path(__file__).resolve().parents[2]

CLIP_NAMES = {2170: 'Idle_Cotton_Ringtail', 2187: 'Idle v4', 2165: 'Pounce v4',
              2188: 'Bite v4', 3001: 'Slither v4'}
PARTS = ('body', 'face', 'ears', 'tail')
SOURCE_FILES = ('resources.assets', 'resources.assets.resS')


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def scratch_dir(prefix='sc-test'):
    """Unique scratch directory under the worktree Lab (auto-cleanable by the caller)."""
    root = REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
    root.mkdir(parents=True, exist_ok=True)
    out = root / f'{prefix}-{uuid.uuid4().hex[:12]}'
    out.mkdir()
    return out


def make_sr2_dir(root, marker=b'v1'):
    """Miniature read-only stand-in for SlimeRancher2_Data with the two fingerprinted files."""
    data = Path(root) / 'SlimeRancher2_Data'
    data.mkdir(parents=True, exist_ok=True)
    (data / 'resources.assets').write_bytes(b'FAKE-SR2-ASSETS\x00' + marker)
    (data / 'resources.assets.resS').write_bytes(b'FAKE-SR2-RESS\x00' + marker * 4)
    return data


def job(cache_dir, sr2_dir, clip_ids=(2170, 2187, 2165, 2188), default_clip='Idle_Cotton_Ringtail', **extra):
    value = {'canonical_id': 'slime.ringtail', 'species': 'Ringtail', 'sr2_dir': str(sr2_dir),
             'cache_dir': str(cache_dir), 'clip_ids': [int(cid) for cid in clip_ids],
             'default_clip': default_clip}
    value.update(extra)
    return value


class FakeExtractor:
    """Deterministic stand-in for the real extraction subprocess (schema-faithful, fail-closed)."""

    CONVERTER_VERSION = 5

    def __init__(self, *, clip_names=None, parts=PARTS, omit=(), write_nothing=False, fail_with=None):
        self.clip_names = dict(clip_names or CLIP_NAMES)
        self.parts = tuple(parts)
        self.omit = set(omit)
        self.write_nothing = write_nothing
        self.fail_with = fail_with
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        out = Path(request.out_dir)
        if out.exists():
            raise ValueError('output directory already exists; choose a unique new directory')
        clip_ids = list(request.clip_ids)
        for pid in clip_ids:
            if pid not in self.clip_names:
                raise ValueError(f'clip {pid} is not referenced by the source species controller/appearance')
        names = [self.clip_names[pid] for pid in clip_ids]
        if request.default_clip not in names:
            raise ValueError(f'default clip {request.default_clip!r} was not decoded for this species')
        roles = [role for role in self.parts if role not in self.omit]
        if self.write_nothing:
            return {'ok': True, 'canonical_id': request.canonical_id, 'cache_directory': str(out),
                    'parts': [], 'vertex_total': 0, 'texture_count': 0, 'materials': []}
        out.mkdir(parents=True)
        sr2 = Path(request.sr2_dir)
        source = {str(sr2 / name): sha256_file(sr2 / name) for name in SOURCE_FILES}
        files = {}

        def write(name, raw: bytes):
            (out / name).write_bytes(raw)
            files[name] = hashlib.sha256(raw).hexdigest()

        model = {
            'schema': 'bridge-native-mesh', 'schema_version': 2, 'source_game': 'SR2',
            'source_kind': 'offline-extract', 'reference_id': f'SlimeDefinition.{request.species}',
            'display_name': request.species, 'visual_root': f'{request.species}Root', 'prefab_scale': 1.0,
            'skeleton': {'bone_count': 2, 'bones': [{'path': 'root'}, {'path': 'root/hip'}]},
            'skinned_renderers': [{'enabled': True, 'role': role, 'path': f'root/{role}',
                                   'mesh_name': f'LF_{request.species.lower()}_{role}_LOD0',
                                   'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                                   'triangles': [0, 1, 2],
                                   'materials': [f'{request.species}{role.title()}Mat']}
                                  for role in roles],
            'static_renderers': [],
            'source_animation': {'default_clip': request.default_clip,
                                 'clips': [{'name': self.clip_names[pid], 'duration': 1.0, 'loop': True,
                                            'sample_rate': 30, 'times': [0.0, 1.0], 'channels': []}
                                           for pid in clip_ids]},
        }
        write('model.json', (json.dumps(model, indent=1, sort_keys=True) + '\n').encode('utf-8'))
        write('tex-body.png', b'\x89PNG\r\n\x1a\n' + b'body-texture')
        icon_name = f'icon{request.species}.source.png'
        write(icon_name, b'\x89PNG\r\n\x1a\n' + b'icon-texture')
        write('icon-descriptor.json', (json.dumps(
            {'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': True, 'file': icon_name,
             'texture_size': [8, 8], 'source_sprite_path_id': 3052,
             'source_assets_sha256': source[str(sr2 / 'resources.assets')]}, indent=1, sort_keys=True) + '\n'
        ).encode('utf-8'))
        write('source-animation-provenance.json', (json.dumps(
            {'selection': 'explicit-source-clip-ids', 'runtime_graph_reproduced': False,
             'source_references': {'prefab_id': 5092},
             'clips': [{'source_path_id': pid, 'name': self.clip_names[pid],
                        'source_object_sha256': sha256_text(f'{request.species}:{pid}')} for pid in clip_ids],
             'limitations': ['synthetic converter test double'], 'source_files': source},
            indent=1, sort_keys=True) + '\n').encode('utf-8'))
        write('dump-receipt.json', (json.dumps(
            {'schema': 'bridge-native-mesh-receipt', 'schema_version': 2, 'target': request.species,
             'reference_id': f'SlimeDefinition.{request.species}', 'model': 'model.json',
             'model_sha256': files['model.json'], 'vertex_total': 3 * len(roles), 'source': source},
            indent=1, sort_keys=True) + '\n').encode('utf-8'))
        manifest = {'schema': 'bridge-asset-cache-manifest', 'schema_version': 1,
                    'canonical_id': request.canonical_id, 'converter_version': self.CONVERTER_VERSION,
                    'source_game': 'SR2', 'source_run_id': 'offline-fake', 'files': dict(files),
                    'published_utc': '2026-01-01T00:00:00Z', 'icon': {'file': icon_name}}
        (out / 'cache-manifest.json').write_bytes((json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode('utf-8'))
        return {'ok': True, 'canonical_id': request.canonical_id, 'cache_directory': str(out),
                'parts': [{'role': role} for role in roles], 'vertex_total': 3 * len(roles),
                'texture_count': 2, 'materials': [f'{request.species}{role.title()}Mat' for role in roles]}


def cleanup(directory):
    shutil.rmtree(Path(directory), ignore_errors=True)
