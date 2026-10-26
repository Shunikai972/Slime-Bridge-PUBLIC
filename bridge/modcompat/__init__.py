"""Mod compatibility: manifests, LOCAL static evidence, classification, set analysis and adapter skeletons.

- ``manifest``: ``bridge-mod-manifest`` v1 + SRML ``modinfo.json`` and ``bridge-melon-metadata`` parsers.
- ``evidence``: ``bridge-mod-static-evidence`` v1 produced by a LOCAL analyzer (never by Cloud).
- ``analyzer``: ``bridge-mod-compat-report`` / ``bridge-mod-set-report`` and deploy-unit conversion.
- ``skeleton``: safe adapter skeletons (templates with TODO(LOCAL_QUERY) markers, never a port).

Everything here is static and metadata-only: no mod binary is opened, executed or decompiled in
Cloud. See ``docs/MOD_COMPATIBILITY.md``.
"""
