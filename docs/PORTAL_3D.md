# SlimeBridge gateway: procedural 3D portal (`bridge/meshgen`)

Status: geometry, gameplay contract, collision/trigger checks, GLB/OBJ structure and determinism are **IMPLEMENTED / UNIT_TESTED** in the Cloud environment (stdlib only). How the asset looks or behaves in Blender, Unity or either game is **NEEDS_LOCAL_VISUAL_PROOF**: no DCC tool, engine or game was run for this work. The generated metadata, GLB extras and sidecar all carry `evidence: NEEDS_LOCAL_VISUAL_PROOF`.

## What it is

An original SlimeBridge structure: a round, chunky portal ring standing over a short walkable bridge. The player walks along the bridge (+Z <-> -Z), up a gentle ramp, across a planked deck, through the ring opening, and down the ramp on the other side. The trigger volume inside the opening is where a realm transition is *requested*; the gateway itself has no gameplay logic.

No Slime Rancher / Monomi Park geometry, textures, UI or proportions were used or consulted. Every shape is built in Python from parametric primitives.

### Style rationale

- **Chunky, readable silhouette.** The ring tube is a torus with a *rounded-rectangle* cross-section (0.62 m x 0.82 m by default, 0.16 m bevels). It reads at distance as one bold arch, and its flat faces carry trims cleanly.
- **Soft, rounded everything.** Rounded boxes with quarter-round edges and spherical corners, capsule rails, knobbed posts, filleted ramp crests. There are no sharp industrial edges.
- **Soft sci-fi, not heavy machinery.** A keystone housing with a glowing energy core, two side "emitter pods" with light bars, glowing rim trims on both ring faces, threshold light bars and guide lines on the deck.
- **Colourful ranch palette.** The palette is cream/teal/peach/honey-wood with a mint energy surface (`meadow`), plus `twilight` and `coral` variants. The colours are placeholder look-dev values chosen for this work.
- **Two-sided.** The structure is mirror-symmetric front/back (unit-tested bounds symmetry), with trims, core bezels, pod lights, signs and spawn points on both faces.

### Anatomy (part name -> semantic material)

| Module | Parts | Material |
|---|---|---|
| ring | `ring.frame` (arc from pedestal to pedestal) | `frame_primary` |
| ring | `ring.seam` (outer two-tone band), `ring.keystone`, `ring.pods` | `frame_secondary` |
| ring | `ring.collars` (four clamp bands) | `accent` |
| ring | `ring.trim` (inner rim glow, both faces), `ring.pod_lights` | `emissive_trim` |
| core | `core.energy` (capsule through the keystone, visible on both faces) | `portal_energy` |
| core | `core.bezels` | `accent` |
| portal | `portal.membrane` (disc cut at deck level, double-sided material) | `portal_energy` |
| deck | `deck.body` (swept slab with ramps), `deck.curbs` | `frame_secondary` |
| deck | `deck.surface` (planks; one thin layer at LOD2) | `bridge_surface` |
| deck | `deck.lights` (threshold bars and guide lines) | `emissive_trim` |
| rails | `rails.posts` / `rails.handrails` / `rails.knobs` | `frame_secondary` / `frame_primary` / `accent` |
| base | `base.pedestals` (two-tier), `base.foundation` / `base.caps` | `frame_secondary` / `accent` |

Only these six semantic materials exist: `frame_primary`, `frame_secondary`, `portal_energy`, `accent`, `bridge_surface`, `emissive_trim`. A unit test enforces this.

## Conventions

- glTF 2.0 frame. Units are metres, **+Y up**, **+Z forward** (the "front" face), right-handed, so **+X is the asset's own left**.
- A player approaching the front (standing at +Z, looking toward -Z) has **-X on their left**. `socket.sign_left` is therefore at -X. The back-side signs (`socket.sign_left_back` / `socket.sign_right_back`) use the same viewer-relative naming.
- Pivot/origin: deck centre on the ground plane (y = 0). The model stands on y = 0 (min y = 0, unit-tested).
- Faces are counter-clockwise from the front, and normals are unit and point outward (unit-tested per LOD).
- UVs are world-scale (1 UV unit = 1 m), so tiling textures keep a constant texel density. The exception is the portal disc, which is planar 0..1 so a shader can derive radius and angle.
- Floats are rounded to 6 decimals. Ordering is stable, so the same parameters give byte-identical GLB/OBJ/sidecar outputs (unit-tested).

