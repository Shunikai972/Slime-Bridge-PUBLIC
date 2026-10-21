using SlimeBridge.SR1;
using UnityEngine;
using HarmonyLib;
var checks=0;
void Check(bool value,string reason){if(!value)throw new Exception(reason);checks++;}
Console.WriteLine("native Harmony library="+typeof(Harmony).Assembly.FullName);
Check(ImportedGameplayAnimationPatch.EnsurePatched(),"actual Harmony installation failed: "+string.Join(" | ",Debug.Messages));
var foreign=new Harmony("slimebridge.test.foreign-sentinel");
var startTarget=typeof(Chomper).GetMethod("StartChomp")!;
foreign.Patch(startTarget,postfix:new HarmonyMethod(typeof(ForeignProbe),nameof(ForeignProbe.Observe)));
var actor=new GameObject("actual-harmony-imported-actor");
var chomper=actor.AddComponent<Chomper>();
var visual=new GameObject("SlimeBridgeImportedVisual");visual.SetParent(actor.transform);visual.AddComponent<ImportedVisualMarker>();
var clips=new List<object>();
foreach(var name in new[]{"Exact authored idle","Pounce v4","Bite v4"})clips.Add(new Dictionary<string,object>{{"name",name},{"duration",1f},{"loop",name=="Exact authored idle"},{"channels",new List<object>()}});
Check(ImportedSourceAnimation.TryAttach(visual,new Dictionary<string,object>{{"default_clip","Exact authored idle"},{"clips",clips}},out var problem),problem);
visual.SetActive(true);actor.SetActive(true);
Check(ImportedGameplayAnimation.EnsureAttached(actor,visual,out problem),problem);
var source=visual.GetComponent<ImportedSourceAnimation>()!;
void Clip(string expected){Check(source.TryReadPlayback(out var clip,out _,out _) && clip==expected,"actual Harmony did not route clip "+expected);}
var completions=0;
chomper.StartChomp(new GameObject("food"),Identifiable.Id.NONE,false,false,()=>{},(_,_,_,_)=>completions++);
Clip("Bite v4");
chomper.BiteComplete();
Check(completions==1 && chomper.NativeCompletionCalls==1,"actual Harmony suppressed/invented native completion");
Clip("Exact authored idle");
var movement=actor.AddComponent<SlimeRandomMove>();
movement.QueueJump(5);movement.Action();Clip("Pounce v4");
var playback=visual.GetComponent<Animation>()!;playback["Pounce v4"]!.time=.4f;
movement.Action();Check(playback["Pounce v4"]!.time==.4f,"real Harmony reset pounce on non-jump Action");
var plexer=actor.AddComponent<SlimeSubbehaviourPlexer>();
plexer.Ground(null);plexer.Ground(new GameObject("ground").AddComponent<Collider>());Clip("Exact authored idle");
var find=actor.AddComponent<FindConsumable>();var availability=0f;find.InvokeMove(ref availability,9f);
Clip("Pounce v4");Check(availability==9f,"native byref timer modified");
var observerParameter=typeof(ImportedGameplayAnimationPatch).GetMethod("MoveTowardsPostfix",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)!.GetParameters()[1];
Check(observerParameter.IsIn && observerParameter.ParameterType.IsByRef,"native ref timer observer must bind read-only, not mutate/copy an old value");
var stalk=actor.AddComponent<StalkConsumable>();stalk.SetPounceMode(false);stalk.InvokeLeap();
plexer.Ground(null);plexer.Ground(new GameObject("ground2").AddComponent<Collider>());Clip("Exact authored idle");
stalk.SetPounceMode(true);stalk.InvokeLeap();Clip("Pounce v4");
Check(stalk.NativeLeapCalls==2 && plexer.NativeGroundCallbacks==4,"native movement/grounded originals must always execute");
chomper.StartChomp(new GameObject("food2"),Identifiable.Id.NONE,false,false,()=>{},(_,_,_,_)=>completions++);Clip("Bite v4");chomper.Cancel();Clip("Exact authored idle");
Check(completions==1,"cancel invoked native completion");
var native=new GameObject("native-unmarked");native.SetActive(true);var nativeChomper=native.AddComponent<Chomper>();
nativeChomper.StartChomp(new GameObject("native-food"),Identifiable.Id.NONE,false,false,()=>{},(_,_,_,_)=>completions++);nativeChomper.BiteComplete();
Check(completions==2 && native.GetComponent<ImportedGameplayAnimation>()==null,"native actor hook path changed");
ImportedGameplayAnimationPatch.Unload();
Check(!Harmony.GetAllPatchedMethods().Any(m=>Harmony.GetPatchInfo(m).Owners.Contains("slimebridge.sr1.imported-gameplay-animation")),"own patch ID remained after unload");
Check(Harmony.GetPatchInfo(startTarget).Owners.Contains("slimebridge.test.foreign-sentinel") && ForeignProbe.Calls>0,"unload removed or bypassed a foreign observer");
foreign.UnpatchAll("slimebridge.test.foreign-sentinel");
Console.WriteLine($"actual Harmony checks={checks} failures=0; real SR1 Harmony on synthetic behaviours, no Unity runtime");
public static class ForeignProbe
{
 public static int Calls;
 public static void Observe(){Calls++;}
}
namespace SlimeBridge.SR1 { internal static class BridgeEntry { internal static bool LabRestPoseRequested=true; } }
