"""Hybrid multiplayer: protocol, sessions, presence, chat, reconnect, realms, persistence, directory."""

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from bridge.multiplayer import persistence as P
from bridge.multiplayer.auth import DenyAll, DevTokenAuth
from bridge.multiplayer.directory import InMemoryDirectory, join_assessment, listing_from_server, parse_address, remember
from bridge.multiplayer.protocol import message, negotiate, validate
from bridge.multiplayer.server import BridgeServer, ServerConfig
from bridge.multiplayer.transport import Client, Network
from bridge.sim.faults import Fault

H1, H2 = 'a' * 64, 'b' * 64
TOKENS = {f'token-{name}-0123456789': name for name in ('alice', 'bob', 'cara', 'dan')}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def client(name, realm=None, content=None, **kw):
    return Client(name.title(), f'token-{name}-0123456789', content=content or {'SR1': H1, 'SR2': H2}, realm=realm, **kw)


class World:
    def __init__(self, mode='HYBRID', faults=(), store=None, **config):
        self.clock = Clock()
        config.setdefault('required_content', {'SR1': H1, 'SR2': H2})
        self.server = BridgeServer(ServerConfig(name='Test Ranch', mode=mode, **config),
                                   DevTokenAuth(TOKENS, development_only=True), clock=self.clock, store=store)
        self.net = Network(self.server, faults)

    def join(self, name, realm=None, **kw):
        c = self.net.join(client(name, realm, **kw))
        self.net.send(c, c.hello())
        return c


def tid():
    return uuid.uuid4().hex


class ProtocolTests(unittest.TestCase):
    def test_validation_bounds_and_direction(self):
        ok = message('CHAT', {'channel': 'GLOBAL', 'text': 'hi', 'to': None}, seq=1)
        self.assertEqual(validate(ok, direction='client'), [])
        self.assertTrue(validate(ok, direction='server'))
        for bad in (dict(ok, v=2), dict(ok, type='EVIL'), dict(ok, seq=-1), dict(ok, message_id='x'),
                    dict(ok, body={'channel': 'GLOBAL', 'text': 'x' * 501, 'to': None}),
                    dict(ok, body={'channel': 'GLOBAL', 'text': 'bad\x00text', 'to': None}),
                    dict(ok, body={'channel': 'GLOBAL', 'text': 'rtl‮override', 'to': None}),
                    dict(ok, body={'channel': 'GLOBAL', 'text': '   ', 'to': None}),
                    dict(ok, extra=1), 'not a dict', dict(ok, seq=True)):
            with self.subTest(bad=str(bad)[:60]):
                self.assertTrue(validate(bad, direction='client'))
        same = message('REALM_SWITCH', {'transition': {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR1',
                                                       'portal_id': None}, 'phase': 'BEGIN'}, seq=1)
        self.assertTrue(validate(same, direction='client'))
        self.assertEqual(negotiate([1, 5]), 1)
        self.assertIsNone(negotiate([7]))

    def test_auth_is_an_interface_without_production_defaults(self):
        self.assertIsNone(DenyAll().authenticate('dev-token', 'anything'))
        with self.assertRaises(ValueError):
            DevTokenAuth(TOKENS, development_only=False)
        with self.assertRaises(ValueError):
            DevTokenAuth({'short': 'x'}, development_only=True)
        auth = DevTokenAuth(TOKENS, development_only=True)
        self.assertIsNone(auth.authenticate('other-provider', 'token-alice-0123456789'))
        self.assertEqual(auth.authenticate('dev-token', 'token-alice-0123456789').player_id, 'alice')


