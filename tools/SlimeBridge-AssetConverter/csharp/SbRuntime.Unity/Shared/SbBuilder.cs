using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
#if SB_IL2CPP
using Il2CppInterop.Runtime.InteropTypes.Arrays;
#endif

namespace SbRuntime.UnityBuild
{
    public sealed class SbBuildOptions
    {
        /// <summary>LOD to instantiate (falls back to LOD 0). The package carries all LODs.</summary>
        public int Lod = 0;
        /// <summary>Native material of the TARGET game with this name (recipe "template"): cloned so every HDRP/Built-in infrastructure property comes from the game itself.</summary>
        public Func<string, Material> FindTemplate;
        /// <summary>Shader lookup (default: Shader.Find).</summary>
        public Func<string, Shader> FindShader;
        public Transform Parent;
    }

    public sealed class BuiltSpecies
    {
        public GameObject Root;                         // species root
        public Transform Appearance;                    // rig root (animator target)
        public Dictionary<string, Transform> Bones = new Dictionary<string, Transform>();     // rig path -> transform
        public Dictionary<string, Material> Materials = new Dictionary<string, Material>();   // recipe id -> material
        public Dictionary<string, AnimationClip> Clips = new Dictionary<string, AnimationClip>();
        public List<Renderer> Renderers = new List<Renderer>();
        public List<string> Warnings = new List<string>();
    }

    /// <summary>Builds Unity objects from an sbconv runtime package. Works unchanged under SRML (SR1, Mono) and MelonLoader (SR2, IL2CPP via SB_IL2CPP).</summary>
    public static class SbBuilder
    {
        public static BuiltSpecies Build(SbPackage pkg, SbBuildOptions opt = null)
        {
            opt = opt ?? new SbBuildOptions();
            var b = new BuiltSpecies();
            b.Root = new GameObject("sb_" + pkg.CanonicalId);
            if (opt.Parent != null) b.Root.transform.SetParent(opt.Parent, false);
            BuildRig(pkg, b);
            foreach (var rec in pkg.Recipes.Values) b.Materials[rec.Id] = MakeMaterial(pkg, rec, opt, b);
            foreach (var part in pkg.Parts) BuildPart(pkg, part, opt, b);
            foreach (var clip in pkg.Clips) b.Clips[clip.Name] = MakeClip(clip, b);
            return b;
        }

        static void BuildRig(SbPackage pkg, BuiltSpecies b)
        {
            var objs = new Transform[pkg.Rig.Count];
            for (int i = 0; i < pkg.Rig.Count; i++)
            {
                var n = pkg.Rig[i];
                var go = new GameObject(n.Name);
                var tr = go.transform;
                tr.SetParent(n.Parent < 0 ? b.Root.transform : objs[n.Parent], false);
                tr.localPosition = new Vector3(n.P[0], n.P[1], n.P[2]);
                tr.localRotation = new Quaternion(n.Q[0], n.Q[1], n.Q[2], n.Q[3]);
                tr.localScale = new Vector3(n.S[0], n.S[1], n.S[2]);
                objs[i] = tr;
                b.Bones[n.Path] = tr;
                if (i == 0) b.Appearance = tr;
            }
            if (b.Appearance == null) throw new InvalidDataException("package has no rig root");
        }

        // ---- materials -------------------------------------------------------------------------------------
        static Material MakeMaterial(SbPackage pkg, SbRecipe rec, SbBuildOptions opt, BuiltSpecies b)
        {
            Material m = null;
            if (rec.TemplateName != null && opt.FindTemplate != null)
            {
                var t = opt.FindTemplate(rec.TemplateName);
                if (t != null) m = new Material(t);
            }
            if (m == null)
            {
                Shader sh = rec.Shader == null ? null : (opt.FindShader != null ? opt.FindShader(rec.Shader) : Shader.Find(rec.Shader));
                if (sh == null)
                {
                    b.Warnings.Add("material " + rec.SourceName + ": shader '" + rec.Shader + "' not found; using Standard placeholder");
                    sh = Shader.Find("Standard");
                }
                m = new Material(sh);
            }
            m.name = rec.SourceName;
            foreach (var k in rec.Keywords) m.EnableKeyword(k);
            foreach (var kv in rec.Floats) if (m.HasProperty(kv.Key)) m.SetFloat(kv.Key, kv.Value);
            foreach (var kv in rec.Colors) if (m.HasProperty(kv.Key)) m.SetColor(kv.Key, new Color(kv.Value[0], kv.Value[1], kv.Value[2], kv.Value[3]));
            foreach (var t in rec.Textures)
            {
                if (!m.HasProperty(t.Property)) { b.Warnings.Add("material " + rec.SourceName + ": target shader has no property " + t.Property); continue; }
                var tex = new Texture2D(2, 2, TextureFormat.RGBA32, true, !t.Srgb);
                if (!Compat.LoadImage(tex, File.ReadAllBytes(Path.Combine(pkg.Directory, t.File)))) { b.Warnings.Add("texture " + t.File + " could not be decoded"); continue; }
                tex.name = Path.GetFileNameWithoutExtension(t.File);
                m.SetTexture(t.Property, tex);
                m.SetTextureScale(t.Property, new Vector2(t.Scale[0], t.Scale[1]));
                m.SetTextureOffset(t.Property, new Vector2(t.Offset[0], t.Offset[1]));
            }
            return m;
        }

