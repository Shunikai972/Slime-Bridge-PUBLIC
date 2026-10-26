using System.Globalization;
using SlimeBridge.SR1;
using SlimeBridge.Shared;
using UnityEngine;

// Source-linked CPU boundary over the production spawn palette scope. It models the spawn
// palette pass and the imported-visual palette-block sweep against a minimal renderer /
// MaterialPropertyBlock model: no Unity rendering is exercised, and a "pass" here only means
// the production code kept the documented block/material state in this model.
//
// Fixture note: Unity's Object.Instantiate does NOT copy runtime MaterialPropertyBlocks, but the
// legacy scenario below needs the spawned instance to carry the state an instance painted by the
// pre-fix spawn path already has (the heal target of the fail-safe sweep); copying the blocks in
// the harness clone models that "already painted live instance" state, nothing else.
var checks = 0;
void Check(bool condition, string issue) { if (!condition) throw new Exception(issue); checks++; }
void Attach(GameObject parent, GameObject child) => child.transform.parent = parent.transform;
bool ColorEq(Color a, Color b) =>
    Math.Abs(a.r - b.r) < 1e-5f && Math.Abs(a.g - b.g) < 1e-5f &&
    Math.Abs(a.b - b.b) < 1e-5f && Math.Abs(a.a - b.a) < 1e-5f;
bool Under(GameObject node, GameObject root)
{
    Transform current = node.transform;
    while (current != null)
    {
        if (current.gameObject == root) return true;
        current = current.parent;
    }
    return false;
}
string Detail(List<string> details, string key)
{
    foreach (var entry in details)
        if (entry.StartsWith(key, StringComparison.Ordinal))
            return entry.Substring(entry.IndexOf(':') + 1).Trim();
    throw new Exception("missing detail " + key);
}
float[] Rgba(int r, int g, int b) => new[] { r / 255f, g / 255f, b / 255f, 1f };

var topId = Shader.PropertyToID("_TopColor");
var middleId = Shader.PropertyToID("_MiddleColor");
var bottomId = Shader.PropertyToID("_BottomColor");
var heldId = Shader.PropertyToID("_HeldInVac");
var bumpId = Shader.PropertyToID("_BumpMap");
var mainTexId = Shader.PropertyToID("_MainTex");

// Spec palette (placeholder tint) vs the validated visual material colors: the two families must
// differ so a painted visual would be detectable at all.
var specTop = new Color(123f / 255f, 102f / 255f, 87f / 255f, 1f);
var specMiddle = new Color(123f / 255f, 102f / 255f, 87f / 255f, 1f);
var specBottom = new Color(96f / 255f, 78f / 255f, 66f / 255f, 1f);
var matTop = new Color(214f / 255f, 189f / 255f, 148f / 255f, 1f);
var matMiddle = new Color(186f / 255f, 156f / 255f, 118f / 255f, 1f);
var matBottom = new Color(146f / 255f, 116f / 255f, 84f / 255f, 1f);

Material TabbyBody()
{
    var material = new Material("SR/AMP/Slime/Body/Stripe") { name = "tabbyBody(Clone)" };
    material.properties.Add("_TopColor");
    material.properties.Add("_MiddleColor");
    material.properties.Add("_BottomColor");
    return material;
}
Material NativeBody(string part)
{
    var material = new Material("SR/AMP/Slime/Body/Stripe") { name = "SlimeBridgeNativeBody(Clone)" };
    material.properties.Add("_TopColor");
    material.properties.Add("_MiddleColor");
    material.properties.Add("_BottomColor");
    material.properties.Add("_StripeTexture");
    material.properties.Add("_BumpMap");
    material.floats["_Gloss"] = 0.35f;
    material.colors["_TopColor"] = matTop;
    material.colors["_MiddleColor"] = matMiddle;
    material.colors["_BottomColor"] = matBottom;
    material.textures["_StripeTexture"] = new Texture2D { name = "ringtail_stripe_" + part };
    material.textures["_BumpMap"] = new Texture2D { name = "ringtail_bump" };
    return material;
}
Renderer AddRenderer(GameObject parent, string name, Material[] materials, bool partData = false)
{
    var node = new GameObject(name);
    Attach(parent, node);
    var renderer = node.AddComponent<Renderer>();
    renderer.sharedMaterials = materials;
    if (partData)
    {
        // The loader attaches one marker per imported part renderer; the fixtures mirror it so
        // the serialized-field transfer model exercises the same component the mod spawns with.
        var data = node.AddComponent<ImportedPartColors>();
        data.SourceFace = false;
        data.Pattern = new Texture2D { name = "ringtail_pattern" };
        data.FaceTex = null;
        data.FaceMask = null;
        data.Top = matTop;
        data.Middle = matMiddle;
        data.Bottom = matBottom;
    }
    return renderer;
}
Renderer[] AddVisualBodies(GameObject visualRoot) => new[]
{
    AddRenderer(visualRoot, "body_low", new[] { NativeBody("low") }, partData: true),
    AddRenderer(visualRoot, "body_high", new[] { NativeBody("high") }, partData: true),
    AddRenderer(visualRoot, "body_mid", new[] { NativeBody("mid") }, partData: true),
};
GameObject VisualRoot()
{
    var visual = new GameObject(ImportedSpawner.ImportedVisualName);
    visual.AddComponent<ImportedVisualMarker>();
    return visual;
}
GameObject BuildModelRoot()
{
    var root = new GameObject("SlimeBridgeImportRingtail");
    AddVisualBodies(root);
    return root;
}
void ProvideModel() => ImportedModelLoader.LastModel = new ImportedModelLoader.ImportedModel
{
    Root = BuildModelRoot(),
    RootScale = Vector3.one,
    RendererCount = 3,
    VertexTotal = 300,
};
SpeciesRegistrar Registrar()
{
    var registrar = new SpeciesRegistrar();
    registrar.NativeIds["ringtail"] = 4242;
    registrar.Palettes["ringtail"] = new[] { Rgba(123, 102, 87), Rgba(123, 102, 87), Rgba(96, 78, 66) };
    ImportedIdentityPatch.Imported.Add(4242);
    return registrar;
}
(string floats, string colors, string textures) Snap(Material material) => (
    string.Join(";", material.floats.OrderBy(p => p.Key).Select(p => p.Key + "=" + p.Value.ToString("R", CultureInfo.InvariantCulture))),
    string.Join(";", material.colors.OrderBy(p => p.Key).Select(p => p.Key + "=" +
        p.Value.r.ToString("R", CultureInfo.InvariantCulture) + "," + p.Value.g.ToString("R", CultureInfo.InvariantCulture) + "," +
        p.Value.b.ToString("R", CultureInfo.InvariantCulture) + "," + p.Value.a.ToString("R", CultureInfo.InvariantCulture))),
    string.Join(";", material.textures.OrderBy(p => p.Key).Select(p => p.Key + "=" + (p.Value.name ?? ""))));

