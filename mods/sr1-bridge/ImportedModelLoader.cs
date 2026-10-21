using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Loads a converted native source-game model (private local asset cache, produced by the
    /// SlimeBridge capture/conversion pipeline) and rebuilds it as destination-runtime Unity
    /// objects: hierarchical mesh objects, textures, materials. Strictly read-only against the
    /// cache; every file is hash-verified against its manifest before use (corruption detection),
    /// and a mismatched or missing cache entry fails closed to the existing placeholder.
    /// </summary>
    internal static class ImportedModelLoader
    {
        private static readonly Dictionary<string, ImportedModel> Cache = new Dictionary<string, ImportedModel>(StringComparer.Ordinal);
        private static readonly List<string> Failed = new List<string>();

        internal struct ImportedModel
        {
            public string CanonicalId;
            public GameObject Root;               // template hierarchy, inactive, never spawned as-is
            public Vector3 RootScale;             // source prefab scale
            public int VertexTotal;
            public int RendererCount;
        }

        internal static bool TryGet(string canonicalId, out ImportedModel model, out string problem)
        {
            model = default;
            problem = "";
            if (!IsSafeToken(canonicalId)) { problem = "unsafe canonical id"; Failed.Add(canonicalId ?? ""); return false; }
            if (Cache.TryGetValue(canonicalId, out model)) return true;
            if (Failed.Contains(canonicalId)) { problem = "model cache entry previously failed"; return false; }
            try
            {
                if (TryLoad(canonicalId, out model, out problem)) { Cache[canonicalId] = model; return true; }
            }
            catch (Exception ex)
            {
                problem = ex.GetType().Name + ": " + ex.Message;
            }
            Failed.Add(canonicalId);
            return false;
        }

        /// <summary>Flat tokens only: lowercase letters/digits, '.' '_' '-' without traversal.</summary>
        private static bool IsSafeToken(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Contains("..")) return false;
            foreach (var c in value)
            {
                var ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
                if (!ok) return false;
            }
            return true;
        }

        /// <summary>Manifest file references must be plain names inside the cache directory.</summary>
        private static bool IsSafeFileName(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Contains("..") ||
                value.IndexOf('/') >= 0 || value.IndexOf('\\') >= 0 ||
                Path.IsPathRooted(value) || value.IndexOf(':') >= 0)
            {
                return false;
            }
            return string.Equals(Path.GetFileName(value), value, StringComparison.Ordinal);
        }

        private static string CacheDirectory(string canonicalId)
        {
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (repoRoot == null) return null;
            var dir = Path.Combine(repoRoot, "Lab", "AssetCache", "sr2-to-sr1", canonicalId);
            return dir;
        }

        private const string ModelFileName = "model.json";

        private static bool TryLoad(string canonicalId, out ImportedModel model, out string problem)
        {
            model = default;
            problem = "";
            var directory = CacheDirectory(canonicalId);
            if (directory == null) { problem = "no converted model cache for " + canonicalId; return false; }
            // Reparse/containment guard FIRST: no existence probe may run before it (a junction at
            // the cache directory would otherwise be followed by the probe itself).
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (repoRoot == null) { problem = "repository root not found for cache guard"; return false; }
            var cacheRoot = Path.Combine(repoRoot, "Lab", "AssetCache", "sr2-to-sr1");
            if (!LabSpecPathGuard.IsAllowedDirectory(cacheRoot, directory, out var guardReason))
            { problem = "cache directory refused: " + guardReason; return false; }
            var manifestPath = Path.Combine(directory, "cache-manifest.json");
            var modelPath = Path.Combine(directory, ModelFileName);
            // File-level guards BEFORE any existence probe or read (child-file symlinks).
            if (!LabSpecPathGuard.IsAllowedFile(directory, manifestPath, out var manifestReason))
            { problem = "cache manifest refused: " + manifestReason; return false; }
            if (!LabSpecPathGuard.IsAllowedFile(directory, modelPath, out var modelReason))
            { problem = "cache model refused: " + modelReason; return false; }
            if (!File.Exists(manifestPath) || !File.Exists(modelPath)) { problem = "cache manifest/model missing"; return false; }
            var manifest = MiniJson.Parse(File.ReadAllText(manifestPath)) as Dictionary<string, object>;
            var files = manifest != null && manifest.TryGetValue("files", out var f) ? f as Dictionary<string, object> : null;
            if (manifest == null || files == null ||
                !string.Equals(manifest.TryGetValue("canonical_id", out var c) ? c as string : null, canonicalId, StringComparison.Ordinal))
            { problem = "cache manifest invalid or foreign"; return false; }
            // The manifest must COVER every file the loader consumes: the model document itself and
            // every texture it references are verified-set members, never implicitly trusted.
            if (!files.ContainsKey(ModelFileName)) { problem = "cache manifest does not cover " + ModelFileName; return false; }
            var verifiedFiles = new HashSet<string>(StringComparer.Ordinal);
            foreach (var pair in files)
            {
                if (!IsSafeFileName(pair.Key)) { problem = "cache manifest references an unsafe file name"; return false; }
                var path = Path.Combine(directory, pair.Key);
                if (!LabSpecPathGuard.IsAllowedFile(directory, path, out var fileReason))
                { problem = "cache file refused: " + fileReason; return false; }
                if (!File.Exists(path)) { problem = "cache file missing: " + pair.Key; return false; }
                if (!string.Equals(Sha256(path), pair.Value as string, StringComparison.OrdinalIgnoreCase))
                { problem = "cache file hash mismatch: " + pair.Key; return false; }
                verifiedFiles.Add(pair.Key);
            }
            var document = MiniJson.Parse(File.ReadAllText(modelPath)) as Dictionary<string, object>;
            if (document == null || !string.Equals(document.TryGetValue("schema", out var schema) ? schema as string : null,
                "bridge-native-mesh", StringComparison.Ordinal))
            { problem = "model schema mismatch"; return false; }

            var root = new GameObject("SlimeBridgeImportedModelTemplate");
            root.SetActive(false);
            UnityEngine.Object.DontDestroyOnLoad(root);
            if (document.TryGetValue("visual_transform", out var transformValue))
            {
                if (!(transformValue is Dictionary<string, object> visualTransform) ||
                    !TryVector3(visualTransform, "local_position", out var sourcePosition) ||
                    !TryVector4(visualTransform, "local_rotation", out var sourceRotation) ||
                    !TryVector3(visualTransform, "local_scale", out var sourceScale) ||
                    !IsFinite(sourcePosition) || !IsFinite(sourceScale) ||
                    float.IsNaN(sourceRotation.sqrMagnitude) || float.IsInfinity(sourceRotation.sqrMagnitude) ||
                    sourceRotation.sqrMagnitude < 0.000001f ||
                    sourceScale.x == 0f || sourceScale.y == 0f || sourceScale.z == 0f)
                { problem = "invalid source visual transform"; UnityEngine.Object.Destroy(root); return false; }
                ApplyLocalTransform(root.transform, visualTransform);
            }
            if (!BuildSkeleton(root.transform, document, out var skeletonProblem))
            {
                // A source bone whose parent row was never built would flatten silently to the
                // model root, degrading every pose with no error: refuse the whole document.
                problem = skeletonProblem;
                UnityEngine.Object.Destroy(root);
                return false;
            }
            var scale = 1f;
            if (document.TryGetValue("prefab_scale", out var scaleValue) && TryFloat(scaleValue, out var parsedScale) && parsedScale > 0f)
                scale = parsedScale;

            var vertexTotal = 0;
            var rendererCount = 0;
            var enabledRendererCount = 0;
            // Fail-closed hierarchy caps: the loader never builds an unbounded node graph.
            const int maxRenderers = 256;
            const int maxNodesTotal = 1024;
            var builtNodes = 0;
            foreach (var groupName in new[] { "skinned_renderers", "static_renderers" })
            {
                if (!document.TryGetValue(groupName, out var groupValue))
                {
                    if (string.Equals(groupName, "skinned_renderers", StringComparison.Ordinal))
                    { problem = "model has no skinned_renderers array"; UnityEngine.Object.Destroy(root); return false; }
                    continue;
                }
                if (!(groupValue is List<object> renderers))
                { problem = "renderer group " + groupName + " has the wrong shape"; UnityEngine.Object.Destroy(root); return false; }
                foreach (var row in renderers)
                {
                    if (!(row is Dictionary<string, object> renderer))
                    { problem = "renderer group " + groupName + " contains a malformed row"; UnityEngine.Object.Destroy(root); return false; }
                    if (rendererCount >= maxRenderers)
                    { problem = "model exceeds the renderer budget"; UnityEngine.Object.Destroy(root); return false; }
                    if (!TryBuildRenderer(directory, renderer, root.transform, verifiedFiles, ref builtNodes, maxNodesTotal,
                        string.Equals(groupName, "static_renderers", StringComparison.Ordinal),
                        out var vertices, out var enabledRow, out var problemDetail))
                    {
                        problem = problemDetail;
                        UnityEngine.Object.Destroy(root);
                        return false;
                    }
                    vertexTotal += vertices;
                    rendererCount++;
                    if (enabledRow) enabledRendererCount++;
                }
            }
            if (rendererCount == 0 || enabledRendererCount == 0)
            {
                // A model whose rows are all authored-disabled would render nothing while the
                // spawner suppresses the placeholder: that must fail closed to the placeholder.
                problem = "model has no enabled renderers";
                UnityEngine.Object.Destroy(root);
                return false;
            }
            if (!ImportedAnimationProbe.TrySample(root, document, out problem))
            {
                UnityEngine.Object.Destroy(root);
                return false;
            }
            // A declared source_animation is authoritative: a malformed descriptor refuses the whole
            // visual (the spawner then keeps the placeholder) instead of silently dropping to jiggle.
            if (!ImportedSourceAnimation.TryAttachDeclared(root, document, out var authoredAnimation, out problem))
            {
                UnityEngine.Object.Destroy(root);
                return false;
            }
            model = new ImportedModel
            {
                CanonicalId = canonicalId,
                Root = root,
                RootScale = root.transform.localScale * scale,
                VertexTotal = vertexTotal,
                RendererCount = rendererCount,
            };
            // Procedural jiggle is only an approximation of motion the source did not provide.
            if (!authoredAnimation && !BridgeEntry.LabRestPoseRequested) root.AddComponent<SlimeBridgeJiggle>();
            return true;
        }

        private static bool TryBuildRenderer(string directory, Dictionary<string, object> renderer, Transform root,
            HashSet<string> verifiedFiles, ref int builtNodes, int maxNodesTotal, bool staticRow,
            out int vertices, out bool enabledRow, out string problem)
        {
            vertices = 0;
            enabledRow = false;
            problem = "";
            var path = renderer.TryGetValue("path", out var p) ? p as string : null;
            if (string.IsNullOrEmpty(path)) { problem = "renderer row has no path"; return false; }
            if (renderer.TryGetValue("enabled", out var enabled) && enabled is bool active && !active)
                return true; // preserve authored disabled state by skipping; contributes no enabled row
            ImportedBodyLighting? bodyLighting = null;
            if (renderer.TryGetValue("materials", out var lightingRowsValue) && lightingRowsValue is List<object> lightingRows &&
                lightingRows.Count > 0 && lightingRows[0] is Dictionary<string, object> lightingMaterial &&
                !ImportedBodyLighting.TryRead(lightingMaterial, out bodyLighting, out problem))
            { problem = "renderer " + path + ": " + problem; return false; }
            Transform parent;
            if (staticRow)
            {
                // Static rows carry an authored source bone parent: every container must already
                // exist (skeleton or authored hierarchy) and parent_bone must agree with the
                // path. Creating a missing container silently (the legacy EnsurePath behavior)
                // would leave the part visibly present but detached from the bone it follows.
                if (!TryResolveStaticParent(root, renderer, path, out parent, out var parentProblem))
                { problem = "renderer " + path + ": " + parentProblem; return false; }
            }
            else
            {
                parent = EnsurePath(root, path, ref builtNodes, maxNodesTotal);
                if (parent == null) { problem = "model hierarchy exceeds the node budget at " + path; return false; }
            }
            if (++builtNodes > maxNodesTotal) { problem = "model hierarchy exceeds the node budget"; return false; }
            var gameObject = new GameObject(SanitizeName(path));
            gameObject.transform.SetParent(parent, false);
            ApplyLocalTransform(gameObject.transform, renderer);
            var mesh = BuildMesh(renderer, out vertices);
            if (mesh == null) { problem = "renderer " + path + " has no mesh data"; return false; }
            if (!TryBuildMaterials(directory, renderer, verifiedFiles, out var materials, out var sourceOwned, out var materialProblem))
            {
                problem = "renderer " + path + ": " + materialProblem;
                UnityEngine.Object.Destroy(mesh);
                UnityEngine.Object.Destroy(gameObject);
                return false;
            }
            if (sourceOwned.Count > 0)
            {
                try { gameObject.AddComponent<SourceFaceResourceOwner>().Retain(sourceOwned, mesh); }
                catch (Exception error)
                {
                    foreach (var resource in sourceOwned) UnityEngine.Object.Destroy(resource);
                    sourceOwned.Clear();
                    UnityEngine.Object.Destroy(mesh);
                    UnityEngine.Object.Destroy(gameObject);
                    problem = "source face ownership refused: " + error.GetType().Name;
                    return false;
                }
            }
            if (TryBindSkin(renderer, mesh, root, out var skinBones))
            {
                var skinned = gameObject.AddComponent<SkinnedMeshRenderer>();
                skinned.sharedMesh = mesh;
                skinned.bones = skinBones;
                skinned.rootBone = skinBones.Length > 0 ? skinBones[0] : null;
                if (renderer.TryGetValue("root_bone", out var rootBoneValue))
                {
                    var rootBoneName = rootBoneValue as string;
                    skinned.rootBone = string.IsNullOrEmpty(rootBoneName) ? null : FindChildByName(root, rootBoneName);
                    if (skinned.rootBone == null)
                    { problem = "source root bone is unresolved"; UnityEngine.Object.Destroy(gameObject); return false; }
                }
                skinned.sharedMaterials = materials;
                skinned.updateWhenOffscreen = true; // imported actors are cheap; avoid culling pop
            }
            else
            {
                var filter = gameObject.AddComponent<MeshFilter>();
                filter.sharedMesh = mesh;
                var meshRenderer = gameObject.AddComponent<MeshRenderer>();
                meshRenderer.sharedMaterials = materials;
            }
            // Per-part source appearance for the native-material rebind: palette + the baked
            // textures this part's native shader slots consume (_StripeTexture mask / _FaceAtlas).
            var partData = gameObject.AddComponent<ImportedPartColors>();
            partData.BodyLighting = bodyLighting;
            partData.SourceFace = sourceOwned.Count > 0;
            // Row provenance seeds the spawn rebind policy and the LAB read-back before the
            // source-face early-out: face rows report their role and shader too.
            partData.SourceRole = renderer.TryGetValue("role", out var roleValue) ? roleValue as string : null;
            if (renderer.TryGetValue("materials", out var shaderRowsValue) && shaderRowsValue is List<object> shaderRows &&
                shaderRows.Count > 0 && shaderRows[0] is Dictionary<string, object> shaderRow &&
                shaderRow.TryGetValue("shader", out var shaderValue))
                partData.SourceShader = shaderValue as string;
            if (partData.SourceFace)
            {
                // Every pass already has its original palette and atlas. Do not collapse
                // to slot zero or overwrite its raw atlas with the legacy synthetic mask.
                enabledRow = true;
                return true;
            }
            if (renderer.TryGetValue("materials", out var materialRowsValue) && materialRowsValue is List<object> materialRows &&
                materialRows.Count > 0 && materialRows[0] is Dictionary<string, object> firstMaterialRow)
            {
                if (firstMaterialRow.TryGetValue("colors", out var colorValue) && colorValue is Dictionary<string, object> colorMap)
                {
                    if (TryColor(colorMap, "_TopColor", out var topColor)) partData.Top = topColor;
                    if (TryColor(colorMap, "_MiddleColor", out var middleColor)) partData.Middle = middleColor;
                    if (TryColor(colorMap, "_BottomColor", out var bottomColor)) partData.Bottom = bottomColor;
                    // Source gloss is stored as RGB triples on the SR2 material; carry the mean so
                    // the rebind can keep the source's matte/shiny balance on the native shader.
                    if (TryColor(colorMap, "_GlossRGB", out var glossColor))
                    {
                        var gloss = (glossColor.r + glossColor.g + glossColor.b) / 3f;
                        if (!float.IsNaN(gloss) && !float.IsInfinity(gloss) && gloss >= 0f && gloss <= 8f) partData.Gloss = gloss;
                    }
                    if (TryColor(colorMap, "_GlossPowerRGB", out var glossPowerColor))
                    {
                        var glossPower = (glossPowerColor.r + glossPowerColor.g + glossPowerColor.b) / 3f;
                        if (!float.IsNaN(glossPower) && !float.IsInfinity(glossPower) && glossPower >= 0f && glossPower <= 8f) partData.GlossPower = glossPower;
                    }
                }
                if (firstMaterialRow.TryGetValue("textures", out var textureRowsValue) && textureRowsValue is List<object> textureRows)
                {
                    foreach (var textureRowObject in textureRows)
                    {
                        if (!(textureRowObject is Dictionary<string, object> textureRow)) continue;
                        var textureProperty = textureRow.TryGetValue("property", out var propValue) ? propValue as string : null;
                        var textureFile = textureRow.TryGetValue("file", out var fileValue) ? fileValue as string : null;
                        if (string.IsNullOrEmpty(textureProperty) || string.IsNullOrEmpty(textureFile) || !IsSafeFileName(textureFile)) continue;
                        if (!verifiedFiles.Contains(textureFile)) continue;
                        var patternPath = Path.Combine(directory, textureFile);
                        if (!LabSpecPathGuard.IsAllowedFile(directory, patternPath, out _)) continue;
                        if (string.Equals(textureProperty, "_StripeTextureBaked", StringComparison.Ordinal))
                            partData.Pattern = LoadTexture(patternPath) ?? partData.Pattern;
                        // The raw face atlas: the destination eyes shader consumes the SOURCE
                        // channel-mask format directly (SR1's own face_default_happy is
                        // bit-identical to the SR2 atlas), never a re-composited albedo.
                        else if (string.Equals(textureProperty, "_FaceAtlas", StringComparison.Ordinal))
                            partData.FaceMask = LoadTexture(patternPath) ?? partData.FaceMask;
                        else if (string.Equals(textureProperty, "_FaceAtlasBaked", StringComparison.Ordinal))
                            partData.FaceMask = LoadTexture(patternPath) ?? partData.FaceMask; // composed mask wins
                    }
                }
            }
            partData.FaceTex = materials != null && materials.Length > 0 && materials[0] != null
                ? materials[0].mainTexture as Texture2D
                : null;
            if (partData.Pattern == null) partData.Pattern = partData.FaceTex;
            enabledRow = true;
            return true;
        }

        private static Transform EnsurePath(Transform root, string path, ref int builtNodes, int maxNodesTotal)
        {
            var parts = path.Split('/');
            var current = root;
            // The last path segment is the renderer node itself; intermediate nodes are authored
            // containers whose local transforms the dump does not carry, so they are recreated as
            // identity transforms.
            for (var i = 0; i < parts.Length - 1; i++)
            {
                var next = current.Find(parts[i]);
                if (next == null)
                {
                    if (++builtNodes > maxNodesTotal) return null;
                    var node = new GameObject(parts[i]);
                    node.transform.SetParent(current, false);
                    next = node.transform;
                }
                current = next;
            }
            return current;
        }

        /// <summary>Static rows attach to an authored source bone: the declared parent_bone must
        /// resolve under the rebuilt skeleton (FindChildByName) AND agree with the row path, whose
        /// containers must already exist. No container is ever created for a static row; any
        /// mismatch refuses the row (fail closed) instead of silently detaching the part from the
        /// bone it follows. Legacy rows without parent_bone keep the strict path contract only.</summary>
        private static bool TryResolveStaticParent(Transform root, Dictionary<string, object> renderer, string path,
            out Transform parent, out string problem)
        {
            parent = null;
            problem = "";
            var parts = path.Split('/');
            if (parts.Length < 2) { problem = "static row has no parent container"; return false; }
            var parentBoneValue = renderer.TryGetValue("parent_bone", out var declaredParent) ? declaredParent : null;
            if (parentBoneValue != null && !(parentBoneValue is string))
            { problem = "static row parent_bone must be a string"; return false; }
            var parentBone = parentBoneValue as string;
            if (!string.IsNullOrEmpty(parentBone) &&
                !string.Equals(parts[parts.Length - 2], parentBone, StringComparison.Ordinal))
            { problem = "static row parent_bone does not match its path"; return false; }
            var current = root;
            for (var i = 0; i < parts.Length - 1; i++)
            {
                var next = current.Find(parts[i]);
                if (next == null) { problem = "static row path is unresolved at " + parts[i]; return false; }
                current = next;
            }
            if (!string.IsNullOrEmpty(parentBone))
            {
                var declared = FindChildByName(root, parentBone);
                if (declared == null) { problem = "static row parent bone is unresolved: " + parentBone; return false; }
                if (!ReferenceEquals(declared, current))
                { problem = "static row parent_bone disagrees with its path"; return false; }
            }
            parent = current;
            return true;
        }

        private static void ApplyLocalTransform(Transform transform, Dictionary<string, object> renderer)
        {
            if (TryVector3(renderer, "local_position", out var position)) transform.localPosition = position;
            if (TryVector4(renderer, "local_rotation", out var rotation))
                transform.localRotation = new Quaternion(rotation.x, rotation.y, rotation.z, rotation.w);
            if (TryVector3(renderer, "local_scale", out var scale)) transform.localScale = scale;
            else transform.localScale = Vector3.one;
        }

        private static Mesh BuildMesh(Dictionary<string, object> renderer, out int vertexCount)
        {
            vertexCount = 0;
            if (!TryVector3List(renderer, "vertices", out var vertices) || vertices.Count == 0) return null;
            var mesh = new Mesh { name = (renderer.TryGetValue("mesh_name", out var name) ? name as string : null) ?? "SlimeBridgeImportedMesh" };
            if (vertices.Count > 65000) mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
            mesh.SetVertices(vertices);
            if (TryVector3List(renderer, "normals", out var normals) && normals.Count == vertices.Count) mesh.SetNormals(normals);
            // The SR2 body shader samples its colour mask / stripe / baked albedo with the unique 0..1 atlas set
            // ("uv2"); UV0 is the tiling set. The cache names the set the albedo textures were authored for.
            var uvKey = renderer.TryGetValue("albedo_uv_set", out var uvSet) && uvSet as string == "uv2" ? "uv2" : "uv";
            if (TryVector2List(renderer, uvKey, out var uv) && uv.Count == vertices.Count) mesh.SetUVs(0, uv);
            else if (uvKey != "uv" && TryVector2List(renderer, "uv", out var uv0) && uv0.Count == vertices.Count) mesh.SetUVs(0, uv0);
            if (TryVector4List(renderer, "tangents", out var tangents) && tangents.Count == vertices.Count) mesh.SetTangents(tangents);
            if (TryColorList(renderer, "colors", out var colors) && colors.Count == vertices.Count) mesh.SetColors(colors);
            var subMeshes = renderer.TryGetValue("triangles", out var t) ? t as List<object> : null;
            if (subMeshes == null || subMeshes.Count == 0) return null;
            mesh.subMeshCount = subMeshes.Count;
            for (var i = 0; i < subMeshes.Count; i++)
            {
                if (!(subMeshes[i] is List<object> rows)) return null;
                var indices = new List<int>();
                foreach (var row in rows)
                {
                    if (!TryInt(row, out var index)) return null;
                    if (index < 0 || index >= vertices.Count) return null;
                    indices.Add(index);
                }
                if (indices.Count < 3) return null;
                mesh.SetTriangles(indices, i);
            }
            mesh.RecalculateBounds();
            vertexCount = vertices.Count;
            return mesh;
        }

        private static readonly string[] MainTexturePreference =
            { "_MainTex", "_BaseMap", "_FaceAtlas", "_Diffuse" };

        private static readonly string[] ColorPreference =
            { "_MiddleColor", "_TopColor", "_BottomColor", "_BaseColor", "_Color" };

        private static bool TryBuildMaterials(string directory, Dictionary<string, object> renderer,
            HashSet<string> verifiedFiles, out Material[] materials, out List<UnityEngine.Object> sourceOwned, out string problem)
        {
            materials = null;
            sourceOwned = new List<UnityEngine.Object>();
            problem = "";
            var rows = renderer.TryGetValue("materials", out var m) ? m as List<object> : null;
            var sourceFace = false;
            if (renderer.TryGetValue("source_face", out var faceFlag))
            {
                if (!(faceFlag is bool)) { problem = "source_face flag must be boolean"; return false; }
                sourceFace = (bool)faceFlag;
            }
            if (sourceFace)
            {
                if (rows == null) { problem = "source face materials are missing"; return false; }
                return SourceFaceMaterials.TryBuild(rows, file =>
                {
                    if (!IsSafeFileName(file) || !verifiedFiles.Contains(file)) return null;
                    var texturePath = Path.Combine(directory, file);
                    if (!LabSpecPathGuard.IsAllowedFile(directory, texturePath, out _)) return null;
                    return LoadTexture(texturePath);
                }, out materials, out sourceOwned, out problem);
            }
            var built = new List<Material>();
            if (rows != null)
            {
                foreach (var row in rows)
                {
                    if (!(row is Dictionary<string, object> materialRow))
                    { problem = "materials array contains a malformed row"; return false; }
                    if (!TryBuildMaterialEntry(directory, materialRow, verifiedFiles, out var material, out var entryProblem))
                    { problem = entryProblem; return false; }
                    built.Add(material);
                }
            }
            if (built.Count == 0) { problem = "renderer has no material slots"; return false; }
            // Preserve authored surplus passes: Unity draws those on the final submesh.
            // Padding a short array retains the existing repeat-last-slot policy.
            var submeshCount = 1;
            if (renderer.TryGetValue("triangles", out var t) && t is List<object> submeshRows && submeshRows.Count > 0)
                submeshCount = submeshRows.Count;
            while (built.Count < submeshCount) built.Add(built[built.Count - 1]);
            materials = built.ToArray();
            return true;
        }

        private static bool TryBuildMaterialEntry(string directory, Dictionary<string, object> entry,
            HashSet<string> verifiedFiles, out Material material, out string problem)
        {
            material = null;
            problem = "";
            var result = new Material(Shader.Find("Standard"));
            Texture2D displayTexture = null;
            var propertyTextures = new Dictionary<string, Texture2D>(StringComparer.Ordinal);
            List<object> textures = null;
            if (entry.TryGetValue("textures", out var texRows))
            {
                textures = texRows as List<object>;
                if (textures == null)
                { problem = "textures field has the wrong shape"; UnityEngine.Object.Destroy(result); return false; }
            }
            if (textures != null)
            {
                foreach (var row in textures)
                {
                    if (!(row is Dictionary<string, object> textureRow))
                    { problem = "textures array contains a malformed row"; UnityEngine.Object.Destroy(result); return false; }
                    if (textureRow.ContainsKey("error"))
                    {
                        // A producer that recorded a failed export must not yield an applied model:
                        // the visual would silently lose that texture while the placeholder is gone.
                        problem = "material references a failed texture export";
                        UnityEngine.Object.Destroy(result);
                        return false;
                    }
                    var file = textureRow.TryGetValue("file", out var f) ? f as string : null;
                    if (string.IsNullOrEmpty(file) || !IsSafeFileName(file))
                    { problem = "texture reference is unsafe"; UnityEngine.Object.Destroy(result); return false; }
                    if (!verifiedFiles.Contains(file))
                    { problem = "texture is not covered by the verified manifest: " + file; UnityEngine.Object.Destroy(result); return false; }
                    var texturePath = Path.Combine(directory, file);
                    if (!LabSpecPathGuard.IsAllowedFile(directory, texturePath, out var fileReason))
                    { problem = "texture refused: " + fileReason; UnityEngine.Object.Destroy(result); return false; }
                    var loaded = LoadTexture(texturePath);
                    if (loaded == null)
                    { problem = "texture failed to decode: " + file; UnityEngine.Object.Destroy(result); return false; }
                    var property = textureRow.TryGetValue("property", out var prop) ? prop as string : "_MainTex";
                    if (string.IsNullOrEmpty(property)) continue;
                    propertyTextures[property] = loaded;
                    result.SetTexture(property, loaded);
                }
            }
            foreach (var property in MainTexturePreference)
            {
                if (propertyTextures.TryGetValue(property, out var candidate)) { displayTexture = candidate; break; }
            }
            if (displayTexture != null) result.mainTexture = displayTexture;
            var color = Color.white;
            if (displayTexture == null && entry.TryGetValue("colors", out var colorRows) && colorRows is Dictionary<string, object> colors)
            {
                // Flat fallback when no baked albedo exists: blend the source three-way palette
                // (top/middle/bottom) so mask-driven materials never render colorless.
                var blended = new Color(0f, 0f, 0f, 1f);
                var weightSum = 0f;
                if (TryColor(colors, "_TopColor", out var top)) { blended += top * 0.5f; weightSum += 0.5f; }
                if (TryColor(colors, "_MiddleColor", out var middle)) { blended += middle * 0.3f; weightSum += 0.3f; }
                if (TryColor(colors, "_BottomColor", out var bottom)) { blended += bottom * 0.2f; weightSum += 0.2f; }
                if (weightSum > 0f)
                {
                    color = new Color(blended.r / weightSum, blended.g / weightSum, blended.b / weightSum, 1f);
                }
                else
                {
                    foreach (var property in ColorPreference)
                    {
                        if (TryColor(colors, property, out var parsed)) { color = parsed; break; }
                    }
                }
            }
            result.color = color;
            result.name = "SlimeBridgeImportedMaterial";
            material = result;
            return true;
        }

        private static Texture2D LoadTexture(string path)
        {
            Texture2D? texture = null;
            var complete = false;
            try
            {
                if (!File.Exists(path)) return null;
                texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (!texture.LoadImage(File.ReadAllBytes(path))) return null;
                texture.name = Path.GetFileNameWithoutExtension(path);
                // Repeat wrapping: the source meshes tile across the atlas (the Ringtail body
                // spans u[-0.5, 1.5]); clamping would smear the border pixels across half the
                // body surface.
                texture.wrapMode = TextureWrapMode.Repeat;
                complete = true;
                return texture;
            }
            catch (Exception)
            {
                return null;
            }
            finally
            {
                if (!complete && texture != null) UnityEngine.Object.Destroy(texture);
            }
        }

        private static string SanitizeName(string path)
        {
            var last = path.Split('/').Last();
            return string.IsNullOrEmpty(last) ? "SlimeBridgeImportedMesh" : last;
        }

        private static bool IsFinite(Vector3 value) =>
            !float.IsNaN(value.x) && !float.IsInfinity(value.x) &&
            !float.IsNaN(value.y) && !float.IsInfinity(value.y) &&
            !float.IsNaN(value.z) && !float.IsInfinity(value.z);

        private static bool TryVector3(Dictionary<string, object> row, string key, out Vector3 value)
        {
            value = default;
            var array = row.TryGetValue(key, out var a) ? a as List<object> : null;
            if (array == null || array.Count < 3) return false;
            if (!TryFloat(array[0], out var x) || !TryFloat(array[1], out var y) || !TryFloat(array[2], out var z)) return false;
            value = new Vector3(x, y, z);
            return true;
        }

        private static bool TryVector4(Dictionary<string, object> row, string key, out Vector4 value)
        {
            value = default;
            var array = row.TryGetValue(key, out var a) ? a as List<object> : null;
            if (array == null || array.Count < 4) return false;
            if (!TryFloat(array[0], out var x) || !TryFloat(array[1], out var y) ||
                !TryFloat(array[2], out var z) || !TryFloat(array[3], out var w)) return false;
            value = new Vector4(x, y, z, w);
            return true;
        }

        private static bool TryVector3List(Dictionary<string, object> row, string key, out List<Vector3> values)
        {
            values = new List<Vector3>();
            var array = row.TryGetValue(key, out var a) ? a as List<object> : null;
            if (array == null) return false;
            foreach (var entry in array)
            {
                if (!(entry is List<object> triple) || triple.Count < 3) return false;
                if (!TryFloat(triple[0], out var x) || !TryFloat(triple[1], out var y) || !TryFloat(triple[2], out var z)) return false;
                values.Add(new Vector3(x, y, z));
            }
            return true;
        }

        private static bool TryVector2List(Dictionary<string, object> row, string key, out List<Vector2> values)
        {
            values = new List<Vector2>();
            var array = row.TryGetValue(key, out var a) ? a as List<object> : null;
            if (array == null) return false;
            foreach (var entry in array)
            {
                if (!(entry is List<object> pair) || pair.Count < 2) return false;
                if (!TryFloat(pair[0], out var x) || !TryFloat(pair[1], out var y)) return false;
                values.Add(new Vector2(x, y));
            }
            return true;
        }

        private static bool TryVector4List(Dictionary<string, object> row, string key, out List<Vector4> values)
        {
            values = new List<Vector4>();
            var array = row.TryGetValue(key, out var a) ? a as List<object> : null;
            if (array == null) return false;
            foreach (var entry in array)
            {
                if (!(entry is List<object> quad) || quad.Count < 4) return false;
                if (!TryFloat(quad[0], out var x) || !TryFloat(quad[1], out var y) ||
                    !TryFloat(quad[2], out var z) || !TryFloat(quad[3], out var w)) return false;
                values.Add(new Vector4(x, y, z, w));
            }
            return true;
        }

        /// <summary>Bind a part's mesh to the source skeleton when the portable document carries
        /// skin data (skin_bones + bind_poses + bone_weights). Fails closed to the static
        /// MeshRenderer path when any piece is missing or out of range.</summary>
        private static bool TryBindSkin(Dictionary<string, object> renderer, Mesh mesh, Transform root,
            out Transform[] bones)
        {
            bones = null;
            var names = renderer.TryGetValue("skin_bones", out var n) ? n as List<object> : null;
            var poses = renderer.TryGetValue("bind_poses", out var p) ? p as List<object> : null;
            var weights = renderer.TryGetValue("bone_weights", out var w) ? w as List<object> : null;
            if (names == null || poses == null || weights == null || names.Count == 0 || poses.Count != names.Count) return false;
            if (weights.Count != mesh.vertexCount) return false;
            var boneList = new Transform[names.Count];
            for (var i = 0; i < names.Count; i++)
            {
                var boneName = names[i] as string;
                if (string.IsNullOrEmpty(boneName)) return false;
                boneList[i] = FindChildByName(root, boneName);
                if (boneList[i] == null) return false;
            }
            var poseArray = new Matrix4x4[poses.Count];
            for (var i = 0; i < poses.Count; i++)
            {
                if (!(poses[i] is List<object> row) || row.Count != 16) return false;
                var m = new Matrix4x4();
                for (var r = 0; r < 4; r++)
                {
                    for (var c = 0; c < 4; c++)
                    {
                        if (!TryFiniteFloat(row[r * 4 + c], out var value)) return false;
                        m[r, c] = value;
                    }
                }
                poseArray[i] = m;
            }
            var boneWeights = new BoneWeight[weights.Count];
            for (var i = 0; i < weights.Count; i++)
            {
                if (!(weights[i] is List<object> row) || row.Count != 8) return false;
                if (!TryFiniteFloat(row[0], out var w0) || !TryFiniteFloat(row[1], out var w1) || !TryFiniteFloat(row[2], out var w2) || !TryFiniteFloat(row[3], out var w3) ||
                    !TryInt(row[4], out var i0) || !TryInt(row[5], out var i1) || !TryInt(row[6], out var i2) || !TryInt(row[7], out var i3)) return false;
                var weight = new BoneWeight
                {
                    weight0 = w0, weight1 = w1, weight2 = w2, weight3 = w3,
                    boneIndex0 = i0, boneIndex1 = i1, boneIndex2 = i2, boneIndex3 = i3,
                };
                if (weight.boneIndex0 < 0 || weight.boneIndex0 >= boneList.Length ||
                    weight.boneIndex1 < 0 || weight.boneIndex1 >= boneList.Length ||
                    weight.boneIndex2 < 0 || weight.boneIndex2 >= boneList.Length ||
                    weight.boneIndex3 < 0 || weight.boneIndex3 >= boneList.Length) return false;
                boneWeights[i] = weight;
            }
            mesh.boneWeights = boneWeights;
            mesh.bindposes = poseArray;
            bones = boneList;
            return true;
        }

        /// <summary>Bounded breadth-first search for a named transform under the model root.</summary>
        private static Transform FindChildByName(Transform root, string name)
        {
            if (root == null) return null;
            const int budget = 4096;
            var visited = 0;
            var stack = new Stack<Transform>();
            stack.Push(root);
            while (stack.Count > 0)
            {
                if (visited++ >= budget) return null;
                var current = stack.Pop();
                if (current == null) continue;
                if (string.Equals(current.gameObject.name, name, StringComparison.Ordinal)) return current;
                for (var childIndex = 0; childIndex < current.childCount; childIndex++) stack.Push(current.GetChild(childIndex));
            }
            return null;
        }

        /// <summary>Rebuild the source bone hierarchy from the portable document (names, parents,
        /// local TRS) under the model root so skinned parts can bind to it. The source order is
        /// authoritative: a parent index that is not an earlier built row (negative beyond the
        /// root marker -1, forward or not-yet-built) refuses the whole document instead of
        /// silently flattening the bone to the model root, which would degrade every pose.</summary>
        private static bool BuildSkeleton(Transform root, Dictionary<string, object> document, out string problem)
        {
            problem = "";
            if (!document.TryGetValue("skeleton", out var skeletonValue) || !(skeletonValue is Dictionary<string, object> skeleton)) return true;
            if (!skeleton.TryGetValue("bones", out var bonesValue) || !(bonesValue is List<object> boneRows)) return true;
            const int budget = 4096;
            var transforms = new Transform[boneRows.Count];
            for (var i = 0; i < boneRows.Count && i < budget; i++)
            {
                if (!(boneRows[i] is Dictionary<string, object> bone)) continue;
                var name = bone.TryGetValue("name", out var n) ? n as string : null;
                var go = new GameObject(string.IsNullOrEmpty(name) ? "bone" : name);
                var boneTransform = go.transform;
                var parentIndex = TryInt(bone.TryGetValue("parent", out var p) ? p : null, out var parsedParent) ? parsedParent : -1;
                if (parentIndex >= i || parentIndex < -1 || (parentIndex >= 0 && transforms[parentIndex] == null))
                {
                    problem = "skeleton bone " + i + " has an unresolved parent index";
                    UnityEngine.Object.Destroy(go);
                    return false;
                }
                boneTransform.SetParent(parentIndex >= 0 ? transforms[parentIndex] : root, false);
                if (bone.TryGetValue("p", out var positionValue)) boneTransform.localPosition = ListVector3(positionValue, Vector3.zero);
                if (bone.TryGetValue("q", out var rotationValue)) boneTransform.localRotation = ListQuaternion(rotationValue);
                if (bone.TryGetValue("s", out var scaleValue)) boneTransform.localScale = ListVector3(scaleValue, Vector3.one);
                transforms[i] = boneTransform;
            }
            return true;
        }

        private static Vector3 ListVector3(object value, Vector3 fallback)
        {
            if (value is List<object> list && list.Count >= 3 &&
                TryFloat(list[0], out var x) && TryFloat(list[1], out var y) && TryFloat(list[2], out var z))
            {
                return new Vector3(x, y, z);
            }
            return fallback;
        }

        private static Quaternion ListQuaternion(object value)
        {
            if (value is List<object> list && list.Count >= 4 &&
                TryFloat(list[0], out var x) && TryFloat(list[1], out var y) && TryFloat(list[2], out var z) && TryFloat(list[3], out var w))
            {
                return new Quaternion(x, y, z, w);
            }
            return Quaternion.identity;
        }

        private static bool TryColorList(Dictionary<string, object> row, string key, out List<Color> values)
        {
            values = new List<Color>();
            var array = row.TryGetValue(key, out var a) ? a as List<object> : null;
            if (array == null) return false;
            foreach (var entry in array)
            {
                if (!(entry is List<object> quad) || quad.Count < 4) return false;
                if (!TryFloat(quad[0], out var r) || !TryFloat(quad[1], out var g) ||
                    !TryFloat(quad[2], out var b) || !TryFloat(quad[3], out var a2)) return false;
                values.Add(new Color(r, g, b, a2));
            }
            return true;
        }

        private static bool TryColor(Dictionary<string, object> colors, string key, out Color value)
        {
            value = default;
            if (!colors.TryGetValue(key, out var raw)) return false;
            if (!(raw is List<object> quad) || quad.Count < 3) return false;
            if (!TryFloat(quad[0], out var r) || !TryFloat(quad[1], out var g) || !TryFloat(quad[2], out var b)) return false;
            var a = 1f;
            if (quad.Count > 3) TryFloat(quad[3], out a);
            value = new Color(r, g, b, a);
            return true;
        }

        private static bool TryFloat(object raw, out float value)
        {
            switch (raw)
            {
                case double d: value = (float)d; return true;
                case float f: value = f; return true;
                case long l: value = l; return true;
                case int i: value = i; return true;
                case string s when float.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed):
                    value = parsed; return true;
                default: value = 0f; return false;
            }
        }

        private static bool TryInt(object raw, out int value)
        {
            switch (raw)
            {
                case long l when l >= int.MinValue && l <= int.MaxValue: value = (int)l; return true;
                case int i: value = i; return true;
                case double d when !double.IsNaN(d) && !double.IsInfinity(d) &&
                                   d >= int.MinValue && d <= int.MaxValue && d == Math.Floor(d):
                    value = (int)d; return true;
                case string s when int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed):
                    value = parsed; return true;
                default: value = 0; return false;
            }
        }

        /// <summary>Strict skin-path float: finite only (no NaN/Infinity), so malformed skin or
        /// bind-pose numbers fail closed to the static renderer fallback instead of binding
        /// corrupt skinning data.</summary>
        private static bool TryFiniteFloat(object raw, out float value)
        {
            if (!TryFloat(raw, out value)) return false;
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static string Sha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(stream);
                var sb = new StringBuilder(64);
                foreach (var b in digest) sb.Append(b.ToString("x2"));
                return sb.ToString();
            }
        }
    }
}
