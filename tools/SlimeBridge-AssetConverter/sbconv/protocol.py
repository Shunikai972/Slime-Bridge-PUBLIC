"""JSON-lines protocol for embedding sbconv in the SlimeBridge launcher (no Python import needed).

Request  (one JSON object per line on stdin):  {"id": "1", "op": "scan|list|convert|verify|status|engines", "args": {...}}
Response (one JSON object per line on stdout):
    {"id": "1", "event": "log",      "message": "..."}            zero or more progress lines
    {"id": "1", "event": "result",   "ok": true, "result": {...}}   exactly one, last
    {"id": "1", "event": "error",    "error": "..."}               instead of result on protocol/usage errors
``args`` mirror the Python API keyword names (see sbconv.api). Operations are sequential; stdout carries
protocol lines only (diagnostics go to stderr)."""

import json
import sys

from . import API_VERSION, __version__, api

OPS = {'scan', 'list', 'convert', 'verify', 'preview', 'export', 'unity-verify', 'status', 'engines'}


def _write(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + '\n')
    sys.stdout.flush()


def handle(req, default_output):
    rid = req.get('id')
    op = req.get('op')
    args = dict(req.get('args') or {})
    if op not in OPS:
        _write({'id': rid, 'event': 'error', 'error': f'unknown op {op!r}; expected one of {sorted(OPS)}'})
        return
    args.setdefault('output', default_output)
    log = lambda m: _write({'id': rid, 'event': 'log', 'message': m})
    try:
        if op == 'scan':
            args.pop('output', None)
            res = api.scan(**args)
        elif op == 'engines':
            args.pop('output', None)
            res = api.engines(**args)
        elif op == 'status':
            res = api.status(**args)
        elif op == 'list':
            res = api.list_species(log=log, **args)
        elif op == 'convert':
            res = api.convert(log=log, **args)
        elif op == 'preview':
            res = api.preview(log=log, **args)
        elif op == 'export':
            res = api.export_bridge(log=log, **args)
        elif op == 'unity-verify':
            res = api.unity_verify(log=log, **args)
        else:
            res = api.verify(log=log, **args)
        _write({'id': rid, 'event': 'result', 'ok': bool(res.get('ok', True)), 'result': res})
    except TypeError as e:
        _write({'id': rid, 'event': 'error', 'error': f'bad arguments for {op}: {e}'})
    except Exception as e:
        _write({'id': rid, 'event': 'error', 'error': f'{type(e).__name__}: {e}'})


def serve(default_output):
    _write({'event': 'hello', 'api_version': API_VERSION, 'tool_version': __version__, 'ops': sorted(OPS)})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            _write({'event': 'error', 'error': f'invalid JSON: {e}'})
            continue
        handle(req, default_output)
    return 0
