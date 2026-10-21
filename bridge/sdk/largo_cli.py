"""Read-only UserLargo readiness CLI, extending bridge.sdk.largo's v1 contract."""
import argparse
import re
from pathlib import Path

from bridge.content.canonical import REALMS, diag, pretty_json, sha256_json
from bridge.conversion.capabilities import CATALOG as CAPABILITIES
from bridge.conversion.targets import builtin_target_profile
from bridge.sdk import largo
from bridge.sdk.largo_geometry import inspect_model
from bridge.sdk.largo_io import ImportProblem, checked_path, inventory, read_json, output_path, write_report
from bridge.sdk.largo_metadata import readiness

MODEL_SUFFIXES = {'.obj', '.stl', '.fbx', '.blend', '.gltf', '.glb'}
CATALOG_SCHEMA = 'bridge-user-largo-catalog'
ADAPTER_BLOCKERS = ['MODEL_LOADER_UNVERIFIED', 'REGISTRATION_ADAPTER_UNVERIFIED']


def _tokens(value):
    return tuple(re.findall(r'[a-z0-9]+', value.casefold()))


def load_catalog(path):
    document = read_json(path)
    if not isinstance(document, dict) or document.get('schema') != CATALOG_SCHEMA or \
            type(document.get('schema_version')) is not int or document['schema_version'] != 1 or \
            set(document) - {'schema', 'schema_version', 'species', 'parent_capabilities', 'target_context', 'evidence'}:
        raise ImportProblem('INVALID_CATALOG', 'expected explicit bridge-user-largo-catalog v1')
    species = document.get('species')
    if not isinstance(species, list) or not 0 < len(species) <= 1024:
        raise ImportProblem('INVALID_CATALOG', '1..1024 explicit species rows required')
    ids, aliases = set(), {}
    for row in species:
        cid = row.get('canonical_id') if isinstance(row, dict) else None
        names = row.get('aliases') if isinstance(row, dict) else None
        if not isinstance(cid, str) or not largo._SLIME.fullmatch(cid) or cid in ids or \
                not isinstance(names, list) or len(names) > 128 or set(row) - {'canonical_id', 'aliases'}:
            raise ImportProblem('INVALID_CATALOG', 'unique canonical slime IDs and explicit alias lists required')
        ids.add(cid)
        for name in [cid, *names]:
            if not isinstance(name, str) or not 0 < len(name) <= 160 or not _tokens(name):
                raise ImportProblem('INVALID_CATALOG', 'nonempty bounded alias strings required')
            aliases.setdefault(_tokens(name), set()).add(cid)
    caps = document.get('parent_capabilities', {})
    if not isinstance(caps, dict) or set(caps) - ids:
        raise ImportProblem('INVALID_CATALOG', 'parent_capabilities keys must be provided canonical parents')
    for values in caps.values():
        if not isinstance(values, list) or not all(isinstance(v, str) and v in CAPABILITIES for v in values) or \
                len(set(values)) != len(values):
            raise ImportProblem('INVALID_CATALOG', 'parent capabilities must be unique known capability IDs')
    context = document.get('target_context', {})
    if not isinstance(context, dict) or set(context) - set(REALMS):
        raise ImportProblem('INVALID_CATALOG', 'target_context keys must be SR1/SR2')
    for realm, row in context.items():
        if not isinstance(row, dict) or set(row) != {'native', 'parents'}:
            raise ImportProblem('INVALID_CATALOG', f'{realm} context needs native and parents lists')
        for key in ('native', 'parents'):
            values = row[key]
            if not isinstance(values, list) or len(values) > 8192 or not all(isinstance(v, str) for v in values) or \
                    len(set(values)) != len(values):
                raise ImportProblem('INVALID_CATALOG', 'context IDs must be unique bounded lists')
            for value in values:
                if value in ids:
                    continue
                match = largo._LARGO.fullmatch(value) if key == 'native' else None
                if not match or match[1] >= match[2] or any('slime.' + slug not in ids for slug in match.groups()):
                    raise ImportProblem('INVALID_CATALOG', f'unknown/noncanonical context ID: {value}')
    return {'ids': ids, 'aliases': aliases, 'document': document}


