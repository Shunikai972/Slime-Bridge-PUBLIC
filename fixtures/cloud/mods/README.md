# Synthetic mod-compatibility corpus

Fictional mods for `tests/services/test_modcompat.py`. Every id is `fixture.*` and every hash is the sha256 of a fixture label, not of a real file. No real mod names, binaries, assets or code appear here.

- `case-*.json` (`bridge-mod-fixture-case` v1): an input (`SRML_MODINFO`, `MELON_METADATA` or `BRIDGE_MANIFEST`) given either as a document or as raw text for malformed input. Each case also has optional synthetic static evidence and the expected parse result, classification, rule, portability and deploy-unit status for both target realms.
- `set-sr1.json`: what `analyze_set` must find when every parseable case is analyzed together for SR1. That covers a cycle, a missing dependency, a duplicate id, a version conflict and case-insensitive content-id/file conflicts.

Expected verdicts hold only inside this synthetic universe. They are not native evidence. See `docs/MOD_COMPATIBILITY.md`.
