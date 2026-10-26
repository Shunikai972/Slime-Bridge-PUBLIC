using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using UnityEngine;
using UObj = UnityEngine.Object;

var loader = Type.GetType("SlimeBridge.SR1.ImportedIconLoader") ?? throw new Exception("missing production source icon loader");
var prepare = loader.GetMethod("TryPrepareFromDirectory", BindingFlags.Static | BindingFlags.NonPublic)!;
var lab = Path.GetFullPath("Lab/RuntimeArtifacts/icon-harness-" + Guid.NewGuid().ToString("N"));
var directory = Path.Combine(lab, "Lab", "AssetCache", "sr2-to-sr1", "slime.ringtail"); Directory.CreateDirectory(directory);
Directory.CreateDirectory(Path.Combine(lab,"Lab","GeneratedContent","species-import"));
var oldAnchor = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
Environment.SetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE",Path.Combine(lab,"Harness.exe"));
var png = new byte[33]; new byte[]{137,80,78,71,13,10,26,10}.CopyTo(png,0);
System.Buffers.Binary.BinaryPrimitives.WriteInt32BigEndian(png.AsSpan(16),1024);
System.Buffers.Binary.BinaryPrimitives.WriteInt32BigEndian(png.AsSpan(20),1024);
var file = "iconSlimeRingtail.source.png"; File.WriteAllBytes(Path.Combine(directory,file),png);
var icon = new Dictionary<string,object> {
    ["schema"]="bridge-source-sprite",["schema_version"]=1,["mandatory"]=true,["file"]=file,
    ["texture_size"]=new[]{1024,1024},["rect"]=new[]{0,0,1024,1024},["pivot"]=new[]{.5,.5},["pixels_per_unit"]=100,
    ["vertices"]=new[]{new[]{-5.12,-5.12},new[]{5.12,-5.12},new[]{0d,5.12}},["triangles"]=new[]{0,1,2},
    ["source_sprite_name"]="iconSlimeRingtail",["source_sprite_path_id"]=3052,["source_appearance_path_id"]=31303,
    ["source_texture_path_id"]=1324,["source_texture_rect"]=new[]{17.07612,43.07612,1006.9239,951.8478},
    ["source_texture_rect_offset"]=new[]{17.07612,43.07612},["source_settings_raw"]=64,["packing_rotation"]=0,
    ["pixel_encoding"]="source-texture-rgba-unresampled"};
var manifest = new Dictionary<string,object> { ["canonical_id"]="slime.ringtail",["icon"]=icon,
    ["files"]=new Dictionary<string,string>{{file,Convert.ToHexString(SHA256.HashData(png)).ToLowerInvariant()}} };
