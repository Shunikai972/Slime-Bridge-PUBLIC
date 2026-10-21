// Synthetic regression fixtures; no games, deployments or native save files are exercised.
using System.Reflection;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using SlimeBridge.Shared;
using SlimeBridge.SR2;

internal static class FocusedChecks
{
    private const string Id = "0123456789abcdef0123456789abcdef";
    private const string Name = "sb2_" + Id;
    internal static void Run()
    {
        var tests = new List<(string Name, Action<Scope> Test)>
        {
            ("invalid id", s => { s.Command.CreationId = Id.ToUpperInvariant(); s.Refused(); }),
            ("uninitialized storage", s => { s.Director.StorageProvider!.Initialized = false; s.Refused(); }),
            ("not main menu", s => { SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "World"; s.Refused(); }),
            ("hooks unavailable", s => { HarmonyLib.Harmony.FailPatch = true; s.Refused(); }),
            ("invalid complete summary traversal", s => { s.Occupy(2); s.Director.Summaries["other_0"].IsInvalid = true; s.Refused(); }),
            ("full dynamic slot range", s => { s.Director.SaveSlotCount = 1; s.Occupy(0); s.Refused(); }),
            ("missing detached model coverage", s => { GameSettingsModel.AfterConstruction = m => m.optionsById!.Clear(); s.Refused(); }),
            ("fresh slot recheck selects new vacancy", s =>
            { s.Director.StorageProvider!.OnNames = () => { if (s.Director.StorageProvider.NameReads == 2) s.Occupy(0); };
              s.Begin(); Require(s.Director.Pending!.metadata!.saveSlotIndex == 1); }),
            ("both durable intents precede dispatch", s =>
            { s.Director.OnDispatch = () => Require(s.Journal.HasAny(s.Command.OpId) && s.Journal.HasAny("create:" + Id)); s.Begin(); }),
            ("wait for actual world before acknowledgement", s => { Require(s.Begin().Step() == null && s.Director.SaveCalls == 0); }),
            ("realistic NEW GAME false flag completes once", s =>
            { var c = s.Begin(); s.Complete(); Require(!s.Director.loadedGame && s.Director.newGameMetadata == null); s.Proven(c); c.Step(); Require(s.Director.SaveCalls == 1); }),
            ("separate native phases never flush early", s =>
            {
                var c = s.Begin(); Require(s.Director.Pending!.MoveNext());
                Require(!s.Director.loadedGame && s.Director.newGameMetadata != null && s.Director.SavedGame != null);
                Require(c.Step() == null && s.Director.SaveCalls == 0);
                Fixture.ActivateScene(); Require(c.Step() == null && s.Director.SaveCalls == 0);
                Fixture.ApplySettings(s.Director); Require(c.Step() == null && s.Director.SaveCalls == 0);
                s.Director.newGameMetadata = null; Require(c.Step() == null && s.Director.SaveCalls == 0);
                SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = false; s.Proven(c);
            }),
            ("equivalent reconstructed applied settings use persisted semantics not pointers", s =>
            {
                var c = s.Begin(); s.Complete(); var original = SceneContext.Instance!.GameModel!.gameSettings!;
                var clone = s.CloneSettings(); Require(clone.Pointer != original.Pointer);
                clone.optionsById = clone.optionsById!.Reverse().ToDictionary(pair => pair.Key, pair => pair.Value);
                // Deliberately distinct definition ReferenceId versus persisted serializer keys.
                Require(clone.optionsById!.Keys.All(k => !k.StartsWith("Fixture.Persisted", StringComparison.Ordinal)));
                s.Proven(c);
            }),
            ("wrong applied setting fails before any flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().optionsById!["Fixture.Option"].OptionValueKey = "wrong"; s.FailsClosed(c); }),
            ("incomplete applied settings fail before any flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().optionsById!.Remove("Fixture.Second"); s.FailsClosed(c); }),
            ("extra applied persisted setting fails closed", s =>
            {
                var c = s.Begin(); s.Complete(); var clone = s.CloneSettings();
                clone.optionsById!.Add("extra", new() { optionsItemDefinition = new() { PersistenceKey = "Fixture.Extra" }, OptionValueKey = "fixture-default" });
                s.FailsClosed(c);
            }),
            ("missing applied icon fails before any flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().gameIconDefinition = null; s.FailsClosed(c); }),
            ("same-count wrong persisted key fails before flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().OnSerialized = d => d.OptionItems![0].PersistenceKey = "Fixture.Other"; s.FailsClosed(c); }),
            ("duplicate persisted keys fail before flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().OnSerialized = d => d.OptionItems![1].PersistenceKey = d.OptionItems[0].PersistenceKey; s.FailsClosed(c); }),
            ("missing serialized option payload fails before flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().OnSerialized = d => d.OptionItems = null; s.FailsClosed(c); }),
            ("blank serialized option value fails before flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().OnSerialized = d => d.OptionItems![0].OptionValueKey = " "; s.FailsClosed(c); }),
            ("serializer icon index mismatch fails before flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().OnSerialized = d => d.GameIconId++; s.FailsClosed(c); }),
            ("already issued flush cannot be attempted again", s =>
            { var c = s.Begin(); s.Complete(); typeof(CounterpartCreator).GetField("_saveIssued", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(c, true); s.FailsClosed(c); }),
            ("wrong applied icon fails before any flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().gameIconDefinition = new() { ReferenceId = "Fixture.WrongIcon" }; s.FailsClosed(c); }),
            ("empty applied settings fail before any flush", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().optionsById!.Clear(); s.FailsClosed(c); }),
            ("applied native serializer exception is uncertain and fenced", s =>
            { var c = s.Begin(); s.Complete(); s.CloneSettings().OnPull = () => throw new IOException("fixture serializer failure"); s.FailsClosed(c); }),
            ("native model accessor exception is uncertain and fenced", s =>
            { var c = s.Begin(); s.Complete(); SceneContext.Instance!.OnModelRead = () => throw new IOException("fixture accessor failure"); s.FailsClosed(c); }),
            ("unconsumed naming hook never flushed", s =>
            {
                var c = s.Begin(); s.Complete();
                var association = typeof(CounterpartCreator).GetField("_association", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
                association.GetType().GetField("NameConsumed", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(association, false);
                s.FailsClosed(c);
            }),
            ("wrong live name never flushed", s =>
            { var c = s.Begin(); s.Complete(); s.Director.SavedGame!.GameState!.GameName = "wrong"; s.FailsClosed(c); }),
            ("duplicate pending command cannot dispatch again", s =>
            { s.Begin(); Require(CounterpartCreator.Begin(s.Command, s.Journal, 1, out var r, s.Binding.IsCurrent) == null && r!.Uncertain); Require(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 0); }),
            ("verified creation reconcile/replay is read-only", s =>
            {
                var c = s.Begin(); s.Complete(); s.Proven(c);
                var r = CounterpartCreator.Reconcile(s.Reconcile(), s.Binding.IsCurrent); Require(r.Verified == true && r.Persisted && !r.Uncertain);
                Require(CounterpartCreator.Begin(s.Command, s.Journal, 1, out r, s.Binding.IsCurrent) == null && r!.Verified == true);
                Require(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1 && s.Director.StorageProvider!.Names.Count == 1);
            }),
            ("strict 240 seconds boundary unchanged", s =>
            { var c = s.Begin(); UnityEngine.Time.realtimeSinceStartup = 240; Require(c.Step() == null); UnityEngine.Time.realtimeSinceStartup = 240.01f; s.FailsClosed(c); }),
            ("wrong live slot never flushed", s =>
            { var c = s.Begin(); s.Complete(); s.Director.SavedGame!.GameState!.SaveSlotIndex = 2; Require(c.Step()!.Uncertain && s.Director.SaveCalls == 0); }),
            ("flush exception never retried", s =>
            { var c = s.Begin(); s.Complete(); s.Director.OnSave = () => throw new IOException("fixture failure"); Require(c.Step()!.Uncertain); c.Step(); Require(s.Director.SaveCalls == 1); }),
            ("scope is one-shot and exception-clean", s =>
            { s.Begin(); Require(s.Director.GetGameSaveFileName("a") == "fixture-native-a");
              var state = Enter(s.Director.Pending!); Require(s.Director.GetGameSaveFileName("a") == Name);
              Require(s.Director.GetGameSaveFileName("b") == "fixture-native-b");
              var exception = new IOException("fixture failure"); Require(ReferenceEquals(Leave(state, exception), exception));
              Require(s.Director.GetGameSaveFileName("c") == "fixture-native-c"); }),
            ("wrong metadata nested scope cannot steal name", s =>
            { s.Begin(); var outer = Enter(s.Director.Pending!);
              var inner = Enter(new AutoSaveDirector._LoadNewGame_Coroutine_d__53 { __4__this = s.Director, metadata = new() });
              Require(s.Director.GetGameSaveFileName("nested") == "fixture-native-nested"); Leave(inner);
              Require(s.Director.GetGameSaveFileName("owned") == Name); Leave(outer); }),
            ("timeout reports actual readiness and hook state without retry or flush", s =>
            {
                var c = s.Begin(); UnityEngine.Time.realtimeSinceStartup = 241;
                var r = c.Step()!;
                if (!r.Reason.Contains("directorMatch=True") || !r.Reason.Contains("loaded=False") ||
                    !r.Reason.Contains("saved=False") || !r.Reason.Contains("scene=False") ||
                    !r.Reason.Contains("iterator=False") || !r.Reason.Contains("named=False"))
                    throw new InvalidOperationException("timeout readiness detail missing: " + r.Reason);
                Require(!r.Ok && !r.Persisted && r.Uncertain && r.Verified == false &&
                    s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 0);
                c.Step(); Require(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 0);
            }),
            ("timeout distinguishes loaded inactive player from missing world", s =>
            {
                var c = s.Begin(); s.Complete();
                SceneContext.Instance!.Player!.activeInHierarchy = false;
                UnityEngine.Time.realtimeSinceStartup = 241; var r = c.Step()!;
                Require(r.Reason.Contains("loaded=False") && r.Reason.Contains("saved=True") &&
                    r.Reason.Contains("playerState=True") && r.Reason.Contains("player=True") &&
                    r.Reason.Contains("active=False") && r.Reason.Contains("iterator=True") &&
                    r.Reason.Contains("named=True") && !r.Ok && r.Uncertain && s.Director.SaveCalls == 0);
            }),
            ("timeout retains late association, never redispatch", s =>
            { var c = s.Begin(); UnityEngine.Time.realtimeSinceStartup = 241; Require(c.Step()!.Uncertain);
              Require(CounterpartCreator.Reconcile(s.Reconcile(), s.Binding.IsCurrent).CanCreate == false);
              Require(CounterpartCreator.Begin(s.Command, s.Journal, 1, out var r, s.Binding.IsCurrent) == null && r!.Uncertain && s.Director.DispatchCalls == 1);
              s.Complete(); Require(s.Director.SavedGame!.GetName() == Name && s.Director.SaveCalls == 0); }),
            ("late occupied slot suppresses native iterator", s =>
            { var c = s.Begin(); s.Occupy(0); Require(!s.Director.Pending!.MoveNext()); Require(c.Step()!.Uncertain && s.Director.SaveCalls == 0 && !s.Director.loadedGame); }),
            ("slot occupied after iterator never flushed", s =>
            { var c = s.Begin(); s.Complete(); s.Occupy(0); Require(c.Step()!.Uncertain && s.Director.SaveCalls == 0); }),
            ("intent-only restart fence", s =>
            { s.Journal.WriteIntent("create:" + Id); Require(CounterpartCreator.Begin(s.Command, s.Journal, 1, out var r, s.Binding.IsCurrent) == null && r!.Uncertain && s.Director.DispatchCalls == 0); }),
            ("reconcile absent is read-only", s =>
            { var r = CounterpartCreator.Reconcile(s.Reconcile(), s.Binding.IsCurrent); Require(r.Ok && r.CanCreate == true && r.Verified == false && s.Director.DispatchCalls == 0 && s.Director.SaveCalls == 0); }),
            ("stock definitions avoid boxed native interface enumerator", s =>
            {
                GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.RejectBoxedEnumerator = true;
                var r = CounterpartCreator.Reconcile(s.Reconcile(), s.Binding.IsCurrent);
                if (!r.Ok || r.CanCreate != true)
                    throw new InvalidOperationException("stock native enumeration refused: " + r.Reason);
                Require(r.Verified == false && s.Director.DispatchCalls == 0 && s.Director.SaveCalls == 0);
                s.Begin();
                Require(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 0);
            }),
            ("unsupported native stock collection fails closed", s =>
            { GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.NativeListAvailable = false; s.Refused(); }),
            ("empty native stock snapshot fails closed", s =>
            { GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.Items.Clear(); s.Refused(); }),
            ("excessive native stock snapshot fails closed", s =>
            {
                var items = GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.Items;
                for (var i = 0; i < 4096; i++) items.Add(new());
                s.Refused();
            }),
            ("same-count native stock version change fails closed", s =>
            {
                var items = GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.Items;
                items.OnIndexRead = _ => items._version++;
                s.Refused();
            }),
            ("native stock count change fails closed", s =>
            {
                var items = GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.Items;
                items.OnIndexRead = _ => items.Add(new());
                s.Refused();
            }),
            ("inventory opt-in gate defaults closed", s => { var r = s.Route(false, s.Command); Require(r != null && !r.Ok && s.Director.DispatchCalls == 0); }),
            ("inventory actual reconcile_save dispatch", s => { var r = s.Route(true, s.Reconcile()); Require(r!.Ok && r.CanCreate == true && s.Director.DispatchCalls == 0); }),
            ("inventory reconciler fences journaled intent", s => { s.Journal.WriteIntent("create:" + Id); Require(s.Route(true, s.Reconcile())!.CanCreate == false); }),
            ("inventory native create is pending, not early ACK", s => { Require(s.Route(true, s.Command) == null && s.Director.DispatchCalls == 1); }),
            ("stock persisted value mismatch refuses proof", s =>
            { var c = s.Begin(); s.Complete(); s.Director.OnSave = () => s.Director.SavedGame!.GameState!.GameSettings!.OptionItems![0].OptionValueKey = "wrong"; Require(c.Step()!.Uncertain); }),
        };
        var incomplete = new List<(string Name, Action<Scope> Change)>
        {
            ("newGameMetadata still retained", s => s.Director.newGameMetadata = s.Director.Pending!.metadata),
            ("replaced pending metadata", s => s.Director.newGameMetadata = new()),
            ("existing-save loaded flag", s => s.Director.loadedGame = true),
            ("loader still busy", s => SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = true),
            ("missing loader", s => SystemContext.Instance!.SceneLoader = null),
            ("not gameplay", s => SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenuFromBoot"),
            ("missing SavedGame", s => s.Director.SavedGame = null),
            ("wrong director ownership", s => GameContext.Instance!.AutoSaveDirector = new()),
            ("replaced native GameContext", s => GameContext.Instance = new() { AutoSaveDirector = new() }),
            ("missing SceneContext", s => SceneContext.Instance = null),
            ("missing PlayerState", s => SceneContext.Instance!.PlayerState = null),
            ("missing Player", s => SceneContext.Instance!.Player = null),
            ("inactive Player", s => SceneContext.Instance!.Player!.activeInHierarchy = false),
            ("missing GameModel", s => SceneContext.Instance!.GameModel = null),
            ("missing applied settings", s => SceneContext.Instance!.GameModel!.gameSettings = null),
            ("replaced native scene context with unapplied settings", s => SceneContext.Instance = new() { PlayerState = new(), Player = new(), GameModel = new() }),
        };
        foreach (var predicate in incomplete)
            tests.Add((predicate.Name + " never flushes", s =>
            {
                var c = s.Begin(); s.Complete(); predicate.Change(s);
                var first = c.Step();
                var lostBinding = predicate.Name == "wrong director ownership" || predicate.Name == "replaced native GameContext";
                Require((lostBinding ? first != null && first.Uncertain : first == null) && s.Director.SaveCalls == 0);
                UnityEngine.Time.realtimeSinceStartup = 241; s.FailsClosed(c);
                Require(CounterpartCreator.Begin(s.Command, s.Journal, 1, out var r, s.Binding.IsCurrent) == null && r!.Uncertain && s.Director.DispatchCalls == 1);
            }));
        foreach (var field in new[] { "World", "Player", "Ranch", "Actors", "GameSettings", "GameIconIndex" })
            tests.Add(("missing full payload " + field, s =>
            { var c = s.Begin(); s.Complete(); s.Director.OnSave = () => typeof(Il2CppMonomiPark.SlimeRancher.Persist.GameV06).GetField(field)!.SetValue(s.Director.SavedGame!.GameState, null);
              var r = c.Step()!; Require(!r.Ok && !r.Persisted && r.Uncertain && r.Verified == false && s.Director.SaveCalls == 1); c.Step(); Require(s.Director.SaveCalls == 1); }));
        var failed = 0;
        foreach (var test in tests)
        {
            try { using var scope = new Scope(); test.Test(scope); Console.WriteLine("PASS " + test.Name); }
            catch (Exception ex) { failed++; Console.WriteLine("FAIL " + test.Name + ": " + ex); }
        }
        Console.WriteLine($"{tests.Count - failed}/{tests.Count} focused checks passed (synthetic)");
        if (failed != 0) throw new Exception("focused harness failed");
    }
    private static object? Enter(AutoSaveDirector._LoadNewGame_Coroutine_d__53 iterator)
    { var args = new object?[] { iterator, null, false }; Require((bool)HarmonyLib.Harmony.Hooks["MoveNext"].Prefix!.Method.Invoke(null, args)!); return args[1]; }
    private static object? Leave(object? state, Exception? error = null) => HarmonyLib.Harmony.Hooks["MoveNext"].Finalizer!.Method.Invoke(null, new[] { state, error });
    private static void Require(bool condition) { if (!condition) throw new InvalidOperationException("assertion failed"); }
    private sealed class Scope : IDisposable
    {
        internal readonly AutoSaveDirector Director = Fixture.Reset();
        private readonly string _path = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "sr2-counterpart-fixture-" + Guid.NewGuid().ToString("N") + ".journal");
        internal readonly OpJournal Journal;
        internal readonly NativeStorageBinding Binding;
        internal readonly BridgeCommand Command = new() { OpId = "create-test", Kind = "create_save", CreationId = Id };
        internal Scope()
        {
            File.WriteAllText(_path, ""); Journal = OpJournal.Open(_path, out var error) ?? throw new Exception(error);
            Binding = new NativeStorageBinding(StorageBindingChecks.Token, Environment.ProcessPath!, _path, Environment.ProcessId,
                _ => { }, () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), () => 0);
            Binding.Tick();
        }
        internal BridgeCommand Reconcile() => new() { OpId = "reconcile-test", Kind = "reconcile_save", CreationId = Id };
        internal CounterpartCreator Begin() { var c = CounterpartCreator.Begin(Command, Journal, Environment.ProcessId, out var r, Binding.IsCurrent); Require(c != null && r == null); return c!; }
        internal void Complete() { Require(Director.Pending!.MoveNext()); Fixture.CompleteNewGame(Director); }
        internal void Proven(CounterpartCreator creator)
        {
            var r = creator.Step();
            Require(r != null && r.Ok && r.Persisted && r.Verified == true && r.Applied && !r.Uncertain &&
                r.SaveId == Name && r.SaveFile == Name + "_0" && r.Sha256.Length == 64 && Director.SaveCalls == 1 && Director.DispatchCalls == 1);
        }
        internal void FailsClosed(CounterpartCreator creator)
        {
            var r = creator.Step(); Require(r != null && !r.Ok && !r.Persisted && r.Verified == false && r.Uncertain && Director.SaveCalls == 0);
            creator.Step(); Require(Director.SaveCalls == 0 && Director.DispatchCalls == 1 && Journal.HasAny(Command.OpId) && Journal.HasAny("create:" + Id));
        }
        internal GameSettingsModel CloneSettings()
        {
            var old = SceneContext.Instance!.GameModel!.gameSettings!;
            var clone = new GameSettingsModel(GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!);
            clone.SetGameIconForNewGame(old.gameIconDefinition!);
            foreach (var item in old.optionsById!) clone.optionsById![item.Key].OptionValueKey = item.Value.OptionValueKey;
            SceneContext.Instance.GameModel.gameSettings = clone; return clone;
        }
        internal void Refused() { Require(CounterpartCreator.Begin(Command, Journal, 1, out var r, Binding.IsCurrent) == null && r != null && !r.Ok && Director.DispatchCalls == 0 && Director.SaveCalls == 0 && !Journal.HasAny(Command.OpId)); }
        internal void Occupy(int slot) => Fixture.Add(Director, new() { GameName = "other", SaveSlotIndex = slot, DisplayName = Director.GetGameSaveDisplayName(slot) }, "other_0");
        internal CommandResult? Route(bool enabled, BridgeCommand command)
        {
            var agent = (InventoryAgent)Activator.CreateInstance(typeof(InventoryAgent), BindingFlags.Instance | BindingFlags.NonPublic,
                null, new object?[] { "http://127.0.0.1:1", "fixture-token", false, Journal, (Action<string>)(_ => { }), enabled, (Func<AutoSaveDirector, bool>)Binding.IsCurrent }, null)!;
            return (CommandResult?)typeof(InventoryAgent).GetMethod("ExecuteCreation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(agent, new object[] { command });
        }
        public void Dispose() => File.Delete(_path);
    }
}
