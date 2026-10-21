"""Recover source skin bindings from the part Mesh's own bone arrays, validated
against the SR2 appearance metadata (never from bindpose ties, which cannot
distinguish a jiggle parent from a skin child).

Read-only source inspection. Generated local game type schemas are required for
MonoBehaviours; no proprietary schemas or extracted assets are shipped here.
"""
from pathlib import Path


def resolve_part_binding(part: dict, bone_names: dict[int, str], mesh_tree: dict,
                         skeleton: list[dict]) -> dict:
    """Bind one appearance part to the part Mesh's own ordered skin bones.

    The Mesh's parallel m_BindPose/m_BoneNameHashes arrays are authoritative for
    which bones are skinned and in which order; the appearance declaration may
    list extra bones the mesh does not weight, but every mesh bone must be
    represented by a declared bone.
    """
    if type(part.get('ParentBone')) is not int or type(part.get('RootBone')) is not int:
        raise ValueError('appearance bone identifiers must be integers')
    if part['ParentBone'] != 0:
        raise ValueError('bone-parented appearance parts are not yet supported')
    attached = part.get('AttachedBones')
    if not isinstance(attached, list) or not attached:
        raise ValueError('appearance declares no attached bones')
    if any(type(n) is not int for n in attached):
        raise ValueError('appearance bone identifiers must be integers')
    try:
        declared = [bone_names[n] for n in attached]
        root = bone_names[part['RootBone']]
    except (KeyError, TypeError) as ex:
        raise ValueError('appearance references an unresolved bone') from ex
    from bridge.content.sr2_asset_extract import resolve_mesh_skin_bones
    return {'skin_bones': resolve_mesh_skin_bones(mesh_tree, skeleton, declared),
            'root_bone': root}


def source_object_index(assets) -> dict:
    """Index the original resources.assets snapshot, not lazy dependencies."""
    objects = {}
    for obj in assets._objects:
        if type(obj.path_id) is not int or obj.path_id in objects:
            raise ValueError('ambiguous or malformed source object identifier')
        objects[obj.path_id] = obj
    return objects


def _named_roots(assets, objects, name):
    """Path ids of the GameObjects called `name`. The name index is built once per game file: scanning every GameObject
    for each call took minutes on the addressables bundles."""
    index = getattr(assets, '_go_name_index', None)
    if index is None:
        index = {}
        for o in objects.values():
            if o.type.name == 'GameObject':
                index.setdefault(o.read_typetree().get('m_Name'), []).append(o.path_id)
        try:
            assets._go_name_index = index
        except AttributeError:
            pass
    return list(index.get(name, []))


ALLOW_EXTERNAL_REFS = False       # bundle-sourced extraction: every bundle shares one index, path ids are globally unique


def source_ref(pointer: dict) -> int:
    """Bounded local PPtr contract (bool/float identifiers are not integers)."""
    if (not isinstance(pointer, dict) or type(pointer.get('m_FileID')) is not int
            or type(pointer.get('m_PathID')) is not int):
        raise ValueError('source appearance reference identifiers must be integers')
    if pointer['m_FileID'] != 0 and not ALLOW_EXTERNAL_REFS:
        raise ValueError('external source appearance reference is unsupported')
    # addressables bundles use signed 64-bit path ids: negative is a valid identifier there (and only there)
    if pointer['m_PathID'] < 0 and not ALLOW_EXTERNAL_REFS:
        raise ValueError('source appearance reference identifier is negative')
    return pointer['m_PathID']


def _definition_nodes(generator):
    """Correct only duplicate top-level registries in generated inheritance.

    TypeTreeGeneratorAPI can emit a ManagedReferencesRegistry at the end of the
    base class AND the derived SlimeDefinition. Unity stores one at the end of
    the complete object. Preserve every source field and the final registry;
    read the complete object with UnityPy's normal byte-count validation.
    """
    node = generator.get_nodes_up('Assembly-CSharp.dll', 'SlimeDefinition')
    registries = [c for c in node.m_Children
                  if c.m_Type == 'ManagedReferencesRegistry' and c.m_Name == 'references']
    if len(registries) <= 1:
        return node
    if node.m_Children[-1] is not registries[-1]:
        raise ValueError('unsupported generated definition registry layout')
    normalized = type(node)(node.m_Level, node.m_Type, node.m_Name, node.m_ByteSize,
                            node.m_Version, m_MetaFlag=node.m_MetaFlag)
    normalized.m_Children = [c for c in node.m_Children
                             if not any(c is registry for registry in registries[:-1])]
    return normalized


