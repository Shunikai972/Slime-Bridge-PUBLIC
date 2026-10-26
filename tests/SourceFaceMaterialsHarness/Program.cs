using SlimeBridge.SR1;
using SlimeBridge.Shared;
using UnityEngine;

var json = """
[
{"face_role":"base-null","name":"Null Render","shader":"SR/Null Render","colors":{},"floats":{},"textures":[],"render_queue":-1,"keywords":[]},
{"face_role":"eyes","name":"eyeHappy","shader":"SR/AMP/Slime/Face/Eyes Default","colors":{"_EyeRed":[1,0.1,0,1]},"floats":{"_EyeSmoothStepBase":0.5},"textures":[{"property":"_FaceAtlas","file":"eye.png","scale":[1,1],"offset":[0,0]}],"render_queue":2475,"keywords":[]},
{"face_role":"mouth","name":"mouthTabby","shader":"SR/AMP/Slime/Face/Mouth Default","colors":{"_MouthColor":[0,0,0,1]},"floats":{"_MouthSmoothStepBase":0.5},"textures":[{"property":"_FaceAtlas","file":"mouth.png","scale":[1,1],"offset":[0,0]}],"render_queue":2475,"keywords":[]}
]
""";
var rows = (List<object>)MiniJson.Parse(json)!;
Texture2D? Load(string file) => new Texture2D { name = file };
if (!SourceFaceMaterials.TryBuild(rows, Load, out var materials, out var owned, out var reason)) throw new Exception(reason);
if (materials.Length != 3 || materials[0].shader.name != "SR/Null Render" ||
    materials[1].shader.name != "SR/AMP/Slime/Face/Eyes Default" ||
    materials[2].shader.name != "SR/AMP/Slime/Face/Mouth Default") throw new Exception("layered roles lost");
if (materials[1].textures["_FaceAtlas"].name != "eye.png" || materials[2].textures["_FaceAtlas"].name != "mouth.png")
    throw new Exception("slot atlas collapsed");
if (materials[2].floats["_MouthSmoothStepBase"] != .5f || owned.Count != 5) throw new Exception("native values/ownership lost");
var checks = 3;
// Generic source metadata can encode identifiers in finite color components;
// those are not spatial coordinates and must not inherit a pose/UV bound.
var withOpaqueMetadata = json.Replace("\"_MouthColor\":[0,0,0,1]", "\"_MouthColor\":[0,0,0,1],\"_OpaqueReference\":[1e20,0,0,0]");
Material.MissingProperty = "_OpaqueReference";
if (!SourceFaceMaterials.TryBuild((List<object>)MiniJson.Parse(withOpaqueMetadata)!, Load,
    out var metadataMaterials, out var metadataOwned, out var metadataProblem)) throw new Exception("finite unsupported source metadata rejected: " + metadataProblem);
if (metadataMaterials[2].colors.ContainsKey("_OpaqueReference")) throw new Exception("unsupported source metadata applied to target shader");
foreach (var resource in metadataOwned) UnityEngine.Object.Destroy(resource);
Material.MissingProperty = null;
if (!SourceFaceMaterials.TryBuild((List<object>)MiniJson.Parse(withOpaqueMetadata)!, Load,
    out var supportedMetadata, out var supportedOwned, out var supportedProblem)) throw new Exception(supportedProblem);
