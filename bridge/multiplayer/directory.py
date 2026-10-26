"""Server browser backend: listings, directory providers, favourites/recents, join compatibility.

A ``bridge-server-listing`` is untrusted input from a directory provider (public list, LAN probe,
favourites file). ``join_assessment`` compares a listing with what this client can actually run
(protocol versions, installed realms, content manifest per realm) and returns one of:
JOINABLE, JOINABLE_RESTRICTED (some hosted realm is unavailable to this client but the server can
still be joined), INCOMPATIBLE_PROTOCOL, NO_PLAYABLE_REALM, FULL, UNREACHABLE.
"""

import ipaddress
import re

from bridge.core import schema as S
from .protocol import CAPABILITIES, MODES, REALMS, VERSIONS, mode_realms

_HOST = re.compile(r'^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}'
                   r'[A-Za-z0-9])?)*$')
LISTING = S.obj({
    'schema': S.enum('bridge-server-listing'), 'schema_version': S.enum(1),
    'server_id': S.string(r'[a-z0-9][a-z0-9._-]{0,63}'), 'name': S.string(max_length=64),
    'address': S.string(max_length=300), 'mode': S.enum(*MODES),
    'protocol_versions': S.list_of(S.integer(minimum=1, maximum=1000), max_items=8, unique=True),
    'players': S.integer(minimum=0, maximum=100000), 'max_players': S.integer(minimum=1, maximum=100000),
    'realm_populations': S.map_of(S.enum(*REALMS), S.integer(minimum=0, maximum=100000)),
    'capabilities': S.list_of(S.enum(*CAPABILITIES), max_items=16, unique=True),
    'content': S.map_of(S.enum(*REALMS), S.obj({'manifest_hash': S.sha256(),
                                                'required_packages': S.list_of(S.string(r'[A-Za-z0-9:._-]{1,160}'),
                                                                               max_items=1024)})),
    'description': S.string(max_length=300, allow_empty=True),
    'ping_ms': S.optional(S.integer(minimum=0, maximum=60000)),
    'source': S.enum('DIRECTORY', 'LAN', 'FAVOURITE', 'DIRECT', 'LOCAL'),
})
ASSESSMENTS = ('JOINABLE', 'JOINABLE_RESTRICTED', 'INCOMPATIBLE_PROTOCOL', 'NO_PLAYABLE_REALM', 'FULL', 'UNREACHABLE')


def parse_address(text, default_port=7777):
    """(host, port) for 'host', 'host:port', 'IPv4:port' or '[IPv6]:port'; ValueError otherwise."""
    if not isinstance(text, str) or not text or len(text) > 300 or any(c in text for c in ' /\\@?#\t\n'):
        raise ValueError('address must be host[:port] without scheme, path or credentials')
    if text.startswith('['):
        host, _, rest = text[1:].partition(']')
        ipaddress.IPv6Address(host)
        port = rest[1:] if rest.startswith(':') else ''
        if rest and not rest.startswith(':'):
            raise ValueError('malformed IPv6 address')
    elif text.count(':') == 1:
        host, port = text.split(':')
    elif ':' in text:
        raise ValueError('IPv6 addresses need brackets')
    else:
        host, port = text, ''
    if not text.startswith('['):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            if not _HOST.match(host):
                raise ValueError('invalid host name') from None
    port = int(port) if port else default_port
    if not 1 <= port <= 65535:
        raise ValueError('port out of range')
    return host, port


def validate_listing(listing):
    problems = S.check(LISTING, listing)
    if not problems:
        try:
            parse_address(listing['address'])
        except ValueError as exc:
            problems.append(f'$.address: {exc}')
        hosted = set(mode_realms(listing['mode']))
        if set(listing['realm_populations']) - hosted or set(listing['content']) - hosted:
            problems.append('$.realm_populations: realms not hosted in this mode')
        if sum(listing['realm_populations'].values()) > listing['players']:
            problems.append('$.players: fewer than the realm populations')
    return problems


def join_assessment(listing, *, client_versions=VERSIONS, installed_realms=REALMS, local_content=None):
    """How (and whether) this client can join; per-realm reasons for the UI."""
    problems = validate_listing(listing)
    if problems:
        return {'assessment': 'UNREACHABLE', 'realms': {}, 'reasons': problems[:5]}
    local_content = local_content or {}
    if not set(client_versions) & set(listing['protocol_versions']):
        return {'assessment': 'INCOMPATIBLE_PROTOCOL', 'realms': {}, 'reasons': ['NO_COMMON_PROTOCOL_VERSION']}
    realms = {}
    for realm in mode_realms(listing['mode']):
        content = listing['content'].get(realm)
        if realm not in installed_realms:
            realms[realm] = 'NOT_INSTALLED'
        elif content is not None and local_content.get(realm) != content['manifest_hash']:
            realms[realm] = 'CONTENT_MISMATCH'
        else:
            realms[realm] = 'PLAYABLE'
    playable = [r for r, v in realms.items() if v == 'PLAYABLE']
    if listing['players'] >= listing['max_players']:
        assessment = 'FULL'
    elif not playable:
        assessment = 'NO_PLAYABLE_REALM'
    elif len(playable) < len(realms):
        assessment = 'JOINABLE_RESTRICTED'
    else:
        assessment = 'JOINABLE'
    missing = sorted({p for realm, v in realms.items() if v == 'CONTENT_MISMATCH'
                      for p in listing['content'][realm]['required_packages']})
    return {'assessment': assessment, 'realms': realms, 'reasons': [], 'missing_packages': missing}


class InMemoryDirectory:
    """Directory provider used by Cloud tests and demo mode."""

    def __init__(self, listings=()):
        self._listings = list(listings)

    def fetch(self):
        valid, rejected = [], []
        for listing in self._listings:
            (rejected if validate_listing(listing) else valid).append(listing)
        return {'listings': sorted(valid, key=lambda l: (l['name'].casefold(), l['server_id'])),
                'rejected': len(rejected)}


def listing_from_server(server, *, server_id, address, description='', source='LOCAL', ping_ms=None):
    """Listing for a locally hosted ``BridgeServer`` (used by the launcher's local-server panel)."""
    snap = server.snapshot()
    content = {realm: {'manifest_hash': h, 'required_packages': server.config.required_packages.get(realm, [])}
               for realm, h in server.config.required_content.items()}
    return {'schema': 'bridge-server-listing', 'schema_version': 1, 'server_id': server_id, 'name': snap['name'],
            'address': address, 'mode': snap['mode'], 'protocol_versions': list(VERSIONS), 'players': snap['players'],
            'max_players': snap['max_players'], 'realm_populations': snap['realm_populations'],
            'capabilities': list(server.config.capabilities), 'content': content, 'description': description,
            'ping_ms': ping_ms, 'source': source}


def remember(entries, listing_or_address, *, limit=20):
    """Most-recent-first list of addresses (favourites/recents), de-duplicated and bounded."""
    address = listing_or_address['address'] if isinstance(listing_or_address, dict) else listing_or_address
    parse_address(address)
    return [address] + [a for a in entries if a != address][:limit - 1]
