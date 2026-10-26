"""Link-store adapter over the existing ``ProfileStore`` (public API only, file unchanged).

ProfileStore remains the canonical UNLINKED/CREATING/LINKED record and keeps its own fences:
``reserve_counterpart`` (one creation id per profile), ``claim_creation`` (issue the native create
at most once) and ``confirm_counterpart`` (idempotent link that refuses conflicting identities and
cross-linked saves). Limitation inherited from it: a creation id cannot be abandoned, so a creation
that is proven absent after dispatch needs an operator decision (documented for the local provider).
"""


class ProfileStoreLinks:
    def __init__(self, store):
        self.store = store

    def snapshot(self, profile_id):
        return self.store.get(profile_id)

    def reserve(self, profile_id, target_realm):
        return self.store.reserve_counterpart(profile_id, target_realm)['creation_id']

    def claim(self, profile_id, creation_id):
        return self.store.claim_creation(profile_id, creation_id)

    def confirm(self, profile_id, creation_id, endpoint):
        return self.store.confirm_counterpart(profile_id, creation_id, endpoint['installation_id'],
                                              endpoint['native_save_id'], endpoint['native_save_identity'])

    def source_endpoint(self, profile_id):
        snap = self.store.get(profile_id)
        endpoints = [(realm, snap[realm.lower()]) for realm in ('SR1', 'SR2') if snap[realm.lower()]]
        if len(endpoints) != 1:
            raise ValueError('counterpart source must be exactly one existing endpoint')
        realm, endpoint = endpoints[0]
        return {'realm': realm, **endpoint}
