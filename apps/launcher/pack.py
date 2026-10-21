"""Make a game ready in one call: plugin + island/portal layout + species caches, and arm the species for LAB copies.

Until now these pieces lived apart: the plugin was copied by one tool, the island layout installed by another, the
imported species only existed in a developer "dev session" that set a handful of environment variables by hand. The
launcher's Play button (and ``build_all.py --play``) now go through here, so a game started from the launcher has the
SlimeBridge plugin, the sanctuary island with its portal to the other game, the imported species and the BridgeHost link
all at once.

Species registration is a LAB boundary in the plugin (it only arms for the exact LAB executable and a spec under
``Lab/GeneratedContent/species-import``); for any other install this module installs the plugin and the island and
nothing else, exactly as before.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from apps.launcher import plugins, sanctuary

REPO_ROOT = Path(__file__).resolve().parents[2]
EXE = {'SR1': 'SlimeRancher.exe', 'SR2': 'SlimeRancher2.exe'}


def is_lab_copy(realm, game_root, repo_root=REPO_ROOT):
    try:
        return Path(game_root).resolve() == (Path(repo_root) / 'Lab' / 'Games' / realm).resolve()
    except OSError:
        return False


def lab_env(realm, game_root, repo_root=REPO_ROOT, spec=None):
    """Environment that arms the imported species in a LAB game started by the launcher ({} for anything else).

    SR1 reads the SR2 species (caches by manifest hash); SR2 reads the SR1 species and Largos: its spec plus one cache folder per
    canonical id under ``Lab/AssetCache/sr1-to-sr2``."""
    if realm not in EXE or not is_lab_copy(realm, game_root, repo_root):
        return {}
    spec_root = Path(repo_root) / 'Lab' / 'GeneratedContent' / 'species-import'
    if realm == 'SR1':
        from bridge.content.species_pipeline import newest_spec
    else:
        from bridge.content.sr1_pipeline import newest_spec
    spec = Path(spec) if spec else newest_spec(spec_root)
    if spec is None or not spec.is_file():
        return {}
    run_id = 'launcher-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    out = Path(repo_root) / 'Lab' / 'RuntimeArtifacts' / run_id
    out.mkdir(parents=True, exist_ok=True)
    env = {'SLIMEBRIDGE_LAB_EXE': str(Path(game_root).resolve() / EXE[realm]),
           'SLIMEBRIDGE_IMPORT_SPEC': str(spec.resolve()),
           'SLIMEBRIDGE_IMPORT_OUT': str(out.resolve()),
           'SLIMEBRIDGE_IMPORT_RUN': run_id}
    caches = Path(repo_root) / 'Lab' / 'AssetCache' / 'sr1-to-sr2'
    if realm == 'SR2' and caches.is_dir():
        env['SLIMEBRIDGE_IMPORT_CACHE_ROOT'] = str(caches.resolve())
    return env


def species_ready(repo_root=REPO_ROOT, spec=None):
    """Which READY species of the spec have a complete model cache: (missing, total)."""
    from bridge.content import species_pipeline as pipeline
    try:
        rows = pipeline.roster(Path(spec) if spec else pipeline.newest_spec(Path(repo_root) / 'Lab' / 'GeneratedContent' / 'species-import'))
    except ValueError:
        return [], 0
    cache = Path(repo_root) / 'Lab' / 'AssetCache' / 'sr2-to-sr1'
    missing = [r['canonical'] for r in rows if not pipeline.cache_is_complete(cache / r['canonical'])]
    return missing, len(rows)


def prepare(realm, game_root, repo_root=REPO_ROOT):
    """Idempotent: plugin, island layout and (LAB copies) species caches. Returns {'ok', 'steps', 'warnings'}; never raises."""
    steps, warnings = [], []

    def step(name, status, detail=''):
        steps.append({'step': name, 'status': status, 'detail': detail})

    try:
        state = plugins.status(realm, game_root, repo_root)
        if state['state'] in ('missing', 'outdated') and state['loader']:
            result = plugins.install(realm, game_root, repo_root)
            step('plugin', 'INSTALLED', ', '.join(result['files']))
        elif state['state'] == 'unavailable':
            warnings.append('no built plugin to install (run build_all.py)')
            step('plugin', 'UNAVAILABLE')
        elif not state['loader']:
            warnings.append('the mod loader is not installed in this game folder')
            step('plugin', 'LOADER_MISSING')
        else:
            step('plugin', 'CURRENT')
    except plugins.PluginError as exc:
        warnings.append(f'plugin: {exc}')
        step('plugin', 'FAILED', str(exc))

    if realm == 'SR1':
        try:
            try:
                rebuilt = sanctuary.refresh_layout()
            except (ValueError, OSError, KeyError) as exc:               # a stale layout is still usable; say so
                rebuilt = None
                warnings.append(f'island layout not refreshed: {exc}')
            latest = sanctuary.latest_layout()
            info = sanctuary.installed(game_root)
            if latest is None:
                warnings.append('no sanctuary layout built yet: use the launcher "Sanctuary" card to read your SR1 and place the island')
                step('island', 'NO_LAYOUT')
            elif info.get('present') and info.get('matches_latest') and not rebuilt:
                step('island', 'CURRENT', latest['name'])
            else:
                sanctuary.install_layout(latest['path'], game_root)
                step('island', 'INSTALLED', f"{latest['name']} ({latest['statues']} statues, portal to Slime Rancher 2)")
        except (ValueError, OSError) as exc:
            warnings.append(f'island: {exc}')
            step('island', 'FAILED', str(exc))
        # LAB copies and normal installs alike (a normal install reads the pointer file written at the end)
        missing, total = species_ready(repo_root)
        if total == 0:
            step('species', 'NO_SPEC')
        elif missing:
            warnings.append(f'{len(missing)} imported species have no model cache yet (launcher Extract, or '
                            '`py -3.12 -m bridge.content.species_pipeline`): ' + ', '.join(missing))
            step('species', 'CACHES_MISSING', ', '.join(missing))
        else:
            step('species', 'READY', f'{total} imported species armed')
        try:
            from bridge.content import largos
            refreshed = largos.refresh_spec(repo_root)
            if refreshed.get('changed'):
                step('largos', 'SPEC_PUBLISHED', f"{refreshed['largos']} Largos armed")
            elif refreshed.get('largos'):
                step('largos', 'READY', f"{refreshed['largos']} Largos armed")
            else:
                step('largos', 'NO_CACHES', 'launcher Extract (Largos) builds them from your Slime Rancher 2')
        except Exception as exc:  # noqa: BLE001 - Largos are an addition, never a reason to refuse to start
            warnings.append(f'largos: {type(exc).__name__}: {exc}')
            step('largos', 'FAILED', str(exc))
    if realm == 'SR2':
        try:
            from bridge.content import sr1_pipeline
            refreshed = sr1_pipeline.refresh_spec(repo_root)
            if refreshed.get('changed'):
                step('sr1-content', 'SPEC_PUBLISHED', f"{refreshed['largos']} SR1 Largos armed")
            elif refreshed.get('largos'):
                step('sr1-content', 'READY', f"{refreshed['largos']} SR1 Largos armed")
            else:
                step('sr1-content', 'NO_CACHES', 'launcher Extract (SR1 → SR2) builds them from your Slime Rancher')
        except Exception as exc:  # noqa: BLE001 - an addition, never a reason to refuse to start
            warnings.append(f'sr1 content: {type(exc).__name__}: {exc}')
            step('sr1-content', 'FAILED', str(exc))
    if realm in EXE and not is_lab_copy(realm, game_root, repo_root):
        try:
            pointer = write_player_pointer(realm, game_root, repo_root)
            step('player-content', 'READY' if pointer else 'NO_SPEC',
                 str(pointer) if pointer else 'extract first (launcher Extract): nothing to arm yet')
        except (OSError, ValueError) as exc:
            warnings.append(f'player content: {exc}')
            step('player-content', 'FAILED', str(exc))
    return {'ok': not any(s['status'] == 'FAILED' for s in steps), 'steps': steps, 'warnings': warnings}


POINTER_PARTS = ('SlimeBridge', 'content.json')


def _reparse(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def write_player_pointer(realm, game_root, repo_root=REPO_ROOT):
    """Write ``<game>/SlimeBridge/content.json``: where the plugin of a normal install finds the extracted content.

    The plugin validates every path again (inside this SlimeBridge folder's Lab tree, no links), so the file only points;
    it never grants more than the LAB guard would. Returns the pointer path, or None (and removes a stale pointer) when
    there is no published spec yet."""
    import json
    import os
    repo_root = Path(repo_root).resolve()
    spec_root = repo_root / 'Lab' / 'GeneratedContent' / 'species-import'
    if realm == 'SR1':
        from bridge.content.species_pipeline import newest_spec
    else:
        from bridge.content.sr1_pipeline import newest_spec
    spec = newest_spec(spec_root) if spec_root.is_dir() else None
    target = Path(game_root).joinpath(*POINTER_PARTS)
    for part in (Path(game_root), target.parent):
        if part.exists() and _reparse(part):
            raise ValueError(f'link refused: {part}')
    if spec is None or not Path(spec).is_file():
        if target.is_file():
            target.unlink()
        return None
    out = repo_root / 'Lab' / 'RuntimeArtifacts' / f'player-{realm}'
    out.mkdir(parents=True, exist_ok=True)
    for stale in sorted((d for d in out.iterdir() if d.is_dir() and not _reparse(d)), key=lambda d: d.name)[:-5]:
        import shutil
        shutil.rmtree(stale, ignore_errors=True)          # keep the receipts of the last five sessions
    for loose in out.glob('*.json'):
        loose.unlink()                                    # pre-folder receipts of older plugins
    doc = {'schema': 'slimebridge-player-content', 'schema_version': 1, 'realm': realm, 'home': str(repo_root),
           'spec': str(Path(spec).resolve()), 'out': str(out)}
    caches = repo_root / 'Lab' / 'AssetCache' / 'sr1-to-sr2'
    if realm == 'SR2' and caches.is_dir():
        doc['cache_root'] = str(caches)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(doc, indent=1), encoding='utf-8')
    os.replace(temp, target)
    return target


def remove_player_pointer(game_root):
    target = Path(game_root).joinpath(*POINTER_PARTS)
    if target.is_file() and not _reparse(target):
        target.unlink()
        return True
    return False
