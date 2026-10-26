"""Entry point of the standalone SlimeBridge.exe (PyInstaller build, see tools/release/build_exe.py).

A frozen application has no ``python -m``, yet the launcher starts its helpers that way (extraction worker, BridgeHost,
conversion jobs): it passes ``sys.executable`` -- here SlimeBridge.exe itself -- with ``-m <module> args``. This entry
point recognises that form and runs the module like the interpreter would; anything else opens the launcher window.
"""
import os
import runpy
import sys
import traceback
from pathlib import Path

ZONE_STREAM = ':Zone.Identifier'
# files .NET Framework or Windows may refuse to load once they carry the "downloaded from the Internet" mark
GUARDED = ('.dll', '.exe', '.pyd', '.config', '.manifest')


def clear_download_mark(root):
    """Windows tags every file extracted from a downloaded zip as coming from the Internet (Zone.Identifier stream).
    .NET Framework then refuses to load pythonnet's Python.Runtime.dll, so the launcher window cannot open
    ("Failed to resolve Python.Runtime.Loader.Initialize"). These are SlimeBridge's own files: drop the mark before
    anything loads a .NET assembly. Returns how many marks were removed."""
    if os.name != 'nt':
        return 0
    removed = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(GUARDED):
                try:
                    os.remove(os.path.join(dirpath, name) + ZONE_STREAM)
                    removed += 1
                except OSError:
                    pass
    return removed


def report_crash(exc):
    """Readable failure instead of PyInstaller's raw traceback box: a short message and a log next to the exe."""
    text = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log = Path(sys.executable).resolve().parent / 'SlimeBridge-crash.log'
    try:
        log.write_text(text, encoding='utf-8')
    except OSError:
        log = None
    message = (f'SlimeBridge could not start:\n\n{type(exc).__name__}: {exc}\n\n'
               + (f'Details were saved to:\n{log}\n\n' if log else '')
               + 'If the window stays white or does not open, install the Microsoft Edge WebView2 Runtime.')
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'SlimeBridge', 0x10)
            return
        except Exception:  # noqa: BLE001 - the message box itself must never mask the original error
            pass
    print(message, file=sys.stderr)


def main() -> None:
    if getattr(sys, 'frozen', False):
        clear_download_mark(Path(sys.executable).resolve().parent)
    if len(sys.argv) >= 3 and sys.argv[1] == '-m':
        module = sys.argv[2]
        sys.argv = [module] + sys.argv[3:]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
        return
    if len(sys.argv) >= 2 and sys.argv[1] == '--selfcheck':
        from apps.launcher import selfcheck
        sys.exit(0 if selfcheck.run() else 1)
    try:
        from apps.launcher import desktop
        desktop.main()
    except Exception as exc:  # noqa: BLE001 - last resort for the windowed exe
        report_crash(exc)
        sys.exit(1)


if __name__ == '__main__':
    main()
