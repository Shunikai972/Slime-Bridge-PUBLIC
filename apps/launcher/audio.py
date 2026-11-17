"""Mute only verified owned game sessions in the Windows mixer, never the master endpoint.

All COM objects stay on the enumerating thread. Original per-session mute values are restored
on foreground handoff/shutdown; a pre-existing user mute is never undone. New sessions and
new output devices are discovered even before a game sends its first plugin heartbeat.
"""
from contextlib import contextmanager
import os
import threading


@contextmanager
def windows_sessions():
    if os.name != 'nt':
        yield []
        return
    from comtypes import CoInitialize, CoUninitialize
    from pycaw.api.audiopolicy import IAudioSessionControl2
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities
    from pycaw.utils import AudioSession
    CoInitialize()
    sessions, devices = [], []
    try:
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        for device in devices:
            enumerator = device.AudioSessionManager.GetSessionEnumerator()
            for index in range(enumerator.GetCount()):
                control = enumerator.GetSession(index)
                sessions.append(AudioSession(control.QueryInterface(IAudioSessionControl2)))
        yield sessions
    finally:
        # No interface pointers are cached by GameAudio or used by another thread.
        sessions.clear()
        devices.clear()
        enumerator = control = device = None
        CoUninitialize()


class GameAudio:
    def __init__(self, verify, *, sessions=windows_sessions):
        self._verify, self._sessions = verify, sessions
        self._muted = set()
        self._original = {}
        self._lock = threading.RLock()
        self.last_status = {'muted': 0, 'errors': []}

    def set_muted(self, pid, muted):
        if type(pid) is not int or pid <= 0:
            return
        with self._lock:
            if muted:
                self._muted.add(pid)
            else:
                self._muted.discard(pid)

    def _apply(self, sessions):
        result = {'muted': 0, 'errors': []}
        for session in sessions:
            try:
                pid = session.ProcessId
                if pid <= 0 or not self._verify(pid):
                    continue
                key = (pid, session.InstanceIdentifier)
                if pid not in self._muted and key not in self._original:
                    continue
                volume = session.SimpleAudioVolume
                before = bool(volume.GetMute())
                if pid in self._muted:
                    self._original.setdefault(key, before)
                    wanted = True
                else:
                    wanted = self._original[key]
                if not self._verify(pid):
                    continue
                if before != wanted:
                    volume.SetMute(wanted, None)
                if bool(volume.GetMute()) != wanted:
                    raise OSError('Windows session mute readback disagrees')
                if pid in self._muted:
                    result['muted'] += 1
                else:
                    del self._original[key]
            except Exception as exc:  # a disappearing session must not skip the other game
                result['errors'].append(type(exc).__name__)
        return result

    def sync(self):
        with self._lock:
            # A dead child/PID reuse is never a reason to touch its replacement process.
            self._muted = {pid for pid in self._muted if self._verify(pid)}
            self._original = {key: value for key, value in self._original.items() if self._verify(key[0])}
            if not self._muted and not self._original:
                self.last_status = {'muted': 0, 'errors': []}
                return self.last_status
            try:
                with self._sessions() as sessions:
                    result = self._apply(sessions)
            except Exception as exc:
                result = {'muted': 0, 'errors': [type(exc).__name__]}
            self.last_status = result
            return result

    def restore(self):
        with self._lock:
            self._muted.clear()
            return self.sync()
