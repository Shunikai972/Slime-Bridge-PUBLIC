using System.Text.Json;
using SlimeBridge.SR1;
using UnityEngine;

var checks = 0;
void Check(bool value, string reason) { if (!value) throw new Exception(reason); checks++; }
var game = new GameContext();
var scene = new SceneContext();
scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.IMPORTED, 2));
scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.OTHER, 99));
scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.IMPORTED, 3));
scene.Player.transform.position = new Vector3 { x = 1, y = 2, z = 3 };
Camera.main = new Camera();
Camera.main.transform.position = new Vector3 { x = 4, y = 5, z = 6 };
var actor = new GameObject { name = "imported-actor", activeSelf = true };
var visual = new GameObject { name = "SlimeBridgeImportedVisual", activeSelf = true };
actor.transform.Add(visual.transform);
actor.Add(new Identifiable { id = Identifiable.Id.IMPORTED });
actor.Add(new ImportedGameplayAnimation());
ImportedSpawner.TrackedImportRoots.Add(actor);
var problems = new List<string>();
using var parsed = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", problems));
var root = parsed.RootElement;
Check(root.TryGetProperty("native_gameplay_snapshot", out var snapshot), "missing read-only native inventory/save/pose snapshot");
Check(snapshot.GetProperty("saved_game_identity").GetString() == "internal-save-123", "wrong saved identity");
Check(snapshot.GetProperty("vacpack").GetProperty("total").GetInt32() == 5, "must sum every matching live usable slot");
var slots = snapshot.GetProperty("vacpack").GetProperty("slots").EnumerateArray().ToArray();
Check(slots.Length == 2 && slots[0].GetProperty("slot").GetInt32() == 0 && slots[1].GetProperty("slot").GetInt32() == 2,
    "slot provenance missing / nonmatching slot included");
Check(snapshot.GetProperty("player_pose").GetProperty("position")[1].GetSingle() == 2, "player pose wrong");
Check(snapshot.GetProperty("camera_pose").GetProperty("position")[2].GetSingle() == 6, "camera pose wrong");
Check(root.GetProperty("imported_visual_proof").GetProperty("instances")[0].GetProperty("gameplay").GetProperty("stub_read_only").GetBoolean(),
    "driver diagnostics not routed from actor root");
Check(root.GetProperty("utc").GetString() is { Length: > 10 }, "stale detection UTC lost");
Check(problems.Count == 0 && scene.PlayerState.Ammo.Reads == 6, "snapshot unexpectedly mutated/read extra inventory");
scene.PlayerState.Ammo.Slots.Clear();
using var empty = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(empty.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").GetProperty("total").GetInt32() == 0, "empty live inventory is not zero");
scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.IMPORTED, -1));
using var invalid = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(invalid.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind == JsonValueKind.Null,
    "invalid native count must not be reported as proof");
scene.PlayerState.Ammo.Slots.Clear();
scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.IMPORTED, int.MaxValue));
scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.IMPORTED, 1));
using var overflow = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(overflow.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind == JsonValueKind.Null,
    "overflow must fail closed, never publish partial total");
scene.PlayerState.Ammo.Slots.Clear();
for (var i=0;i<65;i++) scene.PlayerState.Ammo.Slots.Add((Identifiable.Id.IMPORTED, 1));
using var excessive = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(excessive.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind == JsonValueKind.Null,
    "slot bound must not publish an incomplete aggregate");
game.AutoSaveDirector.Loading=true;
var before = scene.PlayerState.Ammo.Reads;
using var loading = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(loading.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind == JsonValueKind.Null &&
    scene.PlayerState.Ammo.Reads == before, "loading save must not read/publish unstable inventory");
scene.Player.transform.position = new Vector3 { x=float.NaN };
using var nonfinite = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(nonfinite.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("player_pose").ValueKind == JsonValueKind.Null,
    "nonfinite pose must not emit invalid JSON");
actor.Components.RemoveAll(c=>c is ImportedGameplayAnimation);
using var missing = JsonDocument.Parse(BridgeDiagnostics.Collect(game, scene, new SpeciesRegistrar(), "ringtail", new()));
Check(missing.RootElement.GetProperty("imported_visual_proof").GetProperty("instances")[0].GetProperty("gameplay").ValueKind == JsonValueKind.Null,
    "native actors cannot acquire diagnostic driver implicitly");