## Parameters (`PortalParams`)

Each parameter is validated for type, finiteness and range. Invalid input returns unified diagnostics (`bridge.core.diagnostics`, subsystem `meshgen`) and a model with `ok == False` and no geometry. It never raises and never produces NaN.

| Parameter | Default | Range | Notes |
|---|---|---|---|
| `variant` / `palette` | `meadow` | name / `meadow`,`twilight`,`coral` | |
| `ring_inner_radius` | 2.05 | 1.2–4.5 | tube inner radius |
| `ring_tube_width` / `ring_tube_depth` | 0.62 / 0.82 | 0.15–1.2 / 0.2–1.6 | radial / along Z |
| `bevel_radius` | 0.16 | 0.01–0.4 | must be < half of tube width/depth |
| `ring_center_lift` | 1.15 | 0–3.5 | ring centre height above the deck top |
| `ring_segments` / `bevel_segments` | 96 / 4 | 12–256 / 1–8 | LOD0 tessellation |
| `deck_length` / `deck_width` / `deck_thickness` / `deck_height` | 3.4 / 3.0 / 0.3 / 0.45 | see code | `deck_height` = walking surface height |
| `ramp_slope_deg` | 14 | 3–45 | contract max below |
| `curb_width` / `curb_height` | 0.16 / 0.10 | | curbs bound the walkable width |
| `rail_height` / `rail_post_spacing` | 0.95 / 1.1 | | |
| `pedestal_width` / `pedestal_height` / `pedestal_depth` | 1.15 / 1.0 / 1.4 | | ring legs end inside them |
| `core_radius` | 0.2 | 0.05–0.6 | must fit the keystone |
| `lod_count` | 3 | 1–3 | |
| `min_clear_width` / `min_clear_height` | 2.2 / 2.6 | | gameplay contract |
| `min_walkable_width` / `max_ramp_slope_deg` | 2.0 / 20 | | gameplay contract |

### Gameplay contract (checked before any geometry is built)

- The opening is measured with an *effective* radius that includes the collars reaching 2.5 cm inside the tube. **Clear height** at the centre must be at least `min_clear_height`. **Clear width**, taken as the narrowest chord over the full `min_clear_height` above the deck, must be at least `min_clear_width`. Defaults: 2.83 m x 3.18 m.
- Walkable width between the curbs must be at least `min_walkable_width` (default 2.68 m). The ramp slope must not exceed `max_ramp_slope_deg`.
- The ring legs must not cut the deck. Pedestals must contain the leg ends without touching the deck. The bridge must be long enough for spawn points outside the trigger.
- After generation, these are checked as well:
  - every LOD passes mesh validation;
  - only semantic materials are used;
  - no collision box intersects the trigger/passage (separating-axis test);
  - the collision AABB encloses the LOD0 visual AABB within 0.06 m;
  - every sampled walkable deck/ramp point has collision underneath;
  - the trigger lies inside the opening;
  - spawn sockets are at least 0.6 m outside the trigger.

Error codes include `PARAM_OUT_OF_RANGE`, `PARAM_NOT_FINITE`, `PARAM_TYPE`, `PARAM_UNKNOWN`, `OPENING_TOO_NARROW`, `OPENING_TOO_LOW`, `RAMP_TOO_STEEP`, `WALKABLE_TOO_NARROW`, `RING_INTRUDES_DECK`, `PEDESTAL_TOO_NARROW`, `COLLISION_BLOCKS_OPENING`, `DECK_NOT_SUPPORTED`, `MESH_*` and `GLB_*`.

### Variants

`meadow` uses the defaults. `twilight` has a taller, slimmer ring, a narrower bridge, higher pedestals and an indigo/lavender/gold palette. `coral` has an extra-chunky ring, a wider and longer bridge, broad pedestals and a sand/coral/turquoise palette. Variants are only parameter overrides (`VARIANTS` in `portal.py`).

## Collision, trigger, sockets, LODs

