"""Species catalog: every slime / largo defined by a game, discovered from the game's own
``SlimeDefinition`` data (nothing is hard-coded per species). Output rows are plain dicts."""

import re

from .extract.prefab import ref_key


def fld(d, *names, default=None):
    """Field lookup tolerant to the SR1 (``Name``) / SR2 (``_name``, ``name``) naming differences."""
    for n in names:
        for cand in (n, '_' + n, n[:1].lower() + n[1:], '_' + n[:1].lower() + n[1:]):
            if cand in d:
                return d[cand]
    return default


def slug(name):
    s = re.sub(r'[^A-Za-z0-9]+', ' ', str(name)).strip().lower()
    return re.sub(r'\s+', '_', s)


def _definition_rows(session, readers):
    rows = []
    for rd in readers:
        try:
            d = rd.parse_as_dict()
        except Exception as e:
            rows.append({'error': f'{type(e).__name__}: {e}', 'ref': ref_key(rd)})
            continue
        owner = rd.assets_file
        prefab = session.resolve_pptr(owner, fld(d, 'prefab'))
        apps = [session.resolve_pptr(owner, a) for a in (fld(d, 'AppearancesDefault', default=[]) or [])]
        bases = [session.resolve_pptr(owner, b) for b in (fld(d, 'BaseSlimes', default=[]) or [])]
        base_names = []
        for b in bases:
            try:
                base_names.append(b.peek_name())
            except Exception:
                base_names.append(None)
        rows.append({
            'ref': ref_key(rd), 'name': rd.peek_name(), 'display': fld(d, 'Name'),
            'identifiable_id': fld(d, 'IdentifiableId'), 'is_largo': bool(fld(d, 'IsLargo', default=0)),
            'can_largofy': bool(fld(d, 'CanLargofy', default=0)), 'prefab_scale': fld(d, 'PrefabScale', default=1.0),
            'prefab': ref_key(prefab), 'appearances': [ref_key(a) for a in apps if a is not None],
            'base_slimes': base_names, 'base_slime_refs': [ref_key(b) for b in bases],
            'color': fld(d, 'color'), 'sounds': ref_key(session.resolve_pptr(owner, fld(d, 'Sounds'))),
            'is_plort': bool(fld(d, 'IsPlort', default=0)),
        })
    return rows


def sr1_slime_definitions(session):
    """SR1 definitions carry no prefab field: the slime prefab points back at its definition through
    its SlimeAppearanceApplicator, so the link is rebuilt from that (data-driven, no name guessing)."""
    readers, applicators = [], []
    for rd in session.objects():
        if rd.type.name == 'MonoBehaviour':
            sc = session.script_of(rd)
            if sc and sc[0] == 'SlimeDefinition':
                readers.append(rd)
            elif sc and sc[0] == 'SlimeAppearanceApplicator':
                applicators.append(rd)
    prefab_of = {}
    for ap in applicators:
        try:
            d = ap.parse_as_dict()
            definition = session.resolve_pptr(ap.assets_file, d.get('SlimeDefinition'))
            go = session.resolve_pptr(ap.assets_file, d.get('m_GameObject'))
            if definition is not None and go is not None:
                prefab_of.setdefault(ref_key(definition), []).append(ref_key(go))
        except Exception:
            continue
    rows = _definition_rows(session, readers)
    for row in rows:
        cands = prefab_of.get(row.get('ref'), [])
        row['prefab'] = cands[0] if cands else row.get('prefab')
        if len(cands) > 1:
            row['prefab_candidates'] = cands
    return rows


def sr2_slime_definitions(session, inventory, data_dir):
    """SR2 definitions are Addressables assets; locate them through each bundle's container map."""
    readers, bundles = [], []
    for rel, rec in inventory.items():
        hits = [(k, v) for k, v in rec.get('container', {}).items()
                if re.match(r'^assets/actor/slime/(base|largo)/definition/[^/]+\.asset$', k, re.I)]
        if hits:
            bundles.append(rel)
    session.load(bundles)
    for rel in bundles:
        for k, v in inventory[rel]['container'].items():
            if re.match(r'^assets/actor/slime/(base|largo)/definition/[^/]+\.asset$', k, re.I):
                rd = None
                for f in session.serialized_files():
                    o = f.objects.get(v[1])
                    if o is not None and o.type.name == 'MonoBehaviour':
                        rd = o
                        break
                if rd is not None:
                    readers.append((k, rd))
    rows = _definition_rows(session, [r for _, r in readers])
    for (k, _), row in zip(readers, rows):
        row['container_path'] = k
    return rows
