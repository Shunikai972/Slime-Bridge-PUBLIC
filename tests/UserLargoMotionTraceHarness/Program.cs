using System.Reflection;
using System.Text.Json;
using SlimeBridge.SR1;
using UnityEngine;

// Exact production observer linked against inert Unity doubles. No native/game/input calls.
internal static class Program
{
    private static int _failed;
    private static int _checks;
    private static readonly MethodInfo Sample = typeof(LabUserLargoMotionProbe).GetMethod("Sample", BindingFlags.Instance | BindingFlags.NonPublic)!;
    private static void Check(string name, Action test)
    {
        _checks++;
        try { test(); Console.WriteLine("PASS " + name); }
        catch (Exception error) { _failed++; Console.WriteLine("FAIL " + name + ": " + error.Message); }
    }
    private static void Require(bool value, string message)
    {
        if (!value) throw new InvalidOperationException(message);
    }
    private static JsonDocument Capture(GameObject actor)
    {
        var probe = new LabUserLargoMotionProbe { gameObject = actor };
        return JsonDocument.Parse((string)Sample.Invoke(probe, null)!);
    }
    public static int Main()
    {
        Check("same actor animation is nested JSON with counters, playback and history", () =>
        {
            var actor = new GameObject();
            var animation = actor.AddComponent<ImportedGameplayAnimation>();
            using var row = Capture(actor);
            var trace = row.RootElement.GetProperty("gameplay_animation");
            Require(trace.ValueKind == JsonValueKind.Object, "Animation must not be a quoted JSON string");
            Require(trace.GetProperty("counters").GetProperty("jumps").GetInt32() == 2, "Same actor jump counter absent");
            Require(trace.GetProperty("playback").GetProperty("clip").GetString() == "Pounce", "Playback absent");
            Require(trace.GetProperty("history")[0].GetProperty("event").GetString() == "jump_observed", "History absent");
            Require(animation.Reads == 1, "One read-only animation serialization per sample");
            Require(row.RootElement.GetProperty("effects").GetArrayLength() == 0, "Existing effect payload changed");
        });
        Check("missing controller is explicit null, never fabricated animation", () =>
        {
            using var row = Capture(new GameObject());
            Require(row.RootElement.GetProperty("gameplay_animation").ValueKind == JsonValueKind.Null, "Missing controller must be null");
        });
        Check("foreign controller cannot provide actor motion evidence", () =>
        {
            var foreign = new GameObject().AddComponent<ImportedGameplayAnimation>();
            using var row = Capture(new GameObject());
            Require(row.RootElement.GetProperty("gameplay_animation").ValueKind == JsonValueKind.Null && foreign.Reads == 0,
                "Observer borrowed a foreign controller");
        });
        Check("missing Quantum generator is explicit null", () =>
        {
            using var row = Capture(new GameObject());
            Require(row.RootElement.GetProperty("quantum_binding").ValueKind == JsonValueKind.Null,
                "Missing generator must not produce fabricated binding state");
        });
        Check("missing Quantum applicator is distinguished without touching native generation", () =>
        {
            var actor = new GameObject();
            var generator = actor.AddComponent<GenerateQuantumQubit>();
            generator.QubitPrefab = new GameObject();
            using var row = Capture(actor);
            var binding = row.RootElement.GetProperty("quantum_binding");
            Require(binding.GetProperty("applicator_managed_null").GetBoolean() &&
                binding.GetProperty("applicator_unity_null").GetBoolean(), "Null applicator evidence absent");
            Require(!binding.GetProperty("qubit_prefab_null").GetBoolean() && generator.enabled &&
                generator.AppearanceApplicator == null, "Observer changed native generator state");
        });
        Check("actor-local disabled applicator with null appearance stays observable and unchanged", () =>
        {
            var actor = new GameObject();
            var app = actor.AddComponent<SlimeAppearanceApplicator>();
            app.enabled = false;
            actor.AddComponent<GenerateQuantumQubit>().AppearanceApplicator = app;
            using var row = Capture(actor);
            var binding = row.RootElement.GetProperty("quantum_binding");
            Require(binding.GetProperty("applicator_actor_local").GetBoolean() &&
                binding.GetProperty("appearance_null").GetBoolean() &&
                !binding.GetProperty("applicator_enabled").GetBoolean(), "Leading null invariant not observed");
            Require(app.Appearance == null && !app.enabled, "Observer repaired or re-enabled an applicator");
        });
        Check("foreign applicator is reported rather than rebound or treated as actor-local", () =>
        {
            var actor = new GameObject();
            var app = new GameObject().AddComponent<SlimeAppearanceApplicator>();
            app.Appearance = new SlimeAppearance { QubitAppearance = new SlimeAppearance() };
            var generator = actor.AddComponent<GenerateQuantumQubit>();
            generator.AppearanceApplicator = app;
            using var row = Capture(actor);
            var binding = row.RootElement.GetProperty("quantum_binding");
            Require(!binding.GetProperty("applicator_actor_local").GetBoolean() &&
                !binding.GetProperty("appearance_null").GetBoolean() &&
                !binding.GetProperty("qubit_appearance_null").GetBoolean(), "Foreign/non-null binding state lost");
            Require(ReferenceEquals(generator.AppearanceApplicator, app), "Observer rebound a native reference");
        });
        Check("bounded observation samples at two Hz for a minute-long window", () =>
        {
            var probe = new LabUserLargoMotionProbe { gameObject = new GameObject() };
            var type = typeof(LabUserLargoMotionProbe);
            var tick = type.GetMethod("LateUpdate", BindingFlags.Instance | BindingFlags.NonPublic)!;
            type.GetField("_thread", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(probe,
                Environment.CurrentManagedThreadId);
            var rows = (List<string>)type.GetField("_rows", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(probe)!;
            Time.realtimeSinceStartup = 1f;
            tick.Invoke(probe, null);
            Require(rows.Count == 1, "First sample missing");
            Time.realtimeSinceStartup = 1.2f;
            tick.Invoke(probe, null);
            Require(rows.Count == 1, "Sampling too fast: 120 rows would end before the native idle interval");
            Time.realtimeSinceStartup = 1.5f;
            tick.Invoke(probe, null);
            Require(rows.Count == 2, "Half-second sample missing");
        });
        Console.WriteLine($"USER_LARGO_MOTION_TRACE_CHECKS={_checks} FAILURES={_failed}");
        return _failed == 0 ? 0 : 1;
    }
}

namespace UnityEngine
{
    public class Component
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public T? GetComponent<T>() where T : class => gameObject.GetComponent<T>();
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : class => Array.Empty<T>();
        public int GetInstanceID() => 123;
        public string name = "fixture";
    }
    public class MonoBehaviour : Component { public bool enabled = true; }
    public sealed class GameObject
    {
        private readonly Dictionary<Type, Component> _components = new();
        public int GetInstanceID() => 123;
        public Transform transform = new();
        public T? GetComponent<T>() where T : class => _components.TryGetValue(typeof(T), out var component) ? component as T : null;
        public T AddComponent<T>() where T : Component, new()
        {
            var component = new T { gameObject = this }; _components[typeof(T)] = component; return component;
        }
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : class => Array.Empty<T>();
    }
    public sealed class Transform
    {
        public Vector3 position, localPosition, localScale;
        public string name = "bone";
        public Transform? parent;
        public bool IsChildOf(Transform ancestor)
        {
            for (var node = parent; node != null; node = node.parent)
                if (ReferenceEquals(node, ancestor)) return true;
            return false;
        }
    }
    public struct Vector3 { public float x, y, z; }
    public sealed class Rigidbody : Component { public Vector3 velocity; public float mass; public bool useGravity, isKinematic; }
    public static class Time { public static float realtimeSinceStartup = 1; public static int frameCount = 7; }
    public static class Debug { public static void LogWarning(string text) { } }
}
public sealed class RubberBoneEffect : MonoBehaviour
{
    public sealed class Bone { public Transform? trans; public float intensity; }
    public Bone[]? bones = Array.Empty<Bone>();
    public string Presets = "fixture";
    public float mass, stiffness, damping, gravity;
}
public sealed class GenerateQuantumQubit : MonoBehaviour
{
    public SlimeAppearanceApplicator? AppearanceApplicator;
    public GameObject? QubitPrefab;
}
public sealed class SlimeAppearanceApplicator : MonoBehaviour { public SlimeAppearance? Appearance; }
public sealed class SlimeAppearance { public SlimeAppearance? QubitAppearance; }
namespace SlimeBridge.Shared
{
    internal static class LabSpecPathGuard
    {
        public const string OutRelativeRoot = "Lab/RuntimeArtifacts";
        public static string? FindRepoRoot() => null;
        public static bool IsAllowedDirectory(string root, string output, out string reason) { reason = "inert"; return false; }
    }
    internal static class RegistrySnapshotFormat
    {
        public static string Quote(string value) => JsonSerializer.Serialize(value);
        public static void PublishCaptureFile(string output, string name, string text) => throw new InvalidOperationException("Native publication forbidden in synthetic harness");
    }
}
namespace SlimeBridge.SR1
{
    internal static class BridgeEntry
    {
        public static bool OracleExecutableMatches() => false;
        public static bool LabRestPoseRequested => false;
    }
    internal sealed class LabRestPosePin : MonoBehaviour { }
    internal sealed class ImportedVisualMarker : MonoBehaviour { }
    internal sealed class ImportedGameplayAnimation : MonoBehaviour
    {
        public int Reads;
        public string ToJson()
        {
            Reads++;
            return "{\"counters\":{\"jumps\":2,\"landings\":1},\"playback\":{\"clip\":\"Pounce\"},\"history\":[{\"event\":\"jump_observed\"}]}";
        }
    }
}
