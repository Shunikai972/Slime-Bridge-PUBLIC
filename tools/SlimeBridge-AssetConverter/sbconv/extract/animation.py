"""Animation extraction: AssetRipper-exported Unity YAML (.anim / .controller / .overrideController)
-> engine-neutral IR dicts. Curves keep Unity's key format (time, value, in/out slopes, tangent and
weight modes) so the target builders can reproduce them exactly and glTF export can resample them."""

import re
from pathlib import Path

import yaml

_DOC = re.compile(r'^--- !u!(\d+) &(-?\d+)[^\n]*$', re.M)


def load_unity_yaml(path):
    """-> list of (class_id, file_id, body_dict). Tolerates Unity's custom tags."""
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    parts = _DOC.split(text)
    out = []
    for i in range(1, len(parts), 3):
        body = yaml.load(parts[i + 2], Loader=yaml.CSafeLoader if hasattr(yaml, 'CSafeLoader') else yaml.SafeLoader)
        if isinstance(body, dict) and body:
            out.append((int(parts[i]), parts[i + 1], next(iter(body.values()))))
    return out


def guid_map(project_dir):
    """guid -> asset path (relative), from the .meta files AssetRipper writes."""
    m = {}
    root = Path(project_dir)
    for meta in root.rglob('*.meta'):
        try:
            txt = meta.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        g = re.search(r'^guid: ([0-9a-f]{32})', txt, re.M)
        if g:
            m[g.group(1)] = meta.with_suffix('').relative_to(root).as_posix()
    return m


def _vec(d, keys):
    return [float(d[k]) for k in keys]


def _keys(curve, comps):
    keys = []
    for k in curve.get('m_Curve') or []:
        e = {'t': float(k['time']), 'tm': int(k.get('tangentMode', 0)), 'wm': int(k.get('weightedMode', 0))}
        if comps is None:
            e.update(v=float(k['value']), i=float(k['inSlope']), o=float(k['outSlope']))
        else:
            e.update(v=_vec(k['value'], comps), i=_vec(k['inSlope'], comps), o=_vec(k['outSlope'], comps))
        if e['wm']:
            e['iw'] = float(k['inWeight']) if comps is None else _vec(k['inWeight'], comps)
            e['ow'] = float(k['outWeight']) if comps is None else _vec(k['outWeight'], comps)
        keys.append(e)
    return keys


def parse_clip(path):
    for cid, _, body in load_unity_yaml(path):
        if cid == 74:
            break
    else:
        raise ValueError(f'{path}: no AnimationClip document')
    st = body.get('m_AnimationClipSettings', {})
    clip = {'name': body['m_Name'], 'legacy': bool(body.get('m_Legacy', 0)), 'sample_rate': float(body.get('m_SampleRate', 60)),
            'wrap_mode': int(body.get('m_WrapMode', 0)),
            'start': float(st.get('m_StartTime', 0)), 'stop': float(st.get('m_StopTime', 0)),
            'loop': bool(st.get('m_LoopTime', 0)), 'loop_blend': bool(st.get('m_LoopBlend', 0)),
            'cycle_offset': float(st.get('m_CycleOffset', 0)),
            'has_root_motion_flags': {k: bool(st.get(k, 0)) for k in ('m_KeepOriginalPositionY', 'm_KeepOriginalPositionXZ',
                                                                       'm_KeepOriginalOrientation', 'm_LoopBlendOrientation')},
            'positions': [], 'rotations': [], 'eulers': [], 'scales': [], 'floats': [], 'pptr': [], 'events': []}
    for c in body.get('m_PositionCurves') or []:
        clip['positions'].append({'path': (c.get('path') or ''), 'keys': _keys(c['curve'], 'xyz')})
    for c in body.get('m_RotationCurves') or []:
        clip['rotations'].append({'path': (c.get('path') or ''), 'keys': _keys(c['curve'], 'xyzw')})
    for c in body.get('m_EulerCurves') or []:
        clip['eulers'].append({'path': (c.get('path') or ''), 'keys': _keys(c['curve'], 'xyz')})
    for c in body.get('m_ScaleCurves') or []:
        clip['scales'].append({'path': (c.get('path') or ''), 'keys': _keys(c['curve'], 'xyz')})
    for c in body.get('m_FloatCurves') or []:
        clip['floats'].append({'path': (c.get('path') or ''), 'attribute': c['attribute'], 'class_id': int(c['classID']),
                               'script': (c.get('script') or {}).get('fileID', 0),
                               'keys': _keys(c['curve'], None),
                               'pre': int(c['curve'].get('m_PreInfinity', 2)), 'post': int(c['curve'].get('m_PostInfinity', 2))})
    for c in body.get('m_PPtrCurves') or []:
        clip['pptr'].append({'path': (c.get('path') or ''), 'attribute': c['attribute'], 'class_id': int(c['classID']),
                             'keys': [{'t': float(k['time']), 'value': k['value']} for k in c.get('curve') or []]})
    for ev in body.get('m_Events') or []:
        clip['events'].append({'t': float(ev['time']), 'function': ev.get('functionName', ''),
                               'string': ev.get('data', ''), 'float': float(ev.get('floatParameter', 0)),
                               'int': int(ev.get('intParameter', 0)),
                               'object': (ev.get('objectReferenceParameter') or {}).get('fileID', 0),
                               'message_options': int(ev.get('messageOptions', 0))})
    clip['curve_counts'] = {k: len(clip[k]) for k in ('positions', 'rotations', 'eulers', 'scales', 'floats', 'pptr', 'events')}
    clip['length'] = max([clip['stop'] - clip['start']] + [k['t'] for g in ('positions', 'rotations', 'scales') for c in clip[g] for k in c['keys']] + [0])
    return clip


