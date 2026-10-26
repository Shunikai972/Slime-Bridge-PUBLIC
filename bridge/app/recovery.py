"""Diagnostics & recovery center: unified diagnostics -> friendly issues with safe actions.

Every subsystem already emits unified diagnostics (``bridge.core.diagnostics``). The recovery center
groups them into user-facing categories, writes a short friendly title/summary, keeps the raw
diagnostic as "advanced details" and offers only *safe* actions (read-only probes, reconcile,
re-verify, roll back or disable a package, export a diagnostic bundle). Unsafe deletion is never
offered. The exported bundle is redacted (no absolute paths, no credentials/tokens).
"""

import base64
import json
import re

from bridge.cache.fs import atomic_write, confined_root
from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D

CATEGORIES = ('PROVIDERS', 'INSTALLATIONS', 'LOADERS', 'SAVES', 'COUNTERPART', 'PACKAGES', 'MODS', 'SERVERS',
              'TRANSITIONS', 'VERIFICATION', 'UPDATES', 'OTHER')
SAFE_ACTIONS = {
    'RETRY_PROBE': 'Check again (read-only)', 'RECONCILE': 'Check whether the partner save exists',
    'REVERIFY': 'Re-run verification', 'ROLLBACK_PACKAGE': 'Roll back to the previous version',
    'DISABLE_PACKAGE': 'Disable this content', 'EXPORT_BUNDLE': 'Export a diagnostic bundle',
    'OPEN_SETTINGS': 'Open settings', 'CREATE_LAB_COPY': 'Create an isolated game copy (local tool)',
    'INSTALL_LOADER': 'Install the mod loader (local tool)', 'DEPLOY_PLUGIN': 'Install the Bridge plugin (local tool)',
    'CHOOSE_FOLDER': 'Choose the game folder', 'VIEW_MOD_REPORT': 'Open the compatibility report',
    'REBUILD_PLAN': 'Plan a rebuild',
}
_SUBSYSTEM_CATEGORY = {'provider': 'PROVIDERS', 'counterpart': 'COUNTERPART', 'package': 'PACKAGES',
                       'deploy': 'PACKAGES', 'build': 'PACKAGES', 'conversion': 'PACKAGES', 'content': 'PACKAGES',
                       'modcompat': 'MODS', 'multiplayer': 'SERVERS', 'transition': 'TRANSITIONS',
                       'verification': 'VERIFICATION', 'largo': 'PACKAGES'}
_CODE_RULES = (
    (r'^(LOADER_|BRIDGE_PLUGIN_)', 'LOADERS', 'The mod loader or Bridge plugin is not ready', ('RETRY_PROBE',)),
    (r'^(INSTALLATION_|SOURCE_INSTALL_)', 'INSTALLATIONS', 'A game installation needs attention', ('RETRY_PROBE',)),
    (r'^(COUNTERPART_RECOVERED_UNCERTAIN|CREATION_)', 'COUNTERPART', 'A partner save creation was interrupted',
     ('RECONCILE',)),
    (r'^COUNTERPART_LINK_', 'SAVES', 'Two saves could not be linked safely', ('RECONCILE', 'EXPORT_BUNDLE')),
    (r'^(WORLD_READY_|TRANSITION_)', 'TRANSITIONS', 'A switch between games did not complete', ('EXPORT_BUNDLE',)),
    (r'^(RECEIPT_|STATIC_CHECK_)', 'VERIFICATION', 'Verification could not confirm something', ('REVERIFY',)),
    (r'^(UNIT_|DEPENDENCY_|PROVIDES_|FILE_CONFLICT|TARGET_DRIFT|POSTCONDITION_|ARTIFACT_)', 'PACKAGES',
     'Installed content needs attention', ('ROLLBACK_PACKAGE', 'DISABLE_PACKAGE')),
    (r'^PACKAGE_(QUARANTINED|INCOMPLETE)', 'PACKAGES', 'Content was set aside for safety', ('REBUILD_PLAN', 'REVERIFY')),
    (r'^UPDATE_', 'UPDATES', 'An update could not be applied', ('RETRY_PROBE',)),
    (r'^SETTINGS_', 'OTHER', 'Settings were repaired', ('OPEN_SETTINGS',)),
)
# Keep remote URLs, not local file URIs; consume paths through spaces (privacy over prose).
_ABS_PATH = re.compile(r'(?P<url>(?!(?i:file:))[A-Za-z][A-Za-z0-9+.-]+://[^\s"\'<>]+)|'
                       r'(?<![\w./\\~])(?:(?i:file://)|[A-Za-z]:[\\/]|/|\\\\|~[\w.-]*[\\/])[^\n"\'<>]*')
