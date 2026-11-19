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

    requires_focus_confirmation = True

    def __init__(self, verify=None):
        self._verify = verify or (lambda pid: False)
        if os.name != 'nt':
            raise WindowError('window control needs Windows')
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._user32.EnumWindows.argtypes = [self._enum_proc, wintypes.LPARAM]
        self._user32.EnumWindows.restype = wintypes.BOOL
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetWindow.restype = wintypes.HWND
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetClassNameW.restype = ctypes.c_int
        for name in ('IsWindowVisible', 'IsIconic', 'BringWindowToTop', 'SetForegroundWindow'):
            function = getattr(self._user32, name)
            function.argtypes = [wintypes.HWND]
            function.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindowAsync.restype = wintypes.BOOL
        self._user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.PostMessageW.restype = wintypes.BOOL
        self._user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self._user32.AttachThreadInput.restype = wintypes.BOOL
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def _windows_of(self, pid):
        ct, wt = self._ct, self._wt
        found = []

        def callback(hwnd, _lparam):
            owner = wt.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ct.byref(owner))
            if owner.value == pid and self._user32.IsWindowVisible(hwnd) or (owner.value == pid and self._user32.IsIconic(hwnd)):
                if not self._user32.GetWindow(hwnd, 4):                # GW_OWNER: top-level app windows only
                    found.append(hwnd)
            return True

        self._user32.EnumWindows(self._enum_proc(callback), 0)
        return found

    def _checked(self, pid):
        if not self._verify(pid):
            raise WindowError('refusing to touch a window of an unverified process')
        windows = self._windows_of(pid)
        if not windows:
            raise WindowError('the game has no visible window')
        return windows

    def _game_windows(self, pid):
        # MelonLoader has a top-level console in the same PID. It is not the playable Unity window.
        found = []
        for hwnd in self._checked(pid):
            kind = self._ct.create_unicode_buffer(256)
            if self._user32.GetClassNameW(hwnd, kind, len(kind)) and kind.value == 'UnityWndClass':
                found.append(hwnd)
        if not found:
            raise WindowError('the game has no Unity player window yet')
        return found

    def focus(self, pid):
        ct = self._ct
        for hwnd in self._game_windows(pid):
            if self._user32.IsIconic(hwnd):
                self._user32.ShowWindow(hwnd, SW_RESTORE)
            # Windows only lets the foreground thread hand focus over: attach to it for the call.
            foreground = self._user32.GetForegroundWindow()
            current = self._kernel32.GetCurrentThreadId()
            fg_thread = self._user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
            attached = False
            try:
                if fg_thread and fg_thread != current:
                    attached = bool(self._user32.AttachThreadInput(current, fg_thread, True))
                self._user32.BringWindowToTop(hwnd)
                self._user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    self._user32.AttachThreadInput(current, fg_thread, False)
            owner = self._wt.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ct.byref(owner))
            if self._verify(pid) and owner.value == pid and self._user32.GetForegroundWindow() == hwnd:
                return True
        return False

    def focus_startup(self, pid):
        """Best effort on a separate input queue; never restore a minimized front game."""
        for hwnd in self._game_windows(pid):
            if self._user32.IsIconic(hwnd):
                continue
            owner = self._wt.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, self._ct.byref(owner))
            if owner.value != pid or not self._verify(pid):
                continue
            # Startup holds the travel admission lock. Do not wait for a game to
            # restore or attach its input queue: cross-queue activation is asynchronous.
            if self._user32.GetForegroundWindow() != hwnd:
                self._user32.SetForegroundWindow(hwnd)
            if self._user32.GetForegroundWindow() == hwnd:
                return True
        return False

    def minimize(self, pid):
        windows = self._checked(pid)
        for hwnd in windows:
            # A 4-FPS game (or its same-PID loader console) can block a synchronous
            # ShowWindow for seconds. Queue it instead; success still needs IsIconic read-back.
            self._user32.ShowWindowAsync(hwnd, SW_MINIMIZE)
        return all(self._user32.IsIconic(hwnd) for hwnd in windows)

    def restore(self, pid):
        windows = self._game_windows(pid)
        for hwnd in windows:
            self._user32.ShowWindow(hwnd, SW_RESTORE)
        return all(not self._user32.IsIconic(hwnd) for hwnd in windows)

    def request_close(self, pid):
        # Never close MelonLoader's console instead of its Unity player, and never force-kill.
        accepted = True
        for hwnd in self._game_windows(pid):
            if not self._verify(pid):
                return False
            accepted = bool(self._user32.PostMessageW(hwnd, 0x0010, 0, 0)) and accepted  # WM_CLOSE
        return accepted

    def foreground_pid(self):
        hwnd = self._user32.GetForegroundWindow()
        owner = self._wt.DWORD()
        if not hwnd or not self._user32.GetWindowThreadProcessId(hwnd, self._ct.byref(owner)) or not self._verify(owner.value):
            return None
        try:
            return owner.value if hwnd in self._game_windows(owner.value) else None
        except WindowError:
            return None


class NullWindows:
    """No window control (non-Windows, or tests): crossings still run, the player switches windows by hand."""

    def focus(self, pid):
        return False

    def minimize(self, pid):
        return False

    def restore(self, pid):
        return False

    def request_close(self, pid):
        return False

    def foreground_pid(self):
        return None
