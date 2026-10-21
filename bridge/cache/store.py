"""Deterministic product cache keyed by every input that can change a conversion product.

Key = SHA-256 of the canonical inputs from ``engine.cache_inputs``: source/target realms,
source build, target build/facts/profiles, the species classification row and definition
hash, the Merkle closure hash of its dependency graph (so one changed texture invalidates
exactly the species that reach it), the source profile entries actually used, and the
converter/component versions. Synthetic and real inputs can never share a key.

Reads re-derive the key from the stored inputs and re-hash the stored result; anything that
does not match exactly is reported CORRUPT and never returned (cache poisoning, torn writes,
copied entries). Writes are exclusive temp + fsync + atomic replace inside a root that must
not be, or contain, a symlink. Only metadata is cached; no asset bytes.
"""

import json

from bridge.content.canonical import canonical_json, is_sha256, sha256_json
from .fs import atomic_write, confined_root, refuse_links

ENTRY_SCHEMA = 'bridge-cache-entry'


def product_key(inputs: dict) -> str:
    return sha256_json({'schema': 'bridge-cache-key', 'schema_version': 1, 'inputs': inputs})


def _reject_constant(name):
    raise ValueError(f'non-finite JSON constant {name}')


def explain_change(old_inputs, new_inputs):
    """Names of the top-level inputs that differ (why a lookup missed)."""
    if not isinstance(old_inputs, dict):
        return ['NO_PREVIOUS_PRODUCT']
    keys = sorted(set(old_inputs) | set(new_inputs))
    return [k for k in keys if canonical_json(old_inputs.get(k)) != canonical_json(new_inputs.get(k))]


class ContentCache:
    def __init__(self, root):
        self.root = confined_root(root)

    def _entry_path(self, key):
        return self.root / 'products' / key[:2] / f'{key}.json'

    def _index_path(self, inputs):
        ident = sha256_json({'canonical_id': inputs.get('canonical_id'), 'source': inputs.get('source_realm'),
                             'target': inputs.get('target_realm'), 'synthetic': inputs.get('synthetic')})
        return self.root / 'index' / f'{ident}.json'

    def _read_json(self, path):
        refuse_links(self.root, path)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding='utf-8'), parse_constant=_reject_constant)

    def get(self, inputs):
        """Return {'status': HIT|MISS|CORRUPT, 'key', 'result', 'changed'}."""
        key = product_key(inputs)
        path = self._entry_path(key)
        try:
            entry = self._read_json(path)
        except (OSError, ValueError, RecursionError) as exc:
            return {'status': 'CORRUPT', 'key': key, 'result': None, 'changed': [f'unreadable: {type(exc).__name__}']}
        if entry is None:
            try:
                previous = self._read_json(self._index_path(inputs))
            except (OSError, ValueError, RecursionError):
                previous = None
            return {'status': 'MISS', 'key': key, 'result': None,
                    'changed': explain_change((previous or {}).get('inputs'), inputs)}
        try:
            problems = self._entry_problems(entry, key, inputs)
        except (TypeError, ValueError, RecursionError) as exc:
            problems = [f'unhashable entry: {type(exc).__name__}']
        if problems:
            return {'status': 'CORRUPT', 'key': key, 'result': None, 'changed': problems}
        return {'status': 'HIT', 'key': key, 'result': entry['result'], 'changed': []}

    @staticmethod
    def _entry_problems(entry, key, inputs):
        problems = []
        if not isinstance(entry, dict) or entry.get('schema') != ENTRY_SCHEMA or entry.get('schema_version') != 1:
            problems.append('schema')
        elif entry.get('key') != key or product_key(entry.get('inputs')) != key:
            problems.append('key/inputs mismatch')
        elif not is_sha256(entry.get('result_hash')) or sha256_json(entry.get('result')) != entry['result_hash']:
            problems.append('result hash mismatch')
        else:
            result = entry['result']
            if not isinstance(result, dict) or result.get('canonical_id') != inputs.get('canonical_id') or \
                    result.get('synthetic') != inputs.get('synthetic'):
                problems.append('result identity mismatch')
        return problems

    def put(self, inputs, result):
        key = product_key(inputs)
        if result.get('canonical_id') != inputs.get('canonical_id') or result.get('synthetic') != inputs.get('synthetic'):
            raise ValueError('Refusing to cache a result under inputs for a different product')
        entry = {'schema': ENTRY_SCHEMA, 'schema_version': 1, 'key': key, 'inputs': inputs,
                 'result': result, 'result_hash': sha256_json(result)}
        self._write(self._entry_path(key), entry)
        self._write(self._index_path(inputs), {'key': key, 'inputs': inputs})
        return key

    def _write(self, path, value):
        atomic_write(self.root, path, canonical_json(value).encode('utf-8'))