// ---- A: spawn scope — the palette pass must skip the marked visual subtree -------------------
{
    var prefab = new GameObject("slime_ringtail");
    prefab.AddComponent<Identifiable>().id = Identifiable.Id.TEST_IMPORTED;
    AddRenderer(prefab, "body", new[] { TabbyBody() });
    AddRenderer(prefab, "eyes", new[] { TabbyBody() });
    var prefabVisual = VisualRoot();
    Attach(prefab, prefabVisual);
    AddVisualBodies(prefabVisual); // the guard attaches the live visual during Object.Instantiate
    var game = new GameContext();
    game.LookupDirector.Prefab = prefab;
    var scene = new SceneContext { Player = new GameObject("player") };
    ProvideModel();
    var details = new List<string>();
    var problems = new List<string>();
    var instance = ImportedSpawner.Spawn(game, scene, Registrar(), "ringtail", details, problems);
    Check(instance != null && problems.Count == 0, "spawn refused: " + string.Join("; ", problems));
    Check(Detail(details, "\"palette_applied\"") == "true", "placeholder palette no longer applied");
    Check(Detail(details, "\"palette_renderers\"") == "2", "palette scope must count only the two placeholders");
    Check(Detail(details, "\"palette_supported\"") == "2", "placeholder materials no longer palette-capable");
    Check(Detail(details, "\"palette_verified\"") == "2", "placeholder palette read-back failed");
    Check(Detail(details, "\"palette_visual_skipped\"") == "3", "visual subtree renderers must be excluded and counted");
    Check(Detail(details, "\"model_applied\"") == "true", "imported visual not applied");
    Check(instance!.GetComponentInChildren<Identifiable>(true)!.id == Identifiable.Id.TEST_IMPORTED, "instance identity lost");
    var visual = ImportedSpawner.FindImportedVisual(instance!);
    Check(visual != null, "imported visual marker missing after spawn");
    var bodies = new List<Renderer>();
    var placeholders = new List<Renderer>();
    foreach (var renderer in instance.GetComponentsInChildren<Renderer>(true))
        (Under(renderer.gameObject, visual!) ? bodies : placeholders).Add(renderer);
    Check(bodies.Count == 3 && placeholders.Count == 2, "fixture hierarchy lost");
    foreach (var placeholder in placeholders)
    {
        Check(!placeholder.enabled, "placeholder renderer was not suppressed");
        var block = new MaterialPropertyBlock();
        placeholder.GetPropertyBlock(block);
        Check(ColorEq(block.GetColor(topId), specTop) && ColorEq(block.GetColor(bottomId), specBottom),
            "placeholder palette block missing");
    }
    foreach (var body in bodies)
    {
        var block = new MaterialPropertyBlock();
        body.GetPropertyBlock(block);
        Check(block.isEmpty && !block.Contains(topId), "spawn painted the imported visual with a palette block");
        Check(!body.HasPropertyBlock(), "imported visual renderer still carries a property block");
        var material = body.sharedMaterials[0];
        Check(ColorEq(material.GetColor("_TopColor"), matTop) && ColorEq(material.GetColor("_BottomColor"), matBottom),
            "visual material palette changed");
        Check(!ColorEq(material.GetColor("_TopColor"), specTop), "fixture spec/material colors must differ");
    }
}

