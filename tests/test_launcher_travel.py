"""The launcher-side crossing coordinator against a real in-process BridgeHost and simulated realm plugins."""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from apps.launcher import travel
from bridge.host.server import BridgeHost

TOKEN = 'travel-coordinator-token'


class FakeHostProcess:
    def __init__(self, host):
        self.host, self.beats = host, {}

    def state(self):
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
        self.host = BridgeHost(token=TOKEN).__enter__()
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

    def tearDown(self):
        self.host.__exit__(None, None, None)
        self.tmp.cleanup()

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
        self.assertIn(('minimize', 111), self.windows.calls)
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
        plugin_reply(self.host, 'SR1', 'departure_ready', carry={'CARROT_VEGGIE': 3, 'PINK_PLORT': 2})
        self.coord.poll()
        arrive = self.host.travel_peek('SR2')
        self.assertEqual((arrive['kind'], arrive['carry']), ('arrive', {'CARROT_VEGGIE': 3, 'PINK_PLORT': 2}))
        plugin_reply(self.host, 'SR2', 'arrived', carry={'CARROT_VEGGIE': 3})        # the vac was full for the plorts
        self.coord.poll()
        hold = self.host.travel_peek('SR1')
        self.assertEqual((hold['kind'], hold['carry']), ('hold', {'CARROT_VEGGIE': 3}), 'only what was accepted leaves the source')
        plugin_reply(self.host, 'SR1', 'held', carry={'CARROT_VEGGIE': 3})
        done = self.coord.poll()[-1]
        self.assertEqual(done['state'], 'COMPLETE')
        self.assertTrue(any('CARROT_VEGGIE x3' in h['text'] for h in done['history']))

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

    def test_a_vanished_linked_save_drops_the_link_and_asks_again(self):
        self.links.remember('sr1-save', 'sr2-deleted')
        self.proc.alive('SR1', 'sr1-save', 111)
        self.launcher._running.add('SR1')
        cross(self.host)
        self.assertEqual(self.coord.poll()[-1]['state'], 'NEEDS_LINK')
        self.assertIsNone(self.links.lookup('SR1', 'sr1-save'))

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
