"""Generic cross-realm species reconciliation from native registry snapshots.

Classifications: SHARED_NATIVE (use destination-native content, never convert),
SR1_ONLY / SR2_ONLY (convertible exclusives), BLOCKED_UNKNOWN (evidence cannot establish
exclusivity) and QUARANTINED (unsafe identity or source). PENDING_ADAPTER is a conversion
outcome, decided later per species, never a registry fact.

Identity basis, strongest first: ALIAS_MANIFEST (explicit evidence-carrying override),
NATIVE_EVIDENCE (provider canonical with evidence) and DERIVED (deterministic slug of the
native reference). No species list is authoritative; aliases only correct discovered
naming mismatches. Exclusivity additionally requires the other realm's traversal to be
COMPLETE, every entry there to be identifiable, and no produced plort to already exist in
the other realm (a strong hint of a renamed shared species).
"""

import re

from bridge.content.canonical import REALMS, DataEvidence, diag, sha256_json, sort_diagnostics
from bridge.content.scan import _slug
from .snapshot import normalize_snapshot

CLASSIFICATION_SCHEMA = 'bridge-species-classification'
CLASSIFICATION_SCHEMA_VERSION = 1
SLUG_RULE = 'slug-v1'
_CANONICAL = {'slime': re.compile(r'^slime\.[a-z0-9]+(?:_[a-z0-9]+)*$'),
              'plort': re.compile(r'^plort\.[a-z0-9]+(?:_[a-z0-9]+)*$')}
_EVIDENCE_RANK = {DataEvidence.SYNTHETIC: 0, DataEvidence.STATIC: 1, DataEvidence.RUNTIME_ANCHORED: 2}


def derive_canonical(kind, native_ref):
    """Deterministic candidate identity from a native reference (scanner slug rule)."""
    if kind not in _CANONICAL or not isinstance(native_ref, str) or not native_ref:
        return None
    slug = _slug(native_ref)
    if kind == 'plort':
        slug = re.sub(r'(^plort_|_plort$)', '', slug)
    candidate = f'{kind}.{slug}'
    return candidate if _CANONICAL[kind].fullmatch(candidate) else None


def load_aliases(data) -> dict:
    """Validate an identity alias manifest; conflicting or evidence-free aliases are refused."""
    if not isinstance(data, dict) or data.get('schema') != 'bridge-identity-aliases' or \
            data.get('schema_version') != 1 or not isinstance(data.get('aliases'), list):
        raise ValueError('Expected bridge-identity-aliases v1')
    out = {}
    for row in data['aliases']:
        if not isinstance(row, dict) or row.get('realm') not in REALMS:
            raise ValueError('Alias needs a realm')
        ref, canonical, evidence = row.get('native_ref'), row.get('canonical_id'), row.get('evidence')
        if not ((type(ref) is int and ref >= 0) or (isinstance(ref, str) and ref)):
            raise ValueError('Alias needs a native_ref (reference string or build-bound numeric id)')
        kind = canonical.split('.', 1)[0] if isinstance(canonical, str) else None
        if kind not in _CANONICAL or not _CANONICAL[kind].fullmatch(canonical):
            raise ValueError(f'Invalid alias canonical id {canonical!r}')
        if not isinstance(evidence, str) or not evidence:
            raise ValueError('Alias needs evidence')
        build = row.get('build')
        if build is not None and (not isinstance(build, str) or not build):
            raise ValueError('Alias build must be a non-empty fingerprint')
        if type(ref) is int and build is None:
            raise ValueError('Numeric native ids are build-bound; the alias needs a build fingerprint')
        key = (row['realm'], type(ref).__name__, ref, build)
        if key in out and out[key]['canonical_id'] != canonical:
            raise ValueError(f'Conflicting aliases for {key}')
        out.setdefault(key, {'canonical_id': canonical, 'evidence': set()})['evidence'].add(evidence)
    return out


def aliases_from_content_registry(registry, builds=None, require_verified=False) -> dict:
    """Seed identity from the existing transfer registry (e.g. bridge/registry/pink.json).

    Numeric mappings apply only to the build named in ``builds[realm]``; without it they are skipped.
    """
    rows = []
    for entry in registry.entries.values():
        for realm, mapping in entry.native.items():
            if require_verified and not mapping.verified:
                continue
            row = {'realm': realm, 'native_ref': mapping.identifier, 'canonical_id': entry.canonical_id,
                   'evidence': f'bridge/registry ContentRegistry mapping verified={mapping.verified}'}
            if type(mapping.identifier) is int:
                if not (builds or {}).get(realm):
                    continue
                row['build'] = builds[realm]
            rows.append(row)
    return load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': rows})


