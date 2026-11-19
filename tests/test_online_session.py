"""One online code for both games: the launcher's session state machine against simulated plugins (no game, no network)."""
import unittest

from apps.launcher import online_session as O

SESSION = 'Rk7mP2xQa'          # a Ranching Together join code (9 Base58 characters)


class Games:
    """What the plugins report (heartbeats) and receive (mailbox), with a clock."""

    def __init__(self):
        self.now, self.running, self.ready, self.beats, self.sent, self.events = 0.0, set(), set(), {}, [], {}

    def online(self, realm, **flags):
        state = {'mod': True, 'signed_in': True, 'hosting': False, 'connected': False, 'code': '', 'error': ''}
        state.update(flags)
        self.beats[realm] = {'online': state}

    def session(self):
        return O.OnlineSession(self.send, lambda realm: self.beats.get(realm, {}), lambda realm: realm in self.running,
                               lambda realm: realm in self.ready, lambda rid: self.events.get(rid, []), lambda: self.now)

    def send(self, realm, kind, record, **extra):
        self.sent.append((realm, kind, extra))
        return True


class OnlineSessionTests(unittest.TestCase):
    def setUp(self):
        self.g = Games()
        self.s = self.g.session()

    def both_running(self):
        self.g.running |= {'SR1', 'SR2'}
        self.g.ready |= {'SR1', 'SR2'}
        self.g.online('SR1')
        self.g.online('SR2')

    def test_codes_say_which_games_they_join(self):
        self.assertEqual(O.realms_for_code(SESSION), ('SR1', 'SR2'))
        self.assertEqual(O.realms_for_code('K7QX2PA'), ('SR1',))
        for bad in ('', 'Rk7mP2xQ', 'Rk7mP2xQ0', 'Rk7mP2xQaa', 'hello world', 'K7QX2P'):
            self.assertEqual(O.realms_for_code(O.clean(bad)), (), bad)         # 0 is not Base58, lengths are exact
        self.assertEqual(O.clean(' Rk7m-P2xQa '), SESSION)

    def test_hosting_both_games_puts_slime_rancher_under_the_same_code(self):
        self.both_running()
        self.s.host('BOTH')
        self.s.tick()
        self.assertEqual(self.g.sent, [('SR2', 'online_host', {})], 'Slime Rancher 2 opens first: its code is the session code')
        self.s.tick()
        self.assertEqual(self.s.request['step'], 'sr2_auto_host')
        self.g.online('SR2', hosting=True, code=SESSION)
        self.s.tick()
        self.assertEqual(len(self.g.sent), 1, 'heartbeat alone cannot complete a dispatched host attempt')
        self.g.events[self.s.request['id']] = [{'realm': 'SR2', 'event': 'online_done'}]
        self.s.tick()
        self.assertEqual(self.g.sent[-1], ('SR1', 'online_host', {'code': SESSION}))
        self.assertEqual(self.s.request['step'], 'sr1_lobby')
        self.g.online('SR1', hosting=True, code=SESSION)
        self.s.tick()
        self.assertTrue(self.s.request['done'])
        self.assertEqual((self.s.request['step'], self.s.code()), ('hosting', SESSION))
        self.assertEqual(len(self.g.sent), 2, 'each game is asked once')

    def test_hosting_only_slime_rancher_shares_srmp_code(self):
        self.both_running()
        self.s.host('SR1')
        self.s.tick()
        self.assertEqual(self.g.sent, [('SR1', 'online_host', {'code': ''})])
        self.g.online('SR1', hosting=True, code='K7QX2PA')
        self.s.tick()
        self.assertEqual((self.s.request['step'], self.s.code()), ('hosting', 'K7QX2PA'))

    def test_hosting_waits_for_the_games_to_be_in_a_world(self):
        self.s.host('BOTH')
        self.s.tick()
        self.assertEqual((self.g.sent, self.s.request['step']), ([], 'waiting_games'))
        self.g.running.add('SR2'); self.g.online('SR2')
        self.s.tick()
        self.assertEqual(self.g.sent, [], 'still at the menu: nothing to host yet')
        self.g.ready.add('SR2')
        self.s.tick()
        self.assertEqual(self.g.sent, [('SR2', 'online_host', {})])

    def test_auto_host_failure_is_shown_and_ends_the_request(self):
        self.both_running()
        self.s.host('BOTH')
        self.s.tick()
        self.g.events[self.s.request['id']] = [{'realm': 'SR2', 'event': 'failed', 'detail': 'auto_host: UPnP is unavailable'}]
        self.s.tick()
        self.assertTrue(self.s.request['done'])
        self.assertEqual(self.s.request['step'], 'failed')
        self.assertIn('UPnP', self.s.request['error'])

    def test_auto_host_retry_ignores_preexisting_error_until_fresh_result(self):
        self.both_running()
        self.g.online('SR2', error='auto_host: no UPnP')
        self.s.host('SR2')
        self.s.tick()
        self.s.tick()
        self.assertFalse(self.s.request['done'])
        self.assertEqual(self.g.sent, [('SR2', 'online_host', {})])
        self.g.events[self.s.request['id']] = [{'realm': 'SR2', 'event': 'failed', 'detail': 'auto_host: no UPnP'}]
        self.s.tick()
        self.assertEqual(self.s.request['error'], 'SR2: auto_host: no UPnP')

    def test_a_plugin_refusal_ends_the_request(self):
        self.both_running()
        self.s.host('SR1')
        self.s.tick()
        self.g.events[self.s.request['id']] = [{'realm': 'SR1', 'event': 'failed', 'detail': 'eos_signing_in'}]
        self.s.tick()
        self.assertEqual(self.s.request['error'], 'SR1: eos_signing_in')

    def test_another_lobby_in_slime_rancher_is_not_hidden(self):
        self.both_running()
        self.g.online('SR2', hosting=True, code=SESSION)
        self.g.online('SR1', hosting=True, code='OTHER12')
        self.s.host('BOTH')
        self.s.tick()
        self.assertEqual(self.s.request['error'], 'SR1_OTHER_LOBBY')

    def test_session_code_requires_explicit_in_game_join(self):
        self.both_running()
        with self.assertRaisesRegex(ValueError, 'SR2_JOIN_IN_GAME'):
            self.s.join(SESSION)
        self.assertIsNone(self.s.request)
        self.assertEqual(self.g.sent, [])

    def test_a_seven_character_code_joins_slime_rancher_only(self):
        self.both_running()
        self.s.join('k7qx2pa')
        self.s.tick()
        self.assertEqual(self.g.sent, [('SR1', 'online_join', {'code': 'K7QX2PA'})])

    def test_existing_different_lobby_never_confirms_requested_join(self):
        self.both_running()
        self.g.online('SR1', connected=True, code='OTHER12')
        self.s.join('K7QX2PA')
        self.s.tick()
        self.assertEqual(self.s.request['error'], 'SR1_OTHER_LOBBY')
        self.assertEqual(self.g.sent, [])

    def test_requested_lobby_reports_membership_not_game_connection(self):
        self.both_running()
        self.s.join('K7QX2PA')
        self.s.tick()
        self.g.online('SR1', connected=True, code='K7QX2PA')
        self.s.tick()
        self.assertEqual(self.s.request['step'], 'lobby_joined')

    def test_closed_game_after_host_dispatch_reports_exit(self):
        self.both_running()
        self.s.host('BOTH')
        self.s.tick()
        self.g.running.remove('SR2')
        self.s.tick()
        self.assertEqual(self.s.request['error'], 'SR2_EXITED')

    def test_bad_codes_are_refused(self):
        with self.assertRaises(ValueError):
            self.s.join('nope')
        with self.assertRaises(ValueError):
            self.s.host('ALL')

    def test_a_request_gives_up_after_the_deadline(self):
        self.s.host('BOTH')
        self.g.now += O.DEADLINE + 1
        self.s.tick()
        self.assertEqual(self.s.request['error'], 'TIMEOUT_WAITING_GAMES')

    def test_the_code_follows_the_games_even_when_hosted_from_their_own_menu(self):
        self.both_running()
        self.assertEqual(self.s.code(), '')
        self.g.online('SR2', hosting=True, code=SESSION)
        self.assertEqual(self.s.code(), SESSION)
        self.g.running.discard('SR2')
        self.assertEqual(self.s.code(), '', 'a closed game shares nothing')


