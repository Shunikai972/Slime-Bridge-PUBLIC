using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Actor-free native appearance copies for sanctuary badges. Never instantiates an AI prefab.</summary>
    internal sealed class SanctuaryMiniVisual : MonoBehaviour
    {
        private readonly List<Material> _materials = new List<Material>();
        private readonly List<MeshFilter> _sizeReference = new List<MeshFilter>();
        internal IEnumerable<MeshFilter> SizeReference => _sizeReference;

        public void OnDestroy()
        {
            foreach (var material in _materials) if (material != null) UnityEngine.Object.Destroy(material);
            _materials.Clear();
        }

        internal static GameObject? Create(string slime, Transform parent, Vector3 position, Quaternion rotation, out int parts)
        {
            parts = 0;
            var game = GameContext.Instance;
            Identifiable.Id id;
            try { id = (Identifiable.Id)Enum.Parse(typeof(Identifiable.Id), slime + "_SLIME"); }
            catch (ArgumentException) { return null; }
            var definition = game.SlimeDefinitions.GetSlimeByIdentifiableId(id);
            var appearance = definition?.AppearancesDefault != null && definition.AppearancesDefault.Length > 0 ? definition.AppearancesDefault[0] : null;
            if (appearance?.Structures == null) return null;
            var native = game.LookupDirector.GetPrefab(id);
            var applicator = native != null ? native.GetComponentInChildren<SlimeAppearanceApplicator>(true) : null;
            var root = new GameObject("figure." + slime.ToLowerInvariant());
            root.transform.SetParent(parent, false);
            root.transform.position = position;
            root.transform.rotation = rotation;
            var owned = root.AddComponent<SanctuaryMiniVisual>();
            try
            {
                SlimeExpressionFace face = default;
                var hasFace = appearance.Face != null;
                if (hasFace) face = appearance.Face.GetExpressionFace(SlimeFace.SlimeExpression.Happy);
                foreach (var structure in appearance.Structures)
                {
                    if (structure?.Element?.Prefabs == null) continue;
                    var bodyBuilt = false;
                    // Native override/face arrays are parallel to Prefabs, not LOD levels.
                    for (var objectIndex = 0; objectIndex < structure.Element.Prefabs.Length; objectIndex++)
                    {
                        var prefab = structure.Element.Prefabs[objectIndex];
                        if (prefab == null || !(prefab.IgnoreLODIndex || prefab.LODIndex == 0) || structure.SupportsFaces && bodyBuilt) continue;
                        var declared = structure.DefaultMaterials;
                        if (structure.ElementMaterials != null && objectIndex < structure.ElementMaterials.Length && structure.ElementMaterials[objectIndex].OverrideDefaults)
                            declared = structure.ElementMaterials[objectIndex].Materials;
                        var attached = Attach(prefab.ParentBone, applicator, root.transform);
                        if (attached == null) continue; // missing native attachment: do not draw it at a guessed origin
                        var holder = new GameObject(prefab.name).transform;
                        holder.SetParent(attached, false);
                        CopyLocal(prefab.transform, holder);
                        var chains = new Dictionary<Transform, Transform> { [prefab.transform] = holder };
                        var renderers = new List<Renderer>();
                        renderers.AddRange(prefab.GetComponentsInChildren<SkinnedMeshRenderer>(true));
                        renderers.AddRange(prefab.GetComponentsInChildren<MeshRenderer>(true));
                        foreach (var source in renderers)
                        {
                            Mesh? mesh = null;
                            if (source is SkinnedMeshRenderer skin) mesh = skin.sharedMesh;
                            else { var filter = source.GetComponent<MeshFilter>(); if (filter != null) mesh = filter.sharedMesh; }
                            if (mesh == null) continue;
                            var node = CopyChain(source.transform, prefab.transform, holder, chains);
                            var materialSource = declared != null && declared.Length > 0 ? declared : source.sharedMaterials;
                            var materials = new List<Material>();
                            foreach (var material in materialSource)
                            {
                                if (material == null) continue;
                                var copy = new Material(material) { name = material.name + " (SlimeBridge figure)" };
                                if (structure.SupportsFaces)
                                {
                                    var palette = appearance.ColorPalette;
                                    if (copy.HasProperty("_TopColor")) copy.SetColor("_TopColor", palette.Top);
                                    if (copy.HasProperty("_MiddleColor")) copy.SetColor("_MiddleColor", palette.Middle);
                                    if (copy.HasProperty("_BottomColor")) copy.SetColor("_BottomColor", palette.Bottom);
                                }
                                owned._materials.Add(copy);
                                materials.Add(copy);
                            }
                            if (structure.SupportsFaces && hasFace && structure.FaceRules != null && objectIndex < structure.FaceRules.Length)
                            {
                                if (structure.FaceRules[objectIndex].ShowEyes && face.Eyes != null) materials.Add(face.Eyes);
                                if (structure.FaceRules[objectIndex].ShowMouth && face.Mouth != null) materials.Add(face.Mouth);
                            }
                            if (materials.Count == 0) continue;
                            var part = new GameObject(source.name + ".mesh");
                            part.transform.SetParent(node, false);
                            var output = part.AddComponent<MeshFilter>();
                            output.sharedMesh = mesh;
                            var renderer = part.AddComponent<MeshRenderer>();
                            renderer.sharedMaterials = materials.ToArray();
                            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                            // Transparent aura/core/glow never sets body size or lifts the badge off its pad.
                            var solid = structure.SupportsFaces;
                            if (!solid)
                                foreach (var material in materials) if (material.renderQueue < 3000) { solid = true; break; }
                            if (solid) owned._sizeReference.Add(output);
                            if (source.GetComponent<CameraFacingBillboard>() != null) part.AddComponent<MiniBillboard>();
                            parts++;
                            if (structure.SupportsFaces) bodyBuilt = true;
                        }
                    }
                }
                if (parts == 0 || owned._sizeReference.Count == 0) { UnityEngine.Object.Destroy(root); return null; }
                return root;
            }
            catch
            {
                UnityEngine.Object.Destroy(root);
                throw;
            }
        }

        private static Transform? Attach(SlimeAppearance.SlimeBone bone, SlimeAppearanceApplicator? native, Transform target)
        {
            // NONE is the unparented body, not a missing attachment to approximate.
            if (bone == SlimeAppearance.SlimeBone.None) return target;
            if (native?.Bones == null) return null;
            foreach (var mapping in native.Bones)
                if (mapping.Bone == bone && mapping.BoneObject != null)
                {
                    var source = mapping.BoneObject.transform;
                    // Authored mesh vertices and attachments share the LOD appearance space.
                    // The actor's Appearance offset must not be applied only to static parts.
                    var boundary = native.LODGroup != null ? native.LODGroup.transform : native.transform;
                    if (source != boundary && !source.IsChildOf(boundary)) return null;
                    return CopyChain(source, boundary, target, new Dictionary<Transform, Transform> { [boundary] = target });
                }
            return null;
        }

        private static void CopyLocal(Transform source, Transform destination)
        {
            destination.localPosition = source.localPosition;
            destination.localRotation = source.localRotation;
            destination.localScale = source.localScale;
        }

        private static Transform CopyChain(Transform source, Transform boundary, Transform target, Dictionary<Transform, Transform> copied)
        {
            if (copied.TryGetValue(source, out var existing)) return existing;
            if (source.parent == null || !source.IsChildOf(boundary)) throw new InvalidOperationException("appearance transform leaves its native boundary");
            var parent = CopyChain(source.parent, boundary, target, copied);
            var node = new GameObject(source.name).transform;
            node.SetParent(parent, false);
            CopyLocal(source, node);
            copied.Add(source, node);
            return node;
        }

        internal static float Fit(GameObject root, float diameter)
        {
            var marker = root.GetComponent<SanctuaryMiniVisual>();
            if (marker == null) throw new InvalidOperationException("missing owned mini visual");
            var local = LocalBounds(root.transform, marker.SizeReference);
            var extent = Mathf.Max(local.size.x, local.size.y, local.size.z);
            if (extent <= 0.001f) throw new InvalidOperationException("degenerate mini bounds");
            var scale = diameter / extent;
            root.transform.localScale = Vector3.one * scale;
            return scale;
        }

        internal static void StandOn(GameObject root, float groundY)
        {
            var marker = root.GetComponent<SanctuaryMiniVisual>();
            if (marker == null) return;
            var bounds = LocalBounds(root.transform, marker.SizeReference);
            var floor = root.transform.TransformPoint(new Vector3(bounds.center.x, bounds.min.y, bounds.center.z)).y;
            root.transform.position += Vector3.up * (groundY - floor);
        }

        private static Bounds LocalBounds(Transform root, IEnumerable<MeshFilter> filters)
        {
            var result = new Bounds();
            var found = false;
            foreach (var filter in filters)
            {
                if (filter == null || filter.sharedMesh == null) continue;
                var mesh = filter.sharedMesh;
                var bounds = mesh.bounds;
                var matrix = root.worldToLocalMatrix * filter.transform.localToWorldMatrix;
                for (var x = 0; x < 2; x++) for (var y = 0; y < 2; y++) for (var z = 0; z < 2; z++)
                {
                    var point = matrix.MultiplyPoint3x4(new Vector3(x == 0 ? bounds.min.x : bounds.max.x,
                        y == 0 ? bounds.min.y : bounds.max.y, z == 0 ? bounds.min.z : bounds.max.z));
                    if (!found) { result = new Bounds(point, Vector3.zero); found = true; }
                    else result.Encapsulate(point);
                }
            }
            if (!found) throw new InvalidOperationException("mini has no solid mesh bounds");
            return result;
        }
    }

    internal sealed class MiniBillboard : MonoBehaviour
    {
        public void LateUpdate()
        {
            var camera = Camera.main;
            if (camera != null) transform.rotation = camera.transform.rotation;
        }
    }
}
