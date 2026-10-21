"""SR1 authored controller selection, without graph playback or name guesses."""
from __future__ import annotations

import zlib

from bridge.content.unity_animation import decode_generic_clip


def pointer_key(pointer):
    if (not isinstance(pointer, dict) or type(pointer.get('m_FileID')) is not int
            or type(pointer.get('m_PathID')) is not int
            or pointer['m_FileID'] < 0 or pointer['m_PathID'] <= 0):
        raise ValueError('missing or malformed animation reference')
    return pointer['m_FileID'], pointer['m_PathID']


def select_controller_clips(tree):
    """Explicit policy: every declared clip; default from base layer's state.

    Preserve duplicate-slot indices when evaluating the default, deduplicate only
    the exported references. Refuse a default blend instead of picking a leaf.
    Additional layers are recorded, not combined into the single default clip.
    """
    try:
        pointers = tree['m_AnimationClips']
        if not isinstance(pointers, list) or not 1 <= len(pointers) <= 256:
            raise ValueError('controller clip array missing or oversized')
        unique = {}
        for p in pointers:
            unique.setdefault(pointer_key(p), p)
        controller = tree['m_Controller']
        layers = controller['m_LayerArray']
        machines = controller['m_StateMachineArray']
        if not isinstance(layers, list) or not 1 <= len(layers) <= 32:
            raise ValueError('controller layers missing or oversized')
        index = layers[0]['data']['m_StateMachineIndex']
        if type(index) is not int or not 0 <= index < len(machines):
            raise ValueError('base layer state machine index invalid')
        machine = machines[index]['data']
        default = machine['m_DefaultState']
        states = machine['m_StateConstantArray']
        if type(default) is not int or not 0 <= default < len(states):
            raise ValueError('default state index invalid')
        blends = states[default]['data']['m_BlendTreeConstantArray']
        if len(blends) != 1:
            raise ValueError('ambiguous default animation blend')
        nodes = blends[0]['data']['m_NodeArray']
        if len(nodes) != 1 or nodes[0]['data']['m_ChildIndices']:
            raise ValueError('ambiguous default animation blend')
        slot = nodes[0]['data']['m_ClipID']
        if type(slot) is not int or not 0 <= slot < len(pointers):
            raise ValueError('default animation clip slot invalid')
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError('incomplete or malformed controller selection metadata') from exc
    return {'clip_pointers': list(unique.values()), 'default_pointer': pointers[slot],
            'default_state_index': default, 'layer_count': len(layers),
            'policy': 'all-controller-referenced-clips; base-layer-default-state'}


def animation_paths(skeleton):
    """Exact Appearance-relative Unity CRC32 paths; no reflection or retargeting."""
    paths = {0: ''}
    ordered = []
    for index, bone in enumerate(skeleton):
        parent = bone['parent']
        if type(parent) is not int or not -1 <= parent < index:
            raise ValueError('invalid skeleton parent ordering')
        name = bone['name']
        if not isinstance(name, str) or not name or '/' in name:
            raise ValueError('invalid source bone name')
        path = name if parent == -1 else ordered[parent] + '/' + name
        hashed = zlib.crc32(path.encode('utf-8'))
        if hashed in paths:
            raise ValueError('ambiguous animation path hash')
        paths[hashed] = path
        ordered.append(path)
    return paths


