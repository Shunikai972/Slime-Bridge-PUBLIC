# Finished user-Largo import readiness

This slice extends `bridge/sdk/largo.py`, the existing read-only
`bridge-user-largo-package` v1 validator and planner. It does **not** create Largo
art, generate/redesign/remesh models, register a prefab, copy into a game, or
launch/deploy anything. The user's finished source directory remains read-only.
No user model directory or canonical naming catalog has been supplied or tested
here. All executable tests use synthetic fixtures.

## Workflow now

Run from the repository using its pinned Python convention. Replace the paths
below with explicit absolute paths; do not run the synthetic catalog against
real files until you replace its fictional species and aliases.

```text
py -3.12 -m bridge.sdk.largo_cli scan --root "X:/FinishedLargos" --catalog "X:/BridgeReports/canonical-catalog.json"
py -3.12 -m bridge.sdk.largo_cli validate --root "X:/FinishedLargos" --catalog "X:/BridgeReports/canonical-catalog.json"
py -3.12 -m bridge.sdk.largo_cli plan --root "X:/FinishedLargos" --catalog "X:/BridgeReports/canonical-catalog.json" --target SR1 --target SR2
py -3.12 -m bridge.sdk.largo_cli install --root "X:/FinishedLargos" --catalog "X:/BridgeReports/canonical-catalog.json" --target SR1 --lab "X:/IsolatedLab/SR1"
```

The last command is an **intentional refusal**, exit 6. It reports
`MODEL_LOADER_UNVERIFIED` and `REGISTRATION_ADAPTER_UNVERIFIED`. It does not inspect
the requested lab, enumerate models, create a deploy plan, write a ledger, copy
files, or claim success. There is no option to inject a synthetic target profile
or override these blockers.

All normal results/errors are deterministic JSON on stdout. `--help` uses the
usual human-readable argparse help. There are no writes by default. To request
one report file, add `--out "X:/BridgeReports/new-report.json"`. Its existing
parent must be outside the source root, the destination must not already exist,
and ancestors/overlap, symlinks, junctions, reparse points and hardlinks are
rejected. No output directories are created. No reports/manifests are written
beside model files. An install readiness report may not be written into/above
its requested lab either. Do not use shell redirection into the source root;
that bypasses the CLI's output-path checks.

Alternatively supply an **explicit** launcher settings document:

```text
py -3.12 -m bridge.sdk.largo_cli scan --settings "X:/BridgeReports/settings.json" --catalog "X:/BridgeReports/canonical-catalog.json"
```

`content.user_largos_root` is an optional unset (`null`) setting. `--root` takes
precedence. There is no home/Desktop/game-folder search, implicit settings file,
or fallback root. Settings are read and normalized in memory only; malformed
settings are rejected without SettingsStore's corrupt-file renaming behavior.

## Explicit identity catalog

`docs/examples/user-largo-catalog.example.json` is fictional and safe for
synthetic testing. The catalog schema is `bridge-user-largo-catalog` v1:

- `species`: unique **canonical** `slime.<slug>` IDs with explicit `aliases` lists.
  The provided full canonical ID is also usable as a filename token; its slug
  is **not** silently treated as an alias. No display-name-to-native-registry
  inference is performed. Aliases are case-insensitive alphanumeric token
  sequences separated by punctuation/space.
- `parent_capabilities` (optional): canonical parent to known capability-ID list.
  These are input to the existing `compose_capabilities`/`plan_import`, never
  inferred from geometry. Omitted rules cause `PARENT_RULES_UNSPECIFIED` in plans.
- `target_context` (optional): each SR1/SR2 row has explicit `native` and `parents`
  lists. `parents` is the reconciled set available there (native **and** enabled
  converted parents), not just a list of display names. Missing context stays
  unverified; package declarations cannot replace it. Native Largo collisions
  are refused by the existing planner.
- `evidence` (optional): caller's provenance annotation, never runtime proof.