def _static_face_bindings(objects, headers, tree, applicator, root, parts):
    """Resolve only the serialized initial expression and authored LOD0 passes.

    The shared SMR's serialized material is NOT the applied species appearance.
    Dynamic appearance sets, expressions and resource-file references are outside
    this contract; none are guessed from material/atlas names.
    """
    wanted = {p['mesh_obj'].path_id for p in parts if p['role'] == 'face'}
    if not wanted:
        return {}

    def typed(pointer, kind=None, class_name=None):
        pid = source_ref(pointer)
        if not pid or pid not in objects:
            raise ValueError('source face reference is missing')
        if kind and objects[pid].type.name != kind:
            raise ValueError(f'source face reference is not a {kind}')
        if class_name and headers.get(pid) != class_name:
            raise ValueError(f'source face reference is not a {class_name}')
        return pid

    def flag(value):
        if type(value) not in (bool, int) or value not in (0, 1):
            raise ValueError('source face flags must be boolean values')
        return bool(value)

    definition = tree(typed(applicator['SlimeDefinition'], class_name='SlimeDefinition'))
    if source_ref(definition['prefab']) != root:
        raise ValueError('source definition does not reference the species prefab')
    defaults = definition['AppearancesDefault']
    if not isinstance(defaults, list) or len(defaults) != 1:
        raise ValueError('source species has no unique default appearance')
    appearance_id = typed(defaults[0], class_name='SlimeAppearance')
    if source_ref(applicator['Appearance']) not in (0, appearance_id):
        raise ValueError('non-default source appearance is unsupported')
    applied = tree(appearance_id)
    expression = applicator['SlimeExpression']
    if type(expression) is not int or expression <= 0:
        raise ValueError('source face expression must be a positive integer')
    face = tree(typed(applied['_face'], class_name='SlimeFace'))
    expressions = [r for r in face['ExpressionFaces']
                   if type(r['SlimeExpression']) is int and r['SlimeExpression'] == expression]
    if len(expressions) != 1:
        raise ValueError('source face has no unique initial expression')
    expression_row = expressions[0]
    result = {}
    for structure in applied['_structures']:
        if not flag(structure['SupportsFaces']):
            continue
        element = tree(typed(structure['Element'], class_name='SlimeAppearanceElement'))
        # Prefabs are SlimeAppearanceObject references indexed by LOD, not GOs.
        part_id = typed(element['Prefabs'][0], class_name='SlimeAppearanceObject')
        go = tree(typed(tree(part_id)['m_GameObject'], kind='GameObject'))
        component_ids = [typed(c['component']) for c in go['m_Component']]
        renderers = [pid for pid in component_ids if objects[pid].type.name == 'SkinnedMeshRenderer']
        if len(renderers) != 1:
            raise ValueError('source face element has no unique renderer')
        mesh = typed(tree(renderers[0])['m_Mesh'], kind='Mesh')
        if mesh not in wanted:
            raise ValueError('source LOD0 face element does not match an extracted face mesh')
        override = structure['ElementMaterials'][0]
        effective = override['Materials'] if flag(override['OverrideDefaults']) else structure['DefaultMaterials']
        if not effective:
            raise ValueError('source face has no base material')
        materials = [{'face_role': 'base-null', 'source_path_id': typed(p, kind='Material')}
                     for p in effective]
        rules = structure['FaceRules'][0]
        for key, role in (('ShowEyes', 'eyes'), ('ShowMouth', 'mouth')):
            if flag(rules[key]):
                pointer = expression_row['Eyes' if role == 'eyes' else 'Mouth']
                materials.append({'face_role': role, 'source_path_id': typed(pointer, kind='Material')})
        if mesh in result:
            raise ValueError('ambiguous source face structure for shared mesh')
        result[mesh] = {'source_face': True, 'source_face_expression': expression,
                        'source_face_materials': materials}
    if set(result) != wanted:
        raise ValueError('source appearance face structure is missing for an extracted face mesh')
    return result


