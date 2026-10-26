"""Current-user save protection for lab runtime tests (owner-authorized shared LocalLow/HKCU mode).

Before a run: hash every file under the native SR1/SR2 save roots, copy them into a timestamped
backup under Lab/Profiles/CurrentUser/Backups and export the games' HKCU keys. After a run: re-hash,
report added/removed/modified files, and restore modified or removed ORIGINAL files from the backup
when asked (the evidence copy of the post-run state is preserved first). Nothing is ever deleted
without a backup copy, and newly created files are only moved aside into the evidence folder.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools.lab.paths import REPO_ROOT, refuse_reparse

CURRENT_USER_ROOT = REPO_ROOT / 'Lab' / 'Profiles' / 'CurrentUser'
SAVE_DIRS = {
    'SR1': Path('Monomi Park') / 'Slime Rancher',
    'SR2': Path('MonomiPark') / 'SlimeRancher2',
}
REGISTRY_KEYS = {
    'SR1': r'HKCU\Software\Monomi Park\Slime Rancher',
    'SR2': r'HKCU\Software\MonomiPark\SlimeRancher2',
}
# Logs are rewritten by every launch; they are evidence, not user data, and are never restored.
VOLATILE_SUFFIXES = ('.log',)


def _registry_key_exists(key: str) -> bool:
    """True when the HKCU key exists (reg.exe query exits 0). Only the fixed REGISTRY_KEYS strings are ever passed."""
    return subprocess.run(['reg.exe', 'query', key], capture_output=True).returncode == 0


def local_low(profile: Path | None = None) -> Path:
    profile = profile or Path(os.environ['USERPROFILE'])
    return profile / 'AppData' / 'LocalLow'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def hash_tree(root: Path) -> dict[str, dict]:
    result = {}
    refuse_reparse(root)
    if not root.is_dir():
        return result
    for base, dirs, files in os.walk(root, followlinks=False):
        refuse_reparse(Path(base))
        # On Windows, a junction can still be followed by copytree even when
        # os.walk(..., followlinks=False) skips it. Reject before backup/launch.
        for name in dirs:
            refuse_reparse(Path(base) / name)
        for name in files:
            path = Path(base) / name
            refuse_reparse(path)
            rel = path.relative_to(root).as_posix()
            stat = path.stat()
            result[rel] = {'sha256': _sha256(path), 'size': stat.st_size, 'mtime': stat.st_mtime}
    return result


def snapshot(realms, label: str, low: Path | None = None, out_root: Path = CURRENT_USER_ROOT) -> Path:
    """Hash, back up and export registry. Returns the snapshot directory (contains manifest.json)."""
    low = low or local_low()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    snap = out_root / 'Backups' / f'{stamp}-{label}'
    snap.mkdir(parents=True, exist_ok=False)
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'label': label,
                'local_low': str(low), 'realms': {}}
    for realm in realms:
        root = low / SAVE_DIRS[realm]
        files = hash_tree(root)
        if root.is_dir():
            shutil.copytree(root, snap / realm, symlinks=True)
        # Verify the backup byte-for-byte before anything else is allowed to run.
        backed = hash_tree(snap / realm)
        if {k: v['sha256'] for k, v in backed.items()} != {k: v['sha256'] for k, v in files.items()}:
            raise RuntimeError(f'{realm} backup does not match source hashes; refusing to continue')
        reg_file = snap / f'{realm}.reg'
        registry_absent = False
        if os.name == 'nt':
            if not _registry_key_exists(REGISTRY_KEYS[realm]):
                # The game never wrote its PlayerPrefs on this account: the pre-run state is "no key".
                registry_absent = True
                (snap / f'{realm}.reg.absent').write_text('registry key absent before the run\n', encoding='utf-8')
            else:
                exported = subprocess.run(['reg.exe', 'export', REGISTRY_KEYS[realm], str(reg_file), '/y'],
                                          capture_output=True)
                if exported.returncode != 0 or not reg_file.is_file():
                    raise RuntimeError(f'{realm} registry export failed; refusing to launch')
        manifest['realms'][realm] = {'root': str(root), 'files': files,
                                     'registry_exported': os.name == 'nt' and not registry_absent,
                                     'registry_absent': registry_absent}
    (snap / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return snap


def compare(snap: Path, low: Path | None = None) -> dict:
    manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    low = low or Path(manifest['local_low'])
    report = {}
    for realm, info in manifest['realms'].items():
        before = info['files']
        after = hash_tree(low / SAVE_DIRS[realm])
        report[realm] = {
            'added': sorted(set(after) - set(before)),
            'removed': sorted(set(before) - set(after)),
            'modified': sorted(k for k in set(before) & set(after) if before[k]['sha256'] != after[k]['sha256']),
            'unchanged': len([k for k in set(before) & set(after) if before[k]['sha256'] == after[k]['sha256']]),
        }
    return report


def _is_volatile(rel: str) -> bool:
    return rel.lower().endswith(VOLATILE_SUFFIXES)


def _atomic_restore(backup: Path, target: Path, expected_hash: str) -> None:
    """Stage restored bytes in the target directory, then swap the name atomically.

    Never copy onto a live save inode: it may have hard links, or a copy error may
    truncate the only live file. Keep the verified original backup intact.
    """
    refuse_reparse(backup)
    refuse_reparse(target)
    if _sha256(backup) != expected_hash:
        raise RuntimeError(f'Backup corrupted: {backup}')
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.slimebridge-restore-', delete=False) as out:
            staged = Path(out.name)
            with backup.open('rb') as source:
                shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        shutil.copystat(backup, staged)
        if _sha256(staged) != expected_hash:
            raise RuntimeError(f'Staged restore corrupted: {staged}')
        refuse_reparse(target)
        os.replace(staged, target)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


class RestoreIncomplete(RuntimeError):
    """Some files could not be restored; every other file was still attempted.

    `actions` records what did complete, `errors` one entry per failed file. Safety
    precondition failures (another game appeared) are NOT wrapped: they abort at once.
    """

    def __init__(self, actions: dict, errors: list[str]):
        super().__init__('; '.join(errors))
        self.actions = actions
        self.errors = errors


def _evidence_holds(evidence_dir: Path, rel: str, digest: str) -> bool:
    """True when the primary capture or any later versioned capture has these bytes."""
    primary = evidence_dir / rel
    candidates = [primary, *primary.parent.glob(primary.name + '.*.capture')] if primary.parent.is_dir() else []
    for candidate in candidates:
        refuse_reparse(candidate)
        if candidate.is_file() and _sha256(candidate) == digest:
            return True
    return False


def _capture_evidence(src: Path, evidence_dir: Path, rel: str) -> None:
    """Preserve the live post-run bytes without ever overwriting an earlier capture.

    A retry after a partial rollback sees already-restored originals in the live folder;
    copying those over the first capture would destroy the run's only evidence.
    """
    digest = _sha256(src)
    if _evidence_holds(evidence_dir, rel, digest):
        return
    primary = evidence_dir / rel
    primary.parent.mkdir(parents=True, exist_ok=True)
    refuse_reparse(primary.parent)
    target = primary
    if primary.exists():
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        target = primary.with_name(f'{primary.name}.{stamp}.capture')
    with src.open('rb') as reader, open(target, 'xb') as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(src, target)
    if _sha256(target) != digest:
        raise RuntimeError(f'Post-run evidence changed while copying: {rel}')


def restore_originals(snap: Path, report: dict, low: Path | None = None, assert_safe=None) -> dict:
    """Restore modified/removed pre-existing files; move newly added non-log files into evidence.

    The post-run version of every touched file is first preserved under <snap>/post-run/,
    never overwriting an earlier capture. The operation is replay-safe with the original
    report: already-restored files are verified, already-moved files are recognized. One
    file's failure does not stop the others; failures are raised together as RestoreIncomplete.
    """
    manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    low = low or Path(manifest['local_low'])
    if assert_safe is None:
        from tools.lab.process_guard import assert_no_other_game_processes
        assert_safe = assert_no_other_game_processes
    assert_safe()
    actions: dict[str, list] = {}
    errors: list[str] = []
    for realm, changes in report.items():
        root = low / SAVE_DIRS[realm]
        evidence = snap / 'post-run' / realm
        expected = manifest['realms'][realm]['files']
        done: list = []
        actions[realm] = done
        captured = set()
        for rel in changes['modified'] + changes['added']:
            if _is_volatile(rel):
                continue
            assert_safe()
            src = root / rel
            try:
                refuse_reparse(src)
                # A live file already back at its verified pre-run bytes (a retry after a
                # partial rollback) holds no run evidence; the backup already preserves it.
                already_original = (rel in expected and src.is_file() and
                                    _sha256(src) == expected[rel]['sha256'])
                if src.is_file() and not already_original:
                    _capture_evidence(src, evidence, rel)
                captured.add(rel)
            except Exception as ex:
                errors.append(f'{realm}/{rel}: evidence {type(ex).__name__}: {ex}')
        for rel in changes['modified'] + changes['removed']:
            if _is_volatile(rel):
                continue
            assert_safe()
            target = root / rel
            try:
                if rel in changes['modified'] and rel not in captured:
                    raise RuntimeError('post-run evidence was not preserved; refusing to overwrite')
                refuse_reparse(target)
                if target.is_file() and _sha256(target) == expected[rel]['sha256']:
                    done.append(('already_restored', rel))
                    continue
                _atomic_restore(snap / realm / rel, target, expected[rel]['sha256'])
                done.append(('restored', rel))
            except Exception as ex:
                errors.append(f'{realm}/{rel}: {type(ex).__name__}: {ex}')
        for rel in changes['added']:
            if _is_volatile(rel):
                continue
            assert_safe()
            live = root / rel
            try:
                refuse_reparse(live)
                if not live.exists():
                    if not (evidence / rel).is_file():
                        raise RuntimeError('added file is gone and no evidence capture exists')
                    done.append(('already_moved_to_evidence', rel))
                    continue
                if rel not in captured or not _evidence_holds(evidence, rel, _sha256(live)):
                    raise RuntimeError('no verified evidence capture; leaving the live file in place')
                live.unlink()
                done.append(('moved_to_evidence', rel))
            except Exception as ex:
                errors.append(f'{realm}/{rel}: {type(ex).__name__}: {ex}')
    if errors:
        raise RestoreIncomplete(actions, errors)
    return actions


def _reg_records(path: Path) -> set[str]:
    """Canonical '<[key]>\\t<value>' records of a reg.exe export (UTF-16).

    Each value stays bound to the subkey section it appears in, and reg.exe's
    backslash-continued hex lines are joined, so swapping same-named values between
    subkeys or changing one hex byte cannot compare equal. A truncated continuation
    or a value outside any section is refused rather than silently dropped.
    """
    if not path.is_file():
        return set()
    text = path.read_bytes().decode('utf-16')
    records: set[str] = set()
    section = None
    pending = None
    for raw in text.splitlines()[1:]:
        line = raw.strip()
        if pending is not None:
            if not line:
                raise ValueError(f'truncated continuation in registry export: {path}')
            pending += line
            if pending.endswith('\\'):
                pending = pending[:-1]
                continue
            records.add(f'{section}\t{pending}')
            pending = None
            continue
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line
            records.add(section)
            continue
        if section is None:
            raise ValueError(f'registry value outside any key section: {path}')
        if line.endswith('\\'):
            pending = line[:-1]
            continue
        records.add(f'{section}\t{line}')
    if pending is not None:
        raise ValueError(f'truncated continuation in registry export: {path}')
    return records


def registry_changes(snap: Path) -> dict:
    """Export each realm's HKCU key after the run and diff it against the pre-run export."""
    manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    out = {}
    (snap / 'post-run').mkdir(exist_ok=True)
    for realm in manifest['realms']:
        after = snap / 'post-run' / f'{realm}.reg'
        if manifest['realms'][realm].get('registry_absent'):
            if not _registry_key_exists(REGISTRY_KEYS[realm]):
                out[realm] = {'removed_or_changed': [], 'added_or_changed': [], 'absent_before': True}
                continue
            exported = subprocess.run(['reg.exe', 'export', REGISTRY_KEYS[realm], str(after), '/y'], capture_output=True)
            if exported.returncode != 0 or not after.is_file():
                raise RuntimeError(f'{realm} post-run registry export failed; keep backup for manual reconciliation')
            out[realm] = {'removed_or_changed': [], 'added_or_changed': sorted(_reg_records(after))[:50] or ['<key created>'],
                          'absent_before': True}
            continue
        exported = subprocess.run(['reg.exe', 'export', REGISTRY_KEYS[realm], str(after), '/y'], capture_output=True)
        if exported.returncode != 0 or not after.is_file():
            raise RuntimeError(f'{realm} post-run registry export failed; keep backup for manual reconciliation')
        before_v, after_v = _reg_records(snap / f'{realm}.reg'), _reg_records(after)
        out[realm] = {'removed_or_changed': sorted(before_v - after_v)[:50],
                      'added_or_changed': sorted(after_v - before_v)[:50]}
    return out


