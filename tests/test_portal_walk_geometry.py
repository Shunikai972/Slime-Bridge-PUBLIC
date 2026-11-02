"""Geometry contracts for a walking player; native keyboard crossings remain the runtime acceptance gate."""
from pathlib import Path
import math
import re

ROOT = Path(__file__).resolve().parents[1]


def test_native_sr1_trigger_reaches_a_walking_capsule():
    source = (ROOT / 'mods/sr1-bridge/SanctuaryBuilder.cs').read_text()
    block = source.split('var trigger = new GameObject("trigger");', 1)[1].split('var playerTrigger', 1)[0]
    # Actual currently shipped layout: ring centre5.3m, trigger radius3.2m.
    # Synthetic capsule extends0..1.8m above the floor; this is not a measured native-player assertion.
    height = 5.3 if 'trigger.transform.localPosition = center;' in block else min(1.2, 5.3)
    assert 'trigger.transform.localPosition = center;' in block or 'Mathf.Min(1.2f, spec.RingCenterHeight)' in block
    distance_to_capsule = max(0.0, height - 1.8)
    assert distance_to_capsule <= 3.2, 'Walking into the visible portal does not reach its elevated spherical trigger'


def test_sr1_arrival_is_outside_the_trigger_including_player_radius():
    source = (ROOT / 'mods/sr1-bridge/SanctuaryHost.cs').read_text()
    body = source.split('internal bool PlacePlayerAtPortal(bool emerge)', 1)[1].split('// -----', 1)[0]
    if 'emerge ? 3.4f : 5.5f' in body:
        arrival = 3.4
    else:
        assert '_layout.Portal.TriggerRadius + 2f' in body
        arrival = max(4.2, 3.2 + 2.0)
    assert arrival > 3.2 + 0.5, 'Arrival overlaps the trigger, consuming the entry during the re-entry cooldown'
