using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Structured runtime visual diagnostics for imported species (LAB-only, read-only). Dumps the
    /// registered prefab hierarchy and every live scene instance of a canonical id: renderer/mesh
    /// inventory, skeletal data, appearance applicators, materials, active children, and the
    /// runtime bounds-helper population, so placeholder/duplication issues can be diagnosed from
    /// evidence instead of guesswork. Never mutates the scene.
    /// </summary>
    internal static class BridgeDiagnostics
    {
        // LAB palette read-back property ids (renderer-wide block and material slots alike).
        private static readonly int TopColorId = Shader.PropertyToID("_TopColor");
        private static readonly int MiddleColorId = Shader.PropertyToID("_MiddleColor");
        private static readonly int BottomColorId = Shader.PropertyToID("_BottomColor");

        internal static string Collect(GameContext game, SceneContext scene, SpeciesRegistrar registrar,
            string canonical, List<string> problems)
        {
            if (!registrar.TryResolveNativeId(canonical, out var nativeId))
            {
                problems.Add("species not registered in this session");
                return "";
            }
            var id = (Identifiable.Id)nativeId;
            var sb = new StringBuilder(16384);
            sb.Append("{\n  \"schema\": \"bridge-visual-diagnostics\", \"schema_version\": 1");
            sb.Append(",\n  \"canonical_id\": ").Append(RegistrySnapshotFormat.Quote(canonical));
            sb.Append(",\n  \"native_id\": ").Append(nativeId.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\n  \"utc\": ").Append(RegistrySnapshotFormat.Quote(DateTime.UtcNow.ToString("o")));

            sb.Append(",\n  \"prefab\": ");
            var prefab = game.LookupDirector.GetPrefab(id);
            var budget = new[] { 4000 };
            AppendObject(sb, prefab, 1, problems, 0, budget);

            sb.Append(",\n  \"instances\": [");
            const int maxInstances = 32;
            const int instanceScanBudget = 20000;
            const int trackedRootSearchBudget = 4096;
            var instances = new List<GameObject>();
            var matched = 0;
            var scanExhausted = false;
            // Bridge-tracked roots FIRST: the live imported instance must be captured even when
            // the general bounded scan exhausts its budget. Root count is capped and each
            // per-root descent is bounded too (no GetComponentInChildren materialization).
            foreach (var trackedRoot in ImportedSpawner.TrackedImportRoots)
            {
                if (trackedRoot == null) continue;
                Identifiable trackedIdentifiable = null;
                var trackedStack = new Stack<Transform>();
                trackedStack.Push(trackedRoot.transform);
                var trackedVisited = 0;
                while (trackedStack.Count > 0)
                {
                    if (trackedVisited++ >= trackedRootSearchBudget) { scanExhausted = true; break; }
                    var current = trackedStack.Pop();
                    if (current == null) continue;
                    var candidate = current.GetComponent<Identifiable>();
                    if (candidate != null) { trackedIdentifiable = candidate; break; }
                    for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                        trackedStack.Push(current.GetChild(childIndex));
                }
                if (trackedIdentifiable == null || trackedIdentifiable.id != id) continue;
                if (!instances.Contains(trackedRoot))
                {
                    matched++;
                    if (instances.Count < maxInstances) instances.Add(trackedRoot);
                }
            }
            var scanned = 0;
            var sceneCount = UnityEngine.SceneManagement.SceneManager.sceneCount;
            for (var sceneIndex = 0; sceneIndex < sceneCount; sceneIndex++)
            {
                var currentScene = UnityEngine.SceneManagement.SceneManager.GetSceneAt(sceneIndex);
                if (!currentScene.IsValid() || !currentScene.isLoaded) continue;
                // Pre-materialization bound: GetRootGameObjects returns the full array, so a
                // scene whose root count exceeds the cap is skipped before any array is built.
                const int maxSceneRoots = 2048;
                if (currentScene.rootCount > maxSceneRoots) { scanExhausted = true; break; }
                foreach (var rootObject in currentScene.GetRootGameObjects())
                {
                    var stack = new Stack<Transform>();
                    stack.Push(rootObject.transform);
                    while (stack.Count > 0)
                    {
                        if (scanned++ >= instanceScanBudget) { scanExhausted = true; break; }
                        var current = stack.Pop();
                        var identifiable = current.GetComponent<Identifiable>();
                        if (identifiable != null && identifiable.id == id)
                        {
                            var candidate = identifiable.gameObject;
                            if (!instances.Contains(candidate))
                            {
                                matched++;
                                if (instances.Count < maxInstances) instances.Add(candidate);
                            }
                        }
                        for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                        {
                            if (scanned + stack.Count >= instanceScanBudget) { scanExhausted = true; break; }
                            stack.Push(current.GetChild(childIndex));
                        }
                    }
                    if (scanExhausted) break;
                }
            }
            for (var i = 0; i < instances.Count; i++)
            {
                sb.Append(i == 0 ? "\n" : ",\n");
                AppendObject(sb, instances[i], 2, problems, 0, budget);
            }
            if (matched > instances.Count)
            {
                sb.Append(instances.Count == 0 ? "\n" : ",\n");
                sb.Append("{\"omitted_instances\": ").Append((matched - instances.Count).ToString(CultureInfo.InvariantCulture)).Append("}");
            }
            if (scanExhausted)
            {
                sb.Append(instances.Count == 0 ? "\n" : ",\n");
                sb.Append("{\"instance_scan_budget_exhausted\": true}");
            }
            sb.Append(instances.Count == 0 && matched == 0 && !scanExhausted ? "]" : "\n  ]");

            sb.Append(",\n  \"helper_scan\": ");
            AppendHelperScan(sb, instances);
            sb.Append(",\n  \"imported_visual_proof\": ");
            AppendImportedVisualProof(sb, instances);
            sb.Append(",\n  \"native_gameplay_snapshot\": ");
            AppendNativeGameplaySnapshot(sb, game, scene, id);
            sb.Append("\n}\n");
            return sb.ToString();
        }

        /// <summary>Targeted, bounded dump of each attached imported visual subtree: proves the
        /// skinned renderers, counts the bound bones and records the live locals of the procedural
        /// rig bones (bone_probe) so deformation can be validated by comparing against the rest
        /// values in the portable model document.</summary>
        private static void AppendImportedVisualProof(StringBuilder sb, List<GameObject> instances)
        {
            const int maxVisualNodes = 1024;
            const int maxRenderers = 64;
            const int maxRows = 24;
            sb.Append("{\n    \"instances\": [");
            var rows = new List<string>();
            foreach (var root in instances)
            {
                if (rows.Count >= maxRows) break;
                var visual = FindByName(root.transform, "SlimeBridgeImportedVisual", maxVisualNodes);
                if (visual == null) continue;
                var rendererRows = new List<string>();
                var boneRows = new List<string>();
                var skinned = 0;
                var meshRenderers = 0;
                var boneCount = 0;
                var bodyRenderers = 0;
                var reboundBodies = 0;
                var rebindSkippedParts = 0;
                var stack = new Stack<Transform>();
                stack.Push(visual);
                var visited = 0;
                while (stack.Count > 0 && visited < maxVisualNodes)
                {
                    visited++;
                    var current = stack.Pop();
                    if (rendererRows.Count < maxRenderers)
                    {
                        foreach (var component in current.GetComponents<Renderer>())
                        {
                            if (rendererRows.Count >= maxRenderers) break;
                            if (component is SkinnedMeshRenderer) skinned++;
                            else meshRenderers++;
                            var partData = component.gameObject.GetComponent<ImportedPartColors>();
                            if (partData != null && partData.SkipNativePaletteRebind)
                            {
                                // Source parts outside the native slime families keep their
                                // converter materials by design: neither body renders nor
                                // rebind failures.
                                rebindSkippedParts++;
                            }
                            else if (partData == null || !partData.SourceFace)
                            {
                                bodyRenderers++;
                                if (IsFullyRebound(component)) reboundBodies++;
                            }
                            rendererRows.Add(DescribeRenderer(component));
                        }
                    }
                    var name = current.gameObject.name;
                    if (name.StartsWith("bone_", StringComparison.Ordinal))
                    {
                        boneCount++;
                        if (boneRows.Count < 16 && (name == "bone_jiggle_top" || name == "bone_core" ||
                            name == "bone_jiggle_bot" || name == "bone_jiggle_ear_lef" || name == "bone_jiggle_ear_rig" ||
                            name == "bone_jiggle_tail_base" || name == "bone_jiggle_tail01"))
                        {
                            var rotation = current.localRotation;
                            var position = current.localPosition;
                            boneRows.Add("{\"name\": " + RegistrySnapshotFormat.Quote(name) +
                                         ", \"q\": [" + rotation.x.ToString("R", CultureInfo.InvariantCulture) + ", " +
                                         rotation.y.ToString("R", CultureInfo.InvariantCulture) + ", " +
                                         rotation.z.ToString("R", CultureInfo.InvariantCulture) + ", " +
                                         rotation.w.ToString("R", CultureInfo.InvariantCulture) + "]" +
                                         ", \"p\": [" + position.x.ToString("R", CultureInfo.InvariantCulture) + ", " +
                                         position.y.ToString("R", CultureInfo.InvariantCulture) + ", " +
                                         position.z.ToString("R", CultureInfo.InvariantCulture) + "]}");
                        }
                    }
                    for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                        stack.Push(current.GetChild(childIndex));
                }
                rows.Add("{\"instance\": " + RegistrySnapshotFormat.Quote(root.name) +
                         ", \"visual_path\": " + RegistrySnapshotFormat.Quote(PathOf(visual.gameObject, root)) +
                         ", \"active\": " + (visual.gameObject.activeInHierarchy ? "true" : "false") +
                         ", \"bones\": " + boneCount.ToString(CultureInfo.InvariantCulture) +
                         ", \"skinned_renderers\": " + skinned.ToString(CultureInfo.InvariantCulture) +
                         ", \"mesh_renderers\": " + meshRenderers.ToString(CultureInfo.InvariantCulture) +
                         RebindEvidenceJson(bodyRenderers, reboundBodies, rebindSkippedParts) +
                         ", \"renderers\": [" + string.Join(", ", rendererRows) + "]" +
                         ", \"bone_probe\": [" + string.Join(", ", boneRows) + "]" +
                         SourceAnimationJson(visual.gameObject) +
                         ", \"gameplay\": " + GameplayAnimationJson(root) +
                         (BridgeEntry.LabRestPoseRequested ? ", \"petrification\": " + PetrificationJson(root) : "") + "}");
            }
            sb.Append(rows.Count == 0 ? "]" : "\n      " + string.Join(",\n      ", rows) + "\n    ]");
            sb.Append("\n  }");
        }

        /// <summary>Source-animation state of one imported visual: the native playback read-back, whether
        /// the procedural jiggle approximation is present, and (LAB rest-pose runs only) the timed trace.</summary>
        private static string SourceAnimationJson(GameObject visual)
        {
            var player = visual.GetComponent<ImportedSourceAnimation>();
            var jiggle = visual.GetComponent<SlimeBridgeJiggle>() != null;
            if (player == null) return ", \"source_animation\": null, \"jiggle_present\": " + (jiggle ? "true" : "false");
            var playing = player.TryReadPlayback(out var clipName, out var clipTime, out var length);
            var trace = visual.GetComponent<LabSourceAnimationTrace>();
            return ", \"source_animation\": {\"playing\": " + (playing ? "true" : "false") +
                   ", \"clip\": " + RegistrySnapshotFormat.Quote(clipName) +
                   ", \"state_time\": " + clipTime.ToString("R", CultureInfo.InvariantCulture) +
                   ", \"length\": " + length.ToString("R", CultureInfo.InvariantCulture) + "}" +
                   ", \"jiggle_present\": " + (jiggle ? "true" : "false") +
                   (trace != null ? ", \"source_animation_trace\": " + trace.ToJson() : "");
        }

        private static string GameplayAnimationJson(GameObject actor)
        {
            var driver = actor.GetComponent<ImportedGameplayAnimation>();
            if (driver == null) return "null";
            // A diagnostic failure is data, not an excuse to touch native gameplay.
            try { return driver.ToJson(); }
            catch (Exception error) { return "{\"read_error\": " + RegistrySnapshotFormat.Quote(error.GetType().Name) + "}"; }
        }

        /// <summary>LAB rest-pose only: the actor's petrification machine state (read-only; the
        /// machine re-affirms its own state in its own Update, diagnostics never mutate it).</summary>
        private static string PetrificationJson(GameObject actor)
        {
            var machine = actor.GetComponent<ImportedPetrification>();
            if (machine == null) return "null";
            try { return machine.ToJson(); }
            catch (Exception error) { return "{\"read_error\": " + RegistrySnapshotFormat.Quote(error.GetType().Name) + "}"; }
        }

        /// <summary>Requested snapshot only: native reads, never Add/Decrement/shoot/consume APIs.</summary>
        private static void AppendNativeGameplaySnapshot(StringBuilder output, GameContext game,
            SceneContext scene, Identifiable.Id id)
        {
            string? save = null;
            var loading = true;
            string? inventory = null;
            string? readError = null;
            try
            {
                var director = game != null ? game.AutoSaveDirector : null;
                if (director != null)
                {
                    loading = director.IsLoadingGame();
                    if (!loading && director.SavedGame != null) save = director.SavedGame.GetName();
                }
                if (!loading && !string.IsNullOrEmpty(save) && scene != null && scene.PlayerState != null)
                {
                    var ammo = scene.PlayerState.GetAmmo(PlayerState.AmmoMode.DEFAULT);
                    if (ammo != null)
                    {
                        var usable = ammo.GetUsableSlotCount();
                        // Fail closed instead of reporting an incomplete aggregate as a total.
                        if (usable < 0 || usable > 64) throw new InvalidOperationException("native usable slot bound exceeded");
                        var slots = new StringBuilder();
                        var total = 0;
                        var matching = 0;
                        for (var slot = 0; slot < usable; slot++)
                        {
                            var slotId = ammo.GetSlotName(slot);
                            var count = ammo.GetSlotCount(slot);
                            if (count < 0) throw new InvalidOperationException("negative native slot count");
                            if (slotId != id) continue;
                            total = checked(total + count);
                            if (matching++ > 0) slots.Append(", ");
                            slots.Append("{\"slot\": ").Append(slot.ToString(CultureInfo.InvariantCulture))
                                .Append(", \"native_id\": ").Append(((int)slotId).ToString(CultureInfo.InvariantCulture))
                                .Append(", \"count\": ").Append(count.ToString(CultureInfo.InvariantCulture)).Append("}");
                        }
                        inventory = "{\"ammo_mode\": \"DEFAULT\", \"native_id\": " + ((int)id).ToString(CultureInfo.InvariantCulture) +
                            ", \"usable_slots\": " + usable.ToString(CultureInfo.InvariantCulture) +
                            ", \"slots\": [" + slots + "], \"total\": " + total.ToString(CultureInfo.InvariantCulture) + "}";
                    }
                }
            }
            catch (Exception error) { readError = error.GetType().Name + ": " + error.Message; }
            output.Append("{\"saved_game_identity\": ").Append(save != null ? RegistrySnapshotFormat.Quote(save) : "null")
                .Append(", \"loading\": ").Append(loading ? "true" : "false")
                .Append(", \"vacpack\": ").Append(inventory ?? "null")
                .Append(", \"player_pose\": ");
            AppendWorldPose(output, scene != null && scene.Player != null ? scene.Player.transform : null);
            output.Append(", \"camera_pose\": ");
            var camera = Camera.main;
            AppendWorldPose(output, camera != null ? camera.transform : null);
            if (readError != null) output.Append(", \"read_error\": ").Append(RegistrySnapshotFormat.Quote(readError));
            output.Append("}");
        }

        private static void AppendWorldPose(StringBuilder output, Transform? transform)
        {
            if (transform == null) { output.Append("null"); return; }
            var p = transform.position;
            var q = transform.rotation;
            if (!Finite(p.x) || !Finite(p.y) || !Finite(p.z) || !Finite(q.x) || !Finite(q.y) || !Finite(q.z) || !Finite(q.w))
            { output.Append("null"); return; }
            output.Append("{\"name\": ").Append(RegistrySnapshotFormat.Quote(transform.gameObject.name))
                .Append(", \"position\": [").Append(p.x.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                .Append(p.y.ToString("R", CultureInfo.InvariantCulture)).Append(", ").Append(p.z.ToString("R", CultureInfo.InvariantCulture))
                .Append("], \"rotation\": [").Append(q.x.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                .Append(q.y.ToString("R", CultureInfo.InvariantCulture)).Append(", ").Append(q.z.ToString("R", CultureInfo.InvariantCulture))
                .Append(", ").Append(q.w.ToString("R", CultureInfo.InvariantCulture)).Append("]}");
        }

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        /// <summary>Bounded breadth-first search for a named transform (imported visual root).</summary>
        private static Transform FindByName(Transform root, string name, int budget)
        {
            var stack = new Stack<Transform>();
            stack.Push(root);
            var visited = 0;
            while (stack.Count > 0 && visited < budget)
            {
                visited++;
                var current = stack.Pop();
                if (string.Equals(current.gameObject.name, name, StringComparison.Ordinal)) return current;
                for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                    stack.Push(current.GetChild(childIndex));
            }
            return null;
        }

        /// <summary>Find and count runtime-added appearance bounds helpers on every instance
        /// (bounded DURING traversal: manual hierarchy walk with a per-instance component budget;
        /// no whole-hierarchy materialization; markers record any exhaustion).</summary>
        private static void AppendHelperScan(StringBuilder sb, List<GameObject> instances)
        {
            const int maxComponentsPerInstance = 2048;
            const int maxNodesPerInstance = 4096;
            var total = 0;
            sb.Append("{\n    \"total\": ");
            var perInstance = new List<string>();
            foreach (var root in instances)
            {
                var helpers = new List<string>();
                var examined = 0;
                var visited = 0;
                var nodeExhausted = false;
                var componentExhausted = false;
                var stack = new Stack<Transform>();
                stack.Push(root.transform);
                while (stack.Count > 0)
                {
                    if (visited++ >= maxNodesPerInstance) { nodeExhausted = true; break; }
                    var current = stack.Pop();
                    if (examined >= maxComponentsPerInstance) { componentExhausted = true; break; }
                    // Targeted lookup: the private nested helper type is resolved once; no component
                    // array is ever materialized.
                    var helperType = ResolveHelperType();
                    if (helperType != null && current.gameObject.TryGetComponent(helperType, out var helper))
                    {
                        examined++;
                        if (helper != null)
                        {
                            total++;
                            helpers.Add("{\"path\": " + RegistrySnapshotFormat.Quote(PathOf(helper.gameObject, root)) +
                                        ", \"enabled\": " + ((helper as Behaviour) != null && ((Behaviour)helper).enabled ? "true" : "false") +
                                        ", \"active\": " + (helper.gameObject.activeInHierarchy ? "true" : "false") + "}");
                        }
                    }
                    if (componentExhausted) break;
                    for (var childIndex = 0; childIndex < current.childCount; childIndex++)
                    {
                        if (visited + stack.Count >= maxNodesPerInstance) { nodeExhausted = true; break; }
                        stack.Push(current.GetChild(childIndex));
                    }
                }
                var row = "{\"instance\": " + RegistrySnapshotFormat.Quote(root.name) +
                          ", \"helpers\": [" + string.Join(", ", helpers) + "]";
                if (nodeExhausted)
                    row += ", \"node_budget_exhausted\": true";
                if (componentExhausted)
                    row += ", \"component_budget_exhausted\": true";
                row += "}";
                perInstance.Add(row);
            }
            sb.Append(total.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\n    \"per_instance\": [");
            sb.Append(perInstance.Count == 0 ? "]" : "\n      " + string.Join(",\n      ", perInstance) + "\n    ]");
            sb.Append("\n  }");
        }

        private static Type _helperType;
        private static bool _helperTypeResolved;

        /// <summary>Resolve the private nested RecalculateBoundsHelper type once.</summary>
        private static Type ResolveHelperType()
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

        private static string PathOf(GameObject obj, GameObject root)
        {
            var parts = new List<string>();
            var current = obj.transform;
            while (current != null)
            {
                parts.Add(current.name);
                if (current.gameObject == root) break;
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private const int MaxDepth = 24;
        private const int MaxChildrenPerNode = 24;

        private static void AppendObject(StringBuilder sb, GameObject? obj, int indent, List<string> problems,
            int depth, int[] budget)
        {
            if (obj == null) { sb.Append("null"); return; }
            if (depth > MaxDepth || budget[0] <= 0)
            {
                sb.Append("{\"truncated\": true, \"reason\": ").Append(depth > MaxDepth ? "\"max_depth\"" : "\"node_budget\"").Append("}");
                return;
            }
            budget[0]--;
            var pad = new string(' ', indent * 2);
            sb.Append("{\"name\": ").Append(RegistrySnapshotFormat.Quote(obj.name));
            sb.Append(", \"activeSelf\": ").Append(obj.activeSelf ? "true" : "false");
            sb.Append(", \"activeInHierarchy\": ").Append(obj.activeInHierarchy ? "true" : "false");
            sb.Append(", \"layer\": ").Append(obj.layer.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"position\": [").Append(obj.transform.position.x.ToString("F2", CultureInfo.InvariantCulture))
              .Append(", ").Append(obj.transform.position.y.ToString("F2", CultureInfo.InvariantCulture))
              .Append(", ").Append(obj.transform.position.z.ToString("F2", CultureInfo.InvariantCulture)).Append("]");
            sb.Append(", \"renderers\": [");
            var renderers = obj.GetComponents<Renderer>();
            var rows = new List<string>();
            foreach (var renderer in renderers) rows.Add(DescribeRenderer(renderer));
            sb.Append(string.Join(", ", rows));
            sb.Append("], \"components\": [");
            var componentNames = new List<string>();
            foreach (var component in obj.GetComponents<Component>())
            {
                if (component == null) continue;
                if (component is Renderer) continue; // covered in renderers[]
                if (component is UnityEngine.Animator animator)
                {
                    componentNames.Add("{\"type\": \"Animator\", \"controller\": " + RegistrySnapshotFormat.Quote(
                        animator.runtimeAnimatorController != null ? animator.runtimeAnimatorController.name : "") +
                        ", \"enabled\": " + (animator.enabled ? "true" : "false") + "}");
                    continue;
                }
                if (component is MonoBehaviour)
                {
                    componentNames.Add("{\"type\": " + RegistrySnapshotFormat.Quote(component.GetType().Name) + "}");
                }
            }
            sb.Append(string.Join(", ", componentNames));
            sb.Append("], \"children\": [");
            var children = new List<string>();
            var childCount = obj.transform.childCount;
            for (var i = 0; i < childCount && i < MaxChildrenPerNode; i++)
            {
                var childBuilder = new StringBuilder();
                AppendObject(childBuilder, obj.transform.GetChild(i).gameObject, indent + 1, problems, depth + 1, budget);
                children.Add(childBuilder.ToString());
            }
            if (childCount > MaxChildrenPerNode)
            {
                children.Add("{\"omitted_children\": " + (childCount - MaxChildrenPerNode).ToString(CultureInfo.InvariantCulture) + "}");
            }
            sb.Append(string.Join(", ", children));
            sb.Append("], \"child_count\": ").Append(childCount.ToString(CultureInfo.InvariantCulture));
            sb.Append("}");
        }

        private static string DescribeRenderer(Renderer renderer)
        {
            var sb = new StringBuilder(256);
            sb.Append("{\"type\": ").Append(RegistrySnapshotFormat.Quote(renderer.GetType().Name));
            sb.Append(", \"path\": ").Append(RegistrySnapshotFormat.Quote(renderer.gameObject.name));
            sb.Append(", \"enabled\": ").Append(renderer.enabled ? "true" : "false");
            var filter = renderer.GetComponent<MeshFilter>();
            if (filter != null && filter.sharedMesh != null)
            {
                sb.Append(", \"mesh\": ").Append(RegistrySnapshotFormat.Quote(filter.sharedMesh.name));
                sb.Append(", \"vertices\": ").Append(filter.sharedMesh.vertexCount.ToString(CultureInfo.InvariantCulture));
                sb.Append(", \"subMeshes\": ").Append(filter.sharedMesh.subMeshCount.ToString(CultureInfo.InvariantCulture));
            }
            if (renderer is SkinnedMeshRenderer skinned)
            {
                sb.Append(", \"skinned\": true");
                sb.Append(", \"rootBone\": ").Append(RegistrySnapshotFormat.Quote(
                    skinned.rootBone != null ? skinned.rootBone.name : ""));
                sb.Append(", \"bones\": ").Append(skinned.bones != null
                    ? skinned.bones.Count(b => b != null).ToString(CultureInfo.InvariantCulture) : "0");
                sb.Append(", \"bone_names\": [").Append(string.Join(", ",
                    skinned.bones.Select(b => RegistrySnapshotFormat.Quote(b != null ? b.name : "")))).Append("]");
                if (skinned.sharedMesh != null)
                {
                    if (BridgeEntry.LabRestPoseRequested) AppendRestGeometry(sb, skinned);
                    sb.Append(", \"mesh\": ").Append(RegistrySnapshotFormat.Quote(skinned.sharedMesh.name));
                    sb.Append(", \"vertices\": ").Append(skinned.sharedMesh.vertexCount.ToString(CultureInfo.InvariantCulture));
                    sb.Append(", \"subMeshes\": ").Append(skinned.sharedMesh.subMeshCount.ToString(CultureInfo.InvariantCulture));
                }
            }
            var partData = renderer.gameObject.GetComponent<ImportedPartColors>();
            var sourceFace = partData != null && partData.SourceFace;
            sb.Append(", \"source_face\": ").Append(sourceFace ? "true" : "false");
            if (BridgeEntry.LabRestPoseRequested)
            {
                // LAB read-back of the model-row provenance (read-only): the role and source
                // shader correlate this renderer with its cache row, and the skip flag records
                // the deliberate rebind exemption for non-slime source shaders.
                sb.Append(", \"source_role\": ").Append(RegistrySnapshotFormat.Quote(partData != null ? partData.SourceRole : ""));
                sb.Append(", \"source_shader\": ").Append(RegistrySnapshotFormat.Quote(partData != null ? partData.SourceShader : ""));
                sb.Append(", \"native_palette_rebind_skipped\": ").Append(partData != null && partData.SkipNativePaletteRebind ? "true" : "false");
            }
            if (sourceFace)
            {
                var owner = renderer.gameObject.GetComponent<SourceFaceResourceOwner>();
                sb.Append(", \"source_owned_resources\": ").Append(owner != null ? owner.OwnedResourceCount : 0);
            }
            sb.Append(", \"materials\": [");
            var mats = new List<string>();
            var materials = renderer.sharedMaterials;
            if (materials != null)
            {
                foreach (var material in materials)
                {
                    if (material == null) { mats.Add("null"); continue; }
                    // Null Render deliberately has no conventional _Color/_MainTex slots.
                    var color = material.HasProperty("_Color") ? material.GetColor("_Color") : Color.clear;
                    var mainTexture = material.HasProperty("_MainTex") ? material.GetTexture("_MainTex") : null;
                    mats.Add("{\"name\": " + RegistrySnapshotFormat.Quote(material.name) +
                             ", \"shader\": " + RegistrySnapshotFormat.Quote(material.shader != null ? material.shader.name : "") +
                             ", \"mainTex\": " + RegistrySnapshotFormat.Quote(mainTexture != null ? mainTexture.name : "") +
                             ", \"color\": [" + color.r.ToString("F3", CultureInfo.InvariantCulture) + ", " +
                             color.g.ToString("F3", CultureInfo.InvariantCulture) + ", " +
                             color.b.ToString("F3", CultureInfo.InvariantCulture) + "]" +
                             ", \"hasMainTex\": " + (material.HasProperty(Shader.PropertyToID("_MainTex")) ? "true" : "false") +
                             ", \"hasTop\": " + (material.HasProperty(Shader.PropertyToID("_TopColor")) ? "true" : "false") +
                             ", \"hasMiddle\": " + (material.HasProperty(Shader.PropertyToID("_MiddleColor")) ? "true" : "false") +
                             ", \"hasBottom\": " + (material.HasProperty(Shader.PropertyToID("_BottomColor")) ? "true" : "false") +
                             (BridgeEntry.LabRestPoseRequested ? PaletteReadback(material) : "") +
                             (partData != null && partData.HasBodyLighting && BridgeEntry.LabRestPoseRequested
                                 ? ", \"source_body_lighting_matches\": " + (partData.BodyLighting?.Matches(material) == true ? "true" : "false") : "") +
                             (sourceFace && BridgeEntry.LabRestPoseRequested ? ", \"source_face_evidence\": " + SourceFaceEvidence(material) : "") + "}");
                }
            }
            sb.Append(string.Join(", ", mats));
            sb.Append("]");
            if (BridgeEntry.LabRestPoseRequested) sb.Append(", \"color_block\": ").Append(ColorBlockJson(renderer));
            sb.Append("}");
            return sb.ToString();
        }

        /// <summary>LAB read-back of the three palette slots actually present on a material
        /// (values read, never written); the A/B diagnosis compares these with the renderer
        /// color block below.</summary>
        private static string PaletteReadback(Material material)
        {
            var sb = new StringBuilder(", \"palette\": {");
            var comma = false;
            AppendPaletteColor(sb, ref comma, material, "_TopColor", TopColorId);
            AppendPaletteColor(sb, ref comma, material, "_MiddleColor", MiddleColorId);
            AppendPaletteColor(sb, ref comma, material, "_BottomColor", BottomColorId);
            return sb.Append("}").ToString();
        }

        private static void AppendPaletteColor(StringBuilder sb, ref bool comma, Material material, string name, int id)
        {
            if (!material.HasProperty(id)) return;
            if (comma) sb.Append(", ");
            comma = true;
            sb.Append(RegistrySnapshotFormat.Quote(name)).Append(": ");
            AppendColorArray(sb, material.GetColor(id));
        }

        /// <summary>LAB read-back of the renderer-wide palette block (values read, never written).
        /// An empty block reads back default components; the has/empty flags disambiguate
        /// absence. Per-material blocks are separate storage and are not touched here.</summary>
        private static string ColorBlockJson(Renderer renderer)
        {
            try
            {
                var block = new MaterialPropertyBlock();
                renderer.GetPropertyBlock(block);
                var sb = new StringBuilder("{\"has\": ");
                sb.Append(renderer.HasPropertyBlock() ? "true" : "false");
                sb.Append(", \"empty\": ").Append(block.isEmpty ? "true" : "false");
                sb.Append(", \"top\": ");
                AppendColorArray(sb, block.GetColor(TopColorId));
                sb.Append(", \"middle\": ");
                AppendColorArray(sb, block.GetColor(MiddleColorId));
                sb.Append(", \"bottom\": ");
                AppendColorArray(sb, block.GetColor(BottomColorId));
                return sb.Append("}").ToString();
            }
            catch (Exception error)
            {
                return "{\"read_error\": " + RegistrySnapshotFormat.Quote(error.GetType().Name) + "}";
            }
        }

        private static void AppendColorArray(StringBuilder sb, Color color)
        {
            sb.Append("[").Append(color.r.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                .Append(color.g.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                .Append(color.b.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                .Append(color.a.ToString("R", CultureInfo.InvariantCulture)).Append("]");
        }

        /// <summary>Postcondition input for the LAB A/B: every material of a body renderer must be
        /// a per-part native clone (the rebind names them SlimeBridgeNative*); source-face parts
        /// are excluded by the caller. Read-only.</summary>
        private static bool IsFullyRebound(Renderer renderer)
        {
            var shared = renderer.sharedMaterials;
            if (shared == null || shared.Length == 0) return false;
            foreach (var material in shared)
            {
                if (material == null || material.name == null ||
                    !material.name.StartsWith("SlimeBridgeNative", StringComparison.Ordinal)) return false;
            }
            return true;
        }

        /// <summary>LAB-only rebind evidence for the A/B postconditions (empty string when
        /// this is not a rest-pose proof run): every body renderer must sit on per-part native
        /// clones; source-face parts and source-shader skips are excluded by the caller.</summary>
        private static string RebindEvidenceJson(int bodyRenderers, int reboundBodies, int rebindSkippedParts)
        {
            if (!BridgeEntry.LabRestPoseRequested) return "";
            return ", \"visual_rebind_ok\": " + (bodyRenderers > 0 && reboundBodies == bodyRenderers ? "true" : "false") +
                   ", \"body_renderers\": " + bodyRenderers.ToString(CultureInfo.InvariantCulture) +
                   ", \"rebind_skipped_parts\": " + rebindSkippedParts.ToString(CultureInfo.InvariantCulture);
        }
        /// <summary>Read-back of immutable source passes; only called by the bounded LAB proof.</summary>
        private static string SourceFaceEvidence(Material material)
        {
            var sb = new StringBuilder("{\"render_queue\": ");
            sb.Append(material.renderQueue.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"floats\": {");
            var comma = false;
            foreach (var property in new[] { "_EyeSmoothStepBase", "_EyeSmoothStepRange", "_MouthSmoothStepBase", "_MouthSmoothStepRange" })
            {
                if (!material.HasProperty(property)) continue;
                if (comma) sb.Append(", ");
                comma = true;
                sb.Append(RegistrySnapshotFormat.Quote(property)).Append(": ")
                    .Append(material.GetFloat(property).ToString("R", CultureInfo.InvariantCulture));
            }
            sb.Append("}, \"colors\": {");
            comma = false;
            foreach (var property in new[] { "_EyeRed", "_EyeGreen", "_EyeBlue", "_MouthColor" })
            {
                if (!material.HasProperty(property)) continue;
                if (comma) sb.Append(", ");
                comma = true;
                var color = material.GetColor(property);
                sb.Append(RegistrySnapshotFormat.Quote(property)).Append(": [")
                    .Append(color.r.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                    .Append(color.g.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                    .Append(color.b.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                    .Append(color.a.ToString("R", CultureInfo.InvariantCulture)).Append("]");
            }
            sb.Append("}, \"face_atlas\": ");
            var atlas = material.HasProperty("_FaceAtlas") ? material.GetTexture("_FaceAtlas") as Texture2D : null;
            if (atlas == null) return sb.Append("null}").ToString();
            sb.Append("{\"name\": ").Append(RegistrySnapshotFormat.Quote(atlas.name))
                .Append(", \"width\": ").Append(atlas.width.ToString(CultureInfo.InvariantCulture))
                .Append(", \"height\": ").Append(atlas.height.ToString(CultureInfo.InvariantCulture))
                .Append(", \"mip_count\": ").Append(atlas.mipmapCount.ToString(CultureInfo.InvariantCulture))
                .Append(", \"filter_mode\": ").Append(((int)atlas.filterMode).ToString(CultureInfo.InvariantCulture))
                .Append(", \"wrap_u\": ").Append(((int)atlas.wrapModeU).ToString(CultureInfo.InvariantCulture))
                .Append(", \"wrap_v\": ").Append(((int)atlas.wrapModeV).ToString(CultureInfo.InvariantCulture));
            try
            {
                var count = (long)atlas.width * atlas.height;
                if (count <= 0 || count > 65536) throw new InvalidOperationException("source face atlas diagnostic size refused");
                var pixels = atlas.GetPixels32();
                if (pixels.Length != count) throw new InvalidOperationException("source face atlas diagnostic pixel count mismatch");
                // Unity CPU rows are bottom-up; PNG/source receipts are top-down RGBA.
                var bytes = new byte[pixels.Length * 4];
                var offset = 0;
                for (var y = atlas.height - 1; y >= 0; y--)
                    for (var x = 0; x < atlas.width; x++)
                    {
                        var pixel = pixels[y * atlas.width + x];
                        bytes[offset++] = pixel.r; bytes[offset++] = pixel.g;
                        bytes[offset++] = pixel.b; bytes[offset++] = pixel.a;
                    }
                using (var hash = System.Security.Cryptography.SHA256.Create())
                    sb.Append(", \"rgba_sha256\": ").Append(RegistrySnapshotFormat.Quote(
                        BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant()));
            }
            catch (Exception error)
            {
                sb.Append(", \"read_error\": ").Append(RegistrySnapshotFormat.Quote(error.GetType().Name + ": " + error.Message));
            }
            return sb.Append("}}").ToString();
        }

        /// <summary>LAB-only CPU skin proof, never evaluated during ordinary gameplay.</summary>
        private static void AppendRestGeometry(StringBuilder sb, SkinnedMeshRenderer renderer)
        {
            // Bound the diagnostic allocation; the real Ringtail has just 877 vertices.
            if (renderer.sharedMesh.vertexCount > 4096) return;
            var baked = new Mesh();
            try
            {
                renderer.BakeMesh(baked);
                var vertices = baked.vertices;
                sb.Append(", \"baked_vertices\": [");
                for (var i = 0; i < vertices.Length; i++)
                {
                    if (i > 0) sb.Append(", ");
                    var v = vertices[i];
                    sb.Append("[").Append(v.x.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                        .Append(v.y.ToString("R", CultureInfo.InvariantCulture)).Append(", ")
                        .Append(v.z.ToString("R", CultureInfo.InvariantCulture)).Append("]");
                }
                sb.Append("]");
            }
            finally { UnityEngine.Object.Destroy(baked); }
        }

        /// <summary>Run the diagnostic and write it into the LAB out directory (Bridge-owned).</summary>
        internal static bool Write(string outDir, string canonical, GameContext game, SceneContext scene,
            SpeciesRegistrar registrar, List<string> problems)
        {
            var json = Collect(game, scene, registrar, canonical, problems);
            if (json.Length == 0) return false;
            var target = Path.Combine(outDir, "visual-diagnostics-" + Sanitize(canonical) + ".json");
            PublishDiagnosticFile(outDir, Path.GetFileName(target), json);
            return true;
        }

        /// <summary>Only refreshable diagnostics replace their canonical filename. Registry receipts
        /// retain their existing create-only contract. Stage under a pinned directory and atomically
        /// rename the owned file within its OWN parent, never through an absolute destination path.</summary>
        internal static void PublishDiagnosticFile(string directory, string name, string json)
        {
            if (!name.StartsWith("visual-diagnostics-", StringComparison.Ordinal) ||
                !name.EndsWith(".json", StringComparison.Ordinal) || name.Length > 180 ||
                name.IndexOfAny(new[] { '\\', '/', ':', (char)0 }) >= 0)
                throw new IOException("invalid diagnostic publication component");
            using (var lease = RegistrySnapshotFormat.PinCaptureDirectory(directory))
            {
                RequirePlainDirectory(lease.LeafHandle);
                var temporary = name + "." + Guid.NewGuid().ToString("N") + ".tmp";
                using (var owned = RegistrySnapshotFormat.OpenRelative(lease.LeafHandle, temporary, false))
                {
                    try
                    {
                        if (!RegistrySnapshotFormat.DirectoryLease.GetAttributeTag(owned, out var tag) ||
                            (tag.Attributes & 0x410) != 0 || tag.ReparseTag != 0)
                            throw new IOException("diagnostic staging file is not plain");
                        var retained = false;
                        try
                        {
                            owned.DangerousAddRef(ref retained);
                            using (var view = new Microsoft.Win32.SafeHandles.SafeFileHandle(owned.DangerousGetHandle(), false))
                            using (var stream = new FileStream(view, FileAccess.Write, 4096))
                            {
                                var bytes = new UTF8Encoding(false).GetBytes(json);
                                stream.Write(bytes, 0, bytes.Length);
                                stream.Flush(true);
                            }
                        }
                        finally { if (retained) owned.DangerousRelease(); }
                        RequirePlainDirectory(lease.LeafHandle);
                        RenameOwnedWithinParent(owned, name);
                    }
                    catch (Exception failure)
                    {
                        // Cleanup through the exact retained staging handle, never reconstructed paths.
                        var buffer = System.Runtime.InteropServices.Marshal.AllocHGlobal(1);
                        try
                        {
                            System.Runtime.InteropServices.Marshal.WriteByte(buffer, 1);
                            var status = NtSetInformationFile(owned, out _, buffer, 1, 13); // FileDispositionInformation
                            if (status != 0) throw new IOException("diagnostic staging cleanup refused: " + status.ToString("X8", CultureInfo.InvariantCulture), failure);
                        }
                        finally { System.Runtime.InteropServices.Marshal.FreeHGlobal(buffer); }
                        throw;
                    }
                }
            }
        }

        private static void RequirePlainDirectory(Microsoft.Win32.SafeHandles.SafeFileHandle directory)
        {
            if (!RegistrySnapshotFormat.DirectoryLease.GetAttributeTag(directory, out var tag) ||
                (tag.Attributes & 0x10) == 0 || (tag.Attributes & 0x400) != 0 || tag.ReparseTag != 0)
                throw new IOException("diagnostic directory is not plain");
        }

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        private struct NativeRenameInfo
        {
            public uint ReplaceIfExists;
            public IntPtr RootDirectory;
            public uint FileNameLength;
            public ushort FirstCharacter;
        }

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        private struct NativeIoStatus { public IntPtr Status; public UIntPtr Information; }

        [System.Runtime.InteropServices.DllImport("ntdll.dll", ExactSpelling = true)]
        private static extern int NtSetInformationFile(Microsoft.Win32.SafeHandles.SafeFileHandle file,
            out NativeIoStatus status, IntPtr information, uint length, uint informationClass);

        private static void RenameOwnedWithinParent(Microsoft.Win32.SafeHandles.SafeFileHandle owned, string name)
        {
            var bytes = Encoding.Unicode.GetBytes(name);
            var offset = System.Runtime.InteropServices.Marshal.OffsetOf(typeof(NativeRenameInfo), nameof(NativeRenameInfo.FirstCharacter)).ToInt32();
            var length = checked(System.Runtime.InteropServices.Marshal.SizeOf(typeof(NativeRenameInfo)) + bytes.Length);
            var buffer = System.Runtime.InteropServices.Marshal.AllocHGlobal(length);
            try
            {
                var info = new NativeRenameInfo { ReplaceIfExists = 1, RootDirectory = IntPtr.Zero, FileNameLength = (uint)bytes.Length };
                System.Runtime.InteropServices.Marshal.StructureToPtr(info, buffer, false);
                System.Runtime.InteropServices.Marshal.Copy(bytes, 0, IntPtr.Add(buffer, offset), bytes.Length);
                // A simple name and NULL RootDirectory means same parent of the OWNED source file.
                // https://learn.microsoft.com/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
                var status = NtSetInformationFile(owned, out _, buffer, (uint)length, 10); // FileRenameInformation
                if (status != 0) throw new IOException("diagnostic atomic rename refused: " + status.ToString("X8", CultureInfo.InvariantCulture));
            }
            finally { System.Runtime.InteropServices.Marshal.FreeHGlobal(buffer); }
        }

        private static string Sanitize(string value) =>
            new string(value.Select(c => char.IsLetterOrDigit(c) || c == '.' || c == '-' ? c : '_').ToArray());
    }
}