using (var gatedOff=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(!gatedOff.RootElement.GetProperty("imported_visual_proof").GetProperty("instances")[0].TryGetProperty("petrification",out _),
        "petrification readback must stay behind the rest-pose gate");
actor.Add(new ImportedPetrification());
BridgeEntry.LabRestPoseRequested = true;
using (var gatedOn=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
{
    var petrification=gatedOn.RootElement.GetProperty("imported_visual_proof").GetProperty("instances")[0].GetProperty("petrification");
    Check(petrification.GetProperty("state").GetString()=="alive"&&petrification.GetProperty("exposure_minutes").GetInt32()==0&&
        !petrification.GetProperty("protected").GetBoolean()&&petrification.GetProperty("daylight").ValueKind==JsonValueKind.Null&&
        petrification.GetProperty("frozen_count").GetInt32()==0,"petrification readback missing or incorrect at rest pose");
}
BridgeEntry.LabRestPoseRequested = false;
actor.Components.RemoveAll(c=>c is ImportedPetrification);
using var unavailable = JsonDocument.Parse(BridgeDiagnostics.Collect(game, null!, new SpeciesRegistrar(), "ringtail", new()));
Check(unavailable.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("player_pose").ValueKind == JsonValueKind.Null,
    "missing scene player pose must be absent");
game.AutoSaveDirector!.Loading = false;
scene.PlayerState.Ammo.Usable = 64;
using (var accepted = JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(accepted.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").GetProperty("total").GetInt32()==64,"accepted 64-slot boundary/usability filter wrong");
scene.PlayerState.Ammo.Usable = -1;
using (var negative = JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(negative.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind==JsonValueKind.Null,"negative usable slots accepted");
scene.PlayerState.Ammo.Usable = 2;
scene.PlayerState.Ammo.ThrowCountSlot = 1;
using (var fault = JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(fault.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind==JsonValueKind.Null,"late getter failure leaked partial total");
scene.PlayerState.Ammo.ThrowCountSlot = null;
scene.PlayerState.Ammo.Slots[1]=(Identifiable.Id.OTHER,-1);
using (var badOther = JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(badOther.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind==JsonValueKind.Null,"invalid nonmatching count accepted");
scene.PlayerState.Ammo.Usable = 1;
var director=game.AutoSaveDirector!;
var save=director.SavedGame!;
director.SavedGame=null;
using (var noSave=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(noSave.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("saved_game_identity").ValueKind==JsonValueKind.Null,"absent save serialization failed");
director.SavedGame=save;
save.Throw=true;
using (var saveError=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(saveError.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("vacpack").ValueKind==JsonValueKind.Null,"save getter failure was treated as stable identity");
save.Throw=false;
game.AutoSaveDirector=null;
using (var noDirector=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(noDirector.RootElement.GetProperty("native_gameplay_snapshot").GetProperty("saved_game_identity").ValueKind==JsonValueKind.Null,"absent director serialization failed");
game.AutoSaveDirector=director;
actor.Add(new ImportedGameplayAnimation {Throw=true});
using (var driverError=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
    Check(driverError.RootElement.GetProperty("imported_visual_proof").GetProperty("instances")[0].GetProperty("gameplay").GetProperty("read_error").GetString()=="IOException","driver diagnostic failure escaped collection");
var captureStart=DateTimeOffset.UtcNow;
using (var fresh=JsonDocument.Parse(BridgeDiagnostics.Collect(game,scene,new SpeciesRegistrar(),"ringtail",new())))
{
    var timestamp=DateTimeOffset.Parse(fresh.RootElement.GetProperty("utc").GetString()!,System.Globalization.CultureInfo.InvariantCulture);
    Check(timestamp.Offset==TimeSpan.Zero && timestamp>=captureStart && timestamp<=DateTimeOffset.UtcNow,"UTC is stale or not UTC");
}
var outDir = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "diagnostic-publication");
Directory.CreateDirectory(outDir);
var canonicalPath = Path.Combine(outDir,"visual-diagnostics-ringtail.json");
Check(BridgeDiagnostics.Write(outDir, "ringtail", game, scene, new SpeciesRegistrar(), new()), "manual publication failed");
var firstPublication = File.ReadAllText(canonicalPath);
using (var firstJson=JsonDocument.Parse(firstPublication)) Check(firstJson.RootElement.TryGetProperty("utc",out _),"published UTC missing");
scene.Player.transform.position = new Vector3 { x=9 };
Check(BridgeDiagnostics.Write(outDir, "ringtail", game, scene, new SpeciesRegistrar(), new()), "manual republication failed");
var secondPublication = File.ReadAllText(canonicalPath);
Check(secondPublication!=firstPublication && Directory.GetFiles(outDir).Length == 1, "manual diag must replace the same canonical target, leaving no staging files");
void Refused(Action action,string reason)
{
    var refused=false;
    try { action(); } catch(IOException) { refused=true; }
    Check(refused,reason);
}
var preserved=File.ReadAllText(canonicalPath);
using (var locked=new FileStream(canonicalPath,FileMode.Open,FileAccess.Read,FileShare.Read))
    Refused(()=>BridgeDiagnostics.PublishDiagnosticFile(outDir,Path.GetFileName(canonicalPath),"{\"v\":-1}"),"locked previous diagnostic must refuse rename");
Check(File.ReadAllText(canonicalPath)==preserved && Directory.GetFiles(outDir).Length==1,"failed rename did not preserve old JSON / cleanup exact staging");
foreach(var bad in new[]{"visual-diagnostics-../escape.json","visual-diagnostics-bad:ads.json","other.json","C:/visual-diagnostics-root.json"})
    Refused(()=>BridgeDiagnostics.PublishDiagnosticFile(outDir,bad,"{}"),"unsafe filename accepted");
var directoryDestination=Path.Combine(outDir,"visual-diagnostics-directory.json");
Directory.CreateDirectory(directoryDestination);
Refused(()=>BridgeDiagnostics.PublishDiagnosticFile(outDir,Path.GetFileName(directoryDestination),"{}"),"directory destination must be preserved");
Check(Directory.Exists(directoryDestination) && Directory.GetFiles(outDir).Length==1,"failed directory replacement leaked staging");
Directory.Delete(directoryDestination);
var junction=Environment.GetEnvironmentVariable("DIAGNOSTICS_JUNCTION_ROOT");
if(junction!=null)
{
    Refused(()=>BridgeDiagnostics.PublishDiagnosticFile(junction,"visual-diagnostics-escape.json","{}"),"pre-existing junction must be refused");
    Check(Directory.GetFiles(Environment.GetEnvironmentVariable("DIAGNOSTICS_JUNCTION_TARGET")!).Length==0,"junction escaped publication");
}
var reads=0;
var invalidReads=0;
var publishedDuringReads=0;
var refusedDuringReads=0;
var observedVersions=new System.Collections.Concurrent.ConcurrentDictionary<int,byte>();
using(var stop=new CancellationTokenSource())
using(var firstRead=new ManualResetEventSlim())
{
    var reader=Task.Run(()=>
    {
        while(!stop.IsCancellationRequested)
        {
            try
            {
                using var stream=new FileStream(canonicalPath,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete);
                using var document=JsonDocument.Parse(stream);
                if(document.RootElement.TryGetProperty("v",out var version)) observedVersions.TryAdd(version.GetInt32(),0);
                Interlocked.Increment(ref reads);
                firstRead.Set();
            }
            catch(IOException) { } // Short exclusive staging/rename windows are allowed, torn JSON is not.
            catch(JsonException) { Interlocked.Increment(ref invalidReads); firstRead.Set(); }
        }
    });
    Check(firstRead.Wait(TimeSpan.FromSeconds(5)),"concurrent reader never observed initial diagnostic");
    for(var version=0;version<50;version++)
    {
        try
        {
            BridgeDiagnostics.PublishDiagnosticFile(outDir,Path.GetFileName(canonicalPath),"{\"v\":"+version+",\"pad\":\""+new string('x',4096)+"\"}");
            publishedDuringReads++;
        }
        catch(IOException) { refusedDuringReads++; } // Safe refusal while an old target is open, never a successful replacement claim.
    }
    stop.Cancel();
    reader.GetAwaiter().GetResult();
}
BridgeDiagnostics.PublishDiagnosticFile(outDir,Path.GetFileName(canonicalPath),"{\"v\":50}");
Check(reads>0 && invalidReads==0,"atomic publication exposed incomplete JSON");
Check(Directory.GetFiles(outDir).Length==1,"publication race leaked staging files");
var renameType=typeof(BridgeDiagnostics).GetNestedType("NativeRenameInfo",System.Reflection.BindingFlags.NonPublic)!;
var iosbType=typeof(BridgeDiagnostics).GetNestedType("NativeIoStatus",System.Reflection.BindingFlags.NonPublic)!;
var renameOffset=System.Runtime.InteropServices.Marshal.OffsetOf(renameType,"FirstCharacter").ToInt32();
var iosbSize=System.Runtime.InteropServices.Marshal.SizeOf(iosbType);
Check(renameOffset==(IntPtr.Size==8?20:12) && iosbSize==IntPtr.Size*2,"native publication ABI mismatch");
// Production marker + read-only production renderer description. Scalar-field transfer
// follows the documented serialization rules; this is not an executed Unity Instantiate.
var sourcePart = new ImportedPartColors { BodyLighting = ImportedBodyLighting.Restore(0f, 1f, 0f) };
ImportedPartColors TransferredPart(ImportedPartColors part)
{
    var copy = new ImportedPartColors();
    foreach (var field in typeof(ImportedPartColors).GetFields(System.Reflection.BindingFlags.Instance |
                 System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic))
    {
        if (field.IsStatic || field.IsInitOnly || Attribute.IsDefined(field, typeof(NonSerializedAttribute))) continue;
        if (!field.IsPublic && !Attribute.IsDefined(field, typeof(SerializeField))) continue;
        if (field.FieldType == typeof(bool) || field.FieldType == typeof(float)) field.SetValue(copy, field.GetValue(part));
    }
    return copy;
}
var finalPart = TransferredPart(sourcePart);
var finalBody = new GameObject { name = "body", activeSelf = true };
finalBody.Add(finalPart);
var finalMaterial = new Material { shader = new Shader { name = ImportedBodyLighting.DestinationShader } };
finalMaterial.Floats["_LightingUVContribution"] = 0f;
finalMaterial.Floats["_BodyLightingContribution"] = 1f;
finalMaterial.Floats["_LightingUVHorizontalAdjust"] = 0f;
var finalRenderer = new MeshRenderer { enabled = true, sharedMaterials = new[] { finalMaterial } };
finalBody.Add(finalRenderer);
var describeRenderer = typeof(BridgeDiagnostics).GetMethod("DescribeRenderer", System.Reflection.BindingFlags.NonPublic |
                         System.Reflection.BindingFlags.Static)!;
JsonDocument ReadFinalMaterial() => JsonDocument.Parse((string)describeRenderer.Invoke(null, new object[] { finalRenderer })!);
BridgeEntry.LabRestPoseRequested = true;
using (var readback = ReadFinalMaterial())
    Check(readback.RootElement.GetProperty("materials")[0].GetProperty("source_body_lighting_matches").GetBoolean(),
        "source readback absent/false after scalar-field transfer");
finalMaterial.Floats["_LightingUVContribution"] = 1f;
using (var overwritten = ReadFinalMaterial())
    Check(!overwritten.RootElement.GetProperty("materials")[0].GetProperty("source_body_lighting_matches").GetBoolean(),
        "readback accepted later template override");
finalMaterial.Floats["_LightingUVContribution"] = 0f;
finalBody.Components.Remove(finalPart);
var malformedPart = TransferredPart(sourcePart);
typeof(ImportedPartColors).GetField("lightingUVContribution", System.Reflection.BindingFlags.Instance |
    System.Reflection.BindingFlags.NonPublic)!.SetValue(malformedPart, float.NaN);
finalBody.Add(malformedPart);
Check(malformedPart.HasBodyLighting && malformedPart.BodyLighting == null, "malformed serialized payload was not refused");
using (var malformedReadback = ReadFinalMaterial())
    Check(!malformedReadback.RootElement.GetProperty("materials")[0].GetProperty("source_body_lighting_matches").GetBoolean(),
        "declared but malformed lighting silently disappeared from diagnostics");
finalBody.Components.Remove(malformedPart);
finalBody.Add(new ImportedPartColors());
using (var legacy = ReadFinalMaterial())
    Check(!legacy.RootElement.GetProperty("materials")[0].TryGetProperty("source_body_lighting_matches", out _),
        "legacy absent controls gained readback evidence");
finalBody.Components.RemoveAll(component => component is ImportedPartColors);
finalBody.Add(finalPart);
BridgeEntry.LabRestPoseRequested = false;
using (var normal = ReadFinalMaterial())
    Check(!normal.RootElement.GetProperty("materials")[0].TryGetProperty("source_body_lighting_matches", out _),
        "bounded LAB diagnostic leaked outside rest-pose mode");
Console.WriteLine($"native publication: pointer_bytes={IntPtr.Size} rename_name_offset={renameOffset} iosb_bytes={iosbSize} complete_reads={reads} torn_reads={invalidReads} concurrent_published={publishedDuringReads} concurrent_refused={refusedDuringReads} observed_versions=[{string.Join(',',observedVersions.Keys.Order())}]; Windows owned-handle rename, actual kernel path");
Console.WriteLine($"diagnostic checks={checks} failures=0; linked BridgeDiagnostics, synthetic native read-only boundary");

public class Identifiable : Component { public enum Id { IMPORTED=1000, OTHER=1 } public Id id; }
public class GameContext { public LookupDirector LookupDirector = new(); public AutoSaveDirector? AutoSaveDirector = new(); }
public class LookupDirector { public GameObject GetPrefab(Identifiable.Id id) => new(); }
public class AutoSaveDirector { public SavedGame? SavedGame = new(); public bool Loading; public bool IsLoadingGame()=>Loading; }
public class SavedGame { public string Name="internal-save-123"; public bool Throw; public string GetName()=>Throw ? throw new IOException("save-getter") : Name; }
public class SceneContext { public PlayerState PlayerState = new(); public GameObject Player = new(); }
public class PlayerState { public enum AmmoMode { DEFAULT } public Ammo Ammo = new(); public Ammo GetAmmo(AmmoMode mode)=>Ammo; }
public class Ammo {
 public readonly List<(Identifiable.Id Id,int Count)> Slots = new(); public int Reads; public int? Usable; public int? ThrowCountSlot;
 public int GetUsableSlotCount()=>Usable ?? Slots.Count;
 public Identifiable.Id GetSlotName(int slot) { Reads++; return Slots[slot].Id; }
 public int GetSlotCount(int slot) { Reads++; if (ThrowCountSlot==slot) throw new IOException("ammo-getter"); return Slots[slot].Count; }
}
namespace SlimeBridge.SR1 {
 internal sealed class SpeciesRegistrar { internal bool TryResolveNativeId(string name,out int id) { id=1000; return true; } }
 internal static class ImportedSpawner { internal static readonly List<GameObject> TrackedImportRoots = new(); }
 internal static class BridgeEntry { internal static bool LabRestPoseRequested; }
 internal sealed class ImportedGameplayAnimation : MonoBehaviour { internal bool Throw; internal string ToJson()=>Throw ? throw new IOException("driver-read") : "{\"stub_read_only\":true}"; }
 internal sealed class ImportedPetrification : MonoBehaviour { internal string ToJson()=>"{\"state\":\"alive\",\"exposure_minutes\":0,\"protected\":false,\"daylight\":null,\"frozen_count\":0}"; }
 internal sealed class ImportedSourceAnimation : MonoBehaviour { internal bool TryReadPlayback(out string name,out float time,out float length) {name="idle";time=.5f;length=2;return true;} }
 internal sealed class SlimeBridgeJiggle : MonoBehaviour { }
 internal sealed class LabSourceAnimationTrace : MonoBehaviour { internal string ToJson()=>"{}"; }

 internal sealed class SourceFaceResourceOwner : MonoBehaviour { internal int OwnedResourceCount; }
}
namespace UnityEngine {
 [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute { }
 public class Object { public string name=""; public static void Destroy(Object? obj) {} }
 public class Component : Object {
  public GameObject gameObject=null!; public Transform transform=>gameObject.transform;
  public T? GetComponent<T>() where T:Component=>gameObject.GetComponent<T>();
  public T[] GetComponents<T>() where T:Component=>gameObject.GetComponents<T>();
 }
 public class Behaviour : Component { public bool enabled=true; }
 public class MonoBehaviour : Behaviour { }
 public class GameObject : Object {
  public Transform transform; public bool activeSelf; public bool activeInHierarchy=>activeSelf; public int layer;
  public List<Component> Components=new();
  public GameObject(){transform=new Transform{gameObject=this};}
  public void Add(Component c){c.gameObject=this;Components.Add(c);}
  public T? GetComponent<T>() where T:Component=>Components.OfType<T>().FirstOrDefault();
  public T[] GetComponents<T>() where T:Component=>Components.OfType<T>().ToArray();
  public bool TryGetComponent(Type type,out Component? c){c=Components.FirstOrDefault(type.IsInstanceOfType);return c!=null;}
 }
 public class Transform : Component {
  private readonly List<Transform> children=new(); public Transform? parent; public int childCount=>children.Count;
  public Vector3 position,localPosition; public Quaternion rotation,localRotation;
  public Transform GetChild(int i)=>children[i]; public void Add(Transform c){children.Add(c);c.parent=this;}
 }
 public struct Vector3 { public float x,y,z; }
 public struct Quaternion { public float x,y,z,w; }
 public struct Color { public float r,g,b,a; public static Color clear=>default; }
 public struct Color32 { public byte r,g,b,a; }
 public class Camera : Behaviour { public static Camera? main; public Camera(){gameObject=new();} }
 public enum AnimatorCullingMode { AlwaysAnimate, CullUpdateTransforms, CullCompletely }
 public class Animator : Behaviour { public Object? runtimeAnimatorController; public AnimatorCullingMode cullingMode; }
 public class Renderer : Component { public bool enabled; public Material[] sharedMaterials=Array.Empty<Material>();
  public void GetPropertyBlock(MaterialPropertyBlock block) {} public bool HasPropertyBlock()=>false; }
 public class MaterialPropertyBlock { public bool isEmpty=true; public Color GetColor(int id)=>default; }
 public class MeshRenderer : Renderer { }
 public class SkinnedMeshRenderer : Renderer {
  public Transform? rootBone; public Transform[] bones=Array.Empty<Transform>(); public Mesh sharedMesh=new(); public void BakeMesh(Mesh mesh) {}
 }
 public class MeshFilter : Component { public Mesh sharedMesh=new(); }
 public class Mesh : Object { public int vertexCount,subMeshCount; public Vector3[] vertices=Array.Empty<Vector3>(); }
 public class Material : Object {
  public Shader? shader; public int renderQueue; public readonly Dictionary<string,float> Floats = new();
  public bool HasProperty(string s)=>Floats.ContainsKey(s); public bool HasProperty(int s)=>false;
  public Color GetColor(string s)=>default; public Color GetColor(int s)=>default; public Texture? GetTexture(string s)=>null; public float GetFloat(string s)=>Floats[s];
  public void SetFloat(string s, float value)=>throw new InvalidOperationException("diagnostics must be read-only");
 }
 public class Shader : Object { public static int PropertyToID(string s)=>0; }
 public class Texture : Object { }
 public class Texture2D : Texture {
  public int width,height,mipmapCount,filterMode,wrapModeU,wrapModeV; public Color32[] GetPixels32()=>Array.Empty<Color32>();
 }
}
namespace UnityEngine.SceneManagement {
 public struct Scene { public bool isLoaded=>true; public int rootCount=>0; public bool IsValid()=>true; public GameObject[] GetRootGameObjects()=>Array.Empty<GameObject>(); }
 public static class SceneManager { public static int sceneCount=>0; public static Scene GetSceneAt(int i)=>new(); }
}
