// CPU boundary over ImportedStoneForm: strict cache validation (schema, receipt SHA-256,
// budgets, TRS) and the inactive stone-visual build, against minimal UnityEngine doubles.
// No Unity runtime and no GPU: a pass means the production loader kept its documented
// fail-closed contract inside this model.
using System.Security.Cryptography;
using System.Text;
using SlimeBridge.SR1;
using UnityEngine;

var checks = 0;
var failures = 0;
void Check(bool condition, string issue)
{
    checks++;
    if (condition) return;
    failures++;
    Console.Error.WriteLine("FAIL: " + issue);
}

var root = Path.Combine(Path.GetTempPath(), "stone-form-harness-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(Path.Combine(root, "Lab", "GeneratedContent", "species-import"));
Environment.SetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE", Path.Combine(root, "anchor-missing.txt"));

string CacheDir(string canonical) => Path.Combine(root, "Lab", "AssetCache", "sr2-to-sr1", canonical);
void WriteBytes(string directory, string name, byte[] bytes)
{
    Directory.CreateDirectory(directory);
    File.WriteAllBytes(Path.Combine(directory, name), bytes);
}
byte[] Png(int seed)
{
    var bytes = new byte[64];
    bytes[0] = 0x89; bytes[1] = 0x50; bytes[2] = 0x4E; bytes[3] = 0x47;
    for (var i = 4; i < bytes.Length; i++) bytes[i] = (byte)((seed * 31 + i * 7) & 0xFF);
    return bytes;
}
string Sha(byte[] bytes)
{
    using var sha = SHA256.Create();
    var digest = sha.ComputeHash(bytes);
    var sb = new StringBuilder(64);
    foreach (var b in digest) sb.Append(b.ToString("x2"));
    return sb.ToString();
}
string F(double value) => value.ToString("R", System.Globalization.CultureInfo.InvariantCulture);

string Trs(double px, double py, double pz, double rx, double ry, double rz, double rw, double sx, double sy, double sz) =>
    "{\"local_position\": [" + F(px) + ", " + F(py) + ", " + F(pz) + "], " +
    "\"local_rotation\": [" + F(rx) + ", " + F(ry) + ", " + F(rz) + ", " + F(rw) + "], " +
    "\"local_scale\": [" + F(sx) + ", " + F(sy) + ", " + F(sz) + "]}";

string MeshRow(int lod, string name, double[][] verts, int[] indices)
{
    var vertices = new StringBuilder();
    var normals = new StringBuilder();
    var uv = new StringBuilder();
    var tangents = new StringBuilder();
    for (var i = 0; i < verts.Length; i++)
    {
        if (i > 0) { vertices.Append(", "); normals.Append(", "); uv.Append(", "); tangents.Append(", "); }
        vertices.Append("[").Append(F(verts[i][0])).Append(", ").Append(F(verts[i][1])).Append(", ").Append(F(verts[i][2])).Append("]");
        normals.Append("[0.0, 1.0, 0.0]");
        uv.Append("[0.25, 0.5]");
        tangents.Append("[1.0, 0.0, 0.0, 1.0]");
    }
    var indexList = string.Join(", ", indices);
    return "{\"game_object_id\": " + (100 + lod) + ", \"game_object_name\": \"" + name + "\", " +
        "\"index_count\": " + indices.Length + ", \"lod\": " + lod + ", " +
        "\"mesh_filter_id\": " + (200 + lod) + ", \"mesh_renderer_id\": " + (300 + lod) + ", " +
        "\"name\": \"" + name + "\", \"normals\": [" + normals + "], \"path_id\": " + (400 + lod) + ", " +
        "\"renderer_enabled\": true, \"tangents\": [" + tangents + "], " +
        "\"transform\": " + Trs(0, -0.5, 0, 0, 0, 0, 1, 1, 1, 1) + ", \"transform_id\": " + (500 + lod) + ", " +
        "\"triangle_count\": " + (indices.Length / 3) + ", \"triangles\": [[" + indexList + "]], " +
        "\"uv\": [" + uv + "], \"vertex_count\": " + verts.Length + ", \"vertices\": [" + vertices + "]}";
}

string MaterialJson(string mainFile, string normalFile, string? renderQueueRaw = "2225") =>
    "{\"colors\": {\"_TopColor\": [0.84, 0.74, 0.58, 1.0], \"_BottomColor\": [0.41, 0.28, 0.19, 1.0]}, " +
    "\"floats\": {\"_Gloss\": 0.3}, \"name\": \"slimeRingtailStoneForm\", \"path_id\": 563" +
    (renderQueueRaw == null ? "" : ", \"render_queue\": " + renderQueueRaw) + ", " +
    "\"shader_name\": \"SR/AMP/Paintlight/StaticUV/Base NormalMap\", \"shader_path_id\": 2009, " +
    "\"textures\": {\"_MainTex\": {\"path_id\": 1483, \"name\": \"alb_ringtail_statue\", \"width\": 4, \"height\": 4, \"file\": \"" + mainFile + "\"}, " +
    "\"_Normal\": {\"path_id\": 1607, \"name\": \"nrm_ringtail_statue\", \"width\": 4, \"height\": 4, \"file\": \"" + normalFile + "\"}}}";

string Document(string rootTransform, string meshes, string material) =>
    "{\"schema\": \"bridge-stone-form\", \"version\": 1, \"entry\": \"appearance\", " +
    "\"provenance\": {\"appearance_id\": 31303, \"statue_id\": 42591}, " +
    "\"root\": {\"game_object_id\": 4430, \"name\": \"ringtailStatueSmall_v1\", \"transform_id\": 15633, " +
    "\"transform\": " + rootTransform + ", \"components\": [{\"path_id\": 15633, \"type\": \"Transform\"}]}, " +
    "\"meshes\": [" + meshes + "], \"material\": " + material + "}";

double[][] Verts(int count)
{
    var verts = new double[count][];
    for (var i = 0; i < count; i++) verts[i] = new[] { 0.1 * i, 0.2, -0.05 * i };
    return verts;
}
int[] Indices(int count, int vertexCount)
{
    var indices = new int[count];
    for (var i = 0; i < count; i++) indices[i] = i % vertexCount;
    return indices;
}

string DefaultDocument() => Document(
    Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
    MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)) + ", " +
    MeshRow(1, "LF_ringtail_stone_LOD1", Verts(4), Indices(6, 4)) + ", " +
    MeshRow(2, "LF_ringtail_stone_LOD2", Verts(3), Indices(3, 3)),
    MaterialJson("tex-alb.png", "tex-nrm.png"));

