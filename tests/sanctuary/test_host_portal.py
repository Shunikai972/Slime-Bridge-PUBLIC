"""BridgeHost accepts a portal crossing request from a realm plugin and hands it to the launcher hook."""
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost

TOKEN = 'portal-test-token'
BODY = {'protocol_version': 1, 'message_type': 'portal_cross', 'realm': 'SR1', 'portal_id': 'portal.sr1.sanctuary_gate',
        'save_id': '20261014231821_SlimeBridgeSanctuaryTest', 'request_id': 'req-1'}


def post(host, body, token=None, realm='SR1'):
    headers = {'Authorization': 'Bearer ' + (token or host.realm_token(realm)), 'Content-Type': 'application/json'}
    try:
        with urlopen(Request(host.url + '/v1/portal/cross', json.dumps(body).encode(), headers), timeout=3) as r:
            return r.status, json.loads(r.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def admin_requests(host):
    request = Request(host.url + '/v1/portal/requests', headers={'Authorization': 'Bearer ' + TOKEN})
    with urlopen(request, timeout=3) as r:
        return json.loads(r.read())['requests']


class PortalCrossTests(unittest.TestCase):
    def test_request_is_recorded_and_visible_to_the_launcher_only(self):
        with BridgeHost(token=TOKEN) as host:
            status, reply = post(host, BODY)
            self.assertEqual((status, reply['state']), (200, 'RECEIVED'))
            rows = admin_requests(host)
            self.assertEqual([(r['request_id'], r['realm'], r['state']) for r in rows], [('req-1', 'SR1', 'RECEIVED')])
            with self.assertRaises(HTTPError) as denied:             # a realm token cannot read the list
                urlopen(Request(host.url + '/v1/portal/requests', headers={'Authorization': 'Bearer ' + host.realm_token('SR1')}), timeout=3)
            self.assertEqual(denied.exception.code, 401)

    def test_hook_decides_and_a_repeated_request_id_never_runs_twice(self):
        calls = []

        def hook(request):
            calls.append(request['request_id'])
            return {'state': 'TRANSITION_STARTED', 'detail': 'linked saves ok'}

        with BridgeHost(token=TOKEN, portal_handler=hook) as host:
            self.assertEqual(post(host, BODY)[1]['state'], 'TRANSITION_STARTED')
            self.assertEqual(post(host, BODY)[1]['state'], 'TRANSITION_STARTED')
            self.assertEqual(calls, ['req-1'])
            self.assertEqual(admin_requests(host)[0]['detail'], 'linked saves ok')

    def test_a_failing_hook_becomes_a_status_not_a_crash(self):
        def hook(request):
            raise RuntimeError('boom')

        with BridgeHost(token=TOKEN, portal_handler=hook) as host:
            status, reply = post(host, BODY)
            self.assertEqual((status, reply['state']), (200, 'HANDLER_ERROR'))
            self.assertEqual(post(host, dict(BODY, request_id='req-2'))[0], 200)

    def test_malformed_unauthorised_and_foreign_requests_are_refused(self):
        with BridgeHost(token=TOKEN) as host:
            for bad in (dict(BODY, message_type='heartbeat'), dict(BODY, realm='SR3'), dict(BODY, portal_id='Bad Id!'),
                        dict(BODY, save_id='  '), dict(BODY, extra=1), {k: v for k, v in BODY.items() if k != 'request_id'},
                        dict(BODY, protocol_version=2)):
                self.assertEqual(post(host, bad)[0], 400, bad)
            self.assertEqual(post(host, BODY, token='wrong')[0], 401)
            # the SR2 token cannot speak for SR1
            self.assertEqual(post(host, BODY, token=host.realm_token('SR2'))[0], 401)
            self.assertEqual(admin_requests(host), [])

    def test_list_is_bounded(self):
        with BridgeHost(token=TOKEN) as host:
            for i in range(80):
                post(host, dict(BODY, request_id=f'req-{i}'))
            rows = admin_requests(host)
            self.assertEqual(len(rows), 64)
            self.assertEqual(rows[-1]['request_id'], 'req-79')

    def test_hook_must_be_callable(self):
        with self.assertRaises(ValueError):
            BridgeHost(token=TOKEN, portal_handler='nope')


if __name__ == '__main__':
    unittest.main()
