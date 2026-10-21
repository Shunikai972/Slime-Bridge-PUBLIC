using System;
using System.Globalization;
using Il2CppMonomiPark.SlimeRancher.UI.IntroSequence;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Main-thread, volatile permission for one freshly created NEWGAME, not a save-name heuristic.
    /// Constructed only after ExecuteCreation's pending Begin completes with strict owned readback
    /// and a durable DONE. TryReadWorld remains the authority on every update, including false
    /// loadedGame, applied stock settings and current protected storage. Never replays permission.
    /// Per update: authority -> focus -> bounded frame read -> focus -> FULL authority again ->
    /// callback-free gate -> final native-only focus read -> callback-free gate -> one native call.
    /// The production final focus predicate is P/Invoke/Unity icalls only, not a user callback.
    /// Bounded diagnostics: arm/refusal, one foreground wait, first call, actual frame advances,
    /// and one retirement with totals. Root clearing is NOT evidence of restored controls.
    /// </summary>
    internal sealed class BackgroundIntroSkipper
    {
        private const float DeadlineSeconds = 300f;
        private const int MaxCalls = 512;
        private const int MaxFrames = 256;
        private readonly string _name;
        private readonly IntPtr _director;
        private readonly Action<string> _log;
        private readonly Func<bool> _background;
        private readonly float _started;
        private object? _witness;
        private IntPtr _scene, _saved, _cutscene, _root;
        private int _slot = -1, _calls, _advances, _lastFrame = -1, _lastIndex = -1, _lastCount = -1;
        private bool _stopped, _armed, _waitLogged;
        internal bool Stopped => _stopped;

        internal BackgroundIntroSkipper(string name, IntPtr director, Action<string> log, Func<bool>? background = null)
        {
            _name = name; _director = director; _log = log;
            _background = background ?? BackgroundGameFocus.IsBackground;
            _started = UnityEngine.Time.realtimeSinceStartup;
            if (director == IntPtr.Zero) { Stop("no owned director when arming"); return; }
            try
            {
                // Bind the witnessed epoch NOW. The native root registers itself in Awake while the
                // gameplay scene loads, before completion can be proven; an absent root/cutscene
                // here is refused, so a later-attached root is never adopted.
                if (!CounterpartCreator.TryReadWorld(out var world) || world == null || _stopped)
                { Stop("current owned witness unavailable when arming"); return; }
                _witness = CounterpartCreator.CurrentWorldWitness;
                var saved = world.Director.SavedGame;
                var cutscene = world.Scene.CutsceneDirector;
                var root = cutscene != null && cutscene ? cutscene._uiRoot : null;
                if (_witness == null || world.Director.Pointer != director || world.Name != name || saved == null || saved.GameState == null)
                { Stop("current owned witness mismatched when arming"); return; }
                if (cutscene == null || !cutscene || cutscene.Pointer == IntPtr.Zero)
                { Stop("cutscene director absent when arming"); return; }
                if (root == null || !root || root.Pointer == IntPtr.Zero)
                { Stop("intro root absent when arming"); return; }
                _scene = world.Scene.Pointer; _saved = saved.Pointer; _slot = saved.GameState.SaveSlotIndex;
                _cutscene = cutscene.Pointer; _root = root.Pointer;
                var arming = AuthorityFailure();
                if (arming != null) { Stop("identity changed while arming: " + arming); return; }
                _lastCount = root._frameConfigs?.Count ?? -1;
                _lastIndex = root._frameIndex;
                _armed = true;
                _log("background intro armed save=" + _name + " root=0x" + ((long)_root).ToString("x", CultureInfo.InvariantCulture) +
                     " frames=" + _lastCount.ToString(CultureInfo.InvariantCulture) + " index=" + _lastIndex.ToString(CultureInfo.InvariantCulture));
            }
            catch (Exception ex) { Stop("native read exception while arming: " + Describe(ex)); }
        }

        internal void Cancel() => Stop("lifecycle cancellation");

        internal void Tick()
        {
            if (_stopped) return;
            try
            {
                var now = UnityEngine.Time.realtimeSinceStartup;
                var frame = UnityEngine.Time.frameCount;
                if (!float.IsFinite(now) || !float.IsFinite(_started) || now < _started || now - _started >= DeadlineSeconds ||
                    frame < _lastFrame || _calls >= MaxCalls) { Stop("deadline/epoch/call budget"); return; }
                if (frame == _lastFrame) return; // At most one native invocation per ordinary Unity frame.
                _lastFrame = frame;
                // Authority first: ownership/identity losses retire permission even while foreground.
                if (!Authorized()) return;
                if (!Background()) return; // Foreground/unknown: wait, no new native call.
                var changed = AuthorityFailure(); // Focus seam may call back: identity loss is permanent.
                if (changed != null) { Stop("during focus check: " + changed); return; }
                var root = CurrentRoot();
                if (root == null || !Eligible(root, out var index, out var frames, out var count, out var version) || _stopped) return;
                if (!Background()) return; // Windows + Unity, fail-closed, rechecked after the frame read.
                // FULL authority after every callback-capable seam above (frame list, focus) ...
                if (!Authorized()) return;
                // ... then only callback-free reads until the single native call. Callbacks raised
                // inside that authority read are caught by cancellation, witness token and identity.
                var failure = AuthorityFailure(); if (failure != null) { Stop("before native advance: " + failure); return; }
                if (Gate(frames, count, index, version) == null) return; // Fresh re-read next frame.
                // Final focus check follows the full authority read. Production focus uses only
                // P/Invoke and Unity icalls; repeat callback-free checks after the injected seam.
                if (!Background()) return;
                failure = AuthorityFailure(); if (failure != null) { Stop("after final focus check: " + failure); return; }
                var current = Gate(frames, count, index, version);
                if (current == null) return;
                _calls++; // Reserve budget BEFORE the native call (including an exception).
                current.TryToIterateFrame(index);
                var after = current._frameIndex;
                _lastIndex = after; _lastCount = count;
                if (_calls == 1)
                    _log("background intro first native call frameIndex=" + index.ToString(CultureInfo.InvariantCulture) +
                         " count=" + count.ToString(CultureInfo.InvariantCulture));
                if (after != index)
                {
                    _advances++;
                    _log("background intro advanced frameIndex=" + index.ToString(CultureInfo.InvariantCulture) + "->" +
                         after.ToString(CultureInfo.InvariantCulture) + " count=" + count.ToString(CultureInfo.InvariantCulture) +
                         " calls=" + _calls.ToString(CultureInfo.InvariantCulture));
                }
                // Native interrupt / EndFrame / StartFrame / EndCoroutine own all subsequent work.
                // A scheduled native transition may finish after a later foreground switch. We
                // only guarantee no NEW invocation while foreground; we never cancel its coroutine.
            }
            catch (Exception ex) { Stop("native read/advance exception: " + Describe(ex)); }
        }

        private bool Background()
        {
            if (_background() && !_stopped) return true;
            if (!_stopped && !_waitLogged) { _waitLogged = true; _log("background intro waiting: game window is foreground or focus unknown"); }
            return false;
        }

        // Full authority read (callback-capable), then callback-free identity confirmation.
        private bool Authorized()
        {
            if (_stopped) return false;
            if (!CounterpartCreator.TryReadWorld(out var world) || world == null)
            { if (!_stopped) Stop("current owned completion witness unavailable or changed"); return false; }
            if (_stopped) return false; // Revoked by a callback during the authority read.
            var failure = AuthorityFailure(); if (failure != null) { Stop(failure); return false; }
            return true;
        }

        // Callback-free: managed fields, native field/property reads and native getters only.
        // Returns the first failing predicate (runtime diagnostics) or null when still current.
        // SR2-R5 boundary: settings/storage cannot change between the final full authority read
        // and native call except through a callback. Successful NativeStorageBinding.IsCurrent
        // reads native fields only (revocation Emit returns false); BackgroundGameFocus.IsBackground
        // reads P/Invoke/Unity icalls only. Keep both production predicates free of game mutations.
        // Injected focus callbacks that rewrite settings/storage at the final read are unsupported;
        // cancellations, witness/identity changes and frame-list changes are still rechecked.
        private string? AuthorityFailure()
        {
            if (_stopped) return "cancelled";
            if (_witness == null || !ReferenceEquals(CounterpartCreator.CurrentWorldWitness, _witness)) return "owned witness cleared/replaced";
            var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
            var scene = Il2Cpp.SceneContext.Instance;
            var loader = Il2Cpp.SystemContext.Instance?.SceneLoader;
            var saved = director?.SavedGame;
            var state = saved?.GameState;
            if (director == null || director.Pointer != _director || director.loadedGame || director.newGameMetadata != null) return "director/flag/metadata";
            if (saved == null || saved.Pointer != _saved || saved.GetName() != _name || state == null || state.SaveSlotIndex != _slot) return "saved game identity";
            if (scene == null || !scene || scene.Pointer != _scene) return "scene identity";
            if (loader == null || loader.IsSceneLoadInProgress || !loader.IsCurrentSceneGroupGameplay() ||
                loader._currentSceneGroup?.name?.StartsWith("MainMenu", StringComparison.OrdinalIgnoreCase) == true) return "loader not gameplay-idle";
            if (scene.PlayerState == null || scene.PlayerState.GetCurrHealth() <= 0 || scene.Player == null || !scene.Player.activeInHierarchy) return "player readiness";
            var cutscene = scene.CutsceneDirector;
            if (cutscene == null || !cutscene || cutscene.Pointer != _cutscene) return "cutscene director identity";
            var root = cutscene._uiRoot;
            if (root == null || !root || root.Pointer != _root) return "intro root cleared/replaced (not control-release evidence)";
            return null;
        }

        // Current managed wrapper of the bound native root (wrappers may differ; pointers may not).
        private IntroSequenceUIRoot? CurrentRoot()
        {
            var cutscene = Il2Cpp.SceneContext.Instance?.CutsceneDirector;
            var root = cutscene != null && cutscene ? cutscene._uiRoot : null;
            return root != null && root && root.Pointer == _root ? root : null;
        }

        // Pointer, count, index AND version protect against an entry replaced by an earlier
        // callback. Do not call the callback-capable indexer after the final authority read.
        private IntroSequenceUIRoot? Gate(IntPtr frames, int count, int index, int version)
        {
            var current = CurrentRoot();
            if (current == null) { Stop("before native advance: bound root wrapper unavailable"); return null; }
            var currentFrames = current._frameConfigs;
            if (!UiCurrent(current) || currentFrames == null || currentFrames.Pointer != frames ||
                currentFrames.Count != count || currentFrames._version != version || current._frameIndex != index) return null;
            return current;
        }

        private static bool UiCurrent(IntroSequenceUIRoot root)
        {
            var ui = Il2Cpp.GameContext.Instance?.UIDirector;
            return root && root.enabled && root.isActiveAndEnabled && root.gameObject != null && root.gameObject &&
                root.gameObject.activeInHierarchy && ui != null && ui.Pointer != IntPtr.Zero && ui &&
                ui.TopStackableUI?.Pointer == root.Pointer && root._isInputEnabled && root._currentTween == null;
        }

        private static bool Eligible(IntroSequenceUIRoot root, out int index, out IntPtr list, out int count, out int version)
        {
            index = -1; list = IntPtr.Zero; count = 0; version = 0;
            if (!UiCurrent(root)) return false;
            var frames = root._frameConfigs;
            if (frames == null || frames.Pointer == IntPtr.Zero) return false;
            list = frames.Pointer;
            count = frames.Count;
            version = frames._version; // Before the callback-capable element read.
            index = root._frameIndex;
            if (count <= 0 || count > MaxFrames || index < 0 || index >= count) return false;
            var config = frames[index]; // Callback-capable native accessor; final gates re-read after it.
            var panel = config?.TextPanelView;
            return config != null && config.Pointer != IntPtr.Zero && panel != null && panel && panel.Pointer != IntPtr.Zero;
        }

        private static string Describe(Exception ex)
        {
            var text = ex.GetType().Name + ": " + (ex.Message ?? "");
            text = text.Replace((char)13, ' ').Replace((char)10, ' ');
            return text.Length <= 160 ? text : text.Substring(0, 160) + "...";
        }

        private void Stop(string reason)
        {
            if (_stopped) return; // One retirement line per helper, including cancellation.
            _stopped = true;
            _witness = null; // Release the old epoch even if logging fails.
            var elapsed = UnityEngine.Time.realtimeSinceStartup - _started;
            _log("background intro " + (_armed ? "retired: " : "arm refused: ") + reason +
                 " calls=" + _calls.ToString(CultureInfo.InvariantCulture) + " advances=" + _advances.ToString(CultureInfo.InvariantCulture) +
                 " elapsed=" + elapsed.ToString("F1", CultureInfo.InvariantCulture) + "s lastIndex=" +
                 _lastIndex.ToString(CultureInfo.InvariantCulture) + "/" + _lastCount.ToString(CultureInfo.InvariantCulture));
            // Root-clear is earlier than DeathObscurer's control-release callback. No completion,
            // player-ready, or controls-restored status is published by this helper.
        }
    }
}