void Publish() => File.WriteAllText(Path.Combine(directory,"cache-manifest.json"),JsonSerializer.Serialize(manifest));
Publish();
var nativeIcon = new Sprite();
var native = new SlimeAppearance { Icon=nativeIcon, SaveSet=SlimeAppearance.AppearanceSaveSet.CLASSIC };
var face = native.Face; var structures=native.Structures;
var nativeArray = new[]{new SlimeAppearance {SaveSet=SlimeAppearance.AppearanceSaveSet.OTHER}, native};
var template = new SlimeDefinition { AppearancesDefault=nativeArray };
int checks=0;
void Check(bool ok,string why) {if(!ok)throw new Exception(why);checks++;}
object? Run(bool expected, string canonical="slime.ringtail") {
    var args=new object?[]{lab,directory,canonical,template,null,null};
    var ok=(bool)prepare.Invoke(null,args)!;
    Check(ok==expected,"unexpected loader decision: "+args[5]);
    if(!expected) Check(args[4]==null && !string.IsNullOrEmpty(args[5] as string),"failure not explicit");
    return args[4];
}
try {
    var owner=Run(true)!; var appearance=(SlimeAppearance)owner.GetType().GetField("Appearance", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(owner)!;
    Check(!ReferenceEquals(appearance,native)&&appearance.SaveSet==SlimeAppearance.AppearanceSaveSet.CLASSIC,"CLASSIC is shared/wrong");
    Check(ReferenceEquals(nativeArray,template.AppearancesDefault)&&ReferenceEquals(native.Icon,nativeIcon),"Tabby asset changed");
    Check(ReferenceEquals(appearance.Face,face)&&ReferenceEquals(appearance.Structures,structures),"native face/structures cloned or mutated");
    Check(!ReferenceEquals(appearance.Icon,nativeIcon)&&appearance.Icon!.rect.width==1024&&appearance.Icon.pivot.x==.5f,"source framing lost");
    Check(appearance.Icon.vertices.Length==3&&appearance.Icon.triangles.Length==3,"source geometry lost");
    var live=UObj.Live; ((IDisposable)owner).Dispose(); Check(UObj.Live==live-3,"owned appearance/sprite/texture leaked");
    live=UObj.Live; icon["triangles"]=new[]{0,1,99}; Publish(); Run(false); Check(UObj.Live==live,"invalid geometry allocated resources");
    icon["triangles"]=new[]{0,1,2}; icon["file"]="../outside.png"; Publish(); Run(false); Check(UObj.Live==live,"traversal allocated resources");
    icon["file"]=file; Publish(); File.WriteAllBytes(Path.Combine(directory,file),new byte[]{1}); Run(false); Check(UObj.Live==live,"hash failure leaked");
    File.WriteAllBytes(Path.Combine(directory,file),png); icon["pivot"]=new[]{true,false}; Publish(); Run(false);
    icon["pivot"]=new[]{.5,.5}; Publish(); ImageConversion.Throw=true; Run(false); Check(UObj.Live==live,"native decode exception leaked texture"); ImageConversion.Throw=false;
    Sprite.Throw=true; Run(false); Check(UObj.Live==live,"native create exception leaked texture"); Sprite.Throw=false;
    UObj.ThrowClone=true; Run(false); Check(UObj.Live==live,"native clone exception leaked icon"); UObj.ThrowClone=false;
    manifest.Remove("icon"); Publish(); Run(false); Check(UObj.Live==live,"mandatory source silently replaced by Tabby");
    manifest["icon"]=icon; Publish();
    var alias=Path.Combine(lab,"icon-cache-junction");
    if(OperatingSystem.IsWindows()) {
        using var mklink=System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("cmd.exe",$"/c mklink /J \"{alias}\" \"{directory}\"") {UseShellExecute=false,RedirectStandardOutput=true,RedirectStandardError=true})!;
        mklink.WaitForExit();Check(mklink.ExitCode==0,"private junction fixture creation failed");
    } else Directory.CreateSymbolicLink(alias,directory);
    try {var junctionArgs=new object?[]{lab,alias,"slime.ringtail",template,null,null};live=UObj.Live;
        Check(!(bool)prepare.Invoke(null,junctionArgs)!&&(junctionArgs[5] as string)!.Contains("reparse"),"reparse path consumed");
        Check(UObj.Live==live,"reparse refusal allocated native objects");
    } finally {Directory.Delete(alias);}
    var context=new GameContext();context.SlimeDefinitions.slimeDefinitionsByIdentifiable[Identifiable.Id.TABBY_SLIME]=template;
    context.SlimeDefinitions.Slimes=new[]{template};context.LookupDirector.identifiablePrefabDict[Identifiable.Id.TABBY_SLIME]=new GameObject();
    var registrarType=Type.GetType("SlimeBridge.SR1.SpeciesRegistrar")!;
    var registrar=Activator.CreateInstance(registrarType,BindingFlags.Instance|BindingFlags.NonPublic,null,new object[]{"","",""},null)!;
    var register=registrarType.GetMethod("RegisterEntry",BindingFlags.Instance|BindingFlags.NonPublic)!;
    var entry=new SlimeBridge.Shared.ImportedSpeciesEntry {canonical_id="slime.ringtail",native_id=900001,display_name="Ringtail",
        template=new(){slime_native_ref="TABBY_SLIME"},diet=new(){major_food_groups=new[]{"MEAT"}}};
    bool Register(bool expected){var args=new object?[]{context,entry,null,null,false};var ok=(bool)register.Invoke(registrar,args)!;
        Check(ok==expected,"registrar decision: "+string.Join(";",(List<string>)args[2]!));return (bool)args[4]!;}
    Check(Register(true),"registrar did not publish");
    var imported=context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)900001)!;
    Check(!ReferenceEquals(imported.AppearancesDefault,nativeArray)&&imported.AppearancesDefault.Length==1,"registrar reuses Tabby array");
    Check(!ReferenceEquals(imported.AppearancesDefault[0],native)&&imported.AppearancesDefault[0].SaveSet==SlimeAppearance.AppearanceSaveSet.CLASSIC,"registrar doesn't own CLASSIC");
    Check(ReferenceEquals(imported.GetAppearanceForSet(SlimeAppearance.AppearanceSaveSet.CLASSIC),imported.AppearancesDefault[0]),"save-set resolution not owned CLASSIC");
    Check(ReferenceEquals(context.LookupDirector.slimeAppearanceDirector.GetChosenSlimeAppearance(imported),imported.AppearancesDefault[0]),"director chose foreign appearance");
    Check(context.LookupDirector.slimeAppearanceDirector.Calls.SequenceEqual(new[]{"unlock","select"}),"native selection order invalid");
    Check(ReferenceEquals(template.AppearancesDefault,nativeArray)&&ReferenceEquals(native.Icon,nativeIcon)&&ReferenceEquals(native.Face,face),"registration mutated Tabby");
    live=UObj.Live;Check(!Register(false)&&UObj.Live==live,"repeated registration allocates/leaks or mutates");
    // Failure is before native publication: source decode resources and definition must clean up.
    entry.native_id=900002;ImageConversion.Throw=true;Check(!Register(false),"invalid source icon published");ImageConversion.Throw=false;
    Check(UObj.Live==live&&context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)900002)==null,"failed registration leaked/published objects");
    UObj.ThrowPrefabClone=true;Check(!Register(false),"throwing prefab clone published");UObj.ThrowPrefabClone=false;
    Check(UObj.Live==live,"unpublished definition/appearance/sprite/texture leaked after prefab exception");
    // A late director failure may leave a native chosen reference. Keep it alive,
    // then refuse repeated attempts for that identity instead of growing bundles.
    entry.native_id=900003;context.LookupDirector.slimeAppearanceDirector.ThrowOnSelection=true;
    Check(Register(false),"late failure wasn't marked published");
    var late=context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)900003)!;
    var keep=(System.Collections.IDictionary)loader.GetField("Registered",BindingFlags.Static|BindingFlags.NonPublic)!.GetValue(null)!;
    var publishedOwner=(IDisposable)keep[late]!;live=UObj.Live;publishedOwner.Dispose();Check(UObj.Live==live,"published icon destroyed while director references it");
    registrarType.GetMethod("RollbackEntry",BindingFlags.Static|BindingFlags.NonPublic)!.Invoke(null,new object[]{context,entry,new List<string>()});
    context.LookupDirector.slimeAppearanceDirector.ThrowOnSelection=false;
    Check(!Register(false)&&UObj.Live==live,"repeated rolled-back registration leaks another owned icon");
    if (args.Length == 1)
    {
        using var source=JsonDocument.Parse(File.ReadAllText(args[0]));
        var sourceIcon=source.RootElement.GetProperty("icon");
        var sourceFile=sourceIcon.GetProperty("file").GetString()!;
        File.Copy(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(args[0]))!,sourceFile),Path.Combine(directory,sourceFile),true);
        var sourceCanonical=source.RootElement.TryGetProperty("canonical_id",out var sourceId)?sourceId.GetString()!:"slime.ringtail";
        File.WriteAllText(Path.Combine(directory,"cache-manifest.json"),"{\"canonical_id\":"+JsonSerializer.Serialize(sourceCanonical)+",\"icon\":"+sourceIcon.GetRawText()+",\"files\":"+source.RootElement.GetProperty("files").GetRawText()+"}");
        var actual=Run(true,sourceCanonical)!;
        var actualAppearance=(SlimeAppearance)actual.GetType().GetField("Appearance",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(actual)!;
        Check(actualAppearance.Icon!.vertices.Length==sourceIcon.GetProperty("vertices").GetArrayLength()&&
            actualAppearance.Icon.triangles.Length==sourceIcon.GetProperty("triangles").GetArrayLength(),"real source fragment geometry lost");
        ((IDisposable)actual).Dispose();
    }
    Console.WriteLine($"icon production-linked checks={checks} failures=0 (synthetic Unity boundary; no game/UI proof)");
} finally {Environment.SetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE",oldAnchor);Directory.Delete(lab,true);}

public class SlimeAppearance : ScriptableObject {
    public enum AppearanceSaveSet {CLASSIC, OTHER}
    public Sprite? Icon; public AppearanceSaveSet SaveSet;
    public object Face=new(); public object[] Structures=new object[]{new()};
}
public class SlimeDefinition : ScriptableObject {
    public SlimeAppearance[] AppearancesDefault=Array.Empty<SlimeAppearance>();
    public List<SlimeAppearance> AppearancesDynamic=new();
    public string Name="";public Identifiable.Id IdentifiableId;
    public SlimeDefinition[] BaseSlimes=Array.Empty<SlimeDefinition>();public bool IsLargo,CanLargofy;
    public float PrefabScale;public object Sounds=new();public Identifiable.Id[] FavoriteToys=Array.Empty<Identifiable.Id>();public SlimeDiet Diet=new();
    public SlimeAppearance? GetAppearanceForSet(SlimeAppearance.AppearanceSaveSet set)=>AppearancesDefault.FirstOrDefault(a=>a.SaveSet==set);
}
namespace UnityEngine {
    public class Object {
        public static int Live; public string name=""; bool destroyed;
        public static bool ThrowClone,ThrowPrefabClone;
        public Object(){Live++;}
        public static T Instantiate<T>(T original) where T:Object {if(ThrowClone||(ThrowPrefabClone&&original is GameObject))throw new Exception("clone failed");Live++;return (T)original.MemberwiseClone();}
        public static void Destroy(Object? value){if(value!=null&&!value.destroyed){value.destroyed=true;Live--;}}
        public static void DontDestroyOnLoad(Object value){}
    }
    public class ScriptableObject:Object {public static T CreateInstance<T>() where T:ScriptableObject,new()=>new();}
    public struct Vector2 { public float x,y;public Vector2(float a,float b){x=a;y=b;} }
    public struct Rect {public float x,y,width,height; public Rect(float a,float b,float c,float d){x=a;y=b;width=c;height=d;} }
    public enum TextureFormat { RGBA32 }
    public enum TextureWrapMode {Clamp}
    public enum FilterMode {Bilinear}
    public enum SpriteMeshType {FullRect, Tight}
    public class Texture2D:Object {public int width,height;public TextureWrapMode wrapMode;public FilterMode filterMode; public Texture2D(int w,int h,TextureFormat f,bool m){width=w;height=h;} }
    public static class ImageConversion {public static bool Throw;public static bool LoadImage(Texture2D t,byte[] data,bool markNonReadable){if(Throw)throw new Exception("decode failed");t.width=1024;t.height=1024;return true;} }
    public class Sprite:Object {
        public static bool Throw; public Rect rect;public Vector2 pivot;public Vector2[] vertices=Array.Empty<Vector2>();public ushort[] triangles=Array.Empty<ushort>();
        public static Sprite Create(Texture2D t,Rect r,Vector2 p,float pixelsPerUnit,uint extrude,SpriteMeshType type){if(Throw)throw new Exception("create failed");return new Sprite{rect=r,pivot=p};}
        public void OverrideGeometry(Vector2[] v,ushort[] t){vertices=v;triangles=t;}
    }
}
