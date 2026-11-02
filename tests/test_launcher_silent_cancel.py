"""A silent owned worker must remain cancellable without waiting for another stdout line."""
import threading
import subprocess
import sys
from pathlib import Path
import tempfile
from unittest.mock import patch

from apps.launcher import extract


def test_cancel_terminates_silent_owned_extraction_worker():
    entered, exited = threading.Event(), threading.Event()

    class SilentWorker:
        returncode = None
        stdout = None

        def __init__(self):
            self.stdout = self

        def __iter__(self):
            entered.set()
            assert exited.wait(10), 'synthetic worker cleanup timeout'
            return iter(())

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9
            exited.set()

        def terminate(self):
            self.kill()

        def wait(self, timeout=None):
            assert exited.wait(timeout or 10)
            return self.returncode

        def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix='silent-extract-') as tmp:
        job = extract.BuiltinExtractJob({'direction':'sr2-to-sr1'}, {'SR2':str(Path(tmp)/'SR2')}, Path(tmp)/'cache')
        worker = SilentWorker()
        errors = []

        def run():
            try:
                job._run_worker([], Path(tmp)/'SR2', lambda *_:None)
            except BaseException as error:
                errors.append(error)

        with patch.object(extract.subprocess, 'Popen', return_value=worker):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                assert entered.wait(10)
                job.cancel()
                thread.join(.5)
                stopped_by_cancel = not thread.is_alive()
            finally:
                worker.kill()
                thread.join(10)
        assert stopped_by_cancel, 'Cancel left a silent extraction process/thread running'
        assert len(errors)==1 and isinstance(errors[0],InterruptedError), errors


def test_cancel_reaps_a_real_silent_python_child():
    entered = threading.Event()
    children, errors = [], []
    real_popen = subprocess.Popen
    with tempfile.TemporaryDirectory(prefix='real-silent-extract-') as tmp:
        job = extract.BuiltinExtractJob({'direction':'sr2-to-sr1'}, {'SR2':str(Path(tmp)/'SR2')}, Path(tmp)/'cache')

        def start(_command, **kwargs):
            child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            children.append(child)
            entered.set()
            return child

        def run():
            try:
                job._run_worker([], Path(tmp)/'SR2', lambda *_:None)
            except BaseException as error:
                errors.append(error)

        with patch.object(extract.subprocess, 'Popen', side_effect=start):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                assert entered.wait(10)
                job.cancel()
                thread.join(3)
                stopped_by_cancel = not thread.is_alive()
                pipe_closed = children[0].stdout.closed
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=10)
                    child.stdout.close()
                thread.join(10)
        assert stopped_by_cancel, 'Real owned worker survived cancellation'
        assert pipe_closed, 'Owned worker stdout was not closed by the coordinator'
        assert len(children)==1 and children[0].returncode is not None
        assert len(errors)==1 and isinstance(errors[0],InterruptedError), errors
        assert job._proc is None
        assert not (job.cache_dir/'.extract-request.json').exists()