def _identity(realm, build, row, aliases):
    """Return (canonical, basis, evidence, alias_conflict)."""
    matches = []
    for kind, ref in (('str', row['native_ref']), ('int', row['numeric_id'])):
        for bound in (None, build):
            alias = aliases.get((realm, kind, ref, bound)) if ref is not None else None
            if alias:
                matches.append(alias)
    if matches:
        evidence = sorted({e for alias in matches for e in alias['evidence']})
        return matches[0]['canonical_id'], 'ALIAS_MANIFEST', evidence, \
            len({alias['canonical_id'] for alias in matches}) > 1
    kind = row['content_kind'] or 'slime'
    if isinstance(row.get('canonical_id'), str):
        basis = 'NATIVE_EVIDENCE' if row['canonical_evidence'] else 'DERIVED'
        evidence = row['canonical_evidence'] or [f'provider-canonical:{row["native_ref"]}']
        return row['canonical_id'], basis, evidence, False
    derived = derive_canonical(kind, row['native_ref'])
    return (derived, 'DERIVED', [f'{SLUG_RULE}:{row["native_ref"]}'], False) if derived else (None, None, [], False)


def _index(snapshot, aliases):
    realm = snapshot['realm']
    duplicates = {d['subject'].split(':', 1)[1] for d in snapshot['diagnostics'] if d['code'] == 'DUPLICATE_NATIVE_REF'}
    species, plorts, non_base, unidentified = {}, {}, [], 0
    plort_lookup = {}
    for row in snapshot['entries']:
        canonical, basis, evidence, conflict = _identity(realm, snapshot['build']['fingerprint'], row, aliases)
        kind = row['content_kind']
        item = {'row': row, 'canonical_id': canonical, 'basis': basis, 'evidence': evidence,
                'duplicate': row['native_ref'] in duplicates, 'alias_conflict': conflict}
        if canonical is not None and not _CANONICAL['plort' if kind == 'plort' else 'slime'].fullmatch(canonical):
            item['invalid'] = True
        if kind == 'plort':
            if canonical:
                plorts.setdefault(canonical, []).append(item)
                for ref in (row['native_ref'], row['numeric_id']):
                    if ref is not None:
                        plort_lookup[(type(ref).__name__, ref)] = canonical
            continue
        if row['role'] == 'largo':
            non_base.append({'native_ref': row['native_ref'], 'role': 'largo', 'canonical_id': canonical,
                             'base_slimes': row['base_slimes']})
            continue
        if canonical is None:
            unidentified += 1
            continue
        species.setdefault(canonical, []).append(item)
    return {'species': species, 'plorts': plorts, 'plort_lookup': plort_lookup,
            'non_base': sorted(non_base, key=lambda r: r['native_ref'] or ''), 'unidentified': unidentified}


def _produced(item, index):
    out = set()
    for ref in item['row']['produces']:
        canonical = index['plort_lookup'].get((type(ref).__name__, ref)) or derive_canonical('plort', ref)
        if canonical:
            out.add(canonical)
    return sorted(out)


def _unsafe(realm, items):
    reasons = []
    if len(items) > 1:
        reasons.append(f'AMBIGUOUS_IDENTITY_{realm}')
    for item in items:
        row = item['row']
        if row.get('malformed'):
            reasons.append('MALFORMED_ENTRY')
        if row['quarantine']:
            reasons.append(f'SOURCE_QUARANTINED_{realm}')
        if item['duplicate']:
            reasons.append(f'DUPLICATE_NATIVE_REF_{realm}')
        if item.get('invalid'):
            reasons.append('INVALID_CANONICAL_ID')
        if item['alias_conflict']:
            reasons.append('ALIAS_CONFLICT')
    return reasons


