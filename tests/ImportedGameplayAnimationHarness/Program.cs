using System.Reflection;
using System.Text.Json;
using HarmonyLib;
using SlimeBridge.SR1;
using UnityEngine;

internal static class Program
{
    private static int _checks;
    private static int _failures;
    private static Action<SlimeSubbehaviourPlexer, RaycastHit>? _groundedPostfix;

    private sealed class Fixture
    {
        internal required GameObject Actor;
        internal required GameObject Visual;
        internal required ImportedSourceAnimation Source;
        internal required ImportedGameplayAnimation Driver;
        internal required Animator RootAnimator;
        internal required Animator ChildAnimator;
        internal required Animator VisualAnimator;
    }

    private static int Main()
    {
        Check(Assembly.GetExecutingAssembly().GetType("SlimeBridge.SR1.ImportedGameplayAnimation") != null,
            "production gameplay driver is linked into the executable harness");
        Check(typeof(ImportedGameplayAnimation).GetMethod("ToJson", BindingFlags.Instance | BindingFlags.NonPublic) != null,
            "diagnostics can call the production driver's ToJson method");
        Check(ImportedGameplayAnimationPatch.EnsurePatched(), "exact native gameplay hooks install");
        Check(Harmony.Patches.Count == 8, "eight exact gameplay methods are patched (seven transitions + SlimeEat.EatAndProduce for digestion)");
        var groundedPatch = PatchFor("OnGroundedRaycastResultReceived");
        if (groundedPatch?.postfix != null)
            _groundedPostfix = (Action<SlimeSubbehaviourPlexer, RaycastHit>)groundedPatch.postfix.method.CreateDelegate(
                typeof(Action<SlimeSubbehaviourPlexer, RaycastHit>));
        Check(_groundedPostfix != null, "ground hook has a generated exact RaycastHit signature");
        var groundedParameters = groundedPatch?.postfix?.method.GetParameters();
        Check(groundedParameters != null && groundedParameters.Length == 2 &&
            groundedParameters[0].Name == "__instance" && groundedParameters[1].Name == "result",
            "ground hook uses Harmony 2.0.1 patch-factory argument names");
        CheckPatchContracts();

        var fixture = CreateFixture();
        Check(fixture.Driver != null, "one production driver attaches to the marked actor root");
        Check(fixture.Source.TryReadPlayback(out var idle, out _, out _) && idle == "Idle_Cotton_Ringtail",
            "source player's exact declared default clip is active");
        Check(fixture.RootAnimator.cullingMode == AnimatorCullingMode.AlwaysAnimate &&
            fixture.ChildAnimator.cullingMode == AnimatorCullingMode.AlwaysAnimate,
            "native actor Animator culling is forced active");
        Check(fixture.VisualAnimator.cullingMode == AnimatorCullingMode.CullCompletely,
            "culling walk excludes the imported visual subtree");

        var sourceAnimation = fixture.Visual.GetComponent<Animation>()!;
        sourceAnimation["Idle_Cotton_Ringtail"]!.time = 0.37f;
        var originalDriver = fixture.Actor.GetComponent<ImportedGameplayAnimation>();
        var addCallsBeforeRepeat = sourceAnimation.AddClipCalls;
        var playCallsBeforeRepeat = sourceAnimation.PlayCalls;
        Check(ImportedGameplayAnimation.EnsureAttached(fixture.Actor, fixture.Visual, out _),
            "appearance reapplication succeeds");
        Check(ReferenceEquals(originalDriver, fixture.Actor.GetComponent<ImportedGameplayAnimation>()),
            "appearance reapplication keeps the same actor driver");
        Check(fixture.Source.TryReadPlayback(out idle, out var idleTime, out _) &&
            idle == "Idle_Cotton_Ringtail" && Math.Abs(idleTime - 0.37f) < 0.0001f,
            "same-source reapplication does not reset the active source clock");
        Check(sourceAnimation.AddClipCalls == addCallsBeforeRepeat && sourceAnimation.PlayCalls == playCallsBeforeRepeat,
            "same-source reapplication creates no clip state and performs no playback");

        TestNoDeclarationAndTemplate();
        TestNativeActorExclusion();
        TestPounceLandingFallbackAndBitePriority(fixture);
        TestTimerHooksAndNativeArguments(fixture);
        TestMissingClipAndDestroyedSource();
        TestIndependentActorsAndBoundedHistory();
        TestNoHookAllocations();
        TestCallbackCanDestroyActor();
        TestDisableReenableAndUnload(fixture);

        Console.WriteLine($"gameplay animation checks={_checks} failures={_failures}; production-linked harness");
        return _failures == 0 ? 0 : 1;
    }

