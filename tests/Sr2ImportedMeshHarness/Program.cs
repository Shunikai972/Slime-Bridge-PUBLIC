using System;
using System.IO;
using System.Text.Json.Nodes;
using SlimeBridge.Shared;

class Program
{
    static int passed, failed;
    static JsonObject Basic() => JsonNode.Parse(@"{""schema"":""bridge-native-mesh"",""schema_version"":2,""source_game"":""SR2"",""reference_id"":""Synthetic""}")!.AsObject();
    static void Check(bool value) { if (!value) throw new Exception("assertion failed"); }
    static NativeMeshDocument Parse(JsonObject d) => NativeMeshParser.Parse(d.ToJsonString());
    static void Test(string name, Action test)
    {
        try { test(); passed++; Console.WriteLine("PASS " + name); }
        catch (Exception e) { failed++; Console.WriteLine("FAIL " + name + ": " + e.GetType().Name + " " + e.Message); }
    }
    static void Reject(string name, Action<JsonObject> change)
    {
        Test(name, () => { var d = Basic(); change(d); Bad(d.ToJsonString()); });
    }
    static void Bad(string json)
    {
        try { NativeMeshParser.Parse(json); } catch (FormatException) { return; }
        throw new Exception("expected FormatException");
    }
    static JsonObject Rig()
    {
        var d = Basic(); d["skeleton"] = JsonNode.Parse(@"{""bone_count"":2,""bones"":[{""name"":""root"",""parent"":-1},{""name"":""child"",""parent"":0,""p"":[0,0.5,0]}]}"); return d;
    }
    static void RejectRig(string name, Action<JsonObject> change)
    { Test(name, () => { var d = Rig(); change(d); Bad(d.ToJsonString()); }); }
    static JsonObject Mesh(bool skin = false)
    {
        var d = skin ? Rig() : Basic();
        var p = JsonNode.Parse(@"{""role"":""body"",""path"":""visual/body"",""mesh_name"":""triangle"",""vertices"":[[0,0,0],[1,0,0],[0,1,0]],""normals"":[[0,0,1],[0,0,1],[0,0,1]],""tangents"":[[1,0,0,1],[1,0,0,1],[1,0,0,1]],""uv"":[[0,0],[1,0],[0,1]],""uv2"":[[0,0],[1,0],[0,1]],""colors"":[[1,1,1,1],[1,1,1,1],[1,1,1,1]],""triangles"":[[0,1,2]],""local_position"":[0,1,0]}")!.AsObject();
        if (skin) {
            p["root_bone"] = "root"; p["skin_bones"] = JsonNode.Parse("[\"root\",\"child\"]");
            p["bind_poses"] = JsonNode.Parse("[[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]]");
            p["bone_weights"] = JsonNode.Parse("[[1,0,0,0,0,1,0,0],[1,0,0,0,0,1,0,0],[1,0,0,0,0,1,0,0]]");
        }
        d[skin ? "skinned_renderers" : "static_renderers"] = new JsonArray(p); return d;
    }
    static JsonObject Part(JsonObject d, bool skin = false) => d[skin ? "skinned_renderers" : "static_renderers"]![0]!.AsObject();
    static void RejectMesh(string name, Action<JsonObject> change, bool skin = false)
    { Test(name, () => { var d = Mesh(skin); change(Part(d, skin)); Bad(d.ToJsonString()); }); }
    static JsonObject MaterialDoc()
    {
        var d = Mesh(); Part(d)["materials"] = JsonNode.Parse(@"[{""name"":""face"",""shader"":""source/shader"",""face_role"":""happy"",""render_queue"":3000,""colors"":{""_Color"":[1,0.5,0,1]},""floats"":{""_Gloss"":0.5,""_Integer"":1},""textures"":[{""property"":""_MainTex"",""file"":""face.png"",""scale"":[2,1],""offset"":[0,0.25],""linear"":true}]}]"); return d;
    }
    static void RejectMaterial(string name, Action<JsonObject> change)
    { Test(name, () => { var d = MaterialDoc(); change(Part(d)["materials"]![0]!.AsObject()); Bad(d.ToJsonString()); }); }
    static JsonObject AnimationDoc()
    {
        var d = Rig(); d["source_animation"] = JsonNode.Parse(@"{""default_clip"":""idle"",""clips"":[{""name"":""idle"",""duration"":1,""sample_rate"":30,""loop"":true,""sample_count"":2,""times"":[0,1],""channels"":[{""path"":""root/child"",""property"":""local_position"",""values"":[[0,0,0],[0,1,0]]},{""path"":""root"",""property"":""local_rotation"",""values"":[[0,0,0,1],[0,0,0,1]]},{""path"":""root"",""property"":""local_scale"",""values"":[[1,1,1],[2,1,1]]}]}]}"); return d;
    }
    static JsonObject Clip(JsonObject d) => d["source_animation"]!["clips"]![0]!.AsObject();
    static void RejectAnimation(string name, Action<JsonObject> change)
    { Test(name, () => { var d = AnimationDoc(); change(d); Bad(d.ToJsonString()); }); }
    static string ManyVertices(int vertices, int parts)
    {
        var b = new System.Text.StringBuilder(); b.Append("{\"schema\":\"bridge-native-mesh\",\"schema_version\":2,\"source_game\":\"SR2\",\"reference_id\":\"synthetic\",\"static_renderers\":[");
        for (int p=0;p<parts;p++) { if(p>0)b.Append(','); b.Append("{\"vertices\":["); for (int i=0;i<vertices;i++) { if(i>0)b.Append(','); b.Append("[0,0,0]"); } b.Append("],\"triangles\":[]}"); }
        return b.Append("]}").ToString();
    }
    static string ManySamples(int samples, int channels, int clips = 1)
    {
        var d = Basic(); var bones = new JsonArray();
        for(int i=0;i<(channels+2)/3;i++) bones.Add(new JsonObject { ["name"]="b"+i, ["parent"]=-1 });
        d["skeleton"] = new JsonObject { ["bones"] = bones };
        var list = new JsonArray();
        for (int k=0;k<clips;k++) {
            var times = new JsonArray(); for(int s=0;s<samples;s++) times.Add(samples == 1 ? 0 : (double)s/(samples-1));
            var ch = new JsonArray();
            for(int i=0;i<channels;i++) { var values = new JsonArray(); for(int s=0;s<samples;s++) values.Add(JsonNode.Parse(i%3==1?"[0,0,0,1]":"[0,0,0]")); ch.Add(new JsonObject { ["path"]="b"+(i/3), ["property"]=new [] {"local_position","local_rotation","local_scale"}[i%3], ["values"]=values }); }
            list.Add(new JsonObject { ["name"]="c"+k, ["duration"]=1, ["sample_rate"]=30, ["times"]=times, ["channels"]=ch });
        }
        d["source_animation"] = new JsonObject { ["default_clip"]="c0", ["clips"]=list }; return d.ToJsonString();
    }
    static int Main(string[] args)
    {
        Test("minimal document defaults", () => {
            var d = Parse(Basic()); Check(d.SourceGame == "SR2" && d.ReferenceId == "Synthetic" && d.PrefabScale == 1);
            Check(d.Bones.Length == 0 && d.Parts.Length == 0 && d.Clips.Length == 0 && d.DefaultClip == "");
            Check(d.VisualTransform.Position[0] == 0 && d.VisualTransform.Rotation[3] == 1 && d.VisualTransform.Scale[2] == 1);
        });
        Test("mixed integer and decimal metadata", () => {
            var d = Basic(); d["prefab_scale"] = 2.5;
            d["visual_transform"] = JsonNode.Parse(@"{""local_position"":[1,2.5,0],""local_rotation"":[0,0,0,1],""local_scale"":[1,2,1]}");
            var p = Parse(d); Check(p.PrefabScale == 2.5f && p.VisualTransform.Position[1] == 2.5f && p.VisualTransform.Scale[1] == 2);
        });
        Reject("wrong schema", d => d["schema"] = "other");
        Reject("wrong version", d => d["schema_version"] = 1);
        Reject("fractional version", d => d["schema_version"] = 2.1);
        Reject("missing version", d => d.Remove("schema_version"));
        Reject("empty reference", d => d["reference_id"] = "");
        Reject("numeric source game", d => d["source_game"] = 2);
        Reject("negative prefab scale", d => d["prefab_scale"] = -1);
        Reject("zero prefab scale", d => d["prefab_scale"] = 0);
        Reject("string prefab scale", d => d["prefab_scale"] = "1");
        Reject("null optional transform", d => d["visual_transform"] = null);
        Reject("wrong position width", d => d["visual_transform"] = JsonNode.Parse(@"{""local_position"":[0,0]}"));
        Reject("zero quaternion", d => d["visual_transform"] = JsonNode.Parse(@"{""local_rotation"":[0,0,0,0]}"));
        Reject("nonunit quaternion", d => d["visual_transform"] = JsonNode.Parse(@"{""local_rotation"":[0,0,0,2]}"));
        Test("nonfinite number", () => Bad(Basic().ToJsonString().Replace("Synthetic", "Synthetic").Replace("\"reference_id\":\"Synthetic\"", "\"reference_id\":\"Synthetic\",\"prefab_scale\":1e999")));
        Test("float overflow", () => Bad(Basic().ToJsonString().Replace("\"reference_id\":\"Synthetic\"", "\"reference_id\":\"Synthetic\",\"prefab_scale\":1e40")));
        Test("null JSON", () => Bad(null!));
        Test("nonobject JSON", () => Bad("[]"));
        Test("bone paths and defaults", () => {
            var p = Parse(Rig()); Check(p.Bones.Length == 2 && p.Bones[1].Path == "root/child" && p.Bones[1].Transform.Position[1] == 0.5f && p.Bones[0].Parent == -1);
        });
        Test("forward parent reference", () => { var d = Rig(); d["skeleton"]!["bones"] = JsonNode.Parse(@"[{""name"":""child"",""parent"":1},{""name"":""root"",""parent"":-1}]"); Check(Parse(d).Bones[0].Path == "root/child"); });
        RejectRig("bone count mismatch", d => d["skeleton"]!["bone_count"] = 3);
        RejectRig("bone invalid parent", d => d["skeleton"]!["bones"]![1]!["parent"] = 2);
        RejectRig("bone negative parent", d => d["skeleton"]!["bones"]![1]!["parent"] = -2);
        RejectRig("bone fractional parent", d => d["skeleton"]!["bones"]![1]!["parent"] = 0.5);
        RejectRig("bone self cycle", d => d["skeleton"]!["bones"]![1]!["parent"] = 1);
        RejectRig("bone mutual cycle", d => d["skeleton"]!["bones"]![0]!["parent"] = 1);
        RejectRig("bone duplicate name", d => d["skeleton"]!["bones"]![1]!["name"] = "root");
        RejectRig("bone empty name", d => d["skeleton"]!["bones"]![1]!["name"] = "");
        RejectRig("bone slash name", d => d["skeleton"]!["bones"]![1]!["name"] = "a/b");
        RejectRig("bone invalid quaternion", d => d["skeleton"]!["bones"]![1]!["q"] = JsonNode.Parse("[0,0,0,0]"));
        Test("bone limit accepted", () => { var d = Basic(); var a = new JsonArray(); for (int i=0;i<1024;i++) a.Add(new JsonObject { ["name"] = "b"+i, ["parent"] = -1 }); d["skeleton"] = new JsonObject { ["bones"] = a }; Check(Parse(d).Bones.Length == 1024); });
        Test("bone limit rejected", () => { var d = Basic(); var a = new JsonArray(); for (int i=0;i<1025;i++) a.Add(new JsonObject { ["name"] = "b"+i, ["parent"] = -1 }); d["skeleton"] = new JsonObject { ["bones"] = a }; Bad(d.ToJsonString()); });
        Test("static mesh fields", () => { var p = Parse(Mesh()).Parts[0]; Check(!p.Skinned && p.Enabled && p.Role == "body" && p.Path == "visual/body" && p.MeshName == "triangle" && p.Transform.Position[1] == 1); Check(p.Vertices.Length == 3 && p.Normals[0][2] == 1 && p.Tangents[0].Length == 4 && p.Uv.Length == 3 && p.Uv2.Length == 3 && p.Colors[0][3] == 1 && p.Triangles[0][2] == 2 && p.Materials.Length == 0); });
        Test("skinned mesh palette", () => { var d = Parse(Mesh(true)); var p = d.Parts[0]; Check(p.Skinned && p.RootBone == "root" && p.SkinBones.Length == 2 && p.BindPoses.Length == 2 && p.BoneWeights.Length == 3 && p.BoneWeights[0][3] == 0 && p.BindPoses[1].Length == 16); });
        RejectMesh("triangle nontriplet", p => p["triangles"] = JsonNode.Parse("[[0,1]]"));
        RejectMesh("triangle out of range", p => p["triangles"] = JsonNode.Parse("[[0,1,3]]"));
        RejectMesh("triangle negative", p => p["triangles"] = JsonNode.Parse("[[-1,1,2]]"));
        RejectMesh("triangle fractional", p => p["triangles"] = JsonNode.Parse("[[0,1,1.5]]"));
        RejectMesh("optional row count", p => p["normals"] = JsonNode.Parse("[[0,0,1]]"));
        RejectMesh("vertex wrong width", p => p["vertices"] = JsonNode.Parse("[[0,0]]"));
        RejectMesh("missing vertices", p => p.Remove("vertices"));
        RejectMesh("empty vertices", p => p["vertices"] = new JsonArray());
        RejectMesh("enabled not boolean", p => p["enabled"] = 1);
        RejectMesh("skin root unresolved", p => p["root_bone"] = "missing", true);
        RejectMesh("skin bone unresolved", p => p["skin_bones"]![1] = "missing", true);
        RejectMesh("skin duplicate palette", p => p["skin_bones"]![1] = "root", true);
        RejectMesh("skin missing palette", p => p.Remove("skin_bones"), true);
        RejectMesh("bind pose width", p => p["bind_poses"]![0] = JsonNode.Parse("[1,0]"), true);
        RejectMesh("bind pose count", p => p["bind_poses"]!.AsArray().RemoveAt(1), true);
        RejectMesh("weight count", p => p["bone_weights"]!.AsArray().RemoveAt(1), true);
        RejectMesh("weight width", p => p["bone_weights"]![0] = JsonNode.Parse("[0,1]"), true);
        RejectMesh("weight negative", p => p["bone_weights"]![0]![0] = -1, true);
        RejectMesh("weight unnormalized", p => p["bone_weights"]![0]![0] = 0.5, true);
        RejectMesh("weight index fractional", p => p["bone_weights"]![0]![4] = 0.5, true);
        RejectMesh("weight index out of range", p => p["bone_weights"]![0]![4] = 2, true);
        RejectMesh("zero weight slot index out of range", p => p["bone_weights"]![0]![7] = 9, true);
        Test("optional empty rows and disabled", () => { var d = Mesh(); var p = Part(d); foreach (var key in new [] { "normals", "tangents", "uv", "uv2", "colors" }) p[key] = new JsonArray(); p["enabled"] = false; Check(!Parse(d).Parts[0].Enabled && Parse(d).Parts[0].Normals.Length == 0); });
        Test("material metadata and texture", () => { var m = Parse(MaterialDoc()).Parts[0].Materials[0]; Check(m.Name == "face" && m.Shader == "source/shader" && m.FaceRole == "happy" && m.RenderQueue == 3000 && m.Colors["_Color"][1] == 0.5f && m.Floats["_Integer"] == 1); var t = m.Textures[0]; Check(t.Property == "_MainTex" && t.File == "face.png" && t.Linear && t.Scale[0] == 2 && t.Offset[1] == 0.25f); });
        RejectMaterial("color width", m => m["colors"]!["_Color"] = JsonNode.Parse("[1,1,1]"));
        RejectMaterial("float wrong type", m => m["floats"]!["_Gloss"] = "1");
        RejectMaterial("queue fractional", m => m["render_queue"] = 2000.1);
        RejectMaterial("texture width", m => m["textures"]![0]!["offset"] = JsonNode.Parse("[0]"));
        RejectMaterial("texture missing property", m => m["textures"]![0]!.AsObject().Remove("property"));
        foreach (var file in new [] { "../face.png", "..\\face.png", "/face.png", "C:face.png", "sub/face.png", "face.png:stream", "CON.png", "face.png.", "face?.png", "" }) {
            var captured = file; RejectMaterial("unsafe filename " + captured, m => m["textures"]![0]!["file"] = captured);
        }
        Test("source texture color space", () => { var d = MaterialDoc(); var t = Part(d)["materials"]![0]!["textures"]![0]!.AsObject(); t.Remove("linear"); t["source_color_space"] = 0; Check(Parse(d).Parts[0].Materials[0].Textures[0].Linear); t["source_color_space"] = 1; Check(!Parse(d).Parts[0].Materials[0].Textures[0].Linear); });
        Test("sampled animation channels", () => { var d = Parse(AnimationDoc()); var c = d.Clips[0]; Check(d.DefaultClip == "idle" && c.Name == "idle" && c.Duration == 1 && c.SampleRate == 30 && c.Loop && c.Times.Length == 2 && c.Channels[0].Values[1][1] == 1 && c.Channels[0].Path == "root/child" && c.Channels[0].Property == "local_position"); });
        RejectAnimation("missing selected clip", d => d["source_animation"]!["default_clip"] = "missing");
        RejectAnimation("missing default", d => d["source_animation"]!.AsObject().Remove("default_clip"));
        RejectAnimation("empty animation clips", d => d["source_animation"]!["clips"] = new JsonArray());
        RejectAnimation("duplicate clip name", d => d["source_animation"]!["clips"]!.AsArray().Add(Clip(d).DeepClone()));
        RejectAnimation("negative duration", d => Clip(d)["duration"] = -1);
        RejectAnimation("excess duration", d => Clip(d)["duration"] = 3601);
        RejectAnimation("zero sample rate", d => Clip(d)["sample_rate"] = 0);
        RejectAnimation("excess sample rate", d => Clip(d)["sample_rate"] = 241);
        RejectAnimation("time start nonzero", d => Clip(d)["times"]![0] = 0.1);
        RejectAnimation("time not ascending", d => Clip(d)["times"]![1] = 0);
        RejectAnimation("time past duration", d => Clip(d)["times"]![1] = 2);
        RejectAnimation("sample count mismatch", d => Clip(d)["sample_count"] = 3);
        RejectAnimation("unresolved channel path", d => Clip(d)["channels"]![0]!["path"] = "child");
        RejectAnimation("channel property invalid", d => Clip(d)["channels"]![0]!["property"] = "world_position");
        RejectAnimation("duplicate channel", d => Clip(d)["channels"]!.AsArray().Add(Clip(d)["channels"]![0]!.DeepClone()));
        RejectAnimation("channel sample count", d => Clip(d)["channels"]![0]!["values"]!.AsArray().RemoveAt(1));
        RejectAnimation("channel wrong width", d => Clip(d)["channels"]![0]!["values"]![0] = JsonNode.Parse("[0,0]"));
        RejectAnimation("channel zero quaternion", d => Clip(d)["channels"]![1]!["values"]![0] = JsonNode.Parse("[0,0,0,0]"));
        Test("effects ignored", () => { var d = AnimationDoc(); d["source_effects"] = "unrecognized ignored payload"; Check(Parse(d).DefaultClip == "idle"); });
        Test("schema weights precede four indices", () => { var d = Mesh(true); Part(d,true)["bone_weights"]![0] = JsonNode.Parse("[0.75,0.25,0,0,0,1,0,0]"); var row = Parse(d).Parts[0].BoneWeights[0]; Check(row[0] == 0.75f && row[4] == 0 && row[5] == 1); });
        Test("part limit accepted", () => Check(NativeMeshParser.Parse(ManyVertices(1,256)).Parts.Length == 256));
        Test("part limit rejected", () => Bad(ManyVertices(1,257)));
        Test("vertex limit accepted", () => Check(NativeMeshParser.Parse(ManyVertices(100000,1)).Parts[0].Vertices.Length == 100000));
        Test("vertex limit rejected", () => Bad(ManyVertices(100001,1)));
        Test("aggregate vertices accepted", () => Check(NativeMeshParser.Parse(ManyVertices(100000,5)).Parts.Length == 5));
        Test("aggregate vertices rejected", () => Bad(ManyVertices(100000,6)));
        Test("empty mesh rejected", () => Bad(ManyVertices(0,1)));
        Test("sample limit accepted", () => Check(NativeMeshParser.Parse(ManySamples(4096,1)).Clips[0].Times.Length == 4096));
        Test("sample limit rejected", () => Bad(ManySamples(4097,1)));
        Test("channel limit accepted", () => Check(NativeMeshParser.Parse(ManySamples(1,3072)).Clips[0].Channels.Length == 3072));
        Test("aggregate samples accepted", () => Check(NativeMeshParser.Parse(ManySamples(4096,64)).Clips.Length == 1));
        Test("aggregate samples rejected", () => Bad(ManySamples(4096,64,2)));
        Test("clip limit accepted", () => Check(NativeMeshParser.Parse(ManySamples(1,1,64)).Clips.Length == 64));
        Test("clip limit rejected", () => Bad(ManySamples(1,1,65)));
        Test("leading zero JSON number", () => Bad(Basic().ToJsonString().Replace("\"schema_version\":2", "\"schema_version\":02")));
        Test("trailing decimal JSON number", () => Bad(Basic().ToJsonString().Replace("\"schema_version\":2", "\"schema_version\":2.")));
        Test("unescaped control JSON string", () => Bad(Basic().ToJsonString().Replace("Synthetic", "Syn\u0001thetic")));
        Test("nonJSON whitespace", () => Bad("\u00a0" + Basic().ToJsonString()));
        Test("JSON nesting limit", () => { var s = new string('[',65) + "0" + new string(']',65); var d = Basic().ToJsonString(); Bad(d.Substring(0,d.Length-1)+",\"unknown\":"+s+"}"); });
        Test("JSON size limit", () => Bad(Basic().ToJsonString() + new string(' ',16*1024*1024)));
        Test("float underflow rejected", () => { var d = Basic(); d["visual_transform"] = JsonNode.Parse("{\"local_position\":[1e-50,0,0]}"); Bad(d.ToJsonString()); });
        Test("JSON duplicate keys rejected", () => Bad(Basic().ToJsonString().Replace("\"schema_version\":2", "\"schema_version\":2,\"schema_version\":2")));
        Test("JSON trailing content rejected", () => Bad(Basic().ToJsonString()+"false"));
        RejectMaterial("empty material property", m => m["floats"]![""] = 1);
        RejectMaterial("unsafe device stem space", m => m["textures"]![0]!["file"] = "CON .png");
        RejectMaterial("unsafe superscript device", m => m["textures"]![0]!["file"] = "COM¹.png");
        RejectMaterial("material property limit", m => { var values=new JsonObject(); for(int i=0;i<257;i++) values["p"+i]=1; m["floats"]=values; });
        RejectMaterial("texture limit", m => { var a = new JsonArray(); for(int i=0;i<257;i++) a.Add(new JsonObject { ["property"]="p"+i, ["file"]="face.png" }); m["textures"]=a; });
        RejectMesh("material limit", p => { var a = new JsonArray(); for(int i=0;i<257;i++) a.Add(new JsonObject()); p["materials"]=a; });

        Console.WriteLine("RESULT " + passed + " passed; " + failed + " failed");
        if (args.Length == 2 && args[0] == "--parse-cache") {
            var d = NativeMeshParser.Parse(File.ReadAllText(args[1]));
            Console.WriteLine("CACHE bones=" + d.Bones.Length + " parts=" + d.Parts.Length + " clips=" + d.Clips.Length + " default=" + d.DefaultClip);
        }
        return failed == 0 ? 0 : 1;
    }
}