if (supportedMetadata[2].colors["_OpaqueReference"].r != 1e20f) throw new Exception("finite supported source metadata was clamped");
foreach (var resource in supportedOwned) UnityEngine.Object.Destroy(resource);
checks += 3;
void Refuse(string candidate, Func<string,Texture2D?>? loader = null)
{
    var before = UnityEngine.Object.Created.Count;
    if (SourceFaceMaterials.TryBuild((List<object>)MiniJson.Parse(candidate)!, loader ?? Load,
        out var refused, out var resources, out var issue) || refused.Length != 0 || resources.Count != 0 || issue.Length == 0)
        throw new Exception("invalid source face accepted");
    if (UnityEngine.Object.Created.Skip(before).Any(resource => !(resource is Shader) && !resource.Destroyed)) throw new Exception("failed face build leaked owned resources");
    checks++;
}
Refuse(json.Replace("\"face_role\":\"mouth\"", "\"face_role\":\"eyes\""));
var glowEyeTextures = "{\"property\":\"_FaceAtlas\",\"file\":\"eye.png\",\"scale\":[1,1],\"offset\":[0,0]},{\"property\":\"_GlowNoise\",\"file\":\"noise.png\",\"scale\":[1,1],\"offset\":[0,0]}";
var glowJson = json
    .Replace("\"shader\":\"SR/AMP/Slime/Face/Eyes Default\"", "\"shader\":\"SR/AMP/Slime/Face/Eyes Glow\"")
    .Replace("\"textures\":[{\"property\":\"_FaceAtlas\",\"file\":\"eye.png\",\"scale\":[1,1],\"offset\":[0,0]}]", "\"textures\":[" + glowEyeTextures + "]");
if (!SourceFaceMaterials.TryBuild((List<object>)MiniJson.Parse(glowJson)!, Load, out var glowMaterials,
    out var glowOwned, out var glowProblem)) throw new Exception("declared glow eyes refused: " + glowProblem);
if (glowMaterials.Length != 3 || glowMaterials[1].shader.name != "SR/AMP/Slime/Face/Eyes Glow" ||
    glowMaterials[1].textures["_FaceAtlas"].name != "eye.png" ||
    glowMaterials[1].textures["_GlowNoise"].name != "noise.png" ||
    glowOwned.Count != 6) throw new Exception("glow eye family lost");