def _classify(canonical, present, indexes, snapshots, kind):
    reasons = []
    for realm in REALMS:
        reasons += _unsafe(realm, present.get(realm, []))
    if reasons:
        return 'QUARANTINED', reasons
    realms = [r for r in REALMS if present.get(r)]
    roles = {r: present[r][0]['row']['role'] for r in realms}
    if kind == 'slime' and any(role is None for role in roles.values()):
        return 'BLOCKED_UNKNOWN', ['COUNTERPART_ROLE_UNKNOWN' if len(realms) == 2 else f'ROLE_UNKNOWN_{realms[0]}']
    if len(realms) == 2:
        return 'SHARED_NATIVE', []
    source = realms[0]
    other = 'SR2' if source == 'SR1' else 'SR1'
    if snapshots[other]['traversal']['completeness'] != 'COMPLETE':
        reasons.append(f'{other}_TRAVERSAL_INCOMPLETE')
    if kind == 'slime':
        if indexes[other]['unidentified']:
            reasons.append(f'{other}_HAS_UNIDENTIFIED_ENTRIES')
        if set(_produced(present[source][0], indexes[source])) & set(indexes[other]['plorts']):
            reasons.append('CROSS_REALM_PLORT_OVERLAP')
    return ('BLOCKED_UNKNOWN', reasons) if reasons else (f'{source}_ONLY', [])


def _native(item, index):
    row = item['row']
    return {'native_ref': row['native_ref'], 'numeric_id': row['numeric_id'], 'role': row['role'],
            'definition_hash': row['definition_hash'], 'display_name': row['display_name'],
            'produces': _produced(item, index), 'quarantine': row['quarantine'],
            'malformed': bool(row.get('malformed'))}


def classify_species(sr1, sr2, aliases=None) -> dict:
    """Classify every base species and plort across both realms. Deterministic for any input order."""
    snapshots = {'SR1': normalize_snapshot(sr1), 'SR2': normalize_snapshot(sr2)}
    if snapshots['SR1']['realm'] != 'SR1' or snapshots['SR2']['realm'] != 'SR2':
        raise ValueError('classify_species needs an SR1 snapshot and an SR2 snapshot')
    aliases = aliases or {}
    indexes = {realm: _index(snapshots[realm], aliases) for realm in REALMS}
    out = {'species': [], 'plorts': []}
    for kind, bucket in (('slime', 'species'), ('plort', 'plorts')):
        ids = sorted({cid for realm in REALMS for cid in indexes[realm][bucket]})
        for canonical in ids:
            present = {realm: indexes[realm][bucket].get(canonical, []) for realm in REALMS}
            classification, reasons = _classify(canonical, present, indexes, snapshots, kind)
            row = {'canonical_id': canonical, 'classification': classification, 'reasons': sorted(set(reasons)),
                   'present': {realm: len(present[realm]) for realm in REALMS},
                   'native': {realm: _native(present[realm][0], indexes[realm]) if len(present[realm]) == 1 else None
                              for realm in REALMS},
                   'identity_basis': {realm: present[realm][0]['basis'] for realm in REALMS if present[realm]},
                   'identity_evidence': {realm: sorted({e for i in present[realm] for e in i['evidence']})
                                         for realm in REALMS if present[realm]}}
            out[bucket].append(row)
    weakest = min((snapshots[r]['data_evidence'] for r in REALMS), key=lambda e: _EVIDENCE_RANK[DataEvidence(e)])
    diagnostics = [*snapshots['SR1']['diagnostics'], *snapshots['SR2']['diagnostics']]
    for realm in REALMS:
        if indexes[realm]['unidentified']:
            diagnostics.append(diag('UNIDENTIFIED_ENTRIES', 'warning', realm,
                                    f"{indexes[realm]['unidentified']} entries without derivable identity"))
    counts = {}
    for row in out['species']:
        counts[row['classification']] = counts.get(row['classification'], 0) + 1
    return {'schema': CLASSIFICATION_SCHEMA, 'schema_version': CLASSIFICATION_SCHEMA_VERSION,
            'builds': {r: snapshots[r]['build']['fingerprint'] for r in REALMS},
            'traversal': {r: snapshots[r]['traversal']['completeness'] for r in REALMS},
            'data_evidence': {r: str(snapshots[r]['data_evidence']) for r in REALMS},
            'weakest_evidence': str(weakest), 'synthetic': weakest == DataEvidence.SYNTHETIC,
            'snapshot_hashes': {r: sha256_json(snapshots[r]) for r in REALMS},
            'species': out['species'], 'plorts': out['plorts'],
            'non_base': {r: indexes[r]['non_base'] for r in REALMS},
            'counts': dict(sorted(counts.items())), 'diagnostics': sort_diagnostics(diagnostics)}


def exclusives(classification: dict, source_realm: str) -> list:
    """Canonical ids convertible from ``source_realm`` (sorted)."""
    return [row['canonical_id'] for row in classification['species']
            if row['classification'] == f'{source_realm}_ONLY']
