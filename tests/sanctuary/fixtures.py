"""A tiny synthetic sanctuary kit (names and numbers only) so layout/plugin tests never need a game install."""
import copy

PIECES = {
    'woodPlat09': ('polySurface1019', [[0.19693, 0.01066, 0.47419], [2.92326, 0.29076, 2.99104]], [1.5] * 3),
    'woodPlat01': ('polySurface413', [[0.0, 0.0, 0.0], [6.42415, 0.25832, 1.62293]], [1.5] * 3),
    'woodPlat03': ('polySurface600', [[0.0, -0.018, 0.0], [1.8, 0.2, 1.6]], [1.5] * 3),
    'woodPosts01': ('polySurface1258', [[-0.15, 4.05, 0.0], [0.55, 4.7, 0.6]], [2.2] * 3),
    'woodTorch01': ('polySurface1197', [[0.0, 0.97, 0.0], [0.35, 1.45, 0.35]], [1.5] * 3),
    'mtnBase02': ('mtnBase02', [[0.0, -3.61914, 0.0], [6.35, 3.6, 6.35]], [3.0] * 3),
    'cliffBase02': ('cliffBase02', [[0.0, -3.61914, 0.0], [6.35, 3.6, 6.35]], [1.5] * 3),
    'cliffBase04': ('cliffBase04', [[0.0, -4.86678, 0.0], [10.2, 4.8, 7.5]], [1.4] * 3),
    'ruinPillar01': ('ruinPillar01', [[0.04, 6.05, 0.02], [1.7, 6.05, 2.0]], [0.676] * 3),
    'ruinBlock03': ('ruinBlock03', [[0.0, 1.02, 0.0], [1.05, 1.05, 1.05]], [1.0] * 3),
    'ruinBarrierFrame': ('ruinBarrierFrame', [[0.0, 6.06, 0.0], [7.1, 6.2, 0.75]], [2.3, 1.4, 0.58]),
    'ruinBarrierDoor': ('ruinBarrierDoor', [[0.0, 6.0, 0.0], [6.05, 6.05, 0.5]], [2.3, 1.4, 0.58]),
    'ruinPlatform05': ('ruinPlatform05', [[0.0, 0.0, 0.0], [7.4, 1.7, 7.1]], [1.0] * 3),
    'ruinPath01': ('ruinPath01', [[0.0, 0.0, 0.0], [3.0, 0.1, 1.6]], [1.0] * 3),
    'ruinBlock01': ('ruinBlock01', [[0.0, 1.7, 0.0], [1.7, 1.7, 1.7]], [1.0] * 3),
    'rockMossFlat01': ('rock_round', [[0.0, -0.1, 0.0], [1.1, 0.6, 0.85]], [2.0] * 3),
    'rockMossTall01': ('rock_tall', [[0.0, 1.0, 0.0], [1.0, 2.0, 1.0]], [2.0] * 3),
    'rockMossBoxy01': ('rock_square', [[0.0, 0.5, 0.0], [1.2, 1.0, 1.2]], [2.0] * 3),
    'grassThinSprig01_LOD0': ('grassThinSprig01_LOD0', [[0.0, 1.0, 0.0], [0.6, 1.1, 0.5]], [1.0] * 3),
}


def _compound(key, root, parts):
    nodes = []
    for name, mesh, position, scale, extent in parts:
        nodes.append({'name': name, 'mesh': mesh, 'materials': ['objTreeBark01'], 'bounds': {'center': [0.0, extent[1], 0.0], 'extent': extent},
                      'local': {'position': position, 'rotation': [0.0, 0.0, 0.0, 1.0], 'scale': scale}})
    return {'key': key, 'root': root, 'nodes': nodes}


FLORA = [
    _compound('tree.reef', 'treeReef02', [('treeTrunk01', 'treeTrunk01', [0.0, 0.0, 0.0], [0.84] * 3, [1.4, 6.9, 1.4]),
                                          ('treeLeaves01', 'treeLeaves01', [0.1, 11.0, 0.0], [1.0] * 3, [3.6, 1.5, 4.8])]),
    _compound('tree.pogo', 'treePogo02', [('tree_pogo', 'tree_pogo', [0.0, 0.0, 0.0], [1.0] * 3, [1.0, 5.0, 1.0])]),
    _compound('flowers.bed', 'decoRuinFlowers01', [('flowerBed01', 'flowerBed', [0.0, 0.0, 0.0], [1.0] * 3, [1.5, 0.3, 1.5])]),
    _compound('flower.glow', 'floraMoss04', [('growthFlower01', 'growthFlower01', [0.0, 0.0, 0.0], [0.6] * 3, [1.3, 2.7, 1.3])]),
]
# name: (slime_id, plort_id, status)
SLIMES = {
    'PINK': (3, 6, 'native'), 'BOOM': (21, 25, 'native'), 'ROCK': (2, 5, 'native'), 'TABBY': (40, 50, 'native'),
    'CRYSTAL': (60, 70, 'synthesized'), 'FIRE': (61, 71, 'synthesized'), 'PUDDLE': (62, 72, 'synthesized'),
    'SABER': (63, 73, 'synthesized'), 'GOLD': (11, 7, 'synthesized'), 'HUNTER': (64, 74, 'synthesized'),
}
EXCLUDED = {'QUICKSILVER': 'event-only species', 'GLITCH': 'technical/debug species', 'TARR': 'no collectable plort'}


