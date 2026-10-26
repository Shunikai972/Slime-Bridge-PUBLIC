using System;
using System.Collections.Generic;
using System.Reflection;
using DG.Tweening;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace SlimeBridge.SR1
{
    /// <summary>One fresh lab creation only. Observe native ownership, then finish its original tween
    /// from ordinary SRML Tick. Never emulate UI destruction, input, pause, or tutorial cleanup.</summary>
    internal sealed class BackgroundIntroSkipper
    {
        private const float DeadlineSeconds = 240f;
        private const int TickBudget = 30000;
        private static BackgroundIntroSkipper? _captureOwner;
        private static Harmony? _harmony;
        private readonly bool _authorized;
        private readonly Func<bool> _background = BackgroundGameFocus.IsBackground;
        private NativeContract? _contract;
        private CounterpartCreator? _creator;
        private AutoSaveDirector? _director;
        private object? _savedGame;
        private string _name = "";
        private SceneContext? _scene;
        private Scene _nativeScene;
        private GameObject? _root;
        private IntroUI? _owner;
        private GameObject? _prefab;
        private Action<Destroyer.Metadata>? _monitor;
        private Sequence? _sequence;
        private int _rootId, _captureFrame, _dispatchFrame, _ticks;
        private float _started;
        private bool _used, _terminal, _dispatched, _loaded;

        internal BackgroundIntroSkipper(bool authorized) { _authorized = authorized; }

        internal void Arm(CounterpartCreator creator)
        {
            if (_used || _terminal || !_authorized) return;
            _used = true; // Failures, cancellation, or identity uncertainty never re-arm this owner.
            try
            {
                _contract = _contract ?? NativeContract.Resolve();
                if (_contract == null || creator.IntroFailed) { Cancel(); return; }
                if (_harmony == null)
                {
                    var harmony = new Harmony("slimebridge.sr1.background-intro");
                    harmony.Patch(_contract.Monitor, postfix: new HarmonyMethod(typeof(BackgroundIntroSkipper), nameof(MonitorPostfix)));
                    _harmony = harmony;
                }
                _creator = creator;
                _director = creator.IntroDirector;
                _savedGame = creator.IntroSavedGame;
                _name = creator.IntroGameName;
                _started = Time.realtimeSinceStartup;
                if (_director == null || _savedGame == null || _name != CounterpartCreator.GameNameFor(creator.IntroCreationId)) { Cancel(); return; }
                if (_captureOwner != null && !ReferenceEquals(_captureOwner, this)) { Cancel(); return; }
                _captureOwner = this;
            }
            catch (Exception) { Cancel(); }
        }

        // A normal postfix runs only after native registration returned; no tween work on this stack.
        private static void MonitorPostfix(UnityEngine.Object instance, Action<Destroyer.Metadata> action)
        {
            var owner = _captureOwner;
            if (owner == null || owner._terminal) return;
            try { owner.Capture(instance, action); }
            catch (Exception) { owner.Cancel(); }
        }

        private void Capture(UnityEngine.Object instance, Action<Destroyer.Metadata> action)
        {
            var c = _contract!;
            if (action == null || action.GetInvocationList().Length != 1 || action.Method != c.MonitorCallback) return;
            // Only the pinned native closure is eligible; no root-name, Find, or UI lifecycle fallback.
            if (action.Target == null || action.Target.GetType() != c.MonitorCallback.DeclaringType ||
                !ReferenceEquals(c.ClosureDirector.GetValue(action.Target), _director)) { Cancel(); return; }
            var scene = c.ClosureContext.GetValue(action.Target) as SceneContext;
            var root = instance as GameObject;
            if (_root != null || scene == null || root == null || !ReferenceEquals(scene, SRSingleton<SceneContext>.Instance) ||
                !Identity() || !World(scene, root, out var nativeScene)) { Cancel(); return; }
            var prefab = scene.GameModeConfig == null ? null : scene.GameModeConfig.GetModeSettings()?.newGamePrefab;
            if (prefab == null || ReferenceEquals(prefab, root) || !ValidComposition(prefab, null) || !ValidComposition(root, null)) { Cancel(); return; }
            var ui = root.GetComponent<IntroUI>();
            if (ui == null || !ValidComposition(root, ui)) { Cancel(); return; }
            _scene = scene; _nativeScene = nativeScene; _root = root; _rootId = root.GetInstanceID();
            _owner = ui; _prefab = prefab; _monitor = action; _captureFrame = Time.frameCount;
        }

        internal void Tick()
        {
            if (!_used || _terminal || _creator == null) return;
            try
            {
                var now = Time.realtimeSinceStartup;
                if (float.IsNaN(now) || float.IsInfinity(now) || now < _started || now - _started > DeadlineSeconds ||
                    ++_ticks > TickBudget || _creator.IntroFailed || !DirectorIdentity()) { Cancel(); return; }
                if (_scene == null)
                {
                    // Scene transition is pending; absent native monitor is never permission to search.
                    if (!_director!.IsLoadingGame()) Cancel();
                    return;
                }
                if (!Identity() || !ReferenceEquals(_scene, SRSingleton<SceneContext>.Instance) ||
                    !SceneEpoch(_scene, out var active) || active != _nativeScene || Levels.isMainMenu() || Levels.isSpecial()) { Cancel(); return; }
                if (_director!.IsLoadingGame()) { if (_loaded) Cancel(); return; }
                _loaded = true;
                if (_dispatched)
                {
                    // Unity root/component destruction is deferred. Observe both exact captured
                    // objects and the original sequence on a later frame, without forcing cleanup.
                    if (Time.frameCount > _dispatchFrame && _root == null && _owner == null &&
                        _sequence != null && (!_sequence.IsActive() || _sequence.IsComplete())) Cancel();
                    else if (_root == null || _owner == null || _root.GetInstanceID() != _rootId || _root.scene != active) Cancel();
                    return;
                }
                if (!World(_scene, _root, out active)) { Cancel(); return; }
                if (Time.frameCount <= _captureFrame) return;
                if (!BoundOwner()) { Cancel(); return; }
                if (!_background()) return;
                var sequence = SelectSequence(out var invalid);
                if (invalid) { Cancel(); return; }
                if (sequence == null) return; // Native animation not yet observable; deadline remains bounded.
                // Pinned DOTween ignores OnComplete while isUpdateLoop=true, even withCallbacks=true.
                var updating = _contract!.UpdateLoop.GetValue(null);
                if (!(updating is bool) || (bool)updating) return;
                // Revalidate after enumeration, then recheck read-only focus immediately before dispatch.
                if (!Identity() || _director.IsLoadingGame() || !BoundOwner() ||
                    !ReferenceEquals(_scene, SRSingleton<SceneContext>.Instance) ||
                    !World(_scene, _root, out active) || active != _nativeScene || !OriginalCallback(sequence) ||
                    !sequence.IsActive() || sequence.IsComplete() || _creator.IntroFailed) { Cancel(); return; }
                if (!_background()) return;
                _sequence = sequence; _dispatched = true; _dispatchFrame = Time.frameCount;
                sequence.Complete(withCallbacks: true); // Sole mutation: native original endReached/Close/Destroyer.
                if (sequence.IsActive() && !sequence.IsComplete() || !(_contract.EndReached.GetValue(_owner) is bool ended) || !ended) Cancel();
            }
            catch (Exception) { Cancel(); }
        }

        private bool DirectorIdentity()
        {
            var game = SRSingleton<GameContext>.Instance;
            return _director != null && game != null && ReferenceEquals(game.AutoSaveDirector, _director) &&
                ReferenceEquals(_director.SavedGame, _savedGame) && _director.SavedGame != null;
        }

        private bool Identity()
        {
            return DirectorIdentity() && string.Equals(_director!.SavedGame.GetName(), _name, StringComparison.Ordinal) && _director.IsNewGame();
        }

        private static bool SceneEpoch(SceneContext scene, out Scene active)
        {
            active = SceneManager.GetActiveScene();
            return scene != null && active.IsValid() && active.isLoaded &&
                string.Equals(active.name, "worldGenerated", StringComparison.Ordinal) && scene.gameObject.scene == active;
        }

        private static bool World(SceneContext scene, GameObject? root, out Scene active)
        {
            return SceneEpoch(scene, out active) && root != null && root.scene == active;
        }

        private bool BoundOwner()
        {
            return _root != null && _root.GetInstanceID() == _rootId && _root.activeInHierarchy &&
                _owner != null && _owner.enabled && ReferenceEquals(_owner.gameObject, _root) &&
                _prefab != null && ReferenceEquals(_scene!.GameModeConfig.GetModeSettings().newGamePrefab, _prefab) &&
                _monitor != null && _monitor.GetInvocationList().Length == 1 && _monitor.Method == _contract!.MonitorCallback &&
                ReferenceEquals(_contract.ClosureContext.GetValue(_monitor.Target), _scene) &&
                ReferenceEquals(_contract.ClosureDirector.GetValue(_monitor.Target), _director) && ValidComposition(_root, _owner);
        }

        private bool ValidComposition(GameObject root, IntroUI? expected)
        {
            var owners = root.GetComponentsInChildren<IntroUI>(true);
            if (owners.Length != 1 || owners[0].GetType() != typeof(IntroUI) ||
                !ReferenceEquals(owners[0].gameObject, root) || expected != null && !ReferenceEquals(owners[0], expected)) return false;
            var ui = owners[0];
            if (ui.background == null || !ui.background.transform.IsChildOf(root.transform) ||
                ui.introLine1 == null || ui.introLine2 == null || ui.introLine3 == null ||
                !ui.introLine1.transform.IsChildOf(root.transform) || !ui.introLine2.transform.IsChildOf(root.transform) ||
                !ui.introLine3.transform.IsChildOf(root.transform) || root.GetComponent<CanvasGroup>() == null) return false;
            // Awake initializes the private CanvasGroup only on a live instance, not on the template asset.
            return expected == null || ReferenceEquals(_contract!.IntroCanvas.GetValue(ui), root.GetComponent<CanvasGroup>());
        }

        private Sequence? SelectSequence(out bool invalid)
        {
            invalid = false;
            Sequence? candidate = null;
            var seen = new HashSet<Tween>();
            var lists = new[] { DOTween.PlayingTweens(), DOTween.PausedTweens() };
            foreach (var list in lists)
            {
                if (list == null) continue;
                foreach (var tween in list)
                {
                    if (tween == null || !seen.Add(tween) || tween.onComplete == null) continue;
                    var owns = false;
                    foreach (var cb in tween.onComplete.GetInvocationList()) if (ReferenceEquals(cb.Target, _owner)) owns = true;
                    if (!owns) continue;
                    if (!(tween is Sequence sequence) || !OriginalCallback(sequence) || !sequence.IsActive() || sequence.IsComplete() || candidate != null)
                    { invalid = true; return null; }
                    candidate = sequence;
                }
            }
            return candidate;
        }

        private bool OriginalCallback(Sequence sequence)
        {
            var cb = sequence.onComplete;
            return cb != null && cb.GetInvocationList().Length == 1 && ReferenceEquals(cb.Target, _owner) &&
                cb.Method == _contract!.IntroCallback && cb.Method.DeclaringType == typeof(IntroUI) && cb.Method.Module == typeof(IntroUI).Module;
        }

        internal void CreationFinished(CommandResult result)
        {
            if (_creator == null || result.OpId != _creator.IntroOpId || !result.Ok || result.Uncertain ||
                !result.Persisted || result.Verified != true || result.SaveId != _name) Cancel();
            // Success retains this independent owner until native finish/disappearance or the deadline.
        }

        internal void Cancel()
        {
            _terminal = true;
            if (ReferenceEquals(_captureOwner, this)) _captureOwner = null;
        }

        // Exact offline metadata anchors: unknown builds fail closed. Fixtures replace only this
        // metadata boundary by reflection; there is no production switch to relax it.
        private sealed class NativeContract
        {
            internal MethodInfo Monitor = null!, MonitorCallback = null!, IntroCallback = null!;
            internal FieldInfo ClosureContext = null!, ClosureDirector = null!, IntroCanvas = null!, EndReached = null!, UpdateLoop = null!;
            internal static NativeContract? Resolve()
            {
                var module = typeof(IntroUI).Module;
                if (module.ModuleVersionId != new Guid("d6c74e9f-3d65-49d7-9370-f55f30487405") ||
                    typeof(AutoSaveDirector).Module != module || typeof(Destroyer).Module != module ||
                    typeof(DOTween).Module.ModuleVersionId != new Guid("4379fcb5-ffcd-422a-bdab-e963856ef063")) return null;
                var c = new NativeContract
                {
                    Monitor = (MethodInfo)module.ResolveMethod(0x0600062E),
                    MonitorCallback = (MethodInfo)module.ResolveMethod(0x06000541),
                    IntroCallback = (MethodInfo)module.ResolveMethod(0x06001DF5),
                    ClosureContext = module.ResolveField(0x04000548),
                    ClosureDirector = module.ResolveField(0x04000547),
                    IntroCanvas = typeof(IntroUI).GetField("introCanvasGroup", BindingFlags.Instance | BindingFlags.NonPublic),
                    EndReached = typeof(IntroUI).GetField("endReached", BindingFlags.Instance | BindingFlags.NonPublic),
                    UpdateLoop = (typeof(DOTween).Assembly.GetType("DG.Tweening.Core.TweenManager")?.GetField("isUpdateLoop", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic))!
                };
                var closure = module.ResolveType(0x020000E0);
                if (c.Monitor.DeclaringType != typeof(Destroyer) || !c.Monitor.IsStatic || c.Monitor.ReturnType != typeof(void) ||
                    !Parameters(c.Monitor, typeof(UnityEngine.Object), typeof(Action<Destroyer.Metadata>)) ||
                    c.MonitorCallback.DeclaringType != closure || c.MonitorCallback.IsStatic || c.MonitorCallback.ReturnType != typeof(void) ||
                    !Parameters(c.MonitorCallback, typeof(Destroyer.Metadata)) ||
                    c.ClosureContext.DeclaringType != closure || c.ClosureContext.FieldType != typeof(SceneContext) || c.ClosureContext.IsStatic ||
                    c.ClosureDirector.DeclaringType != closure || c.ClosureDirector.FieldType != typeof(AutoSaveDirector) || c.ClosureDirector.IsStatic ||
                    c.IntroCallback.DeclaringType != typeof(IntroUI) || c.IntroCallback.IsStatic || c.IntroCallback.ReturnType != typeof(void) || !Parameters(c.IntroCallback) ||
                    c.IntroCanvas == null || c.IntroCanvas.FieldType != typeof(CanvasGroup) || c.EndReached == null || c.EndReached.FieldType != typeof(bool) ||
                    c.UpdateLoop == null || !c.UpdateLoop.IsStatic || c.UpdateLoop.FieldType != typeof(bool)) return null;
                return c;
            }
            private static bool Parameters(MethodInfo method, params Type[] types)
            {
                var p = method.GetParameters(); if (p.Length != types.Length) return false;
                for (var i = 0; i < p.Length; ++i) if (p[i].ParameterType != types[i]) return false;
                return true;
            }
        }
    }
}