// ---- B1: legacy painted visual — sweep heals, stays scoped, is idempotent --------------------
{
    var instance = new GameObject("legacy_actor");
    AddRenderer(instance, "body", new[] { TabbyBody() });
    var visual = VisualRoot();
    Attach(instance, visual);
    var bodies = AddVisualBodies(visual);
    var legacy = new MaterialPropertyBlock();
    legacy.SetColor(topId, specTop);
    legacy.SetColor(middleId, specMiddle);
    legacy.SetColor(bottomId, specBottom);
    foreach (var body in bodies) body.SetPropertyBlock(legacy);
    var held = new MaterialPropertyBlock();
    held.SetFloat(heldId, 1f);
    bodies[0].SetPropertyBlock(held, 0); // native per-material block storage
    var foreign = new MaterialPropertyBlock();
    foreign.SetFloat(bumpId, 0.5f);
    foreign.SetTexture(mainTexId, new Texture2D { name = "placeholder_map" });
    var markerless = instance.GetComponentsInChildren<Renderer>(true)
        .First(r => r != bodies[0] && r != bodies[1] && r != bodies[2]);
    markerless.SetPropertyBlock(foreign);
    var before = bodies.Select(b => Snap(b.sharedMaterials[0])).ToArray();
    var writesBefore = Renderer.WideBlockWrites;
    var slotWritesBefore = Renderer.SlotBlockWrites;
    var model = new ImportedModelLoader.ImportedModel { Root = BuildModelRoot(), RootScale = Vector3.one, RendererCount = 3, VertexTotal = 300 };
    Check(ImportedSpawner.ApplyImportedVisualToRoot(instance, "ringtail", model, out _), "apply failed on legacy instance");
    foreach (var body in bodies)
    {
        var block = new MaterialPropertyBlock();
        body.GetPropertyBlock(block);
        Check(block.isEmpty, "legacy palette block survived the sweep");
    }
    Check(Renderer.WideBlockWrites == writesBefore + 3, "sweep wrote an unexpected number of renderer blocks");
    Check(Renderer.SlotBlockWrites == slotWritesBefore, "sweep touched per-material block storage");
    var heldRead = new MaterialPropertyBlock();
    bodies[0].GetPropertyBlock(heldRead, 0);
    Check(heldRead.GetFloat(heldId) == 1f, "native per-material block was cleared by the sweep");
    var foreignRead = new MaterialPropertyBlock();
    markerless.GetPropertyBlock(foreignRead);
    Check(foreignRead.GetFloat(bumpId) == 0.5f && foreignRead.GetTexture(mainTexId) != null,
        "sweep touched a renderer without the marker");
    var after = bodies.Select(b => Snap(b.sharedMaterials[0])).ToArray();
    Check(before.SequenceEqual(after), "sweep touched visual materials (bump/map/stripe)");
    var writesAfterFirst = Renderer.WideBlockWrites;
    Check(ImportedSpawner.ApplyImportedVisualToRoot(instance, "ringtail", model, out _), "second apply failed");
    Check(Renderer.WideBlockWrites == writesAfterFirst, "second sweep wrote renderer blocks again");
    foreach (var body in bodies)
    {
        var block = new MaterialPropertyBlock();
        body.GetPropertyBlock(block);
        Check(block.isEmpty, "second pass repainted a cleared block");
    }
    var heldAgain = new MaterialPropertyBlock();
    bodies[0].GetPropertyBlock(heldAgain, 0);
    Check(heldAgain.GetFloat(heldId) == 1f, "second pass dropped the per-material block");
}

// ---- B2: spawn heals a pre-painted (legacy) visual -------------------------------------------
{
    var prefab = new GameObject("slime_ringtail_legacy");
    prefab.AddComponent<Identifiable>().id = Identifiable.Id.TEST_IMPORTED;
    AddRenderer(prefab, "body", new[] { TabbyBody() });
    AddRenderer(prefab, "eyes", new[] { TabbyBody() });
    var prefabVisual = VisualRoot();
    Attach(prefab, prefabVisual);
    var prefabBodies = AddVisualBodies(prefabVisual);
    var legacy = new MaterialPropertyBlock();
    legacy.SetColor(topId, specTop);
    legacy.SetColor(middleId, specMiddle);
    legacy.SetColor(bottomId, specBottom);
    foreach (var body in prefabBodies) body.SetPropertyBlock(legacy);
    var game = new GameContext();
    game.LookupDirector.Prefab = prefab;
    var scene = new SceneContext { Player = new GameObject("player") };
    ProvideModel();
    var details = new List<string>();
    var problems = new List<string>();
    var instance = ImportedSpawner.Spawn(game, scene, Registrar(), "ringtail", details, problems);
    Check(instance != null && problems.Count == 0, "legacy spawn refused: " + string.Join("; ", problems));
    Check(Detail(details, "\"palette_visual_skipped\"") == "3", "legacy visual not skipped by the palette pass");
    var visual = ImportedSpawner.FindImportedVisual(instance!);
    Check(visual != null, "legacy visual missing");
    var cleared = 0;
    foreach (var renderer in visual!.GetComponentsInChildren<Renderer>(true))
    {
        var block = new MaterialPropertyBlock();
        renderer.GetPropertyBlock(block);
        if (block.isEmpty) cleared++;
    }
    Check(cleared == 3, "spawn did not clear the legacy palette blocks on the visual");
}

