"""Screenshot one game window by process id, even when it is behind other windows (PrintWindow, full-content mode)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

PW_RENDERFULLCONTENT = 2


def _main_window(pid):
    user32 = ctypes.windll.user32
    found = []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            found.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd))
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return max(found)[1] if found else None


def capture(pid, path):
    """Save the window of ``pid`` to ``path``; returns the file name or why it could not."""
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = _main_window(pid)
    if not hwnd:
        return 'no window'
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return 'window minimized'
    hdc = user32.GetDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bitmap)
    try:
        user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT | 1)        # client area only
        buffer = ctypes.create_string_buffer(width * height * 4)
        header = (ctypes.c_uint32 * 10)(40, width, -height, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, header, 0)
        image = Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1).convert('RGB')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return Path(path).name
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(hwnd, hdc)
