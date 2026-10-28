"""Canonical launcher preference schema and normalization without application I/O."""

import json

from bridge.core import diagnostics as D
from bridge.core import schema as S

SETTINGS_SCHEMA = 'bridge-launcher-settings'
MAX_BYTES = 1024 * 1024
VERSION = 4
DEFAULTS = {
    'schema': SETTINGS_SCHEMA, 'schema_version': VERSION,
    'games': {'SR1': {'path': None, 'loader_path': None}, 'SR2': {'path': None, 'loader_path': None}},
    'default_profile': None,
    'transitions': {'mode': 'WARM', 'overlay': True, 'overlay_style': 'aurora', 'background_audio': 'DUCK'},
    'servers': {'default_mode': 'HYBRID', 'favourites': [], 'recent': [], 'directory_enabled': True},
    'content': {'auto_build': False, 'generated_path': 'Lab/GeneratedContent', 'auto_reverify_on_update': True,
                'user_largos_root': None},
    'species': {'foreign_spawn': {'mode': 'NATIVE_ONLY', 'overrides': {}}},
    'bridge': {'sr1_bridge_access_mode': 'ENDGAME', 'sr1_gold_plort_gate_cost': 10, 'dynamic_statue_requirements': False,
               'sr1_portal_requires_statues': True,
               'sr2_portal_enabled': True, 'sr2_portal_gate_enabled': True, 'sr2_portal_cost': 250000, 'currency_mode': 'SEPARATE',
               'cross_realm_chat': True, 'transition_mode': 'AUTO', 'travel_save_mode': 'LINKED', 'travel_link_mode': 'AUTO', 'background_policy': 'FREEZE', 'travel_carry': 'ALL'},
    'updates': {'channel': 'stable', 'check_on_start': True},
    'diagnostics': {'level': 'NORMAL', 'share_anonymous': False},
    'ui': {'language': 'en', 'reduce_motion': False, 'theme': 'auto'},
    'advanced': {'show_developer_tools': False, 'synthetic_demo': False},
}
_PATH = S.optional(S.string(max_length=1024))
_SPECIES_ID = S.string(r'slime\.[a-z0-9]+(?:_[a-z0-9]+)*', max_length=64)
SPEC = S.obj({
    'schema': S.enum(SETTINGS_SCHEMA), 'schema_version': S.enum(VERSION),
    'games': S.obj({realm: S.obj({'path': _PATH, 'loader_path': _PATH}) for realm in ('SR1', 'SR2')}),
    'default_profile': S.optional(S.string(r'[0-9a-f]{32}')),
    'transitions': S.obj({'mode': S.enum('WARM', 'COLD'), 'overlay': S.boolean(),
                          'overlay_style': S.enum('aurora', 'minimal'),
                          'background_audio': S.enum('DUCK', 'MUTE', 'KEEP')}),
    'servers': S.obj({'default_mode': S.enum('SR1_ONLY', 'SR2_ONLY', 'HYBRID'),
                      'favourites': S.list_of(S.string(max_length=300), max_items=50, unique=True),
                      'recent': S.list_of(S.string(max_length=300), max_items=20, unique=True),
                      'directory_enabled': S.boolean()}),
    'content': S.obj({'auto_build': S.boolean(), 'generated_path': S.string(max_length=1024),
                      'auto_reverify_on_update': S.boolean(), 'user_largos_root': _PATH}),
    'species': S.obj({'foreign_spawn': S.obj({'mode': S.enum('NATIVE_ONLY', 'ALL_IMPORTED'),
                                              'overrides': S.map_of(_SPECIES_ID, S.boolean())})}),
    'bridge': S.obj({'sr1_bridge_access_mode': S.enum('ALWAYS', 'ENDGAME'),
                     'sr1_gold_plort_gate_cost': S.integer(minimum=1, maximum=999), 'dynamic_statue_requirements': S.boolean(), 'sr1_portal_requires_statues': S.boolean(),
                     'sr2_portal_enabled': S.boolean(), 'sr2_portal_gate_enabled': S.boolean(), 'sr2_portal_cost': S.integer(minimum=0, maximum=10 ** 9),
                     'currency_mode': S.enum('SEPARATE', 'SYNC_1_TO_1', 'CONVERSION_RATE'), 'cross_realm_chat': S.boolean(),
                     'transition_mode': S.enum('AUTO', 'WARM', 'PREWARM', 'COLD'), 'travel_save_mode': S.enum('LINKED', 'CONTINUE'), 'travel_link_mode': S.enum('AUTO', 'ASK'),
                     'background_policy': S.enum('FREEZE', 'THROTTLE'), 'travel_carry': S.enum('ALL', 'SLIMES_PLORTS', 'NONE')}),
    'updates': S.obj({'channel': S.enum('stable', 'beta', 'dev'), 'check_on_start': S.boolean()}),
    'diagnostics': S.obj({'level': S.enum('NORMAL', 'VERBOSE'), 'share_anonymous': S.boolean()}),
    'ui': S.obj({'language': S.enum('en', 'fr'), 'reduce_motion': S.boolean(), 'theme': S.enum('auto', 'light', 'dark')}),
    'advanced': S.obj({'show_developer_tools': S.boolean(), 'synthetic_demo': S.boolean()}),
})
SECTIONS = tuple(k for k in DEFAULTS if k not in ('schema', 'schema_version'))


