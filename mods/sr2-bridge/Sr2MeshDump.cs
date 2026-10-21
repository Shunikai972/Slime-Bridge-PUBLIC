using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using MelonLoader;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Local native-asset capture for the SlimeBridge asset bridge (SR2 → SR1 v1).
    ///
    /// When SLIMEBRIDGE_MESH_DUMP names a slime (reference id or name fragment) and
    /// SLIMEBRIDGE_DUMP_OUT points at a Bridge-owned LAB directory, this dumps the ACTUAL Ringtail
    /// (or any named species) visual assets from the user's local installation: mesh geometry
    /// (vertices/normals/tangents/uv/colours/triangles/submeshes), the renderer hierarchy with
    /// local transforms, and the body material textures as PNGs. Output stays LOCAL (private
    /// ignored cache); only code and hashes are ever committed.
    /// </summary>
    internal static class Sr2MeshDump
    {
        private enum Phase { Idle, WaitingForAssembly, Done }

        private static Phase _phase = Phase.Idle;
        private static float _settleSince = -1f;
        private static float _spawnTime = -1f;
        private static GameObject _target;
        private static bool _warnedGuard;

        internal static bool Requested => !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SLIMEBRIDGE_MESH_DUMP"));

        internal static void Tick(bool labExecutable)
        {
            if (!Requested || _phase == Phase.Done) return;
            if (!labExecutable)
            {
                if (!_warnedGuard)
                {
                    _warnedGuard = true;
                    MelonLogger.Warning("[SlimeBridge] mesh dump refused: not the authorized LAB executable");
                }
                return;
            }
            if (!WorldReady()) { _settleSince = -1f; return; }
            var now = Time.realtimeSinceStartup;
            if (_phase == Phase.Idle)
            {
                if (_settleSince < 0f) { _settleSince = now; return; }
                if (now - _settleSince < 3f) return;
                _target = SpawnTarget();
                if (_target == null) { _phase = Phase.Done; return; }
                _spawnTime = now;
                _phase = Phase.WaitingForAssembly;
                return;
            }
            if (_phase == Phase.WaitingForAssembly)
            {
                if (_target == null) { _phase = Phase.Done; return; }
                if (now - _spawnTime < 2f) return;
                try
                {
                    Run(_target);
                }
                catch (Exception ex)
                {
                    MelonLogger.Error("[SlimeBridge] mesh dump failed: " + ex);
                }
                finally
                {
                    try { UnityEngine.Object.Destroy(_target); }
                    catch (Exception) { }
                    _target = null;
                    _phase = Phase.Done;
                }
            }
        }

        private static bool WorldReady()
        {
            try
            {
                var game = Il2Cpp.GameContext.Instance;
                var scene = Il2Cpp.SceneContext.Instance;
                return game != null && scene != null && scene.Player != null && scene.Player.activeInHierarchy;
            }
            catch (Exception) { return false; }
        }

        private static GameObject SpawnTarget()
        {
            try
            {
                var target = Environment.GetEnvironmentVariable("SLIMEBRIDGE_MESH_DUMP") ?? "";
                var def = FindSpecies(target);
                if (def == null) { MelonLogger.Error("[SlimeBridge] mesh dump: no species matches '" + target + "'"); return null; }
                if (def.prefab == null) { MelonLogger.Error("[SlimeBridge] mesh dump: species has no prefab"); return null; }
                var player = Il2Cpp.SceneContext.Instance.Player.transform;
                var position = player.position + player.forward * 3f + Vector3.up * 1.2f;
                var instance = UnityEngine.Object.Instantiate(def.prefab, position, Quaternion.identity);
                instance.name = "SlimeBridgeDumpTarget";
                MelonLogger.Msg("[SlimeBridge] mesh dump target spawned: " + def.referenceId + " at " + position);
                return instance;
            }
            catch (Exception ex)
            {
                MelonLogger.Error("[SlimeBridge] mesh dump spawn failed: " + ex.GetType().Name + ": " + ex.Message);
                return null;
            }
        }

        private static void Run(GameObject instance)
        {
            var target = Environment.GetEnvironmentVariable("SLIMEBRIDGE_MESH_DUMP") ?? "";
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_DUMP_OUT") ?? "";
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (repoRoot == null) { MelonLogger.Error("[SlimeBridge] mesh dump refused: repo root not found"); return; }
            if (!LabSpecPathGuard.IsAllowedDirectory(Path.Combine(repoRoot, "Lab"), outDir, out var guardReason))
            {
                MelonLogger.Error("[SlimeBridge] mesh dump refused: " + guardReason);
                return;
            }
            Directory.CreateDirectory(outDir);

            var def = FindSpecies(target);
            var visualRoot = ResolveVisualRoot(instance);
            var root = instance.transform;

            var sb = new StringBuilder(1 << 22);
            sb.Append("{\n  \"schema\": \"bridge-native-mesh\", \"schema_version\": 2");
            sb.Append(",\n  \"source_game\": \"SR2\"");
            sb.Append(",\n  \"reference_id\": ").Append(RegistrySnapshotFormat.Quote(def != null ? def.referenceId ?? "" : ""));
            sb.Append(",\n  \"display_name\": ").Append(RegistrySnapshotFormat.Quote(def != null ? def.name ?? "" : ""));
            sb.Append(",\n  \"prefab_name\": ").Append(RegistrySnapshotFormat.Quote(def != null && def.prefab != null ? def.prefab.name : ""));
            sb.Append(",\n  \"prefab_scale\": ").Append((def != null ? def.PrefabScale : 1f).ToString("R", CultureInfo.InvariantCulture));
            sb.Append(",\n  \"utc\": ").Append(RegistrySnapshotFormat.Quote(DateTime.UtcNow.ToString("o")));
            sb.Append(",\n  \"visual_root\": ").Append(RegistrySnapshotFormat.Quote(visualRoot != null ? PathOf(visualRoot, root) : ""));
            sb.Append(",\n  \"skeleton\": ");
            AppendSkeleton(sb, instance);

            var textureFiles = new List<string>();
            var renderers = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true)
                .Where(r => r != null && r.sharedMesh != null).ToArray();
            var meshRenderers = instance.GetComponentsInChildren<MeshRenderer>(true)
                .Where(r => r != null && r.GetComponent<MeshFilter>() != null && r.GetComponent<MeshFilter>().sharedMesh != null)
                .ToArray();
            sb.Append(",\n  \"skinned_renderers\": [");
            for (var i = 0; i < renderers.Length; i++)
            {
                sb.Append(i == 0 ? "\n" : ",\n");
                AppendSkinnedRenderer(sb, renderers[i], root, outDir, textureFiles);
            }
            sb.Append(renderers.Length == 0 ? "]" : "\n  ]");
            sb.Append(",\n  \"static_renderers\": [");
            for (var i = 0; i < meshRenderers.Length; i++)
            {
                sb.Append(i == 0 ? "\n" : ",\n");
                AppendStaticRenderer(sb, meshRenderers[i], root, outDir, textureFiles);
            }
            sb.Append(meshRenderers.Length == 0 ? "]" : "\n  ]");
            sb.Append("\n}\n");

            var modelPath = Path.Combine(outDir, "model.json");
            File.WriteAllText(modelPath, sb.ToString(), new UTF8Encoding(false));
            var receipt = new StringBuilder(1024);
            receipt.Append("{\n  \"schema\": \"bridge-native-mesh-receipt\", \"schema_version\": 2");
            receipt.Append(",\n  \"target\": ").Append(RegistrySnapshotFormat.Quote(target));
            receipt.Append(",\n  \"reference_id\": ").Append(RegistrySnapshotFormat.Quote(def != null ? def.referenceId ?? "" : ""));
            receipt.Append(",\n  \"skinned_renderers\": ").Append(renderers.Length.ToString(CultureInfo.InvariantCulture));
            receipt.Append(",\n  \"static_renderers\": ").Append(meshRenderers.Length.ToString(CultureInfo.InvariantCulture));
            receipt.Append(",\n  \"model\": ").Append(RegistrySnapshotFormat.Quote(Path.GetFileName(modelPath)));
            receipt.Append(",\n  \"model_sha256\": ").Append(RegistrySnapshotFormat.Quote(Sha256(modelPath)));
            receipt.Append(",\n  \"textures\": [");
            for (var i = 0; i < textureFiles.Count; i++)
            {
                if (i > 0) receipt.Append(", ");
                receipt.Append("{\"file\": ").Append(RegistrySnapshotFormat.Quote(Path.GetFileName(textureFiles[i])))
                       .Append(", \"sha256\": ").Append(RegistrySnapshotFormat.Quote(Sha256(textureFiles[i]))).Append("}");
            }
            receipt.Append("]\n}\n");
            File.WriteAllText(Path.Combine(outDir, "dump-receipt.json"), receipt.ToString(), new UTF8Encoding(false));
            MelonLogger.Msg("[SlimeBridge] mesh dump complete: " + (def != null ? def.referenceId : target) + " -> " + outDir +
                            " (skinned " + renderers.Length + ", static " + meshRenderers.Length + ", textures " + textureFiles.Count + ")");
        }

        private static Transform ResolveVisualRoot(GameObject instance)
        {
            try
            {
                var applicator = instance.GetComponent<Il2Cpp.SlimeAppearanceApplicator>();
                if (applicator != null && applicator.RootAppearanceObject != null)
                    return applicator.RootAppearanceObject.transform;
            }
            catch (Exception) { }
            return null;
        }

        private static void AppendSkeleton(StringBuilder sb, GameObject instance)
        {
            var bones = new Dictionary<string, Transform>(StringComparer.Ordinal);
            foreach (var skinned in instance.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                var array = skinned != null ? skinned.bones : null;
                if (array == null) continue;
                foreach (var bone in array)
                {
                    if (bone == null) continue;
                    var path = PathOf(bone, instance.transform);
                    if (!bones.ContainsKey(path)) bones[path] = bone;
                }
            }
            var rows = new List<string>();
            foreach (var pair in bones)
            {
                var bone = pair.Value;
                rows.Add("{\"path\": " + RegistrySnapshotFormat.Quote(pair.Key) +
                         ", \"local_position\": " + Vector3Json(bone.localPosition) +
                         ", \"local_rotation\": " + QuaternionJson(bone.localRotation) +
                         ", \"local_scale\": " + Vector3Json(bone.localScale) + "}");
            }
            sb.Append("{\"bone_count\": ").Append(bones.Count.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"bones\": [");
            sb.Append(rows.Count == 0 ? "]" : "\n    " + string.Join(",\n    ", rows) + "\n  ]}");
        }

        private static Il2Cpp.SlimeDefinition FindSpecies(string target)
        {
            var definitions = Il2Cpp.GameContext.Instance.SlimeDefinitions;
            var slimes = definitions.Slimes;
            Il2Cpp.SlimeDefinition fallback = null;
            for (var i = 0; i < slimes.Length; i++)
            {
                var def = slimes[i];
                if (def == null) continue;
                var reference = def.referenceId ?? "";
                var name = def.name ?? "";
                if (string.Equals(reference, target, StringComparison.OrdinalIgnoreCase)) return def;
                if (name.IndexOf(target, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    if (string.Equals(reference, "SlimeDefinition." + target, StringComparison.OrdinalIgnoreCase)) return def;
                    fallback ??= def;
                }
            }
            return fallback;
        }

        private static void AppendTransform(StringBuilder sb, Transform transform, Transform root)
        {
            var path = PathOf(transform, root);
            sb.Append(", \"path\": ").Append(RegistrySnapshotFormat.Quote(path));
            sb.Append(", \"local_position\": ").Append(Vector3Json(transform.localPosition));
            sb.Append(", \"local_rotation\": ").Append(QuaternionJson(transform.localRotation));
            sb.Append(", \"local_scale\": ").Append(Vector3Json(transform.localScale));
        }

        private static string PathOf(Transform transform, Transform root)
        {
            var parts = new List<string>();
            var current = transform;
            while (current != null)
            {
                parts.Add(current.name);
                if (current == root) break;
                current = current.parent;
            }
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static void AppendStaticRenderer(StringBuilder sb, MeshRenderer renderer, Transform root,
            string outDir, List<string> textureFiles)
        {
            sb.Append("{\"enabled\": ").Append(renderer.enabled ? "true" : "false");
            AppendTransform(sb, renderer.transform, root);
            AppendMeshData(sb, renderer.GetComponent<MeshFilter>().sharedMesh);
            AppendMaterials(sb, renderer.sharedMaterials, outDir, textureFiles);
            sb.Append("}");
        }

        private static void AppendSkinnedRenderer(StringBuilder sb, SkinnedMeshRenderer renderer, Transform root,
            string outDir, List<string> textureFiles)
        {
            sb.Append("{\"enabled\": ").Append(renderer.enabled ? "true" : "false");
            AppendTransform(sb, renderer.transform, root);
            if (renderer.sharedMesh != null) AppendMeshData(sb, renderer.sharedMesh);
            sb.Append(", \"root_bone\": ").Append(RegistrySnapshotFormat.Quote(renderer.rootBone != null ? PathOf(renderer.rootBone, root) : ""));
            var bones = renderer.bones;
            sb.Append(", \"bone_count\": ").Append(bones != null ? bones.Length.ToString(CultureInfo.InvariantCulture) : "0");
            AppendMaterials(sb, renderer.sharedMaterials, outDir, textureFiles);
            sb.Append("}");
        }

        private static void AppendMeshData(StringBuilder sb, Mesh mesh)
        {
            sb.Append(", \"mesh_name\": ").Append(RegistrySnapshotFormat.Quote(mesh.name));
            sb.Append(", \"vertices\": ").Append(Vector3ArrayJson(mesh.vertices));
            var normals = mesh.normals;
            if (normals != null && normals.Length > 0) sb.Append(", \"normals\": ").Append(Vector3ArrayJson(normals));
            var tangents = mesh.tangents;
            if (tangents != null && tangents.Length > 0) sb.Append(", \"tangents\": ").Append(Vector4ArrayJson(tangents));
            var uv = mesh.uv;
            if (uv != null && uv.Length > 0) sb.Append(", \"uv\": ").Append(Vector2ArrayJson(uv));
            var colors = mesh.colors;
            if (colors != null && colors.Length > 0) sb.Append(", \"colors\": ").Append(ColorArrayJson(colors));
            sb.Append(", \"submesh_count\": ").Append(mesh.subMeshCount.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"triangles\": [");
            for (var s = 0; s < mesh.subMeshCount; s++)
            {
                if (s > 0) sb.Append(", ");
                var triangles = mesh.GetTriangles(s);
                sb.Append("[");
                for (var t = 0; t < triangles.Length; t++)
                {
                    if (t > 0) sb.Append(", ");
                    sb.Append(triangles[t].ToString(CultureInfo.InvariantCulture));
                }
                sb.Append("]");
            }
            sb.Append("]");
            sb.Append(", \"bounds_center\": ").Append(Vector3Json(mesh.bounds.center));
            sb.Append(", \"bounds_size\": ").Append(Vector3Json(mesh.bounds.size));
        }

        private static IEnumerable<string> TexturePropertyNames(Material material)
        {
            List<string> names = new List<string>();
            try
            {
                var shader = material.shader;
                if (shader == null) return names;
                var count = shader.GetPropertyCount();
                for (var i = 0; i < count; i++)
                {
                    if (shader.GetPropertyType(i) != UnityEngine.Rendering.ShaderPropertyType.Texture) continue;
                    var name = shader.GetPropertyName(i);
                    if (!string.IsNullOrEmpty(name)) names.Add(name);
                }
            }
            catch (Exception)
            {
                // Shader property reflection unavailable: fall back to the allowlist.
            }
            return names;
        }

        private static void AppendMaterials(StringBuilder sb, Material[] materials, string outDir, List<string> textureFiles)
        {
            sb.Append(", \"materials\": [");
            for (var i = 0; i < (materials?.Length ?? 0); i++)
            {
                if (i > 0) sb.Append(", ");
                var material = materials[i];
                if (material == null) { sb.Append("null"); continue; }
                sb.Append("{\"name\": ").Append(RegistrySnapshotFormat.Quote(material.name));
                sb.Append(", \"shader\": ").Append(RegistrySnapshotFormat.Quote(material.shader != null ? material.shader.name : ""));
                sb.Append(", \"colors\": {");
                var colorNames = new[] { "_TopColor", "_MiddleColor", "_BottomColor" };
                var first = true;
                foreach (var property in colorNames)
                {
                    var id = Shader.PropertyToID(property);
                    if (!material.HasProperty(id)) continue;
                    if (!first) sb.Append(", ");
                    first = false;
                    sb.Append(RegistrySnapshotFormat.Quote(property)).Append(": ").Append(ColorJson(material.GetColor(id)));
                }
                sb.Append("}");
                sb.Append(", \"textures\": [");
                var candidates = new List<string>(TexturePropertyNames(material));
                foreach (var known in new[] { "_MainTex", "_BaseMap", "_TopTex", "_MiddleTex", "_BottomTex", "_MaskTex", "_EmissionMap", "_PatternTex", "_Pattern" })
                {
                    if (!candidates.Contains(known) && material.HasProperty(Shader.PropertyToID(known)))
                        candidates.Add(known);
                }
                var emitted = new List<string>();
                foreach (var textureName in candidates)
                {
                    var id = Shader.PropertyToID(textureName);
                    if (!material.HasProperty(id)) continue;
                    var texture = material.GetTexture(id) as Texture2D;
                    if (texture == null) continue;
                    var file = "tex-" + Sanitize(material.name) + "-" + textureName.TrimStart('_') + ".png";
                    try
                    {
                        WriteTexturePng(texture, Path.Combine(outDir, file));
                        textureFiles.Add(Path.Combine(outDir, file));
                        emitted.Add("{\"property\": " + RegistrySnapshotFormat.Quote(textureName) +
                                    ", \"file\": " + RegistrySnapshotFormat.Quote(file) +
                                    ", \"width\": " + texture.width.ToString(CultureInfo.InvariantCulture) +
                                    ", \"height\": " + texture.height.ToString(CultureInfo.InvariantCulture) + "}");
                    }
                    catch (Exception ex)
                    {
                        emitted.Add("{\"property\": " + RegistrySnapshotFormat.Quote(textureName) +
                                    ", \"error\": " + RegistrySnapshotFormat.Quote(ex.GetType().Name) + "}");
                    }
                }
                sb.Append(string.Join(", ", emitted));
                sb.Append("]}");
            }
            sb.Append("]");
        }

        private static void WriteTexturePng(Texture2D texture, string path)
        {
            Texture2D readable = texture;
            RenderTexture temporary = null;
            if (!texture.isReadable)
            {
                temporary = RenderTexture.GetTemporary(texture.width, texture.height, 0);
                Graphics.Blit(texture, temporary);
                var previous = RenderTexture.active;
                RenderTexture.active = temporary;
                readable = new Texture2D(texture.width, texture.height, TextureFormat.RGBA32, false);
                readable.ReadPixels(new Rect(0, 0, texture.width, texture.height), 0, 0);
                readable.Apply();
                RenderTexture.active = previous;
            }
            var png = readable.EncodeToPNG();
            if (temporary != null) RenderTexture.ReleaseTemporary(temporary);
            File.WriteAllBytes(path, png);
        }

        private static string Sanitize(string value) =>
            new string(value.Select(c => char.IsLetterOrDigit(c) || c == '.' || c == '-' ? c : '_').ToArray());

        private static string Sha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = System.Security.Cryptography.SHA256.Create())
            {
                var digest = sha.ComputeHash(stream);
                var sb = new StringBuilder(64);
                foreach (var b in digest) sb.Append(b.ToString("x2"));
                return sb.ToString();
            }
        }

        private static string Vector3Json(Vector3 v) =>
            "[" + v.x.ToString("R", CultureInfo.InvariantCulture) + ", " + v.y.ToString("R", CultureInfo.InvariantCulture) + ", " + v.z.ToString("R", CultureInfo.InvariantCulture) + "]";

        private static string Vector2Json(Vector2 v) =>
            "[" + v.x.ToString("R", CultureInfo.InvariantCulture) + ", " + v.y.ToString("R", CultureInfo.InvariantCulture) + "]";

        private static string Vector4Json(Vector4 v) =>
            "[" + v.x.ToString("R", CultureInfo.InvariantCulture) + ", " + v.y.ToString("R", CultureInfo.InvariantCulture) + ", " + v.z.ToString("R", CultureInfo.InvariantCulture) + ", " + v.w.ToString("R", CultureInfo.InvariantCulture) + "]";

        private static string QuaternionJson(Quaternion q) => Vector4Json(new Vector4(q.x, q.y, q.z, q.w));

        private static string ColorJson(Color c) => Vector4Json(new Vector4(c.r, c.g, c.b, c.a));

        private static string Vector3ArrayJson(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector3> values)
        {
            var sb = new StringBuilder(values.Length * 24 + 16);
            sb.Append('[');
            for (var i = 0; i < values.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(Vector3Json(values[i]));
            }
            sb.Append(']');
            return sb.ToString();
        }

        private static string Vector2ArrayJson(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector2> values)
        {
            var sb = new StringBuilder(values.Length * 20 + 16);
            sb.Append('[');
            for (var i = 0; i < values.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(Vector2Json(values[i]));
            }
            sb.Append(']');
            return sb.ToString();
        }

        private static string Vector4ArrayJson(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Vector4> values)
        {
            var sb = new StringBuilder(values.Length * 28 + 16);
            sb.Append('[');
            for (var i = 0; i < values.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(Vector4Json(values[i]));
            }
            sb.Append(']');
            return sb.ToString();
        }

        private static string ColorArrayJson(Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<Color> values)
        {
            var sb = new StringBuilder(values.Length * 28 + 16);
            sb.Append('[');
            for (var i = 0; i < values.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(ColorJson(values[i]));
            }
            sb.Append(']');
            return sb.ToString();
        }
    }
}
