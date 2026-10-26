"""Deterministic fault injection: named points fire on exact hit counts, never on timers or randomness.

A ``Fault`` names an injection point (``'transport.send_after'``, ``'result.drop'`` ...), the hit
on which it first fires (1-based) and how many consecutive hits it affects. Components call
``faults.check(point, **context)`` at each injection point; it returns the fault when it fires and
records every hit, so a test can assert exactly which faults fired and in what order.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fault:
    point: str
    on_hit: int = 1
    times: int = 1
    payload: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.point or self.on_hit < 1 or self.times < 1:
            raise ValueError('fault needs a point, on_hit >= 1 and times >= 1')


class FaultPlan:
    def __init__(self, faults=()):
        self.faults = tuple(faults)
        self.hits = {}
        self.fired = []

    def check(self, point, **context):
        hit = self.hits[point] = self.hits.get(point, 0) + 1
        for fault in self.faults:
            if fault.point == point and fault.on_hit <= hit < fault.on_hit + fault.times:
                self.fired.append({'point': point, 'hit': hit, **{k: context[k] for k in sorted(context)}})
                return fault
        return None

    def fired_points(self):
        return [entry['point'] for entry in self.fired]