void WriteCache(string canonical, string document, bool writeDocument = true, bool writeReceipt = true,
    byte[]? mainBytes = null, byte[]? normalBytes = null, string? forcedMainSha = null,
    string? receiptExtraName = null, string receiptMainName = "tex-alb.png", string receiptNormalName = "tex-nrm.png")
{
    var dir = CacheDir(canonical);
    Directory.CreateDirectory(dir);
    mainBytes ??= Png(1);
    normalBytes ??= Png(2);
    var documentBytes = new UTF8Encoding(false).GetBytes(document);
    if (writeDocument) WriteBytes(dir, "stone-form.json", documentBytes);
    WriteBytes(dir, "tex-alb.png", mainBytes);
    WriteBytes(dir, "tex-nrm.png", normalBytes);
    if (!writeReceipt) return;
    var rows = new List<string>
    {
        "{\"name\": \"stone-form.json\", \"size\": " + documentBytes.Length + ", \"sha256\": \"" + Sha(documentBytes) + "\"}",
        "{\"name\": \"" + receiptMainName + "\", \"size\": " + mainBytes.Length + ", \"sha256\": \"" + (forcedMainSha ?? Sha(mainBytes)) + "\"}",
        "{\"name\": \"" + receiptNormalName + "\", \"size\": " + normalBytes.Length + ", \"sha256\": \"" + Sha(normalBytes) + "\"}",
    };
    if (receiptExtraName != null) rows.Add("{\"name\": \"" + receiptExtraName + "\", \"size\": 4, \"sha256\": \"" + Sha(new byte[] { 1, 2, 3, 4 }) + "\"}");
    File.WriteAllText(Path.Combine(dir, "stone-form-receipt.json"),
        "{\"schema\": \"bridge-stone-form\", \"version\": 1, \"source_read_only\": true, \"files\": [" + string.Join(", ", rows) + "], " +
        "\"counts\": {\"meshes\": 3, \"vertices\": 12, \"triangles\": 5}}");
}

