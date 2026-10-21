"""Content deployment: deploy units, plans, a durable transaction journal and an executor.

Everything destructive is planned first (``planner``), then executed as one journaled
transaction (``executor``) against a ``TargetFS`` that the caller supplies. Cloud ships an
in-memory FS and a confined directory FS; installing into a real game's mod folder is a local
adapter decision and never happens implicitly.
"""