if __name__ == '__main__':
    unittest.main()


class EpicSignInTests(unittest.TestCase):
    def test_slime_rancher_is_asked_to_host_before_its_epic_sign_in(self):
        g = Games()
        s = g.session()
        g.running.add('SR1'); g.ready.add('SR1')
        g.online('SR1', signed_in=False)          # SRMP has no username yet: the plugin signs in when asked
        s.host('SR1')
        s.tick()
        self.assertEqual(g.sent, [('SR1', 'online_host', {'code': ''})])


class PlayerNameTests(unittest.TestCase):
    def test_the_players_own_name_goes_to_slime_rancher_only_when_given(self):
        g = Games(); s = g.session()
        g.running.add('SR1'); g.ready.add('SR1'); g.online('SR1', signed_in=False)
        s.host('SR1', 'Noam_42')
        s.tick()
        self.assertEqual(g.sent, [('SR1', 'online_host', {'code': '', 'name': 'Noam_42'})])
        g2 = Games(); s2 = g2.session()
        g2.running.add('SR1'); g2.ready.add('SR1'); g2.online('SR1')
        s2.host('SR1')
        s2.tick()
        self.assertEqual(g2.sent, [('SR1', 'online_host', {'code': ''})], 'no name is ever invented')

    def test_a_name_srmp_would_refuse_is_refused_here(self):
        s = Games().session()
        for bad in ('9lives', 'two words', 'émile', 'x' * 25):
            with self.assertRaises(ValueError):
                s.host('SR1', bad)
        with self.assertRaises(ValueError):
            s.join('K7QX2PA', 'bad name')

    def test_a_refused_sign_in_ends_the_request_with_the_reason(self):
        g = Games(); s = g.session()
        g.running.add('SR1'); g.ready.add('SR1'); g.online('SR1', signed_in=False)
        s.host('SR1')
        s.tick()
        g.events[s.request['id']] = [{'realm': 'SR1', 'event': 'failed', 'detail': 'needs_name'}]
        s.tick()
        self.assertEqual(s.request['error'], 'SR1: needs_name')


