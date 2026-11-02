"""Static wiring checks complement the native timed crossing oracle; not runtime performance proof."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('realm,file', [('sr1-bridge', 'Sr1Travel.cs'), ('sr2-bridge', 'Sr2Travel.cs')])
def test_covered_departure_enters_recoverable_standby_before_reporting_ready(realm, file):
    source = (ROOT / 'mods' / realm / file).read_text()
    departure = source.split('case "prepare_departure":', 1)[1].split('case "hold":', 1)[0]
    assert '_throttle.Apply(true)' in departure
    assert 'public bool Held => _held || _throttle.Active;' in source
    assert 'departure_standby_failed' in departure
    assert '0.8f' in departure
    assert '_overlay.Open(1.1f' in source
