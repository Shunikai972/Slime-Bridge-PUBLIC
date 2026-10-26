using SlimeBridge.SR2;
using Il2CppMonomiPark.SlimeRancher.UI.IntroSequence;
using static Program;

// Review deleg_f81d654b SR2-1/SR2-2/SR2-3: every callback-capable seam that precedes the one
// native mutation is exercised at EVERY occurrence, independent of how many reads the
// implementation performs. Synthetic only; no IL2CPP/game runtime proof.
internal static class SeamChecks
{
    internal static void Run()
    {
        SeamMatrix();
        FinalFrameReentrantUnload();
        MissingIdentityAtArming();
    }

    enum Seam { FrameList, Serializer, StorageGuard, Focus }

    sealed record Mutation(string Name, Action<World> Apply, Action<World>? Restore);

    static readonly Mutation[] Mutations =
    {
        new("cancel", w => w.Agent.CancelBackgroundIntro(), null),
        new("verbatim unload", w => new LifecycleBridge(w.Agent).OnSceneWasUnloaded(9, "unloaded-nonowned-additive-scene"), null),
        new("verbatim deinit", w => new LifecycleBridge(w.Agent).OnDeinitializeMelon(), null),
        new("witness cleared", w => CounterpartCreator.ClearWorldReadiness(), null),
        new("storage revoked", w => w.StorageCurrent = false, w => w.StorageCurrent = true),
        new("health lost", w => Il2Cpp.SceneContext.Instance!.PlayerState!.Health = 0, w => Il2Cpp.SceneContext.Instance!.PlayerState!.Health = 100),
        new("player inactive", w => Il2Cpp.SceneContext.Instance!.Player!.activeInHierarchy = false, w => Il2Cpp.SceneContext.Instance!.Player!.activeInHierarchy = true),
        new("loading begins", w => Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = true, w => Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = false),
        new("menu group", w => Il2Cpp.SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenuFromBoot", w => Il2Cpp.SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "FixtureWorld"),
        new("stock settings changed", w => Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.optionsById!.First().Value.OptionValueKey = "late-invalid", null),
        new("metadata appeared", w => w.Director.newGameMetadata = new(), w => w.Director.newGameMetadata = null),
        new("slot changed", w => w.Director.SavedGame!.GameState!.SaveSlotIndex = 1, w => w.Director.SavedGame!.GameState!.SaveSlotIndex = 0),
        new("LOADSAVE flag", w => { w.Director.loadedGame = true; w.Director._currentGameName = "sb2_" + World.Id; }, w => w.Director.loadedGame = false),
        new("root replaced", w => { var other = new IntroSequenceUIRoot(); Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = other; Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = other; },
            w => { Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = w.Root; Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = w.Root; }),
        new("scene replaced", w => { var old = Il2Cpp.SceneContext.Instance!; Il2Cpp.SceneContext.Instance = new() { PlayerState = old.PlayerState, Player = old.Player, GameModel = old.GameModel, CutsceneDirector = old.CutsceneDirector }; }, null),
        new("director replaced", w => Il2Cpp.GameContext.Instance!.AutoSaveDirector = new(), w => Il2Cpp.GameContext.Instance!.AutoSaveDirector = w.Director),
    };

    // Native-only boundaries, not arbitrary user callbacks: successful production storage guard
    // reads fields only; final production focus reads P/Invoke/Unity icalls only. Synthetic writes
    // to settings/storage at those final seams cannot occur there in production. Still exercise
    // cancellation, witness, identity and list changes at EVERY focus read, including the last.
    // These exclusions are explicit review obligations, NOT proof for future callback predicates.
    static bool OutOfContract(Seam seam, Mutation m, int k, int n) =>
        (seam == Seam.StorageGuard && k == n && m.Name == "stock settings changed") ||
        (seam == Seam.Focus && k == n && (m.Name == "stock settings changed" || m.Name == "storage revoked"));

    static void Install(World w, Seam seam, Action<int> onOccurrence)
    {
        var n = 0;
        switch (seam)
        {
            case Seam.FrameList: w.Root._frameConfigs!.OnIndexRead = _ => onOccurrence(++n); break;
            case Seam.Serializer: Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.OnPull = () => onOccurrence(++n); break;
            case Seam.StorageGuard: w.OnStorage = () => onOccurrence(++n); break;
            case Seam.Focus: w.Focus = () => { onOccurrence(++n); return true; }; break;
        }
    }

    static void Uninstall(World w, IntroSequenceUIRoot root)
    {
        if (root._frameConfigs != null) root._frameConfigs.OnIndexRead = null;
        var settings = Il2Cpp.SceneContext.Instance?.GameModel?.gameSettings; if (settings != null) settings.OnPull = null;
        w.OnStorage = null; w.Focus = () => true;
    }

    static int Probe(Seam seam)
    {
        // Count only occurrences BEFORE native entry: the fixture's own TryToIterateFrame reads the
        // frame list inside the native call, which is not a pre-mutation seam.
        using var w = ReadyWorld(); var count = 0; var atNativeEntry = -1;
        Install(w, seam, k => count = k);
        w.Root.OnHelper = () => { if (atNativeEntry < 0) atNativeEntry = count; };
        w.Tick();
        Check(w.Root.Helpers == 1 && atNativeEntry >= 0, "seam probe " + seam + ": clean background owned update performs one native advance");
        return atNativeEntry;
    }

    static World ReadyWorld() { var w = new World(); w.Begin(); w.Activate(); w.Complete(); return w; }

    static void SeamMatrix()
    {
        foreach (var seam in Enum.GetValues<Seam>())
        {
            var n = Probe(seam);
            Check(n >= 1, "seam " + seam + " is exercised before the native advance (count=" + n + ")");
            for (var k = 1; k <= n; k++)
                foreach (var m in Mutations)
                {
                    if (OutOfContract(seam, m, k, n)) continue;
                    using var w = ReadyWorld(); var original = w.Root; var applied = false;
                    Install(w, seam, occurrence => { if (occurrence == k && !applied) { applied = true; m.Apply(w); } });
                    w.Tick();
                    var current = Il2Cpp.SceneContext.Instance?.CutsceneDirector?._uiRoot;
                    var label = seam + " occurrence " + k + "/" + n + " -> " + m.Name;
                    Check(applied && original.Helpers == 0 && original.EndFrames == 0 && (current == null || current.Helpers == 0),
                        label + ": no native advance after invalidation inside the same update");
                    Uninstall(w, original); m.Restore?.Invoke(w); w.Tick();
                    current = Il2Cpp.SceneContext.Instance?.CutsceneDirector?._uiRoot;
                    Check(original.Helpers == 0 && (current == null || current.Helpers == 0),
                        label + ": permission stays retired; restoring state never re-arms");
                }
        }
    }

    static void FinalFrameReentrantUnload()
    {
        using var w = ReadyWorld();
        w.Root._frameIndex = w.Root._frameConfigs!.Count - 1;
        w.Root._frameConfigs[w.Root._frameIndex].TextPanelView!.Animating = false;
        var called = false;
        Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.OnPull = () =>
        { if (called) return; called = true; new LifecycleBridge(w.Agent).OnSceneWasUnloaded(9, "unloaded-nonowned-additive-scene"); };
        w.Tick();
        Check(called && w.Root.Helpers == 0 && w.Root.EndFrames == 0 && w.Root.EndCoroutines == 0 && w.Root.MusicDequeues == 0,
            "verbatim unload during final-frame serializer schedules no native final EndFrame/EndCoroutine");
        Check(Get(w.Agent, "_intro") == null, "unload detaches the helper");
    }

    static void MissingIdentityAtArming()
    {
        foreach (var missing in new[] { "root", "cutscene" })
        {
            using var w = new World(); w.Begin(); w.Activate();
            if (missing == "root") Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = null;
            else Il2Cpp.SceneContext.Instance!.CutsceneDirector = null;
            Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = null;
            w.Complete();
            var other = new IntroSequenceUIRoot();
            Il2Cpp.SceneContext.Instance!.CutsceneDirector ??= new();
            Il2Cpp.SceneContext.Instance.CutsceneDirector._uiRoot = other;
            Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = other;
            w.Tick(); w.Tick();
            Check(other.Helpers == 0 && w.Root.Helpers == 0,
                "absent " + missing + " at durable completion: a later-attached root is never adopted");
        }
    }
}
