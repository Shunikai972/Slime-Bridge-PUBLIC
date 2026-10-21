"""UnityPy-backed engine (MIT). Handles both installs:

* SR1 (Unity 2019.4, Mono, no TypeTrees): classic serialized files. ``MonoBehaviour`` data is decoded
  with TypeTrees generated from the game's own Managed assemblies.
* SR2 (Unity 2021.3, IL2CPP, Addressables): AssetBundles resolved lazily through their CAB
  dependencies (bounded memory, LRU eviction); MonoBehaviour TypeTrees come from GameAssembly.dll +
  global-metadata.dat.

All access is read-only; UnityPy never writes to the game directories.
"""

import os
import re
import struct
from collections import OrderedDict
from pathlib import Path

from .base import EngineCapabilities, ExtractionEngine, EngineError

try:  # imported lazily so `scan` works without the dependency
    import UnityPy
except ImportError:  # pragma: no cover
    UnityPy = None

_CAB = re.compile(r'(cab-[0-9a-f]{32})', re.I)


def _norm(name):
    return os.path.basename(str(name).replace('\\', '/')).lower()


class UnityPyEngine(ExtractionEngine):
    name = 'unitypy'

    def capabilities(self):
        ver = getattr(UnityPy, '__version__', 'unknown') if UnityPy else 'not installed'
        return EngineCapabilities(
            'UnityPy', ver, 'MIT', 'Unity 3.4 - 2022.3 (validated: 2019.4.29f1, 2021.3.28f1)',
            ['Mesh', 'Texture2D', 'Material', 'Shader(meta)', 'AnimationClip', 'AnimatorController',
             'Avatar', 'AudioClip', 'GameObject/Transform/Renderers', 'ParticleSystem(typetree)',
             'MonoBehaviour (generated TypeTrees)'],
            ['MonoBehaviour decoding needs the game assemblies / IL2CPP metadata installed locally'])

    def open(self, install, inventory=None, cache_bytes=1_500_000_000):
        if UnityPy is None:
            raise EngineError('UnityPy is not installed (pip install -r requirements.txt)')
        return Session(install, inventory, cache_bytes)


