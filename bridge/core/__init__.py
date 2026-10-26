"""Platform core: unified diagnostics, schema checks and confined file I/O shared by Phase 2 services.

Dependency rule: ``bridge.core`` imports only ``bridge.content.canonical`` (the hashing/evidence
primitives) and the standard library. Every other package may import ``bridge.core``.
"""
