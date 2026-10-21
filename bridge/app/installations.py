"""Installation domain: what the launcher knows about each SR1/SR2 install, loaders and plugins.

An ``InstallationProvider`` reports records; discovery on disk is a local adapter (the existing
``apps/launcher/installations.py`` scanner). Records
are untrusted and validated here. Original installs are read-only: the launcher only plays and
deploys into an isolated LAB copy, so every action offered for a SOURCE install is read-only.
"""

from bridge.core import diagnostics as D
from bridge.core import schema as S

LOADERS = {'SR1': 'SRML', 'SR2': 'MELONLOADER'}
RUNTIMES = {'SR1': 'mono', 'SR2': 'il2cpp'}
STATES = ('INSTALLED', 'MISSING', 'OUTDATED', 'UNKNOWN')
READINESS = ('READY', 'NEEDS_LOADER', 'NEEDS_PLUGIN', 'NEEDS_LAB_COPY', 'NOT_FOUND', 'INVALID')
RECORD = S.obj({
    'realm': S.enum('SR1', 'SR2'), 'root': S.string(max_length=1024), 'store': S.enum('steam', 'epic', 'manual', 'lab'),
    'role': S.enum('SOURCE', 'LAB'), 'runtime': S.enum('mono', 'il2cpp'),
    'fingerprint': S.optional(S.sha256()), 'build': S.optional(S.string(max_length=128)),
    'loader': S.obj({'kind': S.enum('SRML', 'MELONLOADER'), 'state': S.enum(*STATES),
                     'version': S.optional(S.string(max_length=64))}),
    'bridge_plugin': S.obj({'state': S.enum(*STATES), 'version': S.optional(S.string(max_length=64))}),
})


class InstallationProvider:
    def records(self):  # pragma: no cover - interface
        raise NotImplementedError


class StaticInstallations(InstallationProvider):
    """Synthetic/demo provider (also used by tests)."""

    def __init__(self, records):
        self._records = list(records)

    def records(self):
        return list(self._records)


def record(realm, root, *, role='LAB', store='lab', fingerprint=None, build=None, loader='INSTALLED',
           loader_version=None, plugin='INSTALLED', plugin_version=None):
    return {'realm': realm, 'root': root, 'store': store, 'role': role, 'runtime': RUNTIMES[realm],
            'fingerprint': fingerprint, 'build': build,
            'loader': {'kind': LOADERS[realm], 'state': loader, 'version': loader_version},
            'bridge_plugin': {'state': plugin, 'version': plugin_version}}


def assess(rec):
    """(readiness, diagnostics, actions) for one record. Actions are suggestions, never executed here."""
    problems = S.check(RECORD, rec)
    if problems:
        return 'INVALID', [D.make('INSTALLATION_RECORD_INVALID', 'error', 'launcher', 'installation record rejected',
                                  details={'problems': problems[:5]})], []
    realm = rec['realm']
    diags, actions = [], []
    if rec['runtime'] != RUNTIMES[realm] or rec['loader']['kind'] != LOADERS[realm]:
        return 'INVALID', [D.make('INSTALLATION_REALM_MISMATCH', 'error', 'launcher',
                                  'runtime/loader do not match the realm', realm=realm)], []
    if rec['role'] == 'SOURCE':
        diags.append(D.make('SOURCE_INSTALL_READ_ONLY', 'info', 'launcher',
                            'original installation is never modified; play happens in an isolated copy',
                            realm=realm, action='CREATE_LAB_COPY'))
        actions.append({'id': 'CREATE_LAB_COPY', 'destructive': False, 'local_only': True})
        return 'NEEDS_LAB_COPY', diags, actions
    if rec['loader']['state'] != 'INSTALLED':
        diags.append(D.make('LOADER_NOT_READY', 'warning', 'launcher', f"{rec['loader']['kind']} is {rec['loader']['state']}",
                            realm=realm, action='INSTALL_LOADER'))
        actions.append({'id': 'INSTALL_LOADER', 'destructive': False, 'local_only': True})
        return 'NEEDS_LOADER', diags, actions
    if rec['bridge_plugin']['state'] != 'INSTALLED':
        diags.append(D.make('BRIDGE_PLUGIN_NOT_READY', 'warning', 'launcher',
                            f"Bridge plugin is {rec['bridge_plugin']['state']}", realm=realm, action='DEPLOY_PLUGIN'))
        actions.append({'id': 'DEPLOY_PLUGIN', 'destructive': False, 'local_only': True})
        return 'NEEDS_PLUGIN', diags, actions
    if rec['fingerprint'] is None:
        diags.append(D.make('INSTALLATION_NOT_FINGERPRINTED', 'info', 'launcher', 'build fingerprint unknown',
                            realm=realm, evidence='NEEDS_LOCAL_RUNTIME_PROOF'))
    return 'READY', diags, actions


def overview(provider):
    """Per realm: the best record (LAB over SOURCE), its readiness and every record seen."""
    out = {}
    for realm in ('SR1', 'SR2'):
        recs = [r for r in provider.records() if isinstance(r, dict) and r.get('realm') == realm]
        assessed = [(r, *assess(r)) for r in recs]
        rank = {name: i for i, name in enumerate(READINESS)}
        assessed.sort(key=lambda item: (rank[item[1]], item[0].get('role') != 'LAB', str(item[0].get('root'))))
        if not assessed:
            out[realm] = {'readiness': 'NOT_FOUND', 'record': None, 'diagnostics': [
                D.make('INSTALLATION_NOT_FOUND', 'info', 'launcher', 'no installation found', realm=realm,
                       action='CHOOSE_FOLDER')], 'actions': [{'id': 'CHOOSE_FOLDER', 'destructive': False,
                                                              'local_only': False}], 'all': []}
            continue
        best = assessed[0]
        out[realm] = {'readiness': best[1], 'record': best[0], 'diagnostics': best[2], 'actions': best[3],
                      'all': [{'root': r.get('root'), 'role': r.get('role'), 'readiness': state}
                              for r, state, _, _ in assessed]}
    return out
