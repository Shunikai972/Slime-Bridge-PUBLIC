"""Warm SR1<->SR2 transitions between two separate game processes (never Unity-inside-Unity).

``machine`` is the durable, event-driven state machine; ``coordinator`` turns its state into
commands for process/window/input/audio/source adapters (implemented locally); ``simulator``
drives both with fake adapters and deterministic faults; ``view`` maps state to UI modes.
"""
