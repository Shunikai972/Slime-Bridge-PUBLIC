"""Unified, serializable diagnostics for every Bridge subsystem (launcher/UI ready).

A diagnostic is a plain dict with stable keys. ``code`` is a stable machine identifier; ``message``
is for humans; ``details`` holds machine data. Phase 1 diagnostics (code/severity/subject/detail)
are adapted with ``from_phase1`` instead of being rewritten.
"""

from bridge.content.canonical import Evidence, Severity, canonical_json

SUBSYSTEMS = ('core', 'content', 'conversion', 'package', 'build', 'provider', 'verification', 'counterpart',
              'deploy', 'transition', 'multiplayer', 'modcompat', 'launcher', 'largo', 'meshgen')
FIELDS = ('code', 'severity', 'subsystem', 'message', 'subject', 'content_id', 'realm', 'provider',
          'profile_id', 'save_id', 'details', 'action', 'evidence')
_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def make(code, severity, subsystem, message, *, subject=None, content_id=None, realm=None, provider=None,
         profile_id=None, save_id=None, details=None, action=None, evidence=None):
    if not isinstance(code, str) or not code or not code.replace('_', '').isalnum() or code.upper() != code:
        raise ValueError(f'Diagnostic code must be UPPER_SNAKE: {code!r}')
    if subsystem not in SUBSYSTEMS:
        raise ValueError(f'Unknown subsystem {subsystem!r}')
    item = {'code': code, 'severity': str(Severity(severity)), 'subsystem': subsystem, 'message': str(message),
            'subject': subject, 'content_id': content_id, 'realm': realm, 'provider': provider,
            'profile_id': profile_id, 'save_id': save_id, 'details': details if details is not None else {},
            'action': action, 'evidence': str(Evidence(evidence)) if evidence is not None else None}
    canonical_json(item)  # details must be serializable and finite
    return item


def from_phase1(item, subsystem, **context):
    """Adapt a Phase 1 ``diag()`` dict without losing its subject/detail."""
    return make(item['code'], item['severity'], subsystem, item.get('detail') or item['code'],
                subject=item.get('subject'), details={'detail': item.get('detail')}, **context)


def validate(item):
    """Problems with a diagnostic received from outside (UI, network, provider). Never raises."""
    if not isinstance(item, dict) or set(item) != set(FIELDS):
        return ['diagnostic must have exactly the unified fields']
    try:
        make(**{k: item[k] for k in FIELDS})
    except (ValueError, TypeError) as exc:
        return [str(exc)]
    return []


def sort(items):
    unique = {canonical_json(item): item for item in items}
    return [unique[k] for k in sorted(unique, key=lambda k: (_RANK[Severity(unique[k]['severity'])],
                                                              unique[k]['subsystem'], unique[k]['code'], k))]


def has_errors(items):
    return any(item['severity'] == Severity.ERROR for item in items)


def summary(items):
    counts = {}
    for item in items:
        counts[item['severity']] = counts.get(item['severity'], 0) + 1
    return dict(sorted(counts.items()))
