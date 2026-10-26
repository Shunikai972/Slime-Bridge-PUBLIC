"""Synthetic linked-profile host for inventory authorization tests only."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost
from transfer_binding_fixtures import PairFixture


class SyntheticTransferHost:
    """A host whose pair, endpoints, heartbeats, and receipts are explicitly synthetic."""

    def __init__(self, root, store, *, token='local-test-token', sender_save='a', receiver_save='b'):
        self.pair = PairFixture(root)
        self.profile_id = self.pair.linked(sender_save=sender_save, receiver_save=receiver_save)
        self.pair.load(self.profile_id)
        self.host = BridgeHost(token=token, transfer_store=store,
                               transfer_bindings=self.pair.resolver,
                               endpoint_provider=self.pair.report,
                               endpoint_trust=self.pair.trust)

    def __enter__(self):
        self.host.__enter__()
        self.heartbeat('SR1')
        self.heartbeat('SR2')
        return self

    def __exit__(self, exc_type, exc, tb):
        self.host.__exit__(exc_type, exc, tb)

    def heartbeat(self, realm, *, pid=None, request_id=None):
        payload = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': realm,
                   'request_id': request_id or 'synthetic-' + realm, 'pid': os.getpid() if pid is None else pid}
        headers = {'Authorization': 'Bearer ' + self.host.realm_token(realm),
                   'Content-Type': 'application/json'}
        return self._post('/v1/heartbeat', payload, headers=headers)

    def prepare(self, *, key='synthetic-transfer', item='slime.pink', count=1,
                sender='SR1', sender_save='a', receiver='SR2', receiver_save='b'):
        payload = {'bridge_profile_id': self.profile_id, 'sender_realm': sender,
                   'sender_save_id': sender_save, 'receiver_realm': receiver,
                   'receiver_save_id': receiver_save, 'canonical_item_id': item,
                   'count': count, 'idempotency_key': key}
        data = self._post('/v1/transfer/prepare', payload)
        return data['transfer_id']

    def action(self, action, transfer_id, **fields):
        return self._post('/v1/transfer/' + action, {'transfer_id': transfer_id, **fields})

    def enqueue(self, transfer_id, suffix, realm, kind, item, count, save_id):
        return self.host.enqueue_command(realm, kind, item, count,
                                         op_id=transfer_id + ':' + suffix, save_id=save_id)

    def poll(self, realm):
        headers = {'Authorization': 'Bearer ' + self.host.realm_token(realm)}
        with urlopen(Request(self.host.url + '/v1/commands/next?realm=' + realm,
                             headers=headers), timeout=3) as response:
            return json.loads(response.read())['command']

    def result(self, command, realm, *, before, after, can_add=None, **overrides):
        kind = command['kind']
        payload = {'protocol_version': 1, 'message_type': 'command_result', 'realm': realm,
                   'pid': os.getpid(), 'op_id': command['op_id'], 'kind': kind,
                   'item': command['item'], 'count': command['count'],
                   'save_id': command['save_id'], 'ok': True,
                   'applied': kind != 'query', 'before': before, 'after': after,
                   'uncertain': False, 'persisted': kind != 'query'}
        if kind == 'query':
            payload['can_add'] = bool(can_add)
        else:
            payload.update(verified=True, sha256='a' * 64,
                           save_file='synthetic_' + realm.lower() + '_0')
        payload.update(overrides)
        headers = {'Authorization': 'Bearer ' + self.host.realm_token(realm),
                   'Content-Type': 'application/json'}
        return self._post('/v1/commands/result', payload, headers=headers)

    def query_pair(self, transfer_id, *, item='slime.pink', count=1,
                   sender='SR1', sender_save='a', receiver='SR2', receiver_save='b'):
        source_op = self.enqueue(transfer_id, 'q-src', sender, 'query', item, count, sender_save)
        source = self.poll(sender)
        if source['op_id'] != source_op:
            raise AssertionError('source query poll returned a different command')
        self.result(source, sender, before=5, after=5, can_add=True)
        destination_op = self.enqueue(transfer_id, 'q-dst', receiver, 'query', item, count, receiver_save)
        destination = self.poll(receiver)
        if destination['op_id'] != destination_op:
            raise AssertionError('destination query poll returned a different command')
        self.result(destination, receiver, before=0, after=0, can_add=True)

    def remove_receipt(self, transfer_id, *, item='slime.pink', count=1,
                       sender='SR1', sender_save='a', before=5):
        op_id = self.enqueue(transfer_id, 'remove', sender, 'remove', item, count, sender_save)
        command = self.poll(sender)
        if command['op_id'] != op_id:
            raise AssertionError('remove poll returned a different command')
        self.result(command, sender, before=before, after=before - count)

    def add_receipt(self, transfer_id, *, item='slime.pink', count=1,
                    receiver='SR2', receiver_save='b', before=0):
        op_id = self.enqueue(transfer_id, 'add', receiver, 'add', item, count, receiver_save)
        command = self.poll(receiver)
        if command['op_id'] != op_id:
            raise AssertionError('receiver add poll returned a different command')
        self.result(command, receiver, before=before, after=before + count)

    def refund_receipt(self, transfer_id, *, item='slime.pink', count=1,
                       sender='SR1', sender_save='a', before=0):
        op_id = self.enqueue(transfer_id, 'refund', sender, 'add', item, count, sender_save)
        command = self.poll(sender)
        if command['op_id'] != op_id:
            raise AssertionError('refund add poll returned a different command')
        self.result(command, sender, before=before, after=before + count)

    def _post(self, path, payload, *, headers=None):
        if headers is None:
            headers = {'Authorization': 'Bearer ' + self.host.token,
                       'Content-Type': 'application/json'}
        with urlopen(Request(self.host.url + path, json.dumps(payload).encode(), headers), timeout=3) as response:
            data = json.loads(response.read())
            data['_status'] = response.status
            return data