        // ---- parts -------------------------------------------------------------------------------------------
        static void BuildPart(SbPackage pkg, SbPart part, SbBuildOptions opt, BuiltSpecies b)
        {
            SbLod lod = null;
            foreach (var l in part.Lods) if (l.Lod == opt.Lod) lod = l;
            if (lod == null) foreach (var l in part.Lods) if (l.Lod == 0) lod = l;
            if (lod == null) return;
            foreach (var r in lod.Renderers)
            {
                var data = pkg.LoadMesh(r);
                var mesh = MakeMesh(data);
                var mats = new Material[r.MaterialSlots.Length];
                for (int i = 0; i < mats.Length; i++) mats[i] = b.Materials[r.MaterialSlots[i]];
                var sharedMats = new Material[mesh.subMeshCount];
                for (int i = 0; i < sharedMats.Length; i++) sharedMats[i] = mats.Length == 0 ? null : mats[Math.Min(i, mats.Length - 1)];

                var go = new GameObject(part.Element + ":" + r.Node);
                Renderer renderer;
                if (r.Skinned && data.Skinned && lod.Bones.Length == data.BindPoses.Length / 16)
                {
                    go.transform.SetParent(b.Appearance, false);
                    var smr = go.AddComponent<SkinnedMeshRenderer>();
                    var bones = new Transform[lod.Bones.Length];
                    for (int i = 0; i < bones.Length; i++) bones[i] = b.Bones[lod.Bones[i]];
                    Compat.SetBones(smr, bones);
                    smr.rootBone = lod.RootBone != null && b.Bones.ContainsKey(lod.RootBone) ? b.Bones[lod.RootBone] : bones[0];
                    smr.sharedMesh = mesh;
                    smr.updateWhenOffscreen = true;
                    renderer = smr;
                }
                else
                {
                    Transform parent = lod.ParentBone != null && b.Bones.ContainsKey(lod.ParentBone) ? b.Bones[lod.ParentBone] : b.Appearance;
                    go.transform.SetParent(parent, false);
                    go.AddComponent<MeshFilter>().sharedMesh = mesh;
                    renderer = go.AddComponent<MeshRenderer>();
                }
                // A skinned part is positioned by its bones and bind poses: the part prefab's own root transform is only an editor-layout offset
                // (e.g. Batty ears at z = 1.5) that the game ignores, so skinned renderers stay at identity under the rig root.
                if (renderer is SkinnedMeshRenderer)
                {
                    go.transform.localPosition = Vector3.zero; go.transform.localRotation = Quaternion.identity; go.transform.localScale = Vector3.one;
                }
                else
                {
                    go.transform.localPosition = new Vector3(r.P[0], r.P[1], r.P[2]);
                    go.transform.localRotation = new Quaternion(r.Q[0], r.Q[1], r.Q[2], r.Q[3]);
                    go.transform.localScale = new Vector3(r.S[0], r.S[1], r.S[2]);
                }
                Compat.SetMaterials(renderer, sharedMats);
                b.Renderers.Add(renderer);
            }
        }

