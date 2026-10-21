"""Extraction worker: runs the species/Largo pipeline in its own process under a memory ceiling.

The launcher starts it (`python -m bridge.content.extract_worker request.json`); it reports one `@@{json}` line per event on stdout so the
launcher can show progress and cancel (kill) it. The request file carries the `mode` (`sr2-to-sr1`, the default, or `sr1-to-sr2`), the rows to build, the game folder, the cache root and the
memory limit in GB (the launcher's slider). Rows that need more memory than the limit are reported as SKIPPED_RAM with the amount they
need, never crashed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(event: str, **fields) -> None:
    print('@@' + json.dumps({'event': event, **fields}, default=str), flush=True)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print('usage: python -m bridge.content.extract_worker request.json', file=sys.stderr)
        return 2
    request = json.loads(Path(args[0]).read_text(encoding='utf-8'))
    ram_gb = request.get('ram_gb')
    from bridge.content.memcap import limit_process_memory
    capped = limit_process_memory(ram_gb) if ram_gb else False
    emit('start', ram_gb=ram_gb, capped=capped, rows=len(request['rows']))
    progress = lambda done, total, message: emit('progress', done=done, total=total, message=message)  # noqa: E731
    try:
        if request.get('mode') == 'sr1-to-sr2':
            from bridge.content import sr1_pipeline
            outcome = sr1_pipeline.run(request['rows'], Path(request['sr1_dir']), Path(request['cache_root']), force=bool(request.get('force')),
                                       progress=progress, ram_gb=ram_gb)
        else:
            from bridge.content import species_pipeline as pipeline
            outcome = pipeline.run(request['rows'], Path(request['sr2_dir']), Path(request['cache_root']), force=bool(request.get('force')),
                                   progress=progress, ram_gb=ram_gb)
    except MemoryError:
        emit('fatal', error=f'out of memory at the {ram_gb} GB limit: raise the memory limit and run Extract again')
        return 3
    except Exception as exc:  # noqa: BLE001 - reported to the launcher
        emit('fatal', error=f'{type(exc).__name__}: {exc}'[:400])
        return 1
    emit('result', outcome=outcome)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
