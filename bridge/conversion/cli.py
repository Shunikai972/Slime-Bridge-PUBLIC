"""slimebridge-content: local content pipeline commands (``python -m bridge.conversion.cli``).

Inputs are ``bridge-content-source`` manifests (synthetic fixtures today; a local exporter
over user-owned installs later) and data profiles. ``--game-root`` selects the native
provider, which in this build fails closed with BLOCKED_ON_LOCAL_QUERY (exit 3) instead of
pretending. Outputs are metadata-only JSON written atomically under this checkout's ignored
``Lab/GeneratedContent``; inputs are never modified.

``source-cache`` materializes the complete offline source cache (geometry + face + icon +
clips) through the pinned extractor; the clip selection must be explicit in the job (or the
job file) or the command refuses, exit 5.

Exit codes: 0 ok, 2 usage, 3 needs local runtime/native provider, 4 invalid input,
5 blocked: explicit source-cache selection refused or coverage incomplete.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from bridge.cache.fs import atomic_write, confined_root
from bridge.cache.store import ContentCache
from bridge.content.canonical import REALMS, pretty_json
from bridge.content.discovery import discover
from bridge.content.scan import read_runtime_snapshot
from bridge.registry.reconcile import classify_species, load_aliases
from bridge.registry.snapshot import normalize_snapshot, snapshot_from_scan
from . import source_cache
from .batch import render_text, run_batch
from .engine import SourceBundle, convert_species, species_row
from .extraction import SyntheticExtractionProvider, plan_extraction, validate_receipt
from .manifest import ManifestSource
from .materials import AttributeMaterialMapper
from .providers import NativeInstallProvider, NotAvailableInCloud
from .selection import select_first
from .targets import builtin_target_profile

REPO = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO / 'Lab' / 'GeneratedContent'
EXIT_LOCAL, EXIT_INPUT, EXIT_BLOCKED = 3, 4, 5


class InputError(ValueError):
    pass


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InputError(f'cannot read {path}: {exc}') from exc


def _out_dir(args, default):
    root = confined_root(OUTPUT_ROOT)
    out = confined_root(args.out_dir) if args.out_dir else root / 'content-pipeline' / default
    if not out.is_relative_to(root):
        raise InputError(f'--out-dir must stay under {root} (ignored, never committed)')
    return root, out


def _write(args, default, files):
    root, out = _out_dir(args, default)
    for name, value in sorted(files.items()):
        data = value if isinstance(value, str) else pretty_json(value)
        atomic_write(root, out / name, data.encode('utf-8'))
    return str(out)


def _anchored_registry(args, realm):
    """Runtime evidence only through the existing scanner gate (independent build hash + receipt)."""
    r = realm.lower()
    snapshot = getattr(args, f'{r}_runtime_snapshot', None)
    if not snapshot:
        return None
    build = getattr(args, f'{r}_active_build', None)
    if not build:
        raise InputError(f'--{r}-runtime-snapshot needs --{r}-active-build (the build file the reader hashes itself)')
    receipt = getattr(args, f'{r}_traversal_receipt', None)
    scan = read_runtime_snapshot(Path(snapshot), active_build=Path(build),
                                 traversal_anchor=_json(receipt) if receipt else None)
    if scan['realm'] != realm:
        raise InputError(f'runtime snapshot realm {scan["realm"]} given for {realm}')
    return snapshot_from_scan(scan)


def _provider(args, realm):
    manifest = getattr(args, realm.lower(), None)
    game_root = getattr(args, f'{realm.lower()}_game_root', None)
    if game_root:
        return NativeInstallProvider(realm, game_root)
    if not manifest:
        raise InputError(f'--{realm.lower()} manifest or --{realm.lower()}-game-root is required')
    try:
        return ManifestSource(_json(manifest), registry=_anchored_registry(args, realm))
    except ValueError as exc:
        raise InputError(str(exc)) from exc


def _bundle(args, realm):
    provider = _provider(args, realm)
    if not args.material_profile or not args.behaviour_profile:
        raise InputError('--material-profile and --behaviour-profile (source realm data profiles) are required')
    return SourceBundle(provider, AttributeMaterialMapper(realm), _json(args.material_profile),
                        _json(args.behaviour_profile))


def _target(args):
    if args.target_profile in (None, 'builtin'):
        return builtin_target_profile(args.to)
    profile = _json(args.target_profile)
    if profile.get('realm') != args.to:
        raise InputError('target profile realm does not match --to')
    return profile


def _classification(args):
    aliases = load_aliases(_json(args.aliases)) if getattr(args, 'aliases', None) else None
    return classify_species(_provider(args, 'SR1').read_registry(), _provider(args, 'SR2').read_registry(),
                            aliases=aliases)


def cmd_scan(args):
    provider = NativeInstallProvider(args.realm, args.game_root) if args.game_root else \
        ManifestSource(_json(args.source))
    snapshot = normalize_snapshot(provider.read_registry())
    if snapshot['realm'] != args.realm:
        raise InputError('registry realm does not match --realm')
    out = _write(args, f'scan-{args.realm.lower()}', {f'{args.realm.lower()}-registry.json': snapshot})
    return {'realm': args.realm, 'entries': len(snapshot['entries']),
            'completeness': snapshot['traversal']['completeness'], 'evidence': snapshot['data_evidence'],
            'diagnostics': len(snapshot['diagnostics']), 'output': out}


def cmd_classify(args):
    result = _classification(args)
    out = _write(args, 'classify', {'classification.json': result})
    return {'counts': result['counts'], 'synthetic': result['synthetic'], 'output': out}


_SPECIES = re.compile(r'^slime\.[a-z0-9]+(?:_[a-z0-9]+)*$')


def _species_arg(args):
    """Species ids become file names: only the canonical slime id pattern is accepted."""
    if not _SPECIES.fullmatch(args.species):
        raise InputError(f'--species must be a canonical slime id (slime.<slug>), got {args.species!r}')
    return args.species


def _species_context(args):
    _species_arg(args)
    classification = _classification(args)
    source = _provider(args, getattr(args, 'from'))
    row = species_row(classification, args.species)
    native = row['native'].get(source.realm)
    if native is None:
        raise InputError(f'{args.species} has no {source.realm} native entry')
    return classification, source, f'{source.realm}:{source.definition_root(native)}'


def cmd_plan_extraction(args):
    _, source, root = _species_context(args)
    found = discover(source, [root.split(':', 1)[1]])
    plan = plan_extraction(found['graph'], root)
    out = _write(args, 'extract', {f'{args.species}.extraction-plan.json': plan})
    return {'species': args.species, 'items': len(plan['items']), 'plan_hash': plan['plan_hash'], 'output': out}


def cmd_extract(args):
    _, source, root = _species_context(args)
    if not getattr(source, 'synthetic', False):
        raise NotAvailableInCloud('asset extraction from a user-owned install', ('Q016',))
    plan = plan_extraction(discover(source, [root.split(':', 1)[1]])['graph'], root)
    provider = SyntheticExtractionProvider(source.realm)
    receipts = [provider.extract(item) for item in plan['items']]
    problems = [d for r in receipts for d in validate_receipt(plan, r, synthetic_run=True)]
    out = _write(args, 'extract', {f'{args.species}.extraction-plan.json': plan,
                                   f'{args.species}.receipts.json': {'receipts': receipts, 'diagnostics': problems}})
    return {'species': args.species, 'receipts': len(receipts), 'invalid': len(problems), 'synthetic': True,
            'output': out}


def cmd_convert(args):
    _species_arg(args)
    classification = _classification(args)
    source = _bundle(args, getattr(args, 'from'))
    result = convert_species(args.species, classification=classification, source=source,
                             target_profile=_target(args))
    out = _write(args, 'convert', {f'{args.species}.{result["direction"].replace(">", "")}.json': result})
    return {'species': args.species, 'direction': result['direction'], 'final': result['final'],
            'components': result['components'], 'synthetic': result['synthetic'], 'output': out}


def cmd_batch(args):
    realm = getattr(args, 'from')
    source = _bundle(args, realm)
    destination = _provider(args, args.to)
    aliases = load_aliases(_json(args.aliases)) if args.aliases else None
    cache = None
    if not args.no_cache:
        root = confined_root(OUTPUT_ROOT)
        cache_dir = confined_root(args.cache_dir) if args.cache_dir else root / 'content-cache'
        if not cache_dir.is_relative_to(root):
            raise InputError(f'--cache-dir must stay under {root}')
        cache = ContentCache(cache_dir)
    out = run_batch(source=source, destination_registry=destination, target_profile=_target(args), cache=cache,
                    aliases=aliases, only=args.only or None)
    report = out['report']
    files = {'report.json': report, 'report.txt': render_text(report)}
    for cid, result in out['results'].items():
        files[f'results/{cid}.json'] = result
    written = _write(args, f"batch-{report['direction'].replace('->', '-to-').lower()}", files)
    return {'direction': report['direction'], 'counts': report['counts'], 'cache': report['cache'],
            'first_candidate': report['first_candidate']['candidate'], 'synthetic': report['synthetic'],
            'output': written}


def cmd_select_first(args):
    report = _json(args.report)
    if report.get('schema') != 'bridge-batch-report':
        raise InputError('expected a bridge-batch-report')
    return select_first(report['entries'], report['direction'])


def _source_cache_job(args):
    """Build the explicit source-cache job: flags, or one ``--job`` file (never both)."""
    if args.job:
        if any(getattr(args, field) not in (None, [], False) for field in
               ('canonical', 'asset_name', 'sr2_dir', 'cache_dir', 'clip_id', 'default_clip')):
            raise InputError('--job carries the whole explicit request; do not mix it with selection flags')
        payload = _json(args.job)
        if not isinstance(payload, dict):
            raise InputError('--job must be a JSON object')
        mode = payload.pop('visual_mode', None)
        if mode is not None and mode != 'SOURCE_COMPLETE':
            raise InputError(f'visual_mode {mode!r} is not supported by source-cache; request '
                             'SOURCE_COMPLETE (PLACEHOLDER_TEMPLATE stays on the template path)')
        return payload
    missing = [name for name, value in (('--canonical', args.canonical), ('--asset-name', args.asset_name),
                                        ('--sr2-dir', args.sr2_dir), ('--cache-dir', args.cache_dir)) if not value]
    if missing:
        raise InputError(f"{', '.join(missing)} required without --job")
    return {'canonical_id': args.canonical, 'species': args.asset_name, 'sr2_dir': args.sr2_dir,
            'cache_dir': args.cache_dir, 'clip_ids': args.clip_id, 'default_clip': args.default_clip,
            'allow_nonlab': args.allow_nonlab}


class _EventSourceCacheProvider(source_cache.SourceCacheProvider):
    """Additive --events observer of real provider hooks; no duplicate extraction engine.

    The verification interval begins after extraction and ends only on entry into publication:
    the unchanged provider has checked manifest hashes, source identity and complete coverage.
    """
    def __init__(self, *args, expected_source_files=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._active_stage = None
        self._verification_open = False
        self._expected_source_files = expected_source_files

    def _emit(self, stage, status, error=None, details=None):
        self._active_stage = stage if status == 'RUNNING' else None
        event = {'event': 'source-cache-stage', 'schema_version': 1, 'stage': stage, 'status': status}
        if error is not None:
            event['error'] = str(error)[:500]
        if details:
            event.update(details)
        print(json.dumps(event, sort_keys=True), flush=True)

    def _observe(self, stage, action, *args):
        self._emit(stage, 'RUNNING')
        try:
            value = action(*args)
        except BaseException as exc:
            self._emit(stage, 'FAILED', exc)
            raise
        self._emit(stage, 'OK')
        return value

    def _check_source(self, job):
        return self._observe('SOURCE_VALIDATION', super()._check_source, job)

    def _source_fingerprint(self, directory):
        self._emit('SOURCE_FINGERPRINT', 'RUNNING')
        try:
            files = super()._source_fingerprint(directory)
            if self._expected_source_files:
                current = {os.path.normcase(str(key)): str(value) for key, value in files.items()}
                pinned = {os.path.normcase(str(key)): str(value) for key, value in self._expected_source_files.items()}
                if current != pinned:
                    raise source_cache.SourceChanged(
                        'source files changed since the pinned attempt; select the source again or rebuild explicitly')
        except BaseException as exc:
            self._emit('SOURCE_FINGERPRINT', 'FAILED', exc)
            raise
        self._emit('SOURCE_FINGERPRINT', 'OK', details={'source_files': files})
        return files

    def _inspect(self, job, identity, job_hash):
        return self._observe('CACHE_INSPECTION', super()._inspect, job, identity, job_hash)

    def _runner(self):
        runner = super()._runner()
        return lambda request: self._observe('SOURCE_EXTRACTION', runner, request)

    def _check_runner_output(self, output, staging):
        self._verification_open = True
        self._emit('CACHE_VERIFICATION', 'RUNNING')
        return super()._check_runner_output(output, staging)

    def _build(self, *args):
        try:
            return super()._build(*args)
        except BaseException as exc:
            # The staged verification region (runner output, cache scan, source identity and
            # coverage) reports a FAILED stage even when the failure lands between hooks.
            stage = getattr(self, '_active_stage', None) or ('CACHE_VERIFICATION' if self._verification_open else None)
            if stage:
                self._emit(stage, 'FAILED', exc)
            raise
        finally:
            self._verification_open = False

    def _publish(self, staging, directory):
        self._verification_open = False
        self._emit('CACHE_VERIFICATION', 'OK')
        return self._observe('CACHE_PUBLICATION', super()._publish, staging, directory)

    def _write_receipt(self, directory, receipt):
        return self._observe('RECEIPT_WRITE', super()._write_receipt, directory, receipt)


def cmd_source_cache(args):
    job = _source_cache_job(args)
    expected = getattr(args, 'expect_source_files', None)
    if expected:
        if not getattr(args, 'events', False):
            raise InputError('--expect-source-files requires --events: the pinned check runs before extraction '
                             'inside the event provider')
        try:
            expected = json.loads(expected)
        except ValueError as exc:
            raise InputError(f'--expect-source-files must be JSON: {exc}') from exc
        if not isinstance(expected, dict) or not expected or any(not isinstance(value, str)
                                                                for value in expected.values()):
            raise InputError('--expect-source-files must be a non-empty JSON object mapping source paths to sha256')
    provider = _EventSourceCacheProvider(expected_source_files=expected) if getattr(args, 'events', False) else None
    receipt = provider.materialize(job) if provider is not None else source_cache.materialize_source_cache(job)
    out = _write(args, 'source-cache', {f"{receipt['canonical_id']}.source-cache-receipt.json": receipt})
    return {'species': receipt['species'], 'canonical_id': receipt['canonical_id'], 'status': receipt['status'],
            'complete': receipt['complete'], 'cache_directory': receipt['cache_directory'],
            'files': len(receipt['files']), 'clips': len(receipt['clip_coverage']['published']),
            'receipt_hash': receipt['receipt_hash'], 'output': out}


def parser():
    p = argparse.ArgumentParser(prog='slimebridge-content', description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='command', required=True)

    def common(sp, realms=True, profiles=False, species=False, direction=False):
        sp.add_argument('--out-dir', help='output directory under Lab/GeneratedContent')
        if realms:
            for realm in REALMS:
                r = realm.lower()
                sp.add_argument(f'--{r}', help=f'{realm} bridge-content-source manifest')
                sp.add_argument(f'--{r}-game-root', help=f'user-owned {realm} install (native provider)')
                sp.add_argument(f'--{r}-runtime-snapshot', help=f'{realm} plugin registry snapshot (scan reader schema)')
                sp.add_argument(f'--{r}-active-build', help=f'{realm} LAB build file the reader hashes independently')
                sp.add_argument(f'--{r}-traversal-receipt', help=f'{realm} independent native traversal receipt')
            sp.add_argument('--aliases', help='bridge-identity-aliases manifest')
        if direction:
            sp.add_argument('--from', required=True, choices=REALMS)
        if profiles:
            sp.add_argument('--to', required=True, choices=REALMS)
            sp.add_argument('--material-profile', help='source realm bridge-material-profile')
            sp.add_argument('--behaviour-profile', help='source realm bridge-behaviour-profile')
            sp.add_argument('--target-profile', help="bridge-target-profile file or 'builtin'")
        if species:
            sp.add_argument('--species', required=True, help='canonical id, e.g. slime.example')

    scan = sub.add_parser('scan', help='read and normalize one realm registry')
    scan.add_argument('--realm', required=True, choices=REALMS)
    group = scan.add_mutually_exclusive_group(required=True)
    group.add_argument('--source', help='bridge-content-source manifest')
    group.add_argument('--game-root', help='user-owned install (native provider)')
    scan.add_argument('--out-dir')
    scan.set_defaults(func=cmd_scan)
    classify = sub.add_parser('classify', help='reconcile SR1/SR2 registries')
    common(classify)
    classify.set_defaults(func=cmd_classify)
    plan = sub.add_parser('plan-extraction', help='content-addressed extraction plan for one species')
    common(plan, species=True, direction=True)
    plan.set_defaults(func=cmd_plan_extraction)
    extract = sub.add_parser('extract', help='run extraction (synthetic manifests only in this build)')
    common(extract, species=True, direction=True)
    extract.set_defaults(func=cmd_extract)
    convert = sub.add_parser('convert', help='convert one exclusive species')
    common(convert, profiles=True, species=True, direction=True)
    convert.set_defaults(func=cmd_convert)
    batch = sub.add_parser('batch', help='classify and plan every exclusive species')
    common(batch, profiles=True, direction=True)
    batch.add_argument('--only', action='append', help='limit conversion to these canonical ids (lazy)')
    batch.add_argument('--cache-dir', help='content cache directory under Lab/GeneratedContent')
    batch.add_argument('--no-cache', action='store_true')
    batch.set_defaults(func=cmd_batch)
    select = sub.add_parser('select-first', help='lowest-risk candidate from a batch report')
    select.add_argument('--report', required=True)
    select.set_defaults(func=cmd_select_first)
    source = sub.add_parser('source-cache', help='materialize the complete offline source cache '
                                                 '(explicit clip IDs + exact default name required)')
    source.add_argument('--job', help='bridge-source-cache-job JSON (visual_mode SOURCE_COMPLETE) carrying the whole explicit request')
    source.add_argument('--canonical', help='canonical id, e.g. slime.ringtail')
    source.add_argument('--asset-name', help='source asset-library species name, e.g. Ringtail')
    source.add_argument('--sr2-dir', help='read-only SR2 SlimeRancher2_Data directory')
    source.add_argument('--cache-dir', help='fresh private cache directory under Lab/AssetCache or Lab/RuntimeArtifacts')
    source.add_argument('--clip-id', type=int, action='append', help='explicit source clip path ID (repeatable)')
    source.add_argument('--default-clip', help='exact source clip name to mark as default')
    source.add_argument('--allow-nonlab', action='store_true', help='read a source outside the Lab tree (read-only)')
    source.add_argument('--events', action='store_true', help='emit JSON lines for actual provider stages before the final result (opt-in)')
    source.add_argument('--expect-source-files', help='pinned JSON map source path -> sha256 from the original attempt; a change fails closed before extraction')
    source.add_argument('--out-dir', help='receipt output directory under Lab/GeneratedContent')
    source.set_defaults(func=cmd_source_cache)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        summary = args.func(args)
    except NotAvailableInCloud as exc:
        print(json.dumps({'status': str(exc.evidence), 'operation': exc.operation,
                          'local_queries': list(exc.local_queries)}, sort_keys=True))
        return EXIT_LOCAL
    except source_cache.SelectionRequired as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': source_cache.SelectionRequired.code,
                          'error': str(exc)}, sort_keys=True))
        return EXIT_BLOCKED
    except source_cache.SourceCacheIncomplete as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': 'SOURCE_CACHE_INCOMPLETE', 'error': str(exc),
                          'coverage': exc.coverage}, sort_keys=True))
        return EXIT_BLOCKED
    except source_cache.SourceChanged as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': source_cache.SourceChanged.code, 'error': str(exc)}, sort_keys=True))
        return EXIT_BLOCKED
    except source_cache.SourceCacheError as exc:
        print(json.dumps({'status': 'INVALID_INPUT', 'error': str(exc)}, sort_keys=True))
        return EXIT_INPUT
    except (InputError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'INVALID_INPUT', 'error': str(exc)}, sort_keys=True))
        return EXIT_INPUT
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
