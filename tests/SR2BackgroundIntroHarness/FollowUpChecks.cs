using SlimeBridge.SR2;
using static Program;

// Review deleg_78f33a00 P3 follow-ups (SR2-R1..R4) plus bounded runtime observability: the
// protected runtime must be able to tell "armed", "refused", "advanced" and "retired" apart.
// Synthetic only; no IL2CPP/game runtime proof.
internal static class FollowUpChecks
{
    internal static void Run()
    {
        ArmAndAdvanceLogs();
        RefusalLogs();
        ForegroundWaitLoggedOnce();
        ExceptionDetailLogged();
        LogVolumeBounded();
        FinalFocusAfterLastAuthority();
        FrameEntryReplacedInFinalAuthority();
        UnloadDuringArming();
    }

    static World Ready() { var w = new World(); w.Begin(); w.Activate(); w.Complete(); return w; }
    static int Count(World w, string fragment) => w.Log.Count(m => m.Contains(fragment, StringComparison.Ordinal));
    static IEnumerable<string> IntroLines(World w) => w.Log.Where(m => m.StartsWith("background intro", StringComparison.Ordinal));

    static void ArmAndAdvanceLogs()
    {
        using var w = Ready(); var root = w.Root;
        Check(Count(w, "background intro arm attempt save=sb2_" + World.Id + " op=" + w.Command.OpId) == 1,
            "observability: one arm attempt line names the created save and the fresh CREATE op");
        Check(w.Log.Any(m => m.StartsWith("background intro armed save=sb2_" + World.Id + " root=0x", StringComparison.Ordinal) &&
                             m.EndsWith(" frames=2 index=0", StringComparison.Ordinal)),
            "observability: armed line names save, bound native root pointer, frame count and index");
        w.Tick(); // typewriter interrupt on frame 0
        Check(Count(w, "background intro first native call frameIndex=0 count=2") == 1, "observability: first native call is logged");
        w.Tick(); // frame 0 -> 1
        Check(Count(w, "background intro advanced frameIndex=0->1 count=2 calls=2") == 1, "observability: frame advance 0->1 logged with call count");
        root.FinishCameraTransition(); root.BindTextAfterWait(); w.Tick(); // interrupt frame 1
        w.Tick(); // frame 1 -> 2 (end)
        Check(Count(w, "background intro advanced frameIndex=1->2 count=2 calls=4") == 1, "observability: final frame advance logged");
        root.FinishNativeEndWait(); w.Tick();
        Check(w.Log.Any(m => m.StartsWith("background intro retired: intro root cleared/replaced", StringComparison.Ordinal) &&
                             m.Contains(" calls=4 advances=2 elapsed=", StringComparison.Ordinal)),
            "observability: native root clear retires the helper with total calls and advances");
        Check(Count(w, "background intro first native call") == 1, "observability: first-call line is emitted once");
        Check(!w.Log.Any(m => m.Contains("intro complete", StringComparison.OrdinalIgnoreCase) || m.Contains("controls restored", StringComparison.OrdinalIgnoreCase)),
            "observability never claims gameplay completion or control release");
    }

    static void RefusalLogs()
    {
        using (var w = new World())
        {
            w.Begin(); w.Activate(); Set(w.Agent, "_introOp", "foreign-op"); w.Tick(posting: false);
            Check(Count(w, "background intro arm attempt") == 0 &&
                  w.Log.Any(m => m.StartsWith("background intro not armed: ", StringComparison.Ordinal) && m.EndsWith(" op=" + w.Command.OpId, StringComparison.Ordinal)),
                "observability: a verified completion that is not the fresh CREATE logs why intro was not armed");
        }
        using (var w = new World())
        {
            w.Begin(); w.Activate();
            Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = null; Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = null;
            w.Complete();
            Check(Count(w, "background intro arm attempt") == 1 && Count(w, "background intro arm refused: intro root absent when arming calls=0 advances=0") == 1,
                "observability: constructor refusal is logged as an arm refusal with its reason");
        }
    }

    static void ForegroundWaitLoggedOnce()
    {
        using var w = Ready();
        w.Focus = () => false; w.Tick(); w.Tick(); w.Tick();
        Check(w.Root.Helpers == 0 && Count(w, "background intro waiting: game window is foreground") == 1,
            "observability: foreground wait is logged once, not per frame");
        w.Focus = () => true; w.Tick();
        Check(w.Root.Helpers == 1, "foreground wait still resumes when background again");
    }

