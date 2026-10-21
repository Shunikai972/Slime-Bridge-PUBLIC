using System;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Visual-only state driver for one imported actor. Native Chomper/physics/Animator behavior
    /// remains authoritative; this component routes verified native transitions to clips on the
    /// actor's marked imported visual and never calls back into gameplay.
    /// </summary>
    internal sealed class ImportedGameplayAnimation : MonoBehaviour
    {
        private const string PounceClipName = "Pounce v4";
        private const string BiteClipName = "Bite v4";
        private const string DigestClipName = "Digest v4";
        private const int HistoryCapacity = 32;
        private const int ControllerCapacity = 256;
        private const int TransformBudget = 4096;
        private const int AnimatorCapacity = 256;

        private enum VisualState : byte { None, Idle, Pounce, Bite, Digest }
        private enum Transition : byte
        {
            Idle, BiteStart, BiteComplete, BiteCancel, JumpObserved, PounceStart,
            PouncePlayFailed, Airborne, Landing, PounceFallback, DefaultPlayFailed,
            SourceChanged, SourceUnavailable, Disabled, Unloaded
        }

        private struct HistoryEntry
        {
            internal float Time;
            internal Transition Kind;
            internal VisualState State;
        }

        // This is only an unload cleanup index. Harmony routes through GetComponent on the actor
        // that emitted the native event; no process-wide actor lookup occurs on a hook or tick.
        private static readonly WeakReference<ImportedGameplayAnimation>?[] Controllers =
            new WeakReference<ImportedGameplayAnimation>?[ControllerCapacity];
        private static int _controllerCount;

        private readonly HistoryEntry[] _history = new HistoryEntry[HistoryCapacity];
        private int _historyNext;
        private int _historyCount;
        private int _historyTotal;

        private ImportedSourceAnimation? _source;
        private Animator[]? _nativeAnimators;
        private AnimatorCullingMode[]? _originalCulling;
        private int _nativeAnimatorCount;
        private bool _hasCullingSnapshot;
        private bool _cullingApplied;
        private bool _initialized;
        private bool _shutdown;
        private bool _sourceUnavailable;
        private bool _idlePending;
        private bool _biteActive;
        private bool _pounceArmed;
        private bool _sawAirborne;
        private float _pounceDeadline;
        private VisualState _state;
        // A one-shot clip (bite, digest) is played to its end: native completion events arrive earlier than the source
        // clip finishes (quick chomps are near-instant), and cutting to idle there made the eating motion invisible.
        private string? _holdClip;
        private float _holdDeadline;
        private bool _digestPending;
        private int _digestStarts;

        private int _biteStarts;
        private int _biteCompletes;
        private int _biteCancels;
        private int _jumps;
        private int _landings;
        private int _fallbackCompletions;
        private int _playFailures;
        private int _updateErrors;

        /// <summary>Attach only to an active actor with the explicit imported-visual marker.
        /// Reapplication with the same source is a no-op and preserves all animation clocks.</summary>
        internal static bool EnsureAttached(GameObject actorRoot, GameObject importedVisual, out string problem)
        {
            problem = "";
            if (actorRoot == null || importedVisual == null)
            {
                problem = "actor and imported visual are required";
                return false;
            }
            if (importedVisual.transform.parent != actorRoot.transform ||
                importedVisual.GetComponent<ImportedVisualMarker>() == null)
            {
                problem = "a direct, marked imported visual is required";
                return false;
            }

            var source = importedVisual.GetComponent<ImportedSourceAnimation>();
            var existing = actorRoot.GetComponent<ImportedGameplayAnimation>();
            if (source == null)
            {
                if (existing != null) existing.ClearSource();
                return true; // No descriptor means no gameplay driver is added.
            }
            if (existing != null)
            {
                try { return existing.AttachSource(source, out problem); }
                catch (Exception error)
                {
                    existing.ClearSource();
                    problem = "gameplay animation reattach failed: " + error.GetType().Name;
                    return false;
                }
            }
            if (!actorRoot.activeInHierarchy)
            {
                problem = "inactive actor roots are not gameplay instances";
                return false;
            }

            ImportedGameplayAnimation? created = null;
            try
            {
                created = actorRoot.AddComponent<ImportedGameplayAnimation>();
                if (created.Initialize(source, out problem)) return true;
                UnityEngine.Object.Destroy(created);
                return false;
            }
            catch (Exception error)
            {
                if (created != null) UnityEngine.Object.Destroy(created);
                problem = "gameplay animation attach failed: " + error.GetType().Name;
                return false;
            }
        }

        private bool Initialize(ImportedSourceAnimation source, out string problem)
        {
            problem = "";
            if (source == null || !source.PrepareForGameplay(out problem)) return false;
            if (!CaptureAndApplyCulling(source.gameObject))
            {
                problem = "native Animator traversal exceeded its bound";
                return false;
            }
            if (!RegisterController(this))
            {
                RestoreCulling(false);
                problem = "gameplay animation controller capacity reached";
                return false;
            }

            _source = source;
            _initialized = true;
            _sourceUnavailable = false;
            _idlePending = true;
            Record(Transition.SourceChanged);
            QueueDefaultIdle(); // The source player's OnEnable normally started this already.
            return true;
        }

        private bool AttachSource(ImportedSourceAnimation source, out string problem)
        {
            problem = "";
            if (_shutdown) return false;
            if (ReferenceEquals(_source, source)) return true;
            if (source == null || !source.PrepareForGameplay(out problem))
            {
                ClearSource();
                return false;
            }

            _source = source;
            _sourceUnavailable = false;
            _biteActive = false;
            _pounceArmed = false;
            _sawAirborne = false;
            _state = VisualState.None;
            _idlePending = true;
            if (_hasCullingSnapshot) ReapplyCulling();
            else if (!CaptureAndApplyCulling(source.gameObject))
            {
                _source = null;
                _idlePending = false;
                problem = "native Animator traversal exceeded its bound";
                return false;
            }
            Record(Transition.SourceChanged);
            QueueDefaultIdle();
            return true;
        }

        private static bool RegisterController(ImportedGameplayAnimation controller)
        {
            var live = 0;
            for (var i = 0; i < _controllerCount; i++)
            {
                var weak = Controllers[i];
                if (weak == null || !weak.TryGetTarget(out var target) || target == null) continue;
                Controllers[live++] = weak;
            }
            for (var i = live; i < _controllerCount; i++) Controllers[i] = null;
            _controllerCount = live;
            if (_controllerCount >= Controllers.Length) return false;
            Controllers[_controllerCount++] = new WeakReference<ImportedGameplayAnimation>(controller);
            return true;
        }

        private static void UnregisterController(ImportedGameplayAnimation controller)
        {
            for (var i = 0; i < _controllerCount; i++)
            {
                var weak = Controllers[i];
                if (weak == null || !weak.TryGetTarget(out var target) || !ReferenceEquals(target, controller)) continue;
                for (var j = i + 1; j < _controllerCount; j++) Controllers[j - 1] = Controllers[j];
                Controllers[--_controllerCount] = null;
                return;
            }
        }

        /// <summary>Called during plugin unload; the registry is weak and capped, and every actor
        /// restores the exact culling modes captured when its source visual was attached.</summary>
        internal static void RestoreAllForUnload()
        {
            for (var i = 0; i < _controllerCount; i++)
            {
                var weak = Controllers[i];
                if (weak != null && weak.TryGetTarget(out var controller) && controller != null)
                    controller.ShutdownForUnload();
                Controllers[i] = null;
            }
            _controllerCount = 0;
        }

        private bool CaptureAndApplyCulling(GameObject visual)
        {
            var stack = new Transform[TransformBudget];
            var animators = new Animator[AnimatorCapacity];
            var original = new AnimatorCullingMode[AnimatorCapacity];
            var stackCount = 0;
            var animatorCount = 0;
            var visited = 0;
            stack[stackCount++] = transform;
            while (stackCount > 0)
            {
                if (visited++ >= TransformBudget) return false;
                var current = stack[--stackCount];
                if (current == null || current.gameObject == visual) continue;
                var animator = current.gameObject.GetComponent<Animator>();
                if (animator != null)
                {
                    if (animatorCount >= AnimatorCapacity) return false;
                    animators[animatorCount] = animator;
                    original[animatorCount++] = animator.cullingMode;
                }
                var childCount = current.childCount;
                if (stackCount + childCount > TransformBudget) return false;
                for (var child = 0; child < childCount; child++) stack[stackCount++] = current.GetChild(child);
            }

            _nativeAnimators = animators;
            _originalCulling = original;
            _nativeAnimatorCount = animatorCount;
            _hasCullingSnapshot = true;
            ReapplyCulling();
            return true;
        }

        private void ReapplyCulling()
        {
            if (!_hasCullingSnapshot || _nativeAnimators == null) return;
            for (var i = 0; i < _nativeAnimatorCount; i++)
            {
                var animator = _nativeAnimators[i];
                if (animator != null) animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            }
            _cullingApplied = true;
        }

        private void RestoreCulling(bool keepSnapshot)
        {
            if (_hasCullingSnapshot && _nativeAnimators != null && _originalCulling != null)
            {
                for (var i = 0; i < _nativeAnimatorCount; i++)
                {
                    var animator = _nativeAnimators[i];
                    if (animator != null) animator.cullingMode = _originalCulling[i];
                }
            }
            _cullingApplied = false;
            if (keepSnapshot) return;
            _nativeAnimators = null;
            _originalCulling = null;
            _nativeAnimatorCount = 0;
            _hasCullingSnapshot = false;
        }

        private void QueueDefaultIdle()
        {
            var source = _source;
            if (source == null || !source.isActiveAndEnabled)
            {
                _idlePending = true;
                return;
            }
            if (source.TryReadPlayback(out var current, out _, out _) &&
                string.Equals(current, source.DefaultClipName, StringComparison.Ordinal))
            {
                _state = VisualState.Idle;
                _idlePending = false;
                Record(Transition.Idle);
                return;
            }
            _idlePending = true;
        }

        private void PlayDefaultIdle()
        {
            var source = _source;
            _idlePending = false;
            if (source == null || !source.isActiveAndEnabled) return;
            if (HoldActive()) { _idlePending = true; return; }
            if (source.TryReadPlayback(out var current, out _, out _) &&
                string.Equals(current, source.DefaultClipName, StringComparison.Ordinal))
            {
                _state = VisualState.Idle;
                Record(Transition.Idle);
                return;
            }
            if (source.TryPlay(source.DefaultClipName, out _))
            {
                _state = VisualState.Idle;
                Record(Transition.Idle);
            }
            else
            {
                _state = VisualState.None;
                Increment(ref _playFailures);
                Record(Transition.DefaultPlayFailed);
            }
        }

        private bool SourceCanRoute()
        {
            if (!isActiveAndEnabled) return false;
            var source = _source;
            if (source != null && source.isActiveAndEnabled && !_sourceUnavailable) return true;
            HandleSourceUnavailable();
            return false;
        }

        private void HandleSourceUnavailable()
        {
            if (_sourceUnavailable) return;
            _sourceUnavailable = true;
            _idlePending = false;
            _biteActive = false;
            _pounceArmed = false;
            _sawAirborne = false;
            _state = VisualState.None;
            RestoreCulling(true);
            Record(Transition.SourceUnavailable);
        }

        private void ClearSource()
        {
            if (_source == null && _sourceUnavailable && !_idlePending && !_biteActive && !_pounceArmed)
            {
                RestoreCulling(true);
                return;
            }
            _source = null;
            _sourceUnavailable = true;
            _idlePending = false;
            _biteActive = false;
            _pounceArmed = false;
            _sawAirborne = false;
            _state = VisualState.None;
            RestoreCulling(true);
            Record(Transition.SourceUnavailable);
        }

        internal void OnNativeBiteStarted()
        {
            if (!_initialized || _shutdown || !SourceCanRoute()) return;
            if (_biteActive) return;
            _biteActive = true;
            Increment(ref _biteStarts);
            _pounceArmed = false;
            _sawAirborne = false;
            _idlePending = false;
            _holdClip = null;
            _state = VisualState.Bite; // Keep Bite priority even if this species has no bite clip.
            var source = _source;
            var played = source != null && source.TryPlay(BiteClipName, out _);
            if (source != null && !played) Increment(ref _playFailures);
            if (played) BeginHold(BiteClipName);
            Record(Transition.BiteStart);
        }

        internal void OnNativeBiteEnded(bool completed)
        {
            if (!_initialized || _shutdown || !_biteActive) return;
            _biteActive = false;
            if (completed) Increment(ref _biteCompletes);
            else Increment(ref _biteCancels);
            _pounceArmed = false;
            _sawAirborne = false;
            Record(completed ? Transition.BiteComplete : Transition.BiteCancel);
            if (!SourceCanRoute()) return;
            if (!completed)
            {
                // A cancelled chomp never reaches its end: restore idle at once (no digestion follows).
                _holdClip = null;
                _digestPending = false;
                PlayDefaultIdle();
            }
            else if (!HoldActive()) FinishOneShot();
        }

        /// <summary>The native eat path finished its chomp and starts digesting (production delay): play the source
        /// digest clip after the bite has finished.</summary>
        internal void OnNativeDigestStarted()
        {
            if (!_initialized || _shutdown || !SourceCanRoute()) return;
            Increment(ref _digestStarts);
            if (_biteActive || HoldActive()) { _digestPending = true; return; }
            StartDigest();
        }

        private void StartDigest()
        {
            _digestPending = false;
            var source = _source;
            if (source == null) return;
            if (source.TryPlay(DigestClipName, out _))
            {
                _state = VisualState.Digest;
                BeginHold(DigestClipName);
            }
            else
            {
                // A species without a digest clip simply keeps its idle.
                PlayDefaultIdle();
            }
        }

        private void BeginHold(string clip)
        {
            var source = _source;
            _holdClip = clip;
            _holdDeadline = Time.time + 2.5f;
            if (source != null && source.TryReadPlayback(out var playing, out _, out var length) &&
                string.Equals(playing, clip, StringComparison.Ordinal) && length > 0f)
                _holdDeadline = Time.time + length + 0.25f;
        }

        private bool HoldActive()
        {
            var clip = _holdClip;
            if (clip == null) return false;
            var source = _source;
            if (source == null || Time.time >= _holdDeadline) { _holdClip = null; return false; }
            if (source.TryReadPlayback(out var playing, out var time, out var length) &&
                string.Equals(playing, clip, StringComparison.Ordinal) && length > 0f && time < length - 0.03f)
                return true;
            _holdClip = null;
            return false;
        }

        private void FinishOneShot()
        {
            if (_digestPending) StartDigest();
            else PlayDefaultIdle();
        }

        internal void OnConfirmedJump()
        {
            if (!_initialized || _shutdown || !SourceCanRoute()) return;
            Increment(ref _jumps);
            Record(Transition.JumpObserved);
            if (_biteActive) return;
            var source = _source;
            if (source == null || !source.TryGetClipLength(PounceClipName, out var duration) ||
                !source.TryPlay(PounceClipName, out _))
            {
                Increment(ref _playFailures);
                Record(Transition.PouncePlayFailed);
                return;
            }
            _state = VisualState.Pounce;
            _pounceArmed = true;
            _sawAirborne = false;
            _pounceDeadline = Time.time + duration;
            _idlePending = false;
            Record(Transition.PounceStart);
        }

        internal void OnGroundedResult(SlimeSubbehaviourPlexer plexer, bool airborne)
        {
            if (!_initialized || _shutdown || !_pounceArmed || !SourceCanRoute()) return;
            if (plexer == null || plexer.IsFloating() || plexer.IsCaptive()) return;
            if (airborne)
            {
                if (!_sawAirborne)
                {
                    _sawAirborne = true;
                    Record(Transition.Airborne);
                }
                return;
            }
            if (!_sawAirborne) return;

            _pounceArmed = false;
            _sawAirborne = false;
            Increment(ref _landings);
            Record(Transition.Landing);
            if (!_biteActive && _state == VisualState.Pounce) PlayDefaultIdle();
        }

        private void Update()
        {
            if (!_initialized || _shutdown) return;
            try
            {
                var source = _source;
                if (source == null || !source.isActiveAndEnabled)
                {
                    HandleSourceUnavailable();
                    return;
                }
                if (_sourceUnavailable)
                {
                    _sourceUnavailable = false;
                    if (_hasCullingSnapshot) ReapplyCulling();
                    else if (!CaptureAndApplyCulling(source.gameObject)) return;
                    _idlePending = true;
                    _state = VisualState.None;
                }
                if (_holdClip != null && !_biteActive && !HoldActive()) FinishOneShot();
                if (_idlePending) PlayDefaultIdle();
                if (_pounceArmed && !_biteActive && _state == VisualState.Pounce && Time.time >= _pounceDeadline)
                {
                    _pounceArmed = false;
                    _sawAirborne = false;
                    Increment(ref _fallbackCompletions);
                    Record(Transition.PounceFallback);
                    PlayDefaultIdle();
                }
            }
            catch (Exception)
            {
                Increment(ref _updateErrors);
                HandleSourceUnavailable();
            }
        }

        private void OnEnable()
        {
            if (!_initialized || _shutdown) return;
            _biteActive = false;
            _pounceArmed = false;
            _sawAirborne = false;
            _state = VisualState.None;
            _idlePending = true;
            _sourceUnavailable = false;
            ReapplyCulling();
        }

        private void OnDisable()
        {
            if (!_initialized) return;
            _biteActive = false;
            _pounceArmed = false;
            _sawAirborne = false;
            _idlePending = false;
            _state = VisualState.None;
            RestoreCulling(true);
            Record(Transition.Disabled);
        }

        private void OnDestroy()
        {
            try
            {
                RestoreCulling(false);
                UnregisterController(this);
                _source = null;
                _initialized = false;
            }
            catch (Exception) { }
        }

        private void ShutdownForUnload()
        {
            if (_shutdown) return;
            _shutdown = true;
            _biteActive = false;
            _pounceArmed = false;
            _sawAirborne = false;
            _idlePending = false;
            _state = VisualState.None;
            RestoreCulling(false);
            _source = null;
            Record(Transition.Unloaded);
        }

        private void Record(Transition kind)
        {
            _history[_historyNext].Time = Time.time;
            _history[_historyNext].Kind = kind;
            _history[_historyNext].State = _state;
            _historyNext = (_historyNext + 1) % HistoryCapacity;
            if (_historyCount < HistoryCapacity) _historyCount++;
            if (_historyTotal < int.MaxValue) _historyTotal++;
        }

        private static void Increment(ref int counter)
        {
            if (counter < int.MaxValue) counter++;
        }

        /// <summary>Read-only bounded diagnostic JSON. Allocations happen only when explicitly
        /// requested by BridgeDiagnostics, never in Update or a native hook.</summary>
        internal string ToJson()
        {
            var output = new StringBuilder(1024);
            var source = _source;
            output.Append("{\"source_present\":").Append(source != null ? "true" : "false")
                .Append(",\"default_clip\":");
            AppendQuoted(output, source != null ? source.DefaultClipName : "");
            output.Append(",\"state\":");
            AppendQuoted(output, StateName(_state));
            output.Append(",\"playback\":");
            if (source != null)
            {
                var playing = source.TryReadPlayback(out var clip, out var time, out var length);
                output.Append("{\"playing\":").Append(playing ? "true" : "false").Append(",\"clip\":");
                AppendQuoted(output, clip);
                output.Append(",\"state_time\":"); AppendFinite(output, time);
                output.Append(",\"length\":"); AppendFinite(output, length);
                output.Append('}');
            }
            else output.Append("null");
            var chomper = gameObject.GetComponent<Chomper>();
            output.Append(",\"native_chomping\":").Append(chomper != null ? (chomper.IsChomping() ? "true" : "false") : "null")
                .Append(",\"native_animator_states\":[");
            if (_nativeAnimators != null)
            {
                var comma = false;
                for (var i = 0; i < _nativeAnimatorCount; i++)
                {
                    var animator = _nativeAnimators[i];
                    if (animator == null) continue;
                    if (comma) output.Append(',');
                    comma = true;
                    output.Append("{\"name\":"); AppendQuoted(output, animator.gameObject.name);
                    output.Append(",\"enabled\":").Append(animator.enabled ? "true" : "false")
                        .Append(",\"culling\":"); AppendQuoted(output, animator.cullingMode.ToString());
                    output.Append('}');
                }
            }
            output.Append(']');
            output.Append(",\"pounce_armed\":").Append(_pounceArmed ? "true" : "false")
                .Append(",\"saw_airborne\":").Append(_sawAirborne ? "true" : "false")
                .Append(",\"native_animators\":").Append(_nativeAnimatorCount.ToString(CultureInfo.InvariantCulture))
                .Append(",\"culling_forced\":").Append(_cullingApplied ? "true" : "false")
                .Append(",\"counters\":{\"bite_starts\":").Append(_biteStarts.ToString(CultureInfo.InvariantCulture))
                .Append(",\"bite_completes\":").Append(_biteCompletes.ToString(CultureInfo.InvariantCulture))
                .Append(",\"bite_cancels\":").Append(_biteCancels.ToString(CultureInfo.InvariantCulture))
                .Append(",\"jumps\":").Append(_jumps.ToString(CultureInfo.InvariantCulture))
                .Append(",\"landings\":").Append(_landings.ToString(CultureInfo.InvariantCulture))
                .Append(",\"fallback_completions\":").Append(_fallbackCompletions.ToString(CultureInfo.InvariantCulture))
                .Append(",\"play_failures\":").Append(_playFailures.ToString(CultureInfo.InvariantCulture))
                .Append(",\"update_errors\":").Append(_updateErrors.ToString(CultureInfo.InvariantCulture))
                .Append("},\"history_dropped\":").Append(Math.Max(0, _historyTotal - _historyCount).ToString(CultureInfo.InvariantCulture))
                .Append(",\"history\":[");

            var oldest = (_historyNext - _historyCount + HistoryCapacity) % HistoryCapacity;
            for (var i = 0; i < _historyCount; i++)
            {
                if (i != 0) output.Append(',');
                var entry = _history[(oldest + i) % HistoryCapacity];
                output.Append("{\"time\":").Append(entry.Time.ToString("R", CultureInfo.InvariantCulture))
                    .Append(",\"event\":");
                AppendQuoted(output, TransitionName(entry.Kind));
                output.Append(",\"state\":");
                AppendQuoted(output, StateName(entry.State));
                output.Append('}');
            }
            output.Append("]}");
            return output.ToString();
        }

        private static void AppendFinite(StringBuilder output, float value)
        {
            output.Append(float.IsNaN(value) || float.IsInfinity(value) ? "null" : value.ToString("R", CultureInfo.InvariantCulture));
        }

        private static string StateName(VisualState state)
        {
            switch (state)
            {
                case VisualState.Idle: return "idle";
                case VisualState.Pounce: return "pounce";
                case VisualState.Bite: return "bite";
                case VisualState.Digest: return "digest";
                default: return "none";
            }
        }

        private static string TransitionName(Transition kind)
        {
            switch (kind)
            {
                case Transition.Idle: return "idle";
                case Transition.BiteStart: return "bite_start";
                case Transition.BiteComplete: return "bite_complete";
                case Transition.BiteCancel: return "bite_cancel";
                case Transition.JumpObserved: return "jump_observed";
                case Transition.PounceStart: return "pounce_start";
                case Transition.PouncePlayFailed: return "pounce_play_failed";
                case Transition.Airborne: return "airborne";
                case Transition.Landing: return "landing";
                case Transition.PounceFallback: return "pounce_fallback";
                case Transition.DefaultPlayFailed: return "default_play_failed";
                case Transition.SourceChanged: return "source_changed";
                case Transition.SourceUnavailable: return "source_unavailable";
                case Transition.Disabled: return "disabled";
                default: return "unloaded";
            }
        }

        private static void AppendQuoted(StringBuilder output, string value)
        {
            output.Append('"');
            foreach (var character in value)
            {
                switch (character)
                {
                    case '"': output.Append("\\\""); break;
                    case '\\': output.Append("\\\\"); break;
                    case '\b': output.Append("\\b"); break;
                    case '\f': output.Append("\\f"); break;
                    case '\n': output.Append("\\n"); break;
                    case '\r': output.Append("\\r"); break;
                    case '\t': output.Append("\\t"); break;
                    default:
                        if (character < 0x20) output.Append("\\u").Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        else output.Append(character);
                        break;
                }
            }
            output.Append('"');
        }
    }
}
