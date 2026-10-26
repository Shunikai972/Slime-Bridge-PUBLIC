"""Build the Bridge-owned imported-species spec (bridge-imported-species-spec v1) per realm.

Derives the target-realm import list from the reviewed classification capture, joins each species'
diet from the additive runtime diet captures, maps food groups/items across realms through a
normalized nine-part semantic crosswalk, allocates stable reserved native ids (refusing to
reallocate ids that a previous spec already assigned), and fails closed to PENDING_DATA for any
species whose diet or plort relation cannot be mapped from captured evidence.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bridge.content import diet_overrides  # noqa: E402
from bridge.content.diet_capture import DietCaptureError, diet_by_ref, load_diet_snapshot  # noqa: E402
from bridge.content.appearance_capture import (  # noqa: E402
    AppearanceCaptureError, appearance_by_ref, load_appearance_snapshot)
from bridge.conversion.species_parity import derive_roster  # noqa: E402

SCHEMA = 'bridge-imported-species-spec'
SCHEMA_VERSION = 1
OUTPUT_ROOT = REPO / 'Lab' / 'GeneratedContent' / 'species-import'
DEFAULT_CLASSIFICATION = REPO / 'Lab' / 'RuntimeArtifacts' / 'classification-20261006-runtime'

# SR1 SlimeEat.FoodGroup enum name -> canonical semantic.
SR1_GROUP_SEMANTICS = {
    'FRUIT': 'FRUIT',
    'VEGGIES': 'VEGGIES',
    'MEAT': 'MEAT',
    'NONTARRGOLD_SLIMES': 'SLIME',
    'PLORTS': 'PLORTS',
    'GINGER': 'GINGER',
}
SEMANTIC_TO_SR1_GROUP = {semantic: name for name, semantic in SR1_GROUP_SEMANTICS.items()}

DEFAULT_TEMPLATES = {
    'SR1': {'slime': 'TABBY_SLIME', 'plort': 'PINK_PLORT'},
    'SR2': {'slime': 'SlimeDefinition.Tabby', 'plort': 'IdentifiableType.PinkPlort'},
}
# Placeholder visual templates are target-native species references only; final visuals belong to
# the later visual phase. Per-species template overrides go here when a native analogue exists.
TEMPLATE_OVERRIDES = {}

NATIVE_ID_BASE = 900000
NATIVE_ID_SLIME_STEP = 10


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize_item(ref):
    """Cross-realm item identity: last dotted part, case/punctuation-insensitive."""
    return re.sub(r'[^a-z0-9]', '', str(ref).split('.')[-1].lower())


def classify_member(ref):
    """One food item reference -> canonical semantic, or None when it is not a known food."""
    raw = str(ref).lower()
    name = normalize_item(ref)
    if not name:
        return None
    if 'slimedefinition' in raw:
        return 'SLIME'
    if name.endswith('plort'):
        return 'PLORTS'
    if name.endswith('nectar'):
        return 'NECTAR'
    if name.endswith('veggie'):
        return 'VEGGIES'
    if name.endswith('fruit'):
        return 'FRUIT'
    if name.endswith('hen') or name.endswith('rooster') or name.endswith('chick'):
        return 'MEAT'
    if 'slime' in name:
        return 'SLIME'
    return None


def classify_group(members):
    """Majority vote over member item semantics; ties and unknowns stay None (fail closed)."""
    votes = {}
    for member in members:
        semantic = classify_member(member)
        if semantic:
            votes[semantic] = votes.get(semantic, 0) + 1
    if not votes:
        return None
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def source_semantics(realm, diet_entry):
    """Diet groups of one captured entry as a set of canonical semantics; None when unresolved."""
    semantics = set()
    if realm == 'SR1':
        for group in diet_entry['major_food_groups']:
            semantic = SR1_GROUP_SEMANTICS.get(group)
            if semantic is None:
                return None
            semantics.add(semantic)
        return semantics
    # SR2 groups arrive as reference hashes; classify each through its captured member items.
    members_by_group = {item['group']: item['members'] for item in diet_entry.get('major_food_group_members', [])}
    for group in diet_entry['major_food_groups']:
        semantic = classify_group(members_by_group.get(group, []))
        if semantic is None:
            return None
        semantics.add(semantic)
    return semantics


def target_group_ids(realm, semantics, target_diet_doc):
    """Canonical semantics -> target-realm group identifiers, or None when one is unmappable."""
    ids = []
    if realm == 'SR1':
        for semantic in sorted(semantics):
            group = SEMANTIC_TO_SR1_GROUP.get(semantic)
            if group is None:
                return None
            ids.append(group)
        return ids
    # SR2 target: locate the native group hash carrying the semantic, preferring the richest group.
    candidates = {}
    for entry in target_diet_doc['entries']:
        members_by_group = {item['group']: item['members'] for item in entry.get('major_food_group_members', [])}
        for group in entry['major_food_groups']:
            semantic = classify_group(members_by_group.get(group, []))
            if semantic is None:
                continue
            count = len(members_by_group.get(group, []))
            best = candidates.get(semantic)
            if best is None or (count, group) > (best[0], best[1]):
                candidates[semantic] = (count, group)
    for semantic in sorted(semantics):
        found = candidates.get(semantic)
        if found is None:
            return None
        ids.append(found[1])
    return ids


def item_vocabulary(doc):
    """All food-ish item references observed in one diet capture, indexed by normalized name."""
    vocab = {}
    for entry in doc['entries']:
        for ref in list(entry['favorites']) + list(entry['additional_foods']):
            vocab.setdefault(normalize_item(ref), ref)
        for item in entry.get('major_food_group_members', []):
            for ref in item['members']:
                vocab.setdefault(normalize_item(ref), ref)
    return vocab


def crosswalk_items(refs, source_vocab, target_vocab):
    """Map source item refs to target refs by normalized identity; unmatched stay out (recorded)."""
    mapped, dropped = [], []
    for ref in refs:
        target = target_vocab.get(normalize_item(ref))
        if target:
            mapped.append(target)
        else:
            dropped.append(ref)
    return sorted(set(mapped)), dropped


def load_previous_spec(path):
    if path is None:
        return {}
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    assigned = {}
    for entry in doc.get('entries', []):
        if entry.get('native_id'):
            assigned[entry['canonical_id']] = (entry['native_id'], entry.get('plort', {}).get('native_id', 0))
    return assigned


def _doc_sha(doc):
    path = doc.get('_path')
    if path and Path(path).is_file():
        return _sha256(path)
    payload = {key: value for key, value in doc.items() if not key.startswith('_')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()


def _classification_sha(classification):
    return _doc_sha(classification)


SPECIES_IDS = Path(__file__).resolve().parents[2] / 'bridge' / 'content' / 'species_ids.json'


def committed_species_ids(realm, path=SPECIES_IDS):
    """{canonical: (native_id, plort_id)} of the committed, append-only id table for one target realm."""
    import json as _json
    if not Path(path).is_file():
        return {}
    table = _json.loads(Path(path).read_text(encoding='utf-8')).get(realm, {})
    return {canonical: (int(ids[0]), int(ids[1])) for canonical, ids in table.items()}


def build_spec(realm, classification, sr1_diet, sr2_diet, previous, sr1_app=None, sr2_app=None):
    """Build one realm's spec; returns (spec_doc, summary_doc)."""
    target_realm = realm
    source_realm = 'SR2' if realm == 'SR1' else 'SR1'
    class_key = 'SR2_ONLY' if realm == 'SR1' else 'SR1_ONLY'
    roster = derive_roster(classification)
    roster_ids = roster['sr2_only'] if realm == 'SR1' else roster['sr1_only']
    rows = {row['canonical_id']: row for row in classification['species'] if row.get('canonical_id')}
    source_diet = diet_by_ref(sr2_diet if source_realm == 'SR2' else sr1_diet)
    source_app = (sr2_app if source_realm == 'SR2' else sr1_app) or {}
    source_appearance = appearance_by_ref(source_app) if source_app else {}
    target_diet_doc = sr1_diet if target_realm == 'SR1' else sr2_diet
    source_vocab = item_vocabulary(sr2_diet if source_realm == 'SR2' else sr1_diet)
    target_vocab = item_vocabulary(target_diet_doc)

    # The committed table (bridge/content/species_ids.json) decides first: every install must give a species the same id.
    previous = {**previous, **committed_species_ids(realm)}
    used_ids = set()
    for native_id, plort_id in previous.values():
        used_ids.update({native_id, plort_id})
    used_ids.discard(0)

    entries, summary_entries = [], []
    free = NATIVE_ID_BASE
    for canonical in sorted(roster_ids):
        row = rows.get(canonical)
        source = (row or {}).get('native', {}).get(source_realm) or {}
        problems = []
        if row is None:
            problems.append(f'no classification row for {canonical}')
        if not source.get('native_ref'):
            problems.append('source native_ref missing')
        diet_entry = None
        if source.get('native_ref'):
            diet_entry = source_diet.get(source['native_ref'])
            if diet_entry is None:
                problems.append(f'diet capture has no entry for {source["native_ref"]}')
        semantics = None
        if diet_entry is not None and not problems:
            semantics = source_semantics(source_realm, diet_entry)
            if semantics is None:
                problems.append('diet groups could not be mapped to canonical semantics')

        # plort identity from the classification's canonicalized produces relation.
        plort_canonical = ''
        produces_native = diet_entry['produces'] if diet_entry else []
        if row is not None:
            produces = source.get('produces') or []
            if len(produces) == 1:
                plort_canonical = produces[0]
            elif produces:
                problems.append(f'multiple or unknown canonical plort relations {produces}')

        # stable native ids
        if canonical in previous:
            native_id, plort_id = previous[canonical]
        else:
            while free in used_ids:
                free += NATIVE_ID_SLIME_STEP
            native_id = free
            plort_id = free + 1
            used_ids.update({native_id, plort_id})
        free = max(free, native_id + NATIVE_ID_SLIME_STEP)

        group_ids = favorites = additional = None
        notes = []
        if not problems:
            group_ids = target_group_ids(target_realm, semantics, target_diet_doc)
            if group_ids is None:
                problems.append('no target-native food group carries one of the source semantics')
            elif not group_ids:
                # An empty captured diet cannot claim working diet behavior: fail closed until the
                # species' feeding is properly captured or explicitly modeled as non-feeding.
                problems.append('source diet has no captured food groups (unsupported or uncaptured)')
            favorites, fav_dropped = crosswalk_items(diet_entry['favorites'], source_vocab, target_vocab)
            additional, add_dropped = crosswalk_items(diet_entry['additional_foods'], source_vocab, target_vocab)
            if fav_dropped:
                notes.append(f'favorites without a target crosswalk (dropped): {fav_dropped}')
            if add_dropped:
                notes.append(f'additional foods without a target crosswalk (dropped): {add_dropped}')

        status = 'READY' if not problems else 'PENDING_DATA'
        display_name = source.get('display_name') or canonical
        templates = DEFAULT_TEMPLATES[target_realm]
        override = TEMPLATE_OVERRIDES.get(canonical, {})
        entry = {
            'canonical_id': canonical,
            'display_name': display_name,
            'status': status,
            'blockers': [f'can_largofy source={diet_entry["can_largofy"]} target=false (largos reserved)' ] if diet_entry else [],
            'native_id': native_id,
            'native_ref_id': '',
            'plort': {
                'canonical_id': plort_canonical,
                'display_name': (display_name + ' Plort') if plort_canonical else '',
                'native_id': plort_id if plort_canonical else 0,
                'native_ref_id': '',
                'template_native_ref': templates['plort'] if plort_canonical else '',
            },
            'template': {
                'slime_native_ref': override.get('slime', templates['slime']),
                'plort_native_ref': templates['plort'],
            },
            'diet': {
                'major_food_groups': group_ids or [],
                'favorites': favorites or [],
                'additional_foods': additional or [],
                'favorite_production_count': diet_entry['favorite_production_count'] if diet_entry else 2,
            },
            'flags': {
                'can_largofy': False,
                'visual_mode': 'PLACEHOLDER_TEMPLATE',
                'behavior_classification': 'BLOCKED_ON_LOCAL_QUERY',
            },
        }
        if problems:
            entry['blockers'] = entry['blockers'] + problems
        entry['notes'] = notes
        if source_appearance and source.get('native_ref'):
            app_entry = source_appearance.get(source['native_ref'])
            if app_entry is None:
                notes.append('appearance capture has no entry for the source native_ref (template colors kept)')
            else:
                palette = app_entry.get('palette_new') or app_entry.get('palette_legacy')
                if palette:
                    entry['appearance'] = {
                        'palette': {
                            'top': palette[0:4],
                            'middle': palette[4:8],
                            'bottom': palette[8:12],
                        },
                        'ammo': palette[12:16],
                        'save_set': app_entry.get('save_set', ''),
                        'source': ('new' if app_entry.get('palette_new') else 'legacy') + ':classic',
                    }
                else:
                    notes.append('appearance capture entry has no palette values (template colors kept)')
        if target_realm == 'SR2':
            suffix = canonical.split('.', 1)[1]
            entry['native_ref_id'] = 'SlimeBridge.SlimeDefinition.' + suffix
            if plort_canonical:
                entry['plort']['native_ref_id'] = 'SlimeBridge.IdentifiableType.' + suffix + 'Plort'
        entries.append(entry)
        summary_entries.append({
            'canonical_id': canonical,
            'status': status,
            'native_id': native_id,
            'plort_native_id': entry['plort']['native_id'],
            'source_realm': source_realm,
            'source_native_ref': source.get('native_ref'),
            'source_definition_hash': source.get('definition_hash'),
            'semantics': sorted(semantics) if semantics else None,
            'produces_ref': produces_native,
            'problems': problems,
        })

    spec = {
        'schema': SCHEMA,
        'schema_version': SCHEMA_VERSION,
        'realm': realm,
        'source_classification_sha256': _classification_sha(classification),
        'source_diet_sha256': _doc_sha(sr1_diet) if realm == 'SR1' else _doc_sha(sr2_diet),
        'entries': entries,
    }
    fixed = set(diet_overrides.apply(entries, realm))     # curated diets for species whose source diet cannot be translated
    for row in summary_entries:
        if row['canonical_id'] in fixed:
            row['status'] = next(e['status'] for e in entries if e['canonical_id'] == row['canonical_id'])
    summary = {
        'realm': realm,
        'ready': sum(1 for e in entries if e['status'] == 'READY'),
        'pending_data': sum(1 for e in entries if e['status'] == 'PENDING_DATA'),
        'entries': summary_entries,
    }
    return spec, summary