_SECRET_KEY = re.compile(r'(token|credential|authorization|password|secret|auth|cookie|key)', re.I)
_BEARER = re.compile(r'\bBearer[ \t]+[A-Za-z0-9._~+/-]+=*', re.I)
_JWT = re.compile(r'(?<![\w-])([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*')


def classify(diag):
    for pattern, category, title, actions in _CODE_RULES:
        if re.search(pattern, diag['code']):
            return category, title, actions
    category = _SUBSYSTEM_CATEGORY.get(diag['subsystem'], 'OTHER')
    title = {'PROVIDERS': 'A provider reported a problem', 'MODS': 'A mod may not work with Bridge',
             'SERVERS': 'A server connection problem', 'PACKAGES': 'Content needs attention'}.get(
        category, 'Something needs attention')
    return category, title, ('VIEW_MOD_REPORT',) if category == 'MODS' else ('EXPORT_BUNDLE',)


def issues(diagnostics):
    """Friendly issues (errors and warnings first); info diagnostics are kept but marked minor."""
    out = []
    for diag in D.sort([d for d in diagnostics if not D.validate(d)]):
        category, title, actions = classify(diag)
        explicit = [diag['action']] if diag.get('action') in SAFE_ACTIONS else []
        action_ids = list(dict.fromkeys(explicit + list(actions) + ['EXPORT_BUNDLE']))
        out.append({'id': sha256_json(diag)[:16], 'category': category, 'severity': diag['severity'],
                    'minor': diag['severity'] == 'info', 'title': title, 'summary': diag['message'],
                    'subject': diag['subject'], 'realm': diag['realm'],
                    'actions': [{'id': a, 'label': SAFE_ACTIONS[a], 'destructive': False} for a in action_ids],
                    'details': diag})
    return out


def summary(items):
    counts = {c: {'error': 0, 'warning': 0, 'info': 0} for c in CATEGORIES}
    for issue in items:
        counts[issue['category']][issue['severity']] += 1
    worst = 'ok'
    if any(i['severity'] == 'error' for i in items):
        worst = 'error'
    elif any(i['severity'] == 'warning' for i in items):
        worst = 'warning'
    return {'status': worst, 'by_category': {c: v for c, v in counts.items() if any(v.values())}}


def _redact_jwt(match):
    """Recognize the decoded header, not its whitespace-dependent base64 prefix."""
    header = match.group(1)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(header + '=' * (-len(header) % 4)))
    except (ValueError, UnicodeError, RecursionError):
        return match.group(0)
    return '<redacted>' if isinstance(decoded, dict) and 'alg' in decoded else match.group(0)


def redact(value):
    """Copy with absolute paths and secret-looking fields removed."""
    if isinstance(value, dict):
        return {redact(k): ('<redacted>' if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _JWT.sub(_redact_jwt, _BEARER.sub('<redacted>', value))
        return _ABS_PATH.sub(lambda m: m.group('url') or '<path>', value)
    return value


def export_bundle(items, root, *, name, context=None, max_items=2000):
    """Write a redacted JSON bundle under a confined root; returns its path."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', name):
        raise ValueError('bundle name must be a simple lowercase file name')
    root = confined_root(root)
    doc = {'schema': 'bridge-diagnostic-bundle', 'schema_version': 1, 'summary': summary(items),
           'issues': redact(items[:max_items]), 'truncated': len(items) > max_items,
           'context': redact(context or {})}
    path = root / f'{name}.json'
    atomic_write(root, path, (json.dumps(doc, indent=2, sort_keys=True) + '\n').encode())
    return path
