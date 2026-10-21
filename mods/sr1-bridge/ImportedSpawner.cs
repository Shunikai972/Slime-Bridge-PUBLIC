using System;
using System.Collections.Generic;
using System.Globalization;
using MonomiPark.SlimeRancher.Regions;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Shared native actor spawn for imported species, used by the LAB ops edge and the in-game
    /// dev console command. Spawns through SRBehaviour.InstantiateActor / GameModel so the game's
    /// actor-model pipeline runs (SlimeEmotions/SlimeFaceAnimator participants get their models
    /// initialized); a raw Object.Instantiate leaves them uninitialized and produces runtime
    /// NullReferenceExceptions. LAB/dev-only surface; natural spawning stays NATIVE_ONLY.
    /// </summary>
    internal static class ImportedSpawner
    {
        private static readonly int TopColorId = Shader.PropertyToID("_TopColor");
        private static readonly int MiddleColorId = Shader.PropertyToID("_MiddleColor");
        private static readonly int BottomColorId = Shader.PropertyToID("_BottomColor");

        /// <summary>Bridge-owned registry of import roots spawned in this process (bounded sweep).</summary>
        private static readonly List<GameObject> ImportRoots = new List<GameObject>();

        /// <summary>Register an import root under a fixed registry bound: dead entries are pruned,
        /// and the oldest live entries are dropped beyond the cap so repeated spawns cannot make
        /// the registry grow without limit.</summary>
        private static void RegisterImportRoot(GameObject instance)
        {
            const int maxRegistry = 256;
            const int targetRegistry = 128;
            ImportRoots.Add(instance);
            if (ImportRoots.Count <= maxRegistry) return;
            for (var i = ImportRoots.Count - 1; i >= 0; i--)
            {
                if (ImportRoots[i] == null) ImportRoots.RemoveAt(i);
            }
            while (ImportRoots.Count > targetRegistry) ImportRoots.RemoveAt(0);
        }

        /// <summary>Snapshot of live tracked import roots (diagnostics prioritizes these;
        /// bounded copy: most recent 64 live roots).</summary>
        internal static List<GameObject> TrackedImportRoots
        {
            get
            {
                const int maxTracked = 64;
                var result = new List<GameObject>(Math.Min(ImportRoots.Count, maxTracked));
                for (var i = ImportRoots.Count - 1; i >= 0 && result.Count < maxTracked; i--)
                {
                    var root = ImportRoots[i];
                    if (root != null) result.Add(root);
                }
                return result;
            }
        }

        /// <summary>Dev convenience: spawn an imported species' plort (its own look) in front of the player.</summary>
        internal static GameObject? SpawnPlort(GameContext game, SceneContext scene, SpeciesRegistrar registrar,
            string plortCanonical, List<string> problems)
        {
            if (!registrar.TryResolvePlortId(plortCanonical, out var nativeId)) { problems.Add("plort not registered in this session"); return null; }
            var prefab = game.LookupDirector.GetPrefab((Identifiable.Id)nativeId);
            if (prefab == null) { problems.Add("no registered plort prefab"); return null; }
            var player = scene.Player.transform;
            var instance = UnityEngine.Object.Instantiate(prefab, player.position + player.forward * 2.0f + Vector3.up * 1.0f, Quaternion.identity);
            instance.SetActive(true);
            var regionSetId = RegionRegistry.RegionSetId.HOME;
            try
            {
                var registry = UnityEngine.Object.FindObjectOfType<RegionRegistry>();
                if (registry != null) regionSetId = registry.GetCurrentRegionSetId();
            }
            catch (Exception) { /* HOME fallback */ }
            scene.GameModel.RegisterStartingActor(instance, regionSetId);
            return instance;
        }

        /// <summary>Dev convenience: spawn any registered actor (native largos, slimes, plorts) in front of the player by identifiable id.</summary>
        internal static GameObject? SpawnNative(GameContext game, SceneContext scene, Identifiable.Id id, int index, List<string> problems)
        {
            var prefab = game.LookupDirector.GetPrefab(id);
            if (prefab == null) { problems.Add("no prefab registered for " + id); return null; }
            var player = scene.Player.transform;
            var spread = Quaternion.AngleAxis((index - 1) * 25f, Vector3.up) * player.forward;
            var instance = UnityEngine.Object.Instantiate(prefab, player.position + spread * 3f + Vector3.up * 1.2f, Quaternion.identity);
            instance.SetActive(true);
            var regionSetId = RegionRegistry.RegionSetId.HOME;
            try
            {
                var registry = UnityEngine.Object.FindObjectOfType<RegionRegistry>();
                if (registry != null) regionSetId = registry.GetCurrentRegionSetId();
            }
            catch (Exception) { /* HOME fallback */ }
            scene.GameModel.RegisterStartingActor(instance, regionSetId);
            return instance;
        }

        internal static GameObject? Spawn(GameContext game, SceneContext scene, SpeciesRegistrar registrar,
            string canonical, List<string> details, List<string> problems)
        {
            if (!registrar.TryResolveNativeId(canonical, out var nativeId))
            {
                problems.Add("species not registered in this session");
                return null;
            }
            var id = (Identifiable.Id)nativeId;
            var prefab = game.LookupDirector.GetPrefab(id);
            if (prefab == null) { problems.Add("no registered prefab"); return null; }
            var player = scene.Player.transform;
            var position = player.position + player.forward * 2.5f + Vector3.up * 1.2f;
            var regionSetId = RegionRegistry.RegionSetId.HOME;
            try
            {
                var registry = UnityEngine.Object.FindObjectOfType<RegionRegistry>();
                if (registry != null) regionSetId = registry.GetCurrentRegionSetId();
            }
            catch (Exception) { /* Region set stays at the HOME fallback. */ }
            GameObject? instance = null;
            try
            {
                // Native order matters: registered templates keep prefab-asset semantics (activeSelf
                // true under an inactive container), so this copy comes out ACTIVE and Unity runs
                // Awake (cached references such as Identifiable.member) at instantiation, exactly like
                // the game's own spawners. The actor model is then registered through the save-loader
                // entry point (GameModel.RegisterStartingActor).
                instance = UnityEngine.Object.Instantiate(prefab, position, Quaternion.identity);
                StripCopiedRuntimeHelpers(instance);
                instance.SetActive(true);
                scene.GameModel.RegisterStartingActor(instance, regionSetId);
                if (instance == null)
                {
                    problems.Add("native actor instantiation returned null");
                    return null;
                }
                // Placeholder tinting: apply the source realm's canonical palette so the placeholder
                // does not visually read as the template species. Mirrors the game's own
                // RecolorSlimeMaterial.SetColors (MaterialPropertyBlock on _TopColor/_MiddleColor/
                // _BottomColor); per-instance only, no shared asset or prefab is modified. The
                // receipt reports the verified read-back: only renderers whose material supports the
                // palette properties and whose block reads back the exact colors count as verified.
                // Scope: placeholder renderers only. The appearance guard attaches the imported
                // visual DURING Object.Instantiate (postfix on the applicator Awake), so the
                // converted subtree already exists here; tinting it would override the validated
                // native-material colors. Renderers inside the marked visual subtree are skipped
                // and reported as palette_visual_skipped instead.
                if (registrar.TryGetPalette(canonical, out var paletteTop, out var paletteMiddle, out var paletteBottom))
                {
                    try
                    {
                        var top = ToColor(paletteTop);
                        var middle = ToColor(paletteMiddle);
                        var bottom = ToColor(paletteBottom);
                        var visited = 0;
                        var supported = 0;
                        var verified = 0;
                        var visualSkipped = 0;
                        var visual = FindImportedVisual(instance);
                        foreach (var renderer in instance.GetComponentsInChildren<Renderer>(true))
                        {
                            if (renderer == null) continue;
                            if (IsInsideVisualSubtree(renderer, instance, visual))
                            {
                                visualSkipped++;
                                continue;
                            }
                            visited++;
                            var canPaint = false;
                            var shared = renderer.sharedMaterials;
                            if (shared != null)
                            {
                                foreach (var material in shared)
                                {
                                    if (material != null && material.HasProperty(TopColorId) &&
                                        material.HasProperty(MiddleColorId) && material.HasProperty(BottomColorId))
                                    { canPaint = true; break; }
                                }
                            }
                            if (!canPaint) continue;
                            supported++;
                            var block = new MaterialPropertyBlock();
                            renderer.GetPropertyBlock(block);
                            block.SetColor(TopColorId, top);
                            block.SetColor(MiddleColorId, middle);
                            block.SetColor(BottomColorId, bottom);
                            renderer.SetPropertyBlock(block);
                            var readback = new MaterialPropertyBlock();
                            renderer.GetPropertyBlock(readback);
                            if (ColorsMatch(readback.GetColor(TopColorId), top) &&
                                ColorsMatch(readback.GetColor(MiddleColorId), middle) &&
                                ColorsMatch(readback.GetColor(BottomColorId), bottom)) verified++;
                        }
                        var applied = supported > 0 && verified == supported;
                        details.Add("\"palette_applied\": " + (applied ? "true" : "false"));
                        details.Add("\"palette_renderers\": " + visited.ToString(CultureInfo.InvariantCulture));
                        details.Add("\"palette_supported\": " + supported.ToString(CultureInfo.InvariantCulture));
                        details.Add("\"palette_verified\": " + verified.ToString(CultureInfo.InvariantCulture));
                        details.Add("\"palette_visual_skipped\": " + visualSkipped.ToString(CultureInfo.InvariantCulture));
                    }
                    catch (Exception ex)
                    {
                        details.Add("\"palette_applied\": " + RegistrySnapshotFormat.Quote("failed:" + ex.GetType().Name));
                    }
                }
                else details.Add("\"palette_applied\": false");
                // Converted native model (asset bridge): when a converted source-game model exists
                // in the private local cache, disable the Tabby/placeholder visuals entirely and
                // attach the rebuilt source geometry so the actor shows the actual imported species.
                if (ImportedModelLoader.TryGet(canonical, out var importedModel, out var modelProblem))
                {
                    List<Renderer> suppressed = null;
                    GameObject visual = null;
                    try
                    {
                        if (!ApplyImportedVisualToRoot(instance, canonical, importedModel, out suppressed))
                            throw new InvalidOperationException("imported visual apply returned false");
                        details.Add("\"model_applied\": true");
                        details.Add("\"model_source\": " + RegistrySnapshotFormat.Quote("sr2-to-sr1-cache"));
                        details.Add("\"model_renderers\": " + importedModel.RendererCount.ToString(CultureInfo.InvariantCulture));
                        details.Add("\"model_vertices\": " + importedModel.VertexTotal.ToString(CultureInfo.InvariantCulture));
                    }
                    catch (Exception ex)
                    {
                        // Fail closed to the placeholder: never leave the actor invisible or half-built.
                        visual = FindImportedVisual(instance);
                        if (visual != null) UnityEngine.Object.Destroy(visual);
                        if (suppressed != null)
                        {
                            foreach (var renderer in suppressed)
                            {
                                if (renderer != null) renderer.enabled = true;
                            }
                        }
                        details.Add("\"model_applied\": " + RegistrySnapshotFormat.Quote("failed:" + ex.GetType().Name));
                    }
                }
                else
                {
                    details.Add("\"model_applied\": false");
                    details.Add("\"model_note\": " + RegistrySnapshotFormat.Quote(modelProblem));
                }
                // Ringtail-family solar petrification: attached (idempotently) by
                // ApplyImportedVisualToRoot only when the private cache declares a stone form;
                // every other imported species reports false and stays untouched.
                details.Add("\"petrification_attached\": " + (instance.GetComponent<ImportedPetrification>() != null ? "true" : "false"));
                var identifiable = instance.GetComponentInChildren<Identifiable>(true);
                details.Add("\"instance_name\": " + RegistrySnapshotFormat.Quote(instance.name));
                details.Add("\"instance_id\": " + ((int)(identifiable != null ? identifiable.id : Identifiable.Id.NONE)).ToString(CultureInfo.InvariantCulture));
                details.Add("\"instance_active\": " + (instance.activeInHierarchy ? "true" : "false"));
                details.Add("\"region_set\": " + RegistrySnapshotFormat.Quote(regionSetId.ToString()));
                details.Add("\"position\": [" + position.x.ToString("F2", CultureInfo.InvariantCulture) + ", " +
                    position.y.ToString("F2", CultureInfo.InvariantCulture) + ", " + position.z.ToString("F2", CultureInfo.InvariantCulture) + "]");
                if (identifiable == null || identifiable.id != id)
                {
                    UnityEngine.Object.Destroy(instance);
                    problems.Add("spawned instance does not carry the imported identity; instance destroyed");
                    return null;
                }
                return instance;
            }
            catch (Exception ex)
            {
                if (instance != null) UnityEngine.Object.Destroy(instance);
                UnityEngine.Debug.LogError("[SlimeBridge] imported spawn threw: " + ex);
                var detail = ex.ToString();
                if (detail.Length > 1200) detail = detail.Substring(0, 1200);
                problems.Add("spawn threw: " + detail);
                return null;
            }
        }

        /// <summary>Structural health check for a spawned imported instance (no state mutation).</summary>
        internal static bool InstanceAlive(GameObject instance)
        {
            try
            {
                if (instance == null) return false;
                return instance.activeInHierarchy;
            }
            catch (Exception) { return false; }
        }

        private static Color ToColor(float[] rgba) =>
            new Color(rgba[0], rgba[1], rgba[2], rgba.Length > 3 ? rgba[3] : 1f);

        private static void SetLayerRecursively(GameObject root, int layer)
        {
            root.layer = layer;
            // Bounded iterative traversal: never materialize the full transform array
            // (GetComponentsInChildren would allocate an uncapped array; the spawn path must
            // stay within an explicit node budget regardless of the cached hierarchy size).
            const int nodeBudget = 4096;
            var visited = 0;
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            while (stack.Count > 0)
            {
                if (visited++ >= nodeBudget)
                {
                    UnityEngine.Debug.LogWarning("[SlimeBridge] layer assignment hit its node budget; some imported nodes keep their default layer");
                    return;
                }
                var current = stack.Pop();
                if (current == null) continue;
                current.gameObject.layer = layer;
                for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                {
                    if (visited + stack.Count >= nodeBudget)
                    {
                        UnityEngine.Debug.LogWarning("[SlimeBridge] layer assignment hit its node budget; some imported nodes keep their default layer");
                        return;
                    }
                    stack.Push(current.GetChild(childIndex));
                }
            }
        }

        internal const string ImportedVisualName = "SlimeBridgeImportedVisual";

        private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<SlimeAppearanceApplicator, string> ApplicatorCanonicals =
            new System.Runtime.CompilerServices.ConditionalWeakTable<SlimeAppearanceApplicator, string>();

        /// <summary>Locate the attached imported visual on an actor root via its marker component
        /// (name-independent, survives template copies and renames).</summary>
        internal static GameObject FindImportedVisual(GameObject root)
        {
            try
            {
                if (root == null) return null;
                var marker = root.GetComponentInChildren<ImportedVisualMarker>(true);
                return marker != null ? marker.gameObject : null;
            }
            catch (Exception) { return null; }
        }

        /// <summary>Palette-scope predicate: true when the renderer is part of the attached
        /// imported-visual subtree. The visual root carries ImportedVisualMarker (name-independent,
        /// survives template copies and renames), so an ancestor walk from any renderer inside the
        /// subtree reaches the marker or the resolved visual root before the actor root. Bounded and
        /// fail-open: any failure reads as "not the visual", keeping the legacy placeholder tint.</summary>
        private static bool IsInsideVisualSubtree(Renderer renderer, GameObject instance, GameObject visualRoot)
        {
            if (renderer == null) return false;
            try
            {
                var current = renderer.transform;
                var steps = 0;
                while (current != null && steps++ < 64)
                {
                    if (visualRoot != null && current.gameObject == visualRoot) return true;
                    if (current.gameObject.GetComponent<ImportedVisualMarker>() != null) return true;
                    if (instance != null && current.gameObject == instance) return false;
                    current = current.parent;
                }
            }
            catch (Exception) { }
            return false;
        }

        /// <summary>Bounded suppression walk: disable every placeholder renderer under the actor,
        /// skipping the imported visual subtree. Disabled renderers are collected (for spawn-path
        /// rollback) when a capture list is supplied.</summary>
        private static void SuppressPlaceholders(GameObject instance, GameObject keepVisual, List<Renderer> captured)
        {
            const int nodeBudget = 4096;
            var visited = 0;
            var stack = new Stack<Transform>();
            stack.Push(instance.transform);
            while (stack.Count > 0)
            {
                if (visited++ >= nodeBudget) return;
                var current = stack.Pop();
                if (current == null) continue;
                var currentObject = current.gameObject;
                if (keepVisual != null && currentObject == keepVisual) continue;
                // The alternate source-owned stone mesh is not a native placeholder.
                if (currentObject.GetComponent<ImportedStoneFormMarker>() != null) continue;
                var renderer = currentObject.GetComponent<Renderer>();
                if (renderer != null && renderer.enabled)
                {
                    renderer.enabled = false;
                    captured?.Add(renderer);
                }
                if (visited + stack.Count + current.childCount + 1 > nodeBudget) return;
                for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                    stack.Push(current.GetChild(childIndex));
            }
        }

        /// <summary>Bounded walk re-enabling every renderer under a subtree (imported visual).</summary>
        private static void SetRenderersEnabled(GameObject root, bool enable)
        {
            if (root == null) return;
            const int nodeBudget = 4096;
            var visited = 0;
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            while (stack.Count > 0)
            {
                if (visited++ >= nodeBudget) return;
                var current = stack.Pop();
                if (current == null) continue;
                var renderer = current.gameObject.GetComponent<Renderer>();
                if (renderer != null && renderer.enabled != enable) renderer.enabled = enable;
                if (visited + stack.Count + current.childCount + 1 > nodeBudget) return;
                for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                    stack.Push(current.GetChild(childIndex));
            }
        }

        /// <summary>Fail-safe palette-block sweep over the imported visual subtree (scope repair
        /// for legacy spawns, rebuilt instances and template copies). Only the renderer-wide
        /// MaterialPropertyBlock storage is touched — the storage this feature's spawn palette pass
        /// (and the game's own RecolorSlimeMaterial) writes with the three palette colors; the
        /// game's per-material blocks (`_HeldInVac`) live in separate storage and material textures
        /// (pattern/bump/atlas) live on the materials, so neither is read or written here. Unity
        /// 2019.4 exposes no per-key removal on MaterialPropertyBlock, so the whole renderer block
        /// is cleared: on these renderers the palette keys are its only writers. Bounded
        /// (CollectRenderersBounded), fail-open and idempotent: an empty block is skipped and a
        /// second pass writes nothing.</summary>
        private static void ClearVisualPaletteBlocks(GameObject visual)
        {
            if (visual == null) return;
            try
            {
                var renderers = new List<Renderer>();
                CollectRenderersBounded(visual, renderers);
                foreach (var renderer in renderers)
                {
                    if (renderer == null || !renderer.HasPropertyBlock()) continue;
                    var block = new MaterialPropertyBlock();
                    renderer.GetPropertyBlock(block);
                    if (block.isEmpty) continue;
                    block.Clear();
                    renderer.SetPropertyBlock(block);
                }
            }
            catch (Exception) { /* fail-open: cleanup must never fail an apply */ }
        }

        /// <summary>Apply (idempotently) the converted native visual onto a live actor root:
        /// suppress the placeholder renderers, attach the cached model once, neutralize the
        /// appearance runtime. Shared by the spawn command and the appearance guard that re-runs
        /// whenever the game rebuilds an imported actor (vacpack release, region reload,
        /// appearance re-apply) so the actor never reverts to the placeholder species.</summary>
        internal static bool ApplyImportedVisualToRoot(GameObject instance, string canonical,
            ImportedModelLoader.ImportedModel model, out List<Renderer> suppressedRenderers)
        {
            suppressedRenderers = new List<Renderer>();
            if (instance == null || model.Root == null) return false;
            var visual = FindImportedVisual(instance);
            if (visual == null)
            {
                SuppressPlaceholders(instance, null, suppressedRenderers);
                visual = UnityEngine.Object.Instantiate(model.Root, instance.transform, false);
                visual.name = ImportedVisualName;
                visual.transform.localPosition = model.Root.transform.localPosition;
                visual.transform.localRotation = model.Root.transform.localRotation;
                visual.transform.localScale = model.RootScale;
                visual.AddComponent<ImportedVisualMarker>();
                visual.SetActive(true);
                SetLayerRecursively(visual, instance.layer);
                if (BridgeEntry.LabRestPoseRequested) LabSourceAnimationTrace.Attach(visual);
            }
            else
            {
                if (!visual.activeSelf) visual.SetActive(true);
                SetRenderersEnabled(visual, true);
                SuppressPlaceholders(instance, visual, suppressedRenderers);
            }
            NeutralizeAppearanceRuntime(instance, out _);
            RebindNativeMaterials(instance, visual, canonical);
            // Fail-safe scope repair: spawns from before the palette-scope fix, rebuilt instances
            // and template copies can still carry the legacy renderer-wide palette block on the
            // visual's renderers, which overrides the rebound material colors. Clear it so every
            // path (spawn, rethrow, load, appearance rebuild) renders the validated material
            // palette; a clean visual is a pure no-op.
            ClearVisualPaletteBlocks(visual);
            // Native squash/stretch (hop, vacpack pull) springs the visual's jiggle bones.
            try
            {
                if (!ImportedRubberBone.EnsureAttached(instance, visual, out var rubberProblem) && !string.IsNullOrEmpty(rubberProblem))
                    UnityEngine.Debug.LogWarning("[SlimeBridge] rubber-bone deformation not attached: " + rubberProblem);
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogWarning("[SlimeBridge] rubber-bone attach threw: " + ex.GetType().Name);
            }
            // One visual-only controller per live imported actor. Re-application is idempotent:
            // the same source player is retained without resetting its currently playing clip.
            if (!ImportedGameplayAnimation.EnsureAttached(instance, visual, out var animationProblem) &&
                !string.IsNullOrEmpty(animationProblem))
                UnityEngine.Debug.LogWarning("[SlimeBridge] imported gameplay animation not attached: " + animationProblem);
            // Solar petrification (ringtail family): the machine only exists for species whose
            // private cache declares a stone form (bridge-stone-form v1). Absent or invalid cache
            // is a clean no-op, and the spawn path plus every appearance rebuild share this call:
            // EnsureAttached is idempotent and the machine re-affirms its own state in Update.
            try
            {
                if (ImportedStoneForm.TryGet(canonical, out var stoneForm, out _) &&
                    !ImportedPetrification.EnsureAttached(instance, canonical, stoneForm, out var petrificationProblem) &&
                    !string.IsNullOrEmpty(petrificationProblem))
                    UnityEngine.Debug.LogWarning("[SlimeBridge] petrification not attached: " + petrificationProblem);
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogWarning("[SlimeBridge] petrification attach threw: " + ex.GetType().Name);
            }
            RegisterImportRoot(instance);
            SweepImportFamily(instance);
            return true;
        }

        /// <summary>Rebind the imported visual's renderers onto per-renderer instances of the
        /// actor's own native slime materials: the game's own SR/AMP slime shaders then render the
        /// imported geometry with exactly the destination lighting/ambient/atlas behavior natives
        /// get. Each part receives its OWN material instance (never a shared mutable instance):
        /// the part's source palette drives _TopColor/_MiddleColor/_BottomColor, the baked
        /// markings mask drives the body shader's _StripeTexture slot, and the baked face drives
        /// the eyes shader's _FaceAtlas slot. The converter materials stay in place when no native
        /// template is found (fail-open).</summary>
        private static void RebindNativeMaterials(GameObject instance, GameObject visual, string canonical)
        {
            if (instance == null || visual == null) return;
            try
            {
                Material bodyTemplate = null;
                Material eyesTemplate = null;
                var stack = new Stack<Transform>();
                stack.Push(instance.transform);
                var visited = 0;
                while (stack.Count > 0 && (bodyTemplate == null || eyesTemplate == null))
                {
                    if (visited++ >= 4096) break;
                    var current = stack.Pop();
                    if (current == null) continue;
                    if (current.gameObject == visual) continue; // never source clones from our own visual
                    var renderer = current.gameObject.GetComponent<Renderer>();
                    var shared = renderer != null ? renderer.sharedMaterials : null;
                    if (shared != null)
                    {
                        foreach (var material in shared)
                        {
                            if (material == null || material.shader == null) continue;
                            var shaderName = material.shader.name ?? "";
                            if (bodyTemplate == null && shaderName.Contains("Slime/Body")) bodyTemplate = material;
                            if (eyesTemplate == null && shaderName.Contains("Slime/Face/Eyes")) eyesTemplate = material;
                        }
                    }
                    if (visited + stack.Count + current.childCount + 1 > 4096) break;
                    for (var childIndex = 0; childIndex < current.childCount; childIndex++) stack.Push(current.GetChild(childIndex));
                }
                // A Largo's placeholder is a native Largo whose body material carries its own species' patterns (Rock's stone texture,
                // Tabby stripes ...): paint the imported Largo on the plain Pink slime's body material instead.
                if (canonical.StartsWith("largo.", StringComparison.Ordinal)) bodyTemplate = PlainBodyTemplate() ?? bodyTemplate;
                if (bodyTemplate == null && eyesTemplate == null) return;
                var specTop = Color.clear;
                var specMiddle = Color.clear;
                var specBottom = Color.clear;
                if (SpeciesRegistrar.Current != null &&
                    SpeciesRegistrar.Current.TryGetPalette(canonical, out var paletteTop, out var paletteMiddle, out var paletteBottom))
                {
                    specTop = ToColor(paletteTop);
                    specMiddle = ToColor(paletteMiddle);
                    specBottom = ToColor(paletteBottom);
                }
                var renderers = new List<Renderer>();
                CollectRenderersBounded(visual, renderers);
                // every spawned actor gets its own material instances; they are destroyed with the actor (they used to pile up)
                var owner = visual.GetComponent<ImportedMaterialOwner>() ?? visual.AddComponent<ImportedMaterialOwner>();
                foreach (var renderer in renderers)
                {
                    if (renderer == null) continue;
                    var shared = renderer.sharedMaterials;
                    if (shared == null || shared.Length == 0) continue;
                    var isFace = (renderer.gameObject.name ?? "").ToLowerInvariant().Contains("face");
                    var partData = renderer.gameObject.GetComponent<ImportedPartColors>();
                    // These materials already use their distinct native shader/atlas roles.
                    // Instances borrow the immutable, template-owned passes.
                    if (partData != null && partData.SourceFace) continue;
                    // Parts whose source shader is outside the native slime families (e.g. the
                    // source FX smoke attachment) keep their converter materials: a body-material
                    // clone would paint them as opaque palette blobs. Unknown shaders (legacy
                    // caches) keep the legacy rebind.
                    if (partData != null && partData.SkipNativePaletteRebind) continue;
                    for (var i = 0; i < shared.Length; i++)
                    {
                        var source = shared[i];
                        if (source == null) continue;
                        if (source.name != null && source.name.StartsWith("SlimeBridgeNative", StringComparison.Ordinal)) continue; // already rebound
                        var template = isFace ? (eyesTemplate ?? bodyTemplate) : (bodyTemplate ?? eyesTemplate);
                        // A body with no baked markings of its own must not inherit the placeholder species' stripes (Tabby's), which land on the
                        // imported mesh's UVs as a hard patch: it is painted on the plain Pink body material instead.
                        if (!isFace && (partData == null || partData.Pattern == null)) template = PlainBodyTemplate() ?? template;
                        if (template == null) continue;
                        var clone = new Material(template) { name = isFace ? "SlimeBridgeNativeFace(Clone)" : "SlimeBridgeNativeBody(Clone)" };
                        var partTop = partData != null && partData.Top != Color.clear ? partData.Top : specTop;
                        var partMiddle = partData != null && partData.Middle != Color.clear ? partData.Middle : specMiddle;
                        var partBottom = partData != null && partData.Bottom != Color.clear ? partData.Bottom : specBottom;
                        // A Largo's SR2 body shader spreads its colors over zones its parents' masks define; on the plain body they come out as a
                        // hard dark patch. Keep the zones but pull the bottom color toward the middle one so the blend stays smooth.
                        if (!isFace && canonical.StartsWith("largo.", StringComparison.Ordinal)) partBottom = Color.Lerp(partMiddle, partBottom, 0.35f);
                        SetMaterialColor(clone, "_TopColor", partTop);
                        SetMaterialColor(clone, "_MiddleColor", partMiddle);
                        SetMaterialColor(clone, "_BottomColor", partBottom);
                        // Source gloss balance: the template species' values are much shinier than
                        // the SR2 source (Tabby _Gloss 1.2 vs Ringtail ~0.35), which rendered the
                        // imported visual with over-bright highlights. NaN = source declares none.
                        if (partData != null && !float.IsNaN(partData.Gloss)) SetMaterialFloat(clone, "_Gloss", partData.Gloss);
                        if (partData != null && !float.IsNaN(partData.GlossPower)) SetMaterialFloat(clone, "_GlossPower", partData.GlossPower);
                        if (!isFace && partData != null) partData.BodyLighting?.Apply(clone);
                        if (isFace)
                        {
                            var faceTexture = partData != null && partData.FaceMask != null ? partData.FaceMask : (partData != null ? partData.FaceTex : null);
                            if (faceTexture != null) SetMaterialTexture(clone, "_FaceAtlas", faceTexture);
                        }
                        else if (partData != null && partData.Pattern != null)
                        {
                            SetMaterialTexture(clone, "_StripeTexture", partData.Pattern);
                        }
                        shared[i] = clone;
                        owner.Own(clone);
                    }
                    renderer.sharedMaterials = shared;
                }
            }
            catch (Exception) { /* keep the converter materials on any failure */ }
        }

        private static Material _plainBody;

        /// <summary>The native Pink slime's body material (no markings, no special texture): the neutral base for imported Largos.</summary>
        private static Material PlainBodyTemplate()
        {
            if (_plainBody != null) return _plainBody;
            try
            {
                // the prefab carries no renderers (the applicator builds them); the Pink slime's appearance holds the body material
                var definition = GameContext.Instance?.SlimeDefinitions?.GetSlimeByIdentifiableId(Identifiable.Id.PINK_SLIME);
                var appearance = definition != null && definition.AppearancesDefault != null && definition.AppearancesDefault.Length > 0 ? definition.AppearancesDefault[0] : null;
                if (appearance == null || appearance.Structures == null) return null;
                foreach (var structure in appearance.Structures)
                    if (structure != null && structure.DefaultMaterials != null)
                        foreach (var material in structure.DefaultMaterials)
                            if (material != null && material.shader != null && (material.shader.name ?? "").Contains("Slime/Body")) return _plainBody = material;
            }
            catch (Exception) { }
            return null;
        }

        private static void SetMaterialColor(Material material, string property, Color color)
        {
            if (material == null || color == Color.clear) return;
            var id = Shader.PropertyToID(property);
            if (!material.HasProperty(id)) return;
            material.SetColor(id, color);
        }

        private static void SetMaterialFloat(Material material, string property, float value)
        {
            if (material == null) return;
            var id = Shader.PropertyToID(property);
            if (!material.HasProperty(id)) return;
            material.SetFloat(id, value);
        }

        private static void SetMaterialTexture(Material material, string property, Texture texture)
        {
            if (material == null || texture == null) return;
            var id = Shader.PropertyToID(property);
            if (!material.HasProperty(id)) return;
            material.SetTexture(id, texture);
        }

        private static void CollectRenderersBounded(GameObject root, List<Renderer> output)
        {
            const int nodeBudget = 4096;
            var visited = 0;
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            while (stack.Count > 0)
            {
                if (visited++ >= nodeBudget) return;
                var current = stack.Pop();
                if (current == null) continue;
                var renderer = current.gameObject.GetComponent<Renderer>();
                if (renderer != null) output.Add(renderer);
                if (visited + stack.Count + current.childCount + 1 > nodeBudget) return;
                for (var childIndex = 0; childIndex < current.childCount; childIndex++) stack.Push(current.GetChild(childIndex));
            }
        }

        /// <summary>Resolve (and cache) the imported canonical species for an applicator's actor;
        /// empty string when the actor is not an imported species. Cached per applicator: entries
        /// die with the component, and the species or root of an instance never changes. Negative
        /// resolutions are only cached once the species registrar is armed: a guard running before
        /// registration completes must stay able to resolve later instead of pinning the actor to
        /// the placeholder path for the component's whole lifetime.</summary>
        internal static string ResolveImportedCanonical(SlimeAppearanceApplicator applicator)
        {
            if (applicator == null) return "";
            if (ApplicatorCanonicals.TryGetValue(applicator, out var cached)) return cached;
            var canonical = "";
            var registrarArmed = false;
            try
            {
                var registrar = SpeciesRegistrar.Current;
                registrarArmed = registrar != null;
                var identifiable = applicator.GetComponentInParent<Identifiable>();
                if (registrar != null && identifiable != null && ImportedIdentityPatch.IsImportedId((int)identifiable.id) &&
                    registrar.TryGetCanonical((int)identifiable.id, out var resolved) && resolved != null)
                    canonical = resolved;
            }
            catch (Exception) { }
            // Positive resolutions are always definitive; an empty resolution is definitive only
            // once the registrar is armed (the id is genuinely not imported in this session).
            // Retrying a pre-armament empty costs one bounded parent lookup on the next appearance
            // event, never a permanent misclassification.
            if (canonical.Length > 0 || registrarArmed)
            {
                try { ApplicatorCanonicals.Add(applicator, canonical); } catch (Exception) { }
            }
            return canonical;
        }

        /// <summary>Appearance guard entry point (Harmony postfix on the applicator's Awake and
        /// ApplyAppearance): re-applies the imported visual each time the game rebuilds an
        /// imported actor's appearance.</summary>
        internal static void GuardApplicator(SlimeAppearanceApplicator applicator)
        {
            var canonical = ResolveImportedCanonical(applicator);
            if (string.IsNullOrEmpty(canonical)) return;
            try
            {
                var identifiable = applicator.GetComponentInParent<Identifiable>();
                var root = identifiable != null ? identifiable.gameObject : null;
                if (root == null) return;
                if (!ImportedModelLoader.TryGet(canonical, out var model, out _)) return;
                ApplyImportedVisualToRoot(root, canonical, model, out _);
            }
            catch (Exception) { }
        }

        /// <summary>
        /// Root-cause neutralization of the placeholder appearance runtime on an imported instance.
        /// The native SlimeAppearanceApplicator periodically instantiates a private
        /// RecalculateBoundsHelper whose Update() dereferences state that only the placeholder's
        /// appearance pipeline maintains; on imported instances that helper throws a
        /// NullReferenceException every frame. The imported actor renders through the converted
        /// model instead of the appearance pipeline, so the applicator is disabled (stops it from
        /// creating new helpers while keeping every field reference valid for other consumers such
        /// as the face animator) and any helper already present is destroyed.
        /// Returns the number of components examined (for the sweep's work bound).
        /// </summary>
        private static int NeutralizeAppearanceRuntime(GameObject instance, out bool exhausted)
        {
            const int componentBudget = 4096;
            const int nodeBudget = 4096;
            exhausted = false;
            var examined = 0;
            var visited = 0;
            var helperType = ResolveHelperType();
            try
            {
                var stack = new Stack<Transform>();
                stack.Push(instance.transform);
                while (stack.Count > 0)
                {
                    if (visited++ >= nodeBudget) { exhausted = true; return examined; }
                    var current = stack.Pop();
                    // Targeted TryGetComponent lookups: no component array is ever materialized.
                    var currentObject = current.gameObject;
                    if (currentObject.TryGetComponent<SlimeAppearanceApplicator>(out var applicator))
                    {
                        examined++;
                        if (applicator != null) applicator.enabled = false;
                    }
                    if (helperType != null && currentObject.TryGetComponent(helperType, out var helper))
                    {
                        examined++;
                        if (helper != null) UnityEngine.Object.Destroy(helper);
                    }
                    if (examined >= componentBudget) { exhausted = true; return examined; }
                    // Bound the enqueue itself: a single wide node must not push more children than
                    // the remaining node budget allows.
                    if (visited + stack.Count + current.childCount + 1 > nodeBudget) { exhausted = true; return examined; }
                    for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                        stack.Push(current.GetChild(childIndex));
                }
            }
            catch (Exception)
            {
                // Neutralization is best-effort: a missing type or API must never fail the spawn.
            }
            return examined;
        }

        private static Type? _helperType;
        private static bool _helperTypeResolved;

        /// <summary>Resolve the private nested RecalculateBoundsHelper type once (null when the
        /// native type cannot be located; neutralization then skips helper destruction).</summary>
        private static Type? ResolveHelperType()
        {
            if (_helperTypeResolved) return _helperType;
            _helperTypeResolved = true;
            try
            {
                _helperType = Type.GetType("SlimeAppearanceApplicator+RecalculateBoundsHelper, Assembly-CSharp");
            }
            catch (Exception)
            {
                _helperType = null;
            }
            return _helperType;
        }

        /// <summary>Apply the same neutralization to previously spawned Bridge instances alive
        /// in the scene (imports from earlier sessions persist through saves and would otherwise
        /// keep emitting per-frame exceptions).</summary>
        private static void SweepImportFamily(GameObject current)
        {
            try
            {
                // Tracked roots first: bounded by an explicit cap (the registry is Bridge-owned and
                // pruned of destroyed objects as it is walked).
                const int maxTrackedRoots = 128;
                var tracked = 0;
                for (var i = ImportRoots.Count - 1; i >= 0 && tracked < maxTrackedRoots; i--)
                {
                    var root = ImportRoots[i];
                    if (root == null) { ImportRoots.RemoveAt(i); continue; }
                    tracked++;
                    if (root == current) continue;
                    NeutralizeAppearanceRuntime(root, out var trackedExhausted);
                    if (trackedExhausted) return;
                }
                // Legacy roots (imports persisted through saves from earlier sessions): enumerate
                // SCENE ROOT objects only (never the full transform graph) under explicit caps.
                const int maxLegacyRoots = 64;
                const int maxLegacyComponents = 8192;
                const int maxRootsExamined = 4096;
                var scannedRoots = 0;
                var scannedComponents = 0;
                var rootsExamined = 0;
                var sceneCount = UnityEngine.SceneManagement.SceneManager.sceneCount;
                for (var sceneIndex = 0; sceneIndex < sceneCount; sceneIndex++)
                {
                    var scene = UnityEngine.SceneManagement.SceneManager.GetSceneAt(sceneIndex);
                    if (!scene.IsValid() || !scene.isLoaded) continue;
                    // Pre-materialization bound: GetRootGameObjects returns the full array, so a
                    // scene whose root count exceeds the cap is skipped before any array is built.
                    const int maxSceneRoots = 2048;
                    if (scene.rootCount > maxSceneRoots) return;
                    foreach (var rootObject in scene.GetRootGameObjects())
                    {
                        if (rootsExamined++ >= maxRootsExamined) return;
                        if (rootObject == null || rootObject == current) continue;
                        if (!rootObject.name.StartsWith("SlimeBridgeImport_", StringComparison.Ordinal)) continue;
                        if (scannedRoots >= maxLegacyRoots || scannedComponents >= maxLegacyComponents) return;
                        scannedRoots++;
                        scannedComponents += NeutralizeAppearanceRuntime(rootObject, out var legacyExhausted);
                        if (legacyExhausted) return;
                    }
                }
            }
            catch (Exception)
            {
                // The sweep is opportunistic housekeeping; never fail the spawn over it.
            }
        }

        private static bool ColorsMatch(Color a, Color b) =>
            Math.Abs(a.r - b.r) < 1e-5f && Math.Abs(a.g - b.g) < 1e-5f &&
            Math.Abs(a.b - b.b) < 1e-5f && Math.Abs(a.a - b.a) < 1e-5f;

        /// <summary>Remove helper components copied from the included prefab clone before the copy
        /// is activated (DestroyImmediate is required pre-activation; a later appearance change
        /// rebuilds them properly). Bounded node-by-node with targeted lookups: no component array
        /// is ever materialized.</summary>
        private static void StripCopiedRuntimeHelpers(GameObject instance)
        {
            const int nodeBudget = 4096;
            const int componentBudget = 4096;
            var helperType = ResolveHelperType();
            if (helperType == null) return;
            var visited = 0;
            var found = 0;
            try
            {
                var stack = new Stack<Transform>();
                stack.Push(instance.transform);
                while (stack.Count > 0)
                {
                    if (visited++ >= nodeBudget || found >= componentBudget) return;
                    var current = stack.Pop();
                    if (current.gameObject.TryGetComponent(helperType, out var helper) && helper != null)
                    {
                        found++;
                        UnityEngine.Object.DestroyImmediate(helper);
                    }
                    if (visited + stack.Count + current.childCount + 1 > nodeBudget) return;
                    for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                        stack.Push(current.GetChild(childIndex));
                }
            }
            catch (Exception) { }
        }
    }
}
