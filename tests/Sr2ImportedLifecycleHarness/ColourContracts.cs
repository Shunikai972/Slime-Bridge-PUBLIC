using System.Reflection;
using SlimeBridge.Shared;
using SlimeBridge.SR2;
using UnityEngine;

// CPU/interop contracts, not a renderer or GPU simulation. Calls the production decoder
// and private production BakedMaterial via reflection; no copied decoder/material algorithm.
internal static class ColourContracts
{
    // Independent 3x2 RGBA PNG fixtures: identical asymmetric pixels, PNG filters 0..4.
    // Generated with Python stdlib zlib + CRC32, not with Sr2Png. Includes alpha 0/17/128/179/221/255.
    private static readonly string[] PngFixtures = {
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAI0lEQVR4nGN4Lmj6X3a7ewOv6kUGBufIQsHpa4/efX/54GYAiBIMBeMpJRAAAAAASUVORK5CYII=",
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAI0lEQVR4nGN8Lmj632yZUOOHvK4GRufIQsGQkJAzEWp/rgEAkEMLPIT0OFAAAAAASUVORK5CYII=",
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAI0lEQVR4nGN6Lmj6X3a7ewOv6kUGphgPG6Gqb3Wxj9Z92AwAg3ALP1qFLtkAAAAASUVORK5CYII=",
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAI0lEQVR4nGN+Lmj6f9V6Xcb/p9YdYL4QGD4pXTVz6tysb64AqesMxxJH6oQAAAAASUVORK5CYII=",
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAI0lEQVR4nGN5Lmj632yZUOOHvK4GlhgPG6GqbyFnItZ9MAYAlZsLt6GJ+K0AAAAASUVORK5CYII="
    };
    // Unity upload order is bottom row first; expected bytes are deliberately not decoded from a fixture.
    private static readonly byte[] Expected = {67,89,113,17,151,173,197,221,239,211,193,179,231,17,53,255,29,183,71,128,13,37,209,0};
    private static byte[] Bytes(Color32[] pixels) => pixels.SelectMany(p=>new[]{p.r,p.g,p.b,p.a}).ToArray();
    private static readonly Type Spawner = typeof(Sr2ImportedSpawner);
    private static FieldInfo Field(string name) => Spawner.GetField(name,BindingFlags.Static|BindingFlags.NonPublic)!;

    internal static void Run(Action<bool,string> check)
    {
        var fallbackCalls=ImageConversion.LoadImageCalls;
        for(var filter=0;filter<PngFixtures.Length;filter++)
        {
            var png=Convert.FromBase64String(PngFixtures[filter]);
            check(Sr2Png.TryDecode(png,out var w,out var h,out var pixels) && w==3 && h==2,
                $"production PNG filter {filter} decodes asymmetric 3x2 RGBA");
            check(Bytes(pixels).SequenceEqual(Expected),$"production PNG filter {filter} preserves channels/alpha and flips rows exactly once");
            foreach(var linear in new[]{false,true})
            foreach(var mipmaps in new[]{false,true})
            {
                var image=Sr2Png.Load(png,"contract.png",linear,mipmaps);
                check(image.Width==3 && image.Height==2 && image.Format==TextureFormat.RGBA32 && image.Linear==linear && image.Mipmaps==mipmaps
                    && image.Applied && image.ApplyMipmaps==mipmaps && !image.MadeUnreadable && Bytes(image.Pixels).SequenceEqual(Expected),
                    $"production PNG filter {filter} uploads exact Color32 bytes linear={linear} mipmaps={mipmaps}");
                UnityEngine.Object.Destroy(image);
            }
        }
        check(ImageConversion.LoadImageCalls==fallbackCalls,"supported PNGs never use permissive LoadImage double");

        var savedFloor=Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR2_LARGO_FLOOR");
        var oldTemplate=Field("_litTemplate").GetValue(null);
        var oldSearched=Field("_litSearched").GetValue(null);
        var oldFloor=Field("_lightFloor").GetValue(null);
        try
        {
            foreach(var shaderName in new[]{"HDRP/Lit","Standard"})
                MaterialContract(shaderName,check);
        }
        finally
        {
            Environment.SetEnvironmentVariable("SLIMEBRIDGE_SR2_LARGO_FLOOR",savedFloor);
            Field("_litTemplate").SetValue(null,oldTemplate);Field("_litSearched").SetValue(null,oldSearched);Field("_lightFloor").SetValue(null,oldFloor);
        }
    }

