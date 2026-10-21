"""Neutralization: one species' dependency closure + registry facts -> canonical slime IR.

Structure is read only from typed graph edges (has_prefab, has_child, has_component,
uses_mesh/material/bone, has_face, uses_animator, has_clip, produces_plort, has_icon, ...).
Nothing is inferred from names. Material semantics and behaviour capabilities come from the
profile-driven modules; the IR is validated before it is returned.
"""

import re
from collections import deque

from bridge.content.canonical import DataEvidence, Status, diag, sort_diagnostics, worst_status
from bridge.content.graph import NodeKind, Resolution
from bridge.content.ir import IR_SCHEMA, IR_SCHEMA_VERSION, validate_ir
from bridge.registry.reconcile import derive_canonical
from .capabilities import source_capabilities
from .materials import neutralize as neutralize_material

K = NodeKind
_RENDERERS = (K.RENDERER, K.SKINNED_RENDERER)
_MATERIAL_STATUS = {'READY': Status.EXPERIMENTAL, 'DEGRADED': Status.EXPERIMENTAL,
                    'PENDING_ADAPTER': Status.PENDING_ADAPTER, 'QUARANTINED': Status.QUARANTINED}


def _slot_key(slot):
    return (0, int(slot), '') if slot.isdigit() else (1, 0, slot)


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_') or 'unnamed'


class _View:
    def __init__(self, graph, analysis):
        self.graph, self.nodes = graph, graph.nodes
        self.closure = set(analysis['closure'])
        self.critical = set(analysis['critical'])

    def ok(self, nid):
        return nid in self.closure and self.nodes[nid]['resolution'] == Resolution.RESOLVED

    def edges(self, nid, relation):
        return [e for e in self.graph.out.get(nid, []) if e['kind'] == relation and e['to'] in self.closure]

    def targets(self, nid, relation, kinds=None):
        return sorted({e['to'] for e in self.edges(nid, relation)
                       if self.ok(e['to']) and (kinds is None or self.nodes[e['to']]['kind'] in kinds)})

    def hashed(self, nid):
        return {'id': nid, 'hash': self.nodes[nid]['hash']}


def _hierarchy(view, prefab, diags):
    if prefab is None:
        return []
    rows, parent_of, queue = [{'id': prefab, 'parent': None, 'kind': 'prefab'}], {prefab: None}, deque([prefab])
    while queue:
        current = queue.popleft()
        for child in view.targets(current, 'has_child', (K.TRANSFORM,)):
            if child in parent_of:
                diags.append(diag('HIERARCHY_NOT_TREE', 'error', child, f'second parent {current}'))
                continue
            parent_of[child] = current
            rows.append({'id': child, 'parent': current, 'kind': 'transform'})
            queue.append(child)
    return rows


def _renderer(view, rid, host, diags):
    kind = 'skinned' if view.nodes[rid]['kind'] == K.SKINNED_RENDERER else 'mesh'
    meshes = view.targets(rid, 'uses_mesh', (K.MESH,))
    if len(meshes) > 1:
        diags.append(diag('MULTIPLE_MESHES', 'error', rid, ','.join(meshes)))
    slots = sorted(view.edges(rid, 'uses_material'), key=lambda e: _slot_key(e['slot']))
    materials = [e['to'] if view.ok(e['to']) else None for e in slots]
    bone_edges = view.edges(rid, 'uses_bone')
    numeric = sorted((e for e in bone_edges if e['slot'].isdigit()), key=lambda e: int(e['slot']))
    bones = [e['to'] for e in numeric]
    roots = [e['to'] for e in bone_edges if e['slot'] == 'root']
    indices = [int(e['slot']) for e in numeric]
    unknown_slots = sorted(e['slot'] for e in bone_edges if e['slot'] != 'root' and not e['slot'].isdigit())
    if indices != list(range(len(indices))) or unknown_slots or len(roots) > 1:
        # Bone index == skin weight index: a gap, duplicate or unknown slot would silently rebind weights.
        diags.append(diag('BONE_ORDER_INVALID', 'error', rid,
                          f'indices={indices} unknown_slots={unknown_slots} roots={len(roots)}'))
    return {'id': rid, 'kind': kind, 'node': host, 'mesh': meshes[0] if meshes else None, 'materials': materials,
            'bones': bones, 'root_bone': roots[0] if len(roots) == 1 else None}