Canonical IDs and declared values are preserved, not repaired. Catalog IDs must
be supplied from the approved canonical registry. Input catalog/context is
reported as `EXPLICIT_INPUT_NOT_RUNTIME_VERIFIED`; it cannot prove that a parent
is eligible for hybridization in the actual build.

Every bare filename is matched against **all** two-alias splits, order
independent. Exactly one pair of two distinct parents gives
`largo.<sorted-parent-slug>.<sorted-parent-slug>`. Results are:

| Match status | Meaning |
|---|---|
| `MATCHED` | One explicit distinct-parent candidate |
| `AMBIGUOUS` | More than one candidate; none chosen |
| `UNKNOWN` | Filename not fully explained by two explicit aliases |
| `INVALID` | Same parent twice or invalid manifested package |
| `DUPLICATE` | Multiple files/packages claim one Largo ID; all are blocked |

Version suffixes, `largo` suffixes, export labels and other extra words are not
stripped. Put the exact needed phrases in the aliases catalog instead. Existing
`largo-package.json` packages retain their canonical identity, versioning,
asset hashes and schema checks; models inside them are not independently
misclassified as bare packages. Duplicate detection spans both kinds.

## What geometry validation actually checks

No extra dependency is installed, no third-party importer is dynamically loaded,
and no model is executed. Readers are deliberately limited:

| Format | Scope |
|---|---|
| OBJ | UTF-8 triangle polygon meshes: finite `v` (including nonzero homogeneous `w`), `vt`, `vn`; positive/negative nonzero indices in range; nondegenerate triangles; finite local bounds; groups/smoothing labels; confined `mtllib`/`usemtl` references |
| MTL | Named materials, basic numeric/PBR scalar properties, single-token local texture references without map options; dependencies are confined, bounded, nonempty and hashed |
| STL | Standard ASCII facets or exact-length little-endian colourless binary triangles; finite normals/positions, nondegenerate triangles and local bounds |
| FBX, blend, glTF, GLB, legacy opaque `.bin` mesh assets | `NEEDS_FORMAT_ADAPTER`; hash/file boundary only, `geometry_checked: false`, no invented bounds |

OBJ n-gons, curves, vertex extensions, unsupported directives, MTL spectral/xyz
variants or texture options, and binary STL colour attributes also require a
format adapter. This tool does not triangulate/re-export/alter a finished model.
Malformed, empty, nonfinite, degenerate or out-of-range supported inputs are
rejected. Unsupported-format files may be malformed: **their geometry was not
checked**, which is why they remain pending rather than validated.

Texture references are validated and SHA-256 hashed, **not image-pixel decoded**.
Rig, skin weights, animation clips, collision behavior, actual Unity hierarchy,
material appearance and facial attachment behavior are not runtime checked.
`GEOMETRY_VALID` means the above static local geometry passed, not game readiness.
Renderer/material/face/clip declarations still pass the existing package
reference/hash checks. An MTL dependency may be manifested as
`kind: "material_library"`; it is hashed as an artifact, never treated as mesh.
Unmanifested external dependencies remain a planning blocker.

Limits: 32 MiB per model/dependency read, 4 MiB per CLI JSON document (the existing
package validator additionally limits its manifest to 2 MiB), 256 MiB aggregate
source bytes, 4096 source directory entries, depth 6, 500000 vertices per OBJ
attribute stream, 500000 triangles (STL additionally limits duplicated vertices
so at most 166666 triangles), 65536 characters per parsed text line, and 128
external-reference records. Inputs must be regular files. Absolute/traversing/
network references, hardlinks, reparse paths, alternate data streams, Win32
reserved names and unsafe spelling aliases are rejected. Keep source/output
directories **quiescent** while reading: this is an offline readiness reader,
not a handle-relative Windows sandbox against an attacker racing ancestor
renames. Reads compare opened file identity and size/mtime around the bounded
read; output creation is exclusive and paths are rechecked immediately before it.

## Metadata, without authoring or assumptions

For bare models the report contains a **proposal**, not a valid created package.
Unknown required values are `null`. `scan` does not hash bare meshes; `validate`
adds their observed hash to the proposal. The user must supply the package's
version, explicit material/renderers/face/animation declarations and ownership
information before a manifested import can be planned. Models are never
renamed, and no manifest is created in the source folder.

