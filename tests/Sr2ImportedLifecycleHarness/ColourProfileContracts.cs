using System;
using System.Collections.Generic;
using System.Reflection;
using SlimeBridge.Shared;
using SlimeBridge.SR2;
using UnityEngine;

internal static class ColourProfileContracts
{
    internal static void Run(Action<bool,string> check)
    {
        const string id = "largo.cotton.quantum";
        const string model = "852bbc74ea2b19ab28547ecb3f0e8bb979a99c3c6fa9e8b45b957e9631271068";
        const string manifest = "09e689d8f779a6724ca0e741878330567441a80cd8c9fa3e9728523e73b2ba5d";
        var type = typeof(Sr2ImportedSpawner).Assembly.GetType("SlimeBridge.SR2.Sr2BakedColourProfiles");
        check(type != null, "SR2 baked materials have an identity-bound colour resolver");
        var method = type!.GetMethod("Resolve", BindingFlags.Static | BindingFlags.NonPublic)!;
        Vector4 Resolve(string canonical=id, string modelHash=model, string manifestHash=manifest, string shader="HDRP/Lit") =>
            (Vector4)method.Invoke(null,new object[]{canonical,modelHash,manifestHash,shader})!;
        var expected = new Vector4(.6213676f,.4788209f,.3666666f,1f);
        var actual = Resolve();
        bool Near(Vector4 a,Vector4 b) => Math.Abs(a.x-b.x)<.00001f && Math.Abs(a.y-b.y)<.00001f && Math.Abs(a.z-b.z)<.00001f && a.w==b.w;
        var neutral = new Vector4(1,1,1,1);
        check(Near(actual,expected), "exact measured HDRP model+manifest receives linear gain, alpha stays one");
        check(Near(Resolve(canonical:"largo.unknown"),neutral), "unmeasured model stays neutral");
        check(Near(Resolve(modelHash:new string('0',64)),neutral), "model JSON revision invalidates colour profile");
        check(Near(Resolve(manifestHash:new string('0',64)),neutral), "texture-only revision invalidates colour profile");
        check(Near(Resolve(manifestHash:""),neutral), "missing snapshot digest stays neutral");
        check(Near(Resolve(shader:"Standard"),neutral) && Near(Resolve(shader:"HDRP/Unlit"),neutral), "fallback and other shaders are never calibrated with HDRP gains");
        check(Near(Resolve(canonical:"LARGO.COTTON.QUANTUM"),neutral), "canonical identifiers are not rewritten to force a match");

        var bindingType=typeof(Sr2ImportedSpawner).GetNestedType("Binding",BindingFlags.NonPublic)!;
        var cache=new NativeMeshCache {CanonicalId=id,ModelSha256=model};
        typeof(NativeMeshCache).GetProperty("ManifestSha256")!.SetValue(cache,manifest);
        var binding=Activator.CreateInstance(bindingType,true)!;
        bindingType.GetField("Cache",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(binding,cache);
        var source=new NativeMeshMaterial {Name="profile-fixture",Shader="Bridge/BakedLargo"};
        source.Textures=new[]{new NativeMeshTexture {Property="_MainTex",File="fixture.png"}};
        var shader=new Shader {name="HDRP/Lit"};
        shader.Properties.UnionWith(new[]{"_BaseColor","_BaseColorMap","_EmissiveColor","_AlbedoAffectEmissive"});
        var template=new Material(shader);
        typeof(Sr2ImportedSpawner).GetField("_litTemplate",BindingFlags.Static|BindingFlags.NonPublic)!.SetValue(null,template);
        var owner=new GameObject("profile-owner").AddComponent<Sr2ImportedVisualMarker>();
        var image=new Texture2D(1,1,TextureFormat.RGBA32,false,false);
        var uploads=new Dictionary<string,Texture2D>{{"fixture.png|srgb",image}};
        var bake=typeof(Sr2ImportedSpawner).GetMethod("BakedMaterial",BindingFlags.Static|BindingFlags.NonPublic)!;
        try
        {
            var material=(Material)bake.Invoke(null,new object[]{source,binding,owner,uploads})!;
            check(Near(material.GetVector("_BaseColor"),expected), "production BakedMaterial applies measured gain to the cloned lit material");
            check(material.GetColor("_BaseColor").a==1f && ReferenceEquals(material.Textures["_BaseColorMap"],image),
                "calibration leaves uploaded texture and alpha unchanged");
            check(template.GetColor("_BaseColor").Equals(default(Color)), "calibration never mutates the shared native template");
        }
        finally
        {
            owner.ReleaseOwned(); UnityEngine.Object.Destroy(owner.gameObject); UnityEngine.Object.Destroy(image); UnityEngine.Object.Destroy(template); UnityEngine.Object.Destroy(shader);
            typeof(Sr2ImportedSpawner).GetField("_litTemplate",BindingFlags.Static|BindingFlags.NonPublic)!.SetValue(null,null);
        }
    }
}
