using SlimeBridge.Shared;
var passed = 0;
void Check(bool yes, string label) { if (!yes) throw new Exception(label); passed++; }
var keys = NativeMeshAnimationCurves.Build(new float[] {0, 0.5f}, new float[][] {new float[] {0,0,0},new float[] {1,0,0}}, 0, 1, false, false);
Check(keys.Length == 3, "hold endpoint at authored duration");
Check(keys[1].InTangent == 2 && keys[0].OutTangent == 2, "linear sample slopes");
Check(keys[2].Time == 1 && keys[2].Value == 1, "non-loop holds endpoint");
var loop = NativeMeshAnimationCurves.Build(new float[] {0, 0.5f}, new float[][] {new float[] {0,0,0},new float[] {1,0,0}}, 0, 1, true, false);
Check(loop[^1].Value == 0 && loop[1].OutTangent == -2, "loop closes to authored start");
var sign = NativeMeshAnimationCurves.Build(new float[] {0, 1}, new float[][] {new float[] {0,0,0,1},new float[] {0,0,0,-1}}, 3, 1, false, true);
Check(sign[1].Value == 1, "quaternion hemisphere preserved");
var repo = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../../"));
var cacheDir = Path.Combine(repo, "Lab/RuntimeArtifacts/synthetic-visual-harness");
Directory.CreateDirectory(cacheDir);
string model = "{\"schema\":\"bridge-native-mesh\",\"schema_version\":2,\"source_game\":\"SR1\",\"reference_id\":\"RAD_SLIME\",\"static_renderers\":[{\"vertices\":[[0,0,0],[1,0,0],[0,1,0]],\"triangles\":[[0,1,2]]}]}";
File.WriteAllText(Path.Combine(cacheDir, "model.json"), model);
string Digest(string file) => Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Path.Combine(cacheDir,file)))).ToLowerInvariant();
void Manifest(string canonical = "slime.rad", string hash = "") => File.WriteAllText(Path.Combine(cacheDir, "cache-manifest.json"), "{\"schema\":\"bridge-asset-cache-manifest\",\"schema_version\":1,\"canonical_id\":\"" + canonical + "\",\"files\":{\"model.json\":\"" + (hash == "" ? Digest("model.json") : hash) + "\"}}");
Manifest();
var cache = NativeMeshCache.Load(repo, cacheDir);
Check(cache.CanonicalId == "slime.rad" && cache.Document.Parts.Length == 1, "validated cache reads pure model");
Check(cache.ModelSha256 == Digest("model.json"), "hash binds actual model bytes");
void Refuse(Action action, string label) { try {action();} catch (FormatException) { passed++; return;} throw new Exception("accepted: " + label); }
Manifest(hash: new string('0',64));
Refuse(() => NativeMeshCache.Load(repo,cacheDir), "model digest mismatch");
Manifest();
Refuse(() => NativeMeshCache.Load(repo, Path.Combine(repo,"tests")), "outside LAB cache");
File.WriteAllText(Path.Combine(cacheDir,"cache-manifest.json"), "{\"schema\":\"wrong\",\"canonical_id\":\"slime.rad\",\"files\":{}}");
Refuse(() => NativeMeshCache.Load(repo,cacheDir), "manifest wrong schema");
Manifest();
string textured = model.Replace("\"triangles\":[[0,1,2]]", "\"triangles\":[[0,1,2]],\"materials\":[{\"textures\":[{\"property\":\"_MainTex\",\"file\":\"mask.png\"}]}]");
File.WriteAllText(Path.Combine(cacheDir,"model.json"), textured); Manifest();
Refuse(() => NativeMeshCache.Load(repo,cacheDir), "texture not covered by manifest");
File.WriteAllText(Path.Combine(cacheDir,"model.json"),model); Manifest();
Check(NativeMeshVisualPolicy.InScope("body") && !NativeMeshVisualPolicy.InScope("source-effect"), "radioactive geometry is out of milestone");
var eyes = new NativeMeshMaterial { Shader = "SR/AMP/Slime/Face/Eyes Glow" };
var mouth = new NativeMeshMaterial { Name = "mouthHappy" };
mouth.Colors["_MouthMid"] = new float[]{0,0,0,1};
Check(NativeMeshVisualPolicy.Family(eyes,"body") == "eyes", "multipass body mesh eyes remain eyes");
Check(NativeMeshVisualPolicy.Family(mouth,"body") == "mouth", "multipass body mesh mouth remains mouth");
Check(NativeMeshVisualPolicy.IsRequiredTexture("_FaceAtlas"), "source face atlas is not optional");
Check(!NativeMeshVisualPolicy.IsRequiredTexture("_Noise") && !NativeMeshVisualPolicy.IsRequiredTexture("_Drips"), "nonportable shader effects are optional, not invented");
var manifestPath=Path.Combine(cacheDir,"cache-manifest.json");
File.WriteAllText(manifestPath,File.ReadAllText(manifestPath).Replace("\"schema_version\":1", "\"schema_version\":1.0"));
Check(NativeMeshCache.Load(repo,cacheDir).CanonicalId=="slime.rad", "manifest integer-valued decimal version accepted");
Refuse(() => NativeMeshAnimationCurves.Build(new float[]{0,1},new float[][]{new float[]{-3e38f,0,0},new float[]{3e38f,0,0}},0,1,false,false), "overflow slope rejected before native AnimationCurve");
var exactClosing = NativeMeshAnimationCurves.Build(new float[]{0,1},new float[][]{new float[]{0,0,0},new float[]{1,0,0}},0,1,true,false);
Check(exactClosing.Length == 2 && exactClosing[1].Value == 0, "loop closes at exact duration without adding a duplicate-time key");
var exactClosingRotation = NativeMeshAnimationCurves.Build(new float[]{0,1},new float[][]{new float[]{0,0,0,1},new float[]{0,0.70710677f,0,0.70710677f}},3,1,true,true);
Check(exactClosingRotation.Length == 2 && exactClosingRotation[1].Value == 1, "quaternion loop closes at exact duration");
var exactHold = NativeMeshAnimationCurves.Build(new float[]{0,1},new float[][]{new float[]{0,0,0},new float[]{1,0,0}},0,1,false,false);
Check(exactHold.Length == 2 && exactHold[1].Value == 1, "nonloop exact endpoint preserved");
var exactLoop = NativeMeshAnimationCurves.Build(new float[]{0,1},new float[][]{new float[]{2,0,0},new float[]{2,0,0}},0,1,true,false);
Check(exactLoop.Length == 2 && exactLoop[1].Value == 2, "closed authored loop at exact duration preserved");
var exactRotation = NativeMeshAnimationCurves.Build(new float[]{0,1},new float[][]{new float[]{0,0,0,1},new float[]{0,0,0,-1}},3,1,true,true);
Check(exactRotation.Length == 2 && exactRotation[1].Value == 1, "equivalent quaternion endpoint is a closed loop");
Console.WriteLine($"{passed} passed, 0 failed (pure curves/cache; no Unity runtime)");
if(args.Length==3 && args[0]=="--check-cache")
{
    var actual=NativeMeshCache.Load(args[1],args[2]);
    var parts=actual.Document.Parts.Where(p=>NativeMeshVisualPolicy.InScope(p.Role)).ToArray();
    foreach(var clip in actual.Document.Clips.Where(c=>c.Name==actual.Document.DefaultClip))
        foreach(var channel in clip.Channels)
            for(int c=0;c<channel.Values[0].Length;c++)
                NativeMeshAnimationCurves.Build(clip.Times,channel.Values,c,clip.Duration,clip.Loop,channel.Property=="local_rotation");
    Console.WriteLine($"OFFLINE_CACHE_VALIDATED canonical={actual.CanonicalId} model_sha256={actual.ModelSha256} skeleton={actual.Document.Bones.Length} milestone_parts={parts.Length} vertices={parts.Sum(p=>p.Vertices.Length)} skin_bones={parts.Sum(p=>p.SkinBones.Length)} default_clip={actual.Document.DefaultClip} source_textures={actual.Textures.Count}");
}