// ---- C: appearance guard — pre-armament resolution must not pin the actor --------------------
{
    SpeciesRegistrar.Current = null;
    var actor = new GameObject("actor_ringtail");
    actor.AddComponent<Identifiable>().id = Identifiable.Id.TEST_IMPORTED;
    var applicatorNode = new GameObject("appearance");
    Attach(actor, applicatorNode);
    var applicator = applicatorNode.AddComponent<SlimeAppearanceApplicator>();
    var preArmament = ImportedSpawner.ResolveImportedCanonical(applicator);
    Check(preArmament == "", "pre-armament resolution must stay empty");
    SpeciesRegistrar.Current = Registrar();
    var resolved = ImportedSpawner.ResolveImportedCanonical(applicator);
    Check(resolved == "ringtail", "resolution was pinned negative before the registrar armed");
    Check(ImportedSpawner.ResolveImportedCanonical(applicator) == "ringtail", "positive resolution not cached");
    ProvideModel();
    BridgeEntry.LabRestPoseRequested = true;
    ImportedSpawner.GuardApplicator(applicator);
    BridgeEntry.LabRestPoseRequested = false;
    Check(applicator.enabled == false, "appearance runtime not neutralized");
    var visual = ImportedSpawner.FindImportedVisual(actor);
    Check(visual != null && visual!.name == ImportedSpawner.ImportedVisualName, "guard did not attach the visual after armament");
    Check(visual!.GetComponentsInChildren<ImportedVisualMarker>(true).Length >= 1, "attached visual lacks its marker");
    Check(LabSourceAnimationTrace.AttachCalls == 1, "rest-pose trace not attached to a freshly created visual");
    var guardBodies = visual!.GetComponentsInChildren<Renderer>(true);
    Check(guardBodies.Length == 3, "guard visual lost renderers");
    foreach (var body in guardBodies)
    {
        var block = new MaterialPropertyBlock();
        body.GetPropertyBlock(block);
        Check(block.isEmpty, "guard-attached visual carries a palette block");
    }
    SpeciesRegistrar.Current = null;
}

// ---- F: no visual during the palette pass — created by the apply, still clean ----------------
{
    var prefab = new GameObject("slime_ringtail_no_visual");
    prefab.AddComponent<Identifiable>().id = Identifiable.Id.TEST_IMPORTED;
    AddRenderer(prefab, "body", new[] { TabbyBody() });
    AddRenderer(prefab, "eyes", new[] { TabbyBody() });
    var game = new GameContext();
    game.LookupDirector.Prefab = prefab;
    var scene = new SceneContext { Player = new GameObject("player") };
    ProvideModel();
    var details = new List<string>();
    var problems = new List<string>();
    var instance = ImportedSpawner.Spawn(game, scene, Registrar(), "ringtail", details, problems);
    Check(instance != null && problems.Count == 0, "no-visual spawn refused: " + string.Join("; ", problems));
    Check(Detail(details, "\"palette_visual_skipped\"") == "0", "no visual was attached during the palette pass");
    Check(Detail(details, "\"palette_applied\"") == "true", "placeholder palette lost without a visual");
    Check(Detail(details, "\"model_applied\"") == "true", "later visual apply failed");
    var visual = ImportedSpawner.FindImportedVisual(instance!);
    Check(visual != null && visual!.GetComponentsInChildren<Renderer>(true).Length == 3, "later visual missing or incomplete");
    foreach (var body in visual!.GetComponentsInChildren<Renderer>(true))
    {
        var block = new MaterialPropertyBlock();
        body.GetPropertyBlock(block);
        Check(block.isEmpty, "later visual carries a palette block");
    }
    Check(visual!.layer == instance!.layer, "visual layer not propagated");
}

