using System;
using System.Collections.Generic;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using SlimeBridge.Shared;
using UnityEngine;
namespace SlimeBridge.SR2
{
    /// <summary>IL2CPP-only boundary. Never silently downgrades a skinned part to static.</summary>
    internal static class Sr2ImportedModelLoader
    {
        internal static Sr2ImportedVisualMarker Build(GameObject actor, NativeMeshCache cache,
            Func<NativeMeshPart, Sr2ImportedVisualMarker, Material[]> materials)
        {
            Sr2ImportedVisualMarker.Register();
            var root = new GameObject(Sr2ImportedVisualMarker.RootName);
            root.SetActive(false);
            Sr2ImportedVisualMarker? owner = null;
            try
            {
                root.transform.SetParent(actor.transform, false);
                Apply(root.transform, cache.Document.VisualTransform);
                var scale = root.transform.localScale;
                var parentScale = actor.transform.localScale;
                if (parentScale.x == 0 || parentScale.y == 0 || parentScale.z == 0) throw new FormatException("zero actor scale");
                root.transform.localScale = new Vector3(scale.x * cache.Document.PrefabScale / parentScale.x,
                    scale.y * cache.Document.PrefabScale / parentScale.y, scale.z * cache.Document.PrefabScale / parentScale.z);
                owner = root.AddComponent<Sr2ImportedVisualMarker>();
                owner.CanonicalId = cache.CanonicalId; owner.ModelSha256 = cache.ModelSha256;
                var model = cache.Document;
                var bones = new Transform[model.Bones.Length];
                var lookup = new Dictionary<string, Transform>(StringComparer.Ordinal);
                for (var i = 0; i < bones.Length; i++)
                {
                    bones[i] = new GameObject(model.Bones[i].Name).transform;
                    // Parent immediately: a later failed material/mesh allocation must not orphan bones.
                    bones[i].SetParent(root.transform, false);
                    lookup.Add(model.Bones[i].Name, bones[i]);
                }
                for (var i = 0; i < bones.Length; i++)
                {
                    bones[i].SetParent(model.Bones[i].Parent < 0 ? root.transform : bones[model.Bones[i].Parent], false);
                    Apply(bones[i], model.Bones[i].Transform);
                }
                var renderers = new List<Renderer>();
                foreach (var part in model.Parts)
                {
                    if (!NativeMeshVisualPolicy.InScope(part.Role)) { owner.IgnoredEffectParts++; continue; }
                    if (part.Materials.Length == 0 || part.Triangles.Length == 0) throw new FormatException("part has no material/triangle slots");
                    var obj = new GameObject("SourcePart_" + renderers.Count + "_" + part.Role);
                    obj.transform.SetParent(root.transform, false); Apply(obj.transform, part.Transform);
                    var mesh = owner.Own(new Mesh()); mesh.name = part.MeshName;
                    if (part.Vertices.Length > 65535) mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
                    mesh.SetVertices(Vectors3(part.Vertices));
                    if (part.Normals.Length > 0) mesh.SetNormals(Vectors3(part.Normals));
                    if (part.Uv.Length > 0) mesh.SetUVs(0, Vectors2(part.Uv));
                    if (part.Uv2.Length > 0) mesh.SetUVs(1, Vectors2(part.Uv2));
                    if (part.Tangents.Length > 0)
                    {
                        var values = new Il2CppStructArray<Vector4>(part.Tangents.Length);
                        for (var i = 0; i < values.Length; i++) values[i] = new Vector4(part.Tangents[i][0], part.Tangents[i][1], part.Tangents[i][2], part.Tangents[i][3]);
                        mesh.tangents = values;
                    }
                    if (part.Colors.Length > 0)
                    {
                        var values = new Il2CppStructArray<Color>(part.Colors.Length);
                        for (var i = 0; i < values.Length; i++) values[i] = Color4(part.Colors[i]);
                        mesh.colors = values;
                    }
                    mesh.subMeshCount = part.Triangles.Length;
                    for (var i = 0; i < part.Triangles.Length; i++) mesh.SetTriangles(new Il2CppStructArray<int>(part.Triangles[i]), i, false);
                    if (part.Normals.Length == 0) mesh.RecalculateNormals();
                    mesh.RecalculateBounds();
                    Renderer renderer;
                    if (part.Skinned)
                    {
                        var poses = new Il2CppStructArray<Matrix4x4>(part.BindPoses.Length);
                        for (var i = 0; i < poses.Length; i++)
                        {
                            var matrix = new Matrix4x4();
                            for (var r = 0; r < 4; r++) for (var c = 0; c < 4; c++) matrix[r,c] = part.BindPoses[i][r * 4 + c];
                            poses[i] = matrix;
                        }
                        mesh.bindposes = poses;
                        var weights = new Il2CppStructArray<BoneWeight>(part.BoneWeights.Length);
                        for (var i = 0; i < weights.Length; i++)
                        {
                            var w = part.BoneWeights[i];
                            weights[i] = new BoneWeight {weight0=w[0],weight1=w[1],weight2=w[2],weight3=w[3],
                                boneIndex0=(int)w[4],boneIndex1=(int)w[5],boneIndex2=(int)w[6],boneIndex3=(int)w[7]};
                        }
                        mesh.boneWeights = weights;
                        var skin = obj.AddComponent<SkinnedMeshRenderer>();
                        var palette = new Il2CppReferenceArray<Transform>(part.SkinBones.Length);
                        for (var i = 0; i < palette.Length; i++) palette[i] = lookup[part.SkinBones[i]];
                        skin.bones = palette; skin.rootBone = lookup[part.RootBone]; skin.sharedMesh = mesh;
                        skin.updateWhenOffscreen = true;
                        for (var i = 0; i < palette.Length; i++)
                            if (skin.bones[i] == null || skin.bones[i].Pointer != palette[i].Pointer)
                                throw new InvalidOperationException("native bone pointer readback failed");
                        if (skin.bones.Length != part.SkinBones.Length || skin.rootBone == null || skin.sharedMesh == null ||
                            skin.sharedMesh.boneWeights.Length != part.Vertices.Length) throw new InvalidOperationException("skinned native binding readback failed");
                        renderer = skin;
                    }
                    else
                    {
                        obj.AddComponent<MeshFilter>().sharedMesh = mesh;
                        renderer = obj.AddComponent<MeshRenderer>();
                    }
                    var slots = materials(part, owner);
                    // Unity reuses the final submesh for surplus authored material passes
                    // (2021.3 Skinned Mesh Renderer manual); Rad has 1 submesh and 3 passes.
                    if (slots.Length < mesh.subMeshCount) throw new FormatException("material slot count below submesh count");
                    renderer.sharedMaterials = new Il2CppReferenceArray<Material>(slots);
                    renderer.enabled = part.Enabled;
                    renderers.Add(renderer);
                }
                if (renderers.Count == 0 || !renderers.Exists(value => value.enabled)) throw new FormatException("source model has no enabled milestone parts");
                owner.Renderers = renderers.ToArray();
                // Same layer as the actor (SR1 does the same): on SR2 1.x the camera skips the Default layer for actors, so a
                // visual left there cast its shadow but was never drawn.
                SetLayer(root.transform, actor.layer);
                Sr2ImportedAnimation.Attach(root, model, owner);
                return owner;
            }
            catch
            {
                if (owner != null) owner.ReleaseOwned();
                UnityEngine.Object.Destroy(root);
                throw;
            }
        }
        internal static void Apply(Transform transform, NativeMeshTransform value)
        {
            transform.localPosition = Vector3Of(value.Position);
            transform.localRotation = new Quaternion(value.Rotation[0],value.Rotation[1],value.Rotation[2],value.Rotation[3]);
            transform.localScale = Vector3Of(value.Scale);
        }
        internal static Color Color4(float[] v) => new Color(v[0],v[1],v[2],v[3]);
        private static void SetLayer(Transform root, int layer)
        {
            var stack = new Stack<Transform>();
            stack.Push(root);
            var visited = 0;
            while (stack.Count > 0 && visited++ < 4096)
            {
                var current = stack.Pop();
                current.gameObject.layer = layer;
                for (var i = 0; i < current.childCount; i++) stack.Push(current.GetChild(i));
            }
        }
        private static Vector3 Vector3Of(float[] v) => new Vector3(v[0],v[1],v[2]);
        private static Il2CppStructArray<Vector3> Vectors3(float[][] values)
        {
            var result = new Il2CppStructArray<Vector3>(values.Length);
            for (var i = 0; i < values.Length; i++) result[i] = Vector3Of(values[i]);
            return result;
        }
        private static Il2CppStructArray<Vector2> Vectors2(float[][] values)
        {
            var result = new Il2CppStructArray<Vector2>(values.Length);
            for (var i = 0; i < values.Length; i++) result[i] = new Vector2(values[i][0],values[i][1]);
            return result;
        }
    }
}