def build_ir(graph, analysis, *, canonical_id, source_realm, build_fingerprint, registry_native, identity,
             plort_canonicals, material_mapper, material_profile, behaviour_profile, converter_version,
             data_evidence):
    """Return {'ir', 'materials', 'capabilities', 'status', 'diagnostics'} for one species."""
    view = _View(graph, analysis)
    root = analysis['root']
    diags, reasons = [], []
    prefabs = view.targets(root, 'has_prefab', (K.PREFAB,))
    if len(prefabs) > 1:
        diags.append(diag('MULTIPLE_PREFABS', 'error', root, ','.join(prefabs)))
    prefab = prefabs[0] if prefabs else None
    hierarchy = _hierarchy(view, prefab, diags)
    hosts = [row['id'] for row in hierarchy]
    renderers = [_renderer(view, rid, host, diags) for host in hosts
                 for rid in view.targets(host, 'has_component', _RENDERERS)]
    for appearance in view.targets(root, 'has_appearance', (K.APPEARANCE,)):
        renderers.append(_renderer(view, appearance, None, diags))
    skinned = {r['mesh'] for r in renderers if r['kind'] == 'skinned'}
    meshes = [{'id': m, 'hash': view.nodes[m]['hash'], 'skinned': m in skinned,
               'attributes': dict(view.nodes[m]['attributes'] or {})}
              for m in sorted({r['mesh'] for r in renderers if r['mesh']})]
    resolved = {nid: view.nodes[nid]['hash'] for nid in view.closure
                if view.ok(nid) and view.nodes[nid]['kind'] == K.TEXTURE}
    semantics, materials, impacts, material_diags = [], [], [], []
    used_by_renderer = {m for r in renderers for m in r['materials'] if m}
    for mid in sorted(n for n in view.closure if view.ok(n) and view.nodes[n]['kind'] == K.MATERIAL):
        semantic = neutralize_material(mid, material_mapper.describe(view.nodes[mid], graph), material_profile,
                                       resolved)
        semantics.append(semantic)
        shaders = view.targets(mid, 'uses_shader', (K.SHADER,))
        status = _MATERIAL_STATUS[semantic['status']]
        materials.append({'id': mid, 'hash': view.nodes[mid]['hash'],
                          'shader': {'ref': shaders[0], 'hash': view.nodes[shaders[0]]['hash']} if shaders else None,
                          'semantic': {k: semantic[k] for k in ('source_shader', 'family', 'channels', 'render_state',
                                                                'unknown_properties', 'profile_entry_hash')},
                          'status': str(status)})
        if status in (Status.QUARANTINED, Status.PENDING_ADAPTER):
            # Only materials on a critical renderer path decide the species; optional ones (VFX) degrade.
            essential = mid in used_by_renderer and mid in view.critical
            reasons.append(f'MATERIAL_{status}:{mid}')
            impacts.append(status if essential else Status.EXPERIMENTAL)
            material_diags.append(diag('MATERIAL_NOT_CONVERTIBLE', 'error' if essential else 'warning', mid,
                                       semantic['status']))
    face_ids = sorted({f for host in [root, prefab, *view.targets(root, 'has_appearance')] if host
                       for f in view.targets(host, 'has_face', (K.FACE,))})
    face = None
    if face_ids:
        face = {**view.hashed(face_ids[0]),
                'expressions': view.targets(face_ids[0], 'has_expression', (K.FACE_EXPRESSION,))}
    animators = sorted({a for host in [root, *hosts] for a in view.targets(host, 'uses_animator', (K.ANIMATOR,))})
    animator = {**view.hashed(animators[0]), 'clips': view.targets(animators[0], 'has_clip', (K.ANIMATION_CLIP,))} \
        if animators else None
    if len(animators) > 1 or len(face_ids) > 1:
        diags.append(diag('MULTIPLE_FACE_OR_ANIMATOR', 'warning', root, 'first in canonical order used'))
    of_kind = lambda kind: [view.hashed(n) for n in sorted(view.closure) if view.ok(n) and view.nodes[n]['kind'] == kind]
    incoming = {}
    for edge in graph.edges:
        if edge['from'] in view.closure:
            incoming.setdefault(edge['to'], []).append(edge['kind'])
    unknown = [{'id': n, 'kind': str(view.nodes[n]['kind']), 'via': sorted(incoming.get(n, ['root']))[0]}
               for n in sorted(view.closure)
               if view.nodes[n]['kind'] == K.UNKNOWN or view.nodes[n]['resolution'] != Resolution.RESOLVED]
    caps = source_capabilities(graph, analysis, registry_native, behaviour_profile)
    if any(c['policy'] == 'UNKNOWN' for c in caps['capabilities']):
        reasons.append('UNKNOWN_BEHAVIOUR')
    root_attrs = view.nodes[root]['attributes'] or {}
    groups = root_attrs.get('food_groups') if isinstance(root_attrs.get('food_groups'), list) else []
    favourites = [f'food.{_slug(view.nodes[f]["ref"])}' for f in sorted(e['to'] for e in view.edges(root, 'favourite'))]
    plorts = []
    for edge in sorted(view.edges(root, 'produces_plort'), key=lambda e: e['to']):
        node = view.nodes[edge['to']]
        plorts.append({'canonical_id': plort_canonicals.get(node['ref']) or derive_canonical('plort', node['ref']),
                       'native_ref': node['ref'], 'hash': node['hash']})
    produces = sorted({p['canonical_id'] for p in plorts if p['canonical_id']} |
                      set(registry_native.get('produces') or []))
    diet = {'food_groups': [_slug(g) for g in groups if isinstance(g, str)], 'favourite_foods': favourites,
            'produces': produces} if (groups or favourites or produces) else None

    def associated(relation, kind, frm=(root,)):
        found = sorted({t for host in frm for t in view.targets(host, relation, (kind,))})
        if not found:
            return None
        node = view.nodes[found[0]]
        return {'node': found[0], 'hash': node['hash'], 'attributes': dict(node['attributes'] or {})}

    icons = view.targets(root, 'has_icon', (K.ICON,))
    plort_nodes = [e['to'] for e in view.edges(root, 'produces_plort') if view.ok(e['to'])]
    pedia = associated('has_pedia', K.PEDIA)
    renderable = any(r['mesh'] for r in renderers) and prefab is not None
    if not renderable:
        reasons.append('NO_RENDERABLE_MESH')
    graph_errors = [d for d in analysis['diagnostics'] if d['severity'] == 'error']
    if graph_errors:
        reasons.append('DEPENDENCY_ERRORS')
    structural_errors = [d for d in diags if d['severity'] == 'error']
    if structural_errors or graph_errors or not renderable:
        status = Status.QUARANTINED
    else:
        status = worst_status(Status.EXPERIMENTAL, *impacts,
                              *([Status.PENDING_ADAPTER] if 'UNKNOWN_BEHAVIOUR' in reasons else []))
        if status == Status.EXPERIMENTAL and data_evidence == DataEvidence.RUNTIME_ANCHORED and not reasons:
            status = Status.AVAILABLE
    if status != Status.AVAILABLE:
        reasons.append('NEEDS_LOCAL_RUNTIME_PROOF' if data_evidence != DataEvidence.SYNTHETIC else 'SYNTHETIC_SOURCE')
    asset_rows = [*meshes, *materials, *of_kind(K.TEXTURE), *of_kind(K.ANIMATION_CLIP), *of_kind(K.VFX),
                  *of_kind(K.AUDIO), *(r for r in (face, animator) if r)]
    icon = view.hashed(icons[0]) if icons else None
    ir = {
        'schema': IR_SCHEMA, 'schema_version': IR_SCHEMA_VERSION, 'canonical_id': canonical_id,
        'content_kind': 'slime', 'role': 'base',
        'identity': {'source_realm': source_realm, 'source_build_fingerprint': build_fingerprint,
                     'native_definition': {'ref': registry_native['native_ref'],
                                           'numeric_id': registry_native.get('numeric_id'),
                                           'hash': registry_native['definition_hash']},
                     'native_prefab': {'ref': view.nodes[prefab]['ref'], 'hash': view.nodes[prefab]['hash']}
                     if prefab else None,
                     'identity_basis': identity['basis'], 'identity_evidence': list(identity['evidence'])},
        'visual': {'root': prefab, 'hierarchy': hierarchy, 'meshes': meshes, 'renderers': renderers,
                   'materials': materials,
                   'textures': of_kind(K.TEXTURE), 'face': face, 'animator': animator,
                   'animation_clips': [view.hashed(c) for c in (animator['clips'] if animator else [])],
                   'vfx': of_kind(K.VFX), 'audio': of_kind(K.AUDIO), 'unknown_references': unknown},
        'gameplay': {'diet': diet, 'capabilities': caps['capabilities'], 'native_behaviours': caps['native_behaviours']},
        'associated': {'plorts': plorts, 'icon': icon,
                       'market': next((a for a in (associated('has_market', K.MARKET, plort_nodes),) if a), None),
                       'pedia': pedia,
                       'localization': associated('has_localization', K.LOCALIZATION,
                                                  (root, *([pedia['node']] if pedia else []))),
                       'spawn': associated('has_spawn', K.SPAWN)},
        'provenance': {'source_refs': sorted({root, *plort_nodes}),
                       'asset_hashes': {r['id']: r['hash'] for r in asset_rows + ([icon] if icon else [])},
                       'dependency_closure_hash': analysis['closure_hash'], 'scanner_schema_version': 1,
                       'converter_version': converter_version,
                       'synthetic': data_evidence == DataEvidence.SYNTHETIC, 'data_evidence': str(data_evidence)},
        'status': str(status), 'status_reasons': sorted(set(reasons)),
    }
    problems = validate_ir(ir)
    if problems:
        diags.append(diag('IR_INVALID', 'error', canonical_id, '; '.join(f"{p['code']}@{p['subject']}"
                                                                         for p in problems[:6])))
        diags.extend(problems)
        status = Status.QUARANTINED
    return {'ir': ir, 'ir_valid': not problems, 'materials': semantics, 'capabilities': caps,
            'status': status, 'diagnostics': sort_diagnostics(diags + material_diags + caps['diagnostics'] +
                                                             [d for s in semantics for d in s['diagnostics']])}