def _attach_path(doc, path):
    doc = dict(doc)
    doc['_path'] = str(path)
    return doc


def _load_classification(directory):
    directory = Path(directory)
    document = json.loads((directory / 'classification.json').read_text(encoding='utf-8'))
    document['_path'] = str(directory)
    return document


def _validate_out_dir(out_dir):
    from tools.lab.paths import lexical_path, refuse_reparse
    root = lexical_path(OUTPUT_ROOT)
    target = lexical_path(Path(out_dir))
    if target != root and root not in target.parents:
        raise SystemExit(f'--out-dir must stay under {OUTPUT_ROOT}')
    refuse_reparse(target)
    if target.resolve() != root.resolve() and root.resolve() not in target.resolve().parents:
        raise SystemExit(f'--out-dir must stay under {OUTPUT_ROOT}')
    return target


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--classification', default=str(DEFAULT_CLASSIFICATION),
                        help='directory holding classification.json and the realm snapshots')
    parser.add_argument('--sr1-diet', required=True, help='path to an SR1 diet-snapshot.json')
    parser.add_argument('--sr2-diet', required=True, help='path to an SR2 diet-snapshot.json')
    parser.add_argument('--previous-spec', default=None, help='previous spec to keep native ids stable')
    parser.add_argument('--previous-dir', default=None,
                        help='directory of previous per-realm specs (import-spec-SR1.json/SR2.json)')
    parser.add_argument('--sr1-appearance', default=None, help='optional SR1 appearance-colors.json capture')
    parser.add_argument('--sr2-appearance', default=None, help='optional SR2 appearance-colors.json capture')
    parser.add_argument('--out-dir', default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    classification = _load_classification(args.classification)
    try:
        sr1_diet = _attach_path(load_diet_snapshot(args.sr1_diet), args.sr1_diet)
        sr2_diet = _attach_path(load_diet_snapshot(args.sr2_diet), args.sr2_diet)
    except DietCaptureError as exc:
        raise SystemExit(f'diet capture refused: {exc}')
    if sr1_diet['realm'] != 'SR1' or sr2_diet['realm'] != 'SR2':
        raise SystemExit('diet captures do not match their realms')
    sr_app = {}
    for realm, arg in (('SR1', args.sr1_appearance), ('SR2', args.sr2_appearance)):
        if not arg:
            sr_app[realm] = None
            continue
        try:
            doc = _attach_path(load_appearance_snapshot(arg), arg)
        except AppearanceCaptureError as exc:
            raise SystemExit(f'appearance capture refused: {exc}')
        if doc['realm'] != realm:
            raise SystemExit(f'{realm} appearance capture does not match its realm')
        sr_app[realm] = doc
    if args.previous_dir:
        previous_by_realm = {r: load_previous_spec(Path(args.previous_dir) / f'import-spec-{r}.json')
                             for r in ('SR1', 'SR2')}
    else:
        previous = load_previous_spec(args.previous_spec) if args.previous_spec else {}
        previous_by_realm = {'SR1': previous, 'SR2': previous}
    out_dir = _validate_out_dir(args.out_dir if args.out_dir else
                                OUTPUT_ROOT / datetime.now().strftime('run-%Y%m%d-%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {'schema': 'bridge-imported-species-spec-run', 'schema_version': 1,
               'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f'), 'realms': {}}
    for realm in ('SR1', 'SR2'):
        spec, realm_summary = build_spec(realm, classification, sr1_diet, sr2_diet, previous_by_realm[realm],
                                         sr1_app=sr_app['SR1'], sr2_app=sr_app['SR2'])
        target = out_dir / f'import-spec-{realm}.json'
        target.write_text(json.dumps(spec, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        realm_summary['spec'] = target.name
        realm_summary['spec_sha256'] = _sha256(target)
        summary['realms'][realm] = realm_summary
    (out_dir / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({realm: {'ready': summary['realms'][realm]['ready'],
                              'pending_data': summary['realms'][realm]['pending_data'],
                              'spec_sha256': summary['realms'][realm]['spec_sha256'][:16]} for realm in ('SR1', 'SR2')},
                     indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