        public static Mesh MakeMesh(SbMeshData d)
        {
            var mesh = new Mesh { name = d.Name };
            int n = d.VertexCount;
            var v = new Vector3[n];
            for (int i = 0; i < n; i++) v[i] = new Vector3(d.Positions[i * 3], d.Positions[i * 3 + 1], d.Positions[i * 3 + 2]);
            Compat.SetVertices(mesh, v);
            if (d.Normals != null) { var a = new Vector3[n]; for (int i = 0; i < n; i++) a[i] = new Vector3(d.Normals[i * 3], d.Normals[i * 3 + 1], d.Normals[i * 3 + 2]); Compat.SetNormals(mesh, a); }
            if (d.Tangents != null) { var a = new Vector4[n]; for (int i = 0; i < n; i++) a[i] = new Vector4(d.Tangents[i * 4], d.Tangents[i * 4 + 1], d.Tangents[i * 4 + 2], d.Tangents[i * 4 + 3]); Compat.SetTangents(mesh, a); }
            for (int ch = 0; ch < 4; ch++)
                if (d.Uv[ch] != null) { var a = new Vector2[n]; for (int i = 0; i < n; i++) a[i] = new Vector2(d.Uv[ch][i * 2], d.Uv[ch][i * 2 + 1]); Compat.SetUv(mesh, ch, a); }
            if (d.Colors != null) { var a = new Color[n]; for (int i = 0; i < n; i++) a[i] = new Color(d.Colors[i * 4], d.Colors[i * 4 + 1], d.Colors[i * 4 + 2], d.Colors[i * 4 + 3]); Compat.SetColors(mesh, a); }
            mesh.subMeshCount = Math.Max(1, d.Triangles.Count);
            for (int s = 0; s < d.Triangles.Count; s++) Compat.SetTriangles(mesh, d.Triangles[s], s);
            if (d.Skinned)
            {
                var w = new BoneWeight[n];
                for (int i = 0; i < n; i++)
                    w[i] = new BoneWeight
                    {
                        boneIndex0 = d.BoneIndices[i * 4], boneIndex1 = d.BoneIndices[i * 4 + 1], boneIndex2 = d.BoneIndices[i * 4 + 2], boneIndex3 = d.BoneIndices[i * 4 + 3],
                        weight0 = d.BoneWeights[i * 4], weight1 = d.BoneWeights[i * 4 + 1], weight2 = d.BoneWeights[i * 4 + 2], weight3 = d.BoneWeights[i * 4 + 3]
                    };
                Compat.SetBoneWeights(mesh, w);
                int bc = d.BindPoses.Length / 16;
                var bp = new Matrix4x4[bc];
                for (int k = 0; k < bc; k++)
                {
                    var m = new Matrix4x4();
                    for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) m[r, c] = d.BindPoses[k * 16 + r * 4 + c];
                    bp[k] = m;
                }
                Compat.SetBindposes(mesh, bp);
            }
            mesh.RecalculateBounds();
            return mesh;
        }

        // ---- animation -----------------------------------------------------------------------------------------
        const float Step = 1e9f;

        static AnimationClip MakeClip(SbClip c, BuiltSpecies b)
        {
            var clip = new AnimationClip { name = c.Name, frameRate = c.SampleRate > 0 ? c.SampleRate : 30f };
            clip.wrapMode = c.Loop ? WrapMode.Loop : WrapMode.Default;
            foreach (var cu in c.Curves)
            {
                string[] comps = cu.Property == "rotation" ? new[] { "x", "y", "z", "w" } : new[] { "x", "y", "z" };
                string prefix = cu.Property == "position" ? "localPosition." : cu.Property == "rotation" ? "localRotation." : "localScale.";
                for (int k = 0; k < comps.Length; k++)
                {
                    var keys = new Keyframe[cu.Keys.Count];
                    for (int i = 0; i < keys.Length; i++)
                    {
                        var key = cu.Keys[i];
                        float tin = key.In[k], tout = key.Out[k];
                        if (Math.Abs(tin) >= Step) tin = tin > 0 ? float.PositiveInfinity : float.NegativeInfinity;
                        if (Math.Abs(tout) >= Step) tout = tout > 0 ? float.PositiveInfinity : float.NegativeInfinity;
                        keys[i] = new Keyframe(key.T, key.V[k], tin, tout);
                    }
                    Compat.SetCurve(clip, cu.Path, typeof(Transform), prefix + comps[k], keys);
                }
            }
            foreach (var e in c.Events)
                if (!string.IsNullOrEmpty(e.Function))
                    clip.AddEvent(new AnimationEvent { time = e.T, functionName = e.Function, stringParameter = e.StringParam, floatParameter = e.FloatParam, intParameter = e.IntParam });
            return clip;
        }

        /// <summary>Drives the clips through the target game's NATIVE animator controller: every native state whose clip has the same
        /// name as one of ours (e.g. "Idle v4") plays ours. States the target lacks (see SbPackage.StateMap) stay for the adapter.</summary>
        public static AnimatorOverrideController ApplyOverrides(Animator animator, BuiltSpecies built)
        {
            var native = animator.runtimeAnimatorController;
            var oc = new AnimatorOverrideController(native);
            var pairs = Compat.GetOverrides(oc);
            for (int i = 0; i < pairs.Count; i++)
            {
                var original = pairs[i].Key;
                if (original != null && built.Clips.TryGetValue(original.name, out var mine)) pairs[i] = new KeyValuePair<AnimationClip, AnimationClip>(original, mine);
            }
            Compat.ApplyOverrides(oc, pairs);
            animator.runtimeAnimatorController = oc;
            return oc;
        }
    }
}