def collect_source_appearance(assets, species: str, parts: list[dict], skeleton=None) -> dict:
    """Resolve standalone LOD0 appearance parts through the species applicator.

    Each part binds to its Mesh's own ordered m_BindPose/m_BoneNameHashes arrays.
    Equal bind matrices cannot distinguish a jiggle parent from a skin child, so
    the part mesh decides which transforms are skinned while the appearance
    declaration names them (AttachedBones/RootBone may refer to the declared
    jiggle parent of a mesh-bound skin child, e.g. the shared TOP bone).
    """
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

    objects = source_object_index(assets)
    if skeleton is None:
        from bridge.content.sr2_asset_extract import collect_bone_tree
        skeleton = collect_bone_tree(assets, species)
    if not skeleton:
        raise ValueError('source species has no skeleton')
    headers = getattr(assets, '_class_headers', None)
    if headers is None:
        headers = {}
        for o in objects.values():
            if o.type.name == 'MonoBehaviour':
                try:
                    head = o.parse_monobehaviour_head()
                    script = head.m_Script.deref().read()
                    headers[o.path_id] = script.m_ClassName
                except Exception:  # noqa: BLE001 - a behaviour whose script is not in the loaded files is none of the species' components
                    headers[o.path_id] = ''
        try:
            assets._class_headers = headers
        except AttributeError:
            pass

    version = next(iter(objects.values())).assets_file.unity_version
    generator = TypeTreeGenerator(version)
    # MelonLoader already produces the exact Cpp2IL schema locally. Prefer it:
    # load_local_game's native path cannot generate these nodes on the LAB build.
    schema_dir = (assets.data_dir.parent / 'MelonLoader' / 'Dependencies' /
                  'Il2CppAssemblyGenerator' / 'Cpp2IL' / 'cpp2il_out')
    if (schema_dir / 'Assembly-CSharp.dll').is_file():
        generator.load_local_dll_folder(str(schema_dir))
    else:
        generator.load_local_game(str(assets.data_dir.parent))
    assets.env.typetree_generator = generator

    trees = {}

    def tree(pid):
        if not pid or pid not in objects:
            raise ValueError('source appearance reference is missing')
        if pid not in trees:
            if headers.get(pid) == 'SlimeDefinition':
                trees[pid] = objects[pid].read_typetree(nodes=_definition_nodes(generator))
            else:
                trees[pid] = objects[pid].read_typetree()
        return trees[pid]

    ref = source_ref

    def components(go):
        return [ref(c['component']) for c in tree(go)['m_Component']]

    def transform(go):
        candidates = [i for i in components(go) if objects[i].type.name == 'Transform']
        if len(candidates) != 1:
            raise ValueError('source appearance GameObject has no unique transform')
        return tree(candidates[0])

    def component(go, class_name):
        candidates = [i for i in components(go) if headers.get(i) == class_name]
        if len(candidates) != 1:
            raise ValueError(f'source GameObject has no unique {class_name}')
        return tree(candidates[0])

    roots = _named_roots(assets, objects, f'slime{species}')
    if len(roots) != 1:
        raise ValueError('source species has no unique prefab')
    applicator = component(roots[0], 'SlimeAppearanceApplicator')
    try:
        faces = _static_face_bindings(objects, headers, tree, applicator, roots[0], parts)
    except (KeyError, IndexError, TypeError) as ex:
        raise ValueError('source face metadata is incomplete or malformed') from ex
    bone_names = {}
    for b in applicator['Bones']:
        name = tree(ref(b['BoneObject']))['m_Name']
        if b['Bone'] in bone_names:
            raise ValueError('duplicate source bone enum')
        bone_names[b['Bone']] = name
    if len(set(bone_names.values())) != len(bone_names):
        raise ValueError('duplicate source bone name is unsupported')
    appearance = transform(ref(applicator['RootAppearanceObject']))
    # Current portable roots represent the Appearance transform, not the actor.
    # Preserve its actual local TRS while leaving renderer-local geometry intact.
    def trs(t):
        return {'local_position': [t['m_LocalPosition'][k] for k in 'xyz'],
                'local_rotation': [t['m_LocalRotation'][k] for k in 'xyzw'],
                'local_scale': [t['m_LocalScale'][k] for k in 'xyz']}

    by_mesh = {}
    wanted = {p['mesh_obj'].path_id for p in parts}
    for o in objects.values():
        if o.type.name != 'SkinnedMeshRenderer':
            continue
        t = tree(o.path_id)
        if t['m_Mesh']['m_FileID'] != 0:
            continue                      # a renderer whose mesh lives in another bundle is none of this species' parts
        mesh_id = ref(t['m_Mesh'])
        if mesh_id not in wanted:
            continue
        go = ref(t['m_GameObject'])
        part_transform = transform(go)
        if ref(part_transform['m_Father']) != 0:
            raise ValueError('nested source appearance part is not yet supported')
        part_tree = component(go, 'SlimeAppearanceObject')
        binding = resolve_part_binding(part_tree, bone_names, tree(mesh_id), skeleton)
        binding.update(trs(part_transform))
        binding.update(faces.get(mesh_id, {}))
        if mesh_id in by_mesh and by_mesh[mesh_id] != binding:
            raise ValueError('ambiguous source appearance binding for shared mesh')
        by_mesh[mesh_id] = binding
    if set(by_mesh) != wanted:
        raise ValueError('source appearance binding missing for an extracted mesh')
    return {'visual_transform': trs(appearance),
            'parts': {p['mesh_name']: by_mesh[p['mesh_obj'].path_id] for p in parts}}
