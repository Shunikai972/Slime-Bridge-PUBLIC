using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Marker on the attached stone-form visual ("StoneVisual"). Lookup by component, never by
    /// name, so the stone visual survives template copies and diagnostics; the live visual keeps
    /// its own ImportedVisualMarker.
    /// </summary>
    internal sealed class ImportedStoneFormMarker : MonoBehaviour
    {
    }

    /// <summary>
    /// Fail-closed loader for the private stone-form cache (bridge-stone-form v1, produced by the
    /// Ringtail stone-form extraction slice). Strictly read-only: schema and version are pinned,
    /// every consumed file must be covered by the extraction receipt with a matching SHA-256,
    /// budgets are bounded and every TRS must be finite with a unit quaternion. Any doubt refuses
    /// the whole entry — "no stone form" means the actor simply never petrifies, never a partial
    /// visual. The only game object this module ever builds is the inactive stone visual template.
    /// </summary>
    internal static class ImportedStoneForm
    {
        internal const string Schema = "bridge-stone-form";
        internal const int SchemaVersion = 1;
        internal const string StoneFormFileName = "stone-form.json";
        internal const string ReceiptFileName = "stone-form-receipt.json";
        internal const string StoneVisualName = "StoneVisual";
        internal const string TemplateName = "SlimeBridgeStoneFormTemplate";
        internal const string MaterialName = "SlimeBridgeStoneFormMaterial";
        internal const string StandardShaderName = "Standard";

        // Bounds mirrored from bridge/content/sr2_stone_form.py (fail-closed budgets).
        internal const int MaxMeshes = 3;
        internal const int MaxVerticesPerMesh = 2048;
        internal const int MaxTrianglesPerMesh = 4096;
        internal const int MaxTextureDimension = 8192;
        // Bounded material adaptation (docs/RINGTAIL_STONE_FORM.md section 2.5): the source
        // render queue is resumed only inside this interval; anything else refuses the entry.
        internal const int MaxRenderQueue = 5000;
        internal const int StandardDefaultRenderQueue = 2000;
        private const int MaxMeshLod = 15;
        private const int MaxNameLength = 128;
        private const float QuaternionUnitTolerance = 0.001f;

        internal sealed class StoneFormModel
        {
            internal string CanonicalId = "";
            internal GameObject Root = null!;        // inactive template, never spawned as-is
            internal int MeshCount;
            internal int VertexTotal;
            internal int TriangleTotal;
            internal int Lod0VertexCount;
            internal int Lod0TriangleCount;
            internal string Lod0MeshName = "";
        }

        private static readonly Dictionary<string, StoneFormModel> Cache = new Dictionary<string, StoneFormModel>(StringComparer.Ordinal);
        private static readonly List<string> Failed = new List<string>();

        internal static bool TryGet(string canonicalId, out StoneFormModel model, out string problem)
        {
            model = null!;
            problem = "";
            if (!IsSafeToken(canonicalId)) { problem = "unsafe canonical id"; Failed.Add(canonicalId); return false; }
            if (Cache.TryGetValue(canonicalId, out var cached)) { model = cached; return true; }
            if (Failed.Contains(canonicalId)) { problem = "stone form cache entry previously failed"; return false; }
            try
            {
                if (TryLoad(canonicalId, out model, out problem)) { Cache[canonicalId] = model; return true; }
            }
            catch (Exception ex)
            {
                problem = ex.GetType().Name + ": " + ex.Message;
            }
            model = null!;
            Failed.Add(canonicalId);
            return false;
        }

        /// <summary>Instantiate the loaded template under the actor root as the inactive
        /// "StoneVisual". Idempotent: an already attached visual is returned unchanged.</summary>
        internal static GameObject? AttachTo(GameObject actorRoot, StoneFormModel model, out string problem)
        {
            problem = "";
            if (actorRoot == null || model == null || model.Root == null)
            {
                problem = "stone form model is not loaded";
                return null;
            }
            var existing = actorRoot.GetComponentInChildren<ImportedStoneFormMarker>(true);
            if (existing != null) return existing.gameObject;
            try
            {
                var visual = UnityEngine.Object.Instantiate(model.Root, actorRoot.transform, false);
                visual.name = StoneVisualName;
                visual.transform.localPosition = model.Root.transform.localPosition;
                visual.transform.localRotation = model.Root.transform.localRotation;
                visual.transform.localScale = model.Root.transform.localScale;
                visual.AddComponent<ImportedStoneFormMarker>();
                SetLayerRecursively(visual, actorRoot.layer);
                visual.SetActive(false);
                return visual;
            }
            catch (Exception ex)
            {
                problem = "stone visual build failed: " + ex.GetType().Name;
                return null;
            }
        }

        private static bool TryLoad(string canonicalId, out StoneFormModel model, out string problem)
        {
            model = null!;
            problem = "";
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (string.IsNullOrEmpty(repoRoot))
            {
                problem = "repository root not found for the stone form cache guard";
                return false;
            }
            var cacheRoot = Path.Combine(repoRoot, "Lab", "AssetCache", "sr2-to-sr1");
            var directory = Path.Combine(cacheRoot, canonicalId);
            if (!LabSpecPathGuard.IsAllowedDirectory(cacheRoot, directory, out var directoryReason))
            {
                problem = "stone form cache directory refused: " + directoryReason;
                return false;
            }
            var documentPath = Path.Combine(directory, StoneFormFileName);
            var receiptPath = Path.Combine(directory, ReceiptFileName);
            if (!LabSpecPathGuard.IsAllowedFile(directory, documentPath, out var documentReason))
            {
                problem = "stone form refused: " + documentReason;
                return false;
            }
            if (!LabSpecPathGuard.IsAllowedFile(directory, receiptPath, out var receiptReason))
            {
                problem = "stone form receipt refused: " + receiptReason;
                return false;
            }
            // The extraction receipt is the trust anchor: it must cover stone-form.json and every
            // texture this document consumes, and each listed file must exist, stay inside the
            // cache directory and match its SHA-256 byte for byte.
            object? receiptRaw;
            try { receiptRaw = MiniJson.Parse(File.ReadAllText(receiptPath)); }
            catch (Exception) { receiptRaw = null; }
            if (!(receiptRaw is Dictionary<string, object?> receipt))
            {
                problem = "stone form receipt is not a JSON object";
                return false;
            }
            if (!string.Equals(receipt.TryGetValue("schema", out var receiptSchema) ? receiptSchema as string : null, Schema, StringComparison.Ordinal))
            {
                problem = "stone form receipt schema mismatch";
                return false;
            }
            if (!TryInt(receipt.TryGetValue("version", out var receiptVersion) ? receiptVersion : null, out var receiptVersionValue) ||
                receiptVersionValue != SchemaVersion)
            {
                problem = "stone form receipt schema version mismatch";
                return false;
            }
            if (!(receipt.TryGetValue("source_read_only", out var readOnlyValue) && readOnlyValue is bool readOnly && readOnly))
            {
                problem = "stone form receipt is not marked read-only";
                return false;
            }
            if (!(receipt.TryGetValue("files", out var filesValue) && filesValue is List<object?> fileRows && fileRows.Count > 0))
            {
                problem = "stone form receipt does not list any files";
                return false;
            }
            var verified = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var row in fileRows)
            {
                if (!(row is Dictionary<string, object?> entry) ||
                    !(entry.TryGetValue("name", out var nameValue) && nameValue is string name && IsSafeFileName(name)))
                {
                    problem = "stone form receipt references an unsafe file name";
                    return false;
                }
                if (!(entry.TryGetValue("sha256", out var hashValue) && hashValue is string hash && IsSha256(hash)))
                {
                    problem = "stone form receipt row lacks a sha256: " + name;
                    return false;
                }
                if (!TryLong(entry.TryGetValue("size", out var sizeValue) ? sizeValue : null, out var size) || size < 0)
                {
                    problem = "stone form receipt row lacks a size: " + name;
                    return false;
                }
                var path = Path.Combine(directory, name);
                if (!LabSpecPathGuard.IsAllowedFile(directory, path, out var fileReason))
                {
                    problem = "stone form cache file refused: " + fileReason;
                    return false;
                }
                if (!File.Exists(path))
                {
                    problem = "stone form cache file missing: " + name;
                    return false;
                }
                if (new FileInfo(path).Length != size)
                {
                    problem = "stone form cache file size mismatch: " + name;
                    return false;
                }
                if (!string.Equals(Sha256(path), hash, StringComparison.OrdinalIgnoreCase))
                {
                    problem = "stone form cache file hash mismatch: " + name;
                    return false;
                }
                verified[name] = hash;
            }
            if (!verified.ContainsKey(StoneFormFileName))
            {
                problem = "stone form receipt does not cover " + StoneFormFileName;
                return false;
            }

            var documentBytes = File.ReadAllBytes(documentPath);
            if (!(MiniJson.Parse(Encoding.UTF8.GetString(documentBytes)) is Dictionary<string, object?> document))
            {
                problem = "stone form document is not a JSON object";
                return false;
            }
            if (!string.Equals(document.TryGetValue("schema", out var schemaValue) ? schemaValue as string : null, Schema, StringComparison.Ordinal))
            {
                problem = "stone form schema mismatch";
                return false;
            }
            if (!TryInt(document.TryGetValue("version", out var versionValue) ? versionValue : null, out var version) || version != SchemaVersion)
            {
                problem = "stone form schema version mismatch";
                return false;
            }
            if (!(document.TryGetValue("root", out var rootValue) && rootValue is Dictionary<string, object?> rootRow))
            {
                problem = "stone form root is missing";
                return false;
            }
            if (!TryTransform(rootRow, "root", out var rootPosition, out var rootRotation, out var rootScale, out var rootProblem))
            {
                problem = "stone form " + rootProblem;
                return false;
            }
            if (!(document.TryGetValue("meshes", out var meshesValue) && meshesValue is List<object?> meshRows && meshRows.Count > 0))
            {
                problem = "stone form has no meshes";
                return false;
            }
            if (meshRows.Count > MaxMeshes)
            {
                problem = "stone form exceeds the mesh budget";
                return false;
            }
            var parsedMeshes = new List<ParsedMesh>();
            ParsedMesh? lod0 = null;
            foreach (var row in meshRows)
            {
                if (!(row is Dictionary<string, object?> meshRow))
                {
                    problem = "stone form mesh row malformed";
                    return false;
                }
                if (!TryParseMesh(meshRow, out var parsed, out var meshProblem))
                {
                    problem = meshProblem;
                    return false;
                }
                if (parsed.Lod == 0)
                {
                    if (lod0 != null) { problem = "stone form declares multiple LOD0 meshes"; return false; }
                    lod0 = parsed;
                }
                parsedMeshes.Add(parsed);
            }
            if (lod0 == null)
            {
                problem = "stone form has no LOD0 mesh";
                return false;
            }
            if (!(document.TryGetValue("material", out var materialValue) && materialValue is Dictionary<string, object?> materialRow))
            {
                problem = "stone form material is missing";
                return false;
            }
            if (!(materialRow.TryGetValue("textures", out var texturesValue) && texturesValue is Dictionary<string, object?> textures))
            {
                problem = "stone form material textures are missing";
                return false;
            }
            if (!TryTextureEntry(textures, "_MainTex", verified, out var albedoFile, out var albedoProblem))
            {
                problem = albedoProblem;
                return false;
            }
            if (!TryTextureEntry(textures, "_Normal", verified, out var normalFile, out var normalProblem))
            {
                problem = normalProblem;
                return false;
            }
            // Bounded source render-queue resumption (docs section 2.5): an integral queue inside
            // [0, MaxRenderQueue] is applied as-is; absent leaves Standard's own queue (2000);
            // any other shape refuses the entry.
            var renderQueue = StandardDefaultRenderQueue;
            if (materialRow.TryGetValue("render_queue", out var renderQueueValue))
            {
                if (!TryInt(renderQueueValue, out var parsedRenderQueue) || parsedRenderQueue < 0 || parsedRenderQueue > MaxRenderQueue)
                {
                    problem = "stone form material render_queue is not an integer inside [0, " + MaxRenderQueue + "]";
                    return false;
                }
                renderQueue = (int)parsedRenderQueue;
            }

            // Build phase (everything above already validated). Failures here destroy whatever was
            // built and refuse: never a partially built visual.
            GameObject? template = null;
            Texture2D? albedo = null;
            Texture2D? normal = null;
            Mesh? mesh = null;
            Material? material = null;
            var built = false;
            try
            {
                // Albedo stays on the sRGB path; the source normal map is linear data.
                albedo = LoadTexture(Path.Combine(directory, albedoFile), false);
                if (albedo == null) { problem = "stone form texture failed to decode: " + albedoFile; return false; }
                normal = LoadTexture(Path.Combine(directory, normalFile), true);
                if (normal == null) { problem = "stone form texture failed to decode: " + normalFile; return false; }

                mesh = new Mesh { name = lod0.Name };
                mesh.SetVertices(lod0.Vertices);
                mesh.SetNormals(lod0.Normals);
                mesh.SetUVs(0, lod0.UVs);
                if (lod0.Tangents.Count == lod0.Vertices.Count) mesh.SetTangents(lod0.Tangents);
                for (var submesh = 0; submesh < lod0.Submeshes.Count; submesh++)
                    mesh.SetTriangles(lod0.Submeshes[submesh], submesh);

                material = new Material(Shader.Find(StandardShaderName)) { name = MaterialName };
                if (material.shader == null) { problem = "stone form Standard shader is unavailable"; return false; }
                if (!material.HasProperty("_BumpMap"))
                {
                    problem = "stone form Standard shader does not declare _BumpMap; the normal map cannot be bound";
                    return false;
                }
                // Bounded SR1 adaptation (docs section 2.5): sRGB albedo on _MainTex, the source
                // normal map bound as a linear _BumpMap with the _NORMALMAP keyword and a unit
                // bump scale, the source render queue resumed within bounds, white tint. No
                // Paintlight parity is claimed and mips are not regenerated here.
                material.mainTexture = albedo;
                material.SetTexture("_BumpMap", normal);
                material.EnableKeyword("_NORMALMAP");
                material.SetFloat("_BumpScale", 1f);
                material.renderQueue = renderQueue;
                material.color = Color.white;

                template = new GameObject(TemplateName);
                template.SetActive(false);
                UnityEngine.Object.DontDestroyOnLoad(template);
                template.transform.localPosition = rootPosition;
                template.transform.localRotation = rootRotation;
                template.transform.localScale = rootScale;

                var lod0Object = new GameObject(lod0.Name);
                lod0Object.transform.SetParent(template.transform, false);
                lod0Object.transform.localPosition = lod0.Position;
                lod0Object.transform.localRotation = lod0.Rotation;
                lod0Object.transform.localScale = lod0.Scale;
                lod0Object.AddComponent<MeshFilter>().sharedMesh = mesh;
                lod0Object.AddComponent<MeshRenderer>().sharedMaterials = new Material[] { material };

                model = new StoneFormModel
                {
                    CanonicalId = canonicalId,
                    Root = template,
                    MeshCount = parsedMeshes.Count,
                    VertexTotal = Sum(parsedMeshes, parsedMesh => parsedMesh.Vertices.Count),
                    TriangleTotal = Sum(parsedMeshes, parsedMesh => parsedMesh.TriangleCount),
                    Lod0VertexCount = lod0.Vertices.Count,
                    Lod0TriangleCount = lod0.TriangleCount,
                    Lod0MeshName = lod0.Name,
                };
                built = true;
                return true;
            }
            catch (Exception ex)
            {
                problem = "stone form build failed: " + ex.GetType().Name;
                return false;
            }
            finally
            {
                if (!built) DestroyBuilt(template, albedo, normal, mesh, material);
            }
        }

        private sealed class ParsedMesh
        {
            internal int Lod;
            internal string Name = "";
            internal Vector3 Position;
            internal Quaternion Rotation = Quaternion.identity;
            internal Vector3 Scale = Vector3.one;
            internal List<Vector3> Vertices = new List<Vector3>();
            internal List<Vector3> Normals = new List<Vector3>();
            internal List<Vector2> UVs = new List<Vector2>();
            internal List<Vector4> Tangents = new List<Vector4>();
            internal List<List<int>> Submeshes = new List<List<int>>();
            internal int TriangleCount;
        }

        private static bool TryParseMesh(Dictionary<string, object?> row, out ParsedMesh mesh, out string problem)
        {
            mesh = new ParsedMesh();
            problem = "";
            if (!TryInt(row.TryGetValue("lod", out var lodValue) ? lodValue : null, out var lod) || lod < 0 || lod > MaxMeshLod)
            {
                problem = "mesh lod is out of range";
                return false;
            }
            mesh.Lod = lod;
            var name = row.TryGetValue("name", out var nameValue) ? nameValue as string : null;
            mesh.Name = SanitizeName(name);
            if (!TryTransform(row, "mesh " + mesh.Name, out mesh.Position, out mesh.Rotation, out mesh.Scale, out var transformProblem))
            {
                problem = transformProblem;
                return false;
            }
            if (!TryVector3List(row, "vertices", out mesh.Vertices) || mesh.Vertices.Count == 0 || mesh.Vertices.Count > MaxVerticesPerMesh)
            {
                problem = "mesh " + mesh.Name + " vertices are missing or over the vertex budget";
                return false;
            }
            if (!TryVector3List(row, "normals", out mesh.Normals) || mesh.Normals.Count != mesh.Vertices.Count)
            {
                problem = "mesh " + mesh.Name + " normals do not match the vertex count";
                return false;
            }
            if (!TryVector2List(row, "uv", out mesh.UVs) || mesh.UVs.Count != mesh.Vertices.Count)
            {
                problem = "mesh " + mesh.Name + " uv do not match the vertex count";
                return false;
            }
            if (row.ContainsKey("tangents"))
            {
                if (!TryVector4List(row, "tangents", out mesh.Tangents) || mesh.Tangents.Count != mesh.Vertices.Count)
                {
                    problem = "mesh " + mesh.Name + " tangents do not match the vertex count";
                    return false;
                }
            }
            if (!(row.TryGetValue("triangles", out var trianglesValue) && trianglesValue is List<object?> submeshRows && submeshRows.Count > 0))
            {
                problem = "mesh " + mesh.Name + " has no triangle data";
                return false;
            }
            var totalIndices = 0;
            foreach (var submeshValue in submeshRows)
            {
                if (!(submeshValue is List<object?> indexRow) || indexRow.Count == 0 || indexRow.Count % 3 != 0)
                {
                    problem = "mesh " + mesh.Name + " submesh is not triangle-sized";
                    return false;
                }
                var indices = new List<int>(indexRow.Count);
                foreach (var indexValue in indexRow)
                {
                    if (!TryInt(indexValue, out var index) || index < 0 || index >= mesh.Vertices.Count)
                    {
                        problem = "mesh " + mesh.Name + " index out of range";
                        return false;
                    }
                    indices.Add(index);
                }
                mesh.Submeshes.Add(indices);
                totalIndices += indices.Count;
            }
            mesh.TriangleCount = totalIndices / 3;
            if (mesh.TriangleCount > MaxTrianglesPerMesh)
            {
                problem = "mesh " + mesh.Name + " is over the triangle budget";
                return false;
            }
            if (row.TryGetValue("vertex_count", out var vertexCountValue) &&
                (!TryInt(vertexCountValue, out var vertexCount) || vertexCount != mesh.Vertices.Count))
            {
                problem = "mesh " + mesh.Name + " declared vertex count does not match";
                return false;
            }
            if (row.TryGetValue("index_count", out var indexCountValue) &&
                (!TryInt(indexCountValue, out var indexCount) || indexCount != totalIndices))
            {
                problem = "mesh " + mesh.Name + " declared index count does not match";
                return false;
            }
            if (row.TryGetValue("triangle_count", out var triangleCountValue) &&
                (!TryInt(triangleCountValue, out var triangleCount) || triangleCount != mesh.TriangleCount))
            {
                problem = "mesh " + mesh.Name + " declared triangle count does not match";
                return false;
            }
            return true;
        }

        private static bool TryTransform(Dictionary<string, object?> row, string label,
            out Vector3 position, out Quaternion rotation, out Vector3 scale, out string problem)
        {
            position = Vector3.zero;
            rotation = Quaternion.identity;
            scale = Vector3.one;
            problem = "";
            if (!(row.TryGetValue("transform", out var transformValue) && transformValue is Dictionary<string, object?> transform))
            {
                problem = label + " transform is missing";
                return false;
            }
            if (!TryVector3(transform, "local_position", out position) || !IsFinite(position))
            {
                problem = label + " position is not a finite vector";
                return false;
            }
            if (!TryVector4(transform, "local_rotation", out var rotationValues) || !IsFinite(rotationValues))
            {
                problem = label + " rotation is not a finite quaternion";
                return false;
            }
            rotation = new Quaternion(rotationValues.x, rotationValues.y, rotationValues.z, rotationValues.w);
            var unitSquared = rotationValues.x * rotationValues.x + rotationValues.y * rotationValues.y +
                rotationValues.z * rotationValues.z + rotationValues.w * rotationValues.w;
            if (!IsFinite(unitSquared) || Math.Abs(unitSquared - 1f) > QuaternionUnitTolerance)
            {
                problem = label + " quaternion is not unit length";
                return false;
            }
            if (!TryVector3(transform, "local_scale", out scale) || !IsFinite(scale) || scale.x == 0f || scale.y == 0f || scale.z == 0f)
            {
                problem = label + " scale is not a finite non-zero vector";
                return false;
            }
            return true;
        }

        private static bool TryTextureEntry(Dictionary<string, object?> textures, string slot,
            Dictionary<string, string> verified, out string fileName, out string problem)
        {
            fileName = "";
            problem = "";
            if (!(textures.TryGetValue(slot, out var entryValue) && entryValue is Dictionary<string, object?> entry))
            {
                problem = "stone form material is missing the " + slot + " texture";
                return false;
            }
            var file = entry.TryGetValue("file", out var fileValue) ? fileValue as string : null;
            if (file == null || !IsSafeFileName(file))
            {
                problem = "stone form texture " + slot + " references an unsafe file";
                return false;
            }
            if (!TryInt(entry.TryGetValue("width", out var widthValue) ? widthValue : null, out var width) ||
                !TryInt(entry.TryGetValue("height", out var heightValue) ? heightValue : null, out var height) ||
                width <= 0 || height <= 0 || width > MaxTextureDimension || height > MaxTextureDimension)
            {
                problem = "stone form texture " + slot + " dimensions are out of range";
                return false;
            }
            if (!verified.ContainsKey(file))
            {
                problem = "stone form texture " + slot + " is not covered by the verified receipt: " + file;
                return false;
            }
            fileName = file;
            return true;
        }

        /// <summary>Load a cache PNG. <paramref name="linear"/> selects the linear sampling data
        /// path used by normal-map data instead of the default sRGB path used by the albedo.</summary>
        private static Texture2D? LoadTexture(string path, bool linear)
        {
            try
            {
                var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false, linear)
                {
                    name = Path.GetFileNameWithoutExtension(path),
                    wrapMode = TextureWrapMode.Repeat,
                };
                if (!texture.LoadImage(File.ReadAllBytes(path)))
                {
                    UnityEngine.Object.Destroy(texture);
                    return null;
                }
                return texture;
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static void SetLayerRecursively(GameObject root, int layer)
        {
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            var visited = 0;
            while (stack.Count > 0 && visited < 256)
            {
                visited++;
                var current = stack.Pop();
                if (current == null) continue;
                current.gameObject.layer = layer;
                for (var i = 0; i < current.childCount; i++) stack.Push(current.GetChild(i));
            }
        }

        private static void DestroyBuilt(GameObject? template, Texture2D? albedo, Texture2D? normal, Mesh? mesh, Material? material)
        {
            try { if (template != null) UnityEngine.Object.Destroy(template); } catch (Exception) { }
            try { if (albedo != null) UnityEngine.Object.Destroy(albedo); } catch (Exception) { }
            try { if (normal != null) UnityEngine.Object.Destroy(normal); } catch (Exception) { }
            try { if (mesh != null) UnityEngine.Object.Destroy(mesh); } catch (Exception) { }
            try { if (material != null) UnityEngine.Object.Destroy(material); } catch (Exception) { }
        }

        private static int Sum(List<ParsedMesh> meshes, Func<ParsedMesh, int> selector)
        {
            var total = 0;
            foreach (var mesh in meshes) total += selector(mesh);
            return total;
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

        /// <summary>Single flat file name: no separators, no '..', no rooted or reserved names.</summary>
        private static bool IsSafeFileName(string? value)
        {
            if (value == null || value.Length == 0 || value.Length > MaxNameLength) return false;
            if (value.Contains("..") || value.Contains("/") || value.Contains("\\") || value.Contains(":")) return false;
            if (value.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) return false;
            return true;
        }

        private static string SanitizeName(string? value)
        {
            if (value == null || value.Length == 0) return "StoneFormMesh";
            var builder = new StringBuilder();
            foreach (var c in value)
            {
                var safe = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
                builder.Append(safe ? c : '_');
                if (builder.Length >= MaxNameLength) break;
            }
            return builder.Length > 0 ? builder.ToString() : "StoneFormMesh";
        }

        private static bool IsSha256(string value)
        {
            if (value == null || value.Length != 64) return false;
            foreach (var c in value)
            {
                var hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
                if (!hex) return false;
            }
            return true;
        }

        private static string Sha256(string path)
        {
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
            {
                var digest = sha.ComputeHash(stream);
                var builder = new StringBuilder(64);
                foreach (var b in digest) builder.Append(b.ToString("x2", CultureInfo.InvariantCulture));
                return builder.ToString();
            }
        }

        private static bool IsFinite(Vector3 value) =>
            !float.IsNaN(value.x) && !float.IsNaN(value.y) && !float.IsNaN(value.z) &&
            !float.IsInfinity(value.x) && !float.IsInfinity(value.y) && !float.IsInfinity(value.z);

        private static bool IsFinite(Vector4 value) =>
            !float.IsNaN(value.x) && !float.IsNaN(value.y) && !float.IsNaN(value.z) && !float.IsNaN(value.w) &&
            !float.IsInfinity(value.x) && !float.IsInfinity(value.y) && !float.IsInfinity(value.z) && !float.IsInfinity(value.w);

        private static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        private static bool TryInt(object? value, out int result)
        {
            result = 0;
            switch (value)
            {
                case long integer when integer >= int.MinValue && integer <= int.MaxValue:
                    result = (int)integer;
                    return true;
                case int integer:
                    result = integer;
                    return true;
                case double fraction when fraction >= int.MinValue && fraction <= int.MaxValue && fraction == Math.Floor(fraction):
                    result = (int)fraction;
                    return true;
                case float fraction when fraction >= int.MinValue && fraction <= int.MaxValue && fraction == Math.Floor(fraction):
                    result = (int)fraction;
                    return true;
                default:
                    return false;
            }
        }

        private static bool TryLong(object? value, out long result)
        {
            result = 0;
            switch (value)
            {
                case long integer:
                    result = integer;
                    return true;
                case int integer:
                    result = integer;
                    return true;
                case double fraction when fraction >= long.MinValue && fraction <= long.MaxValue && fraction == Math.Floor(fraction):
                    result = (long)fraction;
                    return true;
                case float fraction when fraction >= long.MinValue && fraction <= long.MaxValue && fraction == Math.Floor(fraction):
                    result = (long)fraction;
                    return true;
                default:
                    return false;
            }
        }

        private static bool TryFloat(object? value, out float result)
        {
            result = 0f;
            double number;
            switch (value)
            {
                case long integer: number = integer; break;
                case int integer: number = integer; break;
                case double fraction: number = fraction; break;
                case float fraction: number = fraction; break;
                default: return false;
            }
            if (double.IsNaN(number) || double.IsInfinity(number) || number < -float.MaxValue || number > float.MaxValue) return false;
            result = (float)number;
            return true;
        }

        private static bool TryVector3(Dictionary<string, object?> row, string key, out Vector3 result)
        {
            result = Vector3.zero;
            if (!(row.TryGetValue(key, out var value) && value is List<object?> row3 && row3.Count == 3)) return false;
            if (!TryFloat(row3[0], out var x) || !TryFloat(row3[1], out var y) || !TryFloat(row3[2], out var z)) return false;
            result = new Vector3(x, y, z);
            return true;
        }

        private static bool TryVector4(Dictionary<string, object?> row, string key, out Vector4 result)
        {
            result = default;
            if (!(row.TryGetValue(key, out var value) && value is List<object?> row4 && row4.Count == 4)) return false;
            if (!TryFloat(row4[0], out var x) || !TryFloat(row4[1], out var y) ||
                !TryFloat(row4[2], out var z) || !TryFloat(row4[3], out var w)) return false;
            result = new Vector4(x, y, z, w);
            return true;
        }

        private static bool TryVector3List(Dictionary<string, object?> row, string key, out List<Vector3> result)
        {
            result = new List<Vector3>();
            if (!(row.TryGetValue(key, out var value) && value is List<object?> rows)) return false;
            foreach (var item in rows)
            {
                if (!(item is List<object?> vectorRow && vectorRow.Count == 3) ||
                    !TryFloat(vectorRow[0], out var x) || !TryFloat(vectorRow[1], out var y) || !TryFloat(vectorRow[2], out var z))
                    return false;
                result.Add(new Vector3(x, y, z));
            }
            return true;
        }

        private static bool TryVector2List(Dictionary<string, object?> row, string key, out List<Vector2> result)
        {
            result = new List<Vector2>();
            if (!(row.TryGetValue(key, out var value) && value is List<object?> rows)) return false;
            foreach (var item in rows)
            {
                if (!(item is List<object?> vectorRow && vectorRow.Count == 2) ||
                    !TryFloat(vectorRow[0], out var x) || !TryFloat(vectorRow[1], out var y))
                    return false;
                result.Add(new Vector2(x, y));
            }
            return true;
        }

        private static bool TryVector4List(Dictionary<string, object?> row, string key, out List<Vector4> result)
        {
            result = new List<Vector4>();
            if (!(row.TryGetValue(key, out var value) && value is List<object?> rows)) return false;
            foreach (var item in rows)
            {
                if (!(item is List<object?> vectorRow && vectorRow.Count == 4) ||
                    !TryFloat(vectorRow[0], out var x) || !TryFloat(vectorRow[1], out var y) ||
                    !TryFloat(vectorRow[2], out var z) || !TryFloat(vectorRow[3], out var w))
                    return false;
                result.Add(new Vector4(x, y, z, w));
            }
            return true;
        }
    }
}
