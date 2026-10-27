"""Asset extraction from the player's own installs, driven through the SlimeBridge-AssetConverter protocol.

The converter is a separate, local tool (``sbconv serve``: JSON lines on stdin/stdout). This module only
locates it, starts it as a child process, runs ``list`` -> ``convert`` -> ``verify`` -> ``export`` for the chosen
selection and reports progress. Nothing is uploaded, the game folders are only read, and every output stays under
the output folder the player chose. Results are never described as better than the tool's own statuses.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_NAME = 'SlimeBridge-AssetConverter'
DIRECTIONS = ('sr1-to-sr2', 'sr2-to-sr1', 'both')
PHASES = (('list', 'Counting species', 0.03), ('convert', 'Extracting and converting', 0.62),
          ('verify', 'Verifying', 0.20), ('export', 'Preparing the SlimeBridge caches', 0.15))
_KEY = re.compile(r'^\[([^\]]+)\]')


def tool_candidates(configured=None):
    out = []
    if configured:
        out.append(Path(configured))
    # The copy bundled with SlimeBridge wins over a separate checkout lying next to it.
    out += [REPO_ROOT / 'tools' / TOOL_NAME, REPO_ROOT.parent / TOOL_NAME, Path.home() / TOOL_NAME,
            Path.home() / 'Documents' / TOOL_NAME]
    return out


def locate_tool(configured=None):
    """{'found', 'path', 'python', 'reason'}: the tool folder (contains sbconv/__init__.py) and the Python to run it."""
    for folder in tool_candidates(configured):
        if (folder / 'sbconv' / '__init__.py').is_file():
            venv = folder / '.venv' / ('Scripts' if os.name == 'nt' else 'bin') / ('python.exe' if os.name == 'nt' else 'python')
            python = venv if venv.is_file() else Path(sys.executable)
            return {'found': True, 'path': str(folder), 'python': str(python), 'reason': None,
                    'own_environment': venv.is_file()}
    return {'found': False, 'path': None, 'python': None, 'own_environment': False,
            'reason': f'{TOOL_NAME} was not found next to the launcher: choose its folder.'}


def selected_games(direction):
    return {'sr1-to-sr2': ('SR1',), 'sr2-to-sr1': ('SR2',), 'both': ('SR1', 'SR2')}[direction]


LAST_LOG = REPO_ROOT / 'Lab' / 'Launcher' / 'last-extraction.log'

class ExtractJob:
    """One extraction run on a background thread. Safe to poll from the UI thread via ``snapshot``."""

    def __init__(self, tool, options, installs, output, popen=None, clock=time.monotonic):
        direction = options.get('direction', 'both')
        if direction not in DIRECTIONS:
            raise ValueError(f'direction must be one of {DIRECTIONS}')
        games = selected_games(direction)
        missing = [g for g in games if not installs.get(g)]
        if missing:
            raise ValueError(f'game folder needed for {", ".join(missing)}')
        self.id = uuid.uuid4().hex[:12]
        self.tool, self.installs, self.output = tool, installs, Path(output)
        self.direction, self.only_missing = direction, bool(options.get('only_missing', True))
        self.kind = options.get('kind', 'all') if options.get('kind', 'all') in ('all', 'base', 'largo') else 'all'
        self._popen, self._clock = popen or subprocess.Popen, clock
        self._lock = threading.Lock()
        self._proc = None
        self._cancel = threading.Event()
        self._thread = None
        self._logs = deque(maxlen=300)
        self._keys = set()
        self.state = {'status': 'QUEUED', 'phase': None, 'phase_label': None, 'progress': 0.0, 'total': None,
                      'done': 0, 'error': None, 'result': None, 'started': None, 'finished': None}

    # ---- UI-facing -------------------------------------------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name=f'extract-{self.id}', daemon=True)
        self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def cancel(self):
        self._cancel.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def snapshot(self, log_tail=60):
        with self._lock:
            return {'id': self.id, **json.loads(json.dumps(self.state, default=str)), 'logs': list(self._logs)[-log_tail:],
                    'options': {'direction': self.direction, 'only_missing': self.only_missing, 'kind': self.kind},
                    'output': str(self.output)}

    # ---- internals -------------------------------------------------------------------------------------------
    def _set(self, **changes):
        with self._lock:
            self.state.update(changes)

    def _log(self, message):
        with self._lock:
            self._logs.append(str(message)[:400])

    def _args(self, phase):
        args = {'direction': self.direction, 'all_species': True, 'kind': self.kind, 'only_missing': self.only_missing,
                'sr1_path': self.installs.get('SR1'), 'sr2_path': self.installs.get('SR2')}
        if phase == 'list':
            return {k: args[k] for k in ('sr1_path', 'sr2_path', 'kind')}
        return args

    def _request(self, rid, op, args):
        self._proc.stdin.write(json.dumps({'id': rid, 'op': op, 'args': args}) + '\n')
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError('the converter stopped unexpectedly')
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('id') != rid:
                continue
            kind = event.get('event')
            if kind == 'log':
                message = event.get('message', '')
                self._log(message)
                match = _KEY.match(message)
                if match and op == 'convert':
                    with self._lock:
                        self._keys.add(match.group(1))
                        done = len(self._keys)
                        total = self.state['total']
                    self._set(done=done)
                    if total:
                        self._set(progress=self._phase_base('convert') + min(1.0, done / total) * self._phase_weight('convert'))
            elif kind == 'result':
                return event
            elif kind == 'error':
                raise RuntimeError(event.get('error', 'converter error'))
            if self._cancel.is_set():
                raise InterruptedError('cancelled')

    @staticmethod
    def _phase_base(name):
        base = 0.0
        for key, _label, weight in PHASES:
            if key == name:
                return base
            base += weight
        return base

    @staticmethod
    def _phase_weight(name):
        return next(weight for key, _label, weight in PHASES if key == name)

    def _count_species(self, listing):
        games = selected_games(self.direction)
        rows = [r for r in listing.get('species', []) if r.get('game') in games]
        if self.only_missing:
            rows = [r for r in rows if not r.get('native_in_target')]
        if self.kind != 'all':
            rows = [r for r in rows if r.get('kind') == self.kind]
        return len(rows)

    def _run(self):
        self._set(status='RUNNING', started=time.time())
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            command = [self.tool['python'], '-m', 'sbconv.cli', 'serve', '--output', str(self.output)]
            kwargs = dict(cwd=self.tool['path'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding='utf-8', bufsize=1)
            if os.name == 'nt':
                kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            self._proc = self._popen(command, **kwargs)
            hello = self._proc.stdout.readline()
            if '"hello"' not in hello:
                raise RuntimeError('the converter did not answer: ' + hello.strip()[:200])
            summary = {}
            for phase, label, _weight in PHASES:
                if self._cancel.is_set():
                    raise InterruptedError('cancelled')
                self._set(phase=phase, phase_label=label, progress=self._phase_base(phase))
                event = self._request(f'{self.id}-{phase}', phase, self._args(phase))
                result = event.get('result') or {}
                if phase == 'list':
                    self._set(total=self._count_species(result))
                elif phase == 'convert':
                    summary['convert'] = _counts(result)
                    if result.get('blocked'):
                        raise RuntimeError(result.get('error') or 'the converter refused this game build')
                elif phase == 'verify':
                    summary['verify'] = _counts(result)
                elif phase == 'export':
                    summary['export'] = _counts(result)
                self._set(progress=self._phase_base(phase) + self._phase_weight(phase))
            status = self._request(f'{self.id}-status', 'status', {}).get('result') or {}
            summary['ledger'] = status.get('summary', {})
            self._set(status='DONE', progress=1.0, result=summary, finished=time.time())
        except InterruptedError:
            self._set(status='CANCELLED', finished=time.time())
        except Exception as exc:  # noqa: BLE001 - reported to the player, never swallowed
            self._set(status='FAILED', error=f'{type(exc).__name__}: {exc}', finished=time.time())
        finally:
            self._shutdown()

    def _shutdown(self):
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------------------------------------------------
# Built-in engine: the repository's own SR2 -> SR1 species pipeline (bridge.content.species_pipeline), run in-process.
# One code path: a fix to the extractor / pipeline is a fix to the launcher's Extract button, no external tool involved.
# ---------------------------------------------------------------------------------------------------------------------
ENGINES = ('builtin', 'external')


def builtin_tool():
    return {'found': True, 'path': 'built-in (bridge.content.species_pipeline)', 'python': sys.executable,
            'own_environment': True, 'reason': None, 'builtin': True}


def verify_caches(cache_root, canonicals, direction='sr2-to-sr1'):
    """Re-hash every manifest file of the given species caches: {'ok', 'count', 'by_status', 'problems'}."""
    tally, problems = {}, []
    for canonical in canonicals:
        folder = Path(cache_root) / direction / canonical
        status = 'VERIFIED'
        try:
            files = json.loads((folder / 'cache-manifest.json').read_text(encoding='utf-8'))['files']
            for name, expected in files.items():
                digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
                if digest.lower() != str(expected).lower():
                    status = 'HASH_MISMATCH'
                    problems.append(f'{canonical}/{name}')
                    break
        except (OSError, ValueError, KeyError):
            status = 'MISSING'
        tally[status] = tally.get(status, 0) + 1
    return {'ok': set(tally) <= {'VERIFIED'}, 'count': sum(tally.values()), 'by_status': tally, 'problems': problems}


MIN_RAM_GB, MAX_RAM_GB, DEFAULT_RAM_GB = 2, 128, 8


class BuiltinExtractJob:
    """Same surface as ExtractJob (start/join/cancel/snapshot) but in-process: species list -> extract -> verify -> done."""

    def __init__(self, options, installs, cache_dir, run=None, roster=None, verify=None, largos=None, run_sr1=None, roster_sr1=None):
        self.id = uuid.uuid4().hex[:12]
        self.options = options
        self.sr1_root = installs.get('SR1')
        self.sr2_root = installs.get('SR2')
        self.cache_dir = Path(cache_dir)
        self.output = self.cache_dir
        self.only_missing = bool(options.get('only_missing', True))
        self.kind = options.get('kind', 'all') if options.get('kind', 'all') in ('all', 'base', 'largo') else 'all'
        self.direction = options.get('direction', 'both')
        if self.direction not in DIRECTIONS:
            raise ValueError(f'direction must be one of {DIRECTIONS}')
        ram = options.get('ram_gb')
        if ram is not None and (isinstance(ram, bool) or not isinstance(ram, (int, float)) or not MIN_RAM_GB <= ram <= MAX_RAM_GB):
            raise ValueError(f'ram_gb must be between {MIN_RAM_GB} and {MAX_RAM_GB}')
        self.ram_gb = ram
        if not self.sr2_root:
            raise ValueError('game folder needed for SR2')
        if self.direction == 'sr1-to-sr2' and not self.sr1_root:
            raise ValueError('game folder needed for SR1')
        self._run_fn, self._roster_fn, self._verify_fn, self._largo_fn = run, roster, verify, largos
        self._run_sr1_fn, self._roster_sr1_fn = run_sr1, roster_sr1
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._logs = deque(maxlen=300)
        self.state = {'status': 'QUEUED', 'phase': None, 'phase_label': None, 'progress': 0.0, 'total': None,
                      'done': 0, 'error': None, 'result': None, 'started': None, 'finished': None}

    def start(self):
        self._thread = threading.Thread(target=self._work, name=f'extract-builtin-{self.id}', daemon=True)
        self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def cancel(self):
        self._cancel.set()

    def snapshot(self, log_tail=60):
        with self._lock:
            return {'id': self.id, **json.loads(json.dumps(self.state, default=str)), 'logs': list(self._logs)[-log_tail:],
                    'options': {'direction': self.direction, 'only_missing': self.only_missing, 'kind': self.kind,
                                'engine': 'builtin'},
                    'output': str(self.output)}

    def _set(self, **changes):
        with self._lock:
            self.state.update(changes)

    def _run_worker(self, rows, data_dir, progress, mode='sr2-to-sr1'):
        """The pipeline in its own process under the memory limit; progress arrives as `@@{json}` lines on its stdout."""
        import subprocess
        import sys
        request = self.cache_dir / '.extract-request.json'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        request.write_text(json.dumps({'mode': mode, 'rows': rows, 'sr2_dir': str(data_dir), 'sr1_dir': str(self._sr1_data()),
                                       'cache_root': str(self.cache_dir), 'force': not self.only_missing, 'ram_gb': self.ram_gb}),
                           encoding='utf-8')
        repo = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING='utf-8')
        # Below-normal priority: an extraction running next to a game must never take the frames the game needs.
        low_priority = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        process = subprocess.Popen([sys.executable, '-m', 'bridge.content.extract_worker', str(request)], cwd=str(repo), env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                   creationflags=low_priority)
        outcome, fatal, tail = None, None, deque(maxlen=12)
        try:
            for line in process.stdout:
                if self._cancel.is_set():
                    process.kill()
                    raise InterruptedError('cancelled')
                line = line.rstrip()
                if not line.startswith('@@'):
                    if line:
                        tail.append(line[:300])
                    continue
                try:
                    event = json.loads(line[2:])
                except ValueError:
                    continue
                if event.get('event') == 'progress':
                    progress(event.get('done', 0), event.get('total', 1), event.get('message', ''))
                elif event.get('event') == 'start':
                    self._log('extraction worker started' + (f" (memory limit {event['ram_gb']:g} GB)" if event.get('ram_gb') else ''))
                elif event.get('event') == 'result':
                    outcome = event['outcome']
                elif event.get('event') == 'fatal':
                    fatal = event.get('error')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            try:
                request.unlink()
            except OSError:
                pass
        if fatal:
            raise RuntimeError(fatal)
        if outcome is None:
            raise RuntimeError(f'the extraction worker stopped (exit code {process.returncode}), most likely out of memory at the '
                               + (f'{self.ram_gb:g} GB limit: raise the memory limit. ' if self.ram_gb else 'memory available. ') + ' | '.join(tail))
        return outcome

    def _sr1_data(self):
        from bridge.content import largos
        return Path(self.sr1_root) / 'SlimeRancher_Data' if self.sr1_root else largos.DEFAULT_SR1_DATA

    def _largo_rows(self, data_dir):
        """The Largos SR2 has and SR1 lacks whose parents exist in SR1 (READY rows only); never fatal: species still extract."""
        try:
            from bridge.content import largos
            rows = (self._largo_fn or (lambda: largos.largo_roster(data_dir, self._sr1_data())))()
        except Exception as exc:  # noqa: BLE001 - reported, the species roster must not depend on it
            self._log(f'Largos not listed: {type(exc).__name__}: {exc}')
            return []
        skipped = [r for r in rows if r.get('status') != 'READY']
        if skipped:
            self._log(f'{len(skipped)} Largos wait for a parent species SR1 does not have yet (for example {skipped[0]["canonical"]})')
        return [r for r in rows if r.get('status') == 'READY']

    def _publish_largo_spec(self, summary):
        try:
            from bridge.content import largos
            published = largos.publish_spec(cache_root=self.cache_dir, sr2_dir=Path(self.sr2_root) / 'SlimeRancher2_Data',
                                            sr1_dir=self._sr1_data())
            summary['largo_spec'] = {'ok': True, 'largos': published['largos'], 'directory': published['directory']}
            self._log(f"Largo spec published: {published['largos']} Largos ({Path(published['directory']).name})")
        except Exception as exc:  # noqa: BLE001
            summary['largo_spec'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
            self._log(f'Largo spec not published: {type(exc).__name__}: {exc}')

    def _log(self, message):
        with self._lock:
            self._logs.append(str(message)[:400])
        try:
            with LAST_LOG.open('a', encoding='utf-8') as f:
                f.write(str(message)[:400] + '\n')
        except OSError:
            pass

    def _sr1_rows(self, data_dir):
        """What SR1 has and SR2 lacks (species and Largos); never fatal."""
        try:
            from bridge.content import sr1_pipeline
            rows = (self._roster_sr1_fn or (lambda: sr1_pipeline.roster(self._sr1_data(), data_dir)))()
        except Exception as exc:  # noqa: BLE001 - reported, the other direction must not depend on it
            self._log(f'SR1 content not listed: {type(exc).__name__}: {exc}')
            return []
        return [r for r in rows if self.kind == 'all' or r.get('kind') == ('largo' if self.kind == 'largo' else 'base')]

    def _publish_sr2_spec(self, summary):
        try:
            from bridge.content import sr1_pipeline
            published = sr1_pipeline.publish_spec(cache_root=self.cache_dir, sr1_dir=self._sr1_data(),
                                                  sr2_dir=Path(self.sr2_root) / 'SlimeRancher2_Data')
            summary['sr2_spec'] = {'ok': True, 'largos': published['largos'], 'species': published['species'],
                                   'directory': published['directory']}
            self._log(f"SR2 spec published: {published['species']} species, {published['largos']} Largos ({Path(published['directory']).name})")
        except Exception as exc:  # noqa: BLE001
            summary['sr2_spec'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
            self._log(f'SR2 spec not published: {type(exc).__name__}: {exc}')

    def _work(self):
        from bridge.content import spec_seed, species_pipeline as pipeline
        try:
            LAST_LOG.parent.mkdir(parents=True, exist_ok=True)
            LAST_LOG.write_text(f"extraction {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding='utf-8')
        except OSError:
            pass
        spec_seed.ensure_specs(REPO_ROOT)
        self._set(status='RUNNING', started=time.time())
        try:
            from apps.launcher import integrity
            for game, root in (('SR2', self.sr2_root), ('SR1', self.sr1_root)):
                if root:
                    try:
                        integrity.require(game, root)
                    except integrity.IncompleteGame as exc:
                        if exc.state['missing_count'] and integrity.repair_candidates(game, root):
                            self._log(f'{game} folder incomplete: restoring the missing files from the game archives found on this PC')
                            fixed = integrity.repair(game, root, progress=lambda n: self._log(f'  {n} files restored'))
                            self._log(f"  {fixed['restored']} files restored")
                            if fixed['state']['complete']:
                                continue
                            exc = integrity.IncompleteGame(game, root, fixed['state'])
                        self._log(str(exc))
                        self._set(status='FAILED', error=str(exc), finished=time.time())
                        return
            self._set(phase='list', phase_label='Counting species', progress=0.02)
            data_dir = Path(self.sr2_root) / 'SlimeRancher2_Data'
            if self.sr2_root:
                from bridge.content import sr2_schema
                try:
                    sr2_schema.ensure(self.sr2_root, log=self._log)
                except Exception as exc:  # noqa: BLE001 - species will say why they fail; SR1 -> SR2 still runs
                    self._log(f'SR2 code schema: {exc}')
            plan = []                                        # (mode, rows): SR2 -> SR1 first, then SR1 -> SR2
            largo_rows = []
            if self.direction in ('sr2-to-sr1', 'both'):
                rows = (self._roster_fn or pipeline.roster)() if self.kind in ('all', 'base') else []
                largo_rows = self._largo_rows(data_dir) if self.kind in ('all', 'largo') else []
                plan.append(('sr2-to-sr1', list(rows) + largo_rows))
                self._log(f'{len(rows)} species and {len(largo_rows)} Largos (SR2 Largos SR1 lacks) in the SR2 -> SR1 roster')
            if self.direction == 'both' and not self.sr1_root:
                self._log('no Slime Rancher folder set: only SR2 -> SR1 content is extracted')
            if self.direction == 'sr1-to-sr2' or (self.direction == 'both' and self.sr1_root):
                sr1_rows = self._sr1_rows(data_dir)
                plan.append(('sr1-to-sr2', sr1_rows))
                self._log(f'{sum(1 for r in sr1_rows if r.get("kind") == "base")} species and '
                          f'{sum(1 for r in sr1_rows if r.get("kind") == "largo")} Largos (SR1 content SR2 lacks) in the SR1 -> SR2 roster')
            total_rows = sum(len(rows) for _, rows in plan)
            self._set(total=total_rows)
            offset = 0
            outcomes = []
            self._set(phase='convert', phase_label='Extracting and converting', progress=0.05)
            for mode, rows in plan:
                def progress(done, total, message, base=offset):
                    self._log(message)
                    self._set(done=base + done, progress=0.05 + 0.65 * ((base + done) / max(total_rows, 1)))

                if mode == 'sr2-to-sr1' and self._run_fn is not None:
                    outcome = self._run_fn(rows, data_dir, self.cache_dir, force=not self.only_missing, progress=progress,
                                           cancelled=self._cancel.is_set)
                elif mode == 'sr1-to-sr2' and self._run_sr1_fn is not None:
                    outcome = self._run_sr1_fn(rows, self._sr1_data(), self.cache_dir, force=not self.only_missing, progress=progress,
                                               cancelled=self._cancel.is_set)
                else:
                    outcome = self._run_worker(rows, data_dir, progress, mode)
                outcomes.append((mode, outcome))
                offset += len(rows)
            species = [dict(r, direction=mode) for mode, o in outcomes for r in o.get('species', [])]
            by_status = {}
            for _, o in outcomes:
                for key, value in o.get('by_status', {}).items():
                    by_status[key] = by_status.get(key, 0) + value
            outcome = {'ok': all(o.get('ok', True) for _, o in outcomes), 'count': len(species), 'by_status': by_status, 'species': species}
            summary = {'convert': {'ok': outcome['ok'], 'count': outcome['count'], 'by_status': by_status}}
            self._set(phase='verify', phase_label='Verifying', progress=0.75)
            verified = []
            for mode in ('sr2-to-sr1', 'sr1-to-sr2'):
                good = [r['canonical'] for r in species if r.get('direction') == mode and r.get('status') not in ('FAILED', 'SKIPPED_RAM')]
                if self._verify_fn is not None:
                    if mode == 'sr2-to-sr1':
                        verified.append(self._verify_fn(self.cache_dir, good))
                elif good or mode == 'sr2-to-sr1':
                    verified.append(verify_caches(self.cache_dir, good, mode))
            by_verify = {}
            for v in verified:
                for key, value in v['by_status'].items():
                    by_verify[key] = by_verify.get(key, 0) + value
            summary['verify'] = {'ok': all(v['ok'] for v in verified), 'count': sum(v['count'] for v in verified), 'by_status': by_verify}
            skipped = [r for r in species if r.get('status') == 'SKIPPED_RAM']
            if skipped:
                self._log(f'{len(skipped)} items need more memory than the {self.ram_gb or 0:g} GB limit allows: raise it and run Extract again')
                summary['skipped_ram'] = {'count': len(skipped), 'first': skipped[0].get('error')}
            for row in species:
                for note in row.get('notes', []):
                    self._log(f"[{row['canonical']}] note: {note}")
                if row.get('status') == 'FAILED':
                    self._log(f"[{row['canonical']}] FAILED: {row.get('error')}")
            self._set(phase='export', phase_label='Preparing the SlimeBridge caches', progress=0.95)
            built = sum(1 for r in species if r.get('status') in ('BUILT', 'UP_TO_DATE'))
            if largo_rows and any(r.get('status') in ('BUILT', 'UP_TO_DATE') for r in species
                                  if r.get('direction') == 'sr2-to-sr1' and str(r.get('canonical', '')).startswith('largo.')):
                self._publish_largo_spec(summary)
            if any(r.get('status') in ('BUILT', 'UP_TO_DATE') for r in species if r.get('direction') == 'sr1-to-sr2'):
                self._publish_sr2_spec(summary)
            summary['export'] = {'ok': True, 'count': built, 'by_status': {'READY_IN_CACHE': built}}
            summary['ledger'] = {'cache_root': str(self.cache_dir / ('sr1-to-sr2' if self.direction == 'sr1-to-sr2' else 'sr2-to-sr1'))}
            failed = outcome.get('by_status', {}).get('FAILED', 0)
            if failed:
                self._set(status='FAILED', error=f'{failed} species failed (see the log)', result=summary,
                          finished=time.time())
            else:
                self._set(status='DONE', progress=1.0, result=summary, finished=time.time())
        except InterruptedError:
            self._set(status='CANCELLED', finished=time.time())
        except Exception as exc:  # noqa: BLE001 - reported to the player, never swallowed
            self._set(status='FAILED', error=f'{type(exc).__name__}: {exc}', finished=time.time())


def _counts(result):
    rows = result.get('results') or result.get('species') or []
    tally = {}
    for row in rows:
        key = row.get('status') or ('OK' if row.get('ok') else 'FAILED')
        tally[key] = tally.get(key, 0) + 1
    return {'ok': bool(result.get('ok', True)), 'count': len(rows), 'by_status': tally}


def install_cache(output, destination):
    """Copy the SlimeBridge caches built by the converter into the folder the plugins read (``<dest>/<direction>/<id>``).

    Only complete cache folders (with a manifest) are copied; existing ones are replaced atomically per species.
    """
    import shutil
    source_root = Path(output) / 'bridge-cache'
    destination = Path(destination)
    copied = []
    if not source_root.is_dir():
        return {'copied': 0, 'skipped': 0, 'destination': str(destination)}
    skipped = 0
    for direction_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
        target_dir = destination / direction_dir.name.lower()
        for species in sorted(p for p in direction_dir.iterdir() if p.is_dir()):
            if not any(species.glob('*manifest*.json')):
                skipped += 1
                continue
            target = target_dir / species.name
            temp = target_dir / f'.{species.name}.tmp'
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(temp, ignore_errors=True)
            shutil.copytree(species, temp)
            shutil.rmtree(target, ignore_errors=True)
            os.replace(temp, target)
            copied.append(f'{direction_dir.name}/{species.name}')
    return {'copied': len(copied), 'skipped': skipped, 'destination': str(destination), 'items': copied[:200]}