- **Collision proxy:** about 40 oriented boxes. They cover the deck, two sloped ramps, curbs, rail walls, two pedestals and plinths, the foundation, the keystone, the side pods, and 14 tangent boxes around the ring arc. Nothing crosses the opening, so players walk through. Boxes are `{name, shape: 'box', center, half_extents, rotation (xyzw)}`.
- **Trigger:** `portal_trigger` is an oriented box filling the walkable passage inside the ring. It is the walkable width wide, as tall as fits 5 cm inside the opening, and as deep as the ring. `front_normal` is +Z and `back_normal` is -Z. The crossing direction is the side the player entered from.
- **Sockets:** `socket.energy_core`, `socket.vfx_center` (ring centre), `socket.spawn_front` / `socket.spawn_back` (on the walking surface, facing away from the portal), `socket.sign_left` / `socket.sign_right` (front pedestal faces) and `socket.sign_left_back` / `socket.sign_right_back`. Each socket has `position`, `forward` (+Z of the socket), `up` and `rotation` (xyzw).
- **LODs:** LOD0 is about 22k triangles. LOD1 (about 11k) halves the segment counts. LOD2 (about 4k) quarters them and drops small details: knobs, bezels, pod lights and deck lights, with planks replaced by one surface layer. Each LOD is validated independently.

## Outputs and formats

`python3.12 -m bridge.meshgen.cli --variant meadow --out Lab/GeneratedContent/portal3d [--preview] [--split-lods] [--params overrides.json]`

- `<name>.glb`: binary glTF 2.0 written with `struct`/`json`.
  - One buffer, with every bufferView 4-byte aligned.
  - One mesh per part per LOD (`POSITION` with float32-exact min/max, `NORMAL`, `TEXCOORD_0`, uint16/uint32 indices).
  - One PBR material per used semantic id: linear base colour from the palette, metallic/roughness, `emissiveFactor`, and `BLEND` + `doubleSided` for `portal_energy`.
  - Node tree: `SlimeBridgeGateway_<variant>` -> `gateway_LOD<n>` -> `<part>_LOD<n>`; `collision` -> `COL_<box>`; `TRIGGER_portal`; `sockets` -> `socket.*`. Collision/trigger nodes are empties whose **scale is the half extents** (a [-1, 1] unit box fits them), and the exact box is in `extras`.
  - Root and asset `extras` hold parameters, the params hash, conventions, opening/bridge measurements and evidence.
- `<name>.obj` + `<name>.mtl`: one LOD (default 0), one `o` per part, `usemtl <semantic id>`, 1-based shared v/vt/vn indices, and V flipped to OBJ's bottom-left origin.
- `<name>.portal.json`: the sidecar, holding parameters, params hash, conventions, metadata (bounds, opening, bridge, contract, triangle counts per LOD), collision boxes, trigger, sockets, palette (sRGB and linear, with adapter notes), SHA-256 and size of every other output file, diagnostics, evidence, and what was and wasn't tested. Its `content_sha256` is over everything else.
- `previews/<name>_{front,back,side,three_quarter}.png`: optional pure-Python software renders. These are for human inspection only (see below).

Output safety: `--out` must be a relative POSIX path under `Lab/GeneratedContent/` (git-ignored). Absolute paths, `..`/`.` segments, backslashes and symlinked components (including symlinked ancestors) are refused. Writes use `bridge.cache.fs.atomic_write`, which is exclusive-temp, fsync'd, link-refusing and confined to the root. Generated outputs are never committed.

## Semantic materials -> game materials

The generator never names target-game shaders. A later realm adapter (SR1 Mono/SRML, SR2 IL2CPP/MelonLoader), running locally against the exact game build, maps each id to a material it discovers in that game at runtime. It must never copy proprietary assets into this repository.

| Semantic id | Intended look | Adapter guidance |
|---|---|---|
| `frame_primary` | soft painted shell | the game's opaque lit/toon structure material, tinted with `base_color_linear` |
| `frame_secondary` | darker structural body | same family as `frame_primary`, second tint |
| `accent` | playful highlight | same family, accent tint |
| `bridge_surface` | wooden walking planks | a walkable wood-like material; also the place to attach footstep/surface tags |
| `emissive_trim` | glowing strips | an unlit/emissive material (bloom if the game has it) |
| `portal_energy` | translucent animated energy | the transition VFX material: double-sided, translucent, may animate from the disc's 0..1 UVs (radius = distance from 0.5,0.5) |

If an adapter cannot resolve an id, it should fall back to a plain lit material with the sidecar's colour. It must report a diagnostic rather than guess.

## Tested here vs. NEEDS_LOCAL_VISUAL_PROOF

Tested in this environment (`python3.12 -W error::SyntaxWarning -m unittest discover -s tests -p 'test_meshgen*.py'`):