// ---- Case 1: valid cache loads, builds the inactive stone visual with exact counts. ----
{
    WriteCache("ringtail", DefaultDocument());
    var ok = ImportedStoneForm.TryGet("ringtail", out var model, out var problem);
    Check(ok, "valid cache must load: " + problem);
    if (ok)
    {
        Check(model.MeshCount == 3, "mesh count must be 3, got " + model.MeshCount);
        Check(model.VertexTotal == 12, "vertex total must be 12, got " + model.VertexTotal);
        Check(model.TriangleTotal == 5, "triangle total must be 5, got " + model.TriangleTotal);
        Check(model.Lod0VertexCount == 5, "LOD0 vertex count must be 5, got " + model.Lod0VertexCount);
        Check(model.Lod0TriangleCount == 2, "LOD0 triangle count must be 2, got " + model.Lod0TriangleCount);
        Check(model.Lod0MeshName == "LF_ringtail_stone_LOD0", "LOD0 mesh name must be preserved: " + model.Lod0MeshName);
        Check(!model.Root.activeSelf, "template must stay inactive");
        Check(model.Root.name == "SlimeBridgeStoneFormTemplate", "template name: " + model.Root.name);

        var actor = new GameObject("actor_ringtail");
        var visual = ImportedStoneForm.AttachTo(actor, model, out var attachProblem);
        Check(visual != null, "attach must build the stone visual: " + attachProblem);
        if (visual != null)
        {
            Check(visual.name == "StoneVisual", "visual name must be StoneVisual, got " + visual.name);
            Check(!visual.activeSelf, "stone visual must start inactive");
            Check(visual.GetComponent<ImportedStoneFormMarker>() != null, "stone visual must carry the dedicated marker");
            Check(visual.transform.parent == actor.transform, "stone visual must be a child of the actor root");
            var filter = visual.GetComponentInChildren<MeshFilter>(true);
            Check(filter != null && filter.sharedMesh != null && filter.sharedMesh.vertexCount == 5,
                "stone MeshFilter must hold the 5-vertex LOD0 mesh");
            if (filter != null && filter.sharedMesh != null)
            {
                Check(filter.sharedMesh.subMeshCount == 1 && filter.sharedMesh.triangles.Length == 6,
                    "LOD0 mesh must carry one submesh with 6 indices");
                Check(filter.gameObject.name == "LF_ringtail_stone_LOD0" &&
                      filter.transform.localPosition.y == -0.5f, "LOD0 node must keep its source TRS");
            }
            var renderer = visual.GetComponentInChildren<MeshRenderer>(true);
            Check(renderer != null && renderer.sharedMaterials.Length == 1 && renderer.sharedMaterials[0] != null,
                "stone MeshRenderer must hold one material");
            if (renderer != null && renderer.sharedMaterials.Length == 1 && renderer.sharedMaterials[0] != null)
            {
                var material = renderer.sharedMaterials[0];
                Check(material.shader != null && material.shader.name == "Standard", "stone material must use the Standard shader");
                Check(material.mainTexture is Texture2D albedoTex && albedoTex.name == "tex-alb" && !albedoTex.linear,
                    "stone albedo must be the cache PNG loaded along the sRGB (non-linear) path, got "
                    + (material.mainTexture?.name ?? "<null>"));
                Check(material.GetTexture("_BumpMap") is Texture2D bumpTex && bumpTex.name == "tex-nrm" && bumpTex.linear,
                    "stone normal map must be the cache PNG bound to _BumpMap along the linear path");
                Check(material.IsKeywordEnabled("_NORMALMAP"), "stone material must enable the _NORMALMAP keyword");
                Check(material.GetFloat("_BumpScale") == 1f, "stone _BumpScale must be 1, got " + material.GetFloat("_BumpScale"));
                Check(material.renderQueue == 2225, "stone material must resume the source render queue 2225, got " + material.renderQueue);
                Check(material.GetTexture("_Normal") == null, "the legacy _Normal slot must not be bound");
                Check(material.color == Color.white, "stone material color must be white");
            }
            var again = ImportedStoneForm.AttachTo(actor, model, out _);
            Check(ReferenceEquals(again, visual), "AttachTo must be idempotent");
        }
    }
}

// ---- Case 2: absent cache entry is inert, never a partial visual. ----
{
    var ok = ImportedStoneForm.TryGet("no-cache", out var absentModel, out var absentProblem);
    Check(!ok && absentModel == null, "absent cache must refuse");
    Check(absentProblem.Length > 0 && absentProblem.Contains("missing", StringComparison.OrdinalIgnoreCase),
        "absent cache problem must name the missing directory: " + absentProblem);
}

