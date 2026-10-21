"""Synthetic simulation layer: deterministic fakes and fault injection for Cloud tests and demo mode.

Nothing produced here is runtime evidence. Simulator providers use the ``sim.`` id prefix, which
production services refuse to trust (see ``bridge.counterpart.service.SYNTHETIC_PREFIX``).
"""
