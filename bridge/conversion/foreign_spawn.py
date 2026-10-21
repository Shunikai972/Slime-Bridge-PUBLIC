"""Foreign-world natural spawning policy (``bridge-foreign-spawn-policy`` v1).

Imported species must never spawn naturally in the foreign realm by default: the player
travels to the species' native game, captures it there and carries it through SlimeBridge.
This module owns that Bridge-side switch (mode plus per-species overrides), its fail-closed
normalization/validation and the per-species effective view.

The switch is configuration truth only. Wiring a species into a foreign realm's natural
spawn systems is native work that is not implemented yet, so every effective entry keeps
``active_natural_spawn_foreign == false`` behind ``BLOCKED_ON_LOCAL_QUERY`` (Q034) even when
the requested value is true. ``requested`` is the player's setting; ``active`` is what the
runtime actually honors today. Foreign-world presence of an imported species never implies
natural spawning.
"""
from __future__ import annotations

import re

from bridge.content.canonical import diag, sort_diagnostics

POLICY_SCHEMA = 'bridge-foreign-spawn-policy'
POLICY_SCHEMA_VERSION = 1
MODES = ('NATIVE_ONLY', 'ALL_IMPORTED')
DEFAULT_MODE = 'NATIVE_ONLY'
SPAWN_INTEGRATION_QUERY = 'Q034'
EFFECTIVE_SCHEMA = 'bridge-foreign-spawn-effective'
EFFECTIVE_SCHEMA_VERSION = 1
_SPECIES_ID = re.compile(r'^slime\.[a-z0-9]+(?:_[a-z0-9]+)*$')


def default_policy() -> dict:
    """NATIVE_ONLY with no overrides: imported species never spawn in the foreign world."""
    return {'schema': POLICY_SCHEMA, 'schema_version': POLICY_SCHEMA_VERSION,
            'mode': DEFAULT_MODE, 'overrides': {}}


def _imported_set(imported_ids) -> frozenset:
    if imported_ids is None:
        return frozenset()
    return frozenset(item for item in imported_ids if isinstance(item, str))


def validate_policy(policy, *, imported_ids) -> list:
    """Structural + semantic problems; empty means well-formed and fail-closed-eligible.

    Overrides may only name verified imported (exclusive, adapter-prepared) species: an
    unknown or native species in an override is refused instead of silently ignored.
    """
    if not isinstance(policy, dict):
        return ['$: object required']
    problems = []
    known = _imported_set(imported_ids)
    for key in sorted(set(policy) - {'schema', 'schema_version', 'mode', 'overrides'}):
        problems.append(f'$.{key}: unknown field')
    if policy.get('schema') != POLICY_SCHEMA:
        problems.append('$.schema: wrong schema')
    if policy.get('schema_version') != POLICY_SCHEMA_VERSION:
        problems.append('$.schema_version: wrong version')
    if policy.get('mode') not in MODES:
        problems.append('$.mode: NATIVE_ONLY or ALL_IMPORTED required')
    overrides = policy.get('overrides')
    if not isinstance(overrides, dict):
        problems.append('$.overrides: object required')
        return problems
    for species_id in sorted(overrides, key=lambda key: str(key)):
        if not isinstance(species_id, str) or _SPECIES_ID.fullmatch(species_id) is None:
            problems.append(f'$.overrides[{species_id!r}]: canonical slime id required')
        elif species_id not in known:
            problems.append(f'$.overrides.{species_id}: not a verified imported species')
        if species_id in overrides and overrides[species_id] is not True and overrides[species_id] is not False:
            problems.append(f'$.overrides.{species_id}: boolean required')
    return problems


def normalize_policy(doc, *, imported_ids):
    """(policy, diagnostics): always a valid policy; every repair fails toward NATIVE_ONLY.

    An absent document (``None``) is the normal "defaults apply" case and carries no
    diagnostic; a present-but-unreadable or unknown-version document does.
    """
    if doc is None:
        return default_policy(), []
    if not isinstance(doc, dict) or doc.get('schema') != POLICY_SCHEMA or \
            doc.get('schema_version') != POLICY_SCHEMA_VERSION:
        return default_policy(), sort_diagnostics([diag(
            'FOREIGN_SPAWN_UNREADABLE', 'warning', 'policy',
            'missing or unknown policy document; NATIVE_ONLY defaults in effect')])
    problems = []
    mode = doc.get('mode')
    if mode not in MODES:
        problems.append(diag('FOREIGN_SPAWN_MODE_INVALID', 'warning', 'policy',
                             f'invalid mode reset to NATIVE_ONLY: {str(mode)[:64]}'))
        mode = DEFAULT_MODE
    known = _imported_set(imported_ids)
    overrides_doc = doc.get('overrides')
    overrides = {}
    if isinstance(overrides_doc, dict):
        for key in sorted(overrides_doc, key=lambda item: str(item)):
            value = overrides_doc[key]
            if not isinstance(key, str) or _SPECIES_ID.fullmatch(key) is None or key not in known:
                problems.append(diag('FOREIGN_SPAWN_OVERRIDE_UNKNOWN', 'warning', str(key)[:64],
                                     'override dropped: not a verified imported species'))
                continue
            if value is not True and value is not False:
                problems.append(diag('FOREIGN_SPAWN_OVERRIDE_INVALID', 'warning', key,
                                     'override dropped: boolean required'))
                continue
            overrides[key] = value
    elif overrides_doc is not None:
        problems.append(diag('FOREIGN_SPAWN_OVERRIDES_INVALID', 'warning', 'policy',
                             'overrides reset: object required'))
    policy = {'schema': POLICY_SCHEMA, 'schema_version': POLICY_SCHEMA_VERSION,
              'mode': mode, 'overrides': overrides}
    return policy, sort_diagnostics(problems)


def effective_policy(policy, *, imported_ids) -> dict:
    """Per-species view: requested switches vs the runtime-active switch.

    ``active_natural_spawn_foreign`` stays false for every species until the foreign
    natural-spawn integration query is answered; ALL_IMPORTED only changes ``requested``
    for verified imported species.
    """
    mode = policy.get('mode') if isinstance(policy, dict) else None
    if mode not in MODES:
        raise ValueError('policy mode must be NATIVE_ONLY or ALL_IMPORTED')
    raw_overrides = policy.get('overrides') if isinstance(policy.get('overrides'), dict) else {}
    overrides = {key: value for key, value in raw_overrides.items()
                 if isinstance(key, str) and value in (True, False)}
    species = {}
    for species_id in sorted(_imported_set(imported_ids)):
        if species_id in overrides:
            requested, source = bool(overrides[species_id]), 'OVERRIDE'
        else:
            requested, source = (mode == 'ALL_IMPORTED'), 'MODE'
        species[species_id] = {'requested_natural_spawn_foreign': requested,
                               'requested_source': source,
                               'active_natural_spawn_foreign': False}
    return {'schema': EFFECTIVE_SCHEMA, 'schema_version': EFFECTIVE_SCHEMA_VERSION,
            'mode': mode,
            'integration': {'status': 'BLOCKED_ON_LOCAL_QUERY',
                            'local_query': SPAWN_INTEGRATION_QUERY,
                            'note': 'foreign natural-spawn wiring is not implemented; active '
                                    'stays false for every species until the query is answered'},
            'species': species}
