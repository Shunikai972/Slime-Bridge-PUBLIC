"""Bounded integration of existing SR2 geometry, Sprite and clip extractors.

Selection is EXPLICIT: IDs must be referenced by the authored species controller
or appearance override. This is not Animator graph/transition/blend reconstruction.
No synthetic clips, retargeting or fallback is permitted on this path.
"""
from __future__ import annotations

import hashlib
import zlib
from bridge.content.sr2_appearance import _named_roots, source_object_index, source_ref
from bridge.content.unity_animation import decode_generic_clip


def resolve_species_source(assets, species):
    """Bind the applied unique default appearance, icon and Animator references."""
    objects = source_object_index(assets)
    headers = getattr(assets, '_class_headers', None)        # one pass over the MonoBehaviours per game file, not one per call
    if headers is None:
        headers = {}
        for obj in objects.values():
            if obj.type.name == 'MonoBehaviour':
                try:
                    headers[obj.path_id] = obj.parse_monobehaviour_head().m_Script.deref().read().m_ClassName
                except Exception:  # noqa: BLE001 - script not in the loaded files (bundle-sourced extraction)
                    headers[obj.path_id] = ''
        try:
            assets._class_headers = headers
        except AttributeError:
            pass
    trees = {}

    def tree(pid):
        if pid not in trees:
            from bridge.content import sr2_appearance
            if headers.get(pid) == 'SlimeDefinition':
                trees[pid] = objects[pid].read_typetree(
                    nodes=sr2_appearance._definition_nodes(assets.env.typetree_generator))
            else:
                trees[pid] = objects[pid].read_typetree()
        return trees[pid]

    def typed(pointer, kind=None, class_name=None):
        pid = source_ref(pointer)
        if (not pid or pid not in objects or (kind and objects[pid].type.name != kind)
                or (class_name and headers.get(pid) != class_name)):
            raise ValueError('source cache reference is missing or has an unsupported type')
        return pid

    def component(go, kind=None, class_name=None):
        candidates = [typed(c['component']) for c in tree(go)['m_Component']]
        candidates = [p for p in candidates if (not kind or objects[p].type.name == kind)
                      and (not class_name or headers.get(p) == class_name)]
        if len(candidates) != 1:
            raise ValueError('source cache GameObject has no unique requested component')
        return candidates[0]

    try:
        roots = _named_roots(assets, objects, f'slime{species}')
        if len(roots) != 1:
            raise ValueError('source species has no unique prefab')
        root = roots[0]
        applicator_id = component(root, class_name='SlimeAppearanceApplicator')
        applicator = tree(applicator_id)
        definition_id = typed(applicator['SlimeDefinition'], class_name='SlimeDefinition')
        definition = tree(definition_id)
        if typed(definition['prefab'], kind='GameObject') != root:
            raise ValueError('source definition references another prefab')
        defaults = definition['AppearancesDefault']
        if not isinstance(defaults, list) or len(defaults) != 1:
            raise ValueError('source species has no unique default appearance')
        appearance_id = typed(defaults[0], class_name='SlimeAppearance')
        if source_ref(applicator['Appearance']) not in (0, appearance_id):
            raise ValueError('non-default applied appearance unsupported')
        appearance = tree(appearance_id)
        # a Largo's appearance carries no icon of its own (the game composes it from the parents): the cache builds one
        sprite_id = typed(appearance['_icon'], kind='Sprite') if (source_ref(appearance['_icon']) or not definition.get('IsLargo')) else 0
        parent_names = []
        if definition.get('IsLargo'):
            for pointer in definition['BaseSlimes']:
                parent_names.append(tree(typed(pointer, class_name='SlimeDefinition'))['m_Name'])
        visual = typed(applicator['RootAppearanceObject'], kind='GameObject')
        animator_id = component(visual, kind='Animator')
        animator = tree(animator_id)
        if typed(animator['m_GameObject'], kind='GameObject') != visual:
            raise ValueError('source Animator does not belong to the appearance root')
        # OverrideControllers/BlendTree evaluation is deliberately not approximated.
        controller_id = typed(animator['m_Controller'], kind='AnimatorController')
        controller = tree(controller_id)
        refs = controller['m_AnimationClips']
        if not isinstance(refs, list) or not refs or len(refs) > 256:
            raise ValueError('source controller clip references invalid or oversized')
        controller_clips = [typed(p, kind='AnimationClip') for p in refs]
        overrides = {}
        for field in ('_idleAnimationOverride', '_biteAnimationOverride', '_wingFlapAnimationOverride'):
            pid = source_ref(appearance[field])
            if pid:
                overrides[field] = typed(appearance[field], kind='AnimationClip')
        ids = sorted(set(controller_clips) | set(overrides.values()))
    except (KeyError, IndexError, TypeError, AttributeError) as ex:
        raise ValueError('incomplete or malformed declared source cache metadata') from ex
    return {'prefab_id': root, 'definition_id': definition_id, 'is_largo': bool(definition.get('IsLargo')), 'parent_names': parent_names, 'largo_name': species, 'applicator_id': applicator_id,
            'appearance_id': appearance_id, 'sprite_id': sprite_id, 'animator_id': animator_id,
            'controller_id': controller_id, 'controller_name': controller['m_Name'],
            'controller_clip_ids': controller_clips, 'appearance_overrides': overrides, 'clip_ids': ids}


