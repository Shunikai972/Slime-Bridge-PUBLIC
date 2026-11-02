"""Read-back tests: all User32 calls are inert doubles; no real window is touched."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from apps.launcher.windows import Win32Windows, WindowError


def backend(*, accepts=False, raises=False):
    state = {'foreground': 77, 'attached': [], 'iconic': False, 'show_effect': True}
    def owner(hwnd, output):
        if output is not None:
            output._obj.value = 123 if hwnd == 88 else 999
        return 42
    def attach(current, foreground, enable):
        state['attached'].append(bool(enable))
        return True
    def focus(hwnd):
        if raises:
            raise OSError('synthetic focus fault')
        if accepts:
            state['foreground'] = hwnd
        return True  # Deliberately not proof of foreground ownership.
    def show(hwnd, command):
        if state['show_effect']:
            state['iconic'] = command == 6
        return True
    def window_class(hwnd, buffer, length):
        buffer.value = 'UnityWndClass' if hwnd == 88 else 'ConsoleWindowClass'
        return len(buffer.value)
    instance = object.__new__(Win32Windows)
    instance._verify = lambda pid: pid == 123
    instance._checked = lambda pid: [88]
    instance._ct, instance._wt = ctypes, wintypes
    instance._kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 31)
    instance._user32 = SimpleNamespace(GetForegroundWindow=lambda: state['foreground'],
        GetWindowThreadProcessId=owner, IsIconic=lambda hwnd: state['iconic'],
        AttachThreadInput=attach, BringWindowToTop=lambda hwnd: True,
        SetForegroundWindow=focus, ShowWindow=show, GetClassNameW=window_class)
    return instance, state


def test_focus_refuses_success_when_foreground_did_not_change():
    instance, state = backend()
    assert instance.focus(123) is False
    assert state['foreground'] == 77
    assert state['attached'] == [True, False]


def test_focus_accepts_only_confirmed_owned_foreground():
    instance, state = backend(accepts=True)
    assert instance.focus(123) is True
    assert state['foreground'] == 88


def test_focus_detaches_input_even_after_native_exception():
    instance, state = backend(raises=True)
    with pytest.raises(OSError):
        instance.focus(123)
    assert state['attached'] == [True, False]


def test_minimize_refuses_an_unapplied_showwindow_request():
    instance, state = backend()
    state['show_effect'] = False
    assert instance.minimize(123) is False


def test_restore_refuses_a_window_that_remained_minimized():
    instance, state = backend()
    state.update(iconic=True, show_effect=False)
    assert instance.restore(123) is False


def test_focus_selects_the_unity_player_not_its_loader_console():
    instance, state = backend(accepts=True)
    instance._checked = lambda pid: [99, 88]
    requested = []
    def focus(hwnd):
        requested.append(hwnd)
        state['foreground'] = hwnd
        return True
    instance._user32.SetForegroundWindow = focus
    assert instance.focus(123) is True
    assert requested == [88], 'Same-PID loader console is not the playable game window'


def test_restore_does_not_raise_the_loader_console():
    instance, state = backend()
    instance._checked = lambda pid: [99, 88]
    shown = []
    instance._user32.ShowWindow = lambda hwnd, command: shown.append(hwnd) or True
    assert instance.restore(123) is True
    assert shown == [88]


def test_console_without_a_unity_window_cannot_authorize_arrival():
    instance, state = backend(accepts=True)
    instance._checked = lambda pid: [99]
    with pytest.raises(WindowError, match='Unity'):
        instance.focus(123)
