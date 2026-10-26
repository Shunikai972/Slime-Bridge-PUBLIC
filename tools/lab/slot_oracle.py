"""Protected current-account SR2 read-only occupancy probe, never creates a save."""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from tools.lab.oracle import GAMES, REPO_ROOT, run
from tools.lab.slot_probe import validate_slot_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-user-authorized', required=True, action='store_true')
    parser.add_argument('--sr2-save-id', required=True)
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    work = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / ('slot-probe-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    work.mkdir(parents=True, exist_ok=False)
    result = run(['SR2'], args.timeout, False, work, require_status=True, current_user=True,
                 diag_saves=True, sr2_save_id=args.sr2_save_id, guard_realms=['SR1', 'SR2'])
    (work / 'oracle-result.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    evidence: dict = {'passed': False, 'creation_authorized': False}
    try:
        log = GAMES['SR2'].parent / 'MelonLoader' / 'Latest.log'
        if log.stat().st_mtime < result['realms']['SR2']['launch_time']:
            raise RuntimeError('slot-probe log predates this owned launch')
        prefix = 'lab native slot probe: '
        reports = [json.loads(line.split(prefix, 1)[1]) for line in
                   log.read_text(encoding='utf-8', errors='replace').splitlines() if prefix in line]
        expected_pid = result['realms']['SR2']['launched_pid']
        reports = [r for r in reports if type(r.get('pid')) is int and r['pid'] == expected_pid]
        if len(reports) != 1:
            raise RuntimeError('expected one occupancy report from this exact owned process')
        evidence['report'] = validate_slot_report(reports[0])
        evidence['passed'] = result['passed'] and evidence['report']['status'] == 'OCCUPANCY_OBSERVED'
    except Exception as ex:
        evidence['error'] = f'{type(ex).__name__}: {ex}'
    (work / 'slot-probe-result.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(work), **evidence}, indent=2))
    raise SystemExit(0 if evidence['passed'] else 1)


if __name__ == '__main__':
    main()
