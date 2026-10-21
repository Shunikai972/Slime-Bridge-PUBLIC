"""Evidence-gated, deterministic species and Largo coverage."""

from itertools import combinations

from .scan import checked_runtime_proof, valid_dedicated_prefab


def _verified_inventory(scan: dict) -> bool:
    inv = scan.get('asset_inventory')
    if not isinstance(inv, dict):
        return False
    build = scan.get('build_proof') or {}
    return (inv.get('completeness') == 'COMPLETE' and
            bool(inv.get('build_sha256')) and
            inv.get('build_sha256') == build.get('active_sha256') and
            isinstance(inv.get('inventory_identity'), str) and bool(inv['inventory_identity']) and
            isinstance(inv.get('inventory_sha256'), str) and bool(inv['inventory_sha256']) and
            type(inv.get('expected_count')) is int and
            inv.get('expected_count') == inv.get('observed_count') and
            isinstance(inv.get('dedicated_largo_model_ids'), list) and
            isinstance(inv.get('classification_provenance'), str) and bool(inv['classification_provenance']))


def species_coverage(sr1: dict, sr2: dict) -> dict:
    """Classify proven canonical mappings; retain name-derived candidates separately."""
    by_realm = {}
    for scan in (sr1, sr2):
        by_realm[scan['realm']] = {row['canonical_id'] for row in scan['definitions']
                                   if row['is_largo'] is False and row['canonical_id']
                                   and row['canonical_proven']}
    a, b = by_realm['SR1'], by_realm['SR2']
    candidates = {}
    for scan in (sr1, sr2):
        candidates[scan['realm']] = sorted({row['canonical_id'] for row in scan['definitions']
            if row['is_largo'] is False and row['canonical_id'] and not row['canonical_proven']})
    has_duplicate = False
    for scan in (sr1, sr2):
        canonicals = [row['canonical_id'] for row in scan['definitions']
                      if row['is_largo'] is False and row['canonical_id']]
        if len(canonicals) != len(set(canonicals)):
            has_duplicate = True
            break
    complete = (not has_duplicate and
                all(checked_runtime_proof(scan) and
                    all(row['canonical_proven'] and row['canonical_id'] for row in scan['definitions']
                        if row['is_largo'] is False)
                    for scan in (sr1, sr2)))
    return {'confirmed_shared': sorted(a & b),
            'confirmed_sr1_only': sorted(a - b) if complete else [],
            'confirmed_sr2_only': sorted(b - a) if complete else [],
            'candidate_sr1': candidates['SR1'], 'candidate_sr2': candidates['SR2'],
            'exclusive_classification_complete': complete}


def _realm_pair(scan, species_a, species_b):
    """Return present Largo evidence and whether an unknown definition could hide it."""
    base_rows = [r for r in scan['definitions'] if r['is_largo'] is False]
    bases = {}
    for row in base_rows:
        for ref in (row['object_ref'], row['native_id'], row['reference_id']):
            if ref is not None:
                bucket = bases.setdefault((type(ref).__name__, ref), [])
                if row not in bucket:
                    bucket.append(row)
    present, uncertain = [], False
    for row in scan['definitions']:
        if row['is_largo'] is not True:
            continue
        refs = row['base_slimes']
        resolved = [bases.get((type(ref).__name__, ref), []) for ref in refs]
        if len(refs) != 2 or any(len(rows) != 1 for rows in resolved):
            uncertain = True
            continue
        parents = {rows[0]['canonical_id'] for rows in resolved}
        if None in parents or len(parents) != 2:
            uncertain = True
            continue
        if parents == {species_a, species_b}:
            present.append(row)
    if len(present) > 1 or any(not valid_dedicated_prefab(r, scan.get('assets', [])) for r in present):
        uncertain = True
    return present, uncertain