- parameter validation, including NaN, infinities, huge, negative and zero values, wrong types and unknown keys;
- every contract rule and every LOD's mesh validation (finite values, index ranges, unit normals, non-degenerate triangles, consistent CCW winding, reasonable bounds);
- the six-material rule, symmetry and the pivot;
- that no visual geometry sits inside a player-sized box in the opening, independently of the layout math;
- walkable width measured from the curb vertices, and ramp slope measured from the deck's upward faces and the ramp collision boxes;
- collision/trigger/socket rules;
- deterministic GLB bytes and sidecar hash;
- the GLB self-check (`validate_glb`), plus rejection of truncated, re-magicked, garbage-JSON, out-of-range-index, NaN, min/max-mismatch, out-of-bounds and broken-hierarchy GLBs;
- OBJ/MTL references and 1-based index ranges;
- output-path safety (`../`, absolute, foreign prefix, symlinked directory, symlinked ancestor, link planted after resolution);
- the CLI end to end;
- that the Blender script parses and its parameter keys equal `PortalParams`;
- the PNG writer, with a small deterministic render.

`validate_glb` is a structural self-check written here. It is **not** the Khronos glTF validator and it is **not** an import into any tool.

NEEDS_LOCAL_VISUAL_PROOF (not done):

- opening the GLB/OBJ in Blender;
- running `portal_blender.py`;
- FBX export;
- import into the Unity versions of SR1 (2019.4.29f1) or SR2 (2021.3.28f1);
- material mapping in either game;
- collision feel, trigger behaviour and appearance in-game.

The PNG previews come from a tiny software rasteriser written for this task, with Lambert shading, a ground shadow and an outline. They show the silhouette and colours, not what any engine renders.

## How the local provider should load it

All steps below are local and unverified here. Record results as evidence before claiming them.

1. **Generate.** Run `py -3.12 -m bridge.meshgen.cli --variant meadow --out Lab/GeneratedContent/portal3d --preview` and check that the JSON line reports `"status": "OK"` and `"glb_self_check": "PASSED"`. Optionally run the Khronos glTF validator on the `.glb`. Its licence and tooling are separate, so treat it as an external reference.
2. **Blender** (3.6+ / 4.x):

       blender --background --python bridge/meshgen/blender/portal_blender.py -- --glb Lab/GeneratedContent/portal3d/slimebridge_gateway_meadow.glb --fbx Lab/GeneratedContent/portal3d/slimebridge_gateway_meadow.fbx --blend Lab/GeneratedContent/portal3d/slimebridge_gateway_meadow.blend

   Or use `--params my.json --repo .` to regenerate inside Blender with the same pure-Python generator. The script does the following:
   - imports the GLB (Blender converts +Y up to +Z up; the glTF front +Z becomes Blender -Y, which is the Front view);
   - files LODs into `SlimeBridge_Visual_LOD<n>` (LOD1/2 hidden);
   - turns collision/trigger empties into wire boxes in render-excluded collections;
   - keeps sockets as arrow empties;
   - tags materials and stores parameters, hash and evidence as scene custom properties;
   - optionally exports FBX (`axis_forward='-Z'`, `axis_up='Y'`, `FBX_SCALE_ALL`).

   Check visually: the deck is on the ground, the front faces Blender's Front view, the six materials are present, and the collision boxes sit around the deck/ring with none in the opening.
3. **Unity**, using the game's exact editor version. Import either the FBX from step 2, or the GLB through a glTF importer package whose licence and Unity-version support have been checked first (third-party code is reference-only until cleared, per `AGENTS.md`). Most glTF importers convert handedness by mirroring X. The asset is symmetric, but socket names are viewer-relative, so verify that `socket.sign_left` ends up on the viewer's left when facing the front. Then:
   - build a `LODGroup` from `gateway_LOD0..2`;
   - for each sidecar collision box, add a `BoxCollider` (centre = box centre, size = 2 x half extents, on a child with the box rotation);
   - add the trigger as a `BoxCollider` with `isTrigger = true`, and on enter/exit compare the player's motion with `front_normal` to decide the crossing direction;
   - map materials per the table above;
   - build an AssetBundle for the realm adapter.

   Do not modify original game directories. Work only in `Lab/` copies, per `AGENTS.md`.
4. **In game:** walk through from both sides. Check that ramps and deck are walkable, nothing blocks the opening, and the trigger fires once per crossing. Capture screenshots and logs as evidence before changing any status from NEEDS_LOCAL_VISUAL_PROOF.
