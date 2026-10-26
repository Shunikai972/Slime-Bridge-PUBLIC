using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using SbRuntime;

// Loads runtime packages with the Unity-free core and prints a parity summary that tests/check_csharp_parity.py compares with Python.
static class Program
{
    static int Main(string[] args)
    {
        if (args.Length == 0) { Console.Error.WriteLine("usage: SbRuntime.Tests <package dir>..."); return 2; }
        int failures = 0;
        // negative tests: the loader must refuse tampered input
        failures += Negative();
        foreach (var dir in args)
        {
            try
            {
                var p = SbPackage.Load(dir);
                double posSum = 0; long verts = 0, tris = 0; int meshes = 0, skinned = 0;
                foreach (var part in p.Parts)
                {
                    var lod0 = part.Lods.FirstOrDefault(l => l.Lod == 0);
                    if (lod0 == null) continue;
                    foreach (var r in lod0.Renderers)
                    {
                        var m = p.LoadMesh(r);
                        meshes++; verts += m.VertexCount; tris += m.Triangles.Sum(t => t.Length / 3);
                        foreach (var f in m.Positions) posSum += f;
                        if (m.Skinned) { skinned++; if (lod0.Bones.Length != m.BindPoses.Length / 16) throw new FormatException(part.Element + ": bone/bindpose count mismatch"); }
                    }
                }
                double keySum = 0; long keys = 0;
                foreach (var c in p.Clips) foreach (var cu in c.Curves) foreach (var k in cu.Keys) { keys++; keySum += k.T; }
                Console.WriteLine(string.Join("|", new[] {
                    p.CanonicalId, p.SourceGame + ">" + p.TargetGame, p.Status, "rig=" + p.Rig.Count, "parts=" + p.Parts.Count, "meshes=" + meshes, "skinned=" + skinned,
                    "verts=" + verts, "tris=" + tris, "possum=" + posSum.ToString("F4", CultureInfo.InvariantCulture), "recipes=" + p.Recipes.Count, "expr=" + p.Expressions.Count,
                    "clips=" + p.Clips.Count, "keys=" + keys, "keysum=" + keySum.ToString("F4", CultureInfo.InvariantCulture), "missingScripts=" + p.MissingScripts.Count }));
            }
            catch (Exception e) { Console.WriteLine("FAIL|" + dir + "|" + e.GetType().Name + ": " + e.Message); failures++; }
        }
        return failures == 0 ? 0 : 1;
    }

    static int Negative()
    {
        int bad = 0;
        string[] cases = { "{\"a\":NaN}", "{\"a\":Infinity}", "{\"a\":1,}", "[1,2", "{\"a\":01}", "{\"a\":\"\u0001\"}" };
        foreach (var c in cases)
        {
            try { Json.Parse(c); Console.WriteLine("NEGATIVE-FAIL|accepted invalid JSON: " + c); bad++; } catch (FormatException) { }
        }
        var ok = Json.Parse("{\"a\":[1,2.5,-3e2],\"b\":{\"c\":null,\"d\":true},\"s\":\"x\\u00e9\"}") as Dictionary<string, object>;
        if (ok == null || !(ok["a"] is List<object> l) || l.Count != 3 || (double)l[2] != -300.0) { Console.WriteLine("NEGATIVE-FAIL|valid JSON misparsed"); bad++; }
        return bad;
    }
}