foreach (var resource in glowOwned) UnityEngine.Object.Destroy(resource);
checks++;
Refuse(json.Replace("Face/Eyes Default", "Face/Eyes Feral"));
var battyJson = """[{"face_role":"base-null","name":"Null Render","source_path_id":494,"shader":"SR/Null Render","colors":{},"floats":{},"render_queue":-1,"keywords":[],"textures":[]},{"face_role":"eyes","name":"eyeHappyBat","source_path_id":555,"shader":"SR/AMP/Slime/Face/Eyes Glow","colors":{"_Color":[1.0,1.0,1.0,1.0],"_DoubleSidedConstants":[1.0,1.0,-1.0,0.0],"_EmissionColor":[1.0,1.0,1.0,1.0],"_EyeBlue":[0.9528301954269409,0.8915228247642517,0.8764240145683289,1.0],"_EyeGreen":[0.3137255012989044,0.12156859785318375,0.027450980618596077,1.0],"_EyeRed":[0.11372549831867218,0.03921569138765335,0.0117647098377347,1.0],"_EyeTwinBlue":[0.9528301358222961,0.8915228843688965,0.8764240145683289,1.0],"_EyeTwinGreen":[0.0,0.8916162848472595,1.0,1.0],"_EyeTwinRed":[0.11792445927858353,0.4177149832248688,1.0,1.0],"_GlowColor":[1.0,0.2235294133424759,0.2862745225429535,1.0],"_MouthBot":[0.7529413104057312,0.1450980007648468,0.2588235139846802,1.0],"_MouthMid":[0.49411770701408386,0.01568627916276455,0.019607840105891228,1.0],"_MouthTop":[0.24705879390239716,0.0,0.13725489377975464,1.0],"_NoiseMapPannerSpeed":[0.0,0.0,0.0,0.0],"_NoiseMapSize":[512.0,512.0,0.0,0.0],"_RingPannerSpeed":[0.0,0.0,0.0,0.0],"_Tint":[0.0,0.0,0.0,0.0]},"floats":{"_AddPrecomputedVelocity":1.0,"_AlphaCutoff":0.5,"_AlphaCutoffEnable":1.0,"_AlphaDstBlend":0.0,"_AlphaSrcBlend":1.0,"_AlphaToMask":0.0,"_AlphaToMaskInspectorValue":0.0,"_BlendMode":0.0,"_BumpScale":1.0,"_CullMode":2.0,"_CullModeForward":2.0,"_Cutoff":0.5,"_DepthBlue":0.0,"_DepthGreen":0.0,"_DetailNormalMapScale":1.0,"_DistortionEnable":0.0,"_DistortionOnly":0.0,"_DoubleSidedEnable":0.0,"_DoubleSidedNormalMode":2.0,"_DstBlend":0.0,"_EmissionLM":0.0,"_EnableFogOnTransparent":1.0,"_EnableGlow":1.0,"_EnableHorizontalDivision":0.0,"_EnableTwinEffect":0.0,"_EyeGlare":0.20000000298023224,"_EyeSmoothStepBase":0.16699999570846558,"_FreezeTwinSplit":0.0,"_GlossMapScale":1.0,"_Glossiness":0.5,"_GlossyReflections":1.0,"_GlowNoiseSpeed":1.0,"_HeldInVac":0.0,"_Metallic":0.0,"_Mode":0.0,"_MouthSmoothStepBase":0.699999988079071,"_NoiseMapStrength":0.0,"_OcclusionStrength":1.0,"_OpaqueCullMode":2.0,"_Parallax":0.019999999552965164,"_ReceivesSSR":0.0,"_ReceivesSSRTransparent":0.0,"_RenderQueueType":1.0,"_RequireSplitLighting":0.0,"_ScaleAmplitude":0.05000000074505806,"_SmoothnessTextureChannel":0.0,"_SpecularHighlights":1.0,"_SrcBlend":1.0,"_StencilRef":0.0,"_StencilRefDepth":0.0,"_StencilRefDistortionVec":4.0,"_StencilRefGBuffer":2.0,"_StencilRefMV":32.0,"_StencilWriteMask":6.0,"_StencilWriteMaskDepth":8.0,"_StencilWriteMaskDistortionVec":4.0,"_StencilWriteMaskGBuffer":14.0,"_StencilWriteMaskMV":40.0,"_SupportDecals":1.0,"_SurfaceType":0.0,"_TessEdgeLength":16.0,"_TessMax":25.0,"_TessMaxDisp":25.0,"_TessMin":10.0,"_TessPhongStrength":0.5,"_TessValue":16.0,"_TransparentBackfaceEnable":0.0,"_TransparentCullMode":2.0,"_TransparentSortPriority":0.0,"_TransparentZWrite":1.0,"_TwinEffectOffset":0.0,"_UVSec":0.0,"_UnscaledTime":0.0,"_UseShadowThreshold":0.0,"_WiggleAmplitude":0.009999999776482582,"_XScaleSpeed":2.0,"_XWiggleSpeed":0.0,"_YScaleSpeed":-1.0,"_YWiggleSpeed":-2.0,"_ZTestDepthEqualForOpaque":3.0,"_ZTestGBuffer":3.0,"_ZTestTransparent":4.0,"_ZWrite":1.0,"__dirty":0.0},"render_queue":2475,"keywords":["_ALPHATEST_ON","_ADD_PRECOMPUTED_VELOCITY","_DISABLE_SSR","_DISABLE_SSR_TRANSPARENT","_EMISSION"],"textures":[{"property":"_FaceAtlas","file":"tex-source-1678-face_bat_happy.png","source_name":"face_bat_happy","scale":[1.0,1.0],"offset":[0.0,0.0],"source_texture_settings":{"m_FilterMode":1,"m_Aniso":1,"m_MipBias":0.0,"m_WrapU":0,"m_WrapV":0,"m_WrapW":0},"source_color_space":1,"mip_count":1},{"property":"_GlowNoise","file":"tex-source-1670-perlinnoise.png","source_name":"perlinnoise","scale":[1.0,1.0],"offset":[0.0,0.0],"source_texture_settings":{"m_FilterMode":1,"m_Aniso":1,"m_MipBias":0.0,"m_WrapU":0,"m_WrapV":0,"m_WrapW":0},"source_color_space":1,"mip_count":1}]},{"face_role":"mouth","name":"mouthHappyBat","source_path_id":559,"shader":"SR/AMP/Slime/Face/Mouth Default","colors":{"_Color":[0.0,0.0,0.0,1.0],"_DiffusionProfile1_Asset":[-4.299509424703085e-14,-1.1161089895070622e-26,285614586986496.0,5.096265130613185e-18],"_DiffusionProfile1_asset":[-4.299509424703085e-14,-1.1161089895070622e-26,285614586986496.0,5.096265130613185e-18],"_DiffusionProfile_Asset":[-4.299509424703085e-14,-1.1161089895070622e-26,285614586986496.0,5.096265130613185e-18],"_DoubleSidedConstants":[1.0,1.0,-1.0,0.0],"_EmissionColor":[1.0,1.0,1.0,1.0],"_GlowColor":[1.0,0.2235294133424759,0.2862745225429535,1.0],"_MouthBot":[0.8584905862808228,0.4170968532562256,0.4981691539287567,1.0],"_MouthBotTwin":[0.3607841730117798,0.9426007866859436,0.9607842564582825,1.0],"_MouthMid":[0.49411770701408386,0.01568627916276455,0.019607840105891228,1.0],"_MouthMidTwin":[0.11792445927858353,0.4177149832248688,1.0,1.0],"_MouthTop":[0.24705879390239716,0.0,0.13725489377975464,1.0],"_MouthTopTwin":[0.05098036676645279,0.19746577739715576,0.5754716396331787,1.0]},"floats":{"_":0.10000000149011612,"_AddPrecomputedVelocity":1.0,"_AlphaCutoff":0.5,"_AlphaCutoffEnable":1.0,"_AlphaDstBlend":0.0,"_AlphaSrcBlend":1.0,"_AlphaToMask":0.0,"_AlphaToMaskInspectorValue":0.0,"_BlendMode":0.0,"_CullMode":2.0,"_CullModeForward":2.0,"_Cutoff":0.5,"_DiffusionProfile":3.032423257827759,"_DiffusionProfile1":3.032423257827759,"_DoubleSidedEnable":0.0,"_DoubleSidedNormalMode":2.0,"_DstBlend":0.0,"_EnableFogOnTransparent":1.0,"_EnableHorizontalDivision":0.0,"_EnableTwinEffect":0.0,"_FreezeTwinSplit":0.0,"_GlowNoiseSpeed":1.0,"_HeldInVac":0.0,"_MaskClipValue":0.5,"_MaskClipValue1":0.5,"_MouthSmoothStepBase":0.6000000238418579,"_OpaqueCullMode":2.0,"_ReceivesSSR":0.0,"_ReceivesSSRTransparent":0.0,"_RenderQueueType":1.0,"_RequireSplitLighting":1.0,"_ScaleAmplitude":0.10000000149011612,"_SrcBlend":1.0,"_StencilRef":4.0,"_StencilRefDepth":0.0,"_StencilRefDistortionVec":4.0,"_StencilRefGBuffer":6.0,"_StencilRefMV":32.0,"_StencilWriteMask":6.0,"_StencilWriteMaskDepth":8.0,"_StencilWriteMaskDistortionVec":4.0,"_StencilWriteMaskGBuffer":14.0,"_StencilWriteMaskMV":40.0,"_SupportDecals":1.0,"_SurfaceType":0.0,"_TessEdgeLength":16.0,"_TessMax":25.0,"_TessMaxDisp":25.0,"_TessMin":10.0,"_TessPhongStrength":0.5,"_TessValue":16.0,"_TransparentBackfaceEnable":0.0,"_TransparentCullMode":2.0,"_TransparentSortPriority":0.0,"_TransparentZWrite":1.0,"_TwinEffectOffset":0.0,"_UnscaledTime":0.0,"_UseShadowThreshold":0.0,"_WiggleAmplitude":0.009999999776482582,"_XScaleSpeed":1.0,"_XWiggleSpeed":0.0,"_YScaleSpeed":-1.0,"_YWiggleSpeed":2.0,"_ZTestDepthEqualForOpaque":3.0,"_ZTestGBuffer":3.0,"_ZTestTransparent":4.0,"_ZWrite":1.0,"__dirty":0.0},"render_queue":2475,"keywords":["_ALPHATEST_ON","_ADD_PRECOMPUTED_VELOCITY","_DISABLE_SSR","_DISABLE_SSR_TRANSPARENT"],"textures":[{"property":"_FaceAtlas","file":"tex-source-1500-face_bat_happy02.png","source_name":"face_bat_happy02","scale":[1.0,1.0],"offset":[0.0,0.0],"source_texture_settings":{"m_FilterMode":1,"m_Aniso":1,"m_MipBias":0.0,"m_WrapU":0,"m_WrapV":0,"m_WrapW":0},"source_color_space":1,"mip_count":1}]}]""";
if (!SourceFaceMaterials.TryBuild((List<object>)MiniJson.Parse(battyJson)!, Load, out var battyMaterials,
    out var battyOwned, out var battyProblem)) throw new Exception("real Batty face rows refused: " + battyProblem);
