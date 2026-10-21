"""Pipeline stages and the persistent status ledger.

source game --extract--> IR package (EXTRACTED) --convert--> adapted IR (CONVERTED)
            --build--> runtime package / bundle (REBUILT) --verify--> OFFLINE_VERIFIED / UNITY_VERIFIED /
GAME_RUNTIME_VERIFIED. A species only ever moves forward by evidence recorded here; anything blocking
leaves it INCOMPLETE with the reasons.
"""

import json
import time

from . import __version__
from .context import Context
from .extract.species import extract_species, canonical_id
from .ir.writer import PackageWriter
from .safefs import sha256_json

STATUSES = ('EXTRACTED', 'CONVERTED', 'REBUILT', 'OFFLINE_VERIFIED', 'UNITY_VERIFIED', 'GAME_RUNTIME_VERIFIED', 'INCOMPLETE')
LEDGER = 'status.json'
EXTRACTOR_VERSION = 4


def ledger(ctx):
    return ctx.out.read_json(LEDGER, {'schema': 'sbconv-status', 'version': 1, 'entries': {}})


def record(ctx, key, **fields):
    led = ledger(ctx)
    ent = led['entries'].setdefault(key, {})
    ent.update(fields)
    ent['updated_by'] = f'sbconv {__version__}'
    ctx.out.write_json(LEDGER, led)
    return ent


def ir_dir(game, cid):
    return f'ir/{game}/{cid}'


def extract(ctx: Context, game, name, force=False):
    row = ctx.find_row(game, name)
    cid = canonical_id(row)
    key = f'{game}:{cid}'
    install = ctx.install(game)
    params = {'tool': __version__, 'extractor': EXTRACTOR_VERSION, 'fingerprint': ctx.fingerprint_key(game), 'row': row['ref'], 'appearance': 0}
    pkey = sha256_json(params)[:16]
    base = ir_dir(game, cid)
    existing = ctx.out.read_json(f'{base}/species.json')
    if existing and not force and existing.get('params_key') == pkey and existing.get('status') in ('EXTRACTED',):
        ctx.log(f'[{key}] up to date (cache hit)')
        return existing
    ctx.log(f'[{key}] extracting...')
    session = ctx.session(game)
    anim = ctx.anim(game)
    w = PackageWriter(ctx.out, base)
    t0 = time.time()
    manifest = extract_species(session, install, row, w, ctx.out.root / 'work', anim=anim)
    manifest['params_key'] = pkey
    w.put_json('species.json', manifest)
    record(ctx, key, game=game, canonical_id=cid, name=row['name'], status=manifest['status'], stage='extract',
           problems=len(manifest.get('problems', [])), seconds=round(time.time() - t0, 1), package=base)
    return manifest