def _node(name, mesh=None, materials=None, components=None, path=None):
    return {'path': path or name, 'name': name, 'active': True,
            'local': {'position': [0.0, 0.0, 0.0], 'rotation': [0.0, 0.0, 0.0, 1.0], 'scale': [1.0, 1.0, 1.0]},
            'render': {'mesh': mesh, 'materials': [{'name': m} for m in materials], 'enabled': True} if mesh else None,
            'components': components or []}


def _statue(key):
    return {'construct': f'PlortSwitch_{key.lower()}', 'catch_name': key + '_PLORT', 'nodes': [
        _node(f'PlortSwitch_{key.lower()}', components=[
            {'type': 'SphereCollider', 'trigger': True, 'center': [0.0, 1.0, 1.5], 'radius': 0.75},
            {'type': 'SphereCollider', 'trigger': False, 'center': [0.0, 1.14, 0.02], 'radius': 1.59}]),
        _node('Statue', 'ruinStatue03', [f'objPlortStatue{key.title()}']),
        _node('Eyes', 'ruinStatue03_eyes', [f'plortEye{key.title()}']),
        _node('Activate Toggle'),
        _node('plort', 'plort', [f'plort{key.title()}Base'], path='Activate Toggle/plort'),
        _node('Point light', components=[{'type': 'Light', 'light_type': 2, 'color': [1.0, 0.43, 0.66, 1.0],
                                          'intensity': 5.0, 'range': 3.0, 'spot_angle': 30.0, 'shadows': 0}],
              path='Activate Toggle/Point light')]}


def synthetic_kit():
    pieces = []
    for name, (mesh, (center, extent), scale) in PIECES.items():
        pieces.append({'name': name, 'mesh': mesh, 'bounds': {'center': center, 'extent': extent},
                       'materials': ['objPlatWood01'], 'example_world_scale': scale, 'uses': 3})
    statues, catalog, materials = [], [], {}
    for key, (slime_id, plort_id, status) in SLIMES.items():
        materials[f'plort{key.title()}Base'] = {'name': f'plort{key.title()}Base', 'colors': {
            '_TopColor': [1.0, 0.4, 0.6, 1.0], '_MiddleColor': [1.0, 0.2, 0.3, 1.0], '_BottomColor': [0.2, 0.2, 0.6, 1.0]}}
        materials[f'plortEye{key.title()}'] = {'name': f'plortEye{key.title()}', 'colors': {}}
        row = {'slime': key, 'slime_id': slime_id, 'plort_id': plort_id, 'plort': key + '_PLORT', 'status': status,
               'plort_display_material': f'plort{key.title()}Base'}
        if status == 'native':
            row.update(constructs=[f'PlortSwitch_{key.lower()}'], statue_material=f'objPlortStatue{key.title()}')
            statues.append(_statue(key))
        else:
            row.update(template='PlortSwitch_pink')
        catalog.append(row)
    for key, reason in EXCLUDED.items():
        catalog.append({'slime': key, 'slime_id': 99, 'plort_id': None, 'plort': None, 'status': 'excluded', 'reason': reason})
    return {
        'schema': 'bridge-sanctuary-kit', 'schema_version': 1, 'source_game': 'SR1',
        'identifiables': {'plorts': {k: v[1] for k, v in SLIMES.items()}, 'slimes': {k: v[0] for k, v in SLIMES.items()}},
        'statues': statues, 'statue_catalog': catalog, 'materials': materials, 'pieces': pieces, 'flora': copy.deepcopy(FLORA),
        'walkway': {'cell': 'cellSea_Bridgeway', 'anchor': {'position': [-91.5, 3.0, 287.8], 'heading_y': 67.8},
                    'end_pieces': [{'name': 'woodPlat09', 'mesh': 'polySurface1019', 'distance': 2.36, 'path': 'x',
                                    'world': {'position': [-89.475, 2.254, 288.754], 'scale': [1.5] * 3,
                                              'rotation': [0.044, 0.30, -0.018, 0.95], 'heading_y': 35.2}}], 'pieces': []},
    }


def copy_kit():
    return copy.deepcopy(synthetic_kit())