// ---- Case 3: receipt hash mismatch refuses. ----
{
    WriteCache("bad-hash", DefaultDocument(), forcedMainSha: new string('0', 64));
    var ok = ImportedStoneForm.TryGet("bad-hash", out _, out var hashProblem);
    Check(!ok, "hash mismatch must refuse");
    Check(hashProblem.Contains("hash mismatch", StringComparison.OrdinalIgnoreCase), "hash problem: " + hashProblem);
}

// ---- Case 4: unsafe receipt file name (traversal) refuses. ----
{
    WriteCache("traversal-receipt", DefaultDocument(), receiptMainName: "../escape.png",
        receiptExtraName: null);
    var ok = ImportedStoneForm.TryGet("traversal-receipt", out _, out var traversalProblem);
    Check(!ok, "receipt traversal must refuse");
    Check(traversalProblem.Length > 0, "traversal problem must be reported");
}

// ---- Case 5: unsafe texture file reference in the document refuses. ----
{
    var document = Document(Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)),
        MaterialJson("../escape.png", "tex-nrm.png"));
    WriteCache("traversal-texture", document);
    var ok = ImportedStoneForm.TryGet("traversal-texture", out _, out var traversalTextureProblem);
    Check(!ok, "texture traversal must refuse");
    Check(traversalTextureProblem.Length > 0, "texture traversal problem must be reported");
}

// ---- Case 6: malformed JSON refuses. ----
{
    WriteCache("malformed", "{\"schema\": \"bridge-stone-form\", \"version\": 1, \"root\": {");
    var ok = ImportedStoneForm.TryGet("malformed", out _, out var malformedProblem);
    Check(!ok, "malformed JSON must refuse");
    Check(malformedProblem.Length > 0, "malformed problem must be reported");
}

// ---- Case 7: non-finite / non-unit TRS refuses. ----
{
    WriteCache("singular-scale", Document(Trs(0, 0, 0, 0, 0, 0, 1, 0, 1, 1),
        MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)),
        MaterialJson("tex-alb.png", "tex-nrm.png")));
    Check(!ImportedStoneForm.TryGet("singular-scale", out _, out var scaleProblem), "zero root scale must refuse");
    Check(scaleProblem.Length > 0, "singular scale problem must be reported");

    WriteCache("singular-rotation", Document(Trs(0, 0, 0, 0, 0, 0, 0, 1, 1, 1),
        MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)),
        MaterialJson("tex-alb.png", "tex-nrm.png")));
    Check(!ImportedStoneForm.TryGet("singular-rotation", out _, out var rotationProblem), "non-unit quaternion must refuse");
    Check(rotationProblem.Contains("quaternion", StringComparison.OrdinalIgnoreCase), "rotation problem: " + rotationProblem);
}

// ---- Case 8: bounds. Four meshes, oversized index list and inconsistent counts refuse. ----
{
    var fourMeshes = Document(Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        MeshRow(0, "LF0", Verts(5), Indices(6, 5)) + ", " +
        MeshRow(1, "LF1", Verts(4), Indices(6, 4)) + ", " +
        MeshRow(2, "LF2", Verts(3), Indices(3, 3)) + ", " +
        MeshRow(3, "LF3", Verts(3), Indices(3, 3)),
        MaterialJson("tex-alb.png", "tex-nrm.png"));
    WriteCache("toomany", fourMeshes);
    Check(!ImportedStoneForm.TryGet("toomany", out _, out var manyProblem), "more than 3 meshes must refuse");
    Check(manyProblem.Contains("budget", StringComparison.OrdinalIgnoreCase), "mesh budget problem: " + manyProblem);

    var oversized = Document(Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        MeshRow(0, "LF0", Verts(8), Indices(4098 * 3, 8)),
        MaterialJson("tex-alb.png", "tex-nrm.png"));
    WriteCache("oversize", oversized);
    Check(!ImportedStoneForm.TryGet("oversize", out _, out var oversizeProblem), "over-budget triangles must refuse");
    Check(oversizeProblem.Contains("budget", StringComparison.OrdinalIgnoreCase), "triangle budget problem: " + oversizeProblem);

    var inconsistent = DefaultDocument().Replace("\"vertex_count\": 5", "\"vertex_count\": 6", StringComparison.Ordinal);
    WriteCache("inconsistent", inconsistent);
    Check(!ImportedStoneForm.TryGet("inconsistent", out _, out var inconsistentProblem), "declared/actual vertex mismatch must refuse");
    Check(inconsistentProblem.Length > 0, "inconsistent problem must be reported");
}