class BridgeHostOnlineTests(unittest.TestCase):
    """The plugins' online report and the launcher's online instructions through a real BridgeHost."""

    def setUp(self):
        import json, os
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from bridge.host.server import BridgeHost
        self.json, self.HTTPError, self.Request, self.urlopen, self.pid = json, HTTPError, Request, urlopen, os.getpid()
        self.host = BridgeHost(token='online-token').__enter__()
        self.addCleanup(self.host.__exit__, None, None, None)

    def post(self, path, body, token):
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        try:
            with self.urlopen(self.Request(self.host.url + path, self.json.dumps(body).encode(), headers), timeout=3) as r:
                return r.status
        except self.HTTPError as exc:
            return exc.code

    def beat(self, online):
        body = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': 'SR2', 'request_id': 'r1', 'pid': self.pid, 'online': online}
        return self.post('/v1/heartbeat', body, self.host.realm_token('SR2'))

    def test_the_online_report_reaches_the_launcher(self):
        online = {'mod': True, 'signed_in': True, 'hosting': True, 'connected': False, 'code': SESSION, 'error': ''}
        self.assertEqual(self.beat(online), 200)
        self.assertEqual(self.host.heartbeats()['SR2']['online'], online)

    def test_a_malformed_online_report_is_refused(self):
        good = {'mod': True, 'signed_in': True, 'hosting': True, 'connected': False, 'code': SESSION, 'error': ''}
        for bad in (dict(good, code='bad code!'), dict(good, hosting='yes'), dict(good, extra=1), dict(good, error='x' * 81), 'online'):
            self.assertEqual(self.beat(bad), 400, bad)

    def test_online_instructions_carry_only_a_clean_code(self):
        def send(message):
            return self.post('/v1/travel/send', {'realm': 'SR1', 'message': message}, 'online-token')
        self.assertEqual(send({'kind': 'online_host', 'transition_id': 'abc123', 'code': SESSION}), 200)
        self.assertEqual(self.host.travel_peek('SR1')['code'], SESSION)
        self.assertEqual(send({'kind': 'online_join', 'transition_id': 'abc124', 'code': 'K7QX2PA'}), 200)
        self.assertEqual(send({'kind': 'online_join', 'transition_id': 'abc125', 'code': 'a b'}), 400)
        self.assertEqual(send({'kind': 'online_host', 'transition_id': 'abc127', 'name': 'Noam_42'}), 200)
        self.assertEqual(send({'kind': 'online_host', 'transition_id': 'abc128', 'name': 'two words'}), 400)
        self.assertEqual(self.post('/v1/travel/send', {'realm': 'SR2', 'message': {
            'kind': 'online_join', 'transition_id': 'abc125', 'code': SESSION}}, 'online-token'), 400)
        self.assertEqual(send({'kind': 'save_quit', 'transition_id': 'abc126'}), 400)
        self.assertEqual(send({'kind': 'save_quit', 'transition_id': 'abc126', 'target_pid': self.pid, 'target_save': 'fixture'}), 200)
