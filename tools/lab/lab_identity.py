"""Fail-closed identity and write-access gate for real Unity lab launches.

A copied game shares Unity's LocalLow/HKCU identity with its parent process. Running the game under
this account is unsafe even if the executable lives in Lab/Games. The local, ignored configuration
must describe a separate Windows account whose profile is loaded. Open original paths with write
access requested but never write a byte: if the kernel grants it, the lab account is not isolated.
"""

import ctypes
import json
import os
import re
import stat
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from tools.lab.paths import LAB_GAMES, REPO_ROOT, refuse_reparse

ISOLATION_CONFIG = REPO_ROOT / 'Lab' / 'Profiles' / 'isolation.json'
SAVE_RELATIVE = {
    'SR1': Path('Monomi Park') / 'Slime Rancher',
    'SR2': Path('MonomiPark') / 'SlimeRancher2',
}


def _current_identity() -> tuple[str, Path]:
    if sys.platform != 'win32':
        raise RuntimeError('Isolated game launches are implemented for Windows only')
    script = ("$i=[Security.Principal.WindowsIdentity]::GetCurrent(); "
              "@{sid=$i.User.Value;profile=[Environment]::GetFolderPath('UserProfile')} | ConvertTo-Json -Compress")
    output = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                     text=True, timeout=10)
    data = json.loads(output)
    return data['sid'], Path(data['profile']).resolve()


def _profile_for_sid(sid: str) -> Path:
    """Resolve the OS-registered profile instead of trusting a path from isolation.json."""
    if sys.platform != 'win32':
        raise RuntimeError('Windows profile registration is required')
    import winreg
    try:
        key_path = '\\'.join(('SOFTWARE', 'Microsoft', 'Windows NT', 'CurrentVersion', 'ProfileList', sid))
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, kind = winreg.QueryValueEx(key, 'ProfileImagePath')
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
            raise ValueError('Invalid profile path type')
        return Path(os.path.expandvars(value)).resolve()
    except (OSError, ValueError) as exc:
        raise RuntimeError('Unable to check Windows profile registration') from exc


def _path_owner_sid(path: Path) -> str:
    """Resolve a filesystem owner's SID via Get-Acl; pass the path as data, not shell code."""
    if sys.platform != 'win32':
        raise RuntimeError('Windows owner checks are required')
    script = ("$p=[Console]::In.ReadLine(); "
              "(Get-Acl -LiteralPath $p).GetOwner([Security.Principal.SecurityIdentifier]).Value")
    try:
        output = subprocess.check_output(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            input=str(path) + '\n', text=True, stderr=subprocess.PIPE, timeout=10).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Unable to verify owner of original path: {path}') from exc
    if not re.fullmatch(r'S-\d+(?:-\d+)+', output):
        raise RuntimeError(f'Unable to verify owner of original path: {path}')
    return output