class SessionTests(unittest.TestCase):
    def test_hybrid_presence_and_populations(self):
        w = World()
        alice, bob, cara = w.join('alice', 'SR1'), w.join('bob', 'SR2'), w.join('cara')
        self.assertEqual(alice.welcome['mode'], 'HYBRID')
        snap = w.server.snapshot()
        self.assertEqual((snap['realm_populations'], snap['lobby']), ({'SR1': 1, 'SR2': 1}, 1))
        self.assertEqual({p['player_id']: p['realm'] for p in alice.players},
                         {'alice': 'SR1', 'bob': 'SR2', 'cara': None})
        self.assertEqual(len(alice.received('CONTENT_MANIFEST')), 2)

    def test_handshake_failures(self):
        w = World(max_players=1)
        w.join('alice')
        intruder = w.net.join(Client('X', 'wrong-token-000000000', content={}))
        w.net.send(intruder, intruder.hello())
        self.assertEqual(intruder.rejections[-1]['code'], 'AUTH_FAILED')
        old = w.net.join(client('bob', versions=[9]))
        w.net.send(old, old.hello())
        self.assertEqual(old.rejections[-1]['code'], 'PROTOCOL_UNSUPPORTED')
        full = w.join('cara')
        self.assertEqual(full.rejections[-1]['code'], 'SERVER_FULL')
        anon = w.net.join(client('dan'))
        w.net.send(anon, anon.chat('hi'))
        self.assertEqual(anon.rejections[-1]['code'], 'NOT_AUTHENTICATED')

    def test_content_mismatch_restricts_realm_not_membership(self):
        w = World()
        c = w.join('alice', 'SR2', content={'SR1': H1, 'SR2': 'c' * 64})
        self.assertIsNone(c.realm)
        self.assertEqual(c.welcome['realms']['SR2'], {'allowed': False, 'reason': 'CONTENT_INCOMPATIBLE'})
        self.assertTrue(c.welcome['realms']['SR1']['allowed'])
        t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
        w.net.send(c, c.switch(dict(t, from_realm='SR2', to_realm='SR1'), 'BEGIN'))
        self.assertEqual(c.rejections[-1]['code'], 'WRONG_REALM')

    def test_session_replacement_and_grace_expiry(self):
        w = World(grace_seconds=30)
        first = w.join('alice', 'SR1')
        second = w.join('alice', 'SR1')
        self.assertEqual(first.received('BYE')[-1]['body']['reason'], 'replaced')
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(w.server.snapshot()['players'], 1)
        w.net.drop(second)
        self.assertEqual(w.server.snapshot()['player_list'][0]['status'], 'AWAY')
        w.clock.now += 29
        w.net.tick()
        self.assertEqual(w.server.snapshot()['players'], 1)
        w.clock.now += 1
        w.net.tick()
        self.assertEqual(w.server.snapshot()['players'], 0)


class ChatTests(unittest.TestCase):
    def test_channels_scope_and_wrong_realm(self):
        w = World()
        alice, bob, cara = w.join('alice', 'SR1'), w.join('bob', 'SR2'), w.join('cara', 'SR1')
        w.net.send(alice, alice.chat('hello everyone'))
        self.assertTrue(all(c.received('CHAT_EVENT') for c in (alice, bob, cara)))
        w.net.send(alice, alice.chat('sr1 only', channel='REALM'))
        self.assertEqual([m['body']['text'] for m in bob.received('CHAT_EVENT')], ['hello everyone'])
        self.assertEqual(cara.received('CHAT_EVENT')[-1]['body']['text'], 'sr1 only')
        w.net.send(alice, alice.chat('psst', channel='WHISPER', to='bob'))
        self.assertEqual(bob.received('CHAT_EVENT')[-1]['body']['channel'], 'WHISPER')
        self.assertNotIn('psst', [m['body']['text'] for m in cara.received('CHAT_EVENT')])
        w.net.send(alice, alice.chat('wrong realm', realm='SR2'))
        self.assertEqual(alice.rejections[-1]['code'], 'WRONG_REALM')
        w.net.send(alice, alice.chat('nobody', channel='WHISPER', to='ghost'))
        self.assertEqual(alice.rejections[-1]['code'], 'UNKNOWN_PLAYER')

    def test_duplicates_stale_idempotency_and_rate_limit(self):
        w = World(chat_per_window=3, chat_window_seconds=10)
        alice, bob = w.join('alice', 'SR1'), w.join('bob', 'SR1')
        msg = alice.chat('once', key='k1')
        w.net.send(alice, msg)
        w.net.send(alice, msg)  # exact duplicate: re-acked, no second event
        self.assertEqual([m['body']['text'] for m in bob.received('CHAT_EVENT')], ['once'])
        retry = alice.chat('once', key='k1')  # retry after reconnect with a new seq, same key
        w.net.send(alice, retry)
        self.assertEqual(len(bob.received('CHAT_EVENT')), 1)
        w.net.send(alice, alice.chat('different', key='k1'))
        self.assertEqual(alice.rejections[-1]['code'], 'DUPLICATE_KEY_CONFLICT')
        stale = dict(alice.chat('late'), seq=1)
        w.net.send(alice, stale)
        self.assertEqual(alice.rejections[-1]['code'], 'STALE_SEQUENCE')
        for i in range(3):
            w.net.send(alice, alice.chat(f'spam {i}'))
        self.assertEqual(alice.rejections[-1]['code'], 'RATE_LIMITED')
        w.clock.now += 10
        w.net.send(alice, alice.chat('later'))
        self.assertEqual(bob.received('CHAT_EVENT')[-1]['body']['text'], 'later')

    def test_wrong_session_envelope(self):
        w = World()
        alice, bob = w.join('alice', 'SR1'), w.join('bob', 'SR1')
        forged = dict(alice.chat('as bob'), session_id=bob.session_id, player_id='bob')
        w.net.send(alice, forged)
        self.assertEqual(alice.rejections[-1]['code'], 'WRONG_SESSION')
        self.assertEqual(bob.received('CHAT_EVENT'), [])


