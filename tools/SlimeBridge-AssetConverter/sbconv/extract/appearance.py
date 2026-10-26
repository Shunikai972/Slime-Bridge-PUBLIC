"""Extract one species (SlimeDefinition) into the game-neutral IR package.

Works for SR1 and SR2 because both games model a slime the same way:

  SlimeDefinition -> prefab (skeleton + Animator + gameplay MonoBehaviours, SlimeAppearanceApplicator.Bones)
                  -> SlimeAppearance (structures -> SlimeAppearanceElement -> per-LOD SlimeAppearanceObject
                     -> part GameObject with SkinnedMeshRenderer; face; palette; extras)

Mesh bone index i of a part is bone enum ``AttachedBones[i]``, resolved to the prefab bone through the
applicator's ``Bones`` table. Nothing here is species-specific.
"""

from ..catalog import fld
from . import prefab as pf
from . import materials as mt
from .mesh import decode_mesh, validate_mesh, MeshDecodeError
from ..ir.store import pack_mesh, sha16


class ExtractionProblem:
    """Collects non-fatal problems so a conversion can be INCOMPLETE rather than silently wrong."""

    def __init__(self):
        self.items = []

    def add(self, level, code, message, **extra):
        self.items.append({'level': level, 'code': code, 'message': message, **extra})


def _reader_from_key(session, key):
    if not key:
        return None
    fn, pid = key.rsplit('#', 1)
    f = session.file(fn)
    return f.objects.get(int(pid)) if f is not None else None


def resolve_bone_slots(attached, bind_poses, rel_world, tol=1e-3):
    """Mesh bone slot i must be the skeleton node whose rest matrix (relative to the Appearance root) equals
    inverse(bindpose_i). ``attached`` (declared AttachedBones, as node indices) only supplies the candidates and
    their preference order. Returns (nodes_per_slot | None, method)."""
    import numpy as np
    n = len(bind_poses)
    wants = [np.linalg.inv(np.asarray(bp, dtype=float)) for bp in bind_poses]
    close = lambda node, want: node is not None and np.abs(rel_world[node] - want).max() < tol
    if len(attached) == n and all(close(attached[i], wants[i]) for i in range(n)):
        return list(attached), 'declared'
    used, out = set(), []
    for want in wants:
        cand = [b for b in attached if b is not None and b not in used and close(b, want)]
        if not cand:
            return None, 'unresolved'
        out.append(cand[0])
        used.add(cand[0])
    return out, 'bindpose_matched'


