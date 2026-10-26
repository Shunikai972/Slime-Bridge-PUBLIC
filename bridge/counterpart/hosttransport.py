"""Counterpart transport over the existing BridgeHost public API.

Creation is bound to the owned PID and the intersection of host/transport realm opt-ins.
SR2 remains unavailable by default; protected lab authorization is not runtime proof.
"""


class BridgeHostTransport:
    def __init__(self, host, *, creation_realms=('SR1',)):
        self.host = host
        self.creation_realms = tuple(realm for realm in creation_realms if realm in host.creation_realms)
        self.outstanding = {}

    def live_session(self, realm):
        """The realm's live plugin session as seen by BridgeHost heartbeats (PID-bound), or None."""
        heartbeat = self.host.heartbeats().get(realm)
        if not heartbeat or type(heartbeat.get('pid')) is not int:
            return None
        return {'pid': heartbeat['pid'], 'session_id': f"{realm.lower()}-pid-{heartbeat['pid']}"}

    def health(self, realm):
        if realm not in self.creation_realms:
            return 'UNAVAILABLE'
        return 'HEALTHY' if self.live_session(realm) else 'UNAVAILABLE'

    def preflight(self, realm, session):
        return (realm in self.creation_realms and isinstance(session, dict) and
                type(session.get('pid')) is int and 0 < session['pid'] < 2 ** 32 and
                self.live_session(realm) == session)

    def send(self, realm, command, session):
        self.host.enqueue_creation_command(realm, command['kind'], command['creation_id'], op_id=command['op_id'],
                                           expected_pid=session['pid'])
        self.outstanding[command['op_id']] = (realm, dict(session))

    def poll(self, service, timeout=0.0):
        """Feed every arrived plugin result to the service; returns the service verdicts."""
        verdicts = []
        for op_id, (realm, session) in sorted(self.outstanding.items()):
            result = self.host.wait_result(op_id, timeout)
            if result is None:
                continue
            del self.outstanding[op_id]
            verdicts.append(service.on_wire_result(realm, result, session=session))
        return verdicts

    def forget(self):
        """After a host restart nothing in flight can be answered any more (reconcile instead)."""
        self.outstanding.clear()
