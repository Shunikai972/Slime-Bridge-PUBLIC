"""Shared run context: scans installs once, builds/caches inventory, catalog and sessions, and owns the
single confined output root. Everything the CLI/API does goes through this object."""

import json
import time
from pathlib import Path

from . import __version__, api, catalog, inventory
from .engines.unitypy_engine import UnityPyEngine
from .safefs import OutputRoot, sha256_json


class ConverterError(RuntimeError):
    pass


class Context:
    def __init__(self, output, sr1_path=None, sr2_path=None, search_roots=(), deep=False, log=print, workers=3, dry_run=False):
        self.dry_run = dry_run
        self._log_cb = log
        self.log = self._log
        self.workers = workers
        self.scan = api.scan(sr1_path=sr1_path, sr2_path=sr2_path, search_roots=search_roots, deep=deep, full_hash=True)
        self.installs = self.scan['games']
        protected = [i['root'] for i in self.installs.values()]
        self.out = OutputRoot(output, protected=protected, dry_run=dry_run)
        self._inv, self._sess, self._cat, self._anim = {}, {}, {}, {}

    def _log(self, msg):
        self._log_cb(msg)
        if not self.dry_run:
            try:
                import datetime
                p = self.out.resolve('logs', f'sbconv-{datetime.date.today():%Y%m%d}.log')
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, 'a', encoding='utf-8') as f:
                    f.write(f'{datetime.datetime.now():%H:%M:%S} {msg}' + chr(10))
            except OSError:
                pass

    # -- installs -------------------------------------------------------------------------------
    def install(self, game):
        if game not in self.installs:
            hint = self.scan['not_found_hints'].get(game, ['not found'])[0]
            raise ConverterError(f'{game} install unavailable: {hint}')
        return self.installs[game]

    def fingerprint_key(self, game):
        inst = self.install(game)
        return sha256_json({'fp': inst['fingerprint'], 'unity': inst['unity']['version'], 'tool': __version__})[:16]

    # -- inventory / session ----------------------------------------------------------------------
    def inventory(self, game):
        if game not in self._inv:
            self.log(f'[{game}] inventory...')
            self._inv[game] = inventory.build(self.install(game), self.out, workers=self.workers,
                                              progress=lambda i, n, f, e: self.log(f'  {game} inventory {i}/{n}'
                                                                                    + (f' ERROR {e}' if e else '')) if i % 20 == 0 or e else None)
        return self._inv[game]

    def session(self, game):
        if game not in self._sess:
            inst = self.install(game)
            inv = self.inventory(game) if inst['backend'] == 'il2cpp' else None
            s = UnityPyEngine().open(inst, inventory=inv)
            if inst['backend'] == 'il2cpp':
                pass  # bundles are loaded on demand
            else:
                s.load([h['name'] for h in inst['serialized_files'] if not h['name'].startswith('level')])
            t0 = time.time()
            s.enable_typetrees()
            self.log(f'[{game}] typetrees generated in {time.time() - t0:.0f}s')
            self._sess[game] = s
        return self._sess[game]

    # -- catalog -----------------------------------------------------------------------------------
    def catalog(self, game):
        if game in self._cat:
            return self._cat[game]
        key = self.fingerprint_key(game)
        rel = f'catalog/{game}-{key}.json'
        cached = self.out.read_json(rel)
        if cached:
            self._cat[game] = cached['rows']
            return cached['rows']
        s = self.session(game)
        if game == 'SR1':
            rows = catalog.sr1_slime_definitions(s)
        else:
            rows = catalog.sr2_slime_definitions(s, self.inventory(game), s.data_dir)
        if not rows:
            raise ConverterError(f'{game}: no SlimeDefinition found; refusing to cache an empty catalog')
        self.out.write_json(rel, {'game': game, 'fingerprint_key': key, 'rows': rows})
        self._cat[game] = rows
        return rows

    def find_row(self, game, name):
        rows = [r for r in self.catalog(game) if 'error' not in r]
        low = name.lower()
        hit = [r for r in rows if r['name'].lower() == low or (r.get('display') or '').lower() == low]
        if not hit:
            from .extract.species import canonical_id
            hit = [r for r in rows if canonical_id(r) == low]
        if not hit:
            raise ConverterError(f'{game}: no species named {name!r} (use `sbconv list --game {game}`)')
        return hit[0]

    # -- animation -----------------------------------------------------------------------------------
    def anim(self, game):
        if game not in self._anim:
            from . import anim_project
            inv = self.inventory(game) if self.install(game)['backend'] == 'il2cpp' else None
            try:
                self._anim[game] = anim_project.ensure_export(self.install(game), self.out.root / 'work', inv, log=self.log)
            except Exception as e:
                self.log(f'[{game}] animation export unavailable: {e}')
                self._anim[game] = None
        return self._anim[game]

    # -- material templates --------------------------------------------------------------------------
    def templates(self, game):
        if not hasattr(self, '_tpl'):
            self._tpl = {}
        if game in self._tpl:
            return self._tpl[game]
        from .materials import templates as T
        key = self.fingerprint_key(game)
        rel = f'templates/{game}-{key}.json'
        cached = self.out.read_json(rel)
        if cached and cached.get('version') == T.VERSION:
            self._tpl[game] = cached
            return cached
        rows = [r for r in self.catalog(game) if 'error' not in r]  # largos use shader variants base species do not
        self.log(f'[{game}] building material template library from {len(rows)} species...')
        data = T.build(self.session(game), rows, game)
        self.out.write_json(rel, data)
        self._tpl[game] = data
        return data


    # -- what the target game really offers ---------------------------------------------------------------
    def playable_ids(self, game):
        """Canonical ids of species a player can meet in ``game``.

        SR2 ships unfinished SlimeDefinitions (Rad, Quantum, Mosaic, Glitch...) that have no Pedia entry and no zones: a
        base slime counts as present only when it has its Pedia entry. SR1 definitions are all playable. A largo is
        present only when its definition exists and both parents are present."""
        import re
        from .extract.species import canonical_id
        if not hasattr(self, '_playable'):
            self._playable = {}
        if game in self._playable:
            return self._playable[game]
        rows = [r for r in self.catalog(game) if 'error' not in r]
        if game == 'SR2':
            pedia = set()
            for rec in self.inventory(game).values():
                for k in rec.get('container', {}):
                    m = re.match(r'^assets/lookupdata/ui/pedia/categories/slimes/entries/(.+)\.asset$', k, re.I)
                    if m:
                        pedia.add(m.group(1).lower())
            base_ok = {r['name'] for r in rows if not r.get('is_largo') and r['name'].lower() in pedia}
        else:
            base_ok = {r['name'] for r in rows if not r.get('is_largo')}
        ids = {canonical_id(r) for r in rows if not r.get('is_largo') and r['name'] in base_ok}
        for r in rows:
            if r.get('is_largo') and r.get('base_slimes') and all(b in base_ok for b in r['base_slimes']):
                ids.add(canonical_id(r))
        self._playable[game] = ids
        return ids
