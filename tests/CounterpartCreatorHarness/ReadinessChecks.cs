#if READINESS_SNAPSHOT
// Authored synthetic execution of production sources, never native/world proof.
using System.Reflection;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using SlimeBridge.Shared;
using SlimeBridge.SR2;

internal static class ReadinessChecks
{
    const string Id = "11111111111141118111111111111111";
    static string Name => CounterpartCreator.GameNameFor(Id);
    static readonly BindingFlags Instance = BindingFlags.NonPublic | BindingFlags.Instance;
    static readonly BindingFlags Static = BindingFlags.NonPublic | BindingFlags.Static;
    static int checks, failures;
    static void Check(bool ok, string label)
    { checks++; if (!ok) failures++; Console.WriteLine((ok ? "PASS " : "BEHAVIORAL RED ") + label); }
    static void Require(bool ok, string label) { if (!ok) throw new Exception("fixture prerequisite: " + label); }
    static int Main(string[] args)
    {
        try
        {
            Console.WriteLine("SYNTHETIC ONLY: linked creator/query/storage; verbatim production heartbeat body");
            if (args.Length > 0 && args[0] == "cache") CacheAuthority();
            else
            {
                Vertical();
                if (args.Length == 0 || args[0] != "vertical") { CacheAuthority(); Controls(); Invalidations(); LifecycleAndExceptions(); TransitionsAndBoundaries(); CoherentReadings(); }
            }
            Console.WriteLine($"readiness checks={checks} failures={failures}; readiness failures={failures}");
            return failures == 0 ? 0 : 1;
        }
        catch (Exception ex) { Console.WriteLine("HARNESS ERROR (not behavioral RED): " + ex); return 2; }
    }
    static void Vertical()
    {
        using var s = new Scope(); s.Complete();
        Check(Query(Name).Ok, "owned verified NEWGAME query with loadedGame=false");
        var snapshot = Snapshot();
        Check(snapshot != null && Get<string>(snapshot, "save_id") == Name && Get<bool>(snapshot, "player_alive") &&
            Get<int?>(snapshot, "pink_slime_count") == 0 && Get<int?>(snapshot, "pink_plort_count") == 0,
            "owned verified NEWGAME heartbeat with real zero counts");
        Check(!s.Director.loadedGame && s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1 && Fixture.Loads == 1,
            "observations preserve false flag, one CREATE/flush/full readback");
    }
    static void CacheAuthority()
    {
        foreach (var cache in new string?[] { null, "previous_loaded_save" })
        {
            using var s = new Scope(); s.Director._currentGameName = cache;
            s.Begin();
            var previous = s.Director.SavedGame!.GameState;
            var native = s.Director.SavedGame.Pointer;
            Check(!Query(Name).Ok && Snapshot() == null, "cache=" + (cache ?? "null") + " cannot authorize before completion");
            var result = s.Creator!.Step();
            Check(result != null && result.Ok && result.Applied && result.Persisted && result.Verified == true && !result.Uncertain,
                "cache=" + (cache ?? "null") + " completes verified owned NEWGAME");
            Check(s.Director._currentGameName == cache && !s.Director.loadedGame,
                "native NEWGAME and flush do not populate or force load cache");
            Check(s.Director.SavedGame.Pointer == native && !ReferenceEquals(s.Director.SavedGame.GameState, previous),
                "normal flush replaces nested GameState, not native SavedGame");
            Check(Query(Name).Ok, "cache=" + (cache ?? "null") + " owned query uses exact live SavedGame name");
            Check(Snapshot() is { } snapshot && Get<string>(snapshot, "save_id") == Name,
                "cache=" + (cache ?? "null") + " owned heartbeat uses same live native identity");
            Check(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1 && Fixture.Loads == 1,
                "native-faithful cache case preserves one CREATE/flush/full readback");
        }
    }
    static void Controls()
    {
        var director = Fixture.Reset(); director.loadedGame = true;
        director._currentGameName = "existing_loadsave";
        director.SavedGame = new() { GameState = new() { GameName = "existing_loadsave" } };
        Fixture.ActivateScene(); Configure();
        Check(Query("existing_loadsave").Ok, "existing LOADSAVE query without witness");
        Check(Snapshot() != null, "existing LOADSAVE heartbeat without witness");
        Check(!Query("different_save").Ok, "exact save_id mismatch refused");
        director._currentGameName = null;
        Check(!Query("existing_loadsave").Ok && Snapshot() == null, "LOADSAVE null cache does not fall back to SavedGame name");
        director._currentGameName = "stale_loadsave";
        Check(!Query("existing_loadsave").Ok && !Query("stale_loadsave").Ok && Snapshot() == null,
            "LOADSAVE stale cache/live native name mismatch remains refused");
        director._currentGameName = "existing_loadsave";
        Check(Query("existing_loadsave").Ok && Snapshot() != null, "LOADSAVE matching cache/live identity resumes without owned witness");
        director = Fixture.Reset(); director.SavedGame = new() { GameState = new() { GameName = Name } };
        Fixture.ActivateScene(); Configure();
        Check(!Query(Name).Ok && Snapshot() == null, "unowned false flag never authorizes world");
    }
    static void Invalidations()
    {
        Permanent("director replacement", s => GameContext.Instance!.AutoSaveDirector = new() { SavedGame = s.Director.SavedGame },
            s => GameContext.Instance!.AutoSaveDirector = s.Director);
        Permanent("same-name native SavedGame replacement", s => { s.Director.SavedGame = new() { GameState = s.Director.SavedGame!.GameState }; },
            s => s.Director.SavedGame = saved);
        Permanent("slot replacement", s => s.Director.SavedGame!.GameState!.SaveSlotIndex++, s => s.Director.SavedGame!.GameState!.SaveSlotIndex--);
        Permanent("name replacement", s => s.Director.SavedGame!.GameState!.GameName = "different_name", s => s.Director.SavedGame!.GameState!.GameName = Name);
        Permanent("empty invalid native name", s => s.Director.SavedGame!.GameState!.GameName = "", s => s.Director.SavedGame!.GameState!.GameName = Name);
        Permanent("newGameMetadata reappears", s => s.Director.newGameMetadata = new(), s => s.Director.newGameMetadata = null);
        Permanent("stock option changes", s => model!.optionsById!.Values.First().OptionValueKey = "changed", s => model!.optionsById!.Values.First().OptionValueKey = "fixture-default");
        Permanent("stock icon changes", s => model!.gameIconDefinition!.ReferenceId = "Changed.Icon", s => model!.gameIconDefinition!.ReferenceId = "Fixture.Icon");
        Permanent("stock missing option", s => model!.optionsById!.Remove("Fixture.Option"), s => model!.optionsById!.Add("Fixture.Option", option!));
        Permanent("stock extra option", s => model!.optionsById!.Add("extra", new() { optionsItemDefinition = new() { PersistenceKey = "extra" } }), s => model!.optionsById!.Remove("extra"));
        Permanent("stock duplicate persisted key", s => model!.OnSerialized = data => data.OptionItems!.Add(data.OptionItems[0]), s => model!.OnSerialized = null);
        Permanent("stock serializer exception", s => model!.OnPull = () => throw new InvalidOperationException("synthetic serializer"), s => model!.OnPull = null);
        Permanent("menu observed", s => SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenu", s => SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "FixtureWorld");
        Permanent("storage root lost", s => ((FileStorageProvider)s.Director.StorageProvider!).Root = "", s => ((FileStorageProvider)s.Director.StorageProvider!).Root = root!);
        Permanent("provider replaced", s => s.Director.StorageProvider = new FileStorageProvider(), s => s.Director.StorageProvider = provider);
        Permanent("storage guard false", s => s.GuardDenied = true, s => s.GuardDenied = false);
        Permanent("storage guard exception", s => s.GuardThrows = true, s => s.GuardThrows = false);
        Permanent("null game observed", s => GameContext.Instance = null, s => GameContext.Instance = game);
        Permanent("null director observed", s => GameContext.Instance!.AutoSaveDirector = null, s => GameContext.Instance!.AutoSaveDirector = s.Director);
        Permanent("null native save observed", s => s.Director.SavedGame = null, s => s.Director.SavedGame = saved);
        Permanent("zero native save pointer", s => saved!.Pointer = IntPtr.Zero, s => saved!.Pointer = pointer);
        // Observing identity loss even during a load must not preserve a revivable witness.
        Permanent("loading plus name replacement", s => { SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = true; saved!.GameState!.GameName = "during_load"; },
            s => { SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = false; saved!.GameState!.GameName = Name; });
    }
    static SavedGame? saved;
    static GameContext? game;
    static GameSettingsModel? model;
    static Il2CppMonomiPark.SlimeRancher.DataModel.OptionsItemModel? option;
    static StorageProvider? provider;
    static string? root;
    static IntPtr pointer;
    static void Permanent(string label, Action<Scope> change, Action<Scope> restore)
    {
        using var s = new Scope(); s.Complete();
        saved = s.Director.SavedGame; pointer = saved!.Pointer; game = GameContext.Instance;
        model = SceneContext.Instance!.GameModel!.gameSettings; option = model!.optionsById!["Fixture.Option"];
        provider = s.Director.StorageProvider; root = ((FileStorageProvider)provider!).Root;
        change(s);
        Check(!Query(Name).Ok && Snapshot() == null, label + " refuses current gameplay");
        restore(s);
        Check(!Query(Name).Ok && Snapshot() == null, label + " cannot rearm after restoration");
        Check(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1 && Fixture.Loads == 1, label + " no repeated CREATE/flush/payload");
    }
    static void LifecycleAndExceptions()
    {
        using (var s = new Scope())
        {
            s.Complete();
            GameContext.Instance!.LookupDirector!.OnLookup = () => throw new InvalidOperationException("synthetic lookup");
            Check(!Query(Name).Ok, "query catches read-only lookup exception");
            GameContext.Instance.LookupDirector.OnLookup = null;
            Check(!Query(Name).Ok && Snapshot() == null, "query exception permanently drops witness");
        }
        using (var s = new Scope())
        {
            s.Complete();
            GameContext.Instance!.LookupDirector!.OnLookup = () => throw new InvalidOperationException("synthetic lookup");
            Check(Snapshot() == null, "heartbeat catches read-only lookup exception");
            GameContext.Instance.LookupDirector.OnLookup = null;
            Check(!Query(Name).Ok && Snapshot() == null, "heartbeat exception permanently drops witness");
        }
        using (var s = new Scope())
        {
            s.Complete();
            new BridgeMod().OnDeinitializeMelon();
            Check(!Query(Name).Ok && Snapshot() == null, "actual shutdown body drops owned witness");
            Check(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1, "shutdown cannot dispatch or flush again");
        }
    }
    static void TransitionsAndBoundaries()
    {
        using (var s = new Scope())
        {
            s.Complete();
            var scene = SceneContext.Instance!; var state = scene.PlayerState!; var player = scene.Player!;
            SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = true;
            Check(!Query(Name).Ok && Snapshot() == null, "loading temporarily suppresses both observations");
            SystemContext.Instance.SceneLoader.IsSceneLoadInProgress = false;
            Check(Query(Name).Ok && Snapshot() != null, "idle same owned world resumes");
            SceneContext.Instance = null;
            Check(!Query(Name).Ok && Snapshot() == null, "null scene cannot reuse stale gameplay");
            SceneContext.Instance = scene;
            scene.Player = null;
            Check(!Query(Name).Ok && Snapshot() == null, "null player suppresses gameplay"); scene.Player = player;
            scene.PlayerState = null;
            Check(!Query(Name).Ok && Snapshot() == null, "null playerstate suppresses gameplay"); scene.PlayerState = state;
            player.activeInHierarchy = false;
            Check(!Query(Name).Ok && Snapshot() == null, "inactive player suppresses gameplay"); player.activeInHierarchy = true;
            state.Health = 0;
            Check(!Query(Name).Ok && Snapshot() == null, "dead player suppresses gameplay"); state.Health = 100;
            var reconstructed = new GameSettingsModel(GameContext.Instance!.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!);
            reconstructed.SetGameIconForNewGame(new() { ReferenceId = "Fixture.Icon" });
            SceneContext.Instance = new() { Player = new(), PlayerState = new() { Ammo = new() }, GameModel = new() { gameSettings = reconstructed } };
            Check(Query(Name).Ok && Snapshot() != null, "fresh scene/player with equivalent reconstructed settings resumes");
            var alias = new SavedGame { Pointer = s.Director.SavedGame!.Pointer, GameState = s.Director.SavedGame.GameState };
            s.Director.SavedGame = alias;
            var directorAlias = new AutoSaveDirector { Pointer = s.Director.Pointer, SavedGame = alias, StorageProvider = s.Director.StorageProvider };
            GameContext.Instance.AutoSaveDirector = directorAlias;
            Check(Query(Name).Ok && Snapshot() != null, "distinct wrappers with same native save/director identity accepted");
            for (var i = 0; i < 8; i++) Check(Query(Name).Ok && Snapshot() != null, "repeated read-only observation " + i);
            Check(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1 && Fixture.Loads == 1 && !directorAlias.loadedGame,
                "aliases and observations never CREATE/flush/full payload again");
            GameContext.Instance.LookupDirector = null;
            var omitted = Snapshot();
            Check(omitted != null && Get<int?>(omitted, "pink_slime_count") == null && Get<int?>(omitted, "pink_plort_count") == null && !Query(Name).Ok,
                "missing lookup omits counts, never invents zero");
            Configure(); SceneContext.Instance.PlayerState!.Ammo = null; omitted = Snapshot();
            Check(omitted != null && Get<int?>(omitted, "pink_slime_count") == null && Get<int?>(omitted, "pink_plort_count") == null && !Query(Name).Ok,
                "missing ammo omits counts, query refuses");
        }
        using (var s = new Scope())
        {
            s.Begin();
            Check(!Query(Name).Ok && Snapshot() == null, "name metadata/player/callback alone not completion authority");
            s.Director.OnSave = () => throw new InvalidOperationException("synthetic failed flush");
            var failed = s.Creator!.Step();
            Check(failed != null && failed.Uncertain && !failed.Ok && !Query(Name).Ok && Snapshot() == null,
                "uncertain failed flush cannot mint witness");
        }
        using (var s = new Scope())
        {
            s.Begin(); s.Director.OnSave = () => s.Director.SavedGame = new() { GameState = s.Director.SavedGame!.GameState };
            var failed = s.Creator!.Step();
            Check(failed != null && failed.Uncertain && !failed.Ok && !Query(Name).Ok && Snapshot() == null,
                "same-name save swap during flush never mints proof for replacement world");
        }
        using (var s = new Scope())
        {
            s.Begin(); s.Director.OnSave = () => s.Director.SavedGame!.GameState!.World = null;
            var failed = s.Creator!.Step();
            Check(failed != null && failed.Uncertain && !failed.Ok && !Query(Name).Ok && Snapshot() == null,
                "incomplete detached full readback cannot mint witness");
        }
        using (var s = new Scope())
        {
            s.Complete(); CounterpartCreator.ClearWorldReadiness();
            var reconcile = CounterpartCreator.Reconcile(new() { Kind = "create_save", CreationId = Id, OpId = "readiness-reconcile" }, s.Binding.IsCurrent);
            Check(reconcile.Ok && reconcile.Verified == true && !Query(Name).Ok && Snapshot() == null,
                "successful read-only reconcile cannot mint volatile owned completion");
            var begin = CounterpartCreator.Begin(new() { Kind = "create_save", CreationId = Id, OpId = "readiness-already-exists" }, s.Journal,
                Environment.ProcessId, out var immediate, s.Binding.IsCurrent);
            Check(begin == null && immediate != null && immediate.Ok && !Query(Name).Ok && Snapshot() == null,
                "already-exists Begin result cannot mint owned world witness");
            Check(s.Director.DispatchCalls == 1 && s.Director.SaveCalls == 1, "reconcile/already-exists preserves issued CREATE fence");
        }
    }
    static void CoherentReadings()
    {
        using (var s = new Scope())
        {
            s.Begin();
            var stock = SceneContext.Instance!.GameModel!.gameSettings!;
            s.Director.OnSave = () => stock.OnPull = () => s.Director.SavedGame = new() { GameState = s.Director.SavedGame!.GameState };
            var failed = s.Creator!.Step();
            Check(failed != null && failed.Uncertain && !failed.Ok, "save replacement in final settings read cannot mint completion");
        }
        using (var s = new Scope())
        {
            s.Begin();
            var stock = SceneContext.Instance!.GameModel!.gameSettings!;
            s.Director.OnSave = () => stock.OnPull = () => SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenu";
            var failed = s.Creator!.Step();
            Check(failed != null && failed.Uncertain && !failed.Ok, "menu entered in final settings read cannot mint completion");
        }
        using (var s = new Scope())
        {
            s.Complete();
            var stock = SceneContext.Instance!.GameModel!.gameSettings!;
            stock.OnPull = () => s.Director.SavedGame!.GameState!.GameName = "changed_inside_read";
            Check(!Query(Name).Ok && Snapshot() == null, "name changed inside read refuses coherent query/heartbeat");
            stock.OnPull = null; s.Director.SavedGame!.GameState!.GameName = Name;
            Check(!Query(Name).Ok && Snapshot() == null, "name changed inside read cannot rearm after restoration");
        }
        using (var s = new Scope())
        {
            s.Complete();
            var original = SceneContext.Instance!; var stock = original.GameModel!.gameSettings!;
            stock.OnPull = () => SceneContext.Instance = new() { Player = new(), PlayerState = new() { Ammo = new() }, GameModel = original.GameModel };
            Check(!Query(Name).Ok, "scene switched inside settings read cannot use captured stale player");
            stock.OnPull = null;
            Check(Query(Name).Ok && Snapshot() != null, "fresh replaced scene is revalidated on next observation");
        }
        using (var s = new Scope())
        {
            s.Complete();
            var stock = SceneContext.Instance!.GameModel!.gameSettings!;
            stock.OnPull = () => SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenu";
            Check(!Query(Name).Ok && Snapshot() == null, "menu observed inside serializer suppresses gameplay");
            stock.OnPull = null; SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "FixtureWorld";
            Check(!Query(Name).Ok && Snapshot() == null, "menu observed inside serializer permanently drops witness");
        }
        using (var s = new Scope())
        {
            s.Complete();
            var stock = SceneContext.Instance!.GameModel!.gameSettings!;
            var file = (FileStorageProvider)s.Director.StorageProvider!; var original = file.Root;
            stock.OnPull = () => file.Root = "";
            Check(!Query(Name).Ok && Snapshot() == null, "storage lost inside serializer refuses same observation");
            stock.OnPull = null; file.Root = original;
            Check(!Query(Name).Ok && Snapshot() == null, "storage lost inside serializer cannot rearm");
        }
        using (var s = new Scope())
        {
            s.Complete(); var current = SceneContext.Instance!; var native = current.Pointer; current.Pointer = IntPtr.Zero;
            Check(!Query(Name).Ok && Snapshot() == null, "zero native scene identity suppresses stale gameplay");
            current.Pointer = native;
            Check(Query(Name).Ok && Snapshot() != null, "valid current scene resumes same owned world");
        }
        using (var s = new Scope())
        {
            s.Complete();
            var original = SceneContext.Instance!;
            SceneContext.Instance = new() { Pointer = original.Pointer, Player = original.Player, PlayerState = original.PlayerState, GameModel = original.GameModel };
            Check(Query(Name).Ok && Snapshot() != null, "same native scene through distinct wrapper remains valid");
        }
    }
    static void Configure()
    {
        var lookup = new LookupDirector(); lookup.Items.Add(BridgeMod.PinkSlimeRef, new() { ReferenceId = BridgeMod.PinkSlimeRef }); lookup.Items.Add(BridgeMod.PinkPlortRef, new() { ReferenceId = BridgeMod.PinkPlortRef });
        GameContext.Instance!.LookupDirector = lookup; SceneContext.Instance!.PlayerState!.Ammo = new();
    }
    static CommandResult Query(string name)
    {
        var agent = (InventoryAgent)Activator.CreateInstance(typeof(InventoryAgent), Instance, null,
            new object?[] { "http://127.0.0.1:1/not-used", "synthetic-only", false, null, (Action<string>)(_ => { }), false, null }, null)!;
        var command = BridgeCommand.Parse("{\"op_id\":\"readiness-query\",\"kind\":\"query\",\"item\":\"slime.pink\",\"count\":0,\"save_id\":\"" + name + "\"}")!;
        return (CommandResult)typeof(InventoryAgent).GetMethod("Execute", Instance)!.Invoke(agent, new object[] { command })!;
    }
    static object? Snapshot() => typeof(BridgeMod).GetMethod("SnapshotOnMainThread", Static)!.Invoke(null, null);
    static T Get<T>(object value, string property) => (T)value.GetType().GetProperty(property)!.GetValue(value)!;
    sealed class Scope : IDisposable
    {
        internal readonly AutoSaveDirector Director = Fixture.Reset();
        readonly string path = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "readiness-" + Guid.NewGuid().ToString("N") + ".journal");
        internal readonly OpJournal Journal;
        internal readonly NativeStorageBinding Binding;
        internal CounterpartCreator? Creator;
        internal bool GuardDenied, GuardThrows;
        internal Scope()
        {
            Journal = OpJournal.Open(path, out var error) ?? throw new Exception(error);
            Binding = new NativeStorageBinding("synthetic-only", Environment.ProcessPath!, path, Environment.ProcessId, _ => { }, () => 1791302400000, () => 0);
            Binding.Tick(); Require(Binding.IsCurrent(Director), "current native-storage guard");
        }
        internal void Begin()
        {
            Creator = CounterpartCreator.Begin(new() { Kind = "create_save", CreationId = Id, OpId = "readiness-create" }, Journal,
                Environment.ProcessId, out var immediate, d => { if (GuardThrows) throw new InvalidOperationException("synthetic guard observation"); return !GuardDenied && Binding.IsCurrent(d); });
            Require(Creator != null && immediate == null && Director.DispatchCalls == 1, "dispatch exactly once");
            Require(Director.Pending!.MoveNext(), "scoped iterator/name hook");
            Require(!Director.loadedGame && Director.newGameMetadata != null, "native NEWGAME stays false");
            Require(Creator!.Step() == null && Director.SaveCalls == 0, "metadata pending cannot flush");
            Fixture.CompleteNewGame(Director); Configure();
        }
        internal void Complete()
        {
            Begin(); var result = Creator!.Step();
            Require(result != null && result.Ok && result.Applied && result.Persisted && result.Verified == true && !result.Uncertain,
                "production creator full detached readback completion");
            Require(ReferenceEquals(Creator.Step(), result) && !Director.loadedGame && Director.SaveCalls == 1 && Fixture.Loads == 1,
                "one flush/readback and false loadedGame");
        }
        public void Dispose() => File.Delete(path);
    }
}
#endif