// ---- Case 9: missing receipt and uncovered texture refuse. ----
{
    WriteCache("no-receipt", DefaultDocument(), writeReceipt: false);
    Check(!ImportedStoneForm.TryGet("no-receipt", out _, out var receiptProblem), "missing receipt must refuse");
    Check(receiptProblem.Length > 0, "missing receipt problem must be reported");

    var uncovered = DefaultDocument().Replace("\"tex-nrm.png\"", "\"tex-extra.png\"", StringComparison.Ordinal);
    WriteCache("uncovered", uncovered);
    Check(!ImportedStoneForm.TryGet("uncovered", out _, out var uncoveredProblem), "a texture absent from the receipt must refuse");
    Check(uncoveredProblem.Length > 0, "uncovered texture problem must be reported");

    WriteCache("receipt-extra", DefaultDocument(), receiptExtraName: "tex-extra.png");
    Check(!ImportedStoneForm.TryGet("receipt-extra", out _, out var extraProblem), "a receipt entry whose file is missing must refuse");
    Check(extraProblem.Length > 0, "receipt extra problem must be reported");
}

// ---- Case 10: undecodable PNG refuses. ----
{
    WriteCache("bad-png", DefaultDocument());
    Texture2D.FailLoad = true;
    var ok = ImportedStoneForm.TryGet("bad-png", out _, out var pngProblem);
    Texture2D.FailLoad = false;
    Check(!ok, "undecodable PNG must refuse");
    Check(pngProblem.Contains("decode", StringComparison.OrdinalIgnoreCase), "png problem: " + pngProblem);
}

// ---- Case 11: bounded render-queue resumption (source 2225; absent => Standard 2000; invalid => refuse). ----
{
    WriteCache("no-queue", Document(Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)),
        MaterialJson("tex-alb.png", "tex-nrm.png", null)));
    var ok = ImportedStoneForm.TryGet("no-queue", out var queueModel, out var queueProblem);
    Check(ok, "a material without render_queue must load: " + queueProblem);
    if (ok)
    {
        var queueActor = new GameObject("actor_no_queue");
        var queueVisual = ImportedStoneForm.AttachTo(queueActor, queueModel, out var queueAttachProblem);
        Check(queueVisual != null, "no-queue attach must build the stone visual: " + queueAttachProblem);
        if (queueVisual != null)
        {
            var queueRenderer = queueVisual.GetComponentInChildren<MeshRenderer>(true);
            Check(queueRenderer != null && queueRenderer.sharedMaterials.Length == 1 && queueRenderer.sharedMaterials[0] != null,
                "no-queue stone visual must hold one material");
            if (queueRenderer != null && queueRenderer.sharedMaterials.Length == 1 && queueRenderer.sharedMaterials[0] != null)
            {
                Check(queueRenderer.sharedMaterials[0].renderQueue == 2000,
                    "absent render_queue must fall back to Standard's 2000, got " + queueRenderer.sharedMaterials[0].renderQueue);
            }
        }
    }

    WriteCache("bad-queue", Document(Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)),
        MaterialJson("tex-alb.png", "tex-nrm.png", "6001")));
    Check(!ImportedStoneForm.TryGet("bad-queue", out _, out var highQueueProblem),
        "an out-of-bounds render_queue must refuse");
    Check(highQueueProblem.Contains("render_queue", StringComparison.Ordinal),
        "high queue problem must name render_queue: " + highQueueProblem);

    WriteCache("fractional-queue", Document(Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        MeshRow(0, "LF_ringtail_stone_LOD0", Verts(5), Indices(6, 5)),
        MaterialJson("tex-alb.png", "tex-nrm.png", "2225.5")));
    Check(!ImportedStoneForm.TryGet("fractional-queue", out _, out var fractionalQueueProblem),
        "a non-integer render_queue must refuse");
    Check(fractionalQueueProblem.Contains("render_queue", StringComparison.Ordinal),
        "fractional queue problem must name render_queue: " + fractionalQueueProblem);
}