    private static void TestNoDeclarationAndTemplate()
    {
        var actor = new GameObject("plain-import-without-animation");
        actor.SetActive(true);
        var visual = new GameObject(ImportedSpawnerName);
        visual.SetParent(actor.transform);
        visual.AddComponent<ImportedVisualMarker>();
        visual.SetActive(true);
        Check(ImportedGameplayAnimation.EnsureAttached(actor, visual, out _),
            "missing source-animation declaration remains a no-op success");
        Check(actor.GetComponent<ImportedGameplayAnimation>() == null,
            "no driver is added when the visual has no declared source animation");

        var template = new GameObject("inactive-registered-prefab");
        var templateVisual = new GameObject(ImportedSpawnerName);
        templateVisual.SetParent(template.transform);
        templateVisual.AddComponent<ImportedVisualMarker>();
        Check(ImportedSourceAnimation.TryAttach(templateVisual, Descriptor(true, true), out _),
            "inactive template source-animation fixture is valid");
        Check(!ImportedGameplayAnimation.EnsureAttached(template, templateVisual, out _),
            "inactive registered template never receives an actor controller");
        Check(template.GetComponent<ImportedGameplayAnimation>() == null,
            "template rejection leaves its actor root untouched");
    }

    private static void TestNativeActorExclusion()
    {
        var native = new GameObject("native-tabby");
        native.SetActive(true);
        var chomper = native.AddComponent<Chomper>();
        var callbacks = 0;
        chomper.StartChomp(new GameObject("food"), Identifiable.Id.Tabby, false, false,
            () => { }, (_, _, _, _) => callbacks++);
        ImportedGameplayAnimationPatch.StartChompPostfix(chomper);
        Check(native.GetComponent<ImportedGameplayAnimation>() == null && chomper.IsChomping(),
            "native actor receives no driver and its native chomp continues");
        chomper.BiteComplete();
        ImportedGameplayAnimationPatch.BiteCompletePostfix(chomper);
        Check(chomper.NativeCompletionCalls == 1 && callbacks == 1,
            "native bite completion and callback execute without an imported driver");
    }

