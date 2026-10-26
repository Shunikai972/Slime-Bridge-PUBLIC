using System.Collections.Generic;
using UnityEngine;
#if SB_IL2CPP
using Il2CppInterop.Runtime.InteropTypes.Arrays;
#endif

namespace SbRuntime.UnityBuild
{
    /// <summary>The only place where Mono (SR1/SRML) and IL2CPP (SR2/MelonLoader) Unity bindings differ.</summary>
    internal static class Compat
    {
#if SB_IL2CPP
        public static void SetVertices(Mesh m, Vector3[] a) { m.vertices = new Il2CppStructArray<Vector3>(a); }
        public static void SetNormals(Mesh m, Vector3[] a) { m.normals = new Il2CppStructArray<Vector3>(a); }
        public static void SetTangents(Mesh m, Vector4[] a) { m.tangents = new Il2CppStructArray<Vector4>(a); }
        public static void SetColors(Mesh m, Color[] a) { m.colors = new Il2CppStructArray<Color>(a); }
        public static void SetBoneWeights(Mesh m, BoneWeight[] a) { m.boneWeights = new Il2CppStructArray<BoneWeight>(a); }
        public static void SetBindposes(Mesh m, Matrix4x4[] a) { m.bindposes = new Il2CppStructArray<Matrix4x4>(a); }
        public static void SetTriangles(Mesh m, int[] a, int sub) { m.SetTriangles(new Il2CppStructArray<int>(a), sub); }
        public static void SetUv(Mesh m, int ch, Vector2[] a)
        {
            var arr = new Il2CppStructArray<Vector2>(a);
            if (ch == 0) m.uv = arr; else if (ch == 1) m.uv2 = arr; else if (ch == 2) m.uv3 = arr; else m.uv4 = arr;
        }
        public static void SetBones(SkinnedMeshRenderer r, Transform[] t) { r.bones = new Il2CppReferenceArray<Transform>(t); }
        public static void SetMaterials(Renderer r, Material[] m) { r.sharedMaterials = new Il2CppReferenceArray<Material>(m); }
        public static bool LoadImage(Texture2D t, byte[] data) { return ImageConversion.LoadImage(t, new Il2CppStructArray<byte>(data), false); }
        public static void SetCurve(AnimationClip c, string path, System.Type type, string prop, Keyframe[] keys) { c.SetCurve(path, Il2CppInterop.Runtime.Il2CppType.From(type), prop, new AnimationCurve(new Il2CppStructArray<Keyframe>(keys))); }
        public static List<KeyValuePair<AnimationClip, AnimationClip>> GetOverrides(AnimatorOverrideController oc)
        {
            var l = new Il2CppSystem.Collections.Generic.List<Il2CppSystem.Collections.Generic.KeyValuePair<AnimationClip, AnimationClip>>();
            oc.GetOverrides(l);
            var r = new List<KeyValuePair<AnimationClip, AnimationClip>>();
            for (int i = 0; i < l.Count; i++) r.Add(new KeyValuePair<AnimationClip, AnimationClip>(l[i].Key, l[i].Value));
            return r;
        }
        public static void ApplyOverrides(AnimatorOverrideController oc, List<KeyValuePair<AnimationClip, AnimationClip>> pairs)
        {
            var l = new Il2CppSystem.Collections.Generic.List<Il2CppSystem.Collections.Generic.KeyValuePair<AnimationClip, AnimationClip>>();
            foreach (var p in pairs) l.Add(new Il2CppSystem.Collections.Generic.KeyValuePair<AnimationClip, AnimationClip>(p.Key, p.Value));
            oc.ApplyOverrides(l.TryCast<Il2CppSystem.Collections.Generic.IList<Il2CppSystem.Collections.Generic.KeyValuePair<AnimationClip, AnimationClip>>>());
        }
#else
        public static void SetVertices(Mesh m, Vector3[] a) { m.vertices = a; }
        public static void SetNormals(Mesh m, Vector3[] a) { m.normals = a; }
        public static void SetTangents(Mesh m, Vector4[] a) { m.tangents = a; }
        public static void SetColors(Mesh m, Color[] a) { m.colors = a; }
        public static void SetBoneWeights(Mesh m, BoneWeight[] a) { m.boneWeights = a; }
        public static void SetBindposes(Mesh m, Matrix4x4[] a) { m.bindposes = a; }
        public static void SetTriangles(Mesh m, int[] a, int sub) { m.SetTriangles(a, sub); }
        public static void SetUv(Mesh m, int ch, Vector2[] a)
        {
            if (ch == 0) m.uv = a; else if (ch == 1) m.uv2 = a; else if (ch == 2) m.uv3 = a; else m.uv4 = a;
        }
        public static void SetBones(SkinnedMeshRenderer r, Transform[] t) { r.bones = t; }
        public static void SetMaterials(Renderer r, Material[] m) { r.sharedMaterials = m; }
        public static bool LoadImage(Texture2D t, byte[] data) { return ImageConversion.LoadImage(t, data, false); }
        public static void SetCurve(AnimationClip c, string path, System.Type type, string prop, Keyframe[] keys) { c.SetCurve(path, type, prop, new AnimationCurve(keys)); }
        public static List<KeyValuePair<AnimationClip, AnimationClip>> GetOverrides(AnimatorOverrideController oc)
        {
            var l = new List<KeyValuePair<AnimationClip, AnimationClip>>();
            oc.GetOverrides(l);
            return l;
        }
        public static void ApplyOverrides(AnimatorOverrideController oc, List<KeyValuePair<AnimationClip, AnimationClip>> pairs) { oc.ApplyOverrides(pairs); }
#endif
    }
}