class ReconnectTests(unittest.TestCase):
    def test_lost_resumed_response_can_retry_the_previous_token(self):
        w = World()
        alice, bob = w.join('alice', 'SR1'), w.join('bob', 'SR1')
        previous_token, session_id = bob.resume_token, bob.session_id
        w.net.drop(bob)
        w.net.send(alice, alice.chat('while you were away'))
        rotated_token = None
        for _ in range(3):
            w.net.join(bob)
            # The server processes RESUME, but all replies (including RESUMED) are lost.
            replies = w.server.receive(bob.conn, bob.resume())
            resumed = [m for conn, m in replies if conn == bob.conn and m['type'] == 'RESUMED']
            self.assertTrue(resumed)  # Earlier unacknowledged RESUMED messages are replayed too.
            if rotated_token is None:
                rotated_token = resumed[-1]['body']['resume_token']
                self.assertNotEqual(rotated_token, previous_token)
            self.assertEqual(resumed[-1]['body']['resume_token'], rotated_token)
            self.assertEqual(bob.resume_token, previous_token)
            self.assertEqual(w.server.by_conn, {alice.conn: alice.session_id, bob.conn: session_id})
            w.net.drop(bob)
        w.net.reconnect(bob)
        self.assertEqual(bob.resume_token, rotated_token)
        self.assertEqual(bob.rejections, [])
        self.assertEqual([m['body']['text'] for m in bob.received('CHAT_EVENT')], ['while you were away'])
        self.assertEqual(w.server.snapshot()['players'], 2)
        w.net.drop(bob)
        w.net.reconnect(bob)  # Retrying the replacement token before confirmation is idempotent too.
        self.assertEqual(bob.resume_token, rotated_token)

    def test_first_authenticated_message_retires_the_previous_token(self):
        for confirmation in ('ACK', 'PING', 'CHAT', 'duplicate', 'idempotency_retry', 'stale', 'wrong_realm',
                             'handler_rejection'):
            with self.subTest(confirmation=confirmation):
                w = World()
                bob = w.join('bob', 'SR1')
                original = bob.chat('once', key='once')
                w.net.send(bob, original)
                previous_token = bob.resume_token
                w.net.drop(bob)
                w.net.reconnect(bob)
                current_token = bob.resume_token
                if confirmation == 'ACK':
                    confirm = bob.ack()
                elif confirmation == 'PING':
                    confirm = bob._next('PING', {'nonce': tid()})
                elif confirmation == 'duplicate':
                    confirm = original
                elif confirmation == 'idempotency_retry':
                    confirm = bob.chat('once', key='once')
                elif confirmation == 'stale':
                    confirm = dict(bob.chat('stale'), seq=1)
                elif confirmation == 'wrong_realm':
                    confirm = bob.chat('wrong realm', realm='SR2')
                elif confirmation == 'handler_rejection':
                    confirm = bob.chat('nobody', channel='WHISPER', to='ghost')
                else:
                    confirm = bob.chat('new message')
                w.net.send(bob, confirm)
                thief = w.net.join(client('bob'))
                thief.session_id, thief.resume_token = bob.session_id, previous_token
                w.net.send(thief, thief.resume())
                self.assertEqual([r['code'] for r in thief.rejections], ['RESUME_REJECTED'])
                self.assertEqual(w.server.by_conn, {bob.conn: bob.session_id})
                w.net.drop(bob)
                w.net.reconnect(bob)
                self.assertNotEqual(bob.resume_token, current_token)  # A confirmed rotation permits a new one.

    def test_unauthenticated_or_malformed_messages_do_not_confirm_resume(self):
        w = World()
        bob = w.join('bob', 'SR1')
        session_id, previous_token = bob.session_id, bob.resume_token
        twin = w.net.join(client('bob'))
        twin.session_id, twin.resume_token, twin.player_id, twin.seq = session_id, previous_token, 'bob', bob.seq
        w.net.send(twin, twin.resume())
        current_token = twin.resume_token
        for bad, code in ((dict(twin.chat('malformed'), body={}), 'MALFORMED'),
                          (dict(twin.chat('wrong session'), session_id=tid()), 'WRONG_SESSION'),
                          (dict(twin.chat('wrong player'), player_id='alice'), 'WRONG_SESSION'),
                          (twin.hello(), 'WRONG_SESSION'), (twin.resume(), 'WRONG_SESSION')):
            w.net.send(twin, bad)
            self.assertEqual(twin.rejections[-1]['code'], code)
        w.net.send(bob, bob.chat('from displaced connection'))
        self.assertEqual(bob.rejections[-1]['code'], 'NOT_AUTHENTICATED')
        stranger = w.net.join(client('alice'))
        w.net.send(stranger, message('PING', {'nonce': tid()}))
        self.assertEqual(len(stranger.received('PONG')), 1)
        stranger.session_id, stranger.resume_token = session_id, tid()
        w.net.send(stranger, stranger.resume())
        self.assertEqual(stranger.rejections[-1]['code'], 'RESUME_REJECTED')
        retry = w.net.join(client('bob'))
        retry.session_id, retry.resume_token = session_id, previous_token
        w.net.send(retry, retry.resume())
        self.assertEqual(retry.rejections, [])
        self.assertEqual(retry.resume_token, current_token)
        self.assertEqual(w.server.by_conn, {retry.conn: session_id})
        self.assertEqual(w.server.snapshot()['players'], 1)

    def test_session_end_discards_both_unconfirmed_resume_tokens(self):
        for ending in ('expiry', 'replacement', 'BYE'):
            with self.subTest(ending=ending):
                w = World(grace_seconds=30)
                bob = w.join('bob', 'SR1')
                session_id, previous_token = bob.session_id, bob.resume_token
                w.net.drop(bob)
                w.net.reconnect(bob)
                current_token = bob.resume_token
                if ending == 'expiry':
                    w.net.drop(bob)
                    w.clock.now += 30
                    w.net.tick()
                elif ending == 'replacement':
                    w.join('bob', 'SR1')
                else:
                    w.net.send(bob, bob.bye())
                self.assertNotIn(session_id, w.server.sessions)
                for token in (previous_token, current_token):
                    retry = w.net.join(client('bob'))
                    retry.session_id, retry.resume_token = session_id, token
                    w.net.send(retry, retry.resume())
                    self.assertEqual([r['code'] for r in retry.rejections], ['RESUME_REJECTED'])

    def test_resume_replays_missed_messages_and_rotates_token(self):
        w = World()
        alice, bob = w.join('alice', 'SR1'), w.join('bob', 'SR1')
        old_token = bob.resume_token
        w.net.drop(bob)
        w.net.send(alice, alice.chat('while you were away'))
        w.net.reconnect(bob)
        self.assertEqual(bob.received('RESUMED')[-1]['body']['replayed'] >= 1, True)
        self.assertEqual(bob.received('CHAT_EVENT')[-1]['body']['text'], 'while you were away')
        self.assertNotEqual(bob.resume_token, old_token)
        w.net.send(bob, bob.ack())  # Confirm RESUMED before the previous token is retired.
        thief = w.net.join(client('bob'))
        thief.session_id, thief.resume_token = bob.session_id, old_token
        w.net.send(thief, thief.resume())
        self.assertEqual(thief.rejections[-1]['code'], 'RESUME_REJECTED')

    def test_duplicate_reconnect_moves_the_session(self):
        w = World()
        bob = w.join('bob', 'SR2')
        twin = w.net.join(client('bob'))
        twin.session_id, twin.resume_token, twin.player_id = bob.session_id, bob.resume_token, 'bob'
        twin.seq = bob.seq
        w.net.send(twin, twin.resume())
        self.assertEqual(bob.received('BYE')[-1]['body']['reason'], 'resumed_elsewhere')
        w.net.send(twin, twin.chat('from the new connection'))
        self.assertEqual(twin.received('CHAT_EVENT')[-1]['body']['text'], 'from the new connection')

    def test_lost_and_duplicated_server_messages(self):
        w = World(faults=(Fault('net.duplicate_server', times=50),))
        alice, bob = w.join('alice', 'SR1'), w.join('bob', 'SR1')
        w.net.send(alice, alice.chat('dup-proof'))
        self.assertEqual([m['body']['text'] for m in bob.received('CHAT_EVENT')], ['dup-proof'])
        w2 = World(faults=(Fault('net.drop_server', on_hit=1, times=999),))
        carl = w2.join('cara', 'SR1')
        self.assertIsNone(carl.session_id)  # every server message lost: never assume a session