def clip_paths(clip):
    return sorted({c['path'] for g in ('positions', 'rotations', 'eulers', 'scales', 'floats', 'pptr') for c in clip[g]})


def _motion(mref, guids, fid_index):
    if not mref:
        return None
    fid, guid = mref.get('fileID', 0), mref.get('guid')
    if guid and guid in guids:
        return {'kind': 'clip', 'asset': guids[guid]}
    if fid and str(fid) in fid_index and fid_index[str(fid)][0] == 206:
        return {'kind': 'blendtree', 'tree': _blendtree(fid_index[str(fid)][1], guids, fid_index)}
    return {'kind': 'unresolved', 'file_id': fid, 'guid': guid}


def _blendtree(bt, guids, fid_index):
    return {'name': bt.get('m_Name'), 'blend_type': int(bt.get('m_BlendType', 0)), 'param': bt.get('m_BlendParameter'),
            'param_y': bt.get('m_BlendParameterY'),
            'children': [{'motion': _motion(c.get('m_Motion'), guids, fid_index), 'threshold': c.get('m_Threshold'),
                          'pos': c.get('m_Position'), 'time_scale': c.get('m_TimeScale', 1), 'mirror': bool(c.get('m_Mirror', 0)),
                          'direct_param': c.get('m_DirectBlendParameter')} for c in bt.get('m_Childs') or []]}


def _transition(t, state_names):
    return {'dst': state_names.get(str((t.get('m_DstState') or {}).get('fileID', 0))),
            'dst_machine': str((t.get('m_DstStateMachine') or {}).get('fileID', 0)) if (t.get('m_DstStateMachine') or {}).get('fileID') else None,
            'conditions': [{'mode': int(c['m_ConditionMode']), 'param': c['m_ConditionEvent'], 'threshold': float(c['m_EventTreshold'])}
                           for c in t.get('m_Conditions') or []],
            'solo': bool(t.get('m_Solo', 0)), 'mute': bool(t.get('m_Mute', 0)), 'is_exit': bool(t.get('m_IsExit', 0)),
            'exit_time': float(t.get('m_ExitTime', 0)), 'has_exit_time': bool(t.get('m_HasExitTime', 0)),
            'has_fixed_duration': bool(t.get('m_HasFixedDuration', 0)), 'duration': float(t.get('m_TransitionDuration', 0)),
            'offset': float(t.get('m_TransitionOffset', 0)), 'interruption_source': int(t.get('m_InterruptionSource', 0)),
            'ordered_interruption': bool(t.get('m_OrderedInterruption', 0)), 'can_transition_to_self': bool(t.get('m_CanTransitionToSelf', 0))}


