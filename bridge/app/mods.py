"""Mod manager service: static compatibility rows for the MODS page (no mod is ever executed).

Each entry is a parsed ``bridge-mod-manifest`` (or the parse diagnostics when it was rejected) plus
optional LOCAL static evidence. Rows carry the same-realm and cross-realm verdicts from
``bridge.modcompat.analyzer`` and the page tabs they belong to. Enable/disable/install/remove go
through deploy plans built from ``to_deploy_unit``; nothing here touches a game folder.
"""

from bridge.deploy.planner import plan_deploy
from bridge.modcompat import analyzer as A

TONES = {'CONFIG_ONLY': 'good', 'CONTENT_ONLY': 'good', 'KNOWN_API_GAMEPLAY': 'info', 'HARMONY_PATCH': 'warn',
         'NATIVE_DEEP_PATCH': 'bad', 'UNKNOWN': 'muted', 'INVALID': 'bad'}
PORTABILITY_TONES = {'PORTABLE': 'good', 'ADAPTER_REQUIRED': 'warn', 'MANUAL_PORT': 'warn', 'NOT_PORTABLE': 'bad',
                     'UNKNOWN': 'muted'}
SUMMARIES = {'CONFIG_ONLY': 'Settings only — safe to carry over.', 'CONTENT_ONLY': 'Adds content; Bridge can convert it.',
             'KNOWN_API_GAMEPLAY': 'Uses known mod-loader APIs; an adapter can port it.',
             'HARMONY_PATCH': 'Patches game code; needs a manual port and review.',
             'NATIVE_DEEP_PATCH': 'Hooks native code; kept quarantined.',
             'UNKNOWN': 'Not enough evidence yet — analysed statically, never run.'}


def rows(entries):
    """``entries``: [{'manifest': m | None, 'diagnostics': [...], 'evidence': e | None, 'label': str}]."""
    out = []
    for entry in entries:
        manifest = entry['manifest']
        if manifest is None:
            out.append({'id': entry.get('label', 'unparsed'), 'name': entry.get('label', 'Unreadable manifest'),
                        'version': '', 'realm': 'BRIDGE', 'classification': 'INVALID', 'tone': 'bad',
                        'portability': 'UNKNOWN', 'portability_tone': 'muted', 'tabs': ['UNKNOWN'],
                        'summary': '; '.join(d['message'] for d in entry['diagnostics'][:2]) or 'Rejected manifest',
                        'report': None, 'cross_report': None})
            continue
        realm = manifest['realm']
        other = 'SR2' if realm == 'SR1' else 'SR1'
        same = A.analyze(manifest, entry.get('evidence'))
        cross = A.analyze(manifest, entry.get('evidence'), target_realm=other)
        tabs = [realm]
        if same['classification'] == 'CONTENT_ONLY':
            tabs.append('CONTENT')
        if cross['portability'] in ('PORTABLE', 'ADAPTER_REQUIRED'):
            tabs.append('BRIDGE')
        if cross['portability'] in ('ADAPTER_REQUIRED', 'MANUAL_PORT'):
            tabs.append('ADAPTER')
        if same['classification'] == 'UNKNOWN' or cross['portability'] in ('NOT_PORTABLE', 'UNKNOWN'):
            tabs.append('UNKNOWN')
        out.append({'id': manifest['id'], 'name': manifest['name'], 'version': manifest['version']['text'],
                    'realm': realm, 'classification': same['classification'],
                    'tone': TONES.get(same['classification'], 'muted'), 'portability': cross['portability'],
                    'portability_tone': PORTABILITY_TONES.get(cross['portability'], 'muted'), 'tabs': tabs,
                    'summary': f"{SUMMARIES.get(same['classification'], '')} In {other}: "
                               f"{cross['portability'].replace('_', ' ').lower()} "
                               f"(confidence {cross['confidence']:.2f}).",
                    'report': same, 'cross_report': cross})
    return sorted(out, key=lambda r: (r['realm'], r['name'].casefold()))


def enable_plan(journal, manifest, report, *, state='ENABLED'):
    """Deploy plan for one analysed mod (checked; an unready status cannot be ENABLED), or a blocked
    pseudo-plan carrying the unit diagnostics when no unit can be built."""
    unit, diagnostics = A.to_deploy_unit(manifest, report)
    if unit is None:
        return {'schema': 'bridge-deploy-plan', 'executable': False, 'operations': [], 'blockers': diagnostics,
                'diagnostics': []}
    return plan_deploy(journal.inventory(unit['realm']), [{'key': unit['key'], 'state': state, 'unit': unit}],
                       history=journal.history())