if (battyMaterials.Length != 3 || battyMaterials[1].shader.name != "SR/AMP/Slime/Face/Eyes Glow" ||
    battyMaterials[1].textures["_FaceAtlas"].name != "tex-source-1678-face_bat_happy.png" ||
    battyMaterials[1].textures["_GlowNoise"].name != "tex-source-1670-perlinnoise.png" ||
    battyMaterials[2].textures["_FaceAtlas"].name != "tex-source-1500-face_bat_happy02.png" ||
    battyOwned.Count != 6) throw new Exception("real Batty glow face family lost");
foreach (var resource in battyOwned) UnityEngine.Object.Destroy(resource);
checks++;
Refuse(glowJson.Replace("\"_GlowNoise\"", "\"_GlowNoiseX\""));
Refuse(glowJson.Replace("\"property\":\"_GlowNoise\"", "\"property\":\"_FaceAtlas\""));
Refuse(json.Replace("\"textures\":[{\"property\":\"_FaceAtlas\",\"file\":\"mouth.png\",\"scale\":[1,1],\"offset\":[0,0]}]",
    "\"textures\":[{\"property\":\"_FaceAtlas\",\"file\":\"mouth.png\",\"scale\":[1,1],\"offset\":[0,0]},{\"property\":\"_GlowNoise\",\"file\":\"mouth.png\",\"scale\":[1,1],\"offset\":[0,0]}]"));
