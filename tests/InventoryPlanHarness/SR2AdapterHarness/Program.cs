// Executable regression checks of the actual linked adapter against SYNTHETIC API fixtures.
// No game DLL is loaded or executed. Synthetic payloads are not native persistence proof.
using System.Reflection;
using SlimeBridge.SR2;
using SlimeBridge.Shared;

internal static class Program
{
    private static int Main()
    {
        var tests = new (string Name, Action Test)[]
        {
            ("count is echoed on world-not-ready refusal", () =>
            {
                Il2Cpp.GameContext.Instance = null; Il2Cpp.SceneContext.Instance = null;
                var result = Invoke(Create(null), new BridgeCommand { OpId = "count-refusal", Kind = "add", Item = "slime.pink", Count = 7, SaveId = "fixture_save" });
                Require(!result.Ok && result.Count == 7, "refusal lost requested count");
            }),
            ("missing save binding refuses before native mutation", () => BindingRefusal(c => c.SaveId = null)),
            ("empty save binding refuses before native mutation", () => BindingRefusal(c => c.SaveId = "")),
            ("invalid save binding refuses before native mutation", () => BindingRefusal(c => c.SaveId = "fixture/save")),
            ("loaded saved-game identity mismatch refuses", () => WorldRefusal(w => w.Director._savedGame!.Name = "different")),
            ("loaded saved-game missing refuses", () => WorldRefusal(w => w.Director._savedGame = null)),
            ("scene player missing refuses", () => WorldRefusal(w => w.Scene.Player = null)),
            ("dead scene player refuses", () => WorldRefusal(w => w.Scene.PlayerState!.Health = 0)),
            ("creation kinds refuse without native save or mutation", () =>
            {
                foreach (var kind in new[] { "create_save", "reconcile_save" })
                {
                    using var world = new FixtureWorld((0, 10, true, true));
                    var result = world.Run(world.Command(kind, 7));
                    Require(!result.Ok && !result.Applied && !result.Uncertain && result.Count == 7 &&
                        world.Ammo.Mutations.Count == 0 && world.Director.SaveCalls == 0 && world.JournalBytes().Length == 0,
                        "creation reached inventory or journal path");
                }
            }),
        };
        var all = tests.Concat(MutationChecks.All()).Concat(PersistenceChecks.All()).ToArray();
        var failures = 0;
        Console.WriteLine("SYNTHETIC SR2 adapter harness; not native runtime/save proof.");
        foreach (var test in all)
        {
            try { test.Test(); Console.WriteLine("PASS " + test.Name); }
            catch (Exception ex) { failures++; Console.WriteLine("FAIL " + test.Name + ": " + ex.Message); }
        }
        Console.WriteLine($"{all.Length - failures}/{all.Length} checks passed");
        return failures == 0 ? 0 : 1;
    }
    private static void BindingRefusal(Action<BridgeCommand> alter)
    {
        using var world = new FixtureWorld((0, 10, true, true));
        var command = world.Command(count: 7); alter(command);
        var result = world.Run(command);
        Require(!result.Ok && !result.Applied && !result.Uncertain && result.Count == 7 &&
            world.Ammo.Mutations.Count == 0 && world.Director.SaveCalls == 0 && !world.HasIntent,
            "unsafe save binding reached mutation");
    }
    private static void WorldRefusal(Action<FixtureWorld> alter)
    {
        using var world = new FixtureWorld((0, 10, true, true)); alter(world);
        var result = world.Run(world.Command(count: 7));
        Require(!result.Ok && !result.Applied && !result.Uncertain && result.Count == 7 &&
            world.Ammo.Mutations.Count == 0 && world.Director.SaveCalls == 0 && !world.HasIntent,
            "unready loaded world reached mutation");
    }
    internal static InventoryAgent Create(OpJournal? journal, bool lab = false) =>
        (InventoryAgent)Activator.CreateInstance(typeof(InventoryAgent), BindingFlags.Instance | BindingFlags.NonPublic,
            null, new object?[] { "http://127.0.0.1:1", "synthetic-token", lab, journal, (Action<string>)(_ => { }) }, null)!;
    internal static CommandResult Invoke(InventoryAgent agent, BridgeCommand command)
    {
        try { return (CommandResult)typeof(InventoryAgent).GetMethod("Execute", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(agent, new object[] { command })!; }
        catch (TargetInvocationException ex) { throw ex.InnerException!; }
    }
    internal static void Require(bool condition, string message)
    { if (!condition) throw new InvalidOperationException(message); }
}
