using System.Reflection;
using UnityEngine;

// Exact production binder with inert field/ownership doubles. No native generation calls.
internal static class Program
{
    private const string Canonical = "largo.cotton.quantum";
    private static int _checks, _failures;
    private static void Check(string label, Action action)
    {
        _checks++;
        try { action(); Console.WriteLine("PASS " + label); }
        catch (Exception error) { _failures++; Console.WriteLine("FAIL " + label + ": " + error.Message); }
    }
    private static void Require(bool value, string reason) { if (!value) throw new InvalidOperationException(reason); }
    private static bool Repair(GameObject actor, string canonical)
    {
        var type = typeof(Program).Assembly.GetType("SlimeBridge.SR1.ImportedQuantumAppearance");
        Require(type != null, "Production quantum binding correction missing");
        var method = type!.GetMethod("EnsureBound", BindingFlags.Static | BindingFlags.NonPublic);
        Require(method != null, "Production EnsureBound seam missing");
        object?[] args = { actor, canonical, "" };
        return (bool)method!.Invoke(null, args)!;
    }
    private static (GameObject actor, GenerateQuantumQubit generator, SlimeAppearanceApplicator app, SlimeAppearance owned) Fixture()
    {
        var actor = new GameObject();
        actor.AddComponent<Identifiable>().id = (Identifiable.Id)960013;
        var app = actor.AddComponent<SlimeAppearanceApplicator>();
        app.enabled = false;
        var owned = new SlimeAppearance { QubitAppearance = new SlimeAppearance() };
        app.SlimeDefinition = new SlimeDefinition { name = "SlimeBridge_" + Canonical, IdentifiableId = (Identifiable.Id)960013, AppearancesDefault = new[] { owned } };
        var generator = actor.AddComponent<GenerateQuantumQubit>();
        generator.AppearanceApplicator = app;
        generator.QubitPrefab = new GameObject();
        return (actor, generator, app, owned);
    }
    public static int Main()
    {
        Check("null appearance uses exact owned definition without enabling placeholder or changing generation", () =>
        {
            var f = Fixture(); var prefab = f.generator.QubitPrefab;
            Require(Repair(f.actor, Canonical), "Owned binding refused");
            Require(ReferenceEquals(f.app.Appearance, f.owned), "Null appearance not bound to owned appearance");
            Require(ReferenceEquals(f.generator.AppearanceApplicator, f.app) && ReferenceEquals(f.generator.QubitPrefab, prefab), "Native references replaced");
            Require(f.generator.enabled && !f.app.enabled && f.generator.generationState == 17, "Native generation/enable state changed");
        });
        Check("repeat reapply restores a cleared field but retains existing valid variant", () =>
        {
            var f = Fixture(); Require(Repair(f.actor, Canonical), "Initial repair failed");
            Require(Repair(f.actor, Canonical), "Repeat repair failed");
            f.app.Appearance = null; Require(Repair(f.actor, Canonical), "Appearance rebuild did not repair");
            var variant = new SlimeAppearance { QubitAppearance = new SlimeAppearance() }; f.app.Appearance = variant;
            Require(Repair(f.actor, Canonical) && ReferenceEquals(f.app.Appearance, variant), "Valid selected appearance overwritten");
        });
        Check("native/non-Quantum actor remains a strict no-op", () =>
        {
            var f = Fixture(); Require(Repair(f.actor, "largo.boom.cotton"), "Unrelated actor should no-op");
            Require(f.app.Appearance == null && f.generator.enabled, "Unrelated actor mutated");
        });
        Check("no generator is a no-op", () => Require(Repair(new GameObject(), Canonical), "Generator-free actor refused"));
        Check("foreign applicator refuses without adopting it", () =>
        {
            var f = Fixture(); var external = new GameObject().AddComponent<SlimeAppearanceApplicator>();
            external.SlimeDefinition = f.app.SlimeDefinition; f.generator.AppearanceApplicator = external;
            Require(!Repair(f.actor, Canonical) && external.Appearance == null && f.app.Appearance == null, "Foreign applicator adopted");
        });
        Check("missing applicator refuses without adding components or disabling generator", () =>
        {
            var f = Fixture(); f.generator.AppearanceApplicator = null;
            Require(!Repair(f.actor, Canonical) && f.generator.AppearanceApplicator == null && f.generator.enabled, "Missing applicator guessed");
        });
        Check("non-owned definition refuses", () =>
        {
            var f = Fixture(); f.app.SlimeDefinition!.name = "NativePinkQuantum";
            Require(!Repair(f.actor, Canonical) && f.app.Appearance == null, "Native definition mutated");
        });
        Check("wrong registered identity refuses", () =>
        {
            var f = Fixture(); f.app.SlimeDefinition!.IdentifiableId = (Identifiable.Id)3;
            Require(!Repair(f.actor, Canonical) && f.app.Appearance == null, "Wrong identity accepted");
        });
        Check("missing qubit appearance refuses partial repair", () =>
        {
            var f = Fixture(); f.owned.QubitAppearance = null;
            Require(!Repair(f.actor, Canonical) && f.app.Appearance == null && f.generator.enabled, "Incomplete appearance installed");
        });
        Check("empty owned default refuses without inventing native assets", () =>
        {
            var f = Fixture(); f.app.SlimeDefinition!.AppearancesDefault = Array.Empty<SlimeAppearance>();
            Require(!Repair(f.actor, Canonical) && f.app.Appearance == null, "Missing default guessed");
        });
        Console.WriteLine($"QUANTUM_APPEARANCE_BINDING_CHECKS={_checks} FAILURES={_failures}");
        return _failures == 0 ? 0 : 1;
    }
}
namespace UnityEngine
{
    public class Component
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public T? GetComponent<T>() where T : class => gameObject.GetComponent<T>();
    }
    public class MonoBehaviour : Component { public bool enabled = true; }
    public sealed class GameObject
    {
        private readonly Dictionary<Type, Component> _components = new();
        public Transform transform = new();
        public T? GetComponent<T>() where T : class => _components.TryGetValue(typeof(T), out var value) ? value as T : null;
        public T AddComponent<T>() where T : Component, new() { var value = new T { gameObject = this }; _components[typeof(T)] = value; return value; }
    }
    public sealed class Transform
    {
        public Transform? parent;
        public bool IsChildOf(Transform root) { for (var t = parent; t != null; t = t.parent) if (ReferenceEquals(t, root)) return true; return false; }
    }
}
public sealed class Identifiable : MonoBehaviour { public enum Id { NONE = 0 } public Id id; }
public sealed class SlimeAppearance { public SlimeAppearance? QubitAppearance; }
public sealed class SlimeDefinition { public string name = ""; public Identifiable.Id IdentifiableId; public SlimeAppearance[]? AppearancesDefault; }
public sealed class SlimeAppearanceApplicator : MonoBehaviour { public SlimeAppearance? Appearance; public SlimeDefinition? SlimeDefinition; }
public sealed class GenerateQuantumQubit : MonoBehaviour { public SlimeAppearanceApplicator? AppearanceApplicator; public GameObject? QubitPrefab; public int generationState = 17; }
