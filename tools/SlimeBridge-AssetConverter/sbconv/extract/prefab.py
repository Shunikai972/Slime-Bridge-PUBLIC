"""Generic GameObject-hierarchy walker (both games). Produces plain dict nodes in Unity space;
asset payloads (meshes, textures) are referenced, not embedded."""

from ..engines.unitypy_engine import monobehaviour_header

COLLIDER_TYPES = ('SphereCollider', 'BoxCollider', 'CapsuleCollider', 'MeshCollider')
RENDERER_TYPES = ('MeshRenderer', 'SkinnedMeshRenderer')


def ref_key(reader):
    """Stable identity of an object inside an install: <source-file>#<path_id>."""
    if reader is None:
        return None
    f = reader.assets_file
    return f'{getattr(f, "name", "")}#{reader.path_id}'


def _vec(v, names):
    return [float(getattr(v, n)) for n in names]


def read_transform(tr):
    return {'position': _vec(tr.m_LocalPosition, 'xyz'), 'rotation': _vec(tr.m_LocalRotation, 'xyzw'),
            'scale': _vec(tr.m_LocalScale, 'xyz')}


def walk(session, go_reader, max_nodes=2000):
    """Return {'nodes': [...], 'root': index}. node: {name, path, parent, ref, active, layer, tag,
    transform, components: [{'type', 'ref', ...}]}; children are listed in Transform order."""
    nodes = []
    by_transform = {}

    def add(go_rd, parent_idx, parent_path):
        go = go_rd.read()
        idx = len(nodes)
        if idx > max_nodes:
            raise ValueError('prefab hierarchy too large (cycle?)')
        path = f'{parent_path}/{go.m_Name}' if parent_path is not None else go.m_Name
        node = {'name': go.m_Name, 'path': path, 'parent': parent_idx, 'ref': ref_key(go_rd),
                'active': bool(go.m_IsActive), 'layer': int(go.m_Layer), 'tag': int(getattr(go, 'm_Tag', 0)),
                'transform': None, 'components': [], 'children': []}
        nodes.append(node)
        tr_rd = None
        for cp in go.m_Component:
            c_rd = session.resolve_pptr(go_rd.assets_file, cp.component)
            if c_rd is None:
                node['components'].append({'type': 'MISSING', 'ref': None})
                continue
            t = c_rd.type.name
            if t in ('Transform', 'RectTransform'):
                tr_rd = c_rd
                node['transform'] = read_transform(c_rd.read())
                node['transform']['rect'] = (t == 'RectTransform')
                by_transform[ref_key(c_rd)] = idx
                continue
            node['components'].append(describe_component(session, c_rd))
        if tr_rd is not None:
            tr = tr_rd.read()
            for ch in tr.m_Children:
                ch_tr = session.resolve_pptr(tr_rd.assets_file, ch)
                if ch_tr is None:
                    continue
                child_go = session.resolve_pptr(ch_tr.assets_file, ch_tr.read().m_GameObject)
                if child_go is not None:
                    node['children'].append(add(child_go, idx, path))
        return idx

    root = add(go_reader, None, None)
    return {'nodes': nodes, 'root': root, 'transform_index': by_transform}


def _pptr_list(session, owner, items):
    out = []
    for p in items or []:
        rd = session.resolve_pptr(owner, p)
        out.append(ref_key(rd))
    return out


