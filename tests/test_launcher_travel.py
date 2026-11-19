"""The launcher-side crossing coordinator against a real in-process BridgeHost and simulated realm plugins."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

from apps.launcher import travel
from bridge.host.server import BridgeHost

TOKEN = 'travel-coordinator-token'


class FakeHostProcess:
    def __init__(self, host):
        self.host, self.beats = host, {}
        self.child = object()

    def owned_child(self):
        return self.child

    def state(self):
        if self.child is None:
            return {'state': 'STOPPED', 'url': None, 'pid': None, 'heartbeats': {}}
        return {'state': 'RUNNING', 'url': self.host.url, 'pid': 1, 'heartbeats': dict(self.beats)}

    def admin_token(self):
        return TOKEN

    def env(self):
        return {'SLIMEBRIDGE_URL': self.host.url, 'SLIMEBRIDGE_TOKEN': TOKEN}

    def alive(self, realm, save, pid):
        self.beats[realm] = {'pid': pid, 'status': {'player_alive': True, 'save_id': save}}


class FakeLauncher:
    def __init__(self, hostproc, running=()):
        self.hostproc, self._running, self.started = hostproc, set(running), []
        self.fail = False

    def running(self, realm):
        return realm in self._running

    def launch(self, installation, extra_env=None):
        if self.fail:
            raise OSError('x')
        self.started.append((installation['game'], dict(extra_env or {})))
        self._running.add(installation['game'])


class FakeWindows:
    def __init__(self):
        self.calls = []

    def focus(self, pid):
        self.calls.append(('focus', pid))

    def minimize(self, pid):
        self.calls.append(('minimize', pid))

    def restore(self, pid):
        self.calls.append(('restore', pid))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def post(host, path, body, token=None):
    headers = {'Authorization': 'Bearer ' + (token or TOKEN), 'Content-Type': 'application/json'}
    with urlopen(Request(host.url + path, json.dumps(body).encode(), headers), timeout=3) as r:
        return json.loads(r.read())


def cross(host, rid='r1', save='sr1-save', portal='portal.sr1.sanctuary_gate', realm='SR1'):
    post(host, '/v1/portal/cross', {'protocol_version': 1, 'message_type': 'portal_cross', 'realm': realm, 'portal_id': portal,
                                    'save_id': save, 'request_id': rid}, token=host.realm_token(realm))


def plugin_reply(host, realm, event, carry=None):
    """What a plugin does: take the pending instruction, report the matching event, acking it."""
    message = host.travel_peek(realm)
    assert message is not None, f'{realm} has no pending instruction'
    body = {'protocol_version': 1, 'message_type': 'travel_event', 'realm': realm, 'transition_id': message['transition_id'],
            'event': event, 'ack_id': message['message_id']}
    if carry is not None:
        body['carry'] = carry
    post(host, '/v1/travel/event', body, token=host.realm_token(realm))
    return message


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = BridgeHost(token=TOKEN).__enter__()
        self.addCleanup(self.host.__exit__, None, None, None)
        self.proc = FakeHostProcess(self.host)
        self.launcher = FakeLauncher(self.proc)
        self.windows, self.clock = FakeWindows(), Clock()
        self.links = travel.TravelLinks(Path(self.tmp.name) / 'links.json')
        self.settings = {'travel_link_mode': 'ASK'}      # the dialog flow; AutoLinkTests cover the default
        self.installs = {'SR1': {'game': 'SR1'}, 'SR2': {'game': 'SR2'}}
        self.ack = True
        self.saves = {'SR1': [], 'SR2': [{'group': 'sr2-save', 'label': 'S'}, {'group': 'sr2-linked', 'label': 'L'}, {'group': 'sr2-solo', 'label': 'solo'}]}
        self.coord = travel.TravelCoordinator(self.proc, self.launcher, lambda r: self.installs.get(r), lambda: self.ack, self.windows,
                                              self.links, settings=lambda: self.settings, clock=self.clock,
                                              list_saves=lambda realm: [dict(e) for e in self.saves.get(realm, [])])


    def state(self):
        return self.coord.poll()[-1]['state']

    def test_warm_crossing_both_games_running(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.assertEqual(self.state(), 'PREPARING')
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'prepare_departure')   # the source animates at once
        self.assertEqual(self.state(), 'PREPARING', 'waits for the source animation')
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.assertEqual(self.state(), 'SWITCHING')
        self.assertEqual(self.windows.calls, [('restore', 222), ('focus', 222)])
        self.assertEqual(plugin_reply(self.host, 'SR2', 'arrived')['kind'], 'arrive')
        self.assertEqual(self.state(), 'HOLDING')
        self.assertNotIn(('minimize', 111), self.windows.calls, 'the game left behind is not minimized (no desktop flash on return)')
        message = plugin_reply(self.host, 'SR1', 'held')
        self.assertEqual((message['kind'], message['policy']), ('hold', 'FREEZE'))
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('COMPLETE', 'ARRIVED'))
        self.assertEqual(self.launcher.started, [], 'nothing needed launching')

    def test_items_are_moved_not_copied_between_the_two_vacs(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR1')['carry_mode'], 'ALL')
        plugin_reply(self.host, 'SR1', 'departure_ready', carry={'CARROT_VEGGIE': 3, 'PINK_PLORT': 2, 'slime.cotton': 4})
        self.coord.poll()
        arrive = self.host.travel_peek('SR2')
        self.assertEqual((arrive['kind'], arrive['carry']), ('arrive', {'CARROT_VEGGIE': 3, 'PINK_PLORT': 2, 'slime.cotton': 4}))
        plugin_reply(self.host, 'SR2', 'arrived', carry={'CARROT_VEGGIE': 3, 'slime.cotton': 4})   # the vac was full for the plorts
        self.coord.poll()
        hold = self.host.travel_peek('SR1')
        self.assertEqual((hold['kind'], hold['carry']), ('hold', {'CARROT_VEGGIE': 3, 'slime.cotton': 4}), 'only what was accepted leaves the source')
        plugin_reply(self.host, 'SR1', 'held', carry={'CARROT_VEGGIE': 3, 'slime.cotton': 4})
        done = self.coord.poll()[-1]
        self.assertEqual(done['state'], 'COMPLETE')
        self.assertTrue(any('CARROT_VEGGIE x3' in h['text'] and 'slime.cotton x4' in h['text'] for h in done['history']))
        self.assertTrue(any('left in the SR1 vacpack' in h['text'] and 'PINK_PLORT x2' in h['text'] for h in done['history']))

    def test_nothing_is_offered_when_the_player_turned_carrying_off(self):
        self.settings['travel_carry'] = 'NONE'
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR1')['carry_mode'], 'NONE')
        plugin_reply(self.host, 'SR1', 'departure_ready', carry={'CARROT_VEGGIE': 3})
        self.coord.poll()
        self.assertNotIn('carry', self.host.travel_peek('SR2'))

    def test_the_source_never_gives_up_more_than_it_offered(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.assertEqual(self.state(), 'PREPARING')
        plugin_reply(self.host, 'SR1', 'departure_ready', carry={'PINK_PLORT': 7})
        self.assertEqual(self.state(), 'SWITCHING')
        plugin_reply(self.host, 'SR2', 'arrived', carry={'PINK_PLORT': 9, 'GOLD_PLORT': 5})   # a report larger than the offer
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR1')['carry'], {'PINK_PLORT': 7})
        plugin_reply(self.host, 'SR1', 'held', carry={'PINK_PLORT': 6})                      # the vac changed meanwhile
        done = self.coord.poll()[-1]
        self.assertEqual(done['state'], 'COMPLETE')
        self.assertTrue(any('carry mismatch' in h['text'] for h in done['history']), 'a short removal is reported, not hidden')

    def test_return_trip_uses_the_still_running_first_game(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host, rid='back', portal='portal.sr2.return_portal', realm='SR2', save='sr2-save')
        self.assertEqual(self.state(), 'PREPARING')
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'prepare_departure')
        plugin_reply(self.host, 'SR2', 'departure_ready')
        self.assertEqual(self.state(), 'SWITCHING')
        self.assertEqual(plugin_reply(self.host, 'SR1', 'arrived')['kind'], 'arrive')
        self.coord.poll()
        self.assertEqual(plugin_reply(self.host, 'SR2', 'held')['kind'], 'hold')
        self.assertEqual(self.coord.poll()[-1]['state'], 'COMPLETE')
        self.assertEqual(self.launcher.started, [])

    def test_destination_is_started_on_the_linked_save_while_the_source_animates(self):
        self.links.remember('sr1-save', 'sr2-linked')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.assertEqual(self.state(), 'PREPARING')
        game, env = self.launcher.started[0]
        self.assertEqual((game, env['SLIMEBRIDGE_TRAVEL_SAVE'], env['SLIMEBRIDGE_TRAVEL_MODE']), ('SR2', 'sr2-linked', 'DESTINATION'))
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'prepare_departure')
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.clock.now += 100
        self.assertEqual(self.state(), 'PREPARING', 'source is covered, destination still loading')
        self.proc.alive('SR2', 'sr2-linked', 222)
        self.assertEqual(self.state(), 'SWITCHING')
        self.assertIn(('minimize', 222), self.windows.calls)       # the new game was pushed back while loading

    def test_continue_mode_does_not_pass_a_save(self):
        self.links.remember('sr1-save', 'sr2-linked')
        self.settings['travel_save_mode'] = 'CONTINUE'
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.coord.poll()
        self.assertNotIn('SLIMEBRIDGE_TRAVEL_SAVE', self.launcher.started[0][1])

    def test_cold_destination_on_unrelated_save_never_receives_carry(self):
        self.links.remember('sr1-save', 'sr2-linked')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.assertEqual(self.state(), 'PREPARING')
        plugin_reply(self.host, 'SR1', 'departure_ready', carry={'PINK_PLORT': 7})
        self.proc.alive('SR2', 'sr2-solo', 222)
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('ROLLED_BACK', 'DESTINATION_WRONG_SAVE'))
        self.assertIsNone(self.host.travel_peek('SR2'))
        self.assertEqual(self.links.lookup('SR1', 'sr1-save'), 'sr2-linked')

    def test_a_silent_source_rolls_back_with_resume_and_focus(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.coord.poll()
        self.clock.now += 46
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('ROLLED_BACK', 'TIMEOUT_PREPARING'))
        self.assertIn(('focus', 111), self.windows.calls)
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'resume', 'a stuck instruction never blocks the way back')

    def test_a_failing_destination_rolls_back_after_the_source_already_left(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.coord.poll()
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.coord.poll()
        plugin_reply(self.host, 'SR2', 'failed')
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('ROLLED_BACK', 'DESTINATION_FAILED'))
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'resume')      # the source is told to come back
        self.assertIn(('focus', 111), self.windows.calls)

    def test_an_unlinked_save_asks_which_save_of_the_other_game_to_use(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        self.links.remember('other-sr1', 'sr2-solo')
        cross(self.host)
        self.assertEqual(self.state(), 'NEEDS_LINK')
        shown = self.coord.poll()[-1]
        self.assertEqual({c['group']: c['linked'] for c in shown['candidates']}, {'sr2-save': False, 'sr2-linked': False, 'sr2-solo': True})
        self.assertEqual(self.launcher.started, [], 'nothing is started until the player chooses')
        self.assertIsNone(self.host.travel_peek('SR1'), 'the source keeps playing')
        self.assertEqual(self.coord.choose_link(shown['id'], 'not-a-save'), {'ok': False, 'error': 'UNKNOWN_SAVE'})
        self.assertEqual(self.coord.choose_link(shown['id'], 'sr2-linked'), {'ok': True})
        game, env = self.launcher.started[0]
        self.assertEqual((game, env['SLIMEBRIDGE_TRAVEL_SAVE']), ('SR2', 'sr2-linked'))
        self.assertEqual(self.links.lookup('SR1', 'sr1-save'), 'sr2-linked')
        self.assertEqual(self.links.lookup('SR2', 'sr2-linked'), 'sr1-save')
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'prepare_departure')
        self.assertEqual(self.coord.choose_link(shown['id'], 'sr2-linked')['error'], 'NOT_WAITING')

    def test_cancelling_the_choice_leaves_everything_as_it_was(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        shown = self.coord.poll()[-1]
        self.assertEqual(self.coord.choose_link(shown['id'], None), {'ok': True})
        self.assertEqual(self.coord.poll()[-1]['state'], 'CANCELLED')
        self.assertEqual((self.launcher.started, self.links.all()), ([], {}))

    def test_nobody_answering_ends_the_wait(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.coord.poll()
        self.clock.now += 241
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('FAILED', 'LINK_TIMEOUT'))

    def test_a_running_destination_on_another_save_is_never_switched(self):
        self.links.remember('sr1-save', 'sr2-linked')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-solo', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('FAILED', 'DESTINATION_WRONG_SAVE'))
        self.assertEqual(done['detail'], {'running': 'sr2-solo', 'linked': 'sr2-linked'})
        told = self.host.travel_peek('SR1')                 # the player, still in SR1, is told why instead of nothing happening
        self.assertEqual((told['kind'], told['reason']), ('resume', 'DESTINATION_WRONG_SAVE'))

    def test_a_running_unlinked_destination_is_offered_first(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-solo', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        shown = self.coord.poll()[-1]
        self.assertEqual([c['group'] for c in shown['candidates'] if c['open']], ['sr2-solo'])
        self.coord.choose_link(shown['id'], 'sr2-solo')
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.assertEqual(self.state(), 'SWITCHING')
        self.assertEqual(self.launcher.started, [], 'an already running game is not restarted')

    def test_a_linked_save_missing_from_the_listing_is_still_requested(self):
        self.links.remember('sr1-save', 'sr2-deleted')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.assertEqual(self.state(), 'PREPARING')
        self.assertEqual(self.links.lookup('SR1', 'sr1-save'), 'sr2-deleted')
        self.assertEqual(self.launcher.started[0][1]['SLIMEBRIDGE_TRAVEL_SAVE'], 'sr2-deleted')
        self.assertNotIn('SLIMEBRIDGE_TRAVEL_NEW', self.launcher.started[0][1])

    def test_an_unreadable_save_folder_never_breaks_a_link(self):
        # Saves kept somewhere the launcher cannot list (a non-Steam copy): the linked save is still the one started.
        self.saves['SR2'] = []
        self.links.remember('sr1-save', 'sr2-elsewhere')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.assertEqual(self.coord.poll()[-1]['state'], 'PREPARING')
        game, env = self.launcher.started[0]
        self.assertEqual((game, env['SLIMEBRIDGE_TRAVEL_SAVE']), ('SR2', 'sr2-elsewhere'))
        self.assertEqual(self.links.lookup('SR1', 'sr1-save'), 'sr2-elsewhere')

    def _crossed_to_sr2(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.coord.poll()
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.coord.poll()
        plugin_reply(self.host, 'SR2', 'arrived')
        self.coord.poll()
        plugin_reply(self.host, 'SR1', 'held')
        return self.coord.poll()[-1]


    def test_closing_the_game_the_player_is_in_saves_and_closes_the_parked_one(self):
        self.coord._session = travel.LastSession(Path(self.tmp.name) / 'last.json')
        self.assertEqual(self._crossed_to_sr2()['state'], 'COMPLETE')       # the player is in SR2, SR1 waits at its portal
        self.coord.poll()
        self.assertIsNone(self.host.travel_peek('SR1'), 'nothing is sent while the player plays')
        self.launcher._running.discard('SR2')                                # the player closed SR2
        last = self.coord.poll()[-1]
        self.assertEqual((last['state'], last['to']), ('CLOSING_SESSION', 'SR1'))
        message = self.host.travel_peek('SR1') or {}
        self.assertEqual((message['kind'], message['target_pid'], message['target_save']), ('save_quit', 111, 'sr1-save'))
        self.assertEqual(self.coord._session.get()['realm'], 'SR2')
        plugin_reply(self.host, 'SR1', 'saved')
        self.assertEqual(self.coord.poll()[-1]['state'], 'CLOSING_SESSION', 'save is not proof of exit')
        self.launcher._running.discard('SR1')
        self.coord.poll()
        self.assertEqual(sum(1 for t in self.coord.transitions if t.get('code') == 'SESSION_CLOSED'), 1, 'asked once')
        self.assertEqual(self.coord._session.get()['realm'], 'SR2')

    def test_replacement_game_does_not_inherit_pending_save_and_close(self):
        self._crossed_to_sr2()
        self.launcher._running.discard('SR2')
        self.coord.poll()
        self.proc.alive('SR1', 'unrelated-save', 999)
        result = self.coord.poll()[-1]
        self.assertEqual((result['state'], result['code']), ('FAILED', 'SESSION_CLOSE_TARGET_CHANGED'))
        self.assertIsNone(self.host.travel_peek('SR1'))

    def test_failed_save_does_not_complete_coordinated_close(self):
        self._crossed_to_sr2()
        self.launcher._running.discard('SR2')
        self.coord.poll()
        plugin_reply(self.host, 'SR1', 'failed')
        result = self.coord.poll()[-1]
        self.assertEqual((result['state'], result['code']), ('FAILED', 'SESSION_SAVE_FAILED'))

    def test_alt_tab_neither_crosses_nor_changes_the_paired_close_target(self):
        done = self._crossed_to_sr2()
        self.windows.foreground_pid = lambda: 111
        self.assertEqual(self.coord.poll(), [done])
        self.clock.now += 5
        self.assertEqual(self.coord.poll(), [done])
        self.assertIsNone(self.host.travel_peek('SR1'))
        self.assertIsNone(self.host.travel_peek('SR2'))
        self.launcher._running.discard('SR2')
        self.assertEqual(self.coord.poll()[-1]['state'], 'CLOSING_SESSION')
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'save_quit')

    def test_continue_parks_the_partner_and_closing_the_front_closes_it(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        self.coord.park('SR1', 'SR2')
        self.coord.poll()
        self.assertIsNone(self.host.travel_peek('SR2'))
        self.launcher._running.discard('SR1')
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'close_prepare')

    def test_parked_game_waiting_for_heartbeat_checks_same_child_handle(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                self.proc.beats.pop('SR2', None)
                self.proc.alive('SR1', 'sr1-save', 111)
                self.launcher._running |= {'SR1', 'SR2'}
                children = {'SR2': SimpleNamespace(pid=222, poll=lambda: None)}
                self.launcher.owned_child = children.get
                self.launcher.current_pid = lambda realm: getattr(children.get(realm), 'pid', None)
                self.coord.park('SR1', 'SR2')
                if replaced:
                    children['SR2'] = SimpleNamespace(pid=222, poll=lambda: None)
                self.proc.alive('SR2', 'unrelated-save' if replaced else 'sr2-save', 222)
                self.coord.poll()
                self.launcher._running.remove('SR1')
                result = self.coord.poll()[-1]
                queued = self.host.travel_peek('SR2')
                if replaced:
                    self.assertEqual(result['code'], 'SESSION_CLOSE_TARGET_CHANGED')
                    self.assertIsNone(queued)
                else:
                    self.assertEqual((queued['kind'], queued['target_save']), ('close_prepare', 'sr2-save'))
                    plugin_reply(self.host, 'SR2', 'failed')
                    self.coord.poll()

    def _close_sr2(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        self.coord.park('SR1', 'SR2')
        self.launcher._running.remove('SR1')
        return self.coord.poll()[-1]

    def test_sr2_prepared_retry_after_expiry_never_grants_exit(self):
        row = self._close_sr2()
        msg = self.host.travel_peek('SR2')
        self.assertEqual(msg['kind'], 'close_prepare')
        event = {'protocol_version': 1, 'message_type': 'travel_event', 'realm': 'SR2',
                 'transition_id': row['id'], 'event': 'close_ready', 'ack_id': msg['message_id']}
        post(self.host, '/v1/travel/event', event, token=self.host.realm_token('SR2'))
        self.clock.now += 16
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'close_abort')
        post(self.host, '/v1/travel/event', event, token=self.host.realm_token('SR2'))
        self.assertTrue(self.coord.session_closing('SR2'), 'retain the guard until abort is confirmed')
        plugin_reply(self.host, 'SR2', 'close_aborted')
        self.assertEqual(self.coord.poll()[-1]['code'], 'SESSION_CLOSE_TIMEOUT')
        self.assertFalse(self.coord.session_closing('SR2'))

    def test_sr2_close_preempts_online_survives_history_and_requires_saved_and_exit(self):
        self.coord.online.host('SR2')
        self.coord.online.request['sent'].append('SR2')
        self.host.travel_enqueue('SR2', {'kind': 'online_host', 'transition_id': self.coord.online.request['id']})
        row = self._close_sr2()
        self.assertTrue(self.coord.online.request['done'])
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'close_prepare')
        for i in range(travel.KEEP + 3):
            self.coord._begin({'request_id': f'late{i}', 'portal_id': 'portal.sr2.return_portal', 'realm': 'SR2'})
        self.assertTrue(self.coord.session_closing('SR2'))
        plugin_reply(self.host, 'SR2', 'close_ready')
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'close_commit')
        plugin_reply(self.host, 'SR2', 'saved')
        self.coord.poll()
        self.assertTrue(self.coord.session_closing('SR2'))
        self.launcher._running.remove('SR2')
        rows = self.coord.poll()
        self.assertTrue(any(r['id'] == row['id'] and r['code'] == 'SESSION_CLOSED' for r in rows))

    def test_sr2_commit_receipt_cannot_be_resent_during_abort(self):
        row = self._close_sr2()
        plugin_reply(self.host, 'SR2', 'close_ready')
        self.coord.poll()
        msg = self.host.travel_peek('SR2')
        self.assertEqual(msg['kind'], 'close_commit')
        event = {'realm': 'SR2', 'transition_id': row['id'], 'event': 'saved', 'ack_id': msg['message_id']}
        self.assertTrue(self.host.travel_record(event))
        self.clock.now += 16
        self.coord.poll()
        self.assertFalse(self.host.travel_record(event), 'abort removes authority for a lost commit receipt')
        self.assertTrue(self.coord.session_closing('SR2'))
        plugin_reply(self.host, 'SR2', 'close_aborted')
        self.coord.poll()
        self.assertFalse(self.coord.session_closing('SR2'))

    def test_sr2_exit_during_abort_is_not_a_confirmed_coordinated_close(self):
        self._close_sr2()
        plugin_reply(self.host, 'SR2', 'close_ready')
        self.coord.poll()
        plugin_reply(self.host, 'SR2', 'saved')
        self.clock.now += 16
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'close_abort')
        self.launcher._running.remove('SR2')
        result = self.coord.poll()[-1]
        self.assertEqual((result['state'], result['code']), ('FAILED', 'SESSION_SAVE_UNCONFIRMED'))

    def test_late_saved_retry_cannot_authorize_quit_after_confirmed_abort(self):
        row = self._close_sr2()
        plugin_reply(self.host, 'SR2', 'close_ready')
        self.coord.poll()
        commit = plugin_reply(self.host, 'SR2', 'saved')
        self.clock.now += 16
        self.coord.poll()
        plugin_reply(self.host, 'SR2', 'close_aborted')
        self.coord.poll()
        self.assertFalse(self.host.travel_record({'realm': 'SR2', 'transition_id': row['id'],
                                                  'event': 'saved', 'ack_id': commit['message_id']}))

    def test_lost_host_ends_pending_status_without_releasing_close_guard(self):
        self._check_lost_host_close(self._close_sr2())

    def test_lost_host_after_commit_reply_loss_retains_close_guard(self):
        row = self._close_sr2()
        plugin_reply(self.host, 'SR2', 'close_ready')
        original = self.coord._send
        def lose_commit_response(realm, kind, transition, **extra):
            posted = original(realm, kind, transition, **extra)
            return False if kind == 'close_commit' else posted
        self.coord._send = lose_commit_response
        self.coord.poll()
        self.coord._send = original
        plugin_reply(self.host, 'SR2', 'saved')
        self._check_lost_host_close(row)

    def _check_lost_host_close(self, row):
        self.proc.child = None
        self.clock.now += 2
        result = next(r for r in self.coord.poll() if r['id'] == row['id'])
        self.assertEqual((result['state'], result['code']), ('FAILED', 'SESSION_CLOSE_HOST_LOST'))
        self.assertTrue(self.coord.session_closing('SR2'))
        self.proc.child = object()  # same apparent URL/PID, different owned host
        queued = self.host.travel_peek('SR2')
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR2'), queued, 'no replay/cancel across generations')
        self.launcher._running.remove('SR2')
        result = next(r for r in self.coord.poll() if r['id'] == row['id'])
        self.assertEqual(result['code'], 'SESSION_CLOSE_HOST_LOST', 'late exit cannot turn an uncertain close into success')
        self.assertFalse(self.coord.session_closing('SR2'))

    def test_a_lost_host_guards_a_live_game_only_for_a_bounded_time(self):
        row = self._close_sr2()
        plugin_reply(self.host, 'SR2', 'close_ready')
        self.proc.child = object()                                   # BridgeHost replaced; SR2 itself stays alive
        self.coord.poll()
        self.assertTrue(self.coord.session_closing('SR2'), 'a commit may still be in flight: guarded at first')
        self.clock.now += travel.CLOSE_HOST_LOST_GRACE + 1
        self.coord.poll()
        self.assertFalse(self.coord.session_closing('SR2'), "the game is the player's own again, not refused forever")
        result = next(r for r in self.coord.transitions if r['id'] == row['id'])
        self.assertEqual(result['code'], 'SESSION_CLOSE_HOST_LOST')

    def test_replacement_host_never_receives_commit_from_old_prepare(self):
        row = self._close_sr2()
        plugin_reply(self.host, 'SR2', 'close_ready')
        self.proc.child = object()
        result = next(r for r in self.coord.poll() if r['id'] == row['id'])
        self.assertEqual(result['code'], 'SESSION_CLOSE_HOST_LOST')
        self.assertTrue(self.coord.session_closing('SR2'))
        self.assertIsNone(self.host.travel_peek('SR2'))

    def test_crossing_while_native_auto_host_is_pending_is_not_queued(self):
        self.coord.online.host('SR2')
        self.coord.online.request['sent'].append('SR2')
        self.host.travel_enqueue('SR2', {'kind': 'online_host', 'transition_id': self.coord.online.request['id']})
        row = self.coord._begin({'request_id': 'portal', 'portal_id': 'portal.sr1.sanctuary_gate',
                                 'realm': 'SR1', 'save_id': 'ranch'})
        self.assertEqual((row['state'], row['code']), ('FAILED', 'ONLINE_REQUEST_PENDING'))
        self.assertIsNone(self.host.travel_peek('SR1'))
        self.assertEqual(self.host.travel_peek('SR2')['kind'], 'online_host')

    def test_a_new_slime_rancher_save_can_be_created_for_an_sr2_ranch(self):
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running.add('SR2')
        cross(self.host, rid='new', save='sr2-save', portal='portal.sr2.return_portal', realm='SR2')
        shown = self.coord.poll()[-1]
        self.assertEqual(shown['state'], 'NEEDS_LINK')
        self.assertTrue(any(c['group'] == '__NEW__' and c['new'] for c in shown['candidates']))
        self.assertEqual(self.coord.choose_link(shown['id'], '__NEW__'), {'ok': True})
        game, env = self.launcher.started[0]
        self.assertEqual(game, 'SR1')
        self.assertEqual(env['SLIMEBRIDGE_TRAVEL_NEW'], 'SlimeBridge sr2-save')
        self.assertNotIn('SLIMEBRIDGE_TRAVEL_SAVE', env)
        self.assertEqual(self.links.all(), {}, 'no link until the new save exists')
        plugin_reply(self.host, 'SR2', 'departure_ready')
        self.proc.alive('SR1', '20261015050000_SlimeBridgeSr2Save', 111)
        self.coord.poll()
        self.assertEqual(self.links.lookup('SR2', 'sr2-save'), '20261015050000_SlimeBridgeSr2Save')
        self.assertEqual(self.links.lookup('SR1', '20261015050000_SlimeBridgeSr2Save'), 'sr2-save')

    def test_no_new_save_is_offered_for_sr2_or_when_sr1_is_already_open(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        shown = self.coord.poll()[-1]
        self.assertFalse(any(c.get('new') for c in shown['candidates']), 'SR2 saves cannot be created by the portal yet')
        self.coord.choose_link(shown['id'], None)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running.add('SR2')
        cross(self.host, rid='again', save='x', portal='portal.sr2.return_portal', realm='SR2')
        # SR1 is not running here, so the offer exists; with SR1 open on some save it must not
        self.launcher._running.add('SR1')
        cross(self.host, rid='third', save='y', portal='portal.sr2.return_portal', realm='SR2')
        last = [t for t in self.coord.poll() if t['state'] == 'NEEDS_LINK']
        for transition in last:
            self.assertFalse(transition['to'] == 'SR1' and any(c.get('new') for c in transition['candidates']))

    def test_one_world_per_save_when_linking_again(self):
        self.links.remember('a', 'x')
        self.links.remember('b', 'x')
        self.assertEqual((self.links.lookup('SR1', 'a'), self.links.lookup('SR1', 'b'), self.links.lookup('SR2', 'x')), (None, 'x', 'b'))
        self.assertEqual(self.links.unlink('SR2', 'x'), 'b')
        self.assertEqual(self.links.all(), {})

    def test_refusals_are_terminal_states(self):
        cases = [('DESTINATION_UNAVAILABLE', lambda: self.installs.pop('SR2')),
                 ('NEEDS_BACKUP_CONFIRMATION', lambda: setattr(self, 'ack', False)),
                 ('LAUNCH_FAILED', lambda: setattr(self.launcher, 'fail', True))]
        for index, (code, arrange) in enumerate(cases):
            self.setUp() if index else None
            self.links.remember('sr1-save', 'sr2-save')
            arrange()
            cross(self.host, rid=f'case{index}')
            done = self.coord.poll()[-1]
            self.assertEqual((done['state'], done['code']), ('FAILED', code))
            self.assertEqual(self.launcher.started, [] if code != 'LAUNCH_FAILED' else [])
            self.tearDown()
        self.setUp()

    def test_unknown_portal_and_overlapping_crossing(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host, rid='a', portal='portal.elsewhere')
        self.assertEqual(self.coord.poll()[-1]['code'], 'UNKNOWN_PORTAL')
        cross(self.host, rid='b')
        self.assertEqual(self.state(), 'PREPARING')
        cross(self.host, rid='c', save='other')
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('FAILED', 'ANOTHER_CROSSING_ACTIVE'))

    def test_links_survive_in_a_file_and_ignore_blanks(self):
        self.links.remember('', 'x')
        self.links.remember('a', 'b')
        again = travel.TravelLinks(Path(self.tmp.name) / 'links.json')
        self.assertEqual((again.lookup('SR1', 'a'), again.lookup('SR2', 'b'), again.lookup('SR1', '')), ('b', 'a', None))


if __name__ == '__main__':
    unittest.main()


class AutoLinkTests(unittest.TestCase):
    """Default travel_link_mode AUTO: the first crossing of a save never waits for a dialog."""
    state = CoordinatorTests.state

    def setUp(self):
        CoordinatorTests.setUp(self)
        self.settings.clear()

    def test_no_partner_and_nothing_running_creates_a_new_save_over_there(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        shown = self.coord.poll()[-1]
        self.assertEqual(shown['state'], 'PREPARING')
        game, env = self.launcher.started[0]
        self.assertEqual((game, env['SLIMEBRIDGE_TRAVEL_NEW'], env['SLIMEBRIDGE_TRAVEL_MODE']), ('SR2', 'SlimeBridge sr1-save', 'DESTINATION'))
        self.assertNotIn('SLIMEBRIDGE_TRAVEL_SAVE', env)
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'prepare_departure')
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.proc.alive('SR2', 'sb-new-sr2', 222)
        self.launcher._running.add('SR2')
        self.coord.poll()
        self.assertEqual(self.links.lookup('SR1', 'sr1-save'), 'sb-new-sr2')
        self.assertEqual(self.state(), 'SWITCHING')

    def test_the_open_save_over_there_is_linked_without_asking(self):
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-solo', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        self.coord.poll()
        self.assertEqual(self.links.lookup('SR1', 'sr1-save'), 'sr2-solo')
        self.assertEqual(self.launcher.started, [])
        plugin_reply(self.host, 'SR1', 'departure_ready')
        self.assertEqual(self.state(), 'SWITCHING')

    def test_a_save_of_another_ranch_open_over_there_is_not_taken(self):
        self.links.remember('other-ranch', 'sr2-solo')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-solo', 222)
        self.launcher._running |= {'SR1', 'SR2'}
        cross(self.host)
        done = self.coord.poll()[-1]
        self.assertEqual((done['state'], done['code']), ('FAILED', 'DESTINATION_BUSY'))
        self.assertEqual(self.host.travel_peek('SR1')['reason'], 'DESTINATION_BUSY')
        self.assertEqual(self.links.lookup('SR2', 'sr2-solo'), 'other-ranch')


class LastSessionTests(unittest.TestCase):
    def test_remembers_the_game_and_save_and_keeps_the_save_when_only_the_game_is_known(self):
        import tempfile
        from apps.launcher.travel import LastSession
        with tempfile.TemporaryDirectory() as tmp:
            session = LastSession(Path(tmp) / 'last-session.json')
            self.assertIsNone(session.get())
            session.set('SR2', 'sr2-save')
            self.assertEqual((session.get()['realm'], session.get()['save']), ('SR2', 'sr2-save'))
            session.set('SR2', None)                       # a launch without a known save keeps the save it had
            self.assertEqual(session.get()['save'], 'sr2-save')
            session.set('SR1', None)
            self.assertEqual((session.get()['realm'], session.get()['save']), ('SR1', None))
            session.set('XX', 'nope')
            self.assertEqual(session.get()['realm'], 'SR1')

    def test_a_completed_crossing_marks_the_destination_as_where_the_player_is(self):
        case = CoordinatorTests('test_one_world_per_save_when_linking_again')
        self.addCleanup(case.doCleanups)
        case.setUp()
        import tempfile
        from apps.launcher.travel import LastSession
        with tempfile.TemporaryDirectory() as tmp:
            session = LastSession(Path(tmp) / 's.json')
            case.coord._session = session
            case.links.remember('sr1-save', 'sr2-save')
            case.proc.alive('SR1', 'sr1-save', 111)
            case.proc.alive('SR2', 'sr2-save', 222)
            case.launcher._running |= {'SR1', 'SR2'}
            cross(case.host)
            case.coord.poll()
            for realm, event in (('SR1', 'departure_ready'), ('SR2', 'arrived'), ('SR1', 'held')):
                plugin_reply(case.host, realm, event)
                case.coord.poll()
            self.assertEqual(case.coord.poll()[-1]['state'], 'COMPLETE')
            self.assertEqual(session.get()['realm'], 'SR2')
            self.assertEqual(session.get()['save'], 'sr2-save')


class MenuDestinationTests(CoordinatorTests):
    def test_a_destination_on_its_main_menu_is_restarted_on_the_linked_save(self):
        self.links.remember('sr1-save', 'sr2-linked')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.beats['SR2'] = {'pid': 222}                   # running, no save open (main menu)
        self.launcher._running |= {'SR1', 'SR2'}
        closed = []
        self.windows.request_close = lambda pid: closed.append(pid) or self.launcher._running.discard('SR2') or True
        cross(self.host)
        self.assertEqual(self.state(), 'PREPARING')
        self.assertEqual(closed, [222])
        game, env = self.launcher.started[0]
        self.assertEqual((game, env['SLIMEBRIDGE_TRAVEL_SAVE'], env['SLIMEBRIDGE_TRAVEL_MODE']), ('SR2', 'sr2-linked', 'DESTINATION'))

    def test_a_destination_still_loading_behind_the_portal_is_waited_for(self):
        self.links.remember('sr1-save', 'sr2-linked')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.beats['SR2'] = {'pid': 222}
        self.launcher._running |= {'SR1', 'SR2'}
        self.coord.note_waiting('SR2', 'sr2-linked')
        self.windows.request_close = lambda pid: self.fail('a loading game is never closed')
        cross(self.host)
        last = self.coord.poll()[-1]
        self.assertEqual(last['state'], 'PREPARING')
        self.assertEqual(self.launcher.started, [])
        self.assertEqual(self.coord.transitions[-1]['deadline'], travel.DEADLINES['PREPARING_COLD'])


class VacSyncTests(CoordinatorTests):
    def test_the_vac_of_the_game_played_last_moves_to_the_front_game_without_a_teleport(self):
        self.links.remember('sr1-save', 'sr2-save')
        self.launcher._running |= {'SR1', 'SR2'}
        self.coord.request_sync('SR1', 'SR2')                    # SR1 played last, SR2 started in front
        self.coord.poll()
        self.assertIsNone(self.host.travel_peek('SR1'), 'nothing before both saves are open')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.proc.alive('SR2', 'sr2-save', 222)
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR1')['kind'], 'prepare_departure')
        plugin_reply(self.host, 'SR1', 'departure_ready', carry={'MOSAIC_SLIME': 1, 'CARROT_VEGGIE': 4})
        self.coord.poll()
        arrive = self.host.travel_peek('SR2')
        self.assertEqual((arrive['kind'], arrive['reason'], arrive['carry']), ('arrive', 'sync', {'MOSAIC_SLIME': 1, 'CARROT_VEGGIE': 4}))
        plugin_reply(self.host, 'SR2', 'arrived', carry={'MOSAIC_SLIME': 1, 'CARROT_VEGGIE': 4})
        self.coord.poll()
        self.assertEqual(self.host.travel_peek('SR1')['carry'], {'MOSAIC_SLIME': 1, 'CARROT_VEGGIE': 4})
        plugin_reply(self.host, 'SR1', 'held', carry={'MOSAIC_SLIME': 1, 'CARROT_VEGGIE': 4})
        done = self.coord.poll()[-1]
        self.assertEqual(done['state'], 'COMPLETE')
        self.assertEqual(self.windows.calls, [], 'a vac sync never moves windows')
        self.assertEqual((self.coord._front, self.coord._parked), ('SR2', 'SR1'))
