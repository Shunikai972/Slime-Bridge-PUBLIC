"""Read-only SR2 focus components on an explicitly existing save. Zero CREATE/input.

A successful result proves component telemetry only, NOT intro advancement or controls.
All game launch/stop, original fingerprints and user save/registry restoration use oracle.run.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from tools.lab import oracle
from tools.lab.paths import LAB_GAMES as LAB
from tools.lab.process_guard import same_image

LOG = LAB / 'SR2/MelonLoader/Latest.log'
PLUGIN = LAB / 'SR2/Mods/SlimeBridge.SR2.dll'
FIELDS = frozenset(('sample', 'time_seconds', 'utc_time', 'own_pid', 'unity_platform',
                    'unity_focus_before', 'run_in_background', 'hwnd_before', 'foreground_thread',
                    'foreground_pid', 'win32_error', 'hwnd_after', 'hwnd_stable', 'unity_focus_after',
                    'error_stage', 'error_type', 'error_message'))
STAGES = frozenset(('unity_platform', 'unity_focus_before', 'run_in_background', 'hwnd_before',
                    'window_owner', 'hwnd_after', 'unity_focus_after'))


def parse_rows(text: str, pid: int) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if 'lab-focus ' not in line:
            continue
        payload = line.split('lab-focus ', 1)[1]
        try:
            value = json.loads(payload)
        except (ValueError, TypeError) as error:
            raise ValueError('Malformed focus JSON') from error
        if not isinstance(value, dict) or set(value) != FIELDS:
            raise ValueError('Focus row fields differ from the native schema')
        if type(value['sample']) is not int or value['sample'] != len(rows) + 1 or len(rows) >= 120:
            raise ValueError('Noncontiguous or unbounded focus rows')
        if type(value['own_pid']) is not int or value['own_pid'] != pid or pid <= 0:
            raise ValueError('Focus row is not from the owned target')
        clock = value['time_seconds']
        if type(clock) not in (int, float) or not math.isfinite(clock) or (rows and clock < rows[-1]['time_seconds']):
            raise ValueError('Invalid diagnostic clock')
        if not isinstance(value['utc_time'], str):
            raise ValueError('Missing diagnostic UTC timestamp')
        try:
            stamp = datetime.fromisoformat(value['utc_time'].replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError('Invalid diagnostic UTC timestamp') from error
        if stamp.utcoffset() is None:
            raise ValueError('Diagnostic timestamp must be timezone-aware')
        for key in ('unity_focus_before', 'run_in_background', 'hwnd_stable', 'unity_focus_after'):
            if value[key] is not None and type(value[key]) is not bool:
                raise ValueError('Invalid nullable focus boolean')
        for key in ('foreground_thread', 'foreground_pid'):
            if value[key] is not None and (type(value[key]) is not int or not 0 <= value[key] <= 0xffffffff):
                raise ValueError('Invalid nullable owner identifier')
        if value['win32_error'] is not None and (type(value['win32_error']) is not int or
                                                not -(2 ** 31) <= value['win32_error'] < 2 ** 31):
            raise ValueError('Invalid Win32 error')
        for key in ('hwnd_before', 'hwnd_after'):
            if value[key] is not None and (not isinstance(value[key], str) or
                                          not re.fullmatch(r'0x[0-9a-f]{1,16}', value[key])):
                raise ValueError('Invalid nullable HWND')
        for key, limit in (('unity_platform', 80), ('error_type', 128), ('error_message', 160)):
            item = value[key]
            if item is not None and (not isinstance(item, str) or len(item) > limit or
                                     any(ord(char) < 32 or ord(char) == 127 for char in item)):
                raise ValueError('Unbounded/control-containing diagnostic string')
        if value['error_stage'] is not None:
            if value['error_stage'] not in STAGES or not value['error_type'] or value['error_message'] is None:
                raise ValueError('Incomplete native exception evidence')
        elif value['error_type'] is not None or value['error_message'] is not None:
            raise ValueError('Exception fields without an error stage')
        rows.append(value)
    return rows


def blockers(row: dict) -> list[str]:
    """Describe component observations, never grant authorization from a detached row."""
    reasons = []
    if row['unity_platform'] is not None and row['unity_platform'] != 'WindowsPlayer:2':
        reasons.append('unsupported-unity-platform')
    if row['unity_focus_before'] is True or row['unity_focus_after'] is True:
        reasons.append('unity-focus-true')
    if row['hwnd_before'] == '0x0' or row['hwnd_after'] == '0x0':
        reasons.append('no-foreground-window')
    if row['foreground_thread'] == 0:
        reasons.append('unknown-foreground-thread')
    if row['foreground_pid'] == 0:
        reasons.append('unknown-foreground-owner')
    elif row['foreground_pid'] == row['own_pid']:
        reasons.append('own-process-foreground')
    if row['hwnd_stable'] is False:
        reasons.append('foreground-window-changed')
    if row['error_stage'] is not None:
        reasons.append(f"capture-error:{row['error_stage']}:{row['error_type']}")
    return reasons


def rows_digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def verify_post_cleanup(rows: list[dict], accepted_digest: str) -> None:
    if rows_digest(rows) != accepted_digest:
        raise ValueError('Post-cleanup focus payload differs from live-accepted evidence')


def require_owned(procs: dict, pids: dict) -> None:
    if set(procs) != {'SR1', 'SR2'} or set(pids) != {'SR1', 'SR2'}:
        raise RuntimeError('Missing retained owned game process')
    for realm in ('SR1', 'SR2'):
        proc = procs[realm]
        if proc.pid != pids[realm] or proc.poll() is not None or not same_image(proc.pid, oracle.GAMES[realm]):
            raise RuntimeError('Owned game PID/image/liveness mismatch: ' + realm)


def foreground_sample() -> dict:
    """Read desktop ownership only. Never activates, captures or inputs to a window."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = ()
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    before = user32.GetForegroundWindow()
    owner = wintypes.DWORD(0)
    thread = user32.GetWindowThreadProcessId(before, ctypes.byref(owner)) if before else 0
    after = user32.GetForegroundWindow()
    return {'utc_time': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(),
            'hwnd_before': before or 0, 'hwnd_after': after or 0, 'pid': owner.value,
            'valid_foreground': bool(before and before == after and thread and owner.value)}