    static void ExceptionDetailLogged()
    {
        using (var w = Ready())
        {
            w.Focus = () => throw new InvalidOperationException("synthetic focus unavailable"); w.Tick();
            Check(w.Log.Any(m => m.StartsWith("background intro retired: ", StringComparison.Ordinal) && m.Contains("InvalidOperationException: synthetic focus unavailable", StringComparison.Ordinal)),
                "SR2-R4: focus exception type and message are logged");
        }
        using (var w = Ready())
        {
            w.Root.OnHelper = () => throw new Exception("native failure\r\nsecond line"); w.Tick();
            Check(w.Log.Any(m => m.Contains("Exception: native failure  second line", StringComparison.Ordinal) && !m.Contains('\n') && !m.Contains((char)13)),
                "SR2-R4: native exception detail is logged on one line");
        }
        using (var w = Ready())
        {
            w.Root.OnHelper = () => throw new Exception(new string('x', 2000)); w.Tick();
            Check(IntroLines(w).All(m => m.Length < 400), "SR2-R4: exception detail is length-bounded");
        }
    }

    static void LogVolumeBounded()
    {
        using var w = Ready(); var panel = w.Root._frameConfigs![0].TextPanelView!;
        for (var i = 0; i < 513; i++) { panel.Animating = true; w.Tick(); }
        Check(w.Root.Helpers == 512 && IntroLines(w).Count() <= 5, "observability: 512 native calls emit a bounded number of log lines");
    }

    // SR2-R1: the last background read must follow the last full authority read.
    static void FinalFocusAfterLastAuthority()
    {
        using (var w = Ready())
        {
            var events = new List<string>();
            w.Focus = () => { events.Add("focus"); return true; };
            Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.OnPull = () => events.Add("pull");
            w.Root.OnHelper = () => events.Add("native");
            w.Tick();
            var native = events.IndexOf("native");
            var before = native > 0 ? events.Take(native).ToList() : new List<string>();
            Check(native > 0 && before.LastIndexOf("pull") >= 0 && before.LastIndexOf("focus") > before.LastIndexOf("pull"),
                "SR2-R1: background is re-read after the last full authority read, immediately before the native call");
        }
        int reads;
        using (var probe = Ready()) { var n = 0; var at = -1; probe.Focus = () => { n++; return true; }; probe.Root.OnHelper = () => { if (at < 0) at = n; }; probe.Tick(); reads = at; }
        using (var w = Ready())
        {
            var r = 0; w.Focus = () => ++r < reads; w.Tick();
            Check(reads >= 1 && r == reads && w.Root.Helpers == 0, "SR2-R1: foreground observed by the final background read prevents the native call");
        }
    }

    // SR2-R2: an element replaced inside the final authority read must not be invoked on stale eligibility.
    static void FrameEntryReplacedInFinalAuthority()
    {
        int last;
        using (var probe = Ready())
        {
            var n = 0; var at = -1;
            Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!.OnPull = () => n++;
            probe.Root.OnHelper = () => { if (at < 0) at = n; };
            probe.Tick(); last = at;
        }
        using var w = Ready(); var pulls = 0;
        var settings = Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!;
        settings.OnPull = () => { if (++pulls == last) w.Root._frameConfigs![w.Root._frameIndex] = new(); };
        w.Tick();
        Check(last >= 1 && pulls >= last && w.Root.Helpers == 0, "SR2-R2: frame entry replaced during the final authority read is not invoked");
        settings.OnPull = null; w.Tick();
        Check(w.Root.Helpers == 1, "SR2-R2: next update re-reads the current entry (wait, not retire)");
    }

    // SR2-R3: a lifecycle cancellation raised while the helper is being constructed must still retire it.
    static void UnloadDuringArming()
    {
        using var w = new World(); w.Begin(); w.Activate();
        var fired = false;
        var settings = Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings!;
        settings.OnPull = () =>
        {
            if (fired || Get(w.Agent, "_creation") != null) return;
            fired = true; new LifecycleBridge(w.Agent).OnSceneWasUnloaded(9, "unloaded-during-arming");
        };
        w.Complete();
        settings.OnPull = null; w.Tick(); w.Tick();
        Check(fired && w.Root.Helpers == 0 && Get(w.Agent, "_intro") == null, "SR2-R3: scene unload raised while arming retires the new helper");
    }
}
