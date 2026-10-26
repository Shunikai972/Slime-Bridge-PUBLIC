"""Optional v1 import metadata: validate declarations, never derive them from model shape."""
import math

from bridge.content.canonical import REALMS, diag

FIELDS = {'transform', 'collision', 'face_anchors', 'animation_binding', 'source_realms',
          'intended_realms', 'target_parent_availability'}


def readiness(manifest):
    metadata = manifest.get('import_metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    missing = sorted(FIELDS - {'target_parent_availability'} - set(metadata))
    for field in ('scale', 'orientation_xyzw', 'pivot'):
        if not isinstance(metadata.get('transform'), dict) or field not in metadata['transform']:
            missing.append('transform.' + field)
    if isinstance(metadata.get('collision'), dict):
        if 'mode' not in metadata['collision']:
            missing.append('collision.mode')
        elif metadata['collision']['mode'] == 'mesh' and 'asset' not in metadata['collision']:
            missing.append('collision.asset')
    if isinstance(metadata.get('animation_binding'), dict):
        binding = metadata['animation_binding']
        if 'mode' not in binding:
            missing.append('animation_binding.mode')
        elif binding['mode'] == 'skeletal':
            missing += ['animation_binding.' + key for key in ('root_bone', 'bones') if key not in binding]
    provenance = manifest.get('provenance')
    if not isinstance(provenance, dict) or provenance.get('user_owned') is not True:
        missing.append('provenance.user_owned')
    return {'status': 'METADATA_INCOMPLETE' if missing else 'DECLARED', 'missing': sorted(set(missing)),
            'evidence': 'USER_DECLARED_NOT_RUNTIME_VERIFIED'}


def validate(manifest, declared, subject):
    diagnostics = []

    def bad(detail):
        diagnostics.append(diag('INVALID_IMPORT_METADATA', 'error', subject, detail))

    def vector(value, size, positive=False, quaternion=False):
        if not isinstance(value, list) or len(value) != size or not all(
                type(number) in (int, float) and math.isfinite(number) and abs(number) <= 1e20 for number in value):
            return False
        if positive and not all(number > 0 for number in value):
            return False
        return not quaternion or abs(sum(number * number for number in value) - 1) <= 0.001

    def shape(value, keys, label):
        if not isinstance(value, dict) or set(value) - keys:
            bad(label + ': invalid object/unknown fields')
            return False
        return True

    provenance = manifest.get('provenance')
    if isinstance(provenance, dict) and 'user_owned' in provenance and provenance['user_owned'] is not True:
        bad('provenance.user_owned must explicitly be true when supplied')
    if 'import_metadata' not in manifest:
        return diagnostics  # legacy v1 remains valid, CLI readiness still reports missing declarations
    metadata = manifest['import_metadata']
    if not shape(metadata, FIELDS, 'import_metadata'):
        return diagnostics
    transform = metadata.get('transform')
    if 'transform' in metadata and shape(transform, {'scale', 'orientation_xyzw', 'pivot'}, 'transform'):
        for key, size in (('scale', 3), ('pivot', 3), ('orientation_xyzw', 4)):
            if key in transform and not vector(transform[key], size, key == 'scale', key == 'orientation_xyzw'):
                bad('transform.' + key + ': finite vector; scale positive; quaternion unit length')
    collision = metadata.get('collision')
    if 'collision' in metadata and shape(collision, {'mode', 'asset'}, 'collision'):
        if 'mode' in collision and collision['mode'] not in ('parent_derived', 'none', 'mesh'):
            bad('collision.mode')
        if 'asset' in collision and (not isinstance(collision['asset'], str) or
                                    declared.get(collision['asset']) != 'mesh' or collision.get('mode') != 'mesh'):
            bad('collision.asset must reference a declared mesh with mode mesh')
    anchors = metadata.get('face_anchors')
    if 'face_anchors' in metadata:
        if not isinstance(anchors, list) or len(anchors) > 128:
            bad('face_anchors must be a bounded list')
        else:
            names = set()
            for anchor in anchors:
                if not shape(anchor, {'name', 'position', 'orientation_xyzw', 'bone'}, 'face_anchor'):
                    continue
                name = anchor.get('name')
                if not isinstance(name, str) or not name or len(name) > 80 or name in names or \
                        not vector(anchor.get('position'), 3) or not vector(anchor.get('orientation_xyzw'), 4, quaternion=True):
                    bad('face_anchor: unique name and finite position/unit quaternion required')
                else:
                    names.add(name)
                if 'bone' in anchor and (not isinstance(anchor['bone'], str) or not anchor['bone']):
                    bad('face_anchor.bone')
    binding = metadata.get('animation_binding')
    if 'animation_binding' in metadata and shape(binding, {'mode', 'root_bone', 'bones'}, 'animation_binding'):
        mode = binding.get('mode')
        if 'mode' in binding and mode not in ('static', 'skeletal'):
            bad('animation_binding.mode')
        if mode == 'static' and (manifest.get('animations') or any(
                isinstance(row, dict) and row.get('skinned') for row in manifest.get('renderers', []))):
            bad('static animation binding conflicts with clips/skinned renderers')
        if 'bones' in binding:
            bones = binding['bones']
            if not isinstance(bones, list) or not 0 < len(bones) <= 1024 or not all(
                    isinstance(bone, str) and 0 < len(bone) <= 160 for bone in bones) or len(set(bones)) != len(bones):
                bad('animation_binding.bones')
            elif 'root_bone' in binding and binding['root_bone'] not in bones:
                bad('animation_binding.root_bone must be in bones')
            elif isinstance(anchors, list) and any(isinstance(anchor, dict) and
                    anchor.get('bone') is not None and anchor['bone'] not in bones for anchor in anchors):
                bad('face anchor references unknown bone')
        if 'root_bone' in binding and (not isinstance(binding['root_bone'], str) or not binding['root_bone']):
            bad('animation_binding.root_bone')
        if mode != 'skeletal' and set(binding) & {'root_bone', 'bones'}:
            bad('bone bindings require skeletal mode')
    for key in ('source_realms', 'intended_realms'):
        if key in metadata:
            realms = metadata[key]
            if not isinstance(realms, list) or not all(isinstance(realm, str) and realm in REALMS for realm in realms) or \
                    len(set(realms)) != len(realms) or (key == 'intended_realms' and not realms):
                bad(key + ': unique SR1/SR2 realm list; intended realms nonempty')
    if 'target_parent_availability' in metadata:
        available = metadata['target_parent_availability']
        if not shape(available, set(REALMS), 'target_parent_availability'):
            pass
        else:
            for realm, parents in available.items():
                if not isinstance(parents, list) or not all(isinstance(parent, str) and
                        parent in manifest.get('parents', []) for parent in parents) or len(set(parents)) != len(parents):
                    bad('target_parent_availability.' + realm)
            diagnostics.append(diag('PARENT_DECLARATIONS_UNVERIFIED', 'warning', subject,
                                    'package declarations do not replace reconciled target_context'))
    missing = readiness(manifest)['missing']
    if missing:
        diagnostics.append(diag('IMPORT_METADATA_INCOMPLETE', 'warning', subject, ', '.join(missing)))
    return diagnostics
