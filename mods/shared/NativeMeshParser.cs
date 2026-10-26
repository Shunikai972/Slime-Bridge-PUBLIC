using System;
using System.Collections.Generic;

namespace SlimeBridge.Shared
{
    /// <summary>Fail-closed, loader-neutral bridge-native-mesh v2 reader.</summary>
    public static class NativeMeshParser
    {
        public static NativeMeshDocument Parse(string json)
        {
            if (json == null) throw Error("document", "JSON is null");
            Preflight(json);
            var root = Obj(MiniJson.Parse(json), "document");
            if (Str(root, "schema", true) != "bridge-native-mesh" || Int(Get(root, "schema_version"), 2, 2, "schema_version") != 2)
                throw Error("schema", "expected bridge-native-mesh v2");
            var doc = new NativeMeshDocument {
                SourceGame = Str(root, "source_game", true), ReferenceId = Str(root, "reference_id", true),
                PrefabScale = root.ContainsKey("prefab_scale") ? Number(root["prefab_scale"], "prefab_scale") : 1,
                VisualTransform = root.ContainsKey("visual_transform") ? Transform(Obj(root["visual_transform"], "visual_transform"), false) : new NativeMeshTransform()
            };
            if (doc.PrefabScale <= 0) throw Error("prefab_scale", "must be positive");
            doc.Bones = Bones(root);
            doc.Parts = Parts(root, doc.Bones);
            Animation(root, doc);
            return doc;
        }
        private static FormatException Error(string at, string message) { return new FormatException(at + ": " + message); }
        private static object? Get(Dictionary<string, object?> d, string key) { object? value; return d.TryGetValue(key, out value) ? value : null; }
        private static Dictionary<string, object?> Obj(object? value, string at)
        {
            var result = value as Dictionary<string, object?>;
            if (result == null) throw Error(at, "must be an object");
            return result;
        }
        private static List<object?> List(object? value, string at)
        {
            var result = value as List<object?>;
            if (result == null) throw Error(at, "must be an array");
            return result;
        }
        private static string Str(Dictionary<string, object?> d, string key, bool required = false)
        {
            if (!d.ContainsKey(key) && !required) return "";
            var value = Get(d, key) as string;
            if (value == null || (required && string.IsNullOrWhiteSpace(value)) || value.Length > 4096)
                throw Error(key, "must be " + (required ? "a nonempty" : "a") + " string (max 4096)");
            return value;
        }
        private static float Number(object? value, string at)
        {
            double n;
            if (value is long) n = (long)value;
            else if (value is double) n = (double)value;
            else throw Error(at, "must be a number");
            if (double.IsNaN(n) || double.IsInfinity(n) || n < -float.MaxValue || n > float.MaxValue)
                throw Error(at, "must be finite and representable as float");
            if (n != 0 && Math.Abs(n) < float.Epsilon) throw Error(at, "float underflow");
            return (float)n;
        }
        private static int Int(object? value, int min, int max, string at)
        {
            double n;
            if (value is long) n = (long)value;
            else if (value is double) n = (double)value;
            else throw Error(at, "must be an integer");
            if (double.IsNaN(n) || double.IsInfinity(n) || n < min || n > max || n != Math.Floor(n))
                throw Error(at, "integer outside range " + min + ".." + max);
            return (int)n;
        }
        private static float[] Vector(object? raw, int width, string at)
        {
            var list = List(raw, at);
            if (list.Count != width) throw Error(at, "expected width " + width);
            var result = new float[width];
            for (int i = 0; i < width; i++) result[i] = Number(list[i], at);
            return result;
        }
        private static NativeMeshTransform Transform(Dictionary<string, object?> d, bool bone)
        {
            var t = new NativeMeshTransform();
            string p = bone ? "p" : "local_position", q = bone ? "q" : "local_rotation", s = bone ? "s" : "local_scale";
            if (d.ContainsKey(p)) t.Position = Vector(d[p], 3, p);
            if (d.ContainsKey(q)) t.Rotation = Vector(d[q], 4, q);
            if (d.ContainsKey(s)) t.Scale = Vector(d[s], 3, s);
            Quaternion(t.Rotation, q);
            return t;
        }
        private static void Quaternion(float[] q, string at)
        {
            double norm = 0;
            foreach (float v in q) norm += (double)v * v;
            if (Math.Abs(norm - 1) > 0.01) throw Error(at, "quaternion must have unit length");
        }
        private static NativeMeshBone[] Bones(Dictionary<string, object?> root)
        {
            if (!root.ContainsKey("skeleton")) return new NativeMeshBone[0];
            var skeleton = Obj(root["skeleton"], "skeleton");
            var list = List(Get(skeleton, "bones"), "skeleton.bones");
            Bound(list.Count, 1024, "bones");
            if (skeleton.ContainsKey("bone_count")) Int(skeleton["bone_count"], list.Count, list.Count, "bone_count");
            var result = new NativeMeshBone[list.Count];
            var names = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < list.Count; i++) {
                var b = Obj(list[i], "bone");
                var name = Str(b, "name", true);
                if (name.IndexOfAny(new char[] { '/', '\\', '\0' }) >= 0 || !names.Add(name)) throw Error("bone.name", "invalid or duplicate name");
                result[i] = new NativeMeshBone { Name = name, Parent = Int(Get(b, "parent"), -1, list.Count - 1, "bone.parent"), Transform = Transform(b, true) };
            }
            // Iterative traversal: arbitrary parent ordering, bounded depth, no stack recursion.
            for (int i = 0; i < result.Length; i++) {
                var chain = new List<int>();
                var visiting = new HashSet<int>();
                int n = i;
                while (n != -1 && result[n].Path.Length == 0) {
                    if (!visiting.Add(n)) throw Error("bones", "parent cycle");
                    chain.Add(n); n = result[n].Parent;
                }
                string path = n == -1 ? "" : result[n].Path;
                for (int j = chain.Count - 1; j >= 0; j--) {
                    var bone = result[chain[j]];
                    path = path.Length == 0 ? bone.Name : path + "/" + bone.Name;
                    bone.Path = path;
                }
            }
            return result;
        }
        private static void Bound(int count, int max, string at)
        { if (count > max) throw Error(at, "limit " + max + " exceeded"); }
        private static NativeMeshPart[] Parts(Dictionary<string, object?> root, NativeMeshBone[] bones)
        {
            var result = new List<NativeMeshPart>();
            int partCount = 0, totalVertices = 0, totalIndices = 0;
            foreach (string key in new string[] { "skinned_renderers", "static_renderers" })
                if (root.ContainsKey(key)) partCount += List(root[key], key).Count;
            Bound(partCount, 256, "parts");
            foreach (string key in new string[] { "skinned_renderers", "static_renderers" }) {
                if (!root.ContainsKey(key)) continue;
                foreach (var raw in List(root[key], key)) {
                    var d = Obj(raw, key);
                    var p = new NativeMeshPart {
                        Skinned = key == "skinned_renderers", Enabled = Bool(d, "enabled", true),
                        Role = Str(d, "role"), Path = Str(d, "path"), MeshName = Str(d, "mesh_name"),
                        Transform = Transform(d, false), Vertices = Rows(d, "vertices", 3, true),
                        Normals = Rows(d, "normals", 3), Tangents = Rows(d, "tangents", 4),
                        Uv = Rows(d, "uv", 2), Uv2 = Rows(d, "uv2", 2), Colors = Rows(d, "colors", 4)
                    };
                    if (p.Vertices.Length == 0) throw Error("vertices", "empty mesh");
                    totalVertices += p.Vertices.Length; Bound(totalVertices, 500000, "aggregate vertices");
                    foreach (var rows in new float[][][] { p.Normals, p.Tangents, p.Uv, p.Uv2, p.Colors })
                        if (rows.Length != 0 && rows.Length != p.Vertices.Length) throw Error("mesh", "optional row count mismatch");
                    Skin(p, d, bones);
                    p.Materials = Materials(d);
                    var submeshes = List(Get(d, "triangles"), "triangles");
                    Bound(submeshes.Count, 256, "submeshes");
                    p.Triangles = new int[submeshes.Count][];
                    for (int s = 0; s < submeshes.Count; s++) {
                        var indices = List(submeshes[s], "triangles");
                        totalIndices += indices.Count; Bound(totalIndices, 3000000, "aggregate indices");
                        if (indices.Count % 3 != 0) throw Error("triangles", "must contain triplets");
                        p.Triangles[s] = new int[indices.Count];
                        for (int j = 0; j < indices.Count; j++) p.Triangles[s][j] = Int(indices[j], 0, p.Vertices.Length - 1, "triangles");
                    }
                    result.Add(p);
                }
            }
            return result.ToArray();
        }
        private static bool Bool(Dictionary<string, object?> d, string key, bool fallback)
        {
            if (!d.ContainsKey(key)) return fallback;
            if (!(d[key] is bool)) throw Error(key, "must be boolean");
            return (bool)d[key]!;
        }
        private static float[][] Rows(Dictionary<string, object?> d, string key, int width, bool required = false)
        {
            if (!d.ContainsKey(key) && !required) return new float[0][];
            var rows = List(Get(d, key), key);
            Bound(rows.Count, 100000, key);
            var result = new float[rows.Count][];
            for (int i = 0; i < rows.Count; i++) result[i] = Vector(rows[i], width, key);
            return result;
        }
        private static void Skin(NativeMeshPart p, Dictionary<string, object?> d, NativeMeshBone[] bones)
        {
            if (!p.Skinned) return;
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var bone in bones) names.Add(bone.Name);
            p.RootBone = Str(d, "root_bone", true);
            if (!names.Contains(p.RootBone)) throw Error("root_bone", "unresolved bone");
            var palette = List(Get(d, "skin_bones"), "skin_bones");
            Bound(palette.Count, bones.Length, "skin_bones");
            p.SkinBones = new string[palette.Count];
            var unique = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < palette.Count; i++) {
                var name = palette[i] as string;
                if (name == null || !names.Contains(name) || !unique.Add(name)) throw Error("skin_bones", "unresolved or duplicate bone");
                p.SkinBones[i] = name;
            }
            if (palette.Count == 0) throw Error("skin_bones", "empty palette");
            p.BindPoses = Rows(d, "bind_poses", 16, true);
            p.BoneWeights = Rows(d, "bone_weights", 8, true);
            if (p.BindPoses.Length != palette.Count || p.BoneWeights.Length != p.Vertices.Length)
                throw Error("skin", "bind pose or weight count mismatch");
            foreach (var row in p.BoneWeights) {
                double sum = 0;
                // Schema row layout is [w0,w1,w2,w3,i0,i1,i2,i3].
                for (int j = 0; j < 4; j++) {
                    if (row[j] < 0) throw Error("bone_weights", "negative weight");
                    sum += row[j];
                }
                if (Math.Abs(sum - 1) > 0.001) throw Error("bone_weights", "weights must sum to one");
            }
            var rawRows = List(d["bone_weights"], "bone_weights");
            foreach (var rawRow in rawRows) {
                var row = List(rawRow, "bone_weights");
                // Check all raw indices before float conversion, even zero-weight slots.
                for (int j = 4; j < 8; j++) Int(row[j], 0, palette.Count - 1, "bone_weights.index");
            }
        }
        private static NativeMeshMaterial[] Materials(Dictionary<string, object?> d)
        {
            if (!d.ContainsKey("materials")) return new NativeMeshMaterial[0];
            var list = List(d["materials"], "materials");
            Bound(list.Count, 256, "materials");
            var result = new NativeMeshMaterial[list.Count];
            for (int i = 0; i < list.Count; i++) {
                var m = Obj(list[i], "material");
                var material = new NativeMeshMaterial { Name = Str(m, "name"), Shader = Str(m, "shader"), FaceRole = Str(m, "face_role") };
                if (m.ContainsKey("render_queue")) material.RenderQueue = Int(m["render_queue"], -1, 5000, "render_queue");
                if (m.ContainsKey("colors")) foreach (var kv in Properties(m["colors"], "colors"))
                    material.Colors.Add(kv.Key, Vector(kv.Value, 4, "material.colors." + kv.Key));
                if (m.ContainsKey("floats")) foreach (var kv in Properties(m["floats"], "floats"))
                    material.Floats.Add(kv.Key, Number(kv.Value, "material.floats." + kv.Key));
                if (m.ContainsKey("textures")) {
                    var textures = List(m["textures"], "textures");
                    Bound(textures.Count, 256, "textures");
                    material.Textures = new NativeMeshTexture[textures.Count];
                    for (int j = 0; j < textures.Count; j++) {
                        var t = Obj(textures[j], "texture");
                        var texture = new NativeMeshTexture { Property = Str(t, "property", true), File = Str(t, "file", true),
                            Linear = Bool(t, "linear", t.ContainsKey("source_color_space") && Int(t["source_color_space"], 0, 1, "source_color_space") == 0) };
                        SafeFile(texture.File);
                        if (t.ContainsKey("scale")) texture.Scale = Vector(t["scale"], 2, "texture.scale");
                        if (t.ContainsKey("offset")) texture.Offset = Vector(t["offset"], 2, "texture.offset");
                        material.Textures[j] = texture;
                    }
                }
                result[i] = material;
            }
            return result;
        }
        private static void SafeFile(string file)
        {
            // A single portable basename, not a path, URI, drive, ADS or device alias.
            if (file == "." || file == ".." || file.EndsWith(".", StringComparison.Ordinal) || file.EndsWith(" ", StringComparison.Ordinal))
                throw Error("texture.file", "unsafe filename");
            foreach (char c in file)
                if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '.' && c != '-' && c != '_')
                    throw Error("texture.file", "expected a portable ASCII basename");
            string stem = file.Split('.')[0].ToUpperInvariant();
            if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" ||
                (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) && stem[3] >= '1' && stem[3] <= '9'))
                throw Error("texture.file", "device filename");
        }
        private static void Animation(Dictionary<string, object?> root, NativeMeshDocument doc)
        {
            if (!root.ContainsKey("source_animation")) return;
            var a = Obj(root["source_animation"], "source_animation");
            doc.DefaultClip = Str(a, "default_clip", true);
            var clips = List(Get(a, "clips"), "clips");
            Bound(clips.Count, 64, "clips");
            int sampledValues = 0;
            doc.Clips = new NativeMeshClip[clips.Count];
            var names = new HashSet<string>(StringComparer.Ordinal);
            var paths = new HashSet<string>(StringComparer.Ordinal);
            foreach (var bone in doc.Bones) paths.Add(bone.Path);
            for (int i = 0; i < clips.Count; i++) {
                var d = Obj(clips[i], "clip");
                var c = new NativeMeshClip { Name = Str(d, "name", true), Duration = Number(Get(d, "duration"), "duration"),
                    SampleRate = Number(Get(d, "sample_rate"), "sample_rate"), Loop = Bool(d, "loop", false) };
                if (!names.Add(c.Name)) throw Error("clips", "duplicate name");
                if (c.Duration <= 0 || c.Duration > 3600 || c.SampleRate <= 0 || c.SampleRate > 240) throw Error("clip", "invalid duration or sample rate");
                var times = List(Get(d, "times"), "times");
                Bound(times.Count, 4096, "samples");
                if (times.Count == 0) throw Error("times", "empty samples");
                c.Times = new float[times.Count];
                for (int j = 0; j < times.Count; j++) {
                    c.Times[j] = Number(times[j], "times");
                    if ((j == 0 && c.Times[j] != 0) || (j > 0 && c.Times[j] <= c.Times[j - 1]) || c.Times[j] > c.Duration)
                        throw Error("times", "must start at zero, ascend strictly, and not exceed duration");
                }
                if (d.ContainsKey("sample_count")) Int(d["sample_count"], times.Count, times.Count, "sample_count");
                var channels = List(Get(d, "channels"), "channels");
                Bound(channels.Count, 3072, "channels");
                sampledValues += times.Count * channels.Count;
                Bound(sampledValues, 262144, "aggregate sampled values");
                c.Channels = new NativeMeshChannel[channels.Count];
                var targets = new HashSet<string>(StringComparer.Ordinal);
                for (int j = 0; j < channels.Count; j++) {
                    var raw = Obj(channels[j], "channel");
                    var ch = new NativeMeshChannel { Path = Str(raw, "path", true), Property = Str(raw, "property", true) };
                    int width = ch.Property == "local_rotation" ? 4 : (ch.Property == "local_position" || ch.Property == "local_scale" ? 3 : 0);
                    if (width == 0 || !paths.Contains(ch.Path) || !targets.Add(ch.Path + "\0" + ch.Property)) throw Error("channel", "invalid or duplicate target");
                    ch.Values = Rows(raw, "values", width, true);
                    if (ch.Values.Length != times.Count) throw Error("channel", "sample count mismatch");
                    if (width == 4) foreach (var q in ch.Values) Quaternion(q, "channel.rotation");
                    c.Channels[j] = ch;
                }
                doc.Clips[i] = c;
            }
            if (!names.Contains(doc.DefaultClip)) throw Error("default_clip", "selected clip is missing");
        }
        // MiniJson supplies the tree/duplicate-key checks. This preflight adds strict
        // JSON token grammar and bounded input before allocating that tree.
        private static readonly System.Text.RegularExpressions.Regex NumericToken =
            new System.Text.RegularExpressions.Regex(@"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$", System.Text.RegularExpressions.RegexOptions.CultureInvariant);
        private static void Preflight(string json)
        {
            Bound(json.Length, 16 * 1024 * 1024, "JSON characters");
            int depth = 0;
            for (int i = 0; i < json.Length; i++) {
                char c = json[i];
                if (c == '"') {
                    bool closed = false;
                    while (++i < json.Length) {
                        c = json[i];
                        if (c < 32) throw Error("JSON", "unescaped string control character");
                        if (c == '"') { closed = true; break; }
                        if (c == '\\') {
                            if (++i >= json.Length) throw Error("JSON", "unterminated escape");
                            c = json[i];
                            if (c == 'u') {
                                for (int k = 0; k < 4; k++) {
                                    if (++i >= json.Length || !Uri.IsHexDigit(json[i])) throw Error("JSON", "invalid unicode escape");
                                }
                            } else if ("\"\\/bfnrt".IndexOf(c) < 0) throw Error("JSON", "invalid escape");
                        }
                    }
                    if (!closed) throw Error("JSON", "unterminated string");
                } else if (c == '[' || c == '{') {
                    if (++depth > 64) throw Error("JSON", "nesting limit 64 exceeded");
                } else if (c == ']' || c == '}') {
                    if (--depth < 0) throw Error("JSON", "unbalanced delimiter");
                } else if (c == '-' || (c >= '0' && c <= '9')) {
                    int start = i;
                    while (i + 1 < json.Length && "0123456789.eE+-".IndexOf(json[i + 1]) >= 0) i++;
                    if (!NumericToken.IsMatch(json.Substring(start, i - start + 1))) throw Error("JSON", "invalid number token");
                } else if (char.IsWhiteSpace(c) && c != ' ' && c != '\t' && c != '\r' && c != '\n') throw Error("JSON", "invalid whitespace");
            }
            if (depth != 0) throw Error("JSON", "unbalanced delimiter");
        }
        private static Dictionary<string, object?> Properties(object? raw, string at)
        {
            var d = Obj(raw, at); Bound(d.Count, 256, at);
            foreach (var key in d.Keys) {
                if (string.IsNullOrWhiteSpace(key) || key.Length > 256) throw Error(at, "invalid property name");
                foreach (char c in key) if (c < 32) throw Error(at, "control character in property name");
            }
            return d;
        }

    }
}