class RealmSwitchTests(unittest.TestCase):
    def test_two_phase_switch_and_modes(self):
        w = World()
        alice, bob = w.join('alice', 'SR1'), w.join('bob', 'SR2')
        t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': 'portal.sanctuary'}
        w.net.send(alice, alice.switch(t, 'BEGIN', key='switch-1'))
        self.assertEqual(next(p for p in bob.players if p['player_id'] == 'alice')['status'], 'IN_TRANSITION')
        w.net.send(alice, alice.switch(t, 'BEGIN', key='switch-1'))  # retried BEGIN: idempotent
        self.assertNotEqual(alice.rejections[-1:] and alice.rejections[-1]['code'], 'DUPLICATE_KEY_CONFLICT')
        w.net.send(alice, alice.switch(t, 'COMMIT'))
        self.assertEqual(w.server.snapshot()['realm_populations'], {'SR1': 0, 'SR2': 2})
        back = dict(t, transition_id=tid(), from_realm='SR2', to_realm='SR1')
        w.net.send(alice, alice.switch(back, 'BEGIN'))
        w.net.send(alice, alice.switch(back, 'ABORT'))
        self.assertEqual(alice.realm, 'SR2')
        solo = World(mode='SR1_ONLY')
        dan = solo.join('dan', 'SR1')
        w_t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
        solo.net.send(dan, dan.switch(w_t, 'BEGIN'))
        self.assertEqual(dan.rejections[-1]['code'], 'REALM_NOT_HOSTED')
        self.assertEqual(dan.welcome['realms'], {'SR1': {'allowed': True, 'reason': None}})