// ---- Case 12: a Standard shader without _BumpMap refuses (fail-closed, no partial visual). ----
{
    WriteCache("bumpmap-less", DefaultDocument());
    Material.HarnessMissingProperty = "_BumpMap";
    var ok = ImportedStoneForm.TryGet("bumpmap-less", out _, out var bumpProblem);
    Material.HarnessMissingProperty = null;
    Check(!ok, "a shader lacking _BumpMap must refuse the stone form");
    Check(bumpProblem.Contains("_BumpMap", StringComparison.Ordinal), "bump problem must name _BumpMap: " + bumpProblem);
}

Console.WriteLine($"stone form checks={checks} failures={failures}; production-linked harness; no Unity runtime exercised");
return failures == 0 ? 0 : 1;

#nullable disable
namespace UnityEngine
{
    public class Object
    {
        public string name = "";
        internal bool Destroyed;
        public static bool operator ==(Object left, Object right)
        {
            var leftNull = left is null || left.Destroyed;
            var rightNull = right is null || right.Destroyed;
            return leftNull ? rightNull : !rightNull && ReferenceEquals(left, right);
        }
        public static bool operator !=(Object left, Object right) => !(left == right);
        public override bool Equals(object other) => other is Object o && this == o;
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        public static void Destroy(Object target) { if (target != null) target.Destroyed = true; }
        public static void DestroyImmediate(Object target) { if (target != null) target.Destroyed = true; }
        public static void DontDestroyOnLoad(Object target) { }
        public static T Instantiate<T>(T original, Transform parent, bool worldPositionStays) where T : Object
        {
            var clone = original switch
            {
                GameObject gameObject => (Object)gameObject.CloneForHarness(),
                _ => throw new NotSupportedException("harness Instantiate supports GameObjects only"),
            };
            if (clone is GameObject cloned && parent != null) cloned.transform.parent = parent;
            return (T)clone;
        }
    }

    public class Component : Object
    {
        public GameObject gameObject { get; internal set; } = null;
        public Transform transform => gameObject.transform;
        public T GetComponent<T>() where T : class
        {
            foreach (var component in gameObject.Components)
                if (component is T match) return match;
            return null;
        }
        public T GetComponentInChildren<T>(bool includeInactive) where T : class => gameObject.GetComponentInChildren<T>(includeInactive);
        public T GetComponentInParent<T>() where T : class
        {
            var current = gameObject;
            while (current != null)
            {
                var match = current.GetComponent<T>();
                if (match != null) return match;
                current = current.transform.parent?.gameObject;
            }
            return null;
        }
    }

    public class Behaviour : Component
    {
        public bool enabled = true;
        public bool isActiveAndEnabled => enabled;
    }

    public class MonoBehaviour : Behaviour { }

    public sealed class GameObject : Object
    {
        internal readonly List<Component> Components = new();
        public Transform transform { get; }
        public int layer;
        internal bool ActiveSelf = true;
        public bool activeSelf => ActiveSelf;
        public bool activeInHierarchy
        {
            get
            {
                var current = this;
                while (current != null)
                {
                    if (!current.ActiveSelf) return false;
                    current = current.transform.parent?.gameObject;
                }
                return true;
            }
        }
        public GameObject(string name)
        {
            this.name = name;
            transform = new Transform(this);
        }
        public void SetActive(bool value) => ActiveSelf = value;
        public T AddComponent<T>() where T : Component
        {
            var component = (T)Activator.CreateInstance(typeof(T), nonPublic: true)!;
            component.gameObject = this;
            Components.Add(component);
            return component;
        }
        public T GetComponent<T>() where T : class
        {
            foreach (var component in Components)
                if (component is T match) return match;
            return null;
        }
        public bool TryGetComponent<T>(out T component) where T : class
        {
            component = GetComponent<T>();
            return component != null;
        }
        public T GetComponentInChildren<T>(bool includeInactive) where T : class
        {
            foreach (var component in Components)
                if (component is T match) return match;
            for (var i = 0; i < transform.childCount; i++)
            {
                var child = transform.GetChild(i).gameObject;
                if (!includeInactive && !child.ActiveSelf) continue;
                var match = child.GetComponentInChildren<T>(includeInactive);
                if (match != null) return match;
            }
            return null;
        }
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : class
        {
            var results = new List<T>();
            Collect(results, includeInactive);
            return results.ToArray();
        }
        private void Collect<T>(List<T> results, bool includeInactive) where T : class
        {
            if (!includeInactive && !ActiveSelf) return;
            foreach (var component in Components)
                if (component is T match) results.Add(match);
            for (var i = 0; i < transform.childCount; i++)
                transform.GetChild(i).gameObject.Collect(results, includeInactive);
        }
        internal GameObject CloneForHarness()
        {
            var clone = new GameObject(name) { layer = layer, ActiveSelf = ActiveSelf };
            clone.transform.localPosition = transform.localPosition;
            clone.transform.localRotation = transform.localRotation;
            clone.transform.localScale = transform.localScale;
            foreach (var component in Components)
            {
                switch (component)
                {
                    case Transform: continue;
                    case MeshFilter filter:
                        clone.AddComponent<MeshFilter>().sharedMesh = filter.sharedMesh;
                        break;
                    case MeshRenderer renderer:
                        clone.AddComponent<MeshRenderer>().sharedMaterials = renderer.sharedMaterials;
                        break;
                    case ImportedStoneFormMarker:
                        clone.AddComponent<ImportedStoneFormMarker>();
                        break;
                }
            }
            for (var i = 0; i < transform.childCount; i++)
            {
                var childClone = transform.GetChild(i).gameObject.CloneForHarness();
                childClone.transform.parent = clone.transform;
            }
            return clone;
        }
    }

