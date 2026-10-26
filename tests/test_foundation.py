import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from apps.launcher.installations import discover_manual, discover_stores
from bridge.host.server import BridgeHost
from bridge.transfer.store import TransferStore
from transfer_host_fixture import SyntheticTransferHost


class InstallationTests(unittest.TestCase):
    def test_manual_executable_and_folder_resolve_to_same_sr1_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / 'SlimeRancher.exe'
            exe.touch()
            (root / 'UnityPlayer.dll').touch()
            managed = root / 'SlimeRancher_Data' / 'Managed'
            managed.mkdir(parents=True)
            (managed / 'Assembly-CSharp.dll').touch()
            from_folder = discover_manual(root)
            from_exe = discover_manual(exe)
            self.assertEqual(from_folder, from_exe)
            self.assertEqual(from_folder.game, 'SR1')
            self.assertEqual(from_folder.store, 'manual')

    def test_wrong_executable_in_valid_game_folder_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'UnityCrashHandler64.exe'):
                (root / name).touch()
            (root / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            (root / 'SlimeRancher_Data' / 'Managed' / 'Assembly-CSharp.dll').touch()
            self.assertIsNone(discover_manual(root / 'UnityCrashHandler64.exe'))
            self.assertEqual(discover_manual(root / 'slimerancher.EXE').game, 'SR1')

    def test_manual_selection_keeps_store_provenance(self):
        from unittest.mock import patch
        from apps.launcher.main import available_installations
        from apps.launcher.installations import GameInstallation
        root = Path('C:/Games/SR1')
        installed = GameInstallation('SR1', root, root / 'SlimeRancher.exe', 'steam', 'mono')
        selected = GameInstallation('SR1', root, root / 'SlimeRancher.exe', 'manual', 'mono')
        with patch('apps.launcher.main.discover_stores', return_value=[installed]), \
             patch('apps.launcher.main.discover_manual', return_value=selected):
            self.assertEqual(available_installations([root]), [installed])

    def test_manual_wrong_game_or_missing_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'SlimeRancher2.exe').touch()
            self.assertIsNone(discover_manual(root))

    def test_store_candidates_require_actual_executable_and_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epic = root / 'Epic'
            epic.mkdir()
            (epic / 'bad.item').write_text(json.dumps({'DisplayName': 'Slime Rancher 2', 'InstallLocation': str(root / 'missing')}))
            self.assertEqual(discover_stores(steam_roots=[], epic_manifest_dir=epic), [])