def _copy(value):
    return json.loads(json.dumps(value))


def _migrate_v1(doc):
    """v1 stored ``warm_mode: bool`` and ``overlay: bool`` at top level."""
    doc = dict(doc)
    transitions = _copy(DEFAULTS['transitions'])
    if isinstance(doc.get('warm_mode'), bool):
        transitions['mode'] = 'WARM' if doc.pop('warm_mode') else 'COLD'
    if isinstance(doc.get('overlay'), bool):
        transitions['overlay'] = doc.pop('overlay')
    doc['transitions'] = transitions
    doc['schema_version'] = 2
    return doc


def _migrate_v2(doc):
    """v2 → v3 is additive: the ``species`` section materializes from defaults."""
    doc = dict(doc)
    doc['schema_version'] = 3
    return doc


def _migrate_v3(doc):
    """v3 -> v4 is additive: the ``bridge`` section (sanctuary gate, SR2 portal, currency, chat, transition mode) materializes."""
    doc = dict(doc)
    doc['schema_version'] = 4
    return doc


MIGRATIONS = {1: _migrate_v1, 2: _migrate_v2, 3: _migrate_v3}


def normalize(doc):
    """(settings, diagnostics): always a valid settings document."""
    diags = []
    if not isinstance(doc, dict):
        return _copy(DEFAULTS), [D.make('SETTINGS_UNREADABLE', 'warning', 'launcher', 'settings reset to defaults')]
    version = doc.get('schema_version')
    while type(version) is int and version in MIGRATIONS:
        doc = MIGRATIONS[version](doc)
        diags.append(D.make('SETTINGS_MIGRATED', 'info', 'launcher', f'settings migrated from v{version}'))
        version = doc.get('schema_version')
    if version != VERSION:
        diags.append(D.make('SETTINGS_VERSION_UNKNOWN', 'warning', 'launcher', f'unknown settings version {version!r}'))
    out = _copy(DEFAULTS)
    for key in doc:
        if key not in DEFAULTS:
            diags.append(D.make('SETTINGS_UNKNOWN_KEY', 'info', 'launcher', 'unknown setting dropped', subject=str(key)[:64]))
    for section in SECTIONS:
        candidate = _copy(out)
        if section in doc:
            if isinstance(DEFAULTS[section], dict) and isinstance(doc[section], dict):
                merged = _copy(DEFAULTS[section])
                merged.update({k: v for k, v in doc[section].items() if k in DEFAULTS[section]})
                candidate[section] = merged
            else:
                candidate[section] = doc[section]
            problems = S.check(SPEC, candidate)
            if problems:
                diags.append(D.make('SETTINGS_INVALID_VALUE', 'warning', 'launcher', 'invalid setting reset',
                                    subject=section, details={'problems': problems[:5]}))
            else:
                out = candidate
    return out, D.sort(diags)
