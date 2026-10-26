# SlimeBridge Asset Pipeline — Native Model Portability

Goal: imported base-slime species render with their **actual source-game geometry, materials,
textures and (ultimately) animations**, obtained automatically from the player's own installed
games. No manual exports from players; no proprietary payloads in the repository or distribution.

## Strategy (primary path)

**Offline extraction from the installed game files** is the primary path on both sides:

- SR1 ships a Unity **Mono** build: assets are readable directly from
  `SlimeRancher_Data` (serialized files + bundles).
- SR2 ships a Unity **IL2CPP** build: native asset types (`Mesh`, `Texture2D`, `Material`,
  `AnimationClip`, `GameObject`/`Transform`/`SkinnedMeshRenderer`) parse with the fixed Unity
  layouts; only `MonoBehaviour` ScriptableObjects require il2cpp type metadata, so the pipeline
  sources gameplay values (appearance colours, diets) from the existing Bridge registry captures.

The in-game **runtime capture remains a targeted fallback** for data that only exists at runtime
(dynamically assembled visuals, procedural parameters).

## Current implementation (working)

`bridge/content/sr2_asset_extract.py` (UnityPy, offline, no game launch):

1. Opens `Lab/Games/SR2/SlimeRancher2_Data/resources.assets` (game-local; any user installation
   may be pointed at with `--sr2-dir` by the Launcher).
2. Resolves the species' real parts from the asset library:
   - shared body `slime_default_v4_LOD0` (or a species-specific body when one exists),
   - face `slime_face_LOD0`,
   - species parts `LF_<species>_*_LOD0` (e.g. `LF_ringtail_ears_LOD0`, `LF_ringtail_tail_LOD0`);
     this is how the Ringtail tail/ears the old placeholder was missing are recovered.
3. Decodes full geometry: vertices, normals, tangents, uv/uv2, colours, triangle lists, bone
   weights (bind poses/rig data are carried for the upcoming skeleton stage).
4. Resolves the real materials (`slime<Species>Base` body palette + the default face material
   via `_FaceAtlas`, e.g. `eyeHappy`) with their authored colour properties
   (`_TopColor`/`_MiddleColor`/`_BottomColor`) and display textures (`_StripeTexture`,
   `_ColorMask`, `_FaceAtlas`) decoded to PNG.
5. Writes the portable converted-model cache under `Lab/AssetCache/sr2-to-sr1/<canonical>/`
   (`model.json` + `tex-*.png` + `dump-receipt.json` + `cache-manifest.json` with per-file
   SHA-256), matching the schema the runtime loader consumes. The cache tree is private and
   ignored by git (`/Lab/*`).

### Runtime consumption (SR1)

`mods/sr1-bridge/ImportedModelLoader.cs` validates the cache manifest and every file hash, then
rebuilds the model as destination-runtime Unity objects (hierarchical mesh objects, decoded
textures, material tints). `mods/sr1-bridge/ImportedSpawner.cs` disables the placeholder's
renderers entirely and attaches the rebuilt model to the spawned actor, so the tabby placeholder
and its doubled geometry no longer appear.

### Fallback (SR2 in-game capture)

`mods/sr2-bridge/Sr2MeshDump.cs` + `tools/lab/sr2_mesh_dump.py` run an SR2 LAB session that
instantiates the species prefab so the game assembles its runtime appearance, then dumps the
same portable schema (validated, hashed, published to the same cache contract). Reserved for
runtime-only data and for cross-checking the offline extraction.

## Roadmap (per the full-portability directive)

1. **Rig + skeleton**: bind poses, bone hierarchy, root bone and skinning data are already
   captured (offline: `m_Skin`/`m_BindPose`; runtime: `bone_paths`/`bind_poses`/`bone_weights`).
   Next: rebuild the skeleton in the destination game and bind the SkinnedMeshRenderer to it so
   the model deforms instead of standing in bind pose.
2. **Animations**: SR2 clips are present in `resources.assets` (`Idle v4`, `Bite v4`,
   `Pounce v4`, `FlapWings_*`, …). Extract `AnimationClip` curves (transform paths + keyframes)
   offline, then retarget them onto the rebuilt skeleton via the bone-path map, or reconstruct
   equivalent procedural motion (bounce/scale) through a destination-side animation adapter where
   direct curve transfer is not meaningful.
3. **Species coverage**: generalize the extractor to the full base roster in both directions
   (`--all-base`), driven by the same canonical-id roster the registration specs use. SR1 side:
   extract from the SR1 export-format files (`slime_default` body, per-species textures,
   `AnimationClip` set) with the same portable cache schema (`sr1-to-sr2`).
   `<AssetRipper export>` is a full SR1 reference export kept
   for cross-checking.
4. **Launcher integration**: the Slime Launcher runs the extraction/conversion step for the
   selected roster on first use and when the detected game build hash changes; caches are keyed
   by canonical id + converter version + source hash and re-validated before each session.
5. **Verification**: front/left/right/rear/vacpack views of the imported species in both games,
   compared against the source species; animation checks (idle, movement, eating) once the rig
   stage lands.

## Constraints

- Local proprietary assets are read from the user's installation at runtime/extraction time and
  cached **locally only**; they are never committed, bundled or redistributed. Only extraction
  and conversion code ships.
- No DRM circumvention; the pipeline reads plain game data from a legitimate installation.
- The current static-bind-pose attachment is an explicitly intermediate stage: the completed
  feature requires skinned, animated portability (see roadmap items 1–2).

## Runtime SR1 import milestone — Batty and Twin (2026-10-09)

The SR1-side loader consumed real converted source caches for two additional species end to end
(protected dev-session, current-user saves with backup/restore):

- `Lab/AssetCache/sr2-to-sr1/slime.batty` (13 manifest-verified files) imported in SR1:
  `spawn:slime.batty` reported `model_applied=true`, 5 renderers / 780 vertices (body, face,
  ears, two rigid wings), palette readback 16/16, 0 post-marker NREs; runs
  `batty-sr1-run-16ba242c7dd3` (pre-final bytes) and `batty-sr1-run-41bc0a95eaf3` (committed
  bytes `ec61502`) both `passed=true` with plugin and saves restored (`rollback_verified`).
- `Lab/AssetCache/sr2-to-sr1/slime.twin` (10 manifest-verified files) imported in SR1:
  `spawn:slime.twin` reported `model_applied=true`, 4 renderers / 839 vertices, palette
  readback 15/15, 0 post-marker NREs; run `twin-sr1-run-5f7f26e0333c` `passed=true` on the
  same committed bytes, plugin and saves restored.
- Two cache/loader prerequisites surfaced and were fixed, each independently reviewed:
  the ringtail cache revision must carry its mandatory icon files (`icon-descriptor.json` +
  `iconSlimeRingtail.source.png`; a pre-icon cache makes registration fail closed and the
  SRMP save fail to populate), and the SR1 source-face reader must accept the glow eyes family
  declared by Batty (`SR/AMP/Slime/Face/Eyes Glow`) including its optional `_GlowNoise` map
  (atlas still required exactly once; merged `ec61502`, review round 3 APPROVE).
- Not yet proven: pixel-level sightings of the imported Batty/Twin in captures (night scene +
  HUD occlusion; the receipts remain the evidence), native animation fidelity, and the
  remaining roster species (Sloomber; Flutter/Shadow/Yolky stay blocked upstream).