def describe_component(session, rd):
    t = rd.type.name
    owner = rd.assets_file
    base = {'type': t, 'ref': ref_key(rd)}
    try:
        if t == 'MonoBehaviour':
            h = monobehaviour_header(rd)
            sc = session.script_of(rd)
            base.update({'script': sc[0] if sc else None, 'assembly': sc[1] if sc else None,
                         'enabled': h['enabled']})
            return base
        o = rd.read()
        if t == 'MeshFilter':
            base['mesh'] = ref_key(session.resolve_pptr(owner, o.m_Mesh))
        elif t in RENDERER_TYPES:
            base['materials'] = _pptr_list(session, owner, o.m_Materials)
            base['enabled'] = bool(o.m_Enabled)
            for k, a in (('cast_shadows', 'm_CastShadows'), ('receive_shadows', 'm_ReceiveShadows'),
                         ('motion_vectors', 'm_MotionVectors'), ('light_probe_usage', 'm_LightProbeUsage'),
                         ('reflection_probe_usage', 'm_ReflectionProbeUsage'), ('sorting_order', 'm_SortingOrder'),
                         ('rendering_layer_mask', 'm_RenderingLayerMask')):
                if hasattr(o, a):
                    base[k] = int(getattr(o, a))
            if t == 'SkinnedMeshRenderer':
                base['mesh'] = ref_key(session.resolve_pptr(owner, o.m_Mesh))
                base['bones'] = [ref_key(session.resolve_pptr(owner, b)) for b in o.m_Bones]
                base['root_bone'] = ref_key(session.resolve_pptr(owner, o.m_RootBone))
                base['quality'] = int(getattr(o, 'm_Quality', 0))
                base['update_when_offscreen'] = bool(getattr(o, 'm_UpdateWhenOffscreen', False))
                base['blend_shape_weights'] = [float(w) for w in (getattr(o, 'm_BlendShapeWeights', None) or [])]
                aabb = getattr(o, 'm_AABB', None)
                if aabb is not None:
                    base['aabb'] = {'center': _vec(aabb.m_Center, 'xyz'), 'extent': _vec(aabb.m_Extent, 'xyz')}
        elif t == 'Animator':
            base['controller'] = ref_key(session.resolve_pptr(owner, o.m_Controller))
            base['avatar'] = ref_key(session.resolve_pptr(owner, o.m_Avatar))
            base['apply_root_motion'] = bool(o.m_ApplyRootMotion)
            base['culling_mode'] = int(o.m_CullingMode)
            base['update_mode'] = int(getattr(o, 'm_UpdateMode', 0))
        elif t in COLLIDER_TYPES:
            base['is_trigger'] = bool(o.m_IsTrigger)
            base['enabled'] = bool(o.m_Enabled)
            base['material'] = ref_key(session.resolve_pptr(owner, o.m_Material))
            if t == 'SphereCollider':
                base['radius'] = float(o.m_Radius); base['center'] = _vec(o.m_Center, 'xyz')
            elif t == 'BoxCollider':
                base['size'] = _vec(o.m_Size, 'xyz'); base['center'] = _vec(o.m_Center, 'xyz')
            elif t == 'CapsuleCollider':
                base['radius'] = float(o.m_Radius); base['height'] = float(o.m_Height)
                base['direction'] = int(o.m_Direction); base['center'] = _vec(o.m_Center, 'xyz')
            elif t == 'MeshCollider':
                base['mesh'] = ref_key(session.resolve_pptr(owner, o.m_Mesh)); base['convex'] = bool(o.m_Convex)
        elif t == 'Rigidbody':
            for k, a in (('mass', 'm_Mass'), ('drag', 'm_Drag'), ('angular_drag', 'm_AngularDrag')):
                base[k] = float(getattr(o, a))
            base['use_gravity'] = bool(o.m_UseGravity); base['is_kinematic'] = bool(o.m_IsKinematic)
            base['constraints'] = int(o.m_Constraints); base['interpolate'] = int(o.m_Interpolate)
        elif t == 'LODGroup':
            base['lods'] = [{'screen_height': float(l.screenRelativeHeight),
                             'renderers': [ref_key(session.resolve_pptr(owner, r.renderer)) for r in l.renderers]}
                            for l in o.m_LODs]
            base['fade_mode'] = int(getattr(o, 'm_FadeMode', 0))
            base['size'] = float(getattr(o, 'm_Size', 0))
        elif t == 'Light':
            base['light_type'] = int(o.m_Type)
            base['color'] = _vec(o.m_Color, 'rgba'); base['intensity'] = float(o.m_Intensity)
            base['range'] = float(o.m_Range)
        elif t == 'AudioSource':
            base['clip'] = ref_key(session.resolve_pptr(owner, o.m_audioClip))
            base['volume'] = float(o.m_Volume); base['loop'] = bool(o.Loop); base['play_on_awake'] = bool(o.m_PlayOnAwake)
        elif t == 'ParticleSystem':
            base['has_particle_system'] = True
        elif t == 'ParticleSystemRenderer':
            base['materials'] = _pptr_list(session, owner, o.m_Materials)
    except Exception as e:  # describe what we could not read instead of dropping the component
        base['read_error'] = f'{type(e).__name__}: {e}'
    return base
