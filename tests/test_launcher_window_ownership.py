"""Window-policy tests never focus, minimize, enumerate or send input to a real window."""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.launcher import desktop, runtime, windows
from tools.lab import process_guard

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Win32 callback and ABI gate')


class Child:
    pid=424242
    alive=True

    def poll(self):
        return None if self.alive else 0


def policy(tmp_path):
    child=Child()
    launcher=runtime.GameLauncher(popen=lambda *_a,**_k:child)
    root=tmp_path/'owned'
    root.mkdir()
    executable=root/'SlimeRancher2.exe'
    executable.write_bytes(b'synthetic executable, never launched')
    launcher.launch({'game':'SR2','root':str(root)})
    api=object.__new__(desktop.LauncherAPI)
    api._launcher=launcher
    return api,child,executable


def allowed(api,pid,image):
    with patch.object(process_guard,'process_image_path',return_value=image), \
            patch.object(desktop.windows,'Win32Windows',side_effect=lambda verify:SimpleNamespace(verify=verify)):
        return api._window_control().verify(pid)


def test_window_policy_accepts_the_live_owned_exact_image(tmp_path):
    api,child,image=policy(tmp_path)
    assert allowed(api,child.pid,image)


def test_window_policy_refuses_foreign_same_basename(tmp_path):
    api,child,_=policy(tmp_path)
    assert not allowed(api,child.pid,tmp_path/'foreign'/'SlimeRancher2.exe')


def test_window_policy_refuses_unowned_pid_even_with_the_exact_image(tmp_path):
    api,child,image=policy(tmp_path)
    assert not allowed(api,child.pid+1,image)


def test_window_policy_refuses_an_exited_child_and_missing_image(tmp_path):
    api,child,image=policy(tmp_path)
    child.alive=False
    assert not allowed(api,child.pid,image)
    assert not allowed(api,child.pid,None)


def test_native_backend_defaults_to_no_window_authority():
    backend=windows.Win32Windows()
    with patch.object(backend,'_windows_of',return_value=[123]) as lookup:
        with pytest.raises(windows.WindowError):
            backend._checked(424242)
    lookup.assert_not_called()


def test_native_handle_return_values_and_arguments_are_pointer_sized():
    backend=windows.Win32Windows(verify=lambda _pid:False)
    user=backend._user32
    assert user.GetForegroundWindow.restype is W.HWND
    assert user.GetWindow.restype is W.HWND
    assert user.GetWindow.argtypes==[W.HWND,W.UINT]
    assert user.GetWindowThreadProcessId.argtypes==[W.HWND,ctypes.POINTER(W.DWORD)]
    assert user.SetForegroundWindow.argtypes==[W.HWND]


def test_owned_top_level_window_with_null_owner_is_enumerated():
    backend=windows.Win32Windows(verify=lambda _pid:True)
    handle=0x100000001

    def owner(hwnd,pointer):
        pointer._obj.value=424242
        return 123

    backend._user32=SimpleNamespace(EnumWindows=lambda callback,_:callback(handle,0),GetWindowThreadProcessId=owner,
                                   IsWindowVisible=lambda _:True,IsIconic=lambda _:False,GetWindow=lambda *_:None)
    assert backend._windows_of(424242)==[handle]
