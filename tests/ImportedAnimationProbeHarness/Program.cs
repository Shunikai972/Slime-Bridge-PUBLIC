using SlimeBridge.SR1;
using SlimeBridge.Shared;
using UnityEngine;

var fixture = """
{"lab_source_clip":{"sample_time":1,"clip":{"name":"test","duration":1,"sample_rate":1,"loop":true,
"times":[0,1],"channels":[{"path":"bone_root","property":"local_position","values":[[0,0,0],[1,2,3]]}]}}}
""";
var document = Parse(fixture);
var root = new GameObject();
if (!ImportedAnimationProbe.TrySample(root, document, out var problem))
    throw new Exception("valid decoded source pose refused: " + problem);
if (root.SampleCount != 1 || root.SampleTime != 1 || root.Curves.Count != 3)
    throw new Exception("native sampling was not invoked with XYZ curves");
if (root.Curves["bone_root|localPosition.z"].keys[1].value != 3)
    throw new Exception("source coordinates were modified");
if (UnityEngine.Object.Destroyed != 1) throw new Exception("temporary clip leaked");
var checks = 4;
void Refuse(string json)
{
    var candidate = new GameObject();
    if (ImportedAnimationProbe.TrySample(candidate, Parse(json), out var reason) || candidate.SampleCount != 0 || reason == "")
        throw new Exception("malformed source pose sampled: " + json);
    checks++;
}
Refuse(fixture.Replace("\"sample_time\":1", "\"sample_time\":0.5"));
Refuse(fixture.Replace("\"sample_time\":1", "\"sample_time\":-0.0000001"));
Refuse(fixture.Replace("\"sample_time\":1", "\"sample_time\":true"));
Refuse(fixture.Replace("\"times\":[0,1]", "\"times\":[0,0]"));
Refuse(fixture.Replace("\"duration\":1", "\"duration\":0"));
Refuse(fixture.Replace("\"path\":\"bone_root\"", "\"path\":\"missing\""));
Refuse(fixture.Replace("local_position", "local_euler"));
Refuse(fixture.Replace("[1,2,3]", "[1,2]"));
Refuse(fixture.Replace("[1,2,3]", "[true,2,3]"));
Refuse(fixture.Replace("[1,2,3]", "[1e300,2,3]"));
Refuse(fixture.Replace("[1,2,3]", "[100001,2,3]"));
Refuse(fixture.Replace("[1,2,3]", "[1,2,3,4]"));
Refuse(fixture.Replace("local_position", "local_rotation").Replace("[0,0,0]", "[0,0,0,0]").Replace("[1,2,3]", "[0,0,0,0]"));
var absent = new GameObject();
if (!ImportedAnimationProbe.TrySample(absent, new(), out _) || absent.SampleCount != 0) throw new Exception("absent probe was not a no-op");
checks++;
BridgeEntry.LabRestPoseRequested = false;
if (!ImportedAnimationProbe.TrySample(absent, new() { ["lab_source_clip"] = true }, out _) || absent.SampleCount != 0)
    throw new Exception("unauthorized probe parsed or sampled data");
BridgeEntry.LabRestPoseRequested = true;
checks++;
AnimationClip.ThrowOnSample = true;
var before = UnityEngine.Object.Destroyed;
Refuse(fixture);
if (UnityEngine.Object.Destroyed != before + 1) throw new Exception("throwing native sample leaked clip");
AnimationClip.ThrowOnSample = false;
checks++;
Console.WriteLine($"animation probe checks={checks} failures=0; synthetic only, no Unity playback exercised");

static Dictionary<string, object> Parse(string json) => (Dictionary<string, object>)MiniJson.Parse(json)!;

namespace SlimeBridge.SR1
{
    internal static class BridgeEntry { internal static bool LabRestPoseRequested = true; }
}
namespace UnityEngine
{
    public class Object
    {
        public static int Destroyed;
        public static void Destroy(Object? value) { if (value != null) Destroyed++; }
    }
    public class Transform { public Transform? Find(string path) => path == "bone_root" ? this : null; }
    public class GameObject : Object
    {
        public Transform transform = new();
        public int SampleCount;
        public float SampleTime;
        public Dictionary<string, AnimationCurve> Curves = new();
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
    }
    public class AnimationClip : Object
    {
        public string name = "";
        public bool legacy;
        public float frameRate;
        readonly Dictionary<string, AnimationCurve> curves = new();
        public void SetCurve(string path, Type type, string property, AnimationCurve curve)
        {
            if (!legacy || type != typeof(Transform)) throw new Exception("invalid native curve contract");
            curves.Add(path + "|" + property, curve);
        }
        public void EnsureQuaternionContinuity() { }
        public static bool ThrowOnSample;
        public void SampleAnimation(GameObject root, float time)
        {
            if (ThrowOnSample) throw new InvalidOperationException("injected native sample failure");
            root.SampleCount++;
            root.SampleTime = time;
            root.Curves = curves;
        }
    }
}