var noiseOnlyTextures = "{\"property\":\"_GlowNoise\",\"file\":\"noise.png\",\"scale\":[1,1],\"offset\":[0,0]}";
Refuse(glowJson.Replace(glowEyeTextures, noiseOnlyTextures));
Refuse(glowJson.Replace(glowEyeTextures, noiseOnlyTextures + "," + noiseOnlyTextures));
Material.MissingProperty = "_GlowNoise";
Refuse(glowJson.Replace(glowEyeTextures, noiseOnlyTextures));
if (!SourceFaceMaterials.TryBuild((List<object>)MiniJson.Parse(glowJson)!, Load, out var droppedMaterials,
    out var droppedOwned, out var droppedProblem)) throw new Exception("atlas with missing glow slot refused: " + droppedProblem);
if (droppedMaterials[1].textures["_FaceAtlas"].name != "eye.png" || droppedMaterials[1].textures.ContainsKey("_GlowNoise") ||
    droppedOwned.Count != 5) throw new Exception("dropped glow map contract lost");
foreach (var resource in droppedOwned) UnityEngine.Object.Destroy(resource);
Material.MissingProperty = null;
checks++;

Shader.Missing = "SR/AMP/Slime/Face/Eyes Glow";
Refuse(glowJson);
Shader.Missing = null;
Refuse(json.Replace("\"file\":\"eye.png\"", "\"file\":\"../eye.png\""));
Refuse(json.Replace("\"file\":\"eye.png\"", "\"file\":\"C:eye.png\""));
Refuse(json.Replace("\"_FaceAtlas\"", "\"_FaceAtlasBaked\""));
Refuse(json.Replace("\"_MouthColor\":[0,0,0,1]", "\"_MouthColor\":[0,0,true,1]"));
Refuse(json.Replace("\"render_queue\":2475", "\"render_queue\":2475.0"));
Refuse(json.Replace("\"scale\":[1,1]", "\"scale\":[1]"));
Refuse(json.Replace("\"offset\":[0,0]", "\"offset\":[0,1e300]"));
Refuse(json.Replace("\"offset\":[0,0]", "\"offset\":[0,1e20]"));
Refuse(json.Replace("\"_MouthColor\":[0,0,0,1]", "\"_MouthColor\":[1e300,0,0,1]"));
Refuse(json.Replace("\"_MouthSmoothStepBase\":0.5", "\"_MouthSmoothStepBase\":true"));
Refuse(json, file => file == "mouth.png" ? null : Load(file));
Refuse(json, file => file == "mouth.png" ? throw new IOException("injected decoder error") : Load(file));
Shader.Missing = "SR/AMP/Slime/Face/Mouth Default";
Refuse(json);
Shader.Missing = null;
Shader.Unsupported = "SR/Null Render";
Refuse(json);
Shader.Unsupported = null;
Material.MissingProperty = "_FaceAtlas";
Refuse(json);
Material.MissingProperty = null;
Material.ThrowOnTexture = true;
Refuse(json);
Material.ThrowOnTexture = false;
if (owned.Any(resource => resource.Destroyed)) throw new Exception("failed candidates touched prior successful source face");
var owner = new SourceFaceResourceOwner();
var blankActorOwner = new SourceFaceResourceOwner();
var mesh = new Mesh();
owner.Retain(owned, mesh);
if (owner.OwnedResourceCount != 6 || blankActorOwner.OwnedResourceCount != 0) throw new Exception("template owner ledger incorrect");
var ledger = typeof(SourceFaceResourceOwner).GetField("owned", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
if (!ledger.GetCustomAttributes(typeof(NonSerializedAttribute), false).Any()) throw new Exception("template ledger would be serialized into actor clones");
var destroy = typeof(SourceFaceResourceOwner).GetMethod("OnDestroy", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
destroy.Invoke(blankActorOwner, null);
if (owned.Any(resource => resource.Destroyed)) throw new Exception("blank actor owner freed borrowed template assets");
var templateAssets = owned.ToArray();
destroy.Invoke(owner, null);
destroy.Invoke(owner, null);
if (templateAssets.Any(resource => !resource.Destroyed) || owner.OwnedResourceCount != 0) throw new Exception("template teardown did not release owned resources");
checks += 4;
Console.WriteLine($"source face checks={checks} failures=0; synthetic only, no Unity rendering exercised");

namespace UnityEngine
{
    public class Object
    {
        public string name = "";
        public bool Destroyed;
        public static List<Object> Created = new();
        public Object() { Created.Add(this); }
        public static void Destroy(Object? value) { if(value != null) value.Destroyed = true; }
    }
    public class Texture2D : Object { }
    public class Mesh : Object { }
    public class MonoBehaviour : Object { }
    public class Shader : Object
    {
        public bool isSupported = true;
        public static string? Missing, Unsupported;
        public static Shader? Find(string name) => name == Missing ? null : new Shader { name = name, isSupported = name != Unsupported };
    }
    public struct Color { public float r,g,b,a; public Color(float r,float g,float b,float a) {this.r=r;this.g=g;this.b=b;this.a=a;} }
    public struct Vector2 { public float x,y; public Vector2(float x,float y) {this.x=x;this.y=y;} }
    public class Material : Object
    {
        public Shader shader;
        public int renderQueue;
        public string[] shaderKeywords = [];
        public Dictionary<string,Texture2D> textures = new();
        public Dictionary<string,float> floats = new();
        public Dictionary<string,Color> colors = new();
        public Material(Shader shader) { this.shader=shader; }
        public static string? MissingProperty;
        public static bool ThrowOnTexture;
        public bool HasProperty(string property) => property != MissingProperty;
        public void SetTexture(string property,Texture2D texture) { if (ThrowOnTexture) throw new Exception("injected native assignment error"); textures[property]=texture; }
        public void SetTextureScale(string property,Vector2 value) { }
        public void SetTextureOffset(string property,Vector2 value) { }
        public void SetColor(string property,Color color) {colors[property]=color;}
        public void SetFloat(string property,float value) {floats[property]=value;}
    }
}