    private static void MaterialContract(string shaderName,Action<bool,string> check)
    {
        var hdrp=shaderName=="HDRP/Lit";
        var shader=new Shader{name=shaderName};
        shader.Properties.UnionWith(hdrp
            ? new[]{"_BaseColorMap","_BaseColor","_SurfaceType","_Metallic","_Smoothness","_NormalMap","_MaskMap","_EmissiveColorMap","_EmissiveColor","_EmissiveColorLDR","_AlbedoAffectEmissive","_UseEmissiveIntensity","_EmissiveExposureWeight","_AlphaCutoffEnable","_AlphaCutoff"}
            : new[]{"_MainTex","_Color","_Metallic","_Glossiness","_BumpMap","_EmissionMap","_Cutoff"});
        if(hdrp)shader.PropertyFlags["_EmissiveColor"]=16; // Actual game shader metadata, not permission to invent a conversion.
        var template=new Material(shader){name="original-lit-template",renderQueue=2000};
        var originalMap=new Texture2D(1,1,TextureFormat.RGBA32,false,false);
        var mapKey=hdrp?"_BaseColorMap":"_MainTex";
        var tintKey=hdrp?"_BaseColor":"_Color";
        var clearMap=hdrp?"_NormalMap":"_BumpMap";
        template.SetTexture(mapKey,originalMap);template.SetTexture(clearMap,originalMap);
        template.SetTextureScale(mapKey,new Vector2(2,3));template.SetTextureOffset(mapKey,new Vector2(.2f,.3f));
        template.SetColor(tintKey,new Color(.2f,.3f,.4f,.5f));template.SetFloat("_Metallic",.9f);
        template.EnableKeyword("_NORMALMAP");template.EnableKeyword("_EMISSIVE_COLOR_MAP");template.EnableKeyword("_ALPHATEST_ON");
        var ownerObject=new GameObject("baked-colour-contract-owner");
        var owner=ownerObject.AddComponent<Sr2ImportedVisualMarker>();
        try
        {
            Field("_litTemplate").SetValue(null,null);Field("_litSearched").SetValue(null,false);Field("_lightFloor").SetValue(null,-1f);
            Environment.SetEnvironmentVariable("SLIMEBRIDGE_SR2_LARGO_FLOOR","0.25");
            var cache=new NativeMeshCache();cache.Textures.Add("colour.png",Convert.FromBase64String(PngFixtures[4]));
            var bindingType=Spawner.GetNestedType("Binding",BindingFlags.NonPublic)!;
            var binding=Activator.CreateInstance(bindingType,true)!;
            bindingType.GetField("Cache",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(binding,cache);
            var source=new NativeMeshMaterial{Name="asymmetric-baked",Shader="SlimeBridge/Baked",
                Colors=new(){{"_Color",new[]{.1f,.2f,.3f,.4f}}},Floats=new(){{"_Cutoff",.37f}},
                Textures=new[]{new NativeMeshTexture{Property="_MainTex",File="colour.png",Linear=true},new NativeMeshTexture{Property="_EmissionMap",File="colour.png"}}};
            var textures=new Dictionary<string,Texture2D>();
            var method=Spawner.GetMethod("BakedMaterial",BindingFlags.Static|BindingFlags.NonPublic)!;
            Material Bake()=>(Material)method.Invoke(null,new object[]{source,binding,owner,textures})!;
            var clone=Bake();var image=clone.Textures[mapKey];
            check(!ReferenceEquals(clone,template) && ReferenceEquals(clone.shader,shader) && owner.Owned.Contains(clone),shaderName+" production BakedMaterial clones and owns native lit template");
            check(ReferenceEquals(Field("_litTemplate").GetValue(null),template),shaderName+" production LitTemplate selects original opaque native material");
            check(!image.Linear && image.Mipmaps && image.ApplyMipmaps && image.filterMode==FilterMode.Trilinear && image.wrapMode==TextureWrapMode.Repeat
                && Bytes(image.Pixels).SequenceEqual(Expected),shaderName+" baked colour upload is sRGB with mipmaps, repeat and exact RGBA bytes");
            check(textures.Count==1 && owner.Owned.Count(o=>o is Texture2D)==1,shaderName+" same colour/emission file uploads once per sRGB texture cache");
            check(clone.GetColor(tintKey).Equals(Color.white) && clone.TextureScales[mapKey].Equals(Vector2.one) && clone.TextureOffsets[mapKey].Equals(Vector2.zero),shaderName+" loaded baked colour has neutral tint and identity UV transform");
            check(clone.Textures[clearMap]==null && !clone.IsKeywordEnabled("_NORMALMAP") && !clone.IsKeywordEnabled("_EMISSIVE_COLOR_MAP") && clone.GetFloat("_Metallic")==0,
                shaderName+" clone clears inherited map/keywords without metallic tint");
            check(clone.IsKeywordEnabled("_ALPHATEST_ON") && clone.renderQueue==2475 && (hdrp
                ? clone.GetFloat("_AlphaCutoffEnable")==1 && clone.GetFloat("_AlphaCutoff")==.37f : clone.GetFloat("_Cutoff")==.37f),shaderName+" source alpha cutoff configures production clip keyword, threshold and queue");
            check(ReferenceEquals(template.Textures[mapKey],originalMap) && ReferenceEquals(template.Textures[clearMap],originalMap) && template.renderQueue==2000
                && template.GetFloat("_Metallic")==.9f && template.GetColor(tintKey).Equals(new Color(.2f,.3f,.4f,.5f))
                && template.TextureScales[mapKey].Equals(new Vector2(2,3)) && template.TextureOffsets[mapKey].Equals(new Vector2(.2f,.3f))
                && template.IsKeywordEnabled("_NORMALMAP") && template.IsKeywordEnabled("_EMISSIVE_COLOR_MAP"),shaderName+" original shared template textures/colour/floats/UV/keywords/queue remain unchanged");
            source.Floats.Remove("_Cutoff");
            var opaque=Bake();
            check(!opaque.IsKeywordEnabled("_ALPHATEST_ON") && opaque.renderQueue==2000 && ReferenceEquals(opaque.Textures[mapKey],image),
                shaderName+" absent source cutoff leaves clone opaque and reuses uploaded sRGB texture");
            source.Floats.Add("_Cutoff",.37f);
            if(hdrp)
            {
                check(shader.PropertyFlags["_EmissiveColor"]==16 && clone.GetVector("_EmissiveColor").x==.25f
                    && clone.GetFloat("_AlbedoAffectEmissive")==1 && clone.GetFloat("_UseEmissiveIntensity")==0 && clone.GetFloat("_EmissiveExposureWeight")==0,
                    "HDRP floor stores raw .25 per actual SR2 Unity 6000 runtime (conversion hypothesis rejected)");
                var contract=new Material(shader);contract.SetColor("_EmissiveColor",new Color(.25f,.25f,.25f,1));var afterColor=contract.GetVector("_EmissiveColor");
                contract.SetVector("_EmissiveColor",new Vector4(.25f,.25f,.25f,1));
                check(afterColor.Equals(contract.GetVector("_EmissiveColor")),"observed HDR SetColor and SetVector raw CPU storage agree; no synthetic sRGB conversion");UnityEngine.Object.Destroy(contract);
                foreach(var (raw,expected) in new (string?,float)[]{(null,.25f),("invalid",.25f),("NaN",.25f),("Infinity",.25f),("-Infinity",.25f),("1e999",.25f),("0",0),("1",1),("-3",0),("4",1),("0.625",.625f)})
                {
                    Field("_lightFloor").SetValue(null,-1f);Environment.SetEnvironmentVariable("SLIMEBRIDGE_SR2_LARGO_FLOOR",raw);
                    var observed=Bake().GetVector("_EmissiveColor");
                    check(float.IsFinite(observed.x) && observed.x==expected && observed.y==expected && observed.z==expected && observed.w==1,
                        $"production floor finite configuration {raw??"<unset>"} stores expected raw {expected}");
                }
            }
            owner.ReleaseOwned();
            check(clone.Destroyed && image.Destroyed && !template.Destroyed && !originalMap.Destroyed,shaderName+" owner releases cloned material/upload but preserves native assets");
        }
        finally
        {
            owner.ReleaseOwned();UnityEngine.Object.Destroy(ownerObject);UnityEngine.Object.Destroy(template);UnityEngine.Object.Destroy(originalMap);UnityEngine.Object.Destroy(shader);
        }
    }
}