    public sealed class Transform : Component
    {
        private readonly List<Transform> _children = new();
        private Transform _parent;
        public Vector3 localPosition;
        public Quaternion localRotation = Quaternion.identity;
        public Vector3 localScale = new Vector3(1, 1, 1);
        public Transform(GameObject owner) { gameObject = owner; }
        public Transform parent
        {
            get => _parent;
            set
            {
                if (_parent != null) _parent._children.Remove(this);
                _parent = value;
                if (value != null) value._children.Add(this);
            }
        }
        public int childCount => _children.Count;
        public Transform GetChild(int index) => _children[index];
        public void SetParent(Transform value, bool worldPositionStays) => parent = value;
    }

    public struct Vector2
    {
        public float x, y;
        public Vector2(float x, float y) { this.x = x; this.y = y; }
        public override string ToString() => $"({x}, {y})";
    }

    public struct Vector3
    {
        public float x, y, z;
        public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
        public static Vector3 zero => new Vector3(0, 0, 0);
        public static Vector3 one => new Vector3(1, 1, 1);
        public static Vector3 operator +(Vector3 a, Vector3 b) => new Vector3(a.x + b.x, a.y + b.y, a.z + b.z);
        public static Vector3 operator -(Vector3 a, Vector3 b) => new Vector3(a.x - b.x, a.y - b.y, a.z - b.z);
        public static Vector3 operator *(Vector3 a, float s) => new Vector3(a.x * s, a.y * s, a.z * s);
        public override string ToString() => $"({x}, {y}, {z})";
    }

    public struct Vector4
    {
        public float x, y, z, w;
        public Vector4(float x, float y, float z, float w) { this.x = x; this.y = y; this.z = z; this.w = w; }
    }

    public struct Quaternion
    {
        public float x, y, z, w;
        public Quaternion(float x, float y, float z, float w) { this.x = x; this.y = y; this.z = z; this.w = w; }
        public static Quaternion identity => new Quaternion(0, 0, 0, 1);
        public float sqrMagnitude => x * x + y * y + z * z + w * w;
    }

    public struct Color
    {
        public float r, g, b, a;
        public Color(float r, float g, float b, float a) { this.r = r; this.g = g; this.b = b; this.a = a; }
        public static Color white => new Color(1, 1, 1, 1);
        public static Color clear => new Color(0, 0, 0, 0);
        public static bool operator ==(Color left, Color right) => left.r == right.r && left.g == right.g && left.b == right.b && left.a == right.a;
        public static bool operator !=(Color left, Color right) => !(left == right);
        public override bool Equals(object other) => other is Color c && this == c;
        public override int GetHashCode() => r.GetHashCode() ^ g.GetHashCode() ^ b.GetHashCode() ^ a.GetHashCode();
    }