def match_filename(filename, catalog):
    tokens = _tokens(Path(filename).stem)
    pairs = set()
    for split in range(1, len(tokens)):
        for a in catalog['aliases'].get(tokens[:split], ()):
            for b in catalog['aliases'].get(tokens[split:], ()):
                pairs.add(tuple(sorted((a, b))))
    candidates = [list(pair) for pair in sorted(pairs)]
    status = 'UNKNOWN' if not pairs else 'AMBIGUOUS' if len(pairs) > 1 else 'MATCHED'
    parents = candidates[0] if len(candidates) == 1 else None
    if parents and parents[0] == parents[1]:
        status = 'INVALID'
    return {'match_status': status, 'parents': parents, 'candidate_pairs': candidates,
            'canonical_largo_id': largo._largo_id(parents) if status == 'MATCHED' else None}


def manifest_proposal(path, parents):
    return {'schema': largo.PACKAGE_SCHEMA, 'schema_version': 1,
            'canonical_largo_id': largo._largo_id(parents), 'parents': parents,
            'version': None, 'assets': [{'path': path, 'kind': 'mesh', 'sha256': None, 'role': 'body'}],
            'materials': None, 'renderers': None, 'face': None, 'animations': None,
            'behaviour': {'compose': 'union', 'capabilities': [], 'exclude': []},
            'targets': None, 'provenance': {'user_owned': None},
            'import_metadata': {'transform': {'scale': None, 'orientation_xyzw': None, 'pivot': None},
                                'collision': None, 'face_anchors': None, 'animation_binding': None,
                                'source_realms': None, 'intended_realms': None}}


def scan(root, catalog):
    files = inventory(root)
    packages = [p.parent for p in files if p.name == largo.PACKAGE_FILE]
    entries = []
    for directory in packages:
        # Strict bounded JSON preflight before the existing package validator; it does the hashing/schema checks.
        read_json(directory / largo.PACKAGE_FILE)
        validated = largo.validate_package(directory, known_base_species=catalog['ids'], max_file_bytes=32*1024*1024)
        manifest = validated['manifest'] or {}
        entry = {'path': directory.relative_to(root).as_posix(), 'kind': 'manifested_package',
                 'match_status': 'MATCHED' if validated['status'] == 'VALID' else 'INVALID',
                 'parents': manifest.get('parents'), 'canonical_largo_id': manifest.get('canonical_largo_id'),
                 'candidate_pairs': [], 'manifest_proposal': None, 'package_validation': validated,
                 'metadata_readiness': readiness(manifest)}
        entries.append(entry)
    for path in files:
        if path.suffix.casefold() in MODEL_SUFFIXES and not any(path.is_relative_to(p) for p in packages):
            entry = {'path': path.relative_to(root).as_posix(), 'kind': 'bare_model',
                     **match_filename(path.name, catalog)}
            entry['manifest_proposal'] = manifest_proposal(entry['path'], entry['parents']) \
                if entry['match_status'] == 'MATCHED' else None
            entry['metadata_readiness'] = {'status': 'METADATA_INCOMPLETE',
                                          'missing': ['manifest', 'transform', 'collision', 'face_anchors',
                                                      'animation_binding', 'source_realms', 'intended_realms',
                                                      'provenance.user_owned'],
                                          'evidence': 'NOT_DECLARED'}
            entries.append(entry)
    duplicates = {}
    for entry in entries:
        cid = entry['canonical_largo_id']
        if isinstance(cid, str) and largo._LARGO.fullmatch(cid):
            duplicates.setdefault(cid, []).append(entry)
    for group in duplicates.values():
        if len(group) > 1:
            for entry in group:
                entry['match_status'] = 'DUPLICATE'
                entry['manifest_proposal'] = None
    return sorted(entries, key=lambda entry: (entry['path'], entry['kind']))


