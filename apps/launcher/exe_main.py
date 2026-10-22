"""Entry point of the standalone SlimeBridge.exe (PyInstaller build, see tools/release/build_exe.py).

A frozen application has no ``python -m``, yet the launcher starts its helpers that way (extraction worker, BridgeHost,
conversion jobs): it passes ``sys.executable`` -- here SlimeBridge.exe itself -- with ``-m <module> args``. This entry
point recognises that form and runs the module like the interpreter would; anything else opens the launcher window.
"""
import runpy
import sys


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == '-m':
        module = sys.argv[2]
        sys.argv = [module] + sys.argv[3:]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
        return
    if len(sys.argv) >= 2 and sys.argv[1] == '--selfcheck':
        from apps.launcher import selfcheck
        sys.exit(0 if selfcheck.run() else 1)
    from apps.launcher import desktop
    desktop.main()


if __name__ == '__main__':
    main()
