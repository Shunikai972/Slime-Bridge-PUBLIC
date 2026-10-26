using System.Reflection;
using SlimeBridge.Shared;
using UnityEngine;

var playerType = Assembly.GetExecutingAssembly().GetType("SlimeBridge.SR1.ImportedSourceAnimation")
    ?? throw new Exception("continuous source player missing");
var checks = 0;
var fixture = """
{"default_clip":"idle","clips":[{"name":"idle","duration":2,"sample_rate":1,"loop":true,
"times":[0,1],"channels":[{"path":"bone_root","property":"local_position","values":[[0,0,0],[2,4,6]]}]}]}
""";
Dictionary<string, object> Parse(string json) => (Dictionary<string, object>)MiniJson.Parse(json)!;
bool Attach(GameObject root, string json, out string problem)
{
    object?[] args = [root, Parse(json), ""];
    var result = (bool)playerType.GetMethod("TryAttach", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, args)!;
    problem = (string)args[2]!;
    return result;
}
void Check(bool ok, string message) { if (!ok) throw new Exception(message); checks++; }
bool Play(GameObject root, string name, out string problem)
{
    object?[] args = [name, ""];
    var result = (bool)playerType.GetMethod("TryPlay", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(root.GetComponent(playerType), args)!;
    problem = (string)args[1]!;
    return result;
}
var template = new GameObject();
Check(Attach(template, fixture, out var reason), "valid source refused: " + reason);
var animation = template.GetComponent<Animation>()!;
Check(animation != null && !animation.playAutomatically, "no explicit native legacy playback");
Check(animation!["idle"]!.clip.legacy && animation["idle"]!.clip.length == 2, "declared duration was truncated to last sample");
template.SetActive(true);
animation.Advance(0.25f);
Check(Math.Abs(template.PositionX - 0.5f) < 0.00001f, "no continuous between-key advancement");
animation.Advance(1.25f);
Check(template.PositionX == 2, "unsampled source-duration tail must hold last sample");
animation.Advance(0.75f);
Check(Math.Abs(template.PositionX - 0.5f) < 0.00001f, "wrong declared loop period");
Check(Play(template, "idle", out reason), "explicit reset refused: " + reason);
animation.Advance(0.25f);
Check(Math.Abs(animation["idle"]!.time - 0.25f) < 0.00001f, "play did not reset source clock");
// Two Instantiate-like serialized clones share immutable clips, not AnimationState clocks or ownership.
template.SetActive(false);
var actorA = template.CloneInactive();
var actorB = template.CloneInactive();
var a = actorA.GetComponent<Animation>()!;
var b = actorB.GetComponent<Animation>()!;
Check(ReferenceEquals(a["idle"]!.clip, b["idle"]!.clip), "clones rebuilt source clips");
Check(!ReferenceEquals(a["idle"], b["idle"]), "actors share animation clocks");
a.Advance(0.5f);
Check(actorA.PositionX == 0, "inactive template/clone animation sampled early");
actorA.SetActive(true); actorB.SetActive(true);
a.Advance(0.25f); b.Advance(0.75f);
Check(actorA.PositionX == 0.5f && actorB.PositionX == 1.5f, "actors did not advance independently");
actorA.SetActive(false); actorA.SetActive(true); a.Advance(0.25f);
Check(actorA.PositionX == 0.5f && b["idle"]!.time == 0.75f, "activation reset affected other actor");
UnityEngine.Object.Destroy(actorA.GetComponent(playerType));
Check(!b["idle"]!.clip.Destroyed, "actor destroyed template-owned shared clips");
b.Advance(0.25f);
Check(actorB.PositionX == 2, "remaining actor lost shared clip");
Check(!Play(actorB, "missing", out reason) && reason != "" && b["idle"]!.time == 1, "unknown action mutated running clock");
// Unity may not carry runtime AddClip states through Instantiate: a clone whose Animation lost its states
// must re-register the SAME shared clip (no rebuild, no copy), with its own independent clock.
var sharedIdle = b["idle"]!.clip;
var actorC = template.CloneInactive();
var c = actorC.GetComponent<Animation>()!;
c.States.Clear();
actorC.SetActive(true);
Check(Play(actorC, "idle", out reason), "clone without runtime states could not re-register its source clip: " + reason);
Check(ReferenceEquals(c["idle"]!.clip, sharedIdle), "re-registration rebuilt or copied the shared clip");
c.Advance(0.25f);
Check(c["idle"]!.time == 0.25f && b["idle"]!.time == 1, "re-registered clone shares or disturbs another actor's clock");
Check(!sharedIdle.Destroyed, "re-registration destroyed the shared clip");
var quaternion = fixture.Replace("local_position", "local_rotation").Replace("[0,0,0]", "[0,0,0,1]").Replace("[2,4,6]", "[0,0,0,-1]");
var rotationRoot = new GameObject();
Check(Attach(rotationRoot, quaternion, out reason), "equivalent quaternion signs refused: " + reason);
var rotationKeys = rotationRoot.GetComponent<Animation>()!["idle"]!.clip.Curves["bone_root|localRotation.w"].keys;
Check(rotationKeys.All(k => k.value == 1), "quaternion opposite signs were interpolated through zero");
void Refuse(string json)
{
    var candidate = new GameObject();
    Check(!Attach(candidate, json, out var error) && error != "", "malformed descriptor accepted: " + json);
    Check(candidate.Components.Count == 0 && candidate.SampleCount == 0, "partial components/sampling after refusal");
}
Refuse(fixture.Replace("\"duration\":2", "\"duration\":0"));
Refuse(fixture.Replace("\"duration\":2", "\"duration\":120.00000001"));
Refuse(fixture.Replace("\"sample_rate\":1", "\"sample_rate\":240.00000001"));
Refuse(fixture.Replace("[2,4,6]", "[100000.00000001,4,6]"));
Refuse(fixture.Replace("\"times\":[0,1]", "\"times\":[-1e-300,1]"));
Refuse(fixture.Replace("[2,4,6]", "[1e-300,4,6]"));
Refuse(fixture.Replace("\"duration\":2", "\"duration\":121"));
Refuse(fixture.Replace("\"duration\":2", "\"duration\":true"));
Refuse(fixture.Replace("\"duration\":2", "\"duration\":1e300"));
Refuse(fixture.Replace("\"duration\":2", "\"duration\":0.9999999"));
Refuse(fixture.Replace("\"sample_rate\":1", "\"sample_rate\":0"));
Refuse(fixture.Replace("\"sample_rate\":1", "\"sample_rate\":241"));
Refuse(fixture.Replace("\"sample_rate\":1", "\"sample_rate\":true"));
Refuse(fixture.Replace("\"loop\":true", "\"loop\":1"));
Refuse(fixture.Replace("\"default_clip\":\"idle\"", "\"default_clip\":\"missing\""));
Refuse(fixture.Replace("\"times\":[0,1]", "\"times\":[0,0]"));
Refuse(fixture.Replace("\"times\":[0,1]", "\"times\":[0.1,1]"));
Refuse(fixture.Replace("\"times\":[0,1]", "\"times\":[0,-1]"));
Refuse(fixture.Replace("\"times\":[0,1]", "\"times\":[0,0.0000001]"));
foreach (var path in new[] { "", "/bone_root", "../bone_root", "bone_root/..", "bone_root//child", "bone_root/./child", "missing" })
    Refuse(fixture.Replace("\"path\":\"bone_root\"", "\"path\":\"" + path + "\""));
Refuse(fixture.Replace("local_position", "local_euler"));
Refuse(fixture.Replace("[2,4,6]", "[2,4]"));
Refuse(fixture.Replace("[2,4,6]", "[2,4,6,1]"));
Refuse(fixture.Replace("[2,4,6]", "[true,4,6]"));
Refuse(fixture.Replace("[2,4,6]", "[100001,4,6]"));
Refuse(fixture.Replace("[2,4,6]", "[1e300,4,6]"));
Refuse(quaternion.Replace("[0,0,0,-1]", "[0,0,0,0]"));
var sourceObject = ((List<object>)Parse(fixture)["clips"])[0];
var duplicateClip = Parse(fixture);
((List<object>)duplicateClip["clips"]).Add(sourceObject);
var candidateDuplicate = new GameObject();
object?[] duplicateArgs = [candidateDuplicate, duplicateClip, ""];
Check(!(bool)playerType.GetMethod("TryAttach", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, duplicateArgs)!, "duplicate clip accepted");
var sourceJson = System.Text.Json.JsonSerializer.Serialize(sourceObject);
Refuse(fixture.Replace("\"clips\":[", "\"clips\":[" + sourceJson + ","));
var channelJson = "{\"path\":\"bone_root\",\"property\":\"local_position\",\"values\":[[0,0,0],[2,4,6]]}";
Refuse(fixture.Replace("\"channels\":[", "\"channels\":[" + channelJson + ","));
Animation.ThrowOnAddClip = true;
var destroyedBefore = UnityEngine.Object.DestroyedObjects.OfType<AnimationClip>().Count();
Refuse(fixture);
Check(UnityEngine.Object.DestroyedObjects.OfType<AnimationClip>().Count() == destroyedBefore + 1, "AddClip failure leaked native source clip");
Animation.ThrowOnAddClip = false;
Check(!Attach(template, fixture, out reason), "already-owned template accepted duplicate attachment");
Check(!b["idle"]!.clip.Destroyed, "duplicate attachment destroyed existing resources");
var single = fixture.Replace("\"times\":[0,1]", "\"times\":[0]").Replace("[[0,0,0],[2,4,6]]", "[[2,4,6]]");
var singleRoot = new GameObject();
Check(Attach(singleRoot, single, out reason), "one-sample authored hold refused: " + reason);
Check(singleRoot.GetComponent<Animation>()!["idle"]!.clip.length == 2, "single-sample clip has no duration");
var onceRoot = new GameObject();
Check(Attach(onceRoot, fixture.Replace("\"loop\":true", "\"loop\":false"), out reason), "nonloop clip refused: " + reason);
onceRoot.SetActive(true); onceRoot.GetComponent<Animation>()!.Advance(2.25f);
Check(onceRoot.PositionX == 2, "nonloop clip wrapped instead of holding authored endpoint");
object?[] fadeArgs = ["idle", 0.2f, ""];
Check((bool)playerType.GetMethod("TryCrossFade", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(actorB.GetComponent(playerType), fadeArgs)!, "CrossFade contract refused valid source action");
Check(b["idle"]!.time == 0, "CrossFade did not reset target clock");
foreach (var fade in new[] { -0.1f, 10.1f, float.NaN, float.PositiveInfinity })
{
    object?[] badFade = ["idle", fade, ""];
    Check(!(bool)playerType.GetMethod("TryCrossFade", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(actorB.GetComponent(playerType), badFade)!, "invalid fade accepted");
}
object?[] absentDeclared = [new GameObject(), new Dictionary<string, object>(), false, ""];
Check((bool)playerType.GetMethod("TryAttachDeclared", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, absentDeclared)! && !(bool)absentDeclared[2]!, "absent authored descriptor did not preserve legacy fallback");
foreach (var declared in new object[] { true, "foreign", new List<object>(), new Dictionary<string, object>() })
{
    var declaredRoot = new GameObject();
    object?[] declaredArgs = [declaredRoot, new Dictionary<string, object> { ["source_animation"] = declared }, false, ""];
    Check(!(bool)playerType.GetMethod("TryAttachDeclared", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, declaredArgs)! && (bool)declaredArgs[2]! && (string)declaredArgs[3]! != "", "invalid declared source allowed visual alias fallback");
}
var actionRoot = new GameObject();
var actionDescriptor = Parse(fixture);
var actionSources = (List<object>)actionDescriptor["clips"];
actionSources.Add(((List<object>)Parse(fixture.Replace("\"name\":\"idle\"", "\"name\":\"pounce\"").Replace("\"loop\":true", "\"loop\":false"))["clips"])[0]);
actionSources.Add(((List<object>)Parse(fixture.Replace("\"name\":\"idle\"", "\"name\":\"bite\"").Replace("\"loop\":true", "\"loop\":false"))["clips"])[0]);
object?[] actionAttach = [actionRoot, actionDescriptor, ""];
Check((bool)playerType.GetMethod("TryAttach", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, actionAttach)!, "multiple source action clips refused");
actionRoot.SetActive(true);
Check(Play(actionRoot, "pounce", out reason), "pounce action contract unavailable");
var actionAnimation = actionRoot.GetComponent<Animation>()!;
actionAnimation.Advance(0.5f);
Check(actionAnimation["pounce"]!.time == 0.5f && !actionAnimation["idle"]!.enabled, "action Play did not isolate target clock");
object?[] biteFade = ["bite", 0.2f, ""];
Check((bool)playerType.GetMethod("TryCrossFade", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(actionRoot.GetComponent(playerType), biteFade)!, "bite source CrossFade unavailable");
Check(actionAnimation["bite"]!.time == 0, "bite clock was not reset");
Check(Play(actionRoot, "idle", out reason) && actionAnimation["idle"]!.time == 0, "return-to-idle action contract unavailable");
var templatePlayer = template.GetComponent(playerType)!;
playerType.GetMethod("ReleaseOwnedClips", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(templatePlayer, null);
Check(b["idle"]!.clip.Destroyed, "explicit inactive-template release leaked source clips");
var destroyedAfterRelease = UnityEngine.Object.DestroyedObjects.OfType<AnimationClip>().Count();
UnityEngine.Object.Destroy(templatePlayer);
Check(UnityEngine.Object.DestroyedObjects.OfType<AnimationClip>().Count() == destroyedAfterRelease, "OnDestroy released source clips twice");
Console.WriteLine($"source animation checks={checks} failures=0; production-linked synthetic clock, not Unity runtime");

namespace SlimeBridge.SR1
{
    internal static class BridgeEntry { internal static bool LabRestPoseRequested = true; }
}
namespace UnityEngine
{
    [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute { }
    public enum WrapMode { Default, Once, Loop, ClampForever }
    public enum PlayMode { StopSameLayer, StopAll }
    public enum AnimationCullingType { AlwaysAnimate, BasedOnRenderers }
    public class Object
    {
        public bool Destroyed;
        public static readonly List<Object> DestroyedObjects = new();
        public static void Destroy(Object? value)
        {
            if (value == null || value.Destroyed) return;
            value.Destroyed = true;
            DestroyedObjects.Add(value);
            if (value is Component c) { c.Invoke("OnDestroy"); c.gameObject.Components.Remove(c); }
        }
    }
    public class Component : Object
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public T? GetComponent<T>() where T : Component => gameObject.GetComponent<T>();
        public void Invoke(string name) => GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)?.Invoke(this, null);
    }
    public class MonoBehaviour : Component { public bool enabled = true; }
    public class Transform : Component
    {
        public Transform? Find(string path) => path == "bone_root" || path == "bone_root/child" ? this : null;
    }
    public class GameObject : Object
    {
        public Transform transform;
        public bool activeSelf;
        public float PositionX;
        public int SampleCount;
        public List<Component> Components = new();
        public GameObject() { transform = new Transform { gameObject = this }; }
        public T AddComponent<T>() where T : Component, new()
        {
            var component = new T { gameObject = this }; Components.Add(component); return component;
        }
        public T? GetComponent<T>() where T : Component => Components.OfType<T>().FirstOrDefault(c => !c.Destroyed);
        public Component? GetComponent(Type type) => Components.FirstOrDefault(c => type.IsInstanceOfType(c) && !c.Destroyed);
        public void SetActive(bool active)
        {
            activeSelf = active;
            foreach (var c in Components.ToArray()) c.Invoke(active ? "OnEnable" : "OnDisable");
        }
        public GameObject CloneInactive()
        {
            var clone = new GameObject();
            foreach (var original in Components)
            {
                var copy = (Component)Activator.CreateInstance(original.GetType())!;
                copy.gameObject = clone; clone.Components.Add(copy);
                if (original is Animation animation && copy is Animation copied)
                {
                    foreach (var state in animation.States.Values) copied.AddClip(state.clip, state.clip.name);
                    copied.playAutomatically = animation.playAutomatically;
                    continue;
                }
                foreach (var field in original.GetType().GetFields(BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public))
                {
                    if (!field.IsDefined(typeof(SerializeField), false) || field.IsDefined(typeof(NonSerializedAttribute), false)) continue;
                    var value = field.GetValue(original);
                    if (value is Animation) value = clone.GetComponent<Animation>();
                    field.SetValue(copy, value);
                }
            }
            return clone;
        }
    }
    public struct Keyframe
    {
        public float time, value, inTangent, outTangent;
        public Keyframe(float t, float v) { time = t; value = v; inTangent = outTangent = 0; }
    }
    public class AnimationCurve
    {
        public Keyframe[] keys;
        public AnimationCurve(params Keyframe[] values) { keys = values; }
        public float Evaluate(float t)
        {
            if (t <= keys[0].time) return keys[0].value;
            for (var i = 1; i < keys.Length; i++)
            {
                if (t > keys[i].time) continue;
                var a = keys[i - 1]; var b = keys[i]; var d = b.time - a.time; var x = (t - a.time) / d;
                return (2*x*x*x-3*x*x+1)*a.value + (x*x*x-2*x*x+x)*d*a.outTangent +
                       (-2*x*x*x+3*x*x)*b.value + (x*x*x-x*x)*d*b.inTangent;
            }
            return keys[^1].value;
        }
    }
    public class AnimationClip : Object
    {
        public string name = "";
        public bool legacy;
        public float frameRate;
        public WrapMode wrapMode;
        public Dictionary<string, AnimationCurve> Curves = new();
        public float length => Curves.Values.Max(c => c.keys[^1].time);
        public void SetCurve(string path, Type type, string property, AnimationCurve curve)
        {
            if (!legacy || type != typeof(Transform)) throw new Exception("invalid curve contract");
            Curves.Add(path + "|" + property, curve);
        }
        public void EnsureQuaternionContinuity() { }
        public void SampleAnimation(GameObject root, float time) { root.SampleCount++; Apply(root, time); }
        public void Apply(GameObject root, float time)
        {
            if (Destroyed) throw new Exception("shared source clip destroyed");
            if (Curves.TryGetValue("bone_root|localPosition.x", out var curve)) root.PositionX = curve.Evaluate(time);
        }
    }
    public class AnimationState
    {
        public AnimationClip clip;
        public float time, speed = 1;
        public bool enabled;
        public WrapMode wrapMode;
        public AnimationState(AnimationClip source) { clip = source; wrapMode = source.wrapMode; }
    }
    public class Animation : Component
    {
        public static bool ThrowOnAddClip;
        public bool playAutomatically;
        public bool animatePhysics;
        public AnimationCullingType cullingType;
        public AnimationClip? clip;
        public readonly Dictionary<string, AnimationState> States = new();
        public AnimationState? this[string name] => States.TryGetValue(name, out var state) ? state : null;
        public void AddClip(AnimationClip source, string name)
        {
            if (ThrowOnAddClip) throw new InvalidOperationException("injected AddClip failure");
            States.Add(name, new AnimationState(source));
        }
        public bool Play(string name, PlayMode mode)
        {
            if (!States.TryGetValue(name, out var selected)) return false;
            foreach (var state in States.Values) state.enabled = false;
            selected.enabled = true; return true;
        }
        public void CrossFade(string name, float length, PlayMode mode) { Play(name, mode); }
        public void Stop() { foreach (var state in States.Values) state.enabled = false; }
        public void Advance(float seconds)
        {
            if (!gameObject.activeSelf) return;
            foreach (var state in States.Values)
            {
                if (!state.enabled) continue;
                state.time += seconds * state.speed;
                var t = state.wrapMode == WrapMode.Loop ? state.time % state.clip.length : Math.Min(state.time, state.clip.length);
                state.clip.Apply(gameObject, t);
            }
        }
    }
}
