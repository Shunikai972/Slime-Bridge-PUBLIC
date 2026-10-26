"""Window control for the two running games during a crossing (Windows only, owned processes only).

Only windows that belong to a PID the launcher itself started (and whose image still is the verified game executable) are ever
touched: the destination comes to the front, the source is minimized. Nothing is sent to a window by name.
"""
from __future__ import annotations

import os

SW_MINIMIZE, SW_RESTORE, SW_SHOW = 6, 9, 5


class WindowError(RuntimeError):
    pass


class Win32Windows:
    """ctypes backend; ``verify(pid)`` must return True for a PID whose image is the expected game executable."""

    def __init__(self, verify=None):
        self._verify = verify or (lambda pid: True)
        if os.name != 'nt':
            raise WindowError('window control needs Windows')
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        self._user32 = ctypes.windll.user32

    def _windows_of(self, pid):
        ct, wt = self._ct, self._wt
        found = []
        proc = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def callback(hwnd, _lparam):
            owner = wt.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ct.byref(owner))
            if owner.value == pid and self._user32.IsWindowVisible(hwnd) or (owner.value == pid and self._user32.IsIconic(hwnd)):
                if self._user32.GetWindow(hwnd, 4) == 0:                  # GW_OWNER: top-level app windows only
                    found.append(hwnd)
            return True

        self._user32.EnumWindows(proc(callback), 0)
        return found

    def _checked(self, pid):
        if not self._verify(pid):
            raise WindowError('refusing to touch a window of an unverified process')
        windows = self._windows_of(pid)
        if not windows:
            raise WindowError('the game has no visible window')
        return windows

    def focus(self, pid):
        ct = self._ct
        for hwnd in self._checked(pid):
            if self._user32.IsIconic(hwnd):
                self._user32.ShowWindow(hwnd, SW_RESTORE)
            # Windows only lets the foreground thread hand focus over: attach to it for the call.
            foreground = self._user32.GetForegroundWindow()
            current = ct.windll.kernel32.GetCurrentThreadId()
            fg_thread = self._user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
            if fg_thread and fg_thread != current:
                self._user32.AttachThreadInput(current, fg_thread, True)
            self._user32.BringWindowToTop(hwnd)
            self._user32.SetForegroundWindow(hwnd)
            if fg_thread and fg_thread != current:
                self._user32.AttachThreadInput(current, fg_thread, False)
        return True

    def minimize(self, pid):
        for hwnd in self._checked(pid):
            self._user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True

    def restore(self, pid):
        for hwnd in self._checked(pid):
            self._user32.ShowWindow(hwnd, SW_RESTORE)
        return True


class NullWindows:
    """No window control (non-Windows, or tests): crossings still run, the player switches windows by hand."""

    def focus(self, pid):
        return False

    def minimize(self, pid):
        return False

    def restore(self, pid):
        return False