def parse_controller(path, guids):
    docs = load_unity_yaml(path)
    fid_index = {fid: (cid, body) for cid, fid, body in docs}
    ac = next(b for c, _, b in docs if c == 91)
    state_names = {fid: body.get('m_Name') for cid, fid, body in docs if cid == 1102}
    machines = {fid: body for cid, fid, body in docs if cid == 1107}
    out = {'name': ac['m_Name'],
           'parameters': [{'name': p['m_Name'], 'type': int(p['m_Type']), 'default_float': float(p.get('m_DefaultFloat', 0)),
                           'default_int': int(p.get('m_DefaultInt', 0)), 'default_bool': bool(p.get('m_DefaultBool', 0))}
                          for p in ac.get('m_AnimatorParameters') or []], 'layers': []}
    for layer in ac.get('m_AnimatorLayers') or []:
        sm = machines.get(str((layer.get('m_StateMachine') or {}).get('fileID', 0)), {})
        states = []
        for ch in sm.get('m_ChildStates') or []:
            sfid = str((ch['m_State'] or {}).get('fileID', 0))
            sb = fid_index[sfid][1]
            states.append({'name': sb.get('m_Name'), 'speed': float(sb.get('m_Speed', 1)), 'cycle_offset': float(sb.get('m_CycleOffset', 0)),
                           'mirror': bool(sb.get('m_Mirror', 0)), 'tag': sb.get('m_Tag', ''), 'write_defaults': bool(sb.get('m_WriteDefaultValues', 1)),
                           'speed_param': sb.get('m_SpeedParameter') if sb.get('m_SpeedParameterActive') else None,
                           'motion': _motion(sb.get('m_Motion'), guids, fid_index),
                           'transitions': [_transition(fid_index[str(t['fileID'])][1], state_names) for t in sb.get('m_Transitions') or []
                                           if str(t.get('fileID')) in fid_index],
                           'position': ch.get('m_Position')})
        out['layers'].append({
            'name': layer['m_Name'], 'default_weight': float(layer.get('m_DefaultWeight', 0)), 'blending': int(layer.get('m_BlendingMode', 0)),
            'ik_pass': bool(layer.get('m_IKPass', 0)), 'sync_layer_index': int(layer.get('m_SyncedLayerIndex', -1)),
            'avatar_mask': (layer.get('m_AvatarMask') or {}).get('guid'),
            'default_state': state_names.get(str((sm.get('m_DefaultState') or {}).get('fileID', 0))),
            'states': states,
            'any_state_transitions': [_transition(fid_index[str(t['fileID'])][1], state_names) for t in sm.get('m_AnyStateTransitions') or []
                                      if str(t.get('fileID')) in fid_index],
            'entry_transitions': [_transition(fid_index[str(t['fileID'])][1], state_names) for t in sm.get('m_EntryTransitions') or []
                                  if str(t.get('fileID')) in fid_index]})
    return out


_HASHED = re.compile(r'^path_0x([0-9A-Fa-f]+)_')


def resolve_hashed_paths(clip, skeleton_paths):
    """AssetRipper names curves whose bone it cannot see ``path_0x<crc32>_<tag>``. Unity's generic bindings
    hash the full relative bone path with CRC32, so the species' own skeleton resolves them exactly.
    Returns the number resolved; whatever stays hashed belongs to bones this species does not have."""
    import zlib
    table = {zlib.crc32(p.encode('utf-8')) & 0xFFFFFFFF: p for p in skeleton_paths}
    resolved = 0
    for group in ('positions', 'rotations', 'eulers', 'scales', 'floats', 'pptr'):
        for c in clip[group]:
            m = _HASHED.match(c['path'] or '')
            if m:
                real = table.get(int(m.group(1), 16))
                if real is not None:
                    c['path'] = real
                    resolved += 1
    return resolved
