using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace SbRuntime
{
    public sealed class SbRigNode { public string Name, Path; public int Parent; public float[] P, Q, S; }

    public sealed class SbTexture { public string Property, File; public float[] Scale, Offset; public bool Srgb = true; public string Transform; }

    public sealed class SbRecipe
    {
        public string Id, Family, Plan, SourceName, SourceShader, Shader, TemplateName, Fidelity;
        public List<string> Notes = new List<string>(), Keywords = new List<string>();
        public Dictionary<string, float> Floats = new Dictionary<string, float>();
        public Dictionary<string, float[]> Colors = new Dictionary<string, float[]>();
        public List<SbTexture> Textures = new List<SbTexture>();
    }

    public sealed class SbRenderer
    {
        public string Node, Type, MeshFile, MeshName, UvRemap;
        public bool Skinned;
        public int Submeshes;
        public float[] P, Q, S;
        public string[] MaterialSlots;
    }

    public sealed class SbLod { public int Lod; public List<SbRenderer> Renderers = new List<SbRenderer>(); public string[] Bones; public string RootBone, ParentBone; }

    public sealed class SbPart { public string Element, Name; public int Type; public bool SupportsFaces; public List<SbLod> Lods = new List<SbLod>(); }

    public sealed class SbKey { public float T; public float[] V, In, Out; }

    public sealed class SbCurve { public string Path, Property; public List<SbKey> Keys = new List<SbKey>(); }   // Property: position|rotation|scale

    public sealed class SbEvent { public float T; public string Function, StringParam; public float FloatParam; public int IntParam; }

    public sealed class SbClip
    {
        public string Name; public float Length, SampleRate; public bool Loop;
        public List<SbCurve> Curves = new List<SbCurve>(); public List<SbEvent> Events = new List<SbEvent>();
    }

    public sealed class SbExpression { public int Expression; public string Eyes, Mouth; }

    /// <summary>A runtime package written by sbconv (see docs/FORMATS.md). Unity-free: all data as plain arrays.</summary>
    public sealed class SbPackage
    {
        public string Directory, CanonicalId, SpeciesName, SourceGame, TargetGame, Status;
        public List<SbRigNode> Rig = new List<SbRigNode>();
        public List<SbPart> Parts = new List<SbPart>();
        public Dictionary<string, SbRecipe> Recipes = new Dictionary<string, SbRecipe>();
        public List<SbExpression> Expressions = new List<SbExpression>();
        public List<SbClip> Clips = new List<SbClip>();
        public Dictionary<string, string> StateMap = new Dictionary<string, string>();
        public List<string> MissingScripts = new List<string>();
        public float[] AppearanceLocalPosition, AppearanceLocalRotation, AppearanceLocalScale;
        public Dictionary<string, float[]> Palette = new Dictionary<string, float[]>();
        public string DefaultState;

        static string Read(string dir, string rel) { return File.ReadAllText(System.IO.Path.Combine(dir, rel.Replace('/', System.IO.Path.DirectorySeparatorChar)), Encoding.UTF8); }

        /// <summary>Loads and validates a package. Every file listed in package.json must exist and match its SHA-256.</summary>
        public static SbPackage Load(string dir, bool verifyHashes = true)
        {
            var pk = Json.O(Json.Parse(Read(dir, "package.json")), "package.json");
            if (Json.S(pk, "schema") != "sbconv-runtime" || Json.I(pk, "schema_version") != 1) throw new FormatException("unsupported package schema");
            var files = Json.O(Json.Get(pk, "files"), "files");
            if (verifyHashes)
            {
                using (var sha = SHA256.Create())
                    foreach (var kv in files)
                    {
                        string path = System.IO.Path.Combine(dir, kv.Key.Replace('/', System.IO.Path.DirectorySeparatorChar));
                        if (kv.Key.Contains("..") || !File.Exists(path)) throw new FormatException("missing file " + kv.Key);
                        string h = BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(path))).Replace("-", "").ToLowerInvariant();
                        if (h != Json.S(Json.O(kv.Value, kv.Key), "sha256")) throw new FormatException("hash mismatch " + kv.Key);
                    }
            }
            var species = Json.O(Json.Get(pk, "species"), "species");
            var dirn = Json.O(Json.Get(pk, "direction"), "direction");
            var p = new SbPackage
            {
                Directory = dir, CanonicalId = Json.S(species, "canonical_id"), SpeciesName = Json.S(species, "name"),
                SourceGame = Json.S(dirn, "source"), TargetGame = Json.S(dirn, "target"), Status = Json.S(pk, "status")
            };
            p.LoadRig(Json.O(Json.Parse(Read(dir, "rig.json")), "rig.json"));
            p.LoadMaterials(Json.O(Json.Parse(Read(dir, "materials.json")), "materials.json"));
            p.LoadParts(Json.A(Json.Parse(Read(dir, "parts.json")), "parts.json"));
            p.LoadFace(Json.O(Json.Parse(Read(dir, "face.json")), "face.json"));
            p.LoadPalette(Json.O(Json.Parse(Read(dir, "palette.json")), "palette.json"));
            p.LoadAnimation(Json.O(Json.Parse(Read(dir, "animation/controller.json")), "controller.json"));
            var ad = Json.O(Json.Parse(Read(dir, "adapter.json")), "adapter.json");
            foreach (var s in Json.A(Json.Get(ad, "missing_in_target"), "missing")) p.MissingScripts.Add((string)s);
            p.Validate();
            return p;
        }

        void LoadRig(Dictionary<string, object> rig)
        {
            foreach (var o in Json.A(Json.Get(rig, "nodes"), "nodes"))
            {
                var n = Json.O(o, "node");
                var t = Json.O(Json.Get(n, "transform"), "transform");
                Rig.Add(new SbRigNode
                {
                    Name = Json.S(n, "name"), Path = Json.S(n, "path"), Parent = n["parent"] == null ? -1 : (int)Json.N(n["parent"]),
                    P = Json.F(Json.Get(t, "position"), 3, "position"), Q = Json.F(Json.Get(t, "rotation"), 4, "rotation"), S = Json.F(Json.Get(t, "scale"), 3, "scale")
                });
            }
            if (rig.TryGetValue("appearance_local_transform", out var al) && al != null)
            {
                var t = Json.O(al, "appearance_local_transform");
                AppearanceLocalPosition = Json.F(Json.Get(t, "position"), 3, "p"); AppearanceLocalRotation = Json.F(Json.Get(t, "rotation"), 4, "q"); AppearanceLocalScale = Json.F(Json.Get(t, "scale"), 3, "s");
            }
        }

        void LoadMaterials(Dictionary<string, object> doc)
        {
            foreach (var kv in Json.O(Json.Get(doc, "recipes"), "recipes"))
            {
                var r = Json.O(kv.Value, "recipe");
                var src = Json.O(Json.Get(r, "source"), "source"); var tgt = Json.O(Json.Get(r, "target"), "target");
                var fid = Json.O(Json.Get(r, "fidelity"), "fidelity");
                var rec = new SbRecipe
                {
                    Id = kv.Key, Family = Json.S(r, "family"), Plan = Json.S(r, "plan"), SourceName = Json.S(src, "name"), SourceShader = Json.S(src, "shader", ""),
                    Shader = tgt.ContainsKey("shader") && tgt["shader"] != null ? (string)tgt["shader"] : null, Fidelity = Json.S(fid, "level")
                };
                foreach (var n in Json.A(Json.Get(fid, "notes"), "notes")) rec.Notes.Add((string)n);
                if (tgt.TryGetValue("template", out var tp) && tp != null) rec.TemplateName = Json.S(Json.O(tp, "template"), "name");
                if (tgt.TryGetValue("keywords", out var kws)) foreach (var k in Json.A(kws, "keywords")) rec.Keywords.Add((string)k);
                if (tgt.TryGetValue("floats", out var fl)) foreach (var f in Json.O(fl, "floats")) rec.Floats[f.Key] = (float)Json.N(f.Value);
                if (tgt.TryGetValue("colors", out var cl)) foreach (var c in Json.O(cl, "colors")) rec.Colors[c.Key] = Json.F(c.Value, 4, "color " + c.Key);
                if (tgt.TryGetValue("textures", out var tx))
                    foreach (var t in Json.O(tx, "textures"))
                    {
                        var d = Json.O(t.Value, "texture");
                        rec.Textures.Add(new SbTexture { Property = t.Key, File = Json.S(d, "file"), Scale = Json.F(Json.Get(d, "scale"), 2, "scale"), Offset = Json.F(Json.Get(d, "offset"), 2, "offset"), Srgb = Json.B(d, "srgb", true), Transform = d.ContainsKey("transform") ? d["transform"] as string : null });
                    }
                Recipes[rec.Id] = rec;
            }
        }

        void LoadParts(List<object> parts)
        {
            foreach (var o in parts)
            {
                var d = Json.O(o, "part");
                var part = new SbPart { Element = Json.S(d, "element"), Name = Json.S(d, "name", ""), Type = d.ContainsKey("type") && d["type"] != null ? (int)Json.N(d["type"]) : 0, SupportsFaces = Json.B(d, "supports_faces") };
                foreach (var lo in Json.A(Json.Get(d, "lods"), "lods"))
                {
                    var l = Json.O(lo, "lod");
                    var lod = new SbLod { Lod = Json.I(l, "lod"), RootBone = l["root_bone"] as string, ParentBone = l["parent_bone"] as string };
                    var bones = Json.A(Json.Get(l, "bones"), "bones"); lod.Bones = new string[bones.Count];
                    for (int i = 0; i < bones.Count; i++) lod.Bones[i] = bones[i] as string;
                    foreach (var ro in Json.A(Json.Get(l, "renderers"), "renderers"))
                    {
                        var r = Json.O(ro, "renderer");
                        if (!r.ContainsKey("mesh_file") || r["mesh_file"] == null) continue;
                        var t = r["transform"] == null ? null : Json.O(r["transform"], "transform");
                        var slots = Json.A(Json.Get(r, "material_slots"), "slots");
                        var rend = new SbRenderer
                        {
                            Node = Json.S(r, "node"), Type = Json.S(r, "type"), MeshFile = Json.S(r, "mesh_file"), MeshName = Json.S(r, "mesh_name", ""), UvRemap = r["uv_remap"] as string,
                            Skinned = Json.B(r, "skinned"), Submeshes = Json.I(r, "submeshes", 1), MaterialSlots = new string[slots.Count],
                            P = t == null ? new float[] { 0, 0, 0 } : Json.F(Json.Get(t, "position"), 3, "p"), Q = t == null ? new float[] { 0, 0, 0, 1 } : Json.F(Json.Get(t, "rotation"), 4, "q"), S = t == null ? new float[] { 1, 1, 1 } : Json.F(Json.Get(t, "scale"), 3, "s")
                        };
                        for (int i = 0; i < slots.Count; i++) rend.MaterialSlots[i] = slots[i] as string;
                        lod.Renderers.Add(rend);
                    }
                    part.Lods.Add(lod);
                }
                Parts.Add(part);
            }
        }

        void LoadFace(Dictionary<string, object> face)
        {
            foreach (var o in Json.A(Json.Get(face, "expressions"), "expressions"))
            {
                var e = Json.O(o, "expression");
                Expressions.Add(new SbExpression { Expression = Json.I(e, "expression"), Eyes = e["eyes"] as string, Mouth = e["mouth"] as string });
            }
        }

        void LoadPalette(Dictionary<string, object> pal)
        {
            if (pal.TryGetValue("palette", out var p) && p is Dictionary<string, object> pd)
                foreach (var kv in pd) { var c = Json.O(kv.Value, "color"); Palette[kv.Key] = new[] { (float)Json.N(c, "r"), (float)Json.N(c, "g"), (float)Json.N(c, "b"), (float)Json.N(c, "a", 1) }; }
        }

        void LoadAnimation(Dictionary<string, object> doc)
        {
            if (doc.TryGetValue("plan", out var plan) && plan is Dictionary<string, object> pd && pd.TryGetValue("state_map", out var sm) && sm is Dictionary<string, object> smd)
                foreach (var kv in smd) StateMap[kv.Key] = kv.Value as string;
            var ctrl = doc.ContainsKey("controller") ? doc["controller"] as Dictionary<string, object> : null;
            if (ctrl != null && ctrl["layers"] is List<object> layers && layers.Count > 0) DefaultState = Json.O(layers[0], "layer")["default_state"] as string;
            foreach (var kv in Json.O(Json.Get(doc, "clips"), "clips"))
            {
                var meta = Json.O(kv.Value, "clip meta");
                var c = Json.O(Json.Parse(Read(Directory, Json.S(meta, "file"))), "clip");
                var clip = new SbClip { Name = Json.S(c, "name"), Length = (float)Json.N(c, "length"), SampleRate = (float)Json.N(c, "sample_rate", 30), Loop = Json.B(c, "loop") };
                foreach (var grp in new[] { "positions", "rotations", "scales" })
                    foreach (var co in Json.A(Json.Get(c, grp), grp))
                    {
                        var cd = Json.O(co, "curve");
                        var curve = new SbCurve { Path = Json.S(cd, "path", ""), Property = grp == "positions" ? "position" : grp == "rotations" ? "rotation" : "scale" };
                        int w = curve.Property == "rotation" ? 4 : 3;
                        foreach (var ko in Json.A(Json.Get(cd, "keys"), "keys"))
                        {
                            var k = Json.O(ko, "key");
                            curve.Keys.Add(new SbKey { T = (float)Json.N(k, "t"), V = Json.F(Json.Get(k, "v"), w, "v"), In = Json.F(Json.Get(k, "i"), w, "i"), Out = Json.F(Json.Get(k, "o"), w, "o") });
                        }
                        clip.Curves.Add(curve);
                    }
                foreach (var eo in Json.A(Json.Get(c, "events"), "events"))
                {
                    var e = Json.O(eo, "event");
                    clip.Events.Add(new SbEvent { T = (float)Json.N(e, "t"), Function = Json.S(e, "function", ""), StringParam = Json.S(e, "string", ""), FloatParam = (float)Json.N(e, "float", 0), IntParam = Json.I(e, "int", 0) });
                }
                Clips.Add(clip);
            }
        }

        /// <summary>Referential integrity: material slots, bones and textures must all resolve.</summary>
        public void Validate()
        {
            var paths = new HashSet<string>();
            foreach (var n in Rig) { if (!paths.Add(n.Path)) throw new FormatException("duplicate rig path " + n.Path); if (n.Parent >= Rig.Count) throw new FormatException("bad parent"); }
            foreach (var part in Parts)
                foreach (var lod in part.Lods)
                {
                    foreach (var b in lod.Bones) if (b == null || !paths.Contains(b)) throw new FormatException(part.Element + ": bone " + b + " not in rig");
                    foreach (var r in lod.Renderers)
                    {
                        if (!File.Exists(System.IO.Path.Combine(Directory, r.MeshFile))) throw new FormatException("missing mesh " + r.MeshFile);
                        foreach (var s in r.MaterialSlots) if (s == null || !Recipes.ContainsKey(s)) throw new FormatException(part.Element + ": unresolved material slot");
                    }
                }
            foreach (var rec in Recipes.Values)
                foreach (var t in rec.Textures) if (!File.Exists(System.IO.Path.Combine(Directory, t.File))) throw new FormatException("missing texture " + t.File);
            foreach (var clip in Clips) foreach (var c in clip.Curves) if (c.Path != "" && !paths.Contains(c.Path)) throw new FormatException(clip.Name + ": curve path not in rig: " + c.Path);
        }

        public SbMeshData LoadMesh(SbRenderer r) { return SbMeshData.Load(System.IO.Path.Combine(Directory, r.MeshFile)); }
    }
}