def largo_coverage(sr1: dict, sr2: dict) -> dict:
    if sr1['realm'] != 'SR1' or sr2['realm'] != 'SR2':
        raise ValueError('Largo coverage requires SR1 and SR2 snapshots')
    scans = {'SR1': sr1, 'SR2': sr2}
    bases = {realm: {} for realm in scans}
    for realm, scan in scans.items():
        for row in scan['definitions']:
            if row['is_largo'] is False and row['canonical_id']:
                bases[realm].setdefault(row['canonical_id'], []).append(row)
    eligible = sorted({cid for rows in bases.values() for cid, defs in rows.items()
                       if any(d['can_largofy'] is True for d in defs)})
    pairs = []
    for a, b in combinations(eligible, 2):
        proof = {realm: _realm_pair(scan, a, b) for realm, scan in scans.items()}
        relation = {realm: rows[0][0] if len(rows[0]) == 1 else None
                    for realm, rows in proof.items()}
        native = {realm: row if row and valid_dedicated_prefab(row, scans[realm].get('assets', [])) else None
                  for realm, row in relation.items()}
        reasons = []
        if any(scan['completeness'] != 'COMPLETE' for scan in scans.values()):
            reasons.append('PARTIAL_TRAVERSAL')
        if any(not scan.get('registry_traversed', False) or
               scan.get('source_kind') != 'runtime_registry' for scan in scans.values()):
            reasons.append('UNPROVEN_TRAVERSAL')
        if any(scan.get('build_match') != 'VERIFIED' or not scan.get('game_build') for scan in scans.values()):
            reasons.append('UNVERIFIED_BUILD')
        for scan in scans.values():
            traversal = scan.get('traversal_proof') or {}
            count = len(scan.get('definitions', []))
            if (traversal.get('expected_count') != traversal.get('observed_count') or
                traversal.get('expected_count') != count or
                not traversal.get('registry_identity') or
                not traversal.get('registry_sha256')):
                reasons.append('UNCHECKED_TRAVERSAL_PROOF')
        if any(not checked_runtime_proof(scan) for scan in scans.values()):
            reasons.append('UNCHECKED_RUNTIME_PROOF')
        if any(scan.get('errors') for scan in scans.values()):
            reasons.append('SCAN_ERRORS')
        for cid in (a, b):
            defs = [d for realm in scans for d in bases[realm].get(cid, [])]
            if not defs or any(not d['canonical_proven'] for d in defs):
                reasons.append('UNPROVEN_CANONICAL_MAPPING')
            if any(len(bases[realm].get(cid, [])) > 1 for realm in scans):
                reasons.append('AMBIGUOUS_CANONICAL_MAPPING')
            states = {d['can_largofy'] for d in defs}
            if states != {True}:
                reasons.append('ELIGIBILITY_UNKNOWN_OR_CONFLICTING')
        if any(unknown for _, unknown in proof.values()):
            reasons.append('UNRESOLVED_LARGO_RELATION_OR_PREFAB')
        if any(row['is_largo'] is None for scan in scans.values() for row in scan['definitions']):
            reasons.append('UNKNOWN_DEFINITION_KIND')
        if reasons:
            status = 'BLOCKED_UNKNOWN'
        elif native['SR1'] and native['SR2']:
            status = 'NATIVE_BOTH'
        elif native['SR1']:
            status = 'NATIVE_SR1_ONLY'
        elif native['SR2']:
            status = 'NATIVE_SR2_ONLY'
        else:
            status = 'MISSING_BOTH'
        if status == 'MISSING_BOTH':
            future_model = True if all(_verified_inventory(s) for s in scans.values()) else None
        else:
            future_model = False if (native['SR1'] or native['SR2']) else None
        pairs.append({'canonical_largo_id': 'largo.' + a[6:] + '.' + b[6:],
                      'species_a': a, 'species_b': b, 'eligible': not bool(reasons),
                      'native_sr1': native['SR1'] is not None,
                      'native_sr2': native['SR2'] is not None,
                      'sr1_definition_id': relation['SR1']['native_id'] if relation['SR1'] else None,
                      'sr2_definition_id': relation['SR2']['native_id'] if relation['SR2'] else None,
                      'sr1_prefab_identity': native['SR1']['dedicated_prefab_identity']['object_ref'] if native['SR1'] else None,
                      'sr2_prefab_identity': native['SR2']['dedicated_prefab_identity']['object_ref'] if native['SR2'] else None,
                      'available_source_game': [realm for realm in ('SR1', 'SR2') if native[realm]],
                      'can_extract_existing_model': None, 'can_convert_to_sr1': None,
                      'can_convert_to_sr2': None, 'behaviour_compatibility': 'UNTESTED',
                      'visual_status': 'UNINSPECTED', 'final_status': status,
                      'future_model_required': future_model,
                      'blockers': sorted(set(reasons))})
    counts = {status: sum(r['final_status'] == status for r in pairs) for status in
              ('NATIVE_BOTH', 'NATIVE_SR1_ONLY', 'NATIVE_SR2_ONLY', 'MISSING_BOTH', 'BLOCKED_UNKNOWN')}
    return {'schema_version': 1, 'source_completeness': {r: scans[r]['completeness'] for r in scans},
            'eligible_species_candidates': eligible, 'pair_count': len(pairs),
            'counts': counts, 'pairs': pairs}


def missing_backlog(coverage: dict) -> dict:
    """A queue only; no inferred traits, meshes, or automatic conversion claims."""
    return {'schema_version': 1, 'entries': [
        {'canonical_largo_id': row['canonical_largo_id'], 'species_a': row['species_a'],
         'species_b': row['species_b'], 'parent_visual_traits': None,
         'face_features': None, 'appendages': None, 'material_families': None,
         'behaviour_visual_effects': None, 'converted_base_mesh_available': None,
         'complexity_estimate': None, 'status': 'REQUIRES_ASSET_INSPECTION'}
        for row in coverage['pairs'] if row['final_status'] == 'MISSING_BOTH']}