def restore_registry(snap: Path, changes: dict) -> dict:
    """Re-import prior values, then verify a fresh export (imports do not remove new values)."""
    done = {}
    for realm, diff in changes.items():
        if diff.get('absent_before'):
            # The key did not exist before the run: the evidence export was taken in registry_changes, now remove the new key.
            if diff['added_or_changed']:
                removed = subprocess.run(['reg.exe', 'delete', REGISTRY_KEYS[realm], '/f'], capture_output=True)
                done[realm] = ('removed-new-key' if removed.returncode == 0 and not _registry_key_exists(REGISTRY_KEYS[realm])
                               else f'could not remove the key created by the run ({removed.returncode})')
            continue
        backup = snap / f'{realm}.reg'
        if not backup.is_file():
            raise RuntimeError(f'{realm} registry backup missing; cannot restore')
        if diff['removed_or_changed'] or diff['added_or_changed']:
            proc = subprocess.run(['reg.exe', 'import', str(backup)], capture_output=True)
            if proc.returncode != 0:
                done[realm] = f'import failed ({proc.returncode})'
                continue
            verify = snap / 'post-run' / f'{realm}-after-restore.reg'
            exported = subprocess.run(['reg.exe', 'export', REGISTRY_KEYS[realm], str(verify), '/y'],
                                      capture_output=True)
            if exported.returncode != 0 or not verify.is_file():
                done[realm] = 'verification export failed'
            else:
                done[realm] = ('reimported' if _reg_records(backup) == _reg_records(verify)
                               else 'still differs after import (new values may remain)')
    return done


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Back up / compare current-user SR1/SR2 save data')
    sub = parser.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('snapshot')
    s.add_argument('--realm', action='append', choices=sorted(SAVE_DIRS))
    s.add_argument('--label', default='manual')
    c = sub.add_parser('compare')
    c.add_argument('snapshot', type=Path)
    c.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.cmd == 'snapshot':
        print(snapshot(args.realm or sorted(SAVE_DIRS), args.label))
    else:
        report = compare(args.snapshot)
        print(json.dumps(report, indent=2))
        if args.restore:
            print(json.dumps(restore_originals(args.snapshot, report), indent=2))


if __name__ == '__main__':
    main()