class SpeciesExtractor:
    def __init__(self, session, install, writer, problems=None):
        self.s = session
        self.install = install
        self.w = writer
        self.problems = problems or ExtractionProblem()
        self._mesh_files = {}
        self._tex_files = {}
        self._materials = {}
        self._bind = {}

    # -- helpers ---------------------------------------------------------------------------------
    def _mesh(self, key):
        if key in self._mesh_files:
            return self._mesh_files[key]
        rd = _reader_from_key(self.s, key)
        if rd is None:
            self.problems.add('error', 'MESH_UNRESOLVED', f'mesh {key} not found', ref=key)
            self._mesh_files[key] = None
            return None
        try:
            md = decode_mesh(rd)
        except MeshDecodeError as e:
            self.problems.add('error', 'MESH_DECODE', str(e), ref=key)
            self._mesh_files[key] = None
            return None
        self._bind[key] = md.bind_poses
        probs = validate_mesh(md)
        for p in probs:
            self.problems.add('warning', 'MESH_CHECK', f'{md.name}: {p}', ref=key)
        raw = pack_mesh(md)
        rel = f'meshes/{sha16(raw)}.sbmesh'
        self.w.put_bytes(rel, raw)
        entry = {'file': rel, 'name': md.name, 'vertex_count': md.vertex_count,
                 'submeshes': len(md.submeshes), 'triangles': sum(len(sm['indices']) // 3 for sm in md.submeshes if sm['topology'] == 0),
                 'has_normals': md.normals is not None, 'has_tangents': md.tangents is not None,
                 'uv_channels': sorted(md.uvs), 'has_colors': md.colors is not None,
                 'skinned': md.bone_weights is not None, 'bind_pose_count': 0 if md.bind_poses is None else len(md.bind_poses),
                 'content_sha256': md.content_hash(), 'source': key, 'problems': probs}
        self._mesh_files[key] = entry
        return entry

    def _texture(self, key):
        if key in self._tex_files:
            return self._tex_files[key]
        rd = _reader_from_key(self.s, key)
        if rd is None:
            self.problems.add('error', 'TEXTURE_UNRESOLVED', f'texture {key} not found', ref=key)
            self._tex_files[key] = None
            return None
        if rd.type.name != 'Texture2D':
            self.problems.add('warning', 'TEXTURE_TYPE', f'{key} is {rd.type.name}; not exported', ref=key)
            self._tex_files[key] = {'source': key, 'type': rd.type.name, 'file': None}
            return self._tex_files[key]
        try:
            png, info = mt.decode_texture(self.s, rd)
        except Exception as e:
            self.problems.add('error', 'TEXTURE_DECODE', f'{key}: {type(e).__name__}: {e}', ref=key)
            self._tex_files[key] = None
            return None
        rel = f'textures/{sha16(png)}.png'
        self.w.put_bytes(rel, png)
        info.update({'file': rel, 'source': key})
        info.pop('png_bytes', None)
        self._tex_files[key] = info
        return info

    def _material(self, key):
        if not key:
            return None
        if key in self._materials:
            return self._materials[key]
        rd = _reader_from_key(self.s, key)
        if rd is None:
            self.problems.add('error', 'MATERIAL_UNRESOLVED', f'material {key} not found', ref=key)
            self._materials[key] = None
            return None
        m = mt.read_material(self.s, rd)
        if m.get('shader_unresolved'):
            self.problems.add('warning', 'SHADER_UNRESOLVED', f'{m["name"]}: shader lives in {m["shader_unresolved"]}', ref=key)
        for prop, t in m['textures'].items():
            if t.get('texture'):
                info = self._texture(t['texture'])
                t['file'] = info.get('file') if info else None
                if info is None:
                    self.problems.add('error', 'TEXTURE_MISSING', f'{m["name"]}.{prop} texture unavailable', ref=key)
            elif t.get('unresolved'):
                self.problems.add('warning', 'TEXTURE_EXTERNAL', f'{m["name"]}.{prop} -> {t["unresolved"]}', ref=key)
        self._materials[key] = m
        return m

    # -- skeleton ----------------------------------------------------------------------------------
    def _applicator(self, tree):
        for n in tree['nodes']:
            for c in n['components']:
                if c['type'] == 'MonoBehaviour' and c.get('script') == 'SlimeAppearanceApplicator':
                    return n, c
        return None, None

    def _bone_table(self, applicator_ref, tree):
        rd = _reader_from_key(self.s, applicator_ref)
        d = rd.parse_as_dict()
        go_to_node = {n['ref']: i for i, n in enumerate(tree['nodes'])}
        table = {}
        for b in d.get('Bones', []):
            go = self.s.resolve_pptr(rd.assets_file, b['BoneObject'])
            table[int(b['Bone'])] = go_to_node.get(pf.ref_key(go))
        root_obj = self.s.resolve_pptr(rd.assets_file, fld(d, 'RootAppearanceObject'))
        return table, go_to_node.get(pf.ref_key(root_obj)), d

    # -- main --------------------------------------------------------------------------------------
    def extract(self, row, appearance_index=0):
        P = self.problems
        go = _reader_from_key(self.s, row.get('prefab'))
        if go is None:
            P.add('error', 'PREFAB_UNRESOLVED', f'prefab of {row["name"]} not found', ref=row.get('prefab'))
            return None
        tree = pf.walk(self.s, go)
        app_node, app_comp = self._applicator(tree)
        bone_table, appearance_root, applicator_dict = ({}, None, {})
        if app_comp:
            bone_table, appearance_root, applicator_dict = self._bone_table(app_comp['ref'], tree)
        else:
            P.add('warning', 'NO_APPLICATOR', 'prefab has no SlimeAppearanceApplicator')
        nodes = tree['nodes']
        from ..ir.rig import world_matrices
        import numpy as np
        _w = world_matrices(nodes)
        _inv = np.linalg.inv(_w[appearance_root]) if appearance_root is not None else np.eye(4)
        rel_world = {i: _inv @ m for i, m in _w.items()}

        def bone_node(enum):
            return bone_table.get(int(enum)) if enum else None

        # skeleton = every node under the appearance root that is a plain transform chain
        skeleton = []
        if appearance_root is not None:
            stack = list(nodes[appearance_root]['children'])
            while stack:
                i = stack.pop(0)
                n = nodes[i]
                skeleton.append(i)
                stack += n['children']
        animators = [(i, c) for i, n in enumerate(nodes) for c in n['components'] if c['type'] == 'Animator']

        # --- appearance parts ---------------------------------------------------------------------
        app_key = row['appearances'][appearance_index] if row.get('appearances') else None
        app_rd = _reader_from_key(self.s, app_key)
        parts, face, palette, extras, icon = [], None, None, {}, None
        if app_rd is None:
            P.add('error', 'APPEARANCE_UNRESOLVED', f'appearance of {row["name"]} not found', ref=app_key)
        else:
            ad = app_rd.parse_as_dict()
            owner = app_rd.assets_file
            for si, st in enumerate(fld(ad, 'Structures', default=[]) or []):
                el_rd = self.s.resolve_pptr(owner, st.get('Element'))
                if el_rd is None:
                    P.add('error', 'ELEMENT_UNRESOLVED', f'structure {si} element missing', ref=app_key)
                    continue
                ed = el_rd.parse_as_dict()
                part = {'index': si, 'element': el_rd.peek_name(), 'name': fld(ed, 'Name'), 'type': fld(ed, 'Type'),
                        'material_inheritance': fld(ed, 'MaterialInheritance'), 'casts_shadows': bool(fld(ed, 'CastsShadows', default=0)),
                        'supports_faces': bool(st.get('SupportsFaces')), 'face_rules': st.get('FaceRules', []),
                        'default_materials': [], 'lods': []}
                for m in st.get('DefaultMaterials', []) or []:
                    mrd = self.s.resolve_pptr(owner, m)
                    part['default_materials'].append(self._material(pf.ref_key(mrd)))
                overrides = st.get('ElementMaterials', []) or []
                for li, op in enumerate(ed.get('Prefabs', []) or []):
                    ob = self.s.resolve_pptr(el_rd.assets_file, op)
                    if ob is None:
                        P.add('warning', 'LOD_UNRESOLVED', f'{part["element"]} LOD{li} missing', ref=app_key)
                        continue
                    od = ob.parse_as_dict()
                    pgo = self.s.resolve_pptr(ob.assets_file, od.get('m_GameObject'))
                    ptree = pf.walk(self.s, pgo)
                    renderers = []
                    for pn in ptree['nodes']:
                        for c in pn['components']:
                            if c['type'] in pf.RENDERER_TYPES:
                                mf = c.get('mesh')
                                if c['type'] == 'MeshRenderer':
                                    mf = next((x.get('mesh') for x in pn['components'] if x['type'] == 'MeshFilter'), None)
                                renderers.append({'node': pn['name'], 'type': c['type'], 'mesh': self._mesh(mf) if mf else None,
                                                  'transform': pn['transform'], 'cast_shadows': c.get('cast_shadows'),
                                                  'receive_shadows': c.get('receive_shadows'), 'placeholder_materials': c['materials']})
                    attached = [int(x) for x in od.get('AttachedBones', [])]
                    ov = overrides[li] if li < len(overrides) else {}
                    lod_mats = None
                    if ov.get('OverrideDefaults'):
                        lod_mats = []
                        for m in ov.get('Materials', []) or []:
                            mrd = self.s.resolve_pptr(owner, m)
                            lod_mats.append(self._material(pf.ref_key(mrd)))
                    part['lods'].append({
                        'lod': li, 'object': od.get('m_GameObject') and pf.ref_key(pgo), 'renderers': renderers,
                        'parent_bone': bone_node(od.get('ParentBone')), 'root_bone': bone_node(od.get('RootBone')),
                        'attached_bones': [bone_node(b) for b in attached], 'attached_bone_enums': attached,
                        'override_materials': lod_mats, 'rubber_type': od.get('RubberType'),
                        'attach_rubber_bone_effect': bool(od.get('AttachRubberBoneEffect', 0))})
                    for r in renderers:
                        if not (r['mesh'] and r['type'] == 'SkinnedMeshRenderer' and attached):
                            continue
                        bind = self._bind.get(r['mesh']['source'])
                        if bind is None or not len(bind):
                            continue
                        slots, method = resolve_bone_slots(part['lods'][-1]['attached_bones'], bind, rel_world)
                        if slots is None:
                            P.add('error', 'BONE_UNRESOLVED',
                                  f'{part["element"]} LOD{li}: bind poses match none of the {len(attached)} declared bones')
                            continue
                        if method != 'declared':
                            P.add('warning', 'BONE_SLOTS_REMAPPED',
                                  f'{part["element"]} LOD{li}: declared AttachedBones ({len(attached)}) differ from the {len(bind)} mesh slots; '
                                  'slots resolved from bind poses')
                            part['lods'][-1]['attached_bones_declared'] = part['lods'][-1]['attached_bones']
                            part['lods'][-1]['attached_bones'] = slots
                        part['lods'][-1]['bone_slot_method'] = method
                parts.append(part)
            face_rd = self.s.resolve_pptr(owner, fld(ad, 'Face'))
            if face_rd is not None:
                fd = face_rd.parse_as_dict()
                face = {'name': face_rd.peek_name(), 'expressions': []}
                for ef in fd.get('ExpressionFaces', []):
                    e = {'expression': ef.get('SlimeExpression')}
                    for k in ('Eyes', 'Mouth'):
                        mrd = self.s.resolve_pptr(face_rd.assets_file, ef.get(k))
                        e[k.lower()] = self._material(pf.ref_key(mrd))
                    face['expressions'].append(e)
            palette = fld(ad, 'ColorPalette')
            icon = None
            try:
                from .icon import extract_icon
                sprite = self.s.resolve_pptr(owner, fld(ad, 'Icon'))
                if sprite is None:
                    raise ValueError('appearance references no icon Sprite')
                png, desc = extract_icon(self.s, sprite, app_rd)
                rel = self.w.put_bytes('icon/' + desc['file'], png)
                icon = {'file': rel, 'descriptor': desc}
            except Exception as e:  # reported; the bridge-cache export refuses species without an icon
                P.add('warning', 'ICON_UNAVAILABLE', f'{type(e).__name__}: {e}')
            extras = {
                'splat_color': fld(ad, 'SplatColor'), 'title_key': None, 'save_set': fld(ad, 'SaveSet'),
                'animator_override': pf.ref_key(self.s.resolve_pptr(owner, fld(ad, 'AnimatorOverride'))),
                'idle_override': pf.ref_key(self.s.resolve_pptr(owner, fld(ad, 'IdleAnimationOverride'))),
                'wing_flap_override': pf.ref_key(self.s.resolve_pptr(owner, fld(ad, 'WingFlapAnimationOverride'))),
                'bite_override': pf.ref_key(self.s.resolve_pptr(owner, fld(ad, 'BiteAnimationOverride'))),
                'dependent_appearances': [pf.ref_key(self.s.resolve_pptr(owner, x)) for x in (fld(ad, 'DependentAppearances', default=[]) or [])],
            }
            for k in ('QubitAppearance', 'ShockedAppearance', 'GlintAppearance', 'TornadoAppearance', 'VineAppearance',
                      'CrystalAppearance', 'ExplosionAppearance', 'DeathAppearance', 'StoneFormAppearance', 'RoarAppearance'):
                v = fld(ad, k)
                if v is not None:
                    extras[k] = pf.ref_key(self.s.resolve_pptr(owner, v))

        return {
            'prefab': {'ref': row['prefab'], 'name': nodes[tree['root']]['name'], 'nodes': nodes, 'root': tree['root'],
                       'appearance_root': appearance_root, 'skeleton': skeleton,
                       'bone_table': {str(k): v for k, v in bone_table.items()},
                       'animators': [{'node': i, **c} for i, c in animators]},
            'appearance': {'ref': app_key, 'parts': parts, 'face': face, 'palette': palette, 'extras': extras, 'icon': icon},
            'applicator': {k: v for k, v in applicator_dict.items() if k in ('SlimeExpression',)},
            'materials': {k: v for k, v in self._materials.items() if v},
            'textures': {k: v for k, v in self._tex_files.items() if v},
            'meshes': {k: v for k, v in self._mesh_files.items() if v},
        }