def make_scenario(work: Path, source_save: str, target_save: str):
    def scenario(host, beats, pids, procs=None):
        evidence = {'passed': False, 'scope': 'READ_ONLY_FOCUS_COMPONENTS',
                    'intro_verified': False, 'controls_verified': False, 'pids': pids,
                    'expected_saves': {'SR1': source_save, 'SR2': target_save},
                    'native_create_requested': False, 'input_performed': False}
        if procs is None:
            raise RuntimeError('Retained owned processes required')
        require_owned(procs, pids)
        deadline = time.monotonic() + 35
        external = []
        while time.monotonic() < deadline:
            require_owned(procs, pids)
            current = host.heartbeats()
            for realm, save in evidence['expected_saves'].items():
                beat = current.get(realm)
                if (not oracle.loaded_status_ok(beat) or beat.get('pid') != pids[realm] or
                        beat['status'].get('save_id') != save):
                    raise RuntimeError('Fresh exact-save heartbeat lost: ' + realm)
            external.append(foreground_sample())
            text = LOG.read_text(encoding='utf-8', errors='replace')
            rows = parse_rows(text, pids['SR2'])
            if 'lab-focus-end ' in text and len(rows) >= 2:
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Bounded focus sampler did not publish at least two rows and a stop line')
        require_owned(procs, pids)
        (work / 'live-focus-log.log').write_text(text, encoding='utf-8')
        (work / 'live-focus-rows.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        (work / 'external-focus.json').write_text(json.dumps(external, indent=2), encoding='utf-8')
        evidence.update(passed=True, row_count=len(rows), live_rows_digest=rows_digest(rows),
                        native_component_blockers=[{'sample': row['sample'], 'reasons': blockers(row)} for row in rows],
                        external_sample_count=len(external),
                        interpretation='Component observations are not atomic skip authorization; no intro/native advance path tested')
        (work / 'focus-scenario.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        return evidence
    return scenario


def run_diagnostic(work: Path, source_save: str, target_save: str, timeout: float) -> dict:
    # A registry output variable is requested even when empty; never overwrite global environment
    # or substitute a bogus path to disable an inherited native asset-capture mode.
    if os.environ.get('SLIMEBRIDGE_LAB_REGISTRY_OUT') is not None:
        raise ValueError('Inherited registry capture conflicts with read-only focus diagnostic')
    extra_env = {realm: {'SLIMEBRIDGE_LAB_CREATE': '0', 'SLIMEBRIDGE_LAB_GIVE': '0',
                         'SLIMEBRIDGE_LAB_COUNTERPART_DIAGNOSTIC': '0',
                         'SLIMEBRIDGE_LAB_FOCUS_DIAGNOSTICS': '1' if realm == 'SR2' else '0'}
                 for realm in ('SR1', 'SR2')}
    return oracle.run(['SR1', 'SR2'], timeout, False, work, require_status=True, current_user=True,
                      sr1_save_id=source_save, sr2_save_id=target_save,
                      scenario=make_scenario(work, source_save, target_save), extra_env=extra_env,
                      guard_realms=['SR1', 'SR2'], creation_realms=())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-user-authorized', action='store_true', required=True)
    parser.add_argument('--sr1-save-id', required=True)
    parser.add_argument('--sr2-save-id', required=True)
    parser.add_argument('--timeout', type=float, default=420)
    args = parser.parse_args()
    for save in (args.sr1_save_id, args.sr2_save_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', save):
            parser.error('Invalid literal native save group; no normalization/fallback permitted')
    if not math.isfinite(args.timeout) or not 60 <= args.timeout <= 600:
        parser.error('timeout must be finite and between60and600seconds')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    work = oracle.REPO_ROOT / 'Lab/RuntimeArtifacts' / ('focus-' + stamp)
    work.mkdir(parents=True, exist_ok=False)
    plugin_sha = hashlib.sha256(PLUGIN.read_bytes()).hexdigest()
    result = run_diagnostic(work, args.sr1_save_id, args.sr2_save_id, args.timeout)
    evidence = result.get('scenario') or {}
    try:
        if evidence.get('passed'):
            post_text = LOG.read_text(encoding='utf-8', errors='replace')
            rows = parse_rows(post_text, evidence['pids']['SR2'])
            verify_post_cleanup(rows, evidence['live_rows_digest'])
            if hashlib.sha256(PLUGIN.read_bytes()).hexdigest() != plugin_sha:
                raise ValueError('Plugin changed during diagnostic run')
            (work / 'post-focus-log.log').write_text(post_text, encoding='utf-8')
            evidence['post_cleanup_payload_matches'] = True
    except Exception as error:
        result['passed'] = False
        evidence['post_cleanup_error'] = f'{type(error).__name__}: {error}'
    result['focus_scope'] = 'READ_ONLY_FOCUS_COMPONENTS'
    result['diagnostic_plugin_sha256'] = plugin_sha
    result['intro_verified'] = False
    result['controls_verified'] = False
    (work / 'focus-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'passed': result.get('passed'), 'artifact': str(work), 'scenario': evidence,
                      'sources_untouched': result.get('sources_untouched'),
                      'saves_match_backup_after_restore': result.get('saves_match_backup_after_restore')}, indent=2))
    raise SystemExit(0 if result.get('passed') else 1)


if __name__ == '__main__':
    main()
