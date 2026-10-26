using System.Text.Json;
using SlimeBridge.SR1;
using UnityEngine;
var checks=0;
void Check(bool value,string reason){if(!value)throw new Exception(reason);checks++;}
var actor=new GameObject("diagnostic-imported-actor");
var animator=actor.AddComponent<Animator>();
animator.cullingMode=AnimatorCullingMode.CullCompletely;
var nativeChild=new GameObject("native-child");nativeChild.SetParent(actor.transform);
var disabledAnimator=nativeChild.AddComponent<Animator>();disabledAnimator.enabled=false;
disabledAnimator.cullingMode=AnimatorCullingMode.CullUpdateTransforms;
var chomper=actor.AddComponent<Chomper>();
var visual=new GameObject("SlimeBridgeImportedVisual");visual.SetParent(actor.transform);
visual.AddComponent<ImportedVisualMarker>();
var clips=new List<object>();
foreach(var name in new[]{"Exact authored idle","Pounce v4","Bite v4"})
 clips.Add(new Dictionary<string,object>{{"name",name},{"duration",1f},{"loop",name=="Exact authored idle"},{"channels",new List<object>()}});
var descriptor=new Dictionary<string,object>{{"default_clip","Exact authored idle"},{"clips",clips}};
Check(ImportedSourceAnimation.TryAttach(visual,descriptor,out var problem),problem);
visual.SetActive(true);nativeChild.SetActive(true);actor.SetActive(true);
Check(ImportedGameplayAnimation.EnsureAttached(actor,visual,out problem),problem);
var driver=actor.GetComponent<ImportedGameplayAnimation>()!;
var playback=visual.GetComponent<Animation>()!;
playback["Exact authored idle"]!.time=.25f;
using(var initial=JsonDocument.Parse(driver.ToJson()))
{
 var json=initial.RootElement;
 Check(json.TryGetProperty("playback",out var source),"driver diagnostic missing live source clip/time");
 Check(source.GetProperty("clip").GetString()=="Exact authored idle" && source.GetProperty("state_time").GetSingle()==.25f && source.GetProperty("length").GetSingle()==1f,"source playback snapshot wrong");
 Check(json.GetProperty("native_chomping").GetBoolean()==false,"wrong native Chomper idle read");
 var animators=json.GetProperty("native_animator_states").EnumerateArray().ToArray();
 Check(animators.Length==2,"native animator diagnostics must be bounded and exclude imported subtree");
 Check(animators[0].GetProperty("enabled").GetBoolean() && !animators[1].GetProperty("enabled").GetBoolean(),"native Animator enabled was changed/misreported");
 Check(animators.All(a=>a.GetProperty("culling").GetString()=="AlwaysAnimate") && json.GetProperty("culling_forced").GetBoolean(),"actual imported-actor native culling override not reported");
 Check(json.GetProperty("history").EnumerateArray().All(h=>h.TryGetProperty("state",out _)),"history omits state transitions");
}
var calls=playback.PlayCalls;
chomper.StartChomp(new GameObject("food"),Identifiable.Id.NONE,false,false,()=>{},(_,_,_,_)=>{});
using(var biting=JsonDocument.Parse(driver.ToJson()))Check(biting.RootElement.GetProperty("native_chomping").GetBoolean(),"native Chomper read must not be synthesized from visual state");
driver.enabled=false;driver.InvokeLifecycle("OnDisable");
using(var disabled=JsonDocument.Parse(driver.ToJson()))
{
 var json=disabled.RootElement;
 Check(!json.GetProperty("culling_forced").GetBoolean(),"disabled driver must not report culling still forced");
 var animators=json.GetProperty("native_animator_states").EnumerateArray().ToArray();
 Check(animators[0].GetProperty("culling").GetString()=="CullCompletely" && animators[1].GetProperty("culling").GetString()=="CullUpdateTransforms","restored exact native culling missing");
 Check(json.GetProperty("native_chomping").GetBoolean(),"driver disable must not cancel native consumption");
}
Check(playback.PlayCalls==calls,"diagnostic requests must not play clips/reset clock");
ImportedGameplayAnimationPatch.StartChompPostfix(chomper);
Check(playback.PlayCalls==calls,"disabled actor driver must not route native hooks to source clips");
Check(ImportedGameplayAnimationPatch.EnsurePatched(),"ground hook installation failed");
var groundPatch=HarmonyLib.Harmony.Patches.Single(p=>p.original.Name=="OnGroundedRaycastResultReceived").postfix!.method;
var parameters=groundPatch.GetParameters();
Check(parameters[0].Name=="__instance" && parameters[1].Name=="result","dynamic Harmony postfix must declare exact parameter injection names");
var groundCallback=(Action<SlimeSubbehaviourPlexer,RaycastHit>)groundPatch.CreateDelegate(typeof(Action<SlimeSubbehaviourPlexer,RaycastHit>));
var plexer=actor.AddComponent<SlimeSubbehaviourPlexer>();
var getterThrew=false;
try { groundCallback(plexer,new RaycastHit {ThrowOnColliderRead=true}); } catch {getterThrew=true;}
Check(!getterThrew,"dynamic postfix getter failure must never escape native callback");
driver.enabled=true;driver.InvokeLifecycle("OnEnable");
driver.OnConfirmedJump();
var destroyedCollider=new GameObject("destroyed-ground").AddComponent<Collider>();
destroyedCollider.Destroyed=true;
groundCallback(plexer,new RaycastHit {collider=destroyedCollider});
using(var destroyedGround=JsonDocument.Parse(driver.ToJson()))
    Check(destroyedGround.RootElement.GetProperty("saw_airborne").GetBoolean(),"grounded hook must preserve Unity destroyed-object null semantics");
Console.WriteLine($"gameplay diagnostic checks={checks} failures=0; production-linked read-only driver snapshot");
namespace SlimeBridge.SR1 { internal static class BridgeEntry { internal static bool LabRestPoseRequested=true; } }