def _try_access(path: Path, mask: int) -> bool:
    """Ask Windows for one destructive right without modifying the target."""
    if sys.platform != 'win32':
        raise RuntimeError('Windows access checks are required')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    # BACKUP_SEMANTICS permits opening directories; it does not enable backup privilege.
    flags = 0x02000000 if path.is_dir() else 0
    handle = kernel.CreateFileW(str(path), mask, 0x7, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error == 5:  # Only explicit ACCESS_DENIED proves this right unavailable.
            return False
        raise RuntimeError(f'Unable to verify original access (Win32 error {error}): {path}')
    kernel.CloseHandle(handle)
    return True


def _can_write(path: Path) -> bool:
    """Probe each right separately: a single multi-right request could mask a granted right."""
    # FILE_WRITE_DATA/ADD_FILE, APPEND_DATA/ADD_SUBDIRECTORY, WRITE_EA,
    # DELETE_CHILD (directory), WRITE_ATTRIBUTES, DELETE, WRITE_DAC, WRITE_OWNER.
    rights = [0x00000002, 0x00000004, 0x00000010, 0x00000100,
              0x00010000, 0x00040000, 0x00080000]
    if path.is_dir():
        rights.append(0x00000040)
    writable = False
    for mask in rights:
        writable |= _try_access(path, mask)
    return writable


def _tree_paths(root: Path):
    """Visit files and directories, refusing inaccessible or redirected subtrees."""
    refuse_reparse(root)
    if not root.is_dir():
        raise RuntimeError(f'Original tree is not accessible: {root}')
    yield root
    def on_error(exc):
        raise RuntimeError(f'Unable to enumerate original tree: {exc.filename}') from exc
    for base, dirs, files in os.walk(root, onerror=on_error, followlinks=False):
        for name in (*dirs, *files):
            child = Path(base) / name
            refuse_reparse(child)
            yield child


def _existing_parent(path: Path) -> Path:
    while True:
        try:
            info = path.stat()
        except FileNotFoundError:
            if path == path.parent:
                raise RuntimeError('No existing source profile ancestor for access check')
            path = path.parent
        except OSError as exc:
            raise RuntimeError(f'Unable to inspect original save parent: {path}') from exc
        else:
            if not stat.S_ISDIR(info.st_mode):
                raise RuntimeError(f'Original save ancestor is not a directory: {path}')
            return path


def require_isolated_profile(realms: list[str], lab_root: Path = LAB_GAMES,
                             config_path: Path = ISOLATION_CONFIG) -> None:
    if not config_path.is_file():
        raise RuntimeError('No isolated lab profile configured; refusing to launch games against shared saves')
    try:
        settings = json.loads(config_path.read_text(encoding='utf-8'))
        owner = settings['source_user_sid']
        lab = settings['lab_user_sid']
        source_profile = Path(settings['source_profile']).resolve()
        lab_profile = Path(settings['lab_profile']).resolve()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise RuntimeError('Invalid isolated lab profile configuration') from exc
    if not owner or not lab or owner == lab:
        raise RuntimeError('Lab and source have the same Windows identity, or the SID is missing')
    if source_profile == lab_profile:
        raise RuntimeError('A separate Windows profile is required')
    actual_sid, actual_profile = _current_identity()
    if actual_sid == owner or actual_sid != lab:
        raise RuntimeError('Lab and source have the same Windows identity, or the configured lab SID is not active')
    if actual_profile != lab_profile or source_profile in lab_profile.parents:
        raise RuntimeError('Lab process is not running with its distinct Windows profile')
    if _profile_for_sid(owner) != source_profile or _profile_for_sid(lab) != lab_profile:
        raise RuntimeError('Configured paths do not match Windows profile registration')
    # The registered profile directory itself may be owned by SYSTEM; the source
    # installation and existing game save directory are checked below instead.

    sources = []
    for realm in realms:
        if realm not in SAVE_RELATIVE:
            raise RuntimeError(f'Unknown game realm: {realm}')
        manifest = lab_root / realm / 'slimebridge-lab.json'
        try:
            source = Path(json.loads(manifest.read_text(encoding='utf-8'))['source']).resolve()
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise RuntimeError(f'No verified lab source manifest for {realm}') from exc
        if not source.is_dir() or lab_root.resolve() in source.parents:
            raise RuntimeError(f'Invalid original game source in {realm} manifest')
        if _path_owner_sid(source) != owner:
            raise RuntimeError(f'Configured SID is not the owner of original {realm} installation')
        sources.extend(_tree_paths(source))
        save_root = source_profile / 'AppData' / 'LocalLow' / SAVE_RELATIVE[realm]
        try:
            save_stat = save_root.stat()
        except FileNotFoundError:
            sources.append(_existing_parent(save_root))
        except OSError as exc:
            raise RuntimeError(f'Unable to inspect original save root: {save_root}') from exc
        else:
            if not stat.S_ISDIR(save_stat.st_mode):
                raise RuntimeError(f'Original save root is not a directory: {save_root}')
            if _path_owner_sid(save_root) != owner:
                raise RuntimeError(f'Configured SID is not the owner of original {realm} saves')
            sources.extend(_tree_paths(save_root))
    for original in sources:
        if _can_write(original):
            raise RuntimeError(f'Lab identity has writable original path: {original}')
