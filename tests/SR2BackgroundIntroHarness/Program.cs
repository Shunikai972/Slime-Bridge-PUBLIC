using System.Reflection;
using SlimeBridge.Shared;
using SlimeBridge.SR2;
using Il2CppMonomiPark.SlimeRancher.UI.IntroSequence;

internal static class Program
{
    static int _checks, _failures;
    internal static void Check(bool condition, string reason)
    { _checks++; if (!condition) { _failures++; Console.WriteLine("FAIL: " + reason); } }
    internal static void Set(object target, string field, object? value) => target.GetType().GetField(field, BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(target, value);
    internal static object? Get(object target, string field) => target.GetType().GetField(field, BindingFlags.NonPublic | BindingFlags.Instance)?.GetValue(target);
    internal static object? Invoke(object target, string method, params object?[] args) => target.GetType().GetMethod(method, BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(target, args);
    static void Main(string[] args)
    {
        OwnedNativeFlow();
        if (args.Contains("seams")) SeamChecks.Run();
        else if (args.Contains("followups")) FollowUpChecks.Run();
        else if (!args.Contains("flow")) { NegativeChecks.Run(); SeamChecks.Run(); FollowUpChecks.Run(); }
        Console.WriteLine($"intro checks={_checks} intro failures={_failures} (synthetic; no IL2CPP/game runtime proof)");
        if (_failures != 0) Environment.Exit(1);
    }
    static void OwnedNativeFlow()
    {
        using var world = new World();
        world.Begin(); world.Activate(); world.Complete();
        var root = world.Root;
        // A stalled result POST cannot stall ordinary main-thread intro progression.
        world.Tick(posting: true);
        Check(root.Helpers == 1 && root._frameIndex == 0 && root._frameConfigs![0].TextPanelView!.Interrupts == 1,
            "owned background NEWGAME must interrupt typewriter once on the update after verified completion, even while posting");
        if (root.Helpers == 0) return; // Baseline RED is a behavioral assertion, not a missing-type error.
        world.Tick(posting: true);
        Check(root.Helpers == 2 && root.EndFrames == 1 && root.StartFrames == 1 && root._frameIndex == 1 && !root._isInputEnabled && root._currentTween != null,
            "following update advances nonfinal frame through native cleanup and enters native transition");
        world.Tick(posting: true);
        Check(root.Helpers == 2, "transition input gate prohibits helper invocation");
        root.FinishCameraTransition(); world.Tick(posting: true);
        Check(root.Helpers == 2, "camera completion still waits for native delayed text bind");
        root.BindTextAfterWait(); world.Tick(posting: true);
        Check(root.Helpers == 3 && root._frameConfigs![1].TextPanelView!.Interrupts == 1 && root._frameIndex == 1,
            "native bind enables final frame typewriter interruption without advancing");
        world.Tick(posting: true);
        Check(root.Helpers == 4 && root.EndFrames == 2 && root.BackgroundStops == 2 && root._frameIndex == 2 && !root._isInputEnabled && root.MusicDequeues == 1 && root.EndCoroutines == 1,
            "final normal branch performs indicator/background/music cleanup and one end coroutine");
        world.Tick(posting: true); world.Tick(posting: true);
        Check(root.Helpers == 4 && root.EndCoroutines == 1 && root.Closes == 0, "wait for native end coroutine, never force close or duplicate ending");
        root.FinishNativeEndWait(); world.Tick(posting: true);
        Check(root.Closes == 1 && root.Destroys == 1 && Il2Cpp.GameContext.Instance!.UIDirector!.Pops == 1 && root.CameraEnabled && root.Frozen,
            "root clear/pop precedes later gameplay control-release callback");
        Check(!world.Log.Any(m => m.Contains("intro complete", StringComparison.OrdinalIgnoreCase) || m.Contains("controls restored", StringComparison.OrdinalIgnoreCase)),
            "adapter must not claim gameplay completion from root-clear");
        root.FinishGameplayFade(); world.Tick(posting: true);
        Check(root.Helpers == 4 && !root.Frozen, "native gameplay fade alone releases controls; retired helper issues no more calls");
    }
    internal sealed class World : IDisposable
    {
        internal const string Id = "0123456789abcdef0123456789abcdef";
        internal readonly Il2Cpp.AutoSaveDirector Director;
        internal readonly InventoryAgent Agent;
        internal readonly OpJournal Journal;
        internal readonly string JournalPath;
        internal readonly List<string> Log = new();
        internal readonly BridgeCommand Command = new() { OpId = "create-intro", Kind = "create_save", CreationId = Id };
        internal readonly IntroSequenceUIRoot Root = new();
        internal Func<bool> Focus = () => true;
        internal bool StorageCurrent = true;
        internal Action? OnStorage; // Callback-capable storage-guard seam (synthetic).
        internal World(bool lab = true, bool journal = true, bool defaultFocus = false)
        {
            Director = Fixture.Reset();
            JournalPath = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "intro-fixture-" + Guid.NewGuid().ToString("N") + ".journal");
            Journal = OpJournal.Open(JournalPath, out var error) ?? throw new Exception(error);
            var constructors = typeof(InventoryAgent).GetConstructors(BindingFlags.NonPublic | BindingFlags.Instance);
            var ctor = constructors.OrderByDescending(c => c.GetParameters().Length).First();
            var args = new List<object?> { "http://127.0.0.1:1", "synthetic-not-a-credential", false, journal ? Journal : null, (Action<string>)(m => Log.Add(m)), lab,
                (Func<Il2Cpp.AutoSaveDirector, bool>)(d => { OnStorage?.Invoke(); return StorageCurrent && d.Pointer == Director.Pointer; }) };
            if (ctor.GetParameters().Length == 8) args.Add(defaultFocus ? null : (Func<bool>)(() => Focus()));
            Agent = (InventoryAgent)ctor.Invoke(args.ToArray());
            Set(Agent, "_nextPoll", float.PositiveInfinity);
        }
        internal CommandResult? Begin()
        {
            var immediate = (CommandResult?)Invoke(Agent, "ExecuteCreation", Command);
            Check(immediate == null && Get(Agent, "_creation") != null && Director.DispatchCalls == 1 && Journal.HasIntentOnly(Command.OpId) && Journal.HasIntentOnly("create:" + Id),
                "test prerequisite: protected ExecuteCreation must return a pending, doubly journaled native CREATE");
            return immediate;
        }
        internal void Activate()
        {
            Root.Frozen = true;
            Director.Pending!.MoveNext(); Fixture.CompleteNewGame(Director);
            Il2Cpp.SceneContext.Instance!.CutsceneDirector = new() { _uiRoot = Root };
            Il2Cpp.GameContext.Instance!.UIDirector = new() { TopStackableUI = Root };
        }
        internal void Complete()
        {
            Tick(posting: false);
            Check(Get(Agent, "_creation") == null && Director.SaveCalls == 1 && Journal.TryGetDone(Command.OpId, out var receipt) && receipt.Contains("\"verified\":true"),
                "test prerequisite: actual creation Tick performs one native flush, strict full readback, current owned witness and journal DONE");
        }
        internal void Tick(bool posting = true)
        { Set(Agent, "_posting", posting ? 1 : 0); UnityEngine.Time.frameCount++; Agent.Tick(); }
        public void Dispose() { InvokeIfPresent(Agent, "CancelBackgroundIntro"); File.Delete(JournalPath); }
        static void InvokeIfPresent(object target, string method) => target.GetType().GetMethod(method, BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance)?.Invoke(target, null);
    }
}