    private static void TestPounceLandingFallbackAndBitePriority(Fixture fixture)
    {
        var stalk = fixture.Actor.AddComponent<StalkConsumable>();
        var plexer = fixture.Actor.AddComponent<SlimeSubbehaviourPlexer>();
        var ground = fixture.Actor.AddComponent<Collider>();
        var chomper = fixture.Actor.AddComponent<Chomper>();

        stalk.SetPounceMode(false); // FEINT shares LeapToward, but is not a pounce.
        stalk.InvokeLeap();
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        Check(Counter(fixture.Driver, "jumps") == 0 && CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail",
            "feint LeapToward does not route to the pounce clip");

        Time.time = 1f;
        stalk.SetPounceMode(true);
        stalk.InvokeLeap();
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        Check(CurrentClip(fixture.Source) == "Pounce v4" && Counter(fixture.Driver, "jumps") == 1,
            "verified pounce mode starts the exact source pounce clip");

        Grounded(plexer, new RaycastHit { collider = ground });
        Check(Counter(fixture.Driver, "landings") == 0,
            "ground without an observed airborne sample cannot complete a pounce");
        plexer.SetFloating(true);
        Grounded(plexer, new RaycastHit());
        Grounded(plexer, new RaycastHit { collider = ground });
        plexer.SetFloating(false);
        plexer.SetCaptive(true);
        Grounded(plexer, new RaycastHit());
        Grounded(plexer, new RaycastHit { collider = ground });
        Check(Counter(fixture.Driver, "landings") == 0,
            "floating and captive observations cannot arm airborne or landing");
        plexer.SetCaptive(false);
        Grounded(plexer, new RaycastHit());
        Check(Json(fixture.Driver).GetProperty("saw_airborne").GetBoolean(),
            "unblocked null-collider callback records the required airborne observation");
        Grounded(plexer, new RaycastHit { collider = ground });
        Check(Counter(fixture.Driver, "landings") == 1 && CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail",
            "observed airborne-to-ground transition returns to the exact default idle");

        Time.time = 2f;
        stalk.InvokeLeap();
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        var nativeCompletionBeforeClock = chomper.NativeCompletionCalls;
        Time.time = 2.49f;
        fixture.Actor.Tick();
        Check(CurrentClip(fixture.Source) == "Pounce v4", "clip-duration fallback waits for the pounce clip duration");
        Time.time = 2.5f;
        fixture.Actor.Tick();
        Check(CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail" && Counter(fixture.Driver, "fallback_completions") == 1,
            "missed short-hop ground samples use the source clip duration fallback");
        Check(chomper.NativeCompletionCalls == nativeCompletionBeforeClock,
            "source-animation clock never invokes native bite completion");

        Time.time = 3f;
        stalk.InvokeLeap();
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        chomper.StartChomp(new GameObject("food"), Identifiable.Id.Tabby, false, false, () => { }, (_, _, _, _) => { });
        ImportedGameplayAnimationPatch.StartChompPostfix(chomper);
        Check(CurrentClip(fixture.Source) == "Bite v4" && Json(fixture.Driver).GetProperty("state").GetString() == "bite",
            "bite takes priority over an active pounce visual");
        var starts = Counter(fixture.Driver, "bite_starts");
        chomper.BiteComplete();
        ImportedGameplayAnimationPatch.BiteCompletePostfix(chomper);
        Check(Counter(fixture.Driver, "bite_starts") == starts && Counter(fixture.Driver, "bite_completes") == 1 &&
            CurrentClip(fixture.Source) == "Bite v4",
            "native bite completion does not cut the source bite short");
        Time.time = 6f;
        fixture.Actor.Tick();
        Check(CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail",
            "the source bite plays to its end and then returns to declared idle");

        chomper.StartChomp(new GameObject("food2"), Identifiable.Id.Tabby, false, false, () => { }, (_, _, _, _) => { });
        ImportedGameplayAnimationPatch.StartChompPostfix(chomper);
        chomper.Cancel();
        ImportedGameplayAnimationPatch.ForceCancelPostfix(chomper);
        Check(Counter(fixture.Driver, "bite_cancels") == 1 && CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail",
            "ForceCancelChomp cancels only the visual bite and restores idle");

        // Digestion after a successful chomp plays the source digest clip to its end, then returns to idle.
        var eater = fixture.Actor.AddComponent<SlimeEat>();
        Time.time = 7f;
        ImportedGameplayAnimationPatch.EatAndProducePostfix(eater);
        Check(CurrentClip(fixture.Source) == "Digest v4" && Json(fixture.Driver).GetProperty("state").GetString() == "digest",
            "native digestion plays the source digest clip");
        Time.time = 12f;
        fixture.Actor.Tick();
        Check(CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail", "the digest clip ends back on the declared idle");

        chomper.StartChomp(new GameObject("food3"), Identifiable.Id.Tabby, false, false, () => { }, (_, _, _, _) => { });
        ImportedGameplayAnimationPatch.StartChompPostfix(chomper);
        Time.time = 100f;
        fixture.Actor.Tick();
        Check(Json(fixture.Driver).GetProperty("state").GetString() == "bite" && chomper.NativeCompletionCalls == 1,
            "advancing source clocks cannot complete or cancel native gameplay bites");
        chomper.Cancel();
        ImportedGameplayAnimationPatch.ForceCancelPostfix(chomper);
    }

    private static void TestTimerHooksAndNativeArguments(Fixture fixture)
    {
        var random = fixture.Actor.AddComponent<SlimeRandomMove>();
        var before = Counter(fixture.Driver, "jumps");
        ImportedGameplayAnimationPatch.RandomActionPrefix(random, out var state, random.NextJump);
        random.Action(); // Native action with no changed jump timer.
        ImportedGameplayAnimationPatch.RandomActionPostfix(random, state, random.NextJump);
        Check(Counter(fixture.Driver, "jumps") == before,
            "SlimeRandomMove.Action without a changed timer is not classified as a jump");
        ImportedGameplayAnimationPatch.RandomActionPrefix(random, out state, random.NextJump);
        random.QueueJump(11f);
        random.Action();
        ImportedGameplayAnimationPatch.RandomActionPostfix(random, state, random.NextJump);
        Check(Counter(fixture.Driver, "jumps") == before + 1 && CurrentClip(fixture.Source) == "Pounce v4",
            "changed native random-jump timer routes one pounce");

        var finder = fixture.Actor.AddComponent<FindConsumable>();
        var available = 1f;
        ImportedGameplayAnimationPatch.MoveTowardsPrefix(finder, available, out state);
        finder.InvokeMove(ref available, 14f);
        ImportedGameplayAnimationPatch.MoveTowardsPostfix(finder, available, state);
        Check(available == 14f && Counter(fixture.Driver, "jumps") == before + 2,
            "MoveTowards observes its changed ref timer by value and preserves native mutation");
    }

    private static void TestMissingClipAndDestroyedSource()
    {
        var missing = CreateFixture(includePounce: false, includeBite: false);
        var random = missing.Actor.AddComponent<SlimeRandomMove>();
        ImportedGameplayAnimationPatch.RandomActionPrefix(random, out var state, random.NextJump);
        random.QueueJump(5f);
        random.Action();
        ImportedGameplayAnimationPatch.RandomActionPostfix(random, state, random.NextJump);
        Check(Counter(missing.Driver, "jumps") == 1 && Counter(missing.Driver, "play_failures") == 1 &&
            CurrentClip(missing.Source) == "Idle_Cotton_Ringtail",
            "missing pounce clip fails closed without altering native jump or source idle");

        var refusedPlay = CreateFixture();
        refusedPlay.Visual.GetComponent<Animation>()!.FailPlay = true;
        var chomp = refusedPlay.Actor.AddComponent<Chomper>();
        chomp.StartChomp(new GameObject("food"), Identifiable.Id.Tabby, false, false, () => { }, (_, _, _, _) => { });
        ImportedGameplayAnimationPatch.StartChompPostfix(chomp);
        Check(chomp.IsChomping() && Counter(refusedPlay.Driver, "bite_starts") == 1 &&
            Counter(refusedPlay.Driver, "play_failures") == 1,
            "failed visual Play leaves native chomp active and records a visual failure");
        chomp.Cancel();
        ImportedGameplayAnimationPatch.ForceCancelPostfix(chomp);

        var active = CreateFixture();
        var stalk = active.Actor.AddComponent<StalkConsumable>();
        stalk.SetPounceMode(true);
        stalk.InvokeLeap();
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        Check(Json(active.Driver).GetProperty("pounce_armed").GetBoolean(), "source pounce timer arms before source destruction");
        UnityEngine.Object.Destroy(active.Source);
        active.Actor.Tick();
        var afterDestroy = Json(active.Driver);
        Check(!afterDestroy.GetProperty("source_present").GetBoolean() &&
            !afterDestroy.GetProperty("pounce_armed").GetBoolean() &&
            !afterDestroy.GetProperty("culling_forced").GetBoolean(),
            "destroyed source clears timers/routing and restores culling");
        var jumpsBefore = Counter(active.Driver, "jumps");
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        Check(Counter(active.Driver, "jumps") == jumpsBefore,
            "a destroyed source stops only its own actor's visual routing");
        Check(ImportedGameplayAnimation.EnsureAttached(active.Actor, active.Visual, out _) &&
            ReferenceEquals(active.Driver, active.Actor.GetComponent<ImportedGameplayAnimation>()),
            "reapplying an animation-free visual clears routing without adding another controller");
    }

    private static void TestIndependentActorsAndBoundedHistory()
    {
        var first = CreateFixture();
        var second = CreateFixture();
        var stalk = first.Actor.AddComponent<StalkConsumable>();
        stalk.SetPounceMode(true);
        stalk.InvokeLeap();
        ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        Check(CurrentClip(first.Source) == "Pounce v4" && CurrentClip(second.Source) == "Idle_Cotton_Ringtail",
            "two actors keep independent visual animation clocks and state");

        for (var i = 0; i < 20; i++)
        {
            second.Driver.OnNativeBiteStarted();
            second.Driver.OnNativeBiteEnded(true);
        }
        using var diagnostics = JsonDocument.Parse(second.Driver.ToJson());
        var history = diagnostics.RootElement.GetProperty("history");
        Check(history.GetArrayLength() == 32 && diagnostics.RootElement.GetProperty("history_dropped").GetInt32() > 0,
            "transition history is a preallocated 32-entry ring with explicit dropped count");
        Check(diagnostics.RootElement.GetProperty("counters").GetProperty("bite_starts").GetInt32() == 20 &&
            diagnostics.RootElement.GetProperty("counters").GetProperty("bite_completes").GetInt32() == 20,
            "diagnostics expose bite start and completion counters");
    }

    private static void TestNoHookAllocations()
    {
        var fixture = CreateFixture();
        var stalk = fixture.Actor.AddComponent<StalkConsumable>();
        stalk.SetPounceMode(true);
        for (var i = 0; i < 32; i++) ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        var before = GC.GetAllocatedBytesForCurrentThread();
        for (var i = 0; i < 128; i++) ImportedGameplayAnimationPatch.LeapTowardPostfix(stalk);
        var allocated = GC.GetAllocatedBytesForCurrentThread() - before;
        Check(allocated == 0, $"warm pounce hooks allocate zero bytes (observed {allocated})");
    }

    private static void TestCallbackCanDestroyActor()
    {
        var fixture = CreateFixture();
        var chomper = fixture.Actor.AddComponent<Chomper>();
        chomper.StartChomp(new GameObject("food"), Identifiable.Id.Tabby, false, false, () => { },
            (_, _, _, _) => UnityEngine.Object.Destroy(fixture.Actor));
        ImportedGameplayAnimationPatch.StartChompPostfix(chomper);
        chomper.DestroyActorOnCompletion = true;
        chomper.BiteComplete(); // Native callback removes the actor before the patch postfix runs.
        ImportedGameplayAnimationPatch.BiteCompletePostfix(chomper);
        Check(chomper.NativeCompletionCalls == 1 && fixture.RootAnimator.cullingMode == AnimatorCullingMode.CullUpdateTransforms,
            "native completion callback may destroy actor safely and culling is restored");
    }

    private static void TestDisableReenableAndUnload(Fixture fixture)
    {
        var animation = fixture.Visual.GetComponent<Animation>()!;
        var playsBeforeDisable = animation.PlayCalls;
        fixture.Actor.SetActive(false);
        Check(fixture.RootAnimator.cullingMode == AnimatorCullingMode.CullUpdateTransforms &&
            fixture.ChildAnimator.cullingMode == AnimatorCullingMode.CullCompletely,
            "disable restores each actor Animator's exact captured culling mode");
        fixture.Actor.SetActive(true);
        Check(animation.PlayCalls == playsBeforeDisable + 1,
            "source component starts default once on actor re-enable");
        fixture.Actor.Tick();
        Check(animation.PlayCalls == playsBeforeDisable + 1 && CurrentClip(fixture.Source) == "Idle_Cotton_Ringtail",
            "driver enable observes source OnEnable idle without replaying or resetting it");
        Check(fixture.RootAnimator.cullingMode == AnimatorCullingMode.AlwaysAnimate &&
            fixture.ChildAnimator.cullingMode == AnimatorCullingMode.AlwaysAnimate,
            "reenabled actor restores the captured culling override");

        var other = new Harmony("test.other-harmony-owner");
        other.Patch(typeof(Chomper).GetMethod("BiteComplete", BindingFlags.Instance | BindingFlags.Public)!);
        ImportedGameplayAnimationPatch.Unload();
        Check(Harmony.Patches.Count == 1 && Harmony.Patches[0].owner == "test.other-harmony-owner",
            "unload removes only the gameplay patch owner's hooks");
        other.UnpatchAll(other.Id);
        Check(fixture.RootAnimator.cullingMode == AnimatorCullingMode.CullUpdateTransforms &&
            fixture.ChildAnimator.cullingMode == AnimatorCullingMode.CullCompletely,
            "plugin unload restores every live controller's exact original culling values");
        Check(!Json(fixture.Driver).GetProperty("culling_forced").GetBoolean(),
            "unloaded driver reports its culling override cleared");
    }

    private static void CheckPatchContracts()
    {
        var start = PatchFor("StartChomp");
        var complete = PatchFor("BiteComplete");
        var cancel = PatchFor("ForceCancelChomp");
        var leap = PatchFor("LeapToward");
        var random = PatchFor("Action");
        var move = PatchFor("MoveTowards");
        var grounded = PatchFor("OnGroundedRaycastResultReceived");
        Check(start != null && start.prefix == null && start.postfix != null,
            "Chomper.StartChomp is observation-only postfix");
        Check(complete != null && complete.prefix == null && complete.postfix != null &&
            cancel != null && cancel.prefix == null && cancel.postfix != null,
            "bite completion and cancellation are postfix-only and cannot block native paths");
        Check(leap != null && leap.prefix == null && leap.postfix != null,
            "LeapToward is postfix-only and can filter pounce mode after the native impulse");
        Check(random != null && random.prefix != null && random.postfix != null &&
            move != null && move.prefix != null && move.postfix != null,
            "jump hooks capture before/after values without replacing native methods");
        Check(grounded != null && grounded.prefix == null && grounded.postfix != null,
            "grounded callback is an observation-only postfix");
        Check(random?.prefix?.method.ReturnType == typeof(void) && move?.prefix?.method.ReturnType == typeof(void),
            "timer prefixes never return false or skip native gameplay");
    }

    private static Harmony.InstalledPatch? PatchFor(string methodName)
    {
        foreach (var patch in Harmony.Patches)
            if (patch.original.Name == methodName) return patch;
        return null;
    }

    private static Fixture CreateFixture(bool includePounce = true, bool includeBite = true)
    {
        var actor = new GameObject("imported-actor");
        actor.SetActive(true);
        var rootAnimator = actor.AddComponent<Animator>();
        rootAnimator.cullingMode = AnimatorCullingMode.CullUpdateTransforms;
        var nativeChild = new GameObject("native-skeleton");
        nativeChild.SetParent(actor.transform);
        nativeChild.SetActive(true);
        var childAnimator = nativeChild.AddComponent<Animator>();
        childAnimator.cullingMode = AnimatorCullingMode.CullCompletely;

        var visual = new GameObject(ImportedSpawnerName);
        visual.SetParent(actor.transform);
        visual.AddComponent<ImportedVisualMarker>();
        var visualAnimator = visual.AddComponent<Animator>();
        visualAnimator.cullingMode = AnimatorCullingMode.CullCompletely;
        Check(ImportedSourceAnimation.TryAttach(visual, Descriptor(includePounce, includeBite), out var problem),
            "fixture source-animation descriptor attaches: " + problem);
        visual.SetActive(true);
        var source = visual.GetComponent<ImportedSourceAnimation>()!;
        Check(ImportedGameplayAnimation.EnsureAttached(actor, visual, out problem),
            "fixture production gameplay driver attaches: " + problem);
        return new Fixture
        {
            Actor = actor,
            Visual = visual,
            Source = source,
            Driver = actor.GetComponent<ImportedGameplayAnimation>()!,
            RootAnimator = rootAnimator,
            ChildAnimator = childAnimator,
            VisualAnimator = visualAnimator
        };
    }

    private static Dictionary<string, object> Descriptor(bool includePounce, bool includeBite)
    {
        var clips = new List<object> { Clip("Idle_Cotton_Ringtail", 2f, true) };
        if (includePounce) clips.Add(Clip("Pounce v4", 0.5f, false));
        if (includeBite) clips.Add(Clip("Bite v4", 0.3f, false));
        if (includeBite) clips.Add(Clip("Digest v4", 0.8f, false));
        return new Dictionary<string, object>
        {
            ["default_clip"] = "Idle_Cotton_Ringtail",
            ["clips"] = clips
        };
    }

    private static Dictionary<string, object> Clip(string name, float duration, bool loop) =>
        new()
        {
            ["name"] = name,
            ["duration"] = duration,
            ["loop"] = loop,
            ["channels"] = new List<object>()
        };

    private static JsonElement Json(ImportedGameplayAnimation driver)
    {
        using var document = JsonDocument.Parse(driver.ToJson());
        return document.RootElement.Clone();
    }

    private static int Counter(ImportedGameplayAnimation driver, string name) =>
        Json(driver).GetProperty("counters").GetProperty(name).GetInt32();

    private static string CurrentClip(ImportedSourceAnimation source) =>
        source.TryReadPlayback(out var name, out _, out _) ? name : "";

    private static void Grounded(SlimeSubbehaviourPlexer plexer, RaycastHit result) => _groundedPostfix?.Invoke(plexer, result);

    private static void Check(bool condition, string message)
    {
        _checks++;
        if (condition) return;
        _failures++;
        Console.Error.WriteLine("FAIL: " + message);
    }

    private const string ImportedSpawnerName = "SlimeBridgeImportedVisual";
}

namespace SlimeBridge.SR1
{
    internal static class BridgeEntry { internal static bool LabRestPoseRequested = true; }
}
