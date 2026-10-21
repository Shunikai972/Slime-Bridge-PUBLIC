"""Authentication is a provider interface; Cloud ships no production authentication.

``AuthProvider.authenticate(provider_name, credential) -> AuthResult | None``. A real deployment
plugs in a provider decided locally (platform account, signed invite, self-hosted accounts). The
two implementations here are deliberately not production-capable:

- ``DenyAll``: the default; nobody can join.
- ``DevTokenAuth``: fixed tokens for tests/demo. It refuses to construct unless explicitly told it
  runs in an isolated development context, never hashes passwords, and compares tokens in
  constant time only to avoid teaching a bad pattern.
"""

import hmac
import re
from dataclasses import dataclass

_PLAYER = re.compile(r'[A-Za-z0-9_.:-]{1,64}')


@dataclass(frozen=True)
class AuthResult:
    player_id: str
    provider: str


class AuthProvider:
    name = 'abstract'

    def authenticate(self, provider_name, credential):  # pragma: no cover - interface
        raise NotImplementedError


class DenyAll(AuthProvider):
    name = 'deny-all'

    def authenticate(self, provider_name, credential):
        return None


class DevTokenAuth(AuthProvider):
    """Test/demo only: maps fixed tokens to player ids. Not for any network a stranger can reach."""

    name = 'dev-token'

    def __init__(self, tokens, *, development_only):
        if development_only is not True:
            raise ValueError('DevTokenAuth is for isolated development and tests only')
        for token, player in tokens.items():
            if not isinstance(token, str) or len(token) < 16 or not _PLAYER.fullmatch(player):
                raise ValueError('dev tokens must be >= 16 chars and map to valid player ids')
        self._tokens = dict(tokens)

    def authenticate(self, provider_name, credential):
        if provider_name != self.name or not isinstance(credential, str):
            return None
        for token, player in self._tokens.items():
            if hmac.compare_digest(token.encode(), credential.encode()):
                return AuthResult(player, self.name)
        return None