def validate_entries(root, entries):
    for entry in entries:
        if entry['kind'] == 'bare_model':
            paths = [root / entry['path']]
        else:
            validated = entry['package_validation']
            paths = [Path(validated['package_dir']) / row['path'] for row in validated['files'] if row['kind'] == 'mesh']
        entry['geometry'] = [inspect_model(root, path) for path in paths]
        statuses = {item['status'] for item in entry['geometry']}
        entry['validation_status'] = 'REJECTED' if not paths or 'REJECTED' in statuses else \
            'NEEDS_FORMAT_ADAPTER' if 'NEEDS_FORMAT_ADAPTER' in statuses else 'GEOMETRY_VALID'
        if entry['manifest_proposal'] and entry['geometry']:
            entry['manifest_proposal']['assets'][0]['sha256'] = entry['geometry'][0]['sha256']


def plan_entries(entries, catalog, targets):
    """Delegate material/behaviour planning to the existing system; annotate execution blockers only."""
    caps = catalog['document'].get('parent_capabilities', {})
    context = catalog['document'].get('target_context', {})
    for entry in entries:
        entry['plans'] = []
        if entry['match_status'] != 'MATCHED' or entry['validation_status'] == 'REJECTED':
            continue
        if entry['kind'] != 'manifested_package':
            entry['planning_status'] = 'METADATA_INCOMPLETE'
            continue  # never invent a valid package/ledger for an unmanifested model
        validated = entry['package_validation']
        for realm in targets:
            known = context.get(realm, {})
            plan = largo.plan_import(validated, builtin_target_profile(realm), caps,
                                     target_native=frozenset(known.get('native', [])),
                                     target_parents=set(known['parents']) if 'parents' in known else None)
            if plan['final'] not in ('REFUSED', 'REJECTED'):
                blockers = list(ADAPTER_BLOCKERS)
                if entry['validation_status'] == 'NEEDS_FORMAT_ADAPTER':
                    blockers.append('NEEDS_FORMAT_ADAPTER')
                if entry['metadata_readiness']['missing']:
                    blockers.append('IMPORT_METADATA_INCOMPLETE')
                if any(parent not in caps for parent in entry['parents']):
                    blockers.append('PARENT_RULES_UNSPECIFIED')
                intended = validated['manifest'].get('import_metadata', {}).get('intended_realms')
                if intended is not None and realm not in intended:
                    blockers.append('TARGET_NOT_INTENDED')
                declared = {(Path(entry['path']) / row['path']).as_posix() for row in validated['files']}
                if any(ref['path'] not in declared for geometry in entry['geometry'] for ref in geometry['external_references']):
                    blockers.append('UNMANIFESTED_EXTERNAL_REFERENCE')
                plan.update(final='PENDING_ADAPTER', registrable=False,
                            reasons=sorted(set(plan['reasons'] + blockers)))
            plan['registrable'] = False
            plan.pop('plan_hash', None)
            plan['plan_hash'] = sha256_json(plan)
            entry['plans'].append(plan)
        entry['planning_status'] = 'PENDING_ADAPTER'


def receipt(status='NOT_EXECUTED'):
    return {'status': status, 'runtime_proven': False, 'executed_operations': [],
            'proof': None, 'loader_verified': False, 'registration_adapter_verified': False}


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ImportProblem('USAGE_ERROR', message)


def parser():
    result = JSONArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest='command', required=True)
    for verb in ('scan', 'validate', 'plan', 'install'):
        command = sub.add_parser(verb)
        command.add_argument('--root')
        command.add_argument('--settings', help='explicit launcher settings JSON; read-only')
        command.add_argument('--catalog', required=True)
        command.add_argument('--out', help='new exclusive report file outside source root')
        if verb in ('plan', 'install'):
            command.add_argument('--target', action='append', choices=REALMS, required=True)
        if verb == 'install':
            command.add_argument('--lab', required=True, help='requested lab, never inspected while adapters are missing')
    return result