class BridgeHostTests(unittest.TestCase):
    def test_status_write_never_overwrites_an_existing_temp_hardlink(self):
        import os
        from bridge.host.server import _write_status_file
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'unrelated-file'
            original.write_bytes(b'untouched')
            os.link(original, root / 'status.tmp')
            status = root / 'status.json'
            _write_status_file(status, {'url': 'http://127.0.0.1:1', 'heartbeats': {}})
            self.assertEqual(original.read_bytes(), b'untouched')
            self.assertEqual(json.loads(status.read_text())['heartbeats'], {})

    def test_authenticated_heartbeats_from_both_realms(self):
        with BridgeHost(token='local-test-token', port=0) as host:
            for realm in ('SR1', 'SR2'):
                payload = json.dumps({'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': realm + '-1', 'realm': realm}).encode()
                request = Request(host.url + '/v1/heartbeat', payload,
                                  {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
            self.assertEqual(set(host.heartbeats()), {'SR1', 'SR2'})

    def test_records_valid_pid_and_rejects_bad_optional_fields(self):
        from urllib.error import HTTPError
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'}
        with BridgeHost(token='local-test-token', port=0) as host:
            good = {'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': 'a', 'realm': 'SR2',
                    'pid': 4242, 'plugin_version': '0.1.0-dev'}
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(good).encode(), headers), timeout=3):
                pass
            beat = host.heartbeats()['SR2']
            self.assertEqual(beat['pid'], 4242)
            self.assertEqual(beat['plugin_version'], '0.1.0-dev')
            self.assertEqual(beat['count'], 1)
            first = beat['first_received_unix']
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**good, 'request_id': 'b'}).encode(), headers), timeout=3):
                pass
            beat = host.heartbeats()['SR2']
            self.assertEqual(beat['count'], 2)
            self.assertEqual(beat['first_received_unix'], first)
            self.assertGreaterEqual(beat['received_unix'], first)
            for bad in ({'pid': -1}, {'pid': True}, {'pid': '12'}, {'plugin_version': 'x' * 65}):
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**good, **bad}).encode(), headers), timeout=3)
                self.assertEqual(denied.exception.code, 400)
            self.assertEqual(host.heartbeats()['SR2']['count'], 2)
            # A new PID for the same realm is a restarted game: the session record starts over.
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**good, 'request_id': 'c', 'pid': 5151}).encode(), headers), timeout=3):
                pass
            beat = host.heartbeats()['SR2']
            self.assertEqual((beat['pid'], beat['count']), (5151, 1))
            self.assertGreaterEqual(beat['first_received_unix'], first)

    def test_loaded_game_status_can_leave_unverified_counts_unknown(self):
        from urllib.error import HTTPError
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'}
        status = {'player_alive': True, 'save_id': 'game_1'}
        base = {'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': 'unknown-counts',
                'realm': 'SR2', 'pid': 4242}
        with BridgeHost(token='local-test-token', port=0) as host:
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**base, 'status': status}).encode(), headers), timeout=3):
                pass
            self.assertEqual(host.heartbeats()['SR2']['status'], status)
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**base, 'status': {**status, 'pink_slime_count': None}}).encode(), headers), timeout=3)
            self.assertEqual(denied.exception.code, 400)
            denied.exception.close()

    def test_loaded_game_status_accepts_counts_but_rejects_invalid_shapes(self):
        from urllib.error import HTTPError
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'}
        status = {'player_alive': True, 'save_id': 'game_1', 'pink_slime_count': 2, 'pink_plort_count': 1}
        base = {'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': 'a',
                'realm': 'SR1', 'pid': 4242}
        with BridgeHost(token='local-test-token', port=0) as host:
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**base, 'status': status}).encode(), headers), timeout=3):
                pass
            self.assertEqual(host.heartbeats()['SR1']['status'], status)
            for partial in ({'player_alive': True, 'save_id': 'g', 'pink_slime_count': 1},
                            {'player_alive': True, 'save_id': 'g', 'pink_plort_count': 1}):
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(host.url + '/v1/heartbeat',
                                    json.dumps({**base, 'status': partial}).encode(), headers), timeout=3)
                self.assertEqual(denied.exception.code, 400)
            for invalid in ({'player_alive': 1}, {'save_id': ''}, {'pink_slime_count': True},
                            {'pink_plort_count': -1}, {'pink_slime_count': 1_000_001}):
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(host.url + '/v1/heartbeat',
                                    json.dumps({**base, 'status': {**status, **invalid}}).encode(), headers), timeout=3)
                self.assertEqual(denied.exception.code, 400)
            self.assertEqual(host.heartbeats()['SR1']['count'], 1)

    def test_heartbeats_returns_isolated_status_copy(self):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'}
        status = {'player_alive': True, 'save_id': 'game_orig'}
        base = {'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': 'copy-test',
                'realm': 'SR1', 'pid': 4242, 'status': status}
        with BridgeHost(token='local-test-token', port=0) as host:
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(base).encode(), headers), timeout=3):
                pass
            first = host.heartbeats()
            first['SR1']['status']['save_id'] = 'mutated'
            second = host.heartbeats()
            self.assertEqual(second['SR1']['status']['save_id'], 'game_orig')

    def test_stale_heartbeat_is_not_reported_connected(self):
        import time
        from bridge.host.server import HEARTBEAT_STALE_AFTER
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'}
        with BridgeHost(token='local-test-token', port=0) as host:
            good = {'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': 'start',
                    'realm': 'SR1', 'pid': 4242}
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(good).encode(), headers), timeout=3):
                pass
            self.assertIn('SR1', host.heartbeats())
            with host._lock:
                host._heartbeats['SR1']['received_monotonic'] = time.monotonic() - HEARTBEAT_STALE_AFTER - 1
            self.assertNotIn('SR1', host.heartbeats())
            with urlopen(Request(host.url + '/v1/heartbeat', json.dumps({**good, 'request_id': 'restart'}).encode(), headers), timeout=3):
                pass
            self.assertEqual(host.heartbeats()['SR1']['count'], 1)

    def test_rejects_unauthorized_and_wrong_protocol(self):
        from urllib.error import HTTPError
        with BridgeHost(token='local-test-token', port=0) as host:
            payload = json.dumps({'protocol_version': 1, 'message_type': 'heartbeat', 'request_id': 'x', 'realm': 'SR1'}).encode()
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(host.url + '/v1/heartbeat', payload), timeout=3)
            self.assertEqual(denied.exception.code, 401)
            payload = json.dumps({'protocol_version': 2, 'message_type': 'heartbeat', 'request_id': 'y', 'realm': 'SR1'}).encode()
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(host.url + '/v1/heartbeat', payload,
                        {'Authorization': 'Bearer local-test-token', 'Content-Type': 'application/json'}), timeout=3)
            self.assertEqual(denied.exception.code, 400)
            self.assertEqual(host.heartbeats(), {})

    def test_transfer_endpoints_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TransferStore(Path(tmp) / 'transfers.db') as store:
                with SyntheticTransferHost(tmp, store) as fixture:
                    host = fixture.host
                    tid = fixture.prepare(key='foundation-lifecycle', count=2)
                    req = Request(host.url + f'/v1/transfer/{tid}',
                                  headers={'Authorization': 'Bearer local-test-token'})
                    with urlopen(req, timeout=3) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertEqual(json.loads(resp.read())['transfer']['status'], 'PREPARED')

                    # Profile and both live endpoints exist, but lock still needs current query receipts.
                    with self.assertRaises(HTTPError) as missing_queries:
                        fixture.action('lock', tid)
                    self.assertEqual(missing_queries.exception.code, 409)
                    fixture.query_pair(tid, count=2)
                    self.assertEqual(fixture.action('lock', tid)['_status'], 200)

                    # The ledger phase alone cannot claim native source removal.
                    with self.assertRaises(HTTPError) as missing_remove:
                        fixture.action('escrow', tid)
                    self.assertEqual(missing_remove.exception.code, 409)
                    fixture.remove_receipt(tid, count=2, before=5)
                    self.assertEqual(fixture.action('escrow', tid)['_status'], 200)

                    # Nor can escrow alone claim native receiver delivery.
                    with self.assertRaises(HTTPError) as missing_add:
                        fixture.action('commit', tid)
                    self.assertEqual(missing_add.exception.code, 409)
                    fixture.add_receipt(tid, count=2, before=0)
                    self.assertEqual(fixture.action('commit', tid)['_status'], 200)
                    req = Request(host.url + f'/v1/transfer/{tid}',
                                  headers={'Authorization': 'Bearer local-test-token'})
                    with urlopen(req, timeout=3) as resp:
                        self.assertEqual(json.loads(resp.read())['transfer']['status'], 'COMMITTED')

    def test_transfer_endpoints_abort_refund_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TransferStore(Path(tmp) / 'transfers.db') as store:
                with SyntheticTransferHost(tmp, store) as fixture:
                    host = fixture.host
                    tid1 = fixture.prepare(key='foundation-abort')
                    self.assertEqual(fixture.action('abort', tid1, reason='cancelled')['_status'], 200)

                    # This is a distinct logical operation, not a retry of the aborted request.
                    tid2 = fixture.prepare(key='foundation-refund')
                    fixture.query_pair(tid2)
                    fixture.action('lock', tid2)
                    with self.assertRaises(HTTPError) as missing_remove:
                        fixture.action('escrow', tid2)
                    self.assertEqual(missing_remove.exception.code, 409)
                    fixture.remove_receipt(tid2)
                    fixture.action('escrow', tid2)

                    # A controller cannot finalize a refund before its durable decision.
                    with self.assertRaises(HTTPError) as unfenced:
                        fixture.action('refund', tid2, reason='dropped')
                    self.assertEqual(unfenced.exception.code, 409)
                    store.begin_refund(tid2, 'fixture: no receiver command dispatched')
                    with self.assertRaises(HTTPError) as missing_refund:
                        fixture.action('refund', tid2, reason='dropped')
                    self.assertEqual(missing_refund.exception.code, 409)
                    fixture.refund_receipt(tid2)
                    self.assertEqual(fixture.action('refund', tid2, reason='dropped')['_status'], 200)

                    prep_body = {'bridge_profile_id': fixture.profile_id, 'sender_realm': 'SR1',
                                 'sender_save_id': 'a', 'receiver_realm': 'SR2',
                                 'receiver_save_id': 'b', 'canonical_item_id': 'slime.pink',
                                 'count': 1, 'idempotency_key': 'foundation-unauthorized'}
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(Request(host.url + '/v1/transfer/prepare', json.dumps(prep_body).encode(),
                                        {'Content-Type': 'application/json'}), timeout=3)
                    self.assertEqual(denied.exception.code, 401)

        # When transfer_store is None -> 503; the service still refuses even a well-formed request.
        with BridgeHost(token='local-test-token', port=0) as host:
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'}
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(host.url + '/v1/transfer/prepare', json.dumps(prep_body).encode(), headers), timeout=3)
            self.assertEqual(denied.exception.code, 503)


if __name__ == '__main__':
    unittest.main()
