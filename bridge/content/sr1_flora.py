"""Native SR1 flora recipes for the sanctuary kit: trees, flower beds, mushrooms, mossy rocks, glow sticks.

The kit stays a *description* (mesh / material names plus local transforms); the plugin resolves meshes and materials by name from
the running game, like every other sanctuary piece. A "compound" is an authored prefab that is several renderers (a tree is a
trunk, branches and leaf clouds): each renderer is stored with its transform relative to the compound root, so the layout can
place the whole thing with one position / yaw / scale.
"""
from __future__ import annotations

import re

from bridge.content.sr1_sanctuary import _mul, decompose, trs_matrix

# authored root-object name -> short key used by the layout
FLORA_COMPOUNDS = {
    'treeReef02': 'tree.reef',          # trunk + 3 branches + 4 leaf clouds (the Ranch's own trees)
    'treeMoss01': 'tree.moss',          # mossy tree: branches, roots, vines, canopies
    'treeMossStump03': 'stump.moss',    # hollow log with roots and mushrooms
    'shroomGiantB01': 'shroom.giant',   # big mushrooms
    'floraMoss04': 'flower.glow',       # glowing growth flower
    'grassCluster01': 'grass.cluster',  # ten grass tufts
    'decoRuinFlowers01': 'flowers.bed',
    'treePogo02': 'tree.pogo',
}
MAX_NODES = 40
RENDERER_OVERRIDE_DISTANCE = 0.0


def _relative_matrix(scene, go, root):
    """Product of the local matrices from *go* up to (excluding) *root*: go's transform in root space."""
    chain = []
    node = go
    while node is not None and node.path_id != root.path_id:
        chain.append(trs_matrix(*scene.local_trs(node)))
        node = scene.parent(node)
    matrix = None
    for local in reversed(chain):
        matrix = local if matrix is None else _mul(matrix, local)
    return matrix


def extract_compounds(scene, wanted=None):
    """{key: {'root', 'nodes': [{'name','mesh','materials','local'}], 'bounds'}} for the first instance of every authored root."""
    wanted = wanted or FLORA_COMPOUNDS
    found = {}
    for go in scene.game_objects:
        name = re.sub(r' \(\d+\)$', '', scene.name(go))
        if name not in wanted or name in found:
            continue
        nodes = []
        for node in scene.descendants(go, 4000):
            if node.path_id == go.path_id:
                pair = scene.describe_renderer_pair(node)
                matrix = None
            else:
                pair = scene.describe_renderer_pair(node)
                matrix = _relative_matrix(scene, node, go)
            if not pair or not pair['mesh'] or not pair['enabled']:
                continue
            if 'shadow' in scene.name(node).lower():
                continue                                    # baked fake-shadow cards: the game's lighting does that already
            if any(m is None for m in pair['materials']):
                continue
            if matrix is None:
                local = {'position': [0.0, 0.0, 0.0], 'rotation': [0.0, 0.0, 0.0, 1.0], 'scale': [1.0, 1.0, 1.0]}
            else:
                position, rotation, scale = decompose(matrix)
                local = {'position': [round(v, 5) for v in position], 'rotation': [round(v, 6) for v in rotation],
                         'scale': [round(v, 5) for v in scale]}
            nodes.append({'name': re.sub(r' \(\d+\)$', '', scene.name(node)), 'mesh': pair['mesh'],
                          'materials': [m['name'] for m in pair['materials']], 'bounds': pair['bounds'], 'local': local})
        if nodes:
            found[name] = {'key': wanted[name], 'root': name, 'nodes': nodes[:MAX_NODES]}
    return [found[name] for name in sorted(found)]
