"""Index of an AssetRipper export restricted to animation assets, plus the logic that decides which
bundles/files must be exported for a game. Results are cached under the work directory keyed by the
(size, mtime) of the exported source files, so unchanged installs are never re-exported."""

import hashlib
import json
import re
import shutil
from pathlib import Path

from .engines import assetripper
from .extract import animation as A

KEEP_DIRS = ('AnimationClip', 'AnimatorController', 'AnimatorOverrideController', 'Avatar')


def _source_key(paths):
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        st = Path(p).stat()
        h.update(f'{p}|{st.st_size}|{st.st_mtime_ns}\n'.encode())
    return h.hexdigest()[:20]


def animation_sources(install, inventory=None):
    """Files that must be loaded into AssetRipper to export every clip/controller of the install."""
    data = Path(install['data_dir'])
    if install['backend'] == 'il2cpp':
        rels = []
        for rel, rec in (inventory or {}).items():
            types = {o[2] for o in rec['objects']}
            if types & {'AnimationClip', 'AnimatorController', 'AnimatorOverrideController'}:
                rels.append(rel)
        rels += ['globalgamemanagers']
    else:
        rels = ['globalgamemanagers', 'globalgamemanagers.assets', 'resources.assets'] + \
               [h['name'] for h in install['serialized_files'] if h['name'].startswith(('sharedassets', 'level'))]
        rels = [r for r in dict.fromkeys(rels) if (data / r).is_file() and not r.startswith('level')]
    return [data / r for r in rels if (data / r).is_file()]


def ensure_export(install, work_root, inventory=None, log=print):
    """Return the AnimProject for this install, exporting through AssetRipper only when needed."""
    sources = animation_sources(install, inventory)
    key = _source_key(sources)
    base = Path(work_root) / 'animation' / install['game']
    done = base / key / '.complete'
    proj = base / key / 'ExportedProject'
    if not done.is_file():
        if not assetripper.available():
            raise RuntimeError('AssetRipper not found: set SBCONV_ASSETRIPPER (animation export engine)')
        if base.exists():
            shutil.rmtree(base)
        tmp = base / key / '_raw'
        tmp.parent.mkdir(parents=True, exist_ok=True)
        log(f'[animation] exporting {len(sources)} source file(s) with AssetRipper...')
        with assetripper.AssetRipperServer(log_dir=Path(work_root) / 'logs') as ar:
            ar.load(sources)
            ar.export_project(tmp)
        raw = tmp / 'ExportedProject' / 'Assets'
        proj.mkdir(parents=True, exist_ok=True)
        for sub in KEEP_DIRS:
            if (raw / sub).is_dir():
                shutil.copytree(raw / sub, proj / 'Assets' / sub)
        # metas of every kept asset are needed for guid resolution
        shutil.rmtree(tmp, ignore_errors=True)
        done.write_text(json.dumps({'sources': [str(s) for s in sources]}), encoding='utf-8')
    return AnimProject(proj)


class AnimProject:
    def __init__(self, root):
        self.root = Path(root)
        self.guids = A.guid_map(self.root)
        self._clips, self._controllers = {}, {}
        self.by_name = {'clip': {}, 'controller': {}, 'override': {}}
        for sub, kind, ext in (('AnimationClip', 'clip', '.anim'), ('AnimatorController', 'controller', '.controller'),
                               ('AnimatorOverrideController', 'override', '.overrideController')):
            d = self.root / 'Assets' / sub
            if d.is_dir():
                for p in sorted(d.glob(f'*{ext}')):
                    name = re.sub(r'_\d+$', '', p.stem)  # AssetRipper suffixes duplicates with _N
                    self.by_name[kind].setdefault(name, []).append(p)
                    self.by_name[kind].setdefault(p.stem, []).append(p)

    def clip(self, name_or_asset):
        key = name_or_asset
        if key in self._clips:
            return self._clips[key]
        path = self.root / key if key.endswith('.anim') else None
        if path is None:
            cands = self.by_name['clip'].get(key, [])
            path = cands[0] if cands else None
        clip = A.parse_clip(path) if path else None
        self._clips[key] = clip
        return clip

    def controller(self, name):
        if name in self._controllers:
            return self._controllers[name]
        cands = self.by_name['controller'].get(name, [])
        ctrl = A.parse_controller(cands[0], self.guids) if cands else None
        if ctrl is not None and len(cands) > 1:
            ctrl['ambiguous_duplicates'] = [c.name for c in cands]
        self._controllers[name] = ctrl
        return ctrl

    def clip_assets(self, controller):
        """Asset paths of every clip a controller references (recursing through blend trees)."""
        out = []

        def visit(m):
            if not m:
                return
            if m['kind'] == 'clip':
                out.append(m['asset'])
            elif m['kind'] == 'blendtree':
                for c in m['tree']['children']:
                    visit(c['motion'])
        for layer in controller['layers']:
            for s in layer['states']:
                visit(s['motion'])
        return list(dict.fromkeys(out))
