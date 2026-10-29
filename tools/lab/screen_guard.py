"""Keep the player's Unity display settings (fullscreen mode, resolution) across a lab run that starts the games windowed.

Unity remembers the last window mode in HKCU\\Software\\Monomi Park\\<game> ("Screenmanager ..." values): a run started with
-screen-fullscreen 0 would otherwise leave the player's game windowed afterwards.
"""
from __future__ import annotations

import winreg

KEYS = {'SR1': r'Software\Monomi Park\Slime Rancher', 'SR2': r'Software\Monomi Park\Slime Rancher 2'}


def snapshot(realms=('SR1', 'SR2')):
    saved = {}
    for realm in realms:
        values = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEYS[realm]) as key:
                index = 0
                while True:
                    try:
                        name, value, kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    if name.startswith('Screenmanager'):
                        values[name] = (value, kind)
                    index += 1
        except OSError:
            pass
        saved[realm] = values
    return saved


def restore(saved):
    restored = {}
    for realm, values in saved.items():
        if not values:
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEYS[realm], 0, winreg.KEY_SET_VALUE) as key:
                for name, (value, kind) in values.items():
                    winreg.SetValueEx(key, name, 0, kind, value)
            restored[realm] = len(values)
        except OSError as exc:
            restored[realm] = f'failed: {exc}'
    return restored
