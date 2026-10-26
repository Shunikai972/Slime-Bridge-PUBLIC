using System;
using System.Collections.Generic;
using System.Linq;
using MonomiPark.SlimeRancher.Regions;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    internal sealed class BuiltStatue
    {
        public string Slime = "";
        public SanctuaryStatue Spec = new SanctuaryStatue();
        public GameObject Root = null!;
        public GameObject? Toggle;
        public SanctuaryPlortCatcher Catcher = null!;
        public Light[] Lights = new Light[0];
    }

    internal sealed class BuiltPortal
    {
        public GameObject Root = null!;
        public SanctuaryPortalGlow Glow = null!;
        public SanctuaryPlayerTrigger Trigger = null!;
        public GameObject Membrane = null!;
    }

    /// <summary>
    /// Builds the walkway extension, island, colonnade, statues, gate and portal from a <see cref="SanctuaryLayout"/> out of
    /// native SR1 meshes and materials resolved by name. Everything lives under one dedicated world root and a Region
    /// registered with the SEA zone, so native actors (plorts, slimes) simulate here and the root can be culled like a cell.
    ///
    /// The gate (depositor, its tablet and pad) is built at once; the island itself is built lazily, the first time it is
    /// needed (the gate is paid, or access mode ALWAYS), so a locked sanctuary costs nothing.
    /// </summary>
    internal sealed class SanctuaryBuilder
    {
        private readonly SanctuaryLayout _layout;
        private readonly SanctuaryNative _native;
        private readonly List<string> _log = new List<string>();
        private readonly List<KeyValuePair<Transform, SanctuaryPiece>> _seeds = new List<KeyValuePair<Transform, SanctuaryPiece>>();
        private ZoneDirector? _sea;
        private Transform? _extensionGroups;
        private PuzzleSlot? _reference;
        public GameObject Root { get; private set; } = null!;
        /// <summary>Long walkway + island + statues + portal: active only once the Gold gate is paid (or always without a gate).</summary>
        public GameObject? Extension { get; private set; }
        public SanctuaryReveal? Reveal { get; private set; }
        public Region? IslandRegion { get; private set; }
        public readonly Dictionary<string, BuiltStatue> Statues = new Dictionary<string, BuiltStatue>(StringComparer.Ordinal);
        public BuiltStatue? GateDepositor { get; private set; }
        public GameObject? GateDoor { get; private set; }
        public GameObject? GateFrame { get; private set; }
        public GameObject? GateExtras { get; private set; }
        public BuiltPortal? Portal { get; private set; }
        public int PiecesBuilt { get; private set; }
        public bool ExtensionBuilt => Extension != null;
        public IList<string> Log => _log;

        public SanctuaryBuilder(SanctuaryLayout layout, SanctuaryNative native)
        {
            _layout = layout;
            _native = native;
        }

        private static Vector3 V(float[] v) => new Vector3(v[0], v[1], v[2]);
        private static Quaternion Q(float[] q) => new Quaternion(q[0], q[1], q[2], q[3]);

        public void Build(ZoneDirector? sea)
        {
            _native.EnsureIndexed();
            _sea = sea;
            Root = new GameObject(_layout.RootName);
            Root.transform.position = Vector3.zero;
            _reference = _native.ReferenceSlot();
            // The depositor's side pad (and its supports) stay at the dead end while the extension is not there.
            var always = new GameObject("bridge.always").transform;
            always.SetParent(Root.transform, false);
            foreach (var piece in _layout.Pieces.Where(IsAlwaysPresent)) BuildPiece(piece, always, false);
            BuildGate(_reference);
            _log.Add($"built {PiecesBuilt} gate pieces; the island is built when the gate opens; missing: {_native.Missing.Count}");
        }

        /// <summary>Builds the island once. Returns true when it was built by this call (the host wires the new statues and portal).</summary>
        public bool EnsureExtension()
        {
            if (Extension != null) return false;
            var started = Time.realtimeSinceStartup;
            Extension = new GameObject("Extension");
            Extension.transform.SetParent(Root.transform, false);
            Extension.SetActive(false);                        // nothing shows (or collides) until the host says so
            Reveal = Extension.AddComponent<SanctuaryReveal>();
            Reveal.enabled = false;
            _extensionGroups = Extension.transform;
            var plaza = V(_layout.Portal.Position);
            foreach (var group in _layout.Pieces.Where(p => !IsAlwaysPresent(p)).Select(p => p.Group).Distinct())
            {
                var holder = new GameObject(group).transform;
                holder.SetParent(Extension.transform, false);
                foreach (var piece in _layout.Pieces.Where(p => p.Group == group && !IsAlwaysPresent(p)))
                    BuildPiece(piece, holder, true);
            }
            var statues = new GameObject("Statues").transform;
            statues.SetParent(Extension.transform, false);
            foreach (var spec in _layout.Statues) Statues[spec.Slime] = BuildStatue(spec, statues, _reference);
            BuildPortal();
            if (_sea != null) IslandRegion = RegisterRegion(_sea);
            else _log.Add("no SEA zone director: island actors will not be simulated");
            RegisterRevealItems(plaza);
            _log.Add($"island built in {(Time.realtimeSinceStartup - started):F2}s: {PiecesBuilt} pieces, {Statues.Count} statues, portal; missing: {_native.Missing.Count}");
            return true;
        }

        private void RegisterRevealItems(Vector3 plaza)
        {
            if (Reveal == null) return;
            foreach (var seed in _seeds)
                Reveal.Add(seed.Key, seed.Value.Phase, seed.Value.Id, Vector2.Distance(new Vector2(seed.Key.position.x, seed.Key.position.z), new Vector2(plaza.x, plaza.z)), 0);
            var ordinal = 0;
            foreach (var built in Statues.Values.OrderBy(s => s.Spec.Id, StringComparer.Ordinal))
                Reveal.Add(built.Root.transform, "statue", built.Spec.Id, 0f, ordinal++);
            if (Portal != null)
            {
                ordinal = 0;
                foreach (var block in Portal.Glow.RingBlocks) Reveal.Add(block, "portal", "ring" + ordinal, 0f, ordinal++);
            }
        }

        /// <summary>The depositor's side pad and its supports stay at the dead end while the extension is not there.</summary>
        private static bool IsAlwaysPresent(SanctuaryPiece piece) =>
            piece.Id.StartsWith("bridge.depositor_pad", StringComparison.Ordinal) || piece.Id.StartsWith("bridge.padpost", StringComparison.Ordinal) ||
            piece.Id.StartsWith("bridge.padplinth", StringComparison.Ordinal);

        // ----- structure pieces
        private GameObject? BuildPiece(SanctuaryPiece spec, Transform parent, bool reveal)
        {
            var mesh = _native.Mesh(spec.Mesh);
            var materials = spec.Materials.Select(_native.Material).ToArray();
            if (mesh == null || materials.Any(m => m == null))
            {
                _log.Add("skipped " + spec.Id + " (missing native mesh/material)");
                return null;
            }
            var go = new GameObject(spec.Id);
            go.transform.SetParent(parent, false);
            go.transform.position = V(spec.Position);
            go.transform.rotation = Q(spec.Rotation);
            go.transform.localScale = V(spec.Scale);
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterials = materials!;
            if (!spec.CastShadows) renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            AddCollider(go, mesh, spec.Collider);
            if (reveal) _seeds.Add(new KeyValuePair<Transform, SanctuaryPiece>(go.transform, spec));
            PiecesBuilt++;
            return go;
        }

        private static void AddCollider(GameObject go, Mesh mesh, string kind)
        {
            if (kind == "none") return;
            if (kind == "mesh" && mesh.isReadable)
            {
                go.AddComponent<MeshCollider>().sharedMesh = mesh;
                return;
            }
            var box = go.AddComponent<BoxCollider>();
            box.center = mesh.bounds.center;
            box.size = mesh.bounds.size;
        }

        // ----- statues (rebuilt from the native PlortSwitch description)
        private BuiltStatue BuildStatue(SanctuaryStatue spec, Transform parent, PuzzleSlot? reference)
        {
            var root = new GameObject(spec.Id);
            root.transform.SetParent(parent, false);
            root.transform.position = V(spec.Position);
            root.transform.rotation = Q(spec.Rotation);
            var built = new BuiltStatue { Slime = spec.Slime, Spec = spec, Root = root };
            var byPath = new Dictionary<string, Transform>(StringComparer.Ordinal);
            var lights = new List<Light>();
            var rootNode = spec.Template.Nodes.FirstOrDefault(n => n.Path == spec.Template.Construct);
            byPath[spec.Template.Construct] = root.transform;
            if (rootNode != null) AddNodeComponents(root, rootNode, spec, lights);
            foreach (var node in spec.Template.Nodes.Where(n => n != rootNode).OrderBy(n => n.Path.Count(c => c == '/')))
            {
                var cut = node.Path.LastIndexOf('/');
                var parentPath = cut < 0 ? spec.Template.Construct : node.Path.Substring(0, cut);
                var holder = byPath.TryGetValue(parentPath, out var h) ? h : root.transform;
                var go = new GameObject(node.Name);
                go.transform.SetParent(holder, false);
                go.transform.localPosition = V(node.Position);
                go.transform.localRotation = Q(node.Rotation);
                go.transform.localScale = V(node.Scale);
                byPath[node.Path] = go.transform;
                if (node.Mesh.Length > 0) AddRenderer(go, node, spec);
                AddNodeComponents(go, node, spec, lights);
                if (node.Name == "Activate Toggle") built.Toggle = go;
            }
            built.Lights = lights.ToArray();
            built.Catcher = root.AddComponent<SanctuaryPlortCatcher>();
            built.Catcher.Key = spec.Slime;
            built.Catcher.CatchId = (Identifiable.Id)spec.PlortId;
            built.Catcher.Fx = reference != null ? reference.changeFX : null;
            return built;
        }

        private void AddRenderer(GameObject go, SanctuaryNode node, SanctuaryStatue spec)
        {
            var mesh = _native.Mesh(node.Mesh);
            if (mesh == null) { _log.Add("statue part " + node.Name + " has no native mesh " + node.Mesh); return; }
            Material? material = node.Name switch
            {
                "Statue" => StatueMaterial(spec),
                "Eyes" => EyesMaterial(spec),
                "plort" => _native.Material(spec.PlortDisplayMaterial),
                _ => node.Materials.Length > 0 ? _native.Material(node.Materials[0]) : null,
            };
            if (material == null) { _log.Add("statue part " + node.Name + " has no material"); return; }
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            go.AddComponent<MeshRenderer>().sharedMaterial = material;
        }

        private Material? StatueMaterial(SanctuaryStatue spec)
        {
            if (spec.Status == "native") return _native.Material(spec.StatueMaterial);
            var clone = _native.Clone(spec.StatueMaterial);
            if (clone == null) return null;
            var detail = spec.DetailTexture.Length > 0 ? _native.Texture(spec.DetailTexture) : null;
            if (detail != null && clone.HasProperty("_DetailTex")) clone.SetTexture("_DetailTex", detail);
            else if (spec.TintTop != null && clone.HasProperty("_Color"))
                clone.SetColor("_Color", Color.Lerp(Color.white, SanctuaryNative.ToColor(spec.TintTop), 0.45f));
            return clone;
        }

        private Material? EyesMaterial(SanctuaryStatue spec)
        {
            if (spec.Status == "native") return _native.Material(spec.EyesMaterial);
            var clone = _native.Clone(spec.EyesMaterial);
            if (clone == null) return null;
            if (spec.TintMiddle != null && clone.HasProperty("_GlassColor")) clone.SetColor("_GlassColor", SanctuaryNative.ToColor(spec.TintMiddle));
            if (spec.TintTop != null && clone.HasProperty("_SpecularColor")) clone.SetColor("_SpecularColor", SanctuaryNative.ToColor(spec.TintTop));
            return clone;
        }

        private static void AddNodeComponents(GameObject go, SanctuaryNode node, SanctuaryStatue spec, List<Light> lights)
        {
            foreach (var c in node.Components)
            {
                switch (c.Type)
                {
                    case "SphereCollider":
                        var sphere = go.AddComponent<SphereCollider>();
                        sphere.center = V(c.Center); sphere.radius = c.Radius; sphere.isTrigger = c.Trigger;
                        break;
                    case "BoxCollider":
                        var box = go.AddComponent<BoxCollider>();
                        box.center = V(c.Center); box.size = V(c.Size); box.isTrigger = c.Trigger;
                        break;
                    case "CapsuleCollider":
                        var capsule = go.AddComponent<CapsuleCollider>();
                        capsule.center = V(c.Center); capsule.radius = c.Radius; capsule.height = c.Height; capsule.isTrigger = c.Trigger;
                        break;
                    case "Light":
                        var light = go.AddComponent<Light>();
                        light.type = (LightType)c.LightType;
                        light.color = SanctuaryNative.ToColor(spec.LightColor ?? c.Color);
                        light.intensity = c.Intensity; light.range = c.Range; light.spotAngle = c.SpotAngle;
                        light.shadows = LightShadows.None;
                        lights.Add(light);
                        break;
                }
            }
        }

        // ----- gate: the gold depositor (a real gold slime on its pad), its tablet, and the (disabled) barrier parts
        private void BuildGate(PuzzleSlot? reference)
        {
            var gate = _layout.Gate;
            var holder = new GameObject("Gate").transform;
            holder.SetParent(Root.transform, false);
            GateDepositor = BuildStatue(gate.Statue, holder, reference);
            GateDepositor.Root.name = "gate.gold";
            GateFrame = BuildBarrierPart("gate.frame", gate.FrameMesh, gate.FrameMaterials, gate, holder, false);
            GateDoor = BuildBarrierPart("gate.door", gate.DoorMesh, gate.DoorMaterials, gate, holder, true);
            GateExtras = new GameObject("gate.extras");
            GateExtras.transform.SetParent(holder, false);
            if (gate.Figure != null) BuildFigure(GateDepositor, gate.Figure, GateExtras.transform);
            if (gate.Tablet != null) BuildTablet(gate.Tablet, GateExtras.transform);
        }

        private GameObject? BuildBarrierPart(string name, string meshName, string[] materialNames, SanctuaryGate gate, Transform parent, bool solid)
        {
            var mesh = _native.Mesh(meshName);
            var materials = materialNames.Select(_native.Material).ToArray();
            if (mesh == null || materials.Any(m => m == null)) { _log.Add("gate part " + name + " missing native assets"); return null; }
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.transform.position = V(gate.BarrierPosition);
            go.transform.rotation = Quaternion.Euler(0f, gate.BarrierYaw, 0f);
            var size = mesh.bounds.size;
            go.transform.localScale = new Vector3(gate.BarrierWidth / Mathf.Max(0.01f, size.x), gate.BarrierHeight / Mathf.Max(0.01f, size.y), solid ? 0.35f : 0.5f);
            // Keep the piece standing on the deck: lift by the distance from its origin to the bottom of its bounds.
            go.transform.position += Vector3.up * (-mesh.bounds.min.y * go.transform.localScale.y);
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            go.AddComponent<MeshRenderer>().sharedMaterials = materials!;
            if (solid)
            {
                var box = go.AddComponent<BoxCollider>();
                box.center = mesh.bounds.center; box.size = mesh.bounds.size;
            }
            PiecesBuilt++;
            return go;
        }

        /// <summary>
        /// The depositor statue is a frozen copy of the game's own gold slime: the appearance's body and face meshes with their
        /// native materials and the species palette (a Happy face). The stone statue it replaces keeps only its plort display,
        /// light and catch trigger. Fail-open: any problem leaves the stone statue.
        /// </summary>
        private void BuildFigure(BuiltStatue depositor, SanctuaryFigure spec, Transform parent)
        {
            try
            {
                var id = (Identifiable.Id)Enum.Parse(typeof(Identifiable.Id), spec.Slime + "_SLIME");
                var definition = GameContext.Instance.SlimeDefinitions.GetSlimeByIdentifiableId(id);
                var appearance = definition != null && definition.AppearancesDefault != null && definition.AppearancesDefault.Length > 0 ? definition.AppearancesDefault[0] : null;
                if (appearance == null || appearance.Structures == null) { _log.Add("figure: no appearance for " + spec.Slime); return; }
                var root = new GameObject("figure." + spec.Slime.ToLowerInvariant());
                root.transform.SetParent(parent, false);
                root.transform.position = depositor.Root.transform.position;
                root.transform.rotation = depositor.Root.transform.rotation;
                var palette = appearance.ColorPalette;
                SlimeExpressionFace face = default;
                var hasFace = false;
                try { face = appearance.Face.GetExpressionFace(SlimeFace.SlimeExpression.Happy); hasFace = true; } catch (Exception) { }
                var parts = 0;
                foreach (var structure in appearance.Structures)
                {
                    if (structure == null || structure.Element == null || structure.Element.Prefabs == null || structure.Element.Prefabs.Length == 0) continue;
                    var prefab = structure.Element.Prefabs[0];
                    var skinned = prefab != null ? prefab.GetComponentInChildren<SkinnedMeshRenderer>(true) : null;
                    if (skinned == null || skinned.sharedMesh == null) continue;
                    var materials = new List<Material>();
                    foreach (var m in structure.DefaultMaterials ?? new Material[0])
                    {
                        if (m == null) continue;
                        var copy = new Material(m) { name = m.name + " (SlimeBridge figure)" };
                        if (copy.HasProperty("_TopColor")) copy.SetColor("_TopColor", palette.Top);
                        if (copy.HasProperty("_MiddleColor")) copy.SetColor("_MiddleColor", palette.Middle);
                        if (copy.HasProperty("_BottomColor")) copy.SetColor("_BottomColor", palette.Bottom);
                        materials.Add(copy);
                    }
                    if (structure.SupportsFaces && hasFace && structure.FaceRules != null && structure.FaceRules.Length > 0)
                    {
                        if (structure.FaceRules[0].ShowEyes && face.Eyes != null) materials.Add(face.Eyes);
                        if (structure.FaceRules[0].ShowMouth && face.Mouth != null) materials.Add(face.Mouth);
                    }
                    if (materials.Count == 0) continue;
                    var part = new GameObject(structure.Element.Name ?? "part");
                    part.transform.SetParent(root.transform, false);
                    part.transform.localPosition = skinned.transform.localPosition;
                    part.transform.localRotation = skinned.transform.localRotation;
                    part.transform.localScale = skinned.transform.localScale;
                    part.AddComponent<MeshFilter>().sharedMesh = skinned.sharedMesh;
                    var renderer = part.AddComponent<MeshRenderer>();
                    renderer.sharedMaterials = materials.ToArray();
                    renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                    parts++;
                }
                if (parts == 0) { UnityEngine.Object.Destroy(root); _log.Add("figure: " + spec.Slime + " has no renderable part"); return; }
                // size and stand it on the pad: a slime is about 1 m wide; the statue is a little over twice that
                var bounds = new Bounds(root.transform.position, Vector3.zero);
                foreach (var r in root.GetComponentsInChildren<MeshRenderer>()) bounds.Encapsulate(r.bounds);
                var scale = 2.2f * spec.Scale / Mathf.Max(0.2f, bounds.size.x);
                root.transform.localScale = Vector3.one * scale;
                bounds = new Bounds(root.transform.position, Vector3.zero);
                foreach (var r in root.GetComponentsInChildren<MeshRenderer>()) bounds.Encapsulate(r.bounds);
                root.transform.position += Vector3.up * (depositor.Root.transform.position.y + spec.Lift - bounds.min.y);
                // hide the stone statue and its stone eyes
                foreach (var t in depositor.Root.GetComponentsInChildren<Transform>(true))
                    if (t != depositor.Root.transform && (t.name == "Statue" || t.name == "Eyes")) t.gameObject.SetActive(false);
                _log.Add($"figure: native {spec.Slime} slime statue ({parts} parts, scale {scale:F2})");
            }
            catch (Exception ex) { _log.Add("figure failed: " + ex.GetType().Name); }
        }

        /// <summary>A stone tablet carrying the "H" message, readable from the walkway side.</summary>
        private void BuildTablet(SanctuaryTablet spec, Transform parent)
        {
            try
            {
                // preferred: the game's own Hobson journal device (yellow H beacon), reading the sanctuary's message
                try
                {
                    if (SanctuaryJournal.Create(parent, V(spec.Position), Q(spec.Rotation).eulerAngles.y, spec, out var note) != null)
                    {
                        _log.Add("tablet: " + note);
                        PiecesBuilt++;
                        return;
                    }
                    _log.Add("tablet: " + note + "; using the stone slab");
                }
                catch (Exception ex) { _log.Add("journal device failed: " + ex.GetType().Name + "; using the stone slab"); }
                var mesh = _native.Mesh(spec.Mesh);
                var materials = spec.Materials.Select(_native.Material).ToArray();
                if (mesh == null || materials.Any(m => m == null)) { _log.Add("tablet: missing native slab"); return; }
                var tablet = new GameObject(spec.Id);
                tablet.transform.SetParent(parent, false);
                tablet.transform.position = V(spec.Position);
                tablet.transform.rotation = Q(spec.Rotation);
                tablet.transform.localScale = V(spec.Scale);
                tablet.AddComponent<MeshFilter>().sharedMesh = mesh;
                tablet.AddComponent<MeshRenderer>().sharedMaterials = materials!;
                var box = tablet.AddComponent<BoxCollider>();
                box.center = mesh.bounds.center; box.size = mesh.bounds.size;
                var font = Resources.GetBuiltinResource<Font>("Arial.ttf");
                var front = (mesh.bounds.max.z + 0.05f / Mathf.Max(0.01f, spec.Scale[2]));          // just in front of the slab
                var top = mesh.bounds.max.y * spec.Scale[1];
                AddText(tablet.transform, font, spec.Title, new Vector3(0f, top * 0.78f, front * spec.Scale[2]), 0.16f, 72, new Color(1f, 0.83f, 0.35f), FontStyle.Bold);
                var y = top * 0.48f;
                foreach (var line in spec.Lines)
                {
                    if (line.Length > 0) AddText(tablet.transform, font, line, new Vector3(0f, y, front * spec.Scale[2]), 0.029f, 48, new Color(0.96f, 0.9f, 0.7f), FontStyle.Normal);
                    y -= 0.2f;
                }
                AddText(tablet.transform, font, spec.Signature, new Vector3(0f, y - 0.12f, front * spec.Scale[2]), 0.05f, 56, new Color(1f, 0.83f, 0.35f), FontStyle.Italic);
                PiecesBuilt++;
            }
            catch (Exception ex) { _log.Add("tablet failed: " + ex.GetType().Name); }
        }

        private static void AddText(Transform parent, Font? font, string text, Vector3 localPosition, float characterSize, int fontSize, Color color, FontStyle style)
        {
            var go = new GameObject("text");
            go.transform.SetParent(parent, false);
            // the slab's lossy scale squashes the text: undo it, then place the text in the slab's local space
            var s = parent.localScale;
            go.transform.localScale = new Vector3(1f / s.x, 1f / s.y, 1f / s.z);
            go.transform.localPosition = new Vector3(localPosition.x / s.x, localPosition.y / s.y, localPosition.z / s.z);
            go.transform.localRotation = Quaternion.Euler(0f, 180f, 0f);        // TextMesh is read from behind its forward vector
            var mesh = go.AddComponent<TextMesh>();
            mesh.font = font; mesh.text = text; mesh.characterSize = characterSize; mesh.fontSize = fontSize; mesh.fontStyle = style;
            mesh.anchor = TextAnchor.MiddleCenter; mesh.alignment = TextAlignment.Center; mesh.color = color;
            var renderer = go.GetComponent<MeshRenderer>();
            if (font != null) renderer.sharedMaterial = font.material;
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            renderer.receiveShadows = false;
        }

        // ----- portal
        private void BuildPortal()
        {
            var spec = _layout.Portal;
            var root = new GameObject("Portal");
            root.transform.SetParent(Extension!.transform, false);
            root.transform.position = V(spec.Position);
            root.transform.rotation = Quaternion.Euler(0f, spec.Yaw, 0f);
            var block = _native.Mesh(spec.RingMesh);
            var blockMaterials = spec.RingMaterials.Select(_native.Material).ToArray();
            var center = new Vector3(0f, spec.RingCenterHeight, 0f);
            var blocks = new List<Transform>();
            if (block != null && blockMaterials.All(m => m != null))
            {
                for (var i = 0; i < spec.RingCount; i++)
                {
                    var angle = 2f * Mathf.PI * i / spec.RingCount;
                    var go = new GameObject("ring" + i);
                    go.transform.SetParent(root.transform, false);
                    go.transform.localPosition = center + new Vector3(Mathf.Cos(angle), Mathf.Sin(angle), 0f) * spec.RingRadius;
                    go.transform.localRotation = Quaternion.Euler(0f, 0f, angle * Mathf.Rad2Deg);
                    go.transform.localScale = Vector3.one * spec.RingScale;
                    go.AddComponent<MeshFilter>().sharedMesh = block;
                    var blockRenderer = go.AddComponent<MeshRenderer>();
                    blockRenderer.sharedMaterials = blockMaterials!;
                    blockRenderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                    var box = go.AddComponent<BoxCollider>();
                    box.center = block.bounds.center; box.size = block.bounds.size;
                    blocks.Add(go.transform);
                }
            }
            else _log.Add("portal ring has no native block mesh");
            var glow = root.AddComponent<SanctuaryPortalGlow>();
            glow.RingBlocks = blocks.ToArray();
            glow.RingScales = blocks.Select(b => b.localScale).ToArray();
            // Turning light layers drawn with a native additive glow material and procedural textures; the layout's own membrane
            // material is the fallback when that native material is missing from this build.
            var baseMaterial = _native.Clone("fxChromaticGlow");
            var membrane = new GameObject("membrane");
            membrane.transform.SetParent(root.transform, false);
            membrane.transform.localPosition = center;
            Renderer? renderer = null;
            var diameter = spec.MembraneRadius * 2f;
            if (baseMaterial != null)
            {
                var layers = new[]
                {
                    new { Texture = PortalTextures.Spiral(256, 3, 1.1f, new Color(1f, 0.82f, 1f), new Color(0.55f, 0.25f, 0.95f)), Tint = new Color(1f, 1f, 1f, 0.95f), Speed = -42f, Pulse = 0.03f, Scale = 1.0f },
                    new { Texture = PortalTextures.Spiral(256, 5, -0.8f, new Color(0.7f, 0.95f, 1f), new Color(0.9f, 0.35f, 0.95f)), Tint = new Color(1f, 1f, 1f, 0.7f), Speed = 63f, Pulse = 0.05f, Scale = 0.82f },
                    new { Texture = PortalTextures.Glow(128, new Color(1f, 0.9f, 1f)), Tint = new Color(1f, 0.95f, 1f, 0.9f), Speed = 0f, Pulse = 0.12f, Scale = 0.42f },
                };
                for (var i = 0; i < layers.Length; i++)
                {
                    var material = new Material(baseMaterial) { name = "SlimeBridge portal layer " + i, mainTexture = layers[i].Texture };
                    var spinner = new GameObject("layer" + i);
                    spinner.transform.SetParent(membrane.transform, false);
                    spinner.transform.localPosition = new Vector3(0f, 0f, 0.02f * i);
                    foreach (var yaw in new[] { 0f, 180f })
                    {
                        var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
                        quad.name = "membrane-" + i + (yaw == 0f ? "" : "-back");
                        UnityEngine.Object.Destroy(quad.GetComponent<Collider>());
                        quad.transform.SetParent(spinner.transform, false);
                        quad.transform.localRotation = Quaternion.Euler(0f, yaw, 0f);
                        quad.transform.localScale = new Vector3(diameter, diameter, 1f);
                        var quadRenderer = quad.GetComponent<MeshRenderer>();
                        quadRenderer.sharedMaterial = material;
                        quadRenderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                        quadRenderer.receiveShadows = false;
                        renderer ??= quadRenderer;
                    }
                    glow.Layers.Add(new SanctuarySwirlLayer { Spinner = spinner.transform, Material = material, Tint = layers[i].Tint,
                        DegreesPerSecond = layers[i].Speed, PulseAmount = layers[i].Pulse, PulseSpeed = 1.1f + 0.4f * i, BaseScale = layers[i].Scale });
                }
            }
            else
            {
                var flat = GameObject.CreatePrimitive(PrimitiveType.Quad);
                flat.name = "membrane-flat";
                UnityEngine.Object.Destroy(flat.GetComponent<Collider>());
                flat.transform.SetParent(membrane.transform, false);
                flat.transform.localScale = Vector3.one * diameter;
                var fallback = _native.Clone(spec.MembraneMaterial) ?? _native.Clone(spec.MembraneFallback);
                renderer = flat.GetComponent<MeshRenderer>();
                if (fallback != null) renderer.sharedMaterial = fallback;
                var back = UnityEngine.Object.Instantiate(flat, membrane.transform);
                back.name = "membrane-flat-back";
                back.transform.localRotation = Quaternion.Euler(0f, 180f, 0f);
            }
            var lamp = new GameObject("lamp").AddComponent<Light>();
            lamp.transform.SetParent(root.transform, false);
            lamp.transform.localPosition = center;
            lamp.type = LightType.Point; lamp.range = 14f; lamp.intensity = 0f; lamp.color = new Color(0.55f, 0.45f, 1f);
            lamp.shadows = LightShadows.None;
            lamp.enabled = false;
            var trigger = new GameObject("trigger");
            trigger.transform.SetParent(root.transform, false);
            trigger.transform.localPosition = center;
            var sphere = trigger.AddComponent<SphereCollider>();
            sphere.isTrigger = true; sphere.radius = spec.TriggerRadius;
            var playerTrigger = trigger.AddComponent<SanctuaryPlayerTrigger>();
            glow.Target = renderer; glow.Lamp = lamp; glow.MembraneRoot = membrane.transform;
            glow.Snap(false);                                  // dormant: dark ring, no membrane
            Portal = new BuiltPortal { Root = root, Glow = glow, Trigger = playerTrigger, Membrane = membrane };
        }

        // ----- region
        private Region? RegisterRegion(ZoneDirector sea)
        {
            // Region.Awake reads the zone from its parents and OnEnable registers `bounds`: build it inactive, fill it, then enable.
            var holder = new GameObject("SlimeBridge Sanctuary Region");
            holder.SetActive(false);
            holder.transform.SetParent(sea.transform, false);
            // Every native Region carries a CellDirector: actors read `region.cellDir` (FindConsumable, the
            // ambiance director, the cell index). Without one, any slime or the player inside this region threw a
            // NullReferenceException every frame. It is added BEFORE the Region so Region.Awake finds it; it never
            // spawns or culls anything (no spawners, zero targets) and borrows the ambiance zone of the surrounding
            // native cells so the player's sound/lighting stay the same on the island.
            var cell = holder.AddComponent<CellDirector>();
            cell.targetSlimeCount = 0;
            cell.targetAnimalCount = 0;
            cell.minPerSpawn = 0;
            cell.maxPerSpawn = 0;
            cell.minAnimalPerSpawn = 0;
            cell.maxAnimalPerSpawn = 0;
            cell.cullSlimesLimit = 100000;
            cell.cullAnimalsLimit = 100000;
            foreach (var native in sea.GetComponentsInChildren<CellDirector>(true))
            {
                if (native == null || native == cell) continue;
                cell.ambianceZone = native.ambianceZone;
                break;
            }
            var region = holder.AddComponent<Region>();
            region.root = Root;
            var corners = _layout.Pieces.Select(p => V(p.Position)).Concat(_layout.Statues.Select(s => V(s.Position))).ToList();
            corners.Add(V(_layout.Portal.Position));
            var bounds = new Bounds(corners[0], Vector3.zero);
            foreach (var c in corners) bounds.Encapsulate(c);
            bounds.Expand(new Vector3(30f, 40f, 30f));
            region.bounds = bounds;
            holder.SetActive(true);
            return region;
        }

        public void Destroy()
        {
            if (IslandRegion != null) UnityEngine.Object.Destroy(IslandRegion.gameObject);
            if (Root != null) UnityEngine.Object.Destroy(Root);
        }
    }
}
