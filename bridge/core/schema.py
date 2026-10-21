"""Tiny declarative checker for untrusted JSON documents (never raises; returns problems).

    spec = obj({'id': string(), 'count': integer(minimum=0), 'tags': list_of(string())})
    problems = check(spec, document)

Unknown keys are problems unless ``obj(..., extra=True)``. Problems are 'path: reason' strings so
callers can wrap them in their own diagnostics.
"""

import math
import re

from bridge.content.canonical import is_sha256

_MISSING = object()


def check(spec, value, path='$'):
    problems = []
    spec(value, path, problems)
    return problems


def string(pattern=None, max_length=512, allow_empty=False):
    regex = re.compile(pattern) if pattern else None

    def inner(v, p, out):
        if not isinstance(v, str) or (not v and not allow_empty) or len(v) > max_length or \
                (regex is not None and not regex.fullmatch(v)):
            out.append(f'{p}: string{" matching " + pattern if pattern else ""} required')
    return inner


def sha256():
    def inner(v, p, out):
        if not is_sha256(v):
            out.append(f'{p}: lowercase sha256 required')
    return inner


def integer(minimum=None, maximum=None):
    def inner(v, p, out):
        if type(v) is not int or (minimum is not None and v < minimum) or (maximum is not None and v > maximum):
            out.append(f'{p}: integer in [{minimum}, {maximum}] required')
    return inner


def number():
    def inner(v, p, out):
        if type(v) not in (int, float) or not math.isfinite(v):
            out.append(f'{p}: finite number required')
    return inner


def boolean():
    def inner(v, p, out):
        if type(v) is not bool:
            out.append(f'{p}: boolean required')
    return inner


def enum(*values):
    def inner(v, p, out):
        # Type-strict: True must not satisfy enum(1), nor 1.0 satisfy enum(1).
        if not any(type(v) is type(x) and v == x for x in values):
            out.append(f'{p}: one of {sorted(map(str, values))}')
    return inner


def optional(check_fn):
    def inner(v, p, out):
        if v is not None:
            check_fn(v, p, out)
    return inner


def list_of(check_fn, max_items=100000, unique=False):
    def inner(v, p, out):
        if not isinstance(v, list) or len(v) > max_items:
            out.append(f'{p}: list required')
            return
        for i, item in enumerate(v):
            check_fn(item, f'{p}[{i}]', out)
        if unique:
            seen = [repr(x) for x in v]
            if len(set(seen)) != len(seen):
                out.append(f'{p}: duplicate entries')
    return inner


def map_of(key_check, value_check):
    def inner(v, p, out):
        if not isinstance(v, dict):
            out.append(f'{p}: object required')
            return
        for key, item in v.items():
            key_check(key, f'{p}.<key>', out)
            value_check(item, f'{p}.{key}', out)
    return inner


def any_json():
    def inner(v, p, out):
        stack = [(v, p)]
        while stack:
            item, where = stack.pop()
            if isinstance(item, float) and not math.isfinite(item):
                out.append(f'{where}: non-finite number')
            elif isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        out.append(f'{where}: non-string key')
                    stack.append((child, f'{where}.{key}'))
            elif isinstance(item, list):
                stack.extend((child, f'{where}[{i}]') for i, child in enumerate(item))
            elif not (item is None or isinstance(item, (str, bool, int, float))):
                out.append(f'{where}: not JSON')
    return inner


def obj(spec, extra=False):
    def inner(v, p, out):
        if not isinstance(v, dict):
            out.append(f'{p}: object required')
            return
        if not extra:
            for key in sorted(set(v) - set(spec), key=str):
                out.append(f'{p}.{key}: unknown field')
        for key, check_fn in spec.items():
            value = v.get(key, _MISSING)
            if value is _MISSING:
                out.append(f'{p}.{key}: missing')
            else:
                check_fn(value, f'{p}.{key}', out)
    return inner