def collect_declared_parts(assets, resolved, candidates):
    """Filter existing mesh extractor candidates by actual applied LOD0 references.

    Keep its stable role/order/geometry contract; refuse any declared mesh it
    cannot export, and bind non-face materials by PPtr rather than species names.
    """
    objects = source_object_index(assets)
    by_mesh = {p['mesh_obj'].path_id: p for p in candidates}
    if len(by_mesh) != len(candidates):
        raise ValueError('ambiguous mesh candidates')

    def obj(pointer, kind=None):
        pid = source_ref(pointer)
        if not pid or pid not in objects or (kind and objects[pid].type.name != kind):
            raise ValueError('missing or unsupported declared appearance part reference')
        return objects[pid]

    def flag(value):
        if type(value) not in (bool, int) or value not in (0, 1):
            raise ValueError('appearance part flag malformed')
        return bool(value)

    wanted = {}
    try:
        structures = objects[resolved['appearance_id']].read_typetree()['_structures']
        if not isinstance(structures, list) or not 1 <= len(structures) <= 64:
            raise ValueError('appearance structures missing or oversized')
        for structure in structures:
            element = obj(structure['Element'], 'MonoBehaviour').read_typetree()
            part = obj(element['Prefabs'][0], 'MonoBehaviour').read_typetree()
            go = obj(part['m_GameObject'], 'GameObject').read_typetree()
            components = [obj(c['component']) for c in go['m_Component']]
            renderers = [o for o in components if o.type.name == 'SkinnedMeshRenderer']
            if len(renderers) != 1:
                raise ValueError('appearance LOD0 has no unique skinned renderer')
            mesh = obj(renderers[0].read_typetree()['m_Mesh'], 'Mesh')
            if mesh.path_id not in by_mesh or mesh.path_id in wanted:
                raise ValueError('unsupported or duplicate declared appearance mesh')
            candidate = dict(by_mesh[mesh.path_id])
            if flag(structure['SupportsFaces']) != (candidate['role'] == 'face'):
                raise ValueError('declared face role does not match extracted mesh')
            override = structure['ElementMaterials'][0]
            materials = override['Materials'] if flag(override['OverrideDefaults']) else structure['DefaultMaterials']
            if not isinstance(materials, list) or not 1 <= len(materials) <= 16:
                raise ValueError('declared appearance materials missing or oversized')
            slots = [obj(p, 'Material') for p in materials]
            candidate['source_material_slots'] = [(o.read().m_Name, o.read()) for o in slots]
            candidate['source_material_ids'] = [o.path_id for o in slots]
            wanted[mesh.path_id] = candidate
    except (KeyError, IndexError, TypeError, AttributeError) as ex:
        raise ValueError('incomplete or malformed declared appearance parts') from ex
    return [wanted[p['mesh_obj'].path_id] for p in candidates if p['mesh_obj'].path_id in wanted]


def extract_source_animation(assets, resolved, bones, clip_ids, default_clip):
    """Decode explicit source IDs with the existing verified generic decoder."""
    if (not isinstance(clip_ids, list) or not 1 <= len(clip_ids) <= 16
            or any(type(p) is not int or p == 0 for p in clip_ids)
            or len(set(clip_ids)) != len(clip_ids)
            or not isinstance(default_clip, str) or not default_clip):
        raise ValueError('explicit clip IDs/default name missing, malformed or duplicated')
    objects = source_object_index(assets)
    paths, mapping = [], {}
    if not isinstance(bones, list) or not bones or len(bones) > 16384:
        raise ValueError('source animation requires a bounded skeleton')
    for index, bone in enumerate(bones):
        parent = bone.get('parent')
        name = bone.get('name')
        if (type(parent) is not int or not -1 <= parent < index or not isinstance(name, str)
                or not name or '/' in name or '\\' in name or name in ('.', '..')):
            raise ValueError('malformed source skeleton path')
        path = (paths[parent] + '/' if parent >= 0 else '') + name
        hash_value = zlib.crc32(path.encode('utf-8'))
        if hash_value in mapping:
            raise ValueError('duplicate or CRC32-colliding skeleton paths')
        paths.append(path)
        mapping[hash_value] = path
    clips, rows = [], []
    for pid in clip_ids:
        if pid not in resolved['clip_ids']:
            raise ValueError(f'clip {pid} is not referenced by the source species controller/appearance')
        obj = objects[pid]
        if obj.type.name != 'AnimationClip':
            raise ValueError('declared clip reference is not an AnimationClip')
        clip = decode_generic_clip(obj.read_typetree(), mapping)
        if not clip['name'] or not clip['channels']:
            raise ValueError('source clip lacks a name or Transform channels')
        clips.append(clip)
        rows.append({'source_path_id': pid, 'name': clip['name'],
                     'source_object_sha256': hashlib.sha256(obj.get_raw_data()).hexdigest()})
    names = [c['name'] for c in clips]
    if len(set(names)) != len(names) or default_clip not in names:
        raise ValueError('source clip names ambiguous or exact default clip name absent')
    return {'default_clip': default_clip, 'clips': clips}, {
        'selection': 'explicit-source-clip-ids', 'runtime_graph_reproduced': False,
        'source_references': resolved, 'clips': rows,
        'limitations': ['no Animator transitions, layer blends, events, root motion or procedural jiggle']}