def root_from_args(args):
    value = args.root
    if not value and args.settings:
        from bridge.core.launcher_settings import normalize
        settings, diagnostics = normalize(read_json(args.settings))
        if diagnostics:
            raise ImportProblem('INVALID_SETTINGS', 'settings require correction; reader never rewrites them')
        value = settings['content']['user_largos_root']
    if not value:
        raise ImportProblem('ROOT_REQUIRED', 'provide --root or --settings content.user_largos_root; no folder search')
    return checked_path(value, 'UNSAFE_ROOT', directory=True)


def main(argv=None):
    args, root = None, None
    report = {'schema': 'bridge-user-largo-readiness', 'schema_version': 1,
              'command': None, 'status': 'REJECTED', 'entries': [], 'diagnostics': [],
              'registration_receipt': receipt()}
    try:
        args = parser().parse_args(argv)
        report['command'] = args.command
        root = root_from_args(args)
        output = output_path(args.out, root) if args.out else None
        catalog = load_catalog(args.catalog)
        report.update(root=str(root), catalog_hash=sha256_json(catalog['document']),
                      catalog_evidence='EXPLICIT_INPUT_NOT_RUNTIME_VERIFIED')
        if args.command == 'install':
            lab = Path(args.lab)
            if not lab.is_absolute() or '..' in lab.parts or lab == Path(lab.anchor):
                raise ImportProblem('UNSAFE_LAB', 'explicit absolute lab path required; adapter blocker precedes inspection')
            if output and (output.is_relative_to(lab) or lab.is_relative_to(output)):
                raise ImportProblem('UNSAFE_OUTPUT', 'install readiness may not write inside/above requested lab')
            report.update(status='BLOCKED', blockers=list(ADAPTER_BLOCKERS), requested_lab=str(lab), lab_inspected=False)
            code = 6  # no model enumeration, lab reads, copy, deployment, launch or ledger persistence
        else:
            entries = scan(root, catalog)
            report['entries'] = entries
            code = 4 if any(entry['match_status'] != 'MATCHED' for entry in entries) else 0
            if args.command != 'scan':
                validate_entries(root, entries)
                if any(e['validation_status'] == 'REJECTED' for e in entries):
                    code = 4
                elif code != 4 and any(e['validation_status'] == 'NEEDS_FORMAT_ADAPTER' for e in entries):
                    code = 3
            if args.command == 'plan':
                plan_entries(entries, catalog, sorted(set(args.target)))
                if any(p['final'] in ('REFUSED', 'REJECTED') for e in entries for p in e.get('plans', [])):
                    code = 4
                elif code != 4:
                    code = 3
                report['registration_receipt'] = receipt('PLANNED' if any(e.get('plans') for e in entries) else 'NOT_EXECUTED')
            report['status'] = 'REJECTED' if code == 4 else 'PENDING_ADAPTER' if code == 3 else \
                'SCANNED' if args.command == 'scan' else 'VALIDATED'
            if not entries:
                code = 4
                report.update(status='REJECTED', diagnostics=[diag('NO_MODELS', 'error', 'root', 'no discovered models/packages')])
        if output:
            write_report(output, root, pretty_json(report))
    except (OSError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        error = getattr(exc, 'code', 'INPUT_UNREADABLE')
        report.update(status='REJECTED', entries=[], diagnostics=[diag(error, 'error', 'input', str(exc)[:300])],
                      registration_receipt=receipt())
        code = 5 if error == 'UNSAFE_OUTPUT' else 2 if error in ('ROOT_REQUIRED', 'USAGE_ERROR') else 4
    print(pretty_json(report), end='')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
