using SlimeBridge.Shared;
using SlimeBridge.SR2;
using Il2CppMonomiPark.SlimeRancher.UI;
using Il2CppMonomiPark.SlimeRancher.UI.IntroSequence;
using static Program;

internal static class NegativeChecks
{
    internal static void Run()
    {
        LifecycleAndLateRootRace();
        FocusAndTransientGates();
        IdentityLosses();
        BudgetsAndExceptions();
        PermissionBoundaries();
    }
    static World Ready()
    { var world = new World(); world.Begin(); world.Activate(); world.Complete(); return world; }
    static void LifecycleAndLateRootRace()
    {
        using (var world = Ready())
        {
            var mod = new LifecycleBridge(world.Agent);
            mod.OnSceneWasUnloaded(123, "fixture-scene"); world.Tick();
            Check(world.Root.Helpers == 0, "actual scene-unload callback cancels authorized helper before another ordinary update");
        }
        using (var world = new World())
        {
            world.Begin(); world.Activate();
            var mod = new LifecycleBridge(world.Agent); mod.OnDeinitializeMelon(); world.Tick(posting: false); world.Tick();
            Check(world.Root.Helpers == 0 && world.Director.SaveCalls == 0 && Get(world.Agent, "_intro") == null,
                "actual mod deinitialization shuts down adapter, including stale updates of pending creation");
        }
        var beforeFirstTick = new (string Name, Action<World> Change)[] {
            ("root", w => { var other = new IntroSequenceUIRoot(); Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = other; Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = other; }),
            ("scene", w => { var old = Il2Cpp.SceneContext.Instance!; Il2Cpp.SceneContext.Instance = new() { PlayerState = old.PlayerState, Player = old.Player, GameModel = old.GameModel, CutsceneDirector = old.CutsceneDirector }; }),
        };
        foreach (var test in beforeFirstTick)
        {
            using var world = Ready(); test.Change(world); world.Tick();
            var currentRoot = Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot!;
            Check(world.Root.Helpers == 0 && currentRoot.Helpers == 0,
                "completed ownership is bound when armed: " + test.Name + " replacement before first automation update cannot be adopted");
        }
        var lateGates = new (string Name, Action<World> Change)[] {
            ("UI top", w => Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = new BaseUI()),
            ("active", w => w.Root.gameObject!.activeInHierarchy = false),
            ("enabled", w => w.Root.enabled = false),
            ("frame list", w => w.Root._frameConfigs = new() { new(), new() }),
        };
        foreach (var test in lateGates)
        {
            using var world = Ready(); var reads = 0;
            world.Root._frameConfigs!.OnIndexRead = _ => { if (++reads == 1) test.Change(world); }; // SeamChecks covers every occurrence.
            world.Tick(); Check(reads >= 1 && world.Root.Helpers == 0, "final frame read changed " + test.Name + ": no stale eligibility");
        }
        using (var world = Ready())
        {
            var reads = 0; var original = world.Root;
            var other = new IntroSequenceUIRoot();
            original._frameConfigs!.OnIndexRead = _ => {
                if (++reads == 1) Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = other;
            };
            world.Tick();
            Check(reads >= 1 && original.Helpers == 0 && other.Helpers == 0,
                "root swapped during final frame/config read must never invoke old root");
            Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = original;
            original._frameConfigs.OnIndexRead = null; world.Tick();
            Check(original.Helpers == 0, "observed root replacement permanently revokes permission even if restored");
        }
    }
    static void FocusAndTransientGates()
    {
        using (var world = Ready())
        {
            world.Focus = () => false; world.Tick(); Check(world.Root.Helpers == 0, "foreground/unknown never authorizes invocation");
            world.Focus = () => true; world.Tick(); Check(world.Root.Helpers == 1, "background can resume same current intro after foreground pause");
            world.Agent.Tick(); Check(world.Root.Helpers == 1, "repeated Tick in one Unity frame cannot interrupt then advance");
            world.Tick(); Check(world.Root._currentTween != null, "native transition was scheduled while background");
            world.Focus = () => false; world.Root.FinishCameraTransition(); world.Root.BindTextAfterWait(); world.Tick();
            Check(world.Root.Helpers == 2 && world.Root._isInputEnabled, "already scheduled native transition may finish foreground, but no NEW call is made");
        }
        using (var world = Ready())
        {
            var focusReads = 0; world.Focus = () => ++focusReads == 1; world.Tick();
            Check(focusReads == 2 && world.Root.Helpers == 0, "background is rechecked immediately before invocation, not cached");
        }
        using (var world = new World(defaultFocus: true))
        {
            world.Begin(); world.Activate(); world.Complete();
            UnityEngine.Application.platform = UnityEngine.RuntimePlatform.LinuxPlayer;
            world.Tick(); Check(world.Root.Helpers == 0, "production Windows/Unity focus predicate rejects unknown or unsupported platform");
            UnityEngine.Application.platform = UnityEngine.RuntimePlatform.WindowsPlayer;
            UnityEngine.Application.isFocused = true; world.Tick();
            Check(world.Root.Helpers == 0, "production default predicate rejects Unity foreground focus without override");
            UnityEngine.Application.isFocused = false;
        }
        var transient = new (string Name, Action<World> Disable, Action<World> Restore)[] {
            ("inactive", w => w.Root.gameObject!.activeInHierarchy = false, w => w.Root.gameObject!.activeInHierarchy = true),
            ("disabled", w => w.Root.enabled = false, w => w.Root.enabled = true),
            ("not top", w => Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = new BaseUI(), w => Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = w.Root),
            ("startup gate", w => w.Root._isInputEnabled = false, w => w.Root._isInputEnabled = true),
            ("transition tween even if gate true", w => w.Root._currentTween = new(), w => w.Root._currentTween = null),
        };
        foreach (var test in transient)
        {
            using var world = Ready(); test.Disable(world); world.Tick();
            Check(world.Root.Helpers == 0, test.Name + " refuses native advance");
            test.Restore(world); world.Tick(); Check(world.Root.Helpers == 1, test.Name + " waits rather than modifying native gates");
        }
        var invalid = new (string Name, Action<World> Change)[] {
            ("missing root", w => Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = null),
            ("fake-null root", w => w.Root.NativeAlive = false),
            ("zero root pointer", w => w.Root.Pointer = IntPtr.Zero),
            ("missing cutscene", w => Il2Cpp.SceneContext.Instance!.CutsceneDirector = null),
            ("missing UI director", w => Il2Cpp.GameContext.Instance!.UIDirector = null),
            ("missing gameObject", w => w.Root.gameObject = null),
            ("fake-null gameObject", w => w.Root.gameObject!.NativeAlive = false),
            ("missing list", w => w.Root._frameConfigs = null),
            ("zero list pointer", w => w.Root._frameConfigs!.Pointer = IntPtr.Zero),
            ("empty list", w => w.Root._frameConfigs!.Clear()),
            ("oversize list", w => { while(w.Root._frameConfigs!.Count < 257) w.Root._frameConfigs.Add(new()); }),
            ("negative index", w => w.Root._frameIndex = -1),
            ("past-end index", w => w.Root._frameIndex = w.Root._frameConfigs!.Count),
            ("null frame", w => w.Root._frameConfigs![0] = null!),
            ("zero frame pointer", w => w.Root._frameConfigs![0].Pointer = IntPtr.Zero),
            ("missing text panel", w => w.Root._frameConfigs![0].TextPanelView = null),
            ("fake-null text panel", w => w.Root._frameConfigs![0].TextPanelView!.NativeAlive = false),
            ("zero text pointer", w => w.Root._frameConfigs![0].TextPanelView!.Pointer = IntPtr.Zero),
        };
        foreach (var test in invalid)
        { using var world = Ready(); test.Change(world); world.Tick(); Check(world.Root.Helpers == 0, test.Name + " fails closed"); }
        using (var world = Ready())
        {
            world.Root._frameConfigs!.OnIndexRead = _ => world.Root._frameIndex = 1;
            world.Tick(); Check(world.Root.Helpers == 0, "frame index changing while panel is read is rejected, not stale index invocation");
            world.Root._frameConfigs.OnIndexRead = null; world.Tick();
            Check(world.Root.Helpers == 1 && world.Root._frameIndex == 1, "next update uses freshly bounded current index");
        }
    }
    static void IdentityLosses()
    {
        var changes = new (string Name, Action<World> Change, Action<World>? Restore)[] {
            ("changed root", w => Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = new(), w => Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = w.Root),
            ("changed cutscene", w => Il2Cpp.SceneContext.Instance!.CutsceneDirector = new() { _uiRoot = w.Root }, null),
            ("changed scene", w => { var old = Il2Cpp.SceneContext.Instance!; Il2Cpp.SceneContext.Instance = new() { PlayerState = old.PlayerState, Player = old.Player, GameModel = old.GameModel, CutsceneDirector = old.CutsceneDirector }; }, null),
            ("changed director", w => Il2Cpp.GameContext.Instance!.AutoSaveDirector = new(), w => Il2Cpp.GameContext.Instance!.AutoSaveDirector = w.Director),
            ("changed save pointer", w => w.Director.SavedGame!.Pointer = new(99991), null),
            ("changed name", w => w.Director.SavedGame!.GameState!.GameName = "foreign-name", w => w.Director.SavedGame!.GameState!.GameName = "sb2_" + World.Id),
            ("changed slot", w => w.Director.SavedGame!.GameState!.SaveSlotIndex = 1, w => w.Director.SavedGame!.GameState!.SaveSlotIndex = 0),
            ("LOADSAVE true flag", w => { w.Director.loadedGame = true; w.Director._currentGameName = "sb2_" + World.Id; }, w => w.Director.loadedGame = false),
            ("stock settings loss", w => Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.optionsById!.First().Value.OptionValueKey = "foreign-setting", null),
            ("storage guard loss", w => w.StorageCurrent = false, w => w.StorageCurrent = true),
            ("metadata reappears", w => w.Director.newGameMetadata = new(), w => w.Director.newGameMetadata = null),
            ("menu", w => Il2Cpp.SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenuFromBoot", w => Il2Cpp.SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "FixtureWorld"),
            ("loading epoch", w => Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = true, w => Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = false),
            ("scene teardown", w => Il2Cpp.SceneContext.Instance = null, null),
            ("dead current scene", w => Il2Cpp.SceneContext.Instance!.NativeAlive = false, w => Il2Cpp.SceneContext.Instance!.NativeAlive = true),
            ("health readiness loss", w => Il2Cpp.SceneContext.Instance!.PlayerState!.Health = 0, w => Il2Cpp.SceneContext.Instance!.PlayerState!.Health = 100),
            ("witness cleared", w => CounterpartCreator.ClearWorldReadiness(), null),
        };
        foreach (var test in changes)
        {
            using var world = Ready(); world.Tick(); Check(world.Root.Helpers == 1, "test prerequisite binds owned root before " + test.Name);
            test.Change(world); world.Tick(); Check(world.Root.Helpers == 1, test.Name + " stops new native calls");
            test.Restore?.Invoke(world); world.Tick(); Check(world.Root.Helpers == 1, test.Name + " permanently cancels instead of rebinding");
        }
        using (var world = Ready())
        {
            world.Tick();
            var original = world.Root;
            var wrapper = new IntroSequenceUIRoot { Pointer = original.Pointer, _frameConfigs = original._frameConfigs, _frameIndex = original._frameIndex };
            Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = wrapper;
            Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = new BaseUI { Pointer = original.Pointer };
            world.Tick(); Check(wrapper.Helpers == 1 && wrapper.EndFrames == 1,
                "distinct managed wrappers with exact same native root/UI pointer retain identity; ReferenceEquals is not used");
        }
        using (var world = Ready())
        {
            Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.OnPull = () => world.Director.SavedGame!.GameState!.SaveSlotIndex = 1;
            world.Tick(); Check(world.Root.Helpers == 0, "native serializer callback changing slot invalidates current owned witness before invocation");
        }
    }
    static void BudgetsAndExceptions()
    {
        foreach (var time in new[] { 300f, -1f, float.NaN, float.PositiveInfinity })
        { using var world = Ready(); UnityEngine.Time.realtimeSinceStartup = time; world.Tick(); Check(world.Root.Helpers == 0, "deadline/nonfinite/clock epoch fails closed: " + time); }
        using (var world = Ready())
        { world.Tick(); UnityEngine.Time.frameCount = -1; world.Agent.Tick(); Check(world.Root.Helpers == 1, "frame epoch reversal cancels"); }
        using (var world = Ready())
        {
            var panel = world.Root._frameConfigs![0].TextPanelView!;
            for (var i = 0; i < 513; i++) { panel.Animating = true; world.Tick(); }
            Check(world.Root.Helpers == 512, "call budget bounds repeated native typewriter interruptions without forcing completion");
        }
        using (var world = Ready())
        { world.Focus = () => throw new Exception("synthetic focus unavailable"); world.Tick(); world.Focus = () => true; world.Tick(); Check(world.Root.Helpers == 0, "focus exception permanently fails closed"); }
        using (var world = Ready())
        { world.Root.OnHelper = () => throw new Exception("native failure"); world.Tick(); world.Root.OnHelper = null; world.Tick(); Check(world.Root.Helpers == 1, "native invocation exception never retries ambiguous action"); }
    }
    static void PermissionBoundaries()
    {
        using (var world = new World(lab: false))
        { var result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", world.Command); world.Tick(); Check(result != null && world.Director.DispatchCalls == 0 && world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null, "no helper outside protected LAB creation path"); }
        using (var world = new World(journal: false))
        { var result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", world.Command); world.Tick(); Check(result != null && world.Director.DispatchCalls == 0 && Get(world.Agent, "_intro") == null, "no journal means no dispatch or intro permission"); }
        using (var world = new World())
        { world.StorageCurrent = false; var result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", world.Command); world.Tick(); Check(result != null && world.Director.DispatchCalls == 0 && Get(world.Agent, "_intro") == null, "lost protected storage cannot arm native intro"); }
        using (var world = new World())
        {
            using (var locked = new FileStream(world.JournalPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
            { var result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", world.Command); Check(result != null && world.Director.DispatchCalls == 0 && Get(world.Agent, "_intro") == null, "durable INTENT failure cannot dispatch or arm"); }
        }
        using (var world = new World())
        {
            world.Begin(); world.Activate();
            using (var locked = new FileStream(world.JournalPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) world.Tick(posting: false);
            world.Tick(); Check(world.Director.SaveCalls == 1 && world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null, "durable DONE failure after native proof cancels intro permission");
        }
        using (var world = new World())
        {
            world.Begin(); world.Activate(); Il2Cpp.SceneContext.Instance!.PlayerState!.Health = 0;
            world.Tick(posting: false); world.Tick();
            Check(world.Director.SaveCalls == 1 && world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null, "individual health absent at completion: do NOT weaken strict owned readiness after flush");
        }
        using (var world = new World())
        { world.Begin(); world.Activate(); Set(Get(world.Agent, "_creation")!, "_errored", true); world.Tick(posting: false); world.Tick(); Check(world.Director.SaveCalls == 0 && Get(world.Agent, "_intro") == null, "native error callback cannot authorize intro"); }
        using (var world = new World())
        { world.Begin(); UnityEngine.Time.realtimeSinceStartup = 241f; world.Tick(posting: false); world.Tick(); Check(world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null, "uncertain creation timeout never arms helper"); }
        using (var world = new World())
        { world.Begin(); world.Activate(); Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.optionsById!.First().Value.OptionValueKey = "changed"; world.Tick(posting: false); world.Tick(); Check(world.Director.SaveCalls == 0 && Get(world.Agent, "_intro") == null, "settings mismatch cannot arm helper or weaken creator checks"); }
        using (var world = new World())
        { world.Begin(); world.Activate(); Set(world.Agent, "_introOp", "foreign-op"); world.Tick(posting: false); world.Tick(); Check(world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null, "completion must bind original fresh CREATE op id"); }
        using (var world = Ready())
        {
            world.Agent.CancelBackgroundIntro();
            var reconcile = new BridgeCommand { OpId = "reconcile-intro", Kind = "reconcile_save", CreationId = World.Id };
            var result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", reconcile); world.Tick();
            Check(result?.Verified == true && world.Director.DispatchCalls == 1 && world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null,
                "read-only reconciliation cannot grant intro permission, even with old current owned witness");
            result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", world.Command); world.Tick();
            Check(result?.Verified == true && world.Director.DispatchCalls == 1 && world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null,
                "existing-save successful Begin is not a fresh non-null creator and cannot grant permission");
            world.Director.StorageProvider!.Names.Clear(); world.Director.StorageProvider.Payloads.Clear(); world.Director.Summaries.Clear();
            Il2Cpp.SceneContext.Instance = null; Il2Cpp.SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "MainMenuFromBoot";
            result = (CommandResult?)Invoke(world.Agent, "ExecuteCreation", world.Command); world.Tick();
            Check(result?.Uncertain == true && world.Director.DispatchCalls == 1 && Get(world.Agent, "_intro") == null,
                "journal replay/previously dispatched creation cannot re-arm or dispatch even if native rows disappear");
        }
        using (var world = new World())
        { world.Begin(); world.Activate(); world.Tick(posting: true); Check(world.Root.Helpers == 0 && Get(world.Agent, "_intro") == null && world.Director.SaveCalls == 0, "pending CREATE plus intro presence is not current owned completion authority"); }
    }
}
