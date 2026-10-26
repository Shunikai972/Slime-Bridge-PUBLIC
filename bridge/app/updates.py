"""Update architecture: manifest schema, channel/version planning, verified staging, rollback info.

The updater never executes downloaded code and never trusts metadata it cannot verify:
- ``bridge-update-manifest`` v1 is untrusted input; it must carry a signature that a
  ``SignatureVerifier`` provider accepts. Cloud ships ``RejectAll`` only (no keys, no crypto
  shortcuts); the real verifier and key distribution are local release decisions.
- artifacts are fetched through a ``DownloadProvider`` and accepted only when size and sha256
  match; they are written to a confined staging area, never into an install, game or plugin folder.
- applying staged files (swap, migrations, restarts) is a separate, journaled local step; the plan
  lists every step plus the rollback (previous version kept until the new one is confirmed).
"""

import hashlib
import re

from bridge.cache.fs import atomic_write, confined_root
from bridge.content.canonical import canonical_json
from bridge.core import diagnostics as D
from bridge.core import schema as S

COMPONENTS = ('launcher', 'bridgehost', 'sr1_plugin', 'sr2_plugin', 'content_schema', 'providers')
CHANNELS = ('stable', 'beta', 'dev')
RESTART = ('NONE', 'LAUNCHER', 'GAMES', 'ALL')
_VERSION = r'(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})'
_ARTIFACT_NAME = r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'
MANIFEST = S.obj({
    'schema': S.enum('bridge-update-manifest'), 'schema_version': S.enum(1), 'channel': S.enum(*CHANNELS),
    'sequence': S.integer(minimum=1),
    'components': S.map_of(S.enum(*COMPONENTS), S.obj({
        'version': S.string(_VERSION), 'min_from': S.optional(S.string(_VERSION)),
        'restart': S.enum(*RESTART),
        'requires': S.map_of(S.enum(*COMPONENTS), S.string(_VERSION)),
        'migrations': S.list_of(S.string(r'[a-z0-9_.-]{1,64}'), max_items=32),
        'artifacts': S.list_of(S.obj({'name': S.string(_ARTIFACT_NAME), 'sha256': S.sha256(),
                                      'size': S.integer(minimum=1, maximum=2 * 1024 ** 3)}), max_items=64)})),
    'signature': S.obj({'scheme': S.string(r'[a-z0-9-]{1,32}'), 'key_id': S.string(r'[A-Za-z0-9._-]{1,64}'),
                        'value': S.string(max_length=4096)}),
})


def parse_version(text):
    match = re.fullmatch(_VERSION, text or '')
    if not match:
        raise ValueError(f'bad version {text!r}')
    return tuple(int(x) for x in match.groups())


class SignatureVerifier:
    def verify(self, signed_bytes, signature):  # pragma: no cover - interface
        raise NotImplementedError


class RejectAll(SignatureVerifier):
    def verify(self, signed_bytes, signature):
        return False


class DownloadProvider:
    def fetch(self, component, artifact):  # pragma: no cover - interface
        raise NotImplementedError


def signed_payload(manifest):
    return canonical_json({k: v for k, v in manifest.items() if k != 'signature'}).encode()


def plan_updates(installed, manifest, *, verifier, channel='stable', last_sequence=0):
    """Plan from ``installed`` ({component: version}) to the manifest; blockers stop the whole plan."""
    problems = S.check(MANIFEST, manifest)
    if problems:
        return {'executable': False, 'steps': [], 'blockers': [D.make('UPDATE_MANIFEST_INVALID', 'error', 'launcher',
                                                                      'update metadata rejected',
                                                                      details={'problems': problems[:5]})]}
    blockers, steps = [], []

    def block(code, message, subject=None):
        blockers.append(D.make(code, 'error', 'launcher', message, subject=subject))
    if not verifier.verify(signed_payload(manifest), manifest['signature']):
        block('UPDATE_SIGNATURE_UNVERIFIED', 'update metadata is not signed by a trusted key')
    if manifest['channel'] != channel:
        block('UPDATE_WRONG_CHANNEL', f"manifest is for {manifest['channel']}, settings say {channel}")
    if manifest['sequence'] <= last_sequence:
        block('UPDATE_REPLAYED_MANIFEST', 'manifest is not newer than the last one applied (rollback attack?)')
    target = {c: parse_version(v) for c, v in installed.items()}
    for name, comp in sorted(manifest['components'].items()):
        new = parse_version(comp['version'])
        if name in target and new < target[name]:
            block('UPDATE_DOWNGRADE', f'{name} {comp["version"]} is older than installed', name)
            continue
        if name in target and new == target[name]:
            continue
        if comp['min_from'] and name in target and target[name] < parse_version(comp['min_from']):
            block('UPDATE_NEEDS_INTERMEDIATE', f'{name} must first update to {comp["min_from"]}', name)
        names = [a['name'] for a in comp['artifacts']]
        if len({n.casefold() for n in names}) != len(names):
            block('UPDATE_ARTIFACT_COLLISION', 'artifact names collide', name)
        target[name] = new
        steps.append({'component': name, 'from': installed.get(name), 'to': comp['version'],
                      'artifacts': comp['artifacts'], 'migrations': comp['migrations'], 'restart': comp['restart'],
                      'actions': ['DOWNLOAD', 'VERIFY_HASH', 'STAGE'] + (['MIGRATE'] if comp['migrations'] else []) +
                                 ['SWAP_ON_RESTART', 'CONFIRM_OR_ROLLBACK'],
                      'rollback': {'keep_previous': installed.get(name)}})
    for name, comp in manifest['components'].items():
        for dep, minimum in comp['requires'].items():
            have = target.get(dep)
            if have is None or have < parse_version(minimum):
                block('UPDATE_REQUIREMENT_UNMET', f'{name} requires {dep} >= {minimum}', name)
    restart = max((s['restart'] for s in steps), key=RESTART.index, default='NONE')
    return {'executable': not blockers, 'steps': steps, 'blockers': D.sort(blockers), 'restart': restart,
            'sequence': manifest['sequence']}


def stage(plan, downloader, root):
    """Fetch + verify every artifact of an executable plan into ``root/<component>/<version>/``."""
    if not plan['executable']:
        raise ValueError('refusing to stage a plan with blockers')
    root = confined_root(root)
    staged, failures = [], []
    for step in plan['steps']:
        for artifact in step['artifacts']:
            try:
                data = downloader.fetch(step['component'], artifact)
            except Exception as exc:  # provider failure: isolate per artifact
                failures.append({'component': step['component'], 'artifact': artifact['name'],
                                 'reason': type(exc).__name__})
                continue
            if not isinstance(data, (bytes, bytearray)) or len(data) != artifact['size'] or \
                    hashlib.sha256(data).hexdigest() != artifact['sha256']:
                failures.append({'component': step['component'], 'artifact': artifact['name'], 'reason': 'HASH_OR_SIZE'})
                continue
            path = root / step['component'] / step['to'] / artifact['name']
            atomic_write(root, path, bytes(data))
            staged.append(path.relative_to(root).as_posix())
    return {'complete': not failures, 'staged': staged, 'failures': failures}
