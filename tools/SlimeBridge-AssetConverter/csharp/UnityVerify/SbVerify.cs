// Unity Editor batch verification of an sbconv runtime package (copied into <project>/Assets/Editor by `sbconv unity-verify`).
// Builds the species with SbBuilder, then lets UNITY itself skin and animate it:
//   - mesh / bone / bindpose consistency (Unity logs an error when bones and bindposes disagree: counted),
//   - SkinnedMeshRenderer.BakeMesh at rest must equal the source vertices,
//   - every clip is sampled with AnimationClip.SampleAnimation; bone poses are written to the report so Python can compare them with its own curve evaluation,
//   - baked deformation must stay finite,
//   - a screenshot is rendered.
// Command line:  -sbPackage <dir> -sbOut <report.json> -sbShot <png>
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using SbRuntime;
using SbRuntime.UnityBuild;
using UnityEditor;
using UnityEngine;

public static class SbVerify
{
    static string Arg(string name)
    {
        var a = Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++) if (a[i] == name) return a[i + 1];
        return null;
    }

    static string F(float v) { return v.ToString("R", CultureInfo.InvariantCulture); }

    public static void Run()
    {
        string pkgDir = Arg("-sbPackage"), outJson = Arg("-sbOut"), shot = Arg("-sbShot");
        var sb = new StringBuilder();
        var errors = new List<string>();
        int unityErrors = 0, editorOnlyNotices = 0;
        Application.LogCallback cb = (msg, stack, type) =>
        {
            if (type != LogType.Error && type != LogType.Exception && type != LogType.Assert) return;
            // Unity 2021+ refuses AnimationClip.AddEvent in edit mode (allowed at game runtime); counted separately, never hidden.
            if (msg.StartsWith("Please use Editor.AnimationUtility to add persistent animation events")) { editorOnlyNotices++; return; }
            unityErrors++; if (errors.Count < 20) errors.Add(msg);
        };
        Application.logMessageReceived += cb;
        bool ok = true;
        try
        {
            var pkg = SbPackage.Load(pkgDir);
            var built = SbBuilder.Build(pkg, new SbBuildOptions { FindShader = n => Shader.Find("Standard") });
            foreach (var kv in built.Materials) Placeholder(pkg, kv.Key, kv.Value);

            sb.Append("{\"unity\":\"").Append(Application.unityVersion).Append("\",\"species\":\"").Append(pkg.CanonicalId).Append("\",");
            sb.Append("\"rig_nodes\":").Append(built.Bones.Count).Append(",\"renderers\":").Append(built.Renderers.Count).Append(",\"materials\":").Append(built.Materials.Count).Append(",\"clips\":").Append(built.Clips.Count).Append(",");
            var smrs = built.Renderers.OfType<SkinnedMeshRenderer>().ToList();
            sb.Append("\"skinned\":").Append(smrs.Count).Append(",\"renderer_checks\":[");
            double worstRest = 0;
            for (int i = 0; i < smrs.Count; i++)
            {
                var smr = smrs[i];
                var mesh = smr.sharedMesh;
                bool match = smr.bones.Length == mesh.bindposes.Length && mesh.boneWeights.Length == mesh.vertexCount;
                var baked = new Mesh();
                smr.BakeMesh(baked);
                var v0 = mesh.vertices; var v1 = baked.vertices;
                double dev = 0; float ext = Math.Max(mesh.bounds.extents.magnitude, 1e-6f);
                for (int k = 0; k < v0.Length; k++) dev = Math.Max(dev, (v0[k] - v1[k]).magnitude);
                dev /= 2 * ext; worstRest = Math.Max(worstRest, dev);
                if (!match) { ok = false; errors.Add(mesh.name + ": bones/bindposes/weights mismatch"); }
                sb.Append(i > 0 ? "," : "").Append("{\"mesh\":\"").Append(mesh.name).Append("\",\"vertices\":").Append(mesh.vertexCount).Append(",\"submeshes\":").Append(mesh.subMeshCount)
                  .Append(",\"bones\":").Append(smr.bones.Length).Append(",\"counts_match\":").Append(match ? "true" : "false").Append(",\"rest_pose_deviation\":").Append(F((float)dev)).Append("}");
            }
            sb.Append("],\"worst_rest_pose_deviation\":").Append(F((float)worstRest)).Append(",\"animation\":[");
            bool firstClip = true;
            int clipIdx = 0;
            foreach (var clip in pkg.Clips.Take(8))
            {
                var uc = built.Clips[clip.Name];
                var paths = clip.Curves.Where(c => c.Path != "" && built.Bones.ContainsKey(c.Path)).Select(c => c.Path).Distinct().Take(10).ToList();
                sb.Append(firstClip ? "" : ",").Append("{\"clip\":\"").Append(clip.Name.Replace("\"", "'")).Append("\",\"length\":").Append(F(uc.length)).Append(",\"poses\":[");
                firstClip = false;
                bool firstT = true;
                double worstDefo = 0; bool finite = true;
                foreach (float f in new[] { 0f, 0.33f, 0.66f, 0.99f })
                {
                    float t = clip.Length * f;
                    uc.SampleAnimation(built.Appearance.gameObject, t);
                    foreach (var smr in smrs)
                    {
                        var bk = new Mesh(); smr.BakeMesh(bk);
                        var vs = bk.vertices; var v0 = smr.sharedMesh.vertices;
                        float ext = Math.Max(smr.sharedMesh.bounds.extents.magnitude, 1e-6f);
                        for (int k = 0; k < vs.Length; k++)
                        {
                            if (float.IsNaN(vs[k].x) || float.IsInfinity(vs[k].x) || float.IsNaN(vs[k].y) || float.IsNaN(vs[k].z)) { finite = false; break; }
                            worstDefo = Math.Max(worstDefo, (vs[k] - v0[k]).magnitude / (2 * ext));
                        }
                    }
                    sb.Append(firstT ? "" : ",").Append("{\"t\":").Append(F(t)).Append(",\"bones\":[");
                    firstT = false;
                    for (int p = 0; p < paths.Count; p++)
                    {
                        var tr = built.Bones[paths[p]];
                        sb.Append(p > 0 ? "," : "").Append("{\"path\":\"").Append(paths[p]).Append("\",\"pos\":[").Append(F(tr.localPosition.x)).Append(",").Append(F(tr.localPosition.y)).Append(",").Append(F(tr.localPosition.z))
                          .Append("],\"rot\":[").Append(F(tr.localRotation.x)).Append(",").Append(F(tr.localRotation.y)).Append(",").Append(F(tr.localRotation.z)).Append(",").Append(F(tr.localRotation.w)).Append("]}");
                    }
                    sb.Append("]}");
                }
                sb.Append("],\"finite\":").Append(finite ? "true" : "false").Append(",\"max_deformation\":").Append(F((float)worstDefo)).Append("}");
                if (!finite) { ok = false; errors.Add("clip " + clip.Name + ": non-finite deformation"); }
                clipIdx++;
            }
            sb.Append("],");
            // rest pose again for the screenshot
            if (!string.IsNullOrEmpty(shot)) { foreach (var c in built.Clips.Values.Take(1)) c.SampleAnimation(built.Appearance.gameObject, 0); Shoot(built, shot); sb.Append("\"screenshot\":\"").Append(shot.Replace("\\", "/")).Append("\","); }
            sb.Append("\"warnings\":[").Append(string.Join(",", built.Warnings.Take(10).Select(w => "\"" + w.Replace("\\", "/").Replace("\"", "'") + "\"").ToArray())).Append("],");
        }
        catch (Exception e)
        {
            ok = false;
            if (sb.Length == 0) sb.Append("{");
            errors.Add(e.GetType().Name + ": " + e.Message);
        }
        Application.logMessageReceived -= cb;
        sb.Append("\"editor_only_event_notices\":").Append(editorOnlyNotices).Append(",\"unity_errors\":").Append(unityErrors).Append(",\"errors\":[").Append(string.Join(",", errors.Select(x => "\"" + x.Replace("\\", "/").Replace("\"", "'").Replace("\n", " ") + "\"").ToArray())).Append("],\"ok\":").Append(ok && unityErrors == 0 ? "true" : "false").Append("}");
        File.WriteAllText(outJson, sb.ToString(), new UTF8Encoding(false));
        EditorApplication.Exit(ok && unityErrors == 0 ? 0 : 1);
    }

    // The game's shaders do not exist in a bare editor project: tint the Standard placeholder with the recipe so the screenshot is informative.
    static void Placeholder(SbPackage pkg, string id, Material m)
    {
        var rec = pkg.Recipes[id];
        Color c = Color.gray;
        float[] col;
        if (rec.Colors.TryGetValue("_MiddleColor", out col) || rec.Colors.TryGetValue("_Color", out col)) c = new Color(col[0], col[1], col[2], 1);
        m.color = c;
        var tx = rec.Textures.FirstOrDefault(t => t.Property == "_Diffuse");
        if (tx != null)
        {
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, true, false);
            if (ImageConversion.LoadImage(tex, File.ReadAllBytes(Path.Combine(pkg.Directory, tx.File)), false)) { m.mainTexture = tex; m.color = Color.white; }
        }
    }

    static void Shoot(BuiltSpecies built, string path)
    {
        var bounds = new Bounds(built.Root.transform.position, Vector3.zero);
        foreach (var r in built.Renderers) bounds.Encapsulate(r.bounds);
        var camGo = new GameObject("cam"); var cam = camGo.AddComponent<Camera>();
        cam.backgroundColor = new Color(0.18f, 0.18f, 0.18f); cam.clearFlags = CameraClearFlags.SolidColor; cam.orthographic = false; cam.fieldOfView = 30;
        float d = Math.Max(bounds.extents.magnitude * 3.2f, 1f);
        camGo.transform.position = bounds.center + new Vector3(d * 0.6f, d * 0.35f, -d * 0.8f);
        camGo.transform.LookAt(bounds.center);
        var lightGo = new GameObject("light"); var l = lightGo.AddComponent<Light>(); l.type = LightType.Directional; l.transform.rotation = Quaternion.Euler(40, -30, 0);
        var rt = new RenderTexture(768, 768, 24); cam.targetTexture = rt; cam.Render();
        RenderTexture.active = rt;
        var tex = new Texture2D(768, 768, TextureFormat.RGB24, false); tex.ReadPixels(new Rect(0, 0, 768, 768), 0, 0); tex.Apply();
        File.WriteAllBytes(path, ImageConversion.EncodeToPNG(tex));
        RenderTexture.active = null;
    }
}
