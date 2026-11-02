"""Concurrent pywebview calls must not lose disjoint persisted preferences."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from apps.launcher.desktop import LauncherAPI


def test_concurrent_preference_updates_are_one_read_modify_write_transaction():
    with tempfile.TemporaryDirectory(prefix='launcher-config-race-') as tmp:
        api = LauncherAPI(config_path=Path(tmp)/'Launcher/config.json', known_paths=(), lab_games=Path(tmp)/'Games')
        first_loaded, second_entered, second_loaded, release_first = (threading.Event() for _ in range(4))
        original = api._load
        calls = 0
        count_lock = threading.Lock()

        def controlled_load():
            nonlocal calls
            with count_lock:
                calls += 1
                which = calls
            data = original()
            if which == 1:
                first_loaded.set()
                assert release_first.wait(10), 'test failed to release first writer'
            else:
                second_loaded.set()
            return data

        def second_writer():
            second_entered.set()
            return api.set_preference('language', 'fr')

        with patch.object(api, '_load', side_effect=controlled_load):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                one = pool.submit(api.set_preference, 'theme', 'dusk')
                assert first_loaded.wait(10)
                two = pool.submit(second_writer)
                assert second_entered.wait(10)
                # An unlocked implementation reads stale state immediately. A fixed transaction
                # waits for the first update; release it after this bounded overlap observation.
                second_loaded.wait(.5)
                release_first.set()
                assert one.result(timeout=10)['ok']
                assert two.result(timeout=10)['ok']
        data = json.loads(api._config_path.read_text(encoding='utf8'))
        assert (data['theme'], data['language']) == ('dusk', 'fr')
