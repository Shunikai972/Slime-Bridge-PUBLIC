"""Realm-neutral cross-game save counterpart system (host side).

Generic modules (model, matching, journal, policy, verification, world_ready, startup, pair) never
contain realm-specific native details. Those live only in ``bridge.counterpart.realms`` adapters.
The existing ``bridge.profile.store.ProfileStore`` stays the canonical link record and is used
through its public API only.
"""