class PersistenceTests(unittest.TestCase):
    def test_migrations_upgrade_and_refuse_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'server.sqlite3'
            db = sqlite3.connect(path, isolation_level=None)
            self.assertEqual(P.migrate(db, target=1), [1])
            db.execute("INSERT INTO identities VALUES ('alice', 'p', 'Alice')")
            db.close()
            store = P.ServerStore(path)
            self.assertEqual((store.applied, store.version()), ([2, 3, 4, 5], P.LATEST))
            self.assertEqual(store.identity('alice'), {'bridge_profile_id': 'p', 'display_name': 'Alice',
                                                       'associations': 1})
            store.close()
            store = P.ServerStore(path)
            try:
                self.assertEqual(store.applied, [])
            finally:
                store.close()
            db = sqlite3.connect(path, isolation_level=None)
            db.execute('INSERT INTO schema_migrations VALUES (99, "future")')
            db.close()
            with self.assertRaises(P.MigrationError):
                P.ServerStore(path)

    def test_rejected_constructor_releases_database_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'server.sqlite3'
            store = P.ServerStore(path)
            try:
                store._db.execute('INSERT INTO schema_migrations VALUES (99, "future")')
            finally:
                store.close()
            # Keep the failed constructor's traceback alive: cleanup must not rely on GC.
            try:
                P.ServerStore(path)
            except P.MigrationError as error:
                path.unlink()  # Windows refuses this if any constructor connection is still open.
                self.assertIsNotNone(error.__traceback__)
            else:
                self.fail('a newer database must be refused')
            self.assertFalse(path.exists())

    def test_failed_migration_rolls_back_and_numbering_is_enforced(self):
        db = sqlite3.connect(':memory:', isolation_level=None)
        broken = P.MIGRATIONS[:1] + ((2, 'broken', ('CREATE TABLE ok_table (x)', 'NOT SQL')),)
        with self.assertRaises(sqlite3.OperationalError):
            P.migrate(db, migrations=broken)
        self.assertEqual(P.current_version(db), 1)
        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='ok_table'").fetchone())
        with self.assertRaises(P.MigrationError):
            P.migrate(db, migrations=((1, 'a', ()), (3, 'gap', ())))

    def test_server_persists_durable_data_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = P.ServerStore(Path(tmp) / 's.sqlite3')
            w = World(store=store)
            profile = {'bridge_profile_id': 'f' * 32, 'realm_saves': {'SR1': 'save-1'}}
            alice = w.join('alice', 'SR1', profile=profile)
            t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
            w.net.send(alice, alice.switch(t, 'BEGIN'))
            self.assertEqual(store.load_config()['name'], 'Test Ranch')
            self.assertEqual(store.identity('alice')['bridge_profile_id'], 'f' * 32)
            self.assertEqual([p['transition_id'] for p in store.pending_transitions()], [t['transition_id']])
            w.net.send(alice, alice.switch(t, 'COMMIT'))
            self.assertEqual(store.pending_transitions(), [])
            tables = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse({'presence', 'chat', 'sessions'} & tables)
            store.close()