def decode_sr1_clip(tree, paths):
    """Use the shared bounded sampler, with one SR1 float32 boundary adapter.

    Unity 2019 Rock Recover has first real keys 4.768e-7 seconds after zero,
    with exactly the same value as its authored constant startup sentinel.
    Materialize ONLY that proven initial hold at zero, keeping every real key
    and polynomial timestamp unchanged. Never extend a genuinely missing track.
    """
    import copy
    import math
    import struct
    from bridge.content.unity_animation import MAX_STREAM_WORDS, MAX_STREAM_FRAMES, MAX_SCALARS
    try:
        muscle = tree['m_MuscleClip']
        stream = muscle['m_Clip']['data']['m_StreamedClip']
        words, count = stream['data'], stream['curveCount']
        if not isinstance(words, list) or len(words) > MAX_STREAM_WORDS:
            raise ValueError('stream data exceeds SR1 adapter budget')
        if type(count) is not int or not 0 <= count <= MAX_SCALARS:
            raise ValueError('stream scalar count invalid')
        raw = bytearray(len(words) * 4)
        for i, word in enumerate(words):
            if type(word) is not int or not 0 <= word <= 2**32-1:
                raise ValueError('stream word invalid')
            struct.pack_into('<I', raw, i*4, word)
        frames, offset = [], 0
        while offset < len(raw):
            if len(raw) - offset < 8 or len(frames) >= MAX_STREAM_FRAMES:
                raise ValueError('truncated or oversized streamed frames')
            time, size = struct.unpack_from('<fi', raw, offset)
            offset += 8
            if not 0 <= size <= count or len(raw) - offset < size * 20:
                raise ValueError('invalid stream key count')
            keys = [struct.unpack_from('<iffff', raw, offset + i*20) for i in range(size)]
            frames.append((time, keys))
            offset += size*20
        additions = []
        if frames and frames[0][0] == -3.4028234663852886e38 and muscle['m_StartTime'] == 0:
            startup = {key[0]: key for key in frames[0][1]}
            first = {}
            for time, keys in frames[1:]:
                if math.isfinite(time) and time >= 0:
                    for key in keys:
                        first.setdefault(key[0], (time, key))
            rate = tree['m_SampleRate']
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
                raise ValueError('invalid source sample rate')
            tolerance = min(1e-6, 0.001 / rate)
            for index, (time, key) in first.items():
                initial = startup.get(index)
                if 0 < time <= tolerance and initial is not None:
                    if initial[1:4] != (0, 0, 0) or initial[4] != key[4]:
                        raise ValueError('initial streamed key does not match authored startup hold')
                    additions.append(initial)
        if additions:
            if len({key[0] for key in additions}) != len(additions):
                raise ValueError('duplicate initial stream hold')
            rebuilt = bytearray()
            inserted = False
            for time, keys in frames:
                if not inserted and time >= 0:
                    if time == 0:
                        keys = keys + additions
                    else:
                        rebuilt.extend(struct.pack('<fi', 0, len(additions)))
                        for key in additions:
                            rebuilt.extend(struct.pack('<iffff', *key))
                    inserted = True
                rebuilt.extend(struct.pack('<fi', time, len(keys)))
                for key in keys:
                    rebuilt.extend(struct.pack('<iffff', *key))
            tree = copy.deepcopy(tree)
            tree['m_MuscleClip']['m_Clip']['data']['m_StreamedClip']['data'] = [
                struct.unpack_from('<I', rebuilt, i)[0] for i in range(0, len(rebuilt), 4)]
    except (KeyError, TypeError, IndexError, struct.error) as exc:
        raise ValueError('malformed SR1 streamed initial-key metadata') from exc
    return decode_generic_clip(tree, paths)


def extract_animation(assets, controller, skeleton):
    selection = select_controller_clips(assets.tree(controller))
    paths = animation_paths(skeleton)
    clips, records, identities = [], [], set()
    default_obj = assets.follow(controller, selection['default_pointer'], 'AnimationClip')
    default_name = None
    for pointer in selection['clip_pointers']:
        obj = assets.follow(controller, pointer, 'AnimationClip')
        identity = assets.identity(obj)
        if identity in identities:
            continue
        identities.add(identity)
        clip = decode_sr1_clip(assets.tree(obj), paths)
        if not clip['channels']:
            raise ValueError('controller clip has no authored Transform channels')
        if any(c['name'] == clip['name'] for c in clips):
            raise ValueError('ambiguous source animation clip name')
        clips.append(clip)
        records.append({'source': assets.describe(obj), 'name': clip['name'],
                        'channel_count': len(clip['channels']), 'sample_count': len(clip['times'])})
        if identity == assets.identity(default_obj):
            default_name = clip['name']
    if default_name is None:
        raise ValueError('default clip not in source selection')
    provenance = {k: v for k, v in selection.items() if k not in ('clip_pointers', 'default_pointer')}
    provenance.update({'controller': assets.describe(controller), 'clips': records,
                       'sampling': 'authored source lattice; not Animator blend or procedural parity'})
    return {'default_clip': default_name, 'clips': clips}, provenance