// A stone visual is a source-owned alternate form, not a native placeholder.
{
    var actor = new GameObject("petrified_ringtail");
    var live = VisualRoot();
    Attach(actor, live);
    var liveRenderer = AddRenderer(live, "live_body", new[] { NativeBody("live") });
    var stone = new GameObject("StoneVisual");
    Attach(actor, stone);
    stone.AddComponent<ImportedStoneFormMarker>();
    var stoneRenderer = AddRenderer(stone, "LF_ringtail_stone_LOD0", new[] { new Material("Standard") });
    var placeholder = AddRenderer(actor, "native_placeholder", new[] { TabbyBody() });
    typeof(ImportedSpawner).GetMethod("SuppressPlaceholders", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!
        .Invoke(null, new object[] { actor, live, new List<Renderer>() });
    Check(stoneRenderer.enabled, "appearance suppression must not disable the source stone mesh");
    Check(liveRenderer.enabled, "the living visual exclusion must be preserved");
    Check(!placeholder.enabled, "real native placeholders must still be suppressed");
}
Console.WriteLine($"palette scope checks={checks} failures=0; production ImportedSpawner.cs source-linked renderer/palette-block model; no Unity rendering exercised");

#nullable disable
namespace UnityEngine
{
    [AttributeUsage(AttributeTargets.Field)]
    public sealed class SerializeField : Attribute { }

    public class Object
    {
        internal bool destroyRequested;
        public string name = "";
        public static T Instantiate<T>(T original, Vector3 position, Quaternion rotation) where T : Object
        {
            var clone = original.CloneForHarness();
            if (clone is GameObject go) go.transform.position = position;
            return (T)clone;
        }
        public static T Instantiate<T>(T original, Transform parent, bool worldPositionStays) where T : Object
        {
            var clone = original.CloneForHarness();
            if (clone is GameObject go) go.transform.parent = parent;
            return (T)clone;
        }
        public static void Destroy(Object obj) { if (!(obj is null)) obj.destroyRequested = true; }
        public static void DestroyImmediate(Object obj) { if (!(obj is null)) obj.destroyRequested = true; }
        public static T FindObjectOfType<T>() where T : Object => null; // fixture: no live registry
        internal virtual Object CloneForHarness() => this;
        public static bool operator ==(Object left, Object right)
        {
            var leftGone = left is null || left.destroyRequested;
            var rightGone = right is null || right.destroyRequested;
            if (leftGone || rightGone) return leftGone && rightGone;
            return ReferenceEquals(left, right);
        }
        public static bool operator !=(Object left, Object right) => !(left == right);
        public override bool Equals(object obj) => ReferenceEquals(this, obj);
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
    }

    public class Component : Object
    {
        public GameObject gameObject = null;
        public Transform transform => gameObject.transform;
        public T GetComponent<T>() where T : class => gameObject.GetComponent<T>();
        public T GetComponentInParent<T>() where T : class => gameObject.GetComponentInParent<T>();
        public T GetComponentInChildren<T>(bool includeInactive) where T : class => gameObject.GetComponentInChildren<T>(includeInactive);
    }

    public class Behaviour : Component
    {
        public bool enabled = true;
    }

    public class MonoBehaviour : Behaviour { }

    public sealed class GameObject : Object
    {
        internal readonly List<Component> components = new();
        private readonly Transform _transform;
        public bool activeSelf = true;
        public int layer;

        public GameObject(string name = "")
        {
            this.name = name;
            _transform = new Transform();
            _transform.gameObject = this;
            components.Add(_transform);
        }

        public Transform transform => _transform;
        public bool activeInHierarchy
        {
            get
            {
                var current = this;
                while (current != null)
                {
                    if (!current.activeSelf) return false;
                    current = current.transform.parent == null ? null : current.transform.parent.gameObject;
                }
                return true;
            }
        }
        public void SetActive(bool value) => activeSelf = value;

        public T AddComponent<T>() where T : Component
        {
            var component = (T)Activator.CreateInstance(typeof(T), nonPublic: true);
            component.gameObject = this;
            components.Add(component);
            return component;
        }
        public T GetComponent<T>() where T : class
        {
            foreach (var component in components)
                if (component is T match) return match;
            return null;
        }
        public T GetComponentInParent<T>() where T : class
        {
            var current = this;
            while (current != null)
            {
                var found = current.GetComponent<T>();
                if (found != null) return found;
                current = current.transform.parent == null ? null : current.transform.parent.gameObject;
            }
            return null;
        }
        public T GetComponentInChildren<T>(bool includeInactive) where T : class
        {
            foreach (var component in components)
                if (component is T match) return match;
            for (var i = 0; i < transform.childCount; i++)
            {
                var found = transform.GetChild(i).gameObject.GetComponentInChildren<T>(includeInactive);
                if (found != null) return found;
            }
            return null;
        }
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : class
        {
            var result = new List<T>();
            var self = GetComponent<T>();
            if (self != null) result.Add(self);
            for (var i = 0; i < transform.childCount; i++)
                result.AddRange(transform.GetChild(i).gameObject.GetComponentsInChildren<T>(includeInactive));
            return result.ToArray();
        }
        public bool TryGetComponent<T>(out T component) where T : class
        {
            component = GetComponent<T>();
            return component != null;
        }
        public bool TryGetComponent(Type type, out Component component)
        {
            foreach (var candidate in components)
            {
                if (type.IsInstanceOfType(candidate)) { component = candidate; return true; }
            }
            component = null;
            return false;
        }

        internal override Object CloneForHarness()
        {
            var copy = new GameObject(name + "(Clone)") { layer = layer, activeSelf = activeSelf };
            foreach (var component in components)
            {
                switch (component)
                {
                    case Transform:
                        break;
                    case Renderer renderer:
                        var rendererCopy = copy.AddComponent<Renderer>();
                        rendererCopy.enabled = renderer.enabled;
                        rendererCopy.sharedMaterials = renderer.sharedMaterials == null
                            ? Array.Empty<Material>() : (Material[])renderer.sharedMaterials.Clone();
                        rendererCopy.CopyBlocksFrom(renderer);
                        break;
                    case ImportedPartColors partColors:
                        CopySerializedFields(partColors, copy.AddComponent<ImportedPartColors>());
                        break;
                    case ImportedVisualMarker:
                        copy.AddComponent<ImportedVisualMarker>();
                        break;
                    case Identifiable identifiable:
                        copy.AddComponent<Identifiable>().id = identifiable.id;
                        break;
                    case SlimeAppearanceApplicator applicator:
                        copy.AddComponent<SlimeAppearanceApplicator>().enabled = applicator.enabled;
                        break;
                    default:
                        break;
                }
            }
            for (var i = 0; i < transform.childCount; i++)
                ((GameObject)transform.GetChild(i).gameObject.CloneForHarness()).transform.parent = copy.transform;
            return copy;
        }

        private static void CopySerializedFields(ImportedPartColors source, ImportedPartColors target)
        {
            foreach (var field in typeof(ImportedPartColors).GetFields(
                         System.Reflection.BindingFlags.Instance |
                         System.Reflection.BindingFlags.Public |
                         System.Reflection.BindingFlags.NonPublic))
            {
                if (field.IsStatic || field.IsInitOnly || Attribute.IsDefined(field, typeof(NonSerializedAttribute))) continue;
                if (!field.IsPublic && !Attribute.IsDefined(field, typeof(SerializeField))) continue;
                if (field.FieldType != typeof(float) && field.FieldType != typeof(bool) &&
                    field.FieldType != typeof(Color) && field.FieldType != typeof(Texture2D)) continue;
                field.SetValue(target, field.GetValue(source));
            }
        }
    }

    public sealed class Transform : Component
    {
        private readonly List<Transform> _children = new();
        private Transform _parent;
        public Vector3 position;
        public Vector3 localPosition;
        public Vector3 localScale = Vector3.one;
        public Quaternion localRotation = Quaternion.identity;
        public Vector3 forward = new Vector3(0f, 0f, 1f);

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
    }

    public struct Vector3
    {
        public float x;
        public float y;
        public float z;
        public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
        public static Vector3 zero => new Vector3(0f, 0f, 0f);
        public static Vector3 one => new Vector3(1f, 1f, 1f);
        public static Vector3 up => new Vector3(0f, 1f, 0f);
        public static Vector3 operator +(Vector3 left, Vector3 right) => new Vector3(left.x + right.x, left.y + right.y, left.z + right.z);
        public static Vector3 operator *(Vector3 value, float factor) => new Vector3(value.x * factor, value.y * factor, value.z * factor);
    }

    public struct Quaternion
    {
        public float x;
        public float y;
        public float z;
        public float w;
        public static Quaternion identity => new Quaternion { w = 1f };
        public static Quaternion AngleAxis(float angle, Vector3 axis) => identity;
        public static Vector3 operator *(Quaternion rotation, Vector3 point) => point;
    }

    public struct Color
    {
        public float r;
        public float g;
        public float b;
        public float a;
        public Color(float r, float g, float b, float a) { this.r = r; this.g = g; this.b = b; this.a = a; }
        public static Color clear => default;
        public static Color Lerp(Color a, Color b, float t) => new Color(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, a.a + (b.a - a.a) * t);
        public static bool operator ==(Color left, Color right) =>
            left.r == right.r && left.g == right.g && left.b == right.b && left.a == right.a;
        public static bool operator !=(Color left, Color right) => !(left == right);
        public override bool Equals(object obj) => obj is Color other && this == other;
        public override int GetHashCode() => (r, g, b, a).GetHashCode();
    }

    public sealed class Shader
    {
        public string name;
        private static readonly Dictionary<string, int> Ids = new();
        private static readonly Dictionary<int, string> Names = new();
        public Shader(string name) { this.name = name; }
        public static int PropertyToID(string property)
        {
            if (!Ids.TryGetValue(property, out var id))
            {
                id = Ids.Count + 1;
                Ids[property] = id;
                Names[id] = property;
            }
            return id;
        }
        internal static string NameOf(int id) => Names.TryGetValue(id, out var name) ? name : "";
    }

    public class Texture
    {
        public string name = "";
    }

    public sealed class Texture2D : Texture { }

    public sealed class MaterialPropertyBlock
    {
        internal readonly Dictionary<int, Color> colors = new();
        internal readonly Dictionary<int, float> floats = new();
        internal readonly Dictionary<int, Texture> textures = new();
        public bool isEmpty => colors.Count == 0 && floats.Count == 0 && textures.Count == 0;
        public void Clear() { colors.Clear(); floats.Clear(); textures.Clear(); }
        public void SetColor(int id, Color value) => colors[id] = value;
        public Color GetColor(int id) => colors.TryGetValue(id, out var value) ? value : default;
        public void SetFloat(int id, float value) => floats[id] = value;
        public float GetFloat(int id) => floats.TryGetValue(id, out var value) ? value : 0f;
        public void SetTexture(int id, Texture value) => textures[id] = value;
        public Texture GetTexture(int id) => textures.TryGetValue(id, out var value) ? value : null;
        internal bool Contains(int id) => colors.ContainsKey(id) || floats.ContainsKey(id) || textures.ContainsKey(id);
    }

    public sealed class Material
    {
        public string name = "";
        public Shader shader;
        internal readonly HashSet<string> properties = new();
        internal readonly Dictionary<string, float> floats = new();
        internal readonly Dictionary<string, Color> colors = new();
        internal readonly Dictionary<string, Texture> textures = new();

        public Material(string shaderName) { shader = new Shader(shaderName); }
        public Material(Material source)
        {
            name = source.name;
            shader = source.shader;
            properties = new HashSet<string>(source.properties);
            foreach (var pair in source.floats) floats[pair.Key] = pair.Value;
            foreach (var pair in source.colors) colors[pair.Key] = pair.Value;
            foreach (var pair in source.textures) textures[pair.Key] = pair.Value;
        }
        public bool HasProperty(string property) => properties.Contains(property);
        public bool HasProperty(int id) => properties.Contains(Shader.NameOf(id));
        public void SetFloat(string property, float value) => floats[property] = value;
        public void SetFloat(int id, float value) => floats[Shader.NameOf(id)] = value;
        public float GetFloat(string property) => floats.TryGetValue(property, out var value) ? value : 0f;
        public void SetColor(string property, Color value) => colors[property] = value;
        public void SetColor(int id, Color value) => colors[Shader.NameOf(id)] = value;
        public Color GetColor(string property) => colors.TryGetValue(property, out var value) ? value : default;
        public Color GetColor(int id) => GetColor(Shader.NameOf(id));
        public void SetTexture(int id, Texture value) => textures[Shader.NameOf(id)] = value;
        public Texture GetTexture(string property) => textures.TryGetValue(property, out var value) ? value : null;
    }

    public class Renderer : Component
    {
        private Material[] shared = Array.Empty<Material>();
        internal MaterialPropertyBlock wideBlock;
        internal readonly Dictionary<int, MaterialPropertyBlock> slotBlocks = new();
        internal static int WideBlockWrites;
        internal static int SlotBlockWrites;
        public bool enabled = true;
        public Material[] sharedMaterials { get => shared; set => shared = value; }

        public bool HasPropertyBlock()
        {
            if (wideBlock != null && !wideBlock.isEmpty) return true;
            foreach (var block in slotBlocks.Values)
                if (!block.isEmpty) return true;
            return false;
        }
        public void GetPropertyBlock(MaterialPropertyBlock block)
        {
            block.Clear();
            if (wideBlock != null) CopyBlock(wideBlock, block);
        }
        public void SetPropertyBlock(MaterialPropertyBlock block)
        {
            WideBlockWrites++;
            wideBlock = block.isEmpty ? null : CloneBlock(block);
        }
        public void GetPropertyBlock(MaterialPropertyBlock block, int materialIndex)
        {
            block.Clear();
            if (slotBlocks.TryGetValue(materialIndex, out var stored)) CopyBlock(stored, block);
        }
        public void SetPropertyBlock(MaterialPropertyBlock block, int materialIndex)
        {
            SlotBlockWrites++;
            if (block.isEmpty) slotBlocks.Remove(materialIndex);
            else slotBlocks[materialIndex] = CloneBlock(block);
        }
        internal void CopyBlocksFrom(Renderer other)
        {
            wideBlock = other.wideBlock == null ? null : CloneBlock(other.wideBlock);
            slotBlocks.Clear();
            foreach (var pair in other.slotBlocks) slotBlocks[pair.Key] = CloneBlock(pair.Value);
        }
        private static MaterialPropertyBlock CloneBlock(MaterialPropertyBlock source)
        {
            var clone = new MaterialPropertyBlock();
            CopyBlock(source, clone);
            return clone;
        }
        private static void CopyBlock(MaterialPropertyBlock source, MaterialPropertyBlock destination)
        {
            foreach (var pair in source.colors) destination.colors[pair.Key] = pair.Value;
            foreach (var pair in source.floats) destination.floats[pair.Key] = pair.Value;
            foreach (var pair in source.textures) destination.textures[pair.Key] = pair.Value;
        }
    }

    public static class Debug
    {
        public static void Log(object message) => Console.WriteLine("[harness] " + message);
        public static void LogWarning(object message) => Console.WriteLine("[harness] warn: " + message);
        public static void LogError(object message) => Console.WriteLine("[harness] error: " + message);
    }
}

namespace UnityEngine.SceneManagement
{
    public struct Scene
    {
        public bool isLoaded => false;
        public bool IsValid() => false;
        public int rootCount => 0;
        public GameObject[] GetRootGameObjects() => Array.Empty<GameObject>();
    }

    public static class SceneManager
    {
        public static int sceneCount => 0;
        public static Scene GetSceneAt(int index) => default;
    }
}

namespace MonomiPark.SlimeRancher.Regions
{
    public sealed class RegionRegistry : MonoBehaviour
    {
        public enum RegionSetId { HOME = 0 }
        public RegionSetId GetCurrentRegionSetId() => RegionSetId.HOME;
    }
}

namespace SlimeBridge.Shared
{
    internal static class RegistrySnapshotFormat
    {
        public static string Quote(string value) =>
            value == null ? "null" : "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}

namespace SlimeBridge.SR1
{
    internal sealed class SpeciesRegistrar
    {
        internal static SpeciesRegistrar Current;
        public readonly Dictionary<string, int> NativeIds = new();
        public readonly Dictionary<string, float[][]> Palettes = new();
        public bool TryResolveNativeId(string canonicalId, out int nativeId) => NativeIds.TryGetValue(canonicalId, out nativeId);
        public readonly Dictionary<string, int> PlortIds = new();
        public bool TryResolvePlortId(string canonicalId, out int nativeId) => PlortIds.TryGetValue(canonicalId, out nativeId);
        public bool TryGetCanonical(int nativeId, out string canonicalId)
        {
            foreach (var pair in NativeIds)
            {
                if (pair.Value == nativeId) { canonicalId = pair.Key; return true; }
            }
            canonicalId = "";
            return false;
        }
        public bool TryGetPalette(string canonicalId, out float[] top, out float[] middle, out float[] bottom)
        {
            if (Palettes.TryGetValue(canonicalId, out var palette) && palette.Length == 3)
            {
                top = palette[0];
                middle = palette[1];
                bottom = palette[2];
                return true;
            }
            top = Array.Empty<float>();
            middle = Array.Empty<float>();
            bottom = Array.Empty<float>();
            return false;
        }
    }

    internal static class ImportedModelLoader
    {
        internal sealed class ImportedModel
        {
            public GameObject Root = null;
            public Vector3 RootScale = Vector3.one;
            public int VertexTotal;
            public int RendererCount;
        }
        internal static ImportedModel LastModel;
        internal static bool TryGet(string canonicalId, out ImportedModel model, out string problem)
        {
            problem = "";
            if (LastModel != null && LastModel.Root != null)
            {
                model = LastModel;
                return true;
            }
            model = null;
            problem = "no converted model in the local cache";
            return false;
        }
    }

    internal static class ImportedIdentityPatch
    {
        internal static readonly HashSet<int> Imported = new();
        internal static bool IsImportedId(int nativeId) => Imported.Contains(nativeId);
    }

    internal static class ImportedGameplayAnimation
    {
        internal static int AttachCalls;
        internal static bool EnsureAttached(GameObject actorRoot, GameObject importedVisual, out string problem)
        {
            AttachCalls++;
            problem = "";
            return true;
        }
    }

    internal sealed class ImportedMaterialOwner : MonoBehaviour
    {
        internal static int Owned;
        internal void Own(Material material) { Owned++; }
    }

    internal static class ImportedRubberBone
    {
        internal static int AttachCalls;
        internal static bool EnsureAttached(GameObject instance, GameObject visual, out string problem)
        {
            AttachCalls++;
            problem = "";
            return true;
        }
    }

    internal sealed class ImportedStoneFormMarker : MonoBehaviour { }

    internal static class ImportedStoneForm
    {
        // Fixture: this harness never provides a private stone-form cache. The surface mirrors
        // the production types the source-linked ImportedSpawner.cs calls (petrification wiring),
        // exactly like the ImportedGameplayAnimation stub above.
        internal sealed class StoneFormModel
        {
            public GameObject Root = null;
        }
        internal static bool TryGet(string canonicalId, out StoneFormModel model, out string problem)
        {
            model = null;
            problem = "";
            return false;
        }
    }

    internal class ImportedPetrification
    {
        internal static int AttachCalls;
        internal static bool EnsureAttached(GameObject actorRoot, string canonical, ImportedStoneForm.StoneFormModel stoneForm, out string problem)
        {
            AttachCalls++;
            problem = "";
            return true;
        }
    }

    internal sealed class LabSourceAnimationTrace : MonoBehaviour
    {
        internal static int AttachCalls;
        internal static void Attach(GameObject visual) => AttachCalls++;
    }

    internal static class BridgeEntry
    {
        internal static bool LabRestPoseRequested;
    }
}

class Identifiable : MonoBehaviour
{
    public enum Id { NONE = 0, PINK_SLIME = 3, TEST_IMPORTED = 4242 }
    public Id id;
}

class SlimeAppearanceApplicator : MonoBehaviour { }

class LookupDirector
{
    public GameObject Prefab;
    public GameObject GetPrefab(Identifiable.Id id) => Prefab;
}

class SlimeAppearanceStructure { public Material[] DefaultMaterials = new Material[0]; }

class SlimeAppearance { public SlimeAppearanceStructure[] Structures = new SlimeAppearanceStructure[0]; }

class SlimeDefinition { public SlimeAppearance[] AppearancesDefault = new SlimeAppearance[0]; }

class SlimeDefinitions
{
    public SlimeDefinition GetSlimeByIdentifiableId(Identifiable.Id id) => null;
}

class GameContext
{
    public static GameContext Instance = null;
    public LookupDirector LookupDirector = new();
    public SlimeDefinitions SlimeDefinitions = new();
}

class GameModel
{
    public int StartingActors;
    public void RegisterStartingActor(GameObject instance, MonomiPark.SlimeRancher.Regions.RegionRegistry.RegionSetId regionSetId) => StartingActors++;
}

class SceneContext
{
    public GameObject Player = null;
    public GameModel GameModel = new();
}
