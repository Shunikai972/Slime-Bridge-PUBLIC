"""Resolve a PID to its exact executable image so we never act on a process by name alone."""

import ctypes
import csv
import io
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_image_path(pid: int) -> Path | None:
    if sys.platform != 'win32':
        raise NotImplementedError('process_image_path is implemented for Windows only')
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).resolve()
    finally:
        kernel32.CloseHandle(handle)


def same_image(pid: int, expected: Path) -> bool:
    image = process_image_path(pid)
    return image is not None and str(image).casefold() == str(Path(expected).resolve()).casefold()


def other_game_processes(allowed_pids=()) -> dict[int, str]:
    """Enumerate game processes, including source copies, before touching shared saves.

    Tasklist is a Windows system tool; enumerate all rows rather than parsing a
    localized 'no tasks match' message. On enumeration failure, refuse the run.
    """
    if sys.platform != 'win32':
        raise RuntimeError('Current-user game process guard requires Windows')
    result = subprocess.run(['tasklist.exe', '/FO', 'CSV', '/NH'], capture_output=True, timeout=20)
    if result.returncode != 0:
        raise RuntimeError('Cannot enumerate game processes safely')
    text = result.stdout.decode('cp850', errors='replace')
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or any(len(row) < 2 for row in rows):
        raise RuntimeError('Cannot parse game process list safely')
    allowed = set(allowed_pids)
    found = {}
    for name, pid, *_rest in rows:
        if name.casefold() in ('slimerancher.exe', 'slimerancher2.exe'):
            try:
                number = int(pid.replace(',', ''))
            except ValueError as ex:
                raise RuntimeError('Cannot parse game process PID safely') from ex
            if number not in allowed:
                found[number] = name
    return found


def assert_no_other_game_processes(allowed_pids=()) -> None:
    others = other_game_processes(allowed_pids)
    if others:
        raise RuntimeError(f'Other game process(es) share these saves: {others}; no restore/launch')
