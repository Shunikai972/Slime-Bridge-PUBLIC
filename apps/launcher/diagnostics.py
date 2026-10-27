"""One plain-text report of everything that decides whether SlimeBridge can work on this PC (Debug page, "Copy").

Read-only: it looks at files, settings and logs; it never changes a game folder. Tokens and save contents are never
included. Each section is independent: a failing probe prints its error and the report goes on.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_LINES = 15


def _tail(path, lines=LOG_LINES):
    try:
        data = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
        return data[-lines:]
    except OSError as exc:
        return [f'(unreadable: {exc})']


def _section(out, title, fn):
    out.append(f'\n== {title} ==')
    try:
        fn(out)
    except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the rest
        out.append(f'  !! {type(exc).__name__}: {exc}')


def _free(path):
    try:
        return f'{shutil.disk_usage(path).free / 1e9:.1f} GB free'
    except OSError as exc:
        return f'? ({exc})'


def _system(out):
    version = (REPO_ROOT / 'VERSION').read_text(encoding='utf-8').strip() if (REPO_ROOT / 'VERSION').is_file() else '?'
    out.append(f'  SlimeBridge {version}  frozen={getattr(sys, "frozen", False)}  exe={sys.executable}')
    out.append(f'  {platform.platform()}  Python {platform.python_version()}  {platform.machine()}')
    from apps.launcher import integrity
    out.append(f'  Windows long paths enabled: {integrity.long_paths_enabled()}')
    lab = REPO_ROOT / 'Lab'
    try:
        lab.mkdir(parents=True, exist_ok=True)
        probe = lab / '.diag-write'
        probe.write_text('ok', encoding='utf-8'); probe.unlink()
        writable = 'yes'
    except OSError as exc:
        writable = f'NO ({exc})'
    out.append(f'  Lab folder: {lab}  writable={writable}  {_free(lab)}')


def _installs(out, api):
    from apps.launcher import detect
    settings = {}
    try:
        settings = api._load() if api is not None else {}
    except Exception:  # noqa: BLE001
        pass
    out.append(f"  selected: {json.dumps(settings.get('selected', {}))}")
    out.append(f"  manual paths: {json.dumps(settings.get('manual_paths', []))}")
    found = detect.auto_detect()
    items = found if isinstance(found, list) else []
    out.append(f'  auto-detected: {len(items)}')
    for it in items:
        out.append(f'    {it.game}  {it.root}  ({it.store})')
    return items, settings


def _game(out, game, root):
    from apps.launcher import integrity, loaders, online, plugins, sanctuary
    root = Path(root)
    out.append(f'  folder: {root}  exists={root.is_dir()}  {_free(root.anchor or root)}')
    state = integrity.check(game, root)
    out.append(f"  complete: {state['complete']}  missing({state['missing_count']}): {state['missing']}")
    if state.get('too_long'):
        out.append(f'  TOO DEEP for Windows: deepest file path {integrity.deepest_path(root)} characters')
    if state['missing_count']:
        cands = integrity.repair_candidates(game, root)
        out.append(f'  repair archives found on this PC: {cands or "none"}')
    out.append(f'  mod loader: {loaders.describe(game, root)}')
    record = root / 'SlimeBridge' / 'loader.json'
    if record.is_file():
        out.append(f'    installed by SlimeBridge: {record.read_text(encoding="utf-8")[:200]}')
    p = plugins.status(game, root)
    out.append(f"  SlimeBridge plugin: {p['state']}  (loader ready: {p['loader']})")
    try:
        o = online.status(game, root)
        out.append(f"  online: {o.get('state')}")
    except Exception as exc:  # noqa: BLE001
        out.append(f'  online: ? ({exc})')
    if game == 'SR2':
        from bridge.content import sr2_schema
        schema = sr2_schema.schema_dir(root)
        out.append(f'  code schema (Cpp2IL): {schema if schema else "not made yet (Extract makes it, 1-3 minutes)"}')
        log = root / 'MelonLoader' / 'Latest.log'
        if log.is_file():
            out.append('  MelonLoader/Latest.log (end):')
            out += [f'    | {line}' for line in _tail(log)]
        mods = root / 'Mods'
        if mods.is_dir():
            out.append(f"  Mods: {sorted(p.name for p in mods.iterdir())}")
    else:
        info = sanctuary.installed(root)
        out.append(f"  island (sanctuary) installed: {info.get('present')}  matches latest layout: {info.get('matches_latest')}")
        mods = root / 'SRML' / 'Mods'
        if mods.is_dir():
            out.append(f"  SRML/Mods: {sorted(p.name for p in mods.iterdir())}")
        log = Path(os.environ.get('USERPROFILE', '~')).expanduser() / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher' / 'output_log.txt'
        if log.is_file():
            out.append('  output_log.txt (end):')
            out += [f'    | {line}' for line in _tail(log)]


def _sanctuary(out):
    from apps.launcher import sanctuary
    kit, layout = sanctuary.latest_kit(), sanctuary.latest_layout()
    out.append(f"  island kit: {kit['name'] + ' (' + kit['path'] + ')' if kit else 'NONE'}")
    out.append(f"  island layout: {layout['name'] + ', ' + str(layout['pieces']) + ' pieces' if layout else 'NONE (built on the next Play / plugin install)'}")


def _content(out):
    specs = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
    names = sorted(p.name for p in specs.iterdir() if p.is_dir()) if specs.is_dir() else []
    out.append(f"  species specs: {len(names)}  newest: {names[-1] if names else 'none'}")
    cache = REPO_ROOT / 'Lab' / 'AssetCache'
    for sub in ('sr2-to-sr1', 'sr1-to-sr2'):
        d = cache / sub
        n = sum(1 for p in d.iterdir() if p.is_dir()) if d.is_dir() else 0
        out.append(f'  extracted {sub}: {n}')


def _defender(out):
    if os.name != 'nt':
        out.append('  (not Windows)')
        return
    cmd = ("Get-MpThreatDetection -ErrorAction Stop | Select-Object -First 40 | ForEach-Object { "
           "$t = $_; ($t.Resources -join ';') + ' | ' + $t.InitialDetectionTime + ' | ' + $t.ActionSuccess }")
    proc = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = [l for l in proc.stdout.splitlines() if 'slime' in l.lower()]
    if proc.returncode != 0 and not proc.stdout:
        out.append(f'  (could not read: {proc.stderr.strip()[:200]})')
    else:
        out.append(f"  detections touching a Slime folder: {len(lines)}")
        out += [f'    {l[:300]}' for l in lines[:10]]


def _network(out):
    for url in ('https://github.com', 'https://api.github.com'):
        t0 = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'SlimeBridge-diag'}), timeout=6) as r:
                out.append(f'  {url}: {r.status} in {time.time() - t0:.1f}s')
        except Exception as exc:  # noqa: BLE001
            out.append(f'  {url}: FAILED ({type(exc).__name__}: {exc})')


def report(api=None, extraction_logs=None):
    out = [f'SlimeBridge diagnostic - {time.strftime("%Y-%m-%d %H:%M:%S")}']
    _section(out, 'System', _system)
    holder = {}
    _section(out, 'Game installs', lambda o: holder.update(zip(('items', 'settings'), _installs(o, api))))
    chosen = {}
    for item in holder.get('items', []):
        chosen.setdefault(item.game, item.root)
    for game, root in (holder.get('settings', {}).get('selected') or {}).items():
        chosen[game] = root
    if api is not None and hasattr(api, '_primary'):          # exactly the install the launcher uses
        for game in ('SR1', 'SR2'):
            try:
                primary = api._primary(game)
                if primary:
                    chosen[game] = primary['root']
            except Exception:  # noqa: BLE001
                pass
    for game in ('SR1', 'SR2'):
        if game in chosen:
            _section(out, f'{game} (selected)', lambda o, g=game: _game(o, g, chosen[g]))
        else:
            out.append(f'\n== {game} ==\n  not found')
    _section(out, 'Island (sanctuary)', _sanctuary)
    _section(out, 'Extracted content', _content)
    _section(out, 'Windows security (antivirus history)', _defender)
    _section(out, 'Network', _network)
    if extraction_logs:
        out.append('\n== Last extraction (end of log) ==')
        out += [f'  | {line}' for line in extraction_logs[-40:]]
    return '\n'.join(out)