class Session:
    """Loaded Unity sources with cross-file PPtr resolution and bounded memory."""

    def __init__(self, install, inventory=None, cache_bytes=1_500_000_000):
        self.install = install
        self.game = install['game']
        self.data_dir = Path(install['data_dir'])
        self.inventory = inventory or {}
        self.cache_bytes = cache_bytes
        self._cab_to_rel = {}
        if inventory:
            from ..inventory import cab_map
            self._cab_to_rel = cab_map(inventory)
        self._envs = OrderedDict()      # rel -> (env, size)
        self._by_name = {}              # normalised file/CAB name -> SerializedFile
        self._owner = {}                # id(SerializedFile) -> rel
        self._pinned = set()
        self.typetree_generator = None
        self.typetree_skipped = []

    # -- loading ---------------------------------------------------------------------------
    def ensure(self, rel, pin=False):
        """Make a source file (relative to data_dir) available; returns True if loaded."""
        if rel in self._envs:
            self._envs.move_to_end(rel)
            if pin:
                self._pinned.add(rel)
            return True
        path = self.data_dir / rel
        if not path.is_file():
            return False
        env = UnityPy.load(str(path))
        if self.typetree_generator is not None:
            env.typetree_generator = self.typetree_generator
        self._envs[rel] = (env, path.stat().st_size)
        if pin:
            self._pinned.add(rel)
        self._index(rel, env.files)
        self._evict()
        return True

    def load(self, rels, pin=True):
        for r in rels:
            if not self.ensure(r, pin=pin):
                raise EngineError(f'source file not found in install: {r}')
        return self

    def _index(self, rel, files):
        for key, f in files.items():
            self._by_name[_norm(key)] = f
            self._owner[id(f)] = rel
            sub = getattr(f, 'files', None)
            if isinstance(sub, dict):
                self._index(rel, sub)

    def _evict(self):
        total = sum(sz for _, sz in self._envs.values())
        for rel in list(self._envs):
            if total <= self.cache_bytes:
                break
            if rel in self._pinned:
                continue
            _, sz = self._envs.pop(rel)
            total -= sz
            for k in [k for k, f in self._by_name.items() if self._owner.get(id(f)) == rel]:
                f = self._by_name.pop(k)
                self._owner.pop(id(f), None)

    def file(self, name):
        f = self._by_name.get(_norm(name))
        if f is None:
            m = _CAB.search(str(name))
            if m and self._cab_to_rel:
                rel = self._cab_to_rel.get(m.group(1).lower())
                if rel and self.ensure(rel):
                    f = self._by_name.get(_norm(name)) or self._by_name.get(m.group(1).lower())
        return f

    def serialized_files(self):
        seen, out = set(), []
        for f in list(self._by_name.values()):
            if isinstance(getattr(f, 'objects', None), dict) and id(f) not in seen:
                seen.add(id(f))
                out.append(f)
        return out

    def objects(self):
        for f in self.serialized_files():
            for o in f.objects.values():
                yield o

    # -- MonoBehaviour TypeTrees -----------------------------------------------------------------
    def enable_typetrees(self):
        """Generate TypeTrees for MonoBehaviours from the game's own code (Mono assemblies for SR1,
        GameAssembly.dll + global-metadata.dat for SR2/IL2CPP) so ScriptableObjects such as
        SlimeAppearance/SlimeDefinition decode automatically. No TypeTree data is shipped."""
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        gen = TypeTreeGenerator(self.install['unity']['version'])
        root = Path(self.install['root'])
        if self.install['backend'] == 'il2cpp':
            gen.load_il2cpp((root / 'GameAssembly.dll').read_bytes(),
                            (self.data_dir / 'il2cpp_data' / 'Metadata' / 'global-metadata.dat').read_bytes())
        else:
            managed = self.data_dir / 'Managed'
            # A patched copy can hold duplicates of one assembly (e.g. Assembly-CSharp_old.dll): load the
            # live game assemblies once and skip mod loaders, which carry no serialized game types.
            for dll in sorted(managed.glob('*.dll')):
                if dll.name.lower().endswith(('_old.dll', '.bak.dll')) or dll.name.startswith(('SRML', '0Harmony')):
                    continue
                try:
                    gen.load_dll(dll.read_bytes())
                except AssertionError:
                    self.typetree_skipped.append(dll.name)
        self.typetree_generator = gen
        for env, _ in self._envs.values():
            env.typetree_generator = gen
        return gen

    def script_of(self, reader):
        """(class_name, assembly_name, namespace) of a MonoBehaviour reader, from its raw header."""
        h = monobehaviour_header(reader)
        tgt = self.resolve(reader.assets_file, *h['script'])
        if tgt is None:
            return None
        sc = tgt.read()
        return (sc.m_ClassName, sc.m_AssemblyName, getattr(sc, 'm_Namespace', ''))

    # -- reference resolution ------------------------------------------------------------------
    def resolve(self, owner_file, file_id, path_id):
        """PPtr (file_id, path_id) relative to ``owner_file`` -> ObjectReader or None.
        Never guesses: references into Unity builtin resources or missing sources return None."""
        if not path_id:
            return None
        if file_id == 0:
            return owner_file.objects.get(path_id)
        try:
            ext = owner_file.externals[file_id - 1]
        except IndexError:
            return None
        target = self.file(getattr(ext, 'path', '') or getattr(ext, 'name', ''))
        if target is None:
            return None
        return target.objects.get(path_id)

    def resolve_pptr(self, owner_file, pptr):
        """Resolve a parsed PPtr object or {'m_FileID','m_PathID'} dict."""
        if pptr is None:
            return None
        if isinstance(pptr, dict):
            return self.resolve(owner_file, pptr.get('m_FileID', 0), pptr.get('m_PathID', 0))
        fid = getattr(pptr, 'm_FileID', getattr(pptr, 'file_id', 0))
        pid = getattr(pptr, 'm_PathID', getattr(pptr, 'path_id', 0))
        return self.resolve(owner_file, fid, pid)

    def unresolved_external(self, owner_file, file_id):
        if not file_id:
            return None
        try:
            return getattr(owner_file.externals[file_id - 1], 'path', None)
        except IndexError:
            return f'<external #{file_id}>'


def monobehaviour_header(reader):
    """Read only the fixed MonoBehaviour header (no TypeTree needed): owning GameObject, enabled
    flag and script reference."""
    raw = reader.get_raw_data()
    go_fid, go_pid = struct.unpack_from('<iq', raw, 0)
    enabled = raw[12] != 0
    s_fid, s_pid = struct.unpack_from('<iq', raw, 16)
    return {'go': (go_fid, go_pid), 'enabled': enabled, 'script': (s_fid, s_pid)}
