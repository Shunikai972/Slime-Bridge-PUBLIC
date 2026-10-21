"""Package writer: every file goes through the confined, atomic ``OutputRoot`` and is recorded with its
SHA-256 so the package carries its own integrity manifest."""

import hashlib
import json

from ..safefs import OutputRoot


class PackageWriter:
    def __init__(self, out_root: OutputRoot, base, strict=False):
        self.strict = strict
        self.out = out_root
        self.base = base.strip('/\\')
        self.files = {}  # rel -> {'sha256','bytes'}

    def put_bytes(self, rel, data):
        if rel in self.files:
            return rel
        self.out.write_bytes(f'{self.base}/{rel}', data)
        self.files[rel] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        return rel

    def put_json(self, rel, obj):
        data = (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, default=_default) + '\n').encode('utf-8')
        self.out.write_bytes(f'{self.base}/{rel}', data)
        self.files[rel] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        return rel

    def read_bytes(self, rel):
        return self.out.resolve(f'{self.base}/{rel}').read_bytes()


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    raise TypeError(f'not JSON serialisable: {type(o).__name__}')
