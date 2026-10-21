"""BridgeHost travel mailbox: the launcher sends instructions, realm plugins poll, obey and report."""
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost

TOKEN = 'travel-test-token'


def call(host, method, path, body=None, token=None, realm='SR1'):
    headers = {'Authorization': 'Bearer ' + (token or TOKEN)}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    try:
        with urlopen(Request(host.url + path, data, headers, method=method), timeout=3) as r:
            return r.status, json.loads(r.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def plugin_event(host, realm, event, tid='t1', ack=None, detail=''):
    body = {'protocol_version': 1, 'message_type': 'travel_event', 'realm': realm, 'transition_id': tid, 'event': event}
    if ack:
        body['ack_id'] = ack
    if detail:
        body['detail'] = detail
    return call(host, 'POST', '/v1/travel/event', body, token=host.realm_token(realm))


class TravelMailboxTests(unittest.TestCase):
    def test_message_is_redelivered_until_the_plugin_acks_it(self):
        with BridgeHost(token=TOKEN) as host:
            status, reply = call(host, 'POST', '/v1/travel/send', {'realm': 'SR1', 'message': {'kind': 'prepare_departure', 'transition_id': 't1', 'destination': 'SR2'}})
            self.assertEqual(status, 200)
            mid = reply['message_id']
            for _ in range(2):
                status, got = call(host, 'GET', '/v1/travel/next?realm=SR1', token=host.realm_token('SR1'))
                self.assertEqual((status, got['message']['message_id'], got['message']['kind']), (200, mid, 'prepare_departure'))
            self.assertIsNone(call(host, 'GET', '/v1/travel/next?realm=SR2', token=host.realm_token('SR2'))[1]['message'])
            self.assertEqual(plugin_event(host, 'SR1', 'departure_ready', ack=mid)[0], 200)
            self.assertIsNone(call(host, 'GET', '/v1/travel/next?realm=SR1', token=host.realm_token('SR1'))[1]['message'])

    def test_events_are_ordered_and_readable_by_the_launcher_only(self):
        with BridgeHost(token=TOKEN) as host:
            plugin_event(host, 'SR1', 'departure_ready')
            plugin_event(host, 'SR2', 'arrived', detail='at portal')
            status, got = call(host, 'GET', '/v1/travel/events?since=0')
            self.assertEqual([(e['seq'], e['realm'], e['event']) for e in got['events']], [(1, 'SR1', 'departure_ready'), (2, 'SR2', 'arrived')])
            self.assertEqual(call(host, 'GET', '/v1/travel/events?since=1')[1]['events'][0]['detail'], 'at portal')
            self.assertEqual(call(host, 'GET', '/v1/travel/events?since=0', token=host.realm_token('SR1'))[0], 401)
            self.assertEqual(call(host, 'GET', '/v1/travel/events?since=x')[0], 400)

    def test_a_plugin_cannot_send_instructions_or_read_the_other_realm(self):
        with BridgeHost(token=TOKEN) as host:
            body = {'realm': 'SR2', 'message': {'kind': 'hold', 'transition_id': 't1'}}
            self.assertEqual(call(host, 'POST', '/v1/travel/send', body, token=host.realm_token('SR1'))[0], 401)
            call(host, 'POST', '/v1/travel/send', body)
            self.assertEqual(call(host, 'GET', '/v1/travel/next?realm=SR2', token=host.realm_token('SR1'))[0], 401)
            self.assertEqual(plugin_event(host, 'SR2', 'held') [0], 200)
            status, _ = call(host, 'POST', '/v1/travel/event', {'protocol_version': 1, 'message_type': 'travel_event', 'realm': 'SR2', 'transition_id': 't1', 'event': 'held'}, token=host.realm_token('SR1'))
            self.assertEqual(status, 401)

    def test_malformed_messages_are_refused(self):
        with BridgeHost(token=TOKEN) as host:
            for bad in ({'realm': 'SR3', 'message': {'kind': 'hold', 'transition_id': 't1'}},
                        {'realm': 'SR1', 'message': {'kind': 'format_disk', 'transition_id': 't1'}},
                        {'realm': 'SR1', 'message': {'kind': 'hold', 'transition_id': 'bad id!'}},
                        {'realm': 'SR1', 'message': {'kind': 'hold', 'transition_id': 't1', 'extra': 'x'}},
                        {'realm': 'SR1', 'message': {'kind': 'hold', 'transition_id': 't1', 'reason': 'x' * 200}},
                        {'realm': 'SR1'}):
                self.assertEqual(call(host, 'POST', '/v1/travel/send', bad)[0], 400, bad)
            for bad_event in ('detonate', ''):
                self.assertEqual(plugin_event(host, 'SR1', bad_event)[0], 400)
            self.assertEqual(plugin_event(host, 'SR1', 'held', tid='../x')[0], 400)

    def test_carried_items_are_validated_and_relayed(self):
        with BridgeHost(token=TOKEN) as host:
            ok = {'realm': 'SR2', 'message': {'kind': 'arrive', 'transition_id': 't1', 'carry': {'CARROT_VEGGIE': 3, 'IdentifiableType.Hen': 6}, 'carry_mode': 'ALL'}}
            self.assertEqual(call(host, 'POST', '/v1/travel/send', ok)[0], 200)
            got = call(host, 'GET', '/v1/travel/next?realm=SR2', token=host.realm_token('SR2'))[1]['message']
            self.assertEqual(got['carry'], {'CARROT_VEGGIE': 3, 'IdentifiableType.Hen': 6})
            for bad in ({'CARROT_VEGGIE': 0}, {'CARROT_VEGGIE': 10000}, {'bad name': 1}, {'X': 1.5}, {str(i): 1 for i in range(65)}, ['x']):
                body = {'realm': 'SR2', 'message': {'kind': 'arrive', 'transition_id': 't1', 'carry': bad}}
                self.assertEqual(call(host, 'POST', '/v1/travel/send', body)[0], 400, bad)
            self.assertEqual(call(host, 'POST', '/v1/travel/send', {'realm': 'SR1', 'message': {'kind': 'hold', 'transition_id': 't1', 'carry_mode': 'EVERYTHING'}})[0], 400)
            body = {'protocol_version': 1, 'message_type': 'travel_event', 'realm': 'SR2', 'transition_id': 't1', 'event': 'arrived', 'carry': {'CARROT_VEGGIE': 3}}
            self.assertEqual(call(host, 'POST', '/v1/travel/event', body, token=host.realm_token('SR2'))[0], 200)
            self.assertEqual(call(host, 'GET', '/v1/travel/events?since=0')[1]['events'][0]['carry'], {'CARROT_VEGGIE': 3})
            body['carry'] = {'CARROT_VEGGIE': -1}
            self.assertEqual(call(host, 'POST', '/v1/travel/event', body, token=host.realm_token('SR2'))[0], 400)

    def test_queue_and_event_log_are_bounded(self):
        with BridgeHost(token=TOKEN) as host:
            codes = [call(host, 'POST', '/v1/travel/send', {'realm': 'SR1', 'message': {'kind': 'hold', 'transition_id': f't{i}'}})[0] for i in range(20)]
            self.assertEqual(codes.count(200), 16)
            self.assertEqual(codes[-1], 429)
            for i in range(520):
                plugin_event(host, 'SR1', 'held', tid=f't{i % 7}')
            events = call(host, 'GET', '/v1/travel/events?since=0')[1]['events']
            self.assertEqual(len(events), 500)
            self.assertEqual(events[-1]['seq'], 520)


class RealmCredentialTests(unittest.TestCase):
    def test_only_the_admin_gets_a_realm_credential_and_the_launcher_hands_that_out(self):
        from apps.launcher.runtime import HostProcess
        with BridgeHost(token=TOKEN) as host:
            self.assertEqual(call(host, 'GET', '/v1/realm-token?realm=SR2')[1]['token'], host.realm_token('SR2'))
            self.assertEqual(call(host, 'GET', '/v1/realm-token?realm=SR2', token=host.realm_token('SR1'))[0], 401)
            self.assertEqual(call(host, 'GET', '/v1/realm-token?realm=SR9')[0], 400)
            proc = HostProcess('.')
            proc.running = lambda: True
            proc.state = lambda: {'state': 'RUNNING', 'url': host.url, 'pid': 1, 'heartbeats': {}}
            proc._token = TOKEN
            env = proc.realm_env('SR1')
            self.assertEqual(env, {'SLIMEBRIDGE_URL': host.url, 'SLIMEBRIDGE_TOKEN': host.realm_token('SR1')})
            self.assertNotEqual(env['SLIMEBRIDGE_TOKEN'], TOKEN)
            self.assertEqual(proc.realm_env('SR3'), {})


class ResumeJumpsTheQueueTests(unittest.TestCase):
    def test_a_resume_replaces_instructions_of_the_same_crossing(self):
        with BridgeHost(token=TOKEN) as host:
            call(host, 'POST', '/v1/travel/send', {'realm': 'SR1', 'message': {'kind': 'prepare_departure', 'transition_id': 't1'}})
            call(host, 'POST', '/v1/travel/send', {'realm': 'SR1', 'message': {'kind': 'hold', 'transition_id': 't2'}})
            call(host, 'POST', '/v1/travel/send', {'realm': 'SR1', 'message': {'kind': 'resume', 'transition_id': 't1'}})
            first = call(host, 'GET', '/v1/travel/next?realm=SR1', token=host.realm_token('SR1'))[1]['message']
            self.assertEqual((first['kind'], first['transition_id']), ('hold', 't2'))   # t2 untouched, t1's stuck prepare is gone
            self.assertEqual(host._travel_queue['SR1'][1]['kind'], 'resume')


if __name__ == '__main__':
    unittest.main()