    public sealed class Shader : Object
    {
        private static readonly Dictionary<string, Shader> Known = new(StringComparer.Ordinal);
        internal Shader(string shaderName) { name = shaderName; }
        public static Shader Find(string shaderName)
        {
            if (!Known.TryGetValue(shaderName, out var shader)) { shader = new Shader(shaderName); Known[shaderName] = shader; }
            return shader;
        }
    }

    public class Texture : Object
    {
        public TextureWrapMode wrapMode;
    }

    public enum TextureFormat { RGBA32 = 4 }
    public enum TextureWrapMode { Repeat = 0, Clamp = 1 }

    public sealed class Texture2D : Texture
    {
        public static bool FailLoad;
        public bool linear;
        public Texture2D(int width, int height, TextureFormat format, bool mipChain) : this(width, height, format, mipChain, false) { }
        public Texture2D(int width, int height, TextureFormat format, bool mipChain, bool linear) { this.linear = linear; }
        public bool LoadImage(byte[] data) => !FailLoad && data.Length > 0;
    }

    public sealed class Material : Object
    {
        private readonly Dictionary<string, Texture> textures = new(StringComparer.Ordinal);
        private readonly Dictionary<string, float> floats = new(StringComparer.Ordinal);
        private readonly HashSet<string> keywords = new(StringComparer.Ordinal);
        public static string HarnessMissingProperty = null; // test switch: simulate a shader that dropped a property
        public Shader shader { get; }
        public Color color;
        public int renderQueue = -1; // Unity convention: -1 resolves to the shader's own queue
        public Material(Shader shader) { this.shader = shader; }
        public Material(string shaderName) { shader = Shader.Find(shaderName); }
        public Material(Material source) { shader = source.shader; color = source.color; foreach (var pair in source.textures) textures[pair.Key] = pair.Value; }
        public Texture mainTexture
        {
            get => textures.TryGetValue("_MainTex", out var texture) ? texture : null;
            set { if (value != null) textures["_MainTex"] = value; }
        }
        public bool HasProperty(string property) =>
            (HarnessMissingProperty == null || property != HarnessMissingProperty) &&
            (property == "_MainTex" || property == "_Color" || property == "_BumpMap" || textures.ContainsKey(property) || floats.ContainsKey(property));
        public void SetTexture(string property, Texture texture) { if (texture != null) textures[property] = texture; }
        public Texture GetTexture(string property) => textures.TryGetValue(property, out var texture) ? texture : null;
        public void SetFloat(string property, float value) => floats[property] = value;
        public float GetFloat(string property) => floats.TryGetValue(property, out var value) ? value : 0f;
        public void EnableKeyword(string keyword) => keywords.Add(keyword);
        public void DisableKeyword(string keyword) => keywords.Remove(keyword);
        public bool IsKeywordEnabled(string keyword) => keywords.Contains(keyword);
    }

    public sealed class Mesh : Object
    {
        public int vertexCount => Vertices.Count;
        public int subMeshCount { get; internal set; }
        public List<Vector3> Vertices { get; } = new();
        public List<Vector3> NormalsList { get; } = new();
        public List<Vector2> UVs { get; } = new();
        public List<Vector4> Tangents { get; } = new();
        public List<int> TriangleList { get; } = new();
        public int[] triangles => TriangleList.ToArray();
        public void SetVertices(List<Vector3> values) { Vertices.Clear(); Vertices.AddRange(values); }
        public void SetNormals(List<Vector3> values) { NormalsList.Clear(); NormalsList.AddRange(values); }
        public void SetUVs(int channel, List<Vector2> values) { UVs.Clear(); UVs.AddRange(values); }
        public void SetTangents(List<Vector4> values) { Tangents.Clear(); Tangents.AddRange(values); }
        public void SetTriangles(List<int> values, int submesh)
        {
            TriangleList.Clear(); TriangleList.AddRange(values);
            subMeshCount = 1;
        }
        public void RecalculateBounds() { }
    }

    public sealed class MeshFilter : Component
    {
        public Mesh sharedMesh;
    }

    public class Renderer : Component
    {
        public bool enabled = true;
        public Material[] sharedMaterials = Array.Empty<Material>();
        public bool HasPropertyBlock() => false;
    }

    public sealed class MeshRenderer : Renderer { }

    public static class Debug
    {
        public static void Log(object message) { }
        public static void LogWarning(object message) { Console.Error.WriteLine("WARN: " + message); }
        public static void LogError(object message) { Console.Error.WriteLine("ERROR: " + message); }
    }
}