class TransitionIntentBoundsTests(unittest.TestCase):
    """F6: stored realm-switch intents are one per player and end with the player's presence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = P.ServerStore(Path(self._tmp.name) / 's.sqlite3')
        self.addCleanup(self.store.close)

    def rows(self):
        return self.store._db.execute('SELECT COUNT(*) FROM transition_intents').fetchone()[0]

    def test_bye_expiry_and_aborts_leave_nothing_behind(self):
        w = World(store=self.store, grace_seconds=30)
        alice = w.join('alice', 'SR1')
        for _ in range(25):
            t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
            w.net.send(alice, alice.switch(t, 'BEGIN'))
            w.net.send(alice, alice.switch(t, 'ABORT'))
        self.assertEqual(self.rows(), 0)
        w.net.send(alice, alice.switch({'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2',
                                        'portal_id': None}, 'BEGIN'))
        w.net.send(alice, alice.bye())
        self.assertEqual(self.rows(), 0)
        bob = w.join('bob', 'SR2')
        w.net.send(bob, bob.switch({'transition_id': tid(), 'from_realm': 'SR2', 'to_realm': 'SR1',
                                    'portal_id': None}, 'BEGIN'))
        w.net.drop(bob)
        w.clock.now += 30
        w.net.tick()
        self.assertEqual(self.rows(), 0)

    def test_replacing_session_inherits_the_switch(self):
        w = World(store=self.store)
        first = w.join('cara', 'SR1')
        t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
        w.net.send(first, first.switch(t, 'BEGIN'))
        second = w.join('cara', 'SR1')
        self.assertEqual(first.received('BYE')[-1]['body']['reason'], 'replaced')
        self.assertEqual(second.welcome['pending_transition'], t)
        self.assertEqual(self.rows(), 1)

    def test_migration_keeps_only_the_latest_switch_and_startup_drops_malformed_ones(self):
        path = Path(self._tmp.name) / 'v4.sqlite3'
        db = sqlite3.connect(path, isolation_level=None)
        P.migrate(db, target=4)
        ids = [tid() for _ in range(3)]
        for transition_id in ids:
            doc = {'transition_id': transition_id, 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
            db.execute('INSERT INTO transition_intents VALUES (?,?,?)', ('alice', transition_id, P.canonical_json(doc)))
        db.execute("INSERT INTO transition_intents VALUES ('bob', 'x', '{not json')")
        db.execute("INSERT INTO transition_intents VALUES ('dan', 'y', '{\"transition_id\": \"y\"}')")
        db.close()
        store = P.ServerStore(path)
        self.addCleanup(store.close)
        self.assertEqual(store.applied, [5])
        self.assertEqual(store.pending_transition('alice')['transition_id'], ids[-1])
        store.save_config(ServerConfig(name='Old Ranch', mode='HYBRID').to_dict())
        server = BridgeServer.from_store(store, DevTokenAuth(TOKENS, development_only=True), clock=Clock())
        self.assertEqual([t['player_id'] for t in store.pending_transitions()], ['alice'])
        net = Network(server)
        bob = net.join(client('bob'))
        net.send(bob, bob.hello())
        self.assertIsNone(bob.welcome['pending_transition'])


class DirectoryTests(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual(parse_address('ranch.example'), ('ranch.example', 7777))
        self.assertEqual(parse_address('10.0.0.2:9000'), ('10.0.0.2', 9000))
        self.assertEqual(parse_address('[::1]:8080'), ('::1', 8080))
        for bad in ('', 'http://x', 'user@host', 'a b', 'host:0', 'host:70000', '::1', 'x/y', '-bad-.com', '[zz]:1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_address(bad)
        recents = remember(['a.example', 'b.example'], 'b.example')
        self.assertEqual(recents, ['b.example', 'a.example'])

    def test_listing_and_join_assessment(self):
        w = World()
        w.join('alice', 'SR1')
        listing = listing_from_server(w.server, server_id='local', address='127.0.0.1:7777')
        directory = InMemoryDirectory([listing, dict(listing, server_id='bad', mode='SR1_ONLY')])
        fetched = directory.fetch()
        self.assertEqual((len(fetched['listings']), fetched['rejected']), (1, 1))
        full_match = {'SR1': H1, 'SR2': H2}
        self.assertEqual(join_assessment(listing, local_content=full_match)['assessment'], 'JOINABLE')
        partial = join_assessment(listing, local_content={'SR1': H1})
        self.assertEqual((partial['assessment'], partial['realms']['SR2']), ('JOINABLE_RESTRICTED', 'CONTENT_MISMATCH'))
        self.assertEqual(join_assessment(listing, installed_realms=('SR2',), local_content=full_match)['realms']['SR1'],
                         'NOT_INSTALLED')
        self.assertEqual(join_assessment(listing, client_versions=(5,))['assessment'], 'INCOMPATIBLE_PROTOCOL')
        self.assertEqual(join_assessment(dict(listing, players=16), local_content=full_match)['assessment'], 'FULL')
        self.assertEqual(join_assessment(dict(listing, address='http://evil'))['assessment'], 'UNREACHABLE')


if __name__ == '__main__':
    unittest.main()


class RestartTests(unittest.TestCase):
    def test_unconfirmed_resume_tokens_are_ephemeral_but_switch_intents_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.sqlite3'
            store = P.ServerStore(path)
            try:
                w = World(store=store)
                bob = w.join('bob', 'SR1', profile={'bridge_profile_id': 'f' * 32, 'realm_saves': {'SR1': 'save-1'}})
                transition = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': None}
                w.net.send(bob, bob.switch(transition, 'BEGIN'))
                session_id, previous_token = bob.session_id, bob.resume_token
                w.net.drop(bob)
                w.net.reconnect(bob)
                current_token = bob.resume_token
                retry = w.net.join(client('bob'))
                retry.session_id, retry.resume_token = session_id, previous_token
                w.net.send(retry, retry.resume())
                self.assertEqual(retry.resume_token, current_token)
                self.assertEqual(w.server.sessions[session_id].transition, transition)
                self.assertEqual(store.pending_transition('bob'), transition)
                persisted = '\n'.join(store._db.iterdump())
                for ephemeral in (session_id, previous_token, current_token):
                    self.assertNotIn(ephemeral, persisted)
            finally:
                store.close()
            store = P.ServerStore(path)
            try:
                server = BridgeServer.from_store(store, DevTokenAuth(TOKENS, development_only=True), clock=Clock())
                self.assertEqual(server.sessions, {})
                net = Network(server)
                for token in (previous_token, current_token):
                    retry = net.join(client('bob'))
                    retry.session_id, retry.resume_token = session_id, token
                    net.send(retry, retry.resume())
                    self.assertEqual([r['code'] for r in retry.rejections], ['RESUME_REJECTED'])
                fresh = net.join(client('bob', 'SR1'))
                net.send(fresh, fresh.hello())
                self.assertNotEqual(fresh.session_id, session_id)
                self.assertEqual(fresh.welcome and fresh.welcome['pending_transition'], transition)
                self.assertEqual(server.sessions[fresh.session_id].status, 'IN_TRANSITION')
                # RESUME does not re-associate profiles; the fresh HELLO did not supply one either.
                self.assertEqual(store.identity('bob'), {'bridge_profile_id': 'f' * 32, 'display_name': 'Bob',
                                                        'associations': 1})
                net.send(fresh, fresh.switch(transition, 'COMMIT'))
                self.assertIsNone(store.pending_transition('bob'))
            finally:
                store.close()

    def test_server_restart_restores_config_and_in_flight_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = P.ServerStore(Path(tmp) / 's.sqlite3')
            w = World(store=store, max_players=5)
            alice = w.join('alice', 'SR1')
            t = {'transition_id': tid(), 'from_realm': 'SR1', 'to_realm': 'SR2', 'portal_id': 'portal.sanctuary'}
            w.net.send(alice, alice.switch(t, 'BEGIN'))
            store.close()
            store = P.ServerStore(Path(tmp) / 's.sqlite3')   # server process restarted
            server = BridgeServer.from_store(store, DevTokenAuth(TOKENS, development_only=True), clock=Clock())
            self.assertEqual((server.config.name, server.config.max_players), ('Test Ranch', 5))
            self.assertEqual(server.sessions, {})            # presence is never persisted
            net = Network(server)
            again = net.join(client('alice'))
            net.send(again, again.hello())
            self.assertEqual(again.welcome['pending_transition'], t)
            self.assertEqual(server.snapshot()['player_list'][0]['status'], 'IN_TRANSITION')
            net.send(again, again.switch(dict(t, from_realm='SR1'), 'BEGIN'))
            self.assertEqual(again.rejections[-1]['code'], 'DUPLICATE_KEY_CONFLICT')  # no second switch
            net.send(again, again.switch(t, 'COMMIT'))
            self.assertEqual(server.snapshot()['realm_populations'], {'SR1': 0, 'SR2': 1})
            self.assertEqual(store.pending_transitions(), [])
            store.close()
            empty = P.ServerStore(Path(tmp) / 'empty.sqlite3')
            with self.assertRaises(ValueError):
                BridgeServer.from_store(empty, DenyAll(), clock=Clock())
            empty.close()