One optional v1 top-level extension, `import_metadata`, describes:

- `transform`: positive finite three-component `scale`, finite local `pivot`,
  and finite unit quaternion `orientation_xyzw`. Values describe intended
  import orientation/units, not inferred game scale. No transforms are applied.
- `collision`: `parent_derived`, `none`, or `mesh` with `asset` referencing a
  declared mesh. The declaration does not verify a Unity collider.
- `face_anchors`: explicit list (empty is an explicit declaration) of unique
  `name`, finite `position`, unit `orientation_xyzw`, optional `bone`.
- `animation_binding`: `static`, or `skeletal` with `root_bone` and unique `bones`.
  Static bindings conflicting with declared clips/skinned renderers are rejected.
  Bone names/anchors are checked as declarations, not against an opaque rig.
- `source_realms`: unique SR1/SR2 list, allowed empty for independently authored art.
  `intended_realms`: nonempty unique SR1/SR2 list.
- `target_parent_availability` (optional): per-realm subsets of the declared pair,
  reported as unverified user declarations; it never overrides `target_context`.

`docs/examples/user-largo-metadata.example.json` is an illustrative snippet,
not measured values for the user's models. `provenance.user_owned: true` is an
explicit ownership declaration; do not set it unless accurate. Legacy v1
packages without these additions still validate. Partial valid metadata remains
schema-valid with `IMPORT_METADATA_INCOMPLETE` diagnostics; invalid values are
rejected. CLI planning reports missing declarations instead of filling them in.
No diet, plort rules, special behavior or parent eligibility is derived from
mesh shape, filenames beyond matching, or appearance.

## Planning and proof boundary

`plan` invokes the existing `largo.plan_import` with built-in target profiles,
parent capability composition and target context; it does not introduce a
competing content/deployment architecture. Existing real target material
profiles have no verified templates, and registration queries Q007/Q008 remain
unresolved. The CLI annotates **every** non-refused plan as `PENDING_ADAPTER`,
`registrable: false`, with real model-loader and registration blockers, even if
static geometry and metadata are complete. Bare sources receive proposals only,
not invented valid packages or ledger rows.

The registration receipt distinguishes `PLANNED` from `NOT_EXECUTED`; both have
`runtime_proven: false`, `executed_operations: []`, no proof, and unverified
loader/registration flags. Neither indicates successful import. No runtime proof
can be produced by this offline slice.

Exit codes: **0** scan/limited validation passed; **2** usage/root configuration
missing; **3** pending adapter or metadata/planning work; **4** invalid inputs,
unknown/ambiguous/duplicate matches, refusal or empty discovery; **5** unsafe
report output; **6** install blocked by missing verified adapters. A validation
exit 0 may still need metadata and always still needs runtime adapters.

## Tests and remaining user-specific steps

```text
PYTHONPATH=tests PYTHONDONTWRITEBYTECODE=1 py -3.12 -m unittest content.test_user_largo content.test_user_largo_import_cli content.test_user_largo_import_geometry services.test_app_services -v
```

Tests create only synthetic OBJ/STL/metadata/catalog fixtures in this worktree's
ignored `Lab/GeneratedContent`, including real Windows junction and hardlink
checks. Source-tree SHA-256/mtime snapshots are compared around every CLI
command. Existing package/app service tests are included. The full suite's known
base-c787caf native-collector import failures are not repaired by this slice.

Next required inputs/work: the explicit read-only finished-model root, an
approved canonical species/aliases/capabilities catalog and reconciled per-target
parent/native context, and accurate metadata/ownership declarations. For opaque
formats, a bounded read-only format adapter must be implemented/verified without
changing the user's originals. Packaging can be explicitly staged **outside**
the original root after approval; this CLI performs no staging copies. Actual
installation remains blocked until matching-build loader, materials,
registration, hierarchy/animation/collider behavior and isolated-save lab proof
are independently implemented and executed. Copying raw models is not Largo
registration.
