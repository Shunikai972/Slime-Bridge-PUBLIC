"""Automatic save pairing: a live save is identified -> profile resolved -> partner ensured.

This is the normal, zero-configuration flow: the player launches either game and loads a save; the
plugin reports the save identity through BridgeHost; ``AutoPairing.on_save_identified`` resolves (or
creates) the Bridge profile through ``ProfileStore.find_or_create`` (which refuses an identity
change instead of relinking), records the endpoint as observed under the running build and lets the
orchestrator act for the other realm. Later events (plugin replies, timeouts, restarts) call
``resume``. No manual slot choice exists anywhere in this flow.
"""


class AutoPairing:
    def __init__(self, service, orchestrator, store, *, installations):
        """``installations``: realm -> Bridge installation id of the target Lab copy for that realm."""
        self.service, self.orchestrator, self.store = service, orchestrator, store
        self.installations = dict(installations)

    def on_save_identified(self, realm, installation_id, native_save_id, native_save_identity, *, build_fingerprint):
        try:
            profile = self.store.find_or_create(realm, installation_id, native_save_id, native_save_identity)
        except ValueError as exc:
            # Same native save id with a different identity: never relink automatically.
            return {'profile_id': None, 'decision': 'BLOCK_CONFLICT', 'reasons': ['SAVE_IDENTITY_CHANGED'],
                    'detail': str(exc)[:200], 'trace': []}
        profile_id = profile['bridge_profile_id']
        self.service.observe_live(profile_id, realm, build_fingerprint=build_fingerprint)
        return self.resume(profile_id, 'SR2' if realm == 'SR1' else 'SR1')

    def resume(self, profile_id, target_realm, *, max_steps=12):
        trace = self.orchestrator.run(profile_id, target_realm, self.installations[target_realm], max_steps=max_steps)
        last = trace[-1] if trace else {'decision': None, 'reasons': []}
        return {'profile_id': profile_id, 'target_realm': target_realm, 'decision': last['decision'],
                'reasons': last.get('reasons', []), 'trace': trace,
                'pair': self.service.pair(profile_id, current_builds={
                    realm: self.orchestrator.builds(realm) for realm in ('SR1', 'SR2')})['status']}
