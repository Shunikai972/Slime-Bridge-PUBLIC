"""The SR2 code schema (Cpp2IL dummy assemblies) the extractor needs to read Slime Rancher 2's scripted objects.

MelonLoader makes it on the first game start with mods (MelonLoader/Dependencies/Il2CppAssemblyGenerator/Cpp2IL/
cpp2il_out). A player who installs everything and presses Extract before ever starting the game has none, and every
species fails ("failed to dump nodes raw"). ensure() then makes the same schema itself, exactly like MelonLoader:
the same Cpp2IL release (downloaded from its official GitHub release, sha256 pinned), the same plugin and the same
command line, run on the player's own game. The result is cached per game build in Lab/Cache/cpp2il/.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE = REPO_ROOT / 'Lab' / 'Cache' / 'cpp2il'
VERSION = '2022.1.0-pre-release.21'                 # what MelonLoader 0.7.3 uses
_BASE = f'https://github.com/SamboyCoding/Cpp2IL/releases/download/{VERSION}/'
TOOL = {'name': 'Cpp2IL.exe', 'url': _BASE + f'Cpp2IL-{VERSION}-Windows.exe',
        'sha256': '663fb432433b4371fd1ee0ebc321a8fff2a9aac5ac4230c843f9e03ddee4e04c'}
PLUGIN = {'name': 'Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll', 'url': _BASE + 'Cpp2IL.Plugin.StrippedCodeRegSupport.dll',
          'sha256': '2cc4f8c66541f18b4daed5410149ee7859c3190454d30bda5ccd0895d2989ee6'}
MELON_OUT = Path('MelonLoader') / 'Dependencies' / 'Il2CppAssemblyGenerator' / 'Cpp2IL' / 'cpp2il_out'
TIMEOUT = 10 * 60                                   # takes 15-60 s on a normal PC


class SchemaError(RuntimeError):
    pass


def _complete(folder: Path) -> bool:
    return (folder / 'Assembly-CSharp.dll').is_file()


def build_key(game_root) -> str:
    """Identifies the game build: the schema is only valid for the exact GameAssembly + metadata it came from."""
    root = Path(game_root)
    meta = next(root.glob('*_Data/il2cpp_data/Metadata/global-metadata.dat'), None)
    asm = root / 'GameAssembly.dll'
    if meta is None or not asm.is_file():
        raise SchemaError(f'{root} is not an IL2CPP Slime Rancher 2 folder (no GameAssembly.dll / global-metadata.dat)')
    digest = hashlib.sha256(str(asm.stat().st_size).encode())
    with meta.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()[:16]


def schema_dir(game_root, cache=CACHE):
    """MelonLoader's own schema when the game already ran with it, else ours for this build; None when neither."""
    root = Path(game_root)
    if _complete(root / MELON_OUT):
        return root / MELON_OUT
    try:
        ours = Path(cache) / build_key(root) / 'cpp2il_out'
    except SchemaError:
        return None
    return ours if _complete(ours) else None


def _fetch(spec, tool_dir: Path, opener=urllib.request.urlopen):
    target = tool_dir / spec['name']
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == spec['sha256']:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(spec['url'], headers={'User-Agent': 'SlimeBridge'})
    try:
        with opener(req, timeout=300) as r:
            data = r.read()
    except OSError as exc:
        raise SchemaError(f'could not download {spec["url"]}: {exc}') from exc
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise SchemaError(f'{spec["url"]} does not match its pinned sha256')
    tmp = target.with_suffix(target.suffix + '.part')
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target


def ensure(game_root, log=print, cache=CACHE, opener=urllib.request.urlopen, run=subprocess.run):
    """Return the schema folder, making it with Cpp2IL when the game never started with MelonLoader. Raises SchemaError."""
    try:
        return _ensure(Path(game_root), log, Path(cache), opener, run)
    except OSError as exc:                       # disk full, locked folder, antivirus... one clear error type
        raise SchemaError(f'{type(exc).__name__}: {exc}') from exc


def _ensure(root, log, cache, opener, run):
    found = schema_dir(root, cache)
    if found is not None:
        return found
    if os.name != 'nt':
        raise SchemaError('start Slime Rancher 2 once with MelonLoader: it makes the code schema the extractor needs')
    data = next(root.glob('*_Data'), None)
    if data is None:
        raise SchemaError(f'{root} has no *_Data folder')
    key = build_key(root)
    work = Path(cache) / key
    log('Slime Rancher 2 code schema missing (the game never started with MelonLoader): making it now, 1-3 minutes')
    tool_dir = Path(cache) / 'tool'
    exe = _fetch(TOOL, tool_dir, opener)
    _fetch(PLUGIN, tool_dir, opener)                     # Cpp2IL loads plugins from <exe folder>/Plugins
    shutil.rmtree(work / 'cpp2il_out', ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    # same command MelonLoader runs; Cpp2IL writes cpp2il_out/ in its working folder
    command = [str(exe), '--game-path', str(root), '--exe-name', data.name[:-len('_Data')],
               '--output-as', 'dummydll', '--use-processor', 'attributeanalyzer', 'attributeinjector']
    try:
        # no stdin: on an error Cpp2IL waits for a key press, without stdin it exits instead
        proc = run(command, cwd=str(work), stdin=subprocess.DEVNULL, capture_output=True, text=True, errors='replace', timeout=TIMEOUT,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SchemaError(f'Cpp2IL could not run: {exc}') from exc
    out = work / 'cpp2il_out'
    if not _complete(out):
        tail = ' | '.join((proc.stdout or '').strip().splitlines()[-4:] + (proc.stderr or '').strip().splitlines()[-4:])
        raise SchemaError(f'Cpp2IL exited with {proc.returncode} without making the schema: {tail[:600]}')
    log(f'  code schema ready ({sum(1 for _ in out.glob("*.dll"))} assemblies)')
    return out
