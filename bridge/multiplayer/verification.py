"""Proof plan for multiplayer compatibility of a server configuration (runtime evidence only)."""

from bridge.verification.plans import make_plan, requirement
from .protocol import mode_realms

RUNTIME = ('RUNTIME_OBSERVATION',)


def multiplayer_plan(config, *, builds=None, realm_sync=False):
    """``config``: ``ServerConfig.to_dict()``; ``realm_sync`` when a native realm-network adapter is used."""
    builds = builds or {}
    realms = mode_realms(config['mode'])
    reqs = [requirement('multiplayer.protocol_negotiated', 'MULTIPLAYER', 'clients negotiate bridge-net with the server',
                        accepted=('RUNTIME_OBSERVATION', 'STATIC_ANALYSIS')),
            requirement('multiplayer.reconnect_resumes', 'MULTIPLAYER', 'a dropped client resumes without duplicates',
                        accepted=('RUNTIME_OBSERVATION', 'STATIC_ANALYSIS'))]
    for realm in realms:
        reqs.append(requirement(f'multiplayer.presence_{realm.lower()}', 'MULTIPLAYER',
                                f'a {realm} player appears with the right realm', accepted=RUNTIME, realm=realm,
                                build=builds.get(realm)))
        if realm in config.get('required_content', {}):
            reqs.append(requirement(f'multiplayer.content_gate_{realm.lower()}', 'MULTIPLAYER',
                                    f'a client without the {realm} manifest cannot enter {realm}', accepted=RUNTIME,
                                    realm=realm, build=builds.get(realm)))
        reqs.append(requirement(f'multiplayer.realm_sync_{realm.lower()}', 'MULTIPLAYER',
                                f'native {realm} realm sync adapter applies remote events', accepted=RUNTIME,
                                realm=realm, build=builds.get(realm), blocking=realm_sync,
                                not_applicable_allowed=not realm_sync))
    if len(realms) == 2:
        reqs.append(requirement('multiplayer.cross_realm_chat', 'MULTIPLAYER',
                                'chat reaches players in both realms', accepted=RUNTIME))
    return make_plan('multiplayer', config['name'], reqs)
