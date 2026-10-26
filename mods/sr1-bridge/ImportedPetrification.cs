using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Solar petrification machine for imported Ringtail actors (SR1 adaptation v1,
    /// docs/RINGTAIL_STONE_FORM.md). Attached only when the private cache declares a stone form
    /// (bridge-stone-form v1) — every other imported species is untouched. Days only
    /// (TimeDirector.CurrHour in [6, 18)), never under a CaveTrigger volume (caves AND the native
    /// solar shield fire the same events, so both protect), and only after a per-episode uniform
    /// draw of [1, 10] minutes (source RingtailMetadata 1.0/10.0). A petrified actor shows the
    /// stone visual, hides the live visual subtree and freezes exactly five behavioural controls
    /// with an exact restore. Every failure path stays open to the living state: an uncertain
    /// clock, a missing visual or a destroyed control never throws and never half-petrifies.
    /// Faces, palettes and lighting are out of scope and never touched here.
    /// </summary>
    internal sealed class ImportedPetrification : MonoBehaviour, CaveTrigger.Listener
    {
        // Source values (RingtailMetadata SO 32409); see docs/RINGTAIL_STONE_FORM.md section 2.
        internal const float MinMinutesToTransform = 1.0f;
        internal const float MaxMinutesToTransform = 10.0f;
        // Day window derived from DaylightStartHour 18 / DaylightEndHour 6 (the dark window), so
        // daylight is [6, 18).
        internal const float DaylightStartHour = 6.0f;
        internal const float DaylightEndHour = 18.0f;

        private sealed class FrozenEntry
        {
            internal Behaviour? Component;
            internal bool CapturedEnabled;
        }

        private readonly HashSet<GameObject> _protectingVolumes = new HashSet<GameObject>();
        private readonly List<FrozenEntry> _frozenBehaviours = new List<FrozenEntry>();
        private ImportedStoneForm.StoneFormModel? _stoneForm;
        private GameObject? _stoneVisual;
        private GameObject? _liveVisual;
        private bool _captured;
        private bool _controlsFrozen;
        private SlimeSubbehaviourPlexer? _blockedPlexer;
        private bool _ownsBehaviourBlocker;
        private bool _petrified;
        private bool _liveMasked;
        private bool _exposureEpisode;
        private float _exposureTargetMinutes;
        private float _exposureMinutes;
        private bool _daylightKnown;
        private bool _daylight;
        private double _lastWorldTime = double.NaN;
        internal bool IsPetrified => _petrified;

        /// <summary>Attach the machine to an imported actor root. Idempotent: the spawn path and
        /// the appearance-rebuild path (ApplyImportedVisualToRoot) both call this, and an already
        /// attached machine only re-ensures its stone visual.</summary>
        internal static bool EnsureAttached(GameObject actorRoot, string canonical, ImportedStoneForm.StoneFormModel stoneForm, out string problem)
        {
            problem = "";
            if (actorRoot == null || stoneForm == null || stoneForm.Root == null)
            {
                problem = "stone form model is not loaded";
                return false;
            }
            try
            {
                var existing = actorRoot.GetComponent<ImportedPetrification>();
                if (existing != null) return existing.EnsureVisual(out problem);
                if (!actorRoot.activeInHierarchy)
                {
                    problem = "inactive actor roots never receive the petrification machine";
                    return false;
                }
                var machine = actorRoot.AddComponent<ImportedPetrification>();
                machine._stoneForm = stoneForm;
                if (machine.EnsureVisual(out problem)) return true;
                UnityEngine.Object.Destroy(machine);
                return false;
            }
            catch (Exception ex)
            {
                problem = "petrification attach failed: " + ex.GetType().Name;
                return false;
            }
        }

        private bool EnsureVisual(out string problem)
        {
            problem = "";
            try
            {
                if (_stoneVisual != null) return true;
                if (_stoneForm == null || _stoneForm.Root == null)
                {
                    problem = "stone form model is not loaded";
                    return false;
                }
                _stoneVisual = ImportedStoneForm.AttachTo(gameObject, _stoneForm, out problem);
                return _stoneVisual != null;
            }
            catch (Exception ex)
            {
                problem = "stone visual ensure failed: " + ex.GetType().Name;
                return false;
            }
        }

        private float _pendingSeconds;

        private void Update()
        {
            // Duration is measured by the native world clock, not by frames, and every step reads the time director and the cave volumes:
            // five checks a second are plenty for a transformation that takes in-game minutes.
            _pendingSeconds += Time.deltaTime;
            if (_pendingSeconds < 0.2f) return;
            var elapsed = _pendingSeconds;
            _pendingSeconds = 0f;
            Step(elapsed);
        }

        /// <summary>One machine tick. The frame delta is not the duration authority:
        /// RingtailMetadata declares in-game minutes, measured by native WorldTime.</summary>
        internal void Step(float deltaSeconds)
        {
            if (float.IsNaN(deltaSeconds) || float.IsInfinity(deltaSeconds) || deltaSeconds < 0f) deltaSeconds = 0f;
            try
            {
                PurgeProtectingVolumes();
                var director = ResolveTimeDirector();
                var hour = director != null ? director.CurrHour() : float.NaN;
                var worldTime = director != null ? director.WorldTime() : double.NaN;
                _daylightKnown = director != null && !float.IsNaN(hour) && !float.IsInfinity(hour) &&
                    !double.IsNaN(worldTime) && !double.IsInfinity(worldTime);
                var gameMinutes = _daylightKnown && !double.IsNaN(_lastWorldTime) && worldTime >= _lastWorldTime
                    ? (float)((worldTime - _lastWorldTime) / 60d) : 0f;
                var rewound = _daylightKnown && !double.IsNaN(_lastWorldTime) && worldTime < _lastWorldTime;
                _lastWorldTime = _daylightKnown ? worldTime : double.NaN;
                if (rewound)
                {
                    Revert();
                    return;
                }
                _daylight = _daylightKnown && hour >= DaylightStartHour && hour < DaylightEndHour;
                var protectedNow = _protectingVolumes.Count > 0;

                if (_petrified)
                {
                    // Stay stone only in known daylight with no protection: shelter, darkness or an
                    // unreadable clock all return the actor to its living state.
                    if (!_daylightKnown || !_daylight || protectedNow)
                    {
                        Revert();
                        return;
                    }
                    ReaffirmPetrifiedState();
                    return;
                }

                if (!_daylightKnown || !_daylight || protectedNow)
                {
                    Revert();
                    ResetExposureEpisode();
                    return;
                }

                if (!_exposureEpisode)
                {
                    _exposureEpisode = true;
                    _exposureMinutes = 0f;
                    _exposureTargetMinutes = DrawExposureTargetMinutes();
                }
                _exposureMinutes += gameMinutes;
                if (_exposureMinutes >= _exposureTargetMinutes) Petrify();
            }
            catch (Exception)
            {
                // Losing a clock or visual reference must not strand a half-frozen actor.
                _lastWorldTime = double.NaN;
                try { Revert(); } catch (Exception) { }
            }
        }

        /// <summary>Every CaveTrigger volume protects. Caves and the native solar shield fire the
        /// same events; the shield's affectLighting=false flag is deliberately ignored
        /// (docs/RINGTAIL_STONE_FORM.md section 2.2).</summary>
        public void OnCaveEnter(GameObject caveObject, bool affectLighting, AmbianceDirector.Zone caveZone)
        {
            try { if (caveObject != null) _protectingVolumes.Add(caveObject); }
            catch (Exception) { }
        }

        public void OnCaveExit(GameObject caveObject, bool affectLighting, AmbianceDirector.Zone caveZone)
        {
            try { if (caveObject != null) _protectingVolumes.Remove(caveObject); }
            catch (Exception) { }
        }

        private void Petrify()
        {
            if (_petrified)
            {
                ReaffirmPetrifiedState();
                return;
            }
            if (_stoneVisual == null)
            {
                // Never a half stone: without the visual the actor simply stays alive.
                ResetExposureEpisode();
                return;
            }
            _petrified = true;
            try
            {
                if (!_stoneVisual.activeSelf) _stoneVisual.SetActive(true);
                SetSubtreeRenderersEnabled(_stoneVisual, true);
            }
            catch (Exception) { }
            try { MaskLiveVisual(true); }
            catch (Exception) { }
            CaptureOriginalEnabled();
            FreezeFrozen();
            // Clear only momentum carried into the transition. Never clamp later external forces,
            // change kinematic/constraints, or suspend gravity while the stone is being moved.
            var body = GetComponent<Rigidbody>();
            if (body != null && !body.isKinematic)
            {
                body.velocity = Vector3.zero;
                body.angularVelocity = Vector3.zero;
            }
            ResetExposureEpisode();
        }

        private void Revert()
        {
            if (!_petrified && !_liveMasked)
            {
                RestoreFrozen();
                ResetExposureEpisode();
                return;
            }
            _petrified = false;
            try { if (_stoneVisual != null && _stoneVisual.activeSelf) _stoneVisual.SetActive(false); }
            catch (Exception) { }
            MaskLiveVisual(false);
            RestoreFrozen();
            ResetExposureEpisode();
        }

        /// <summary>Re-assert the stone state every tick while petrified. The appearance guard
        /// re-activates the live visual on rebuilds; this heals it without touching anything else.</summary>
        private void ReaffirmPetrifiedState()
        {
            if (_stoneVisual == null)
            {
                Revert();
                return;
            }
            try
            {
                if (!_stoneVisual.activeSelf) _stoneVisual.SetActive(true);
                SetSubtreeRenderersEnabled(_stoneVisual, true);
            }
            catch (Exception) { }
            MaskLiveVisual(true);
            foreach (var entry in _frozenBehaviours)
            {
                try { if (entry.Component != null && entry.Component.enabled) entry.Component.enabled = false; }
                catch (Exception) { }
            }
        }

        private void MaskLiveVisual(bool hidden)
        {
            var live = ResolveLiveVisual();
            if (live == null) return;
            SetSubtreeRenderersEnabled(live, !hidden);
            _liveMasked = hidden;
        }

        private GameObject? ResolveLiveVisual()
        {
            if (_liveVisual == null)
            {
                var marker = GetComponentInChildren<ImportedVisualMarker>(true);
                _liveVisual = marker != null ? marker.gameObject : null;
            }
            return _liveVisual;
        }

        private static void SetSubtreeRenderersEnabled(GameObject root, bool enabled)
        {
            const int budget = 512;
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            var visited = 0;
            while (stack.Count > 0 && visited < budget)
            {
                visited++;
                var current = stack.Pop();
                if (current == null) continue;
                var renderer = current.gameObject.GetComponent<Renderer>();
                if (renderer != null) renderer.enabled = enabled;
                for (var i = 0; i < current.childCount; i++) stack.Push(current.GetChild(i));
            }
        }

        /// <summary>Capture controls once per freeze episode, including the actor's actual
        /// native subbehaviours and both living-animation paths. Restore the exact entry state.</summary>
        private void CaptureOriginalEnabled()
        {
            if (_captured) return;
            _captured = true;
            AddFrozen(GetComponent<SlimeRandomMove>());
            AddFrozen(GetComponent<SlimeEat>());
            AddFrozen(GetComponent<GotoConsumable>());
            AddFrozen(GetComponent<FleeThreats>());
            AddFrozen(GetComponent<SlimeFeral>());
            var subbehaviours = GetComponents<SlimeSubbehaviour>();
            if (subbehaviours.Length > 64) throw new InvalidOperationException("subbehaviour freeze budget exceeded");
            foreach (var subbehaviour in subbehaviours) AddFrozen(subbehaviour);
            AddFrozen(GetComponent<KeepUpright>());
            AddFrozen(GetComponent<Chomper>()); // native OnDisable cancels outstanding mouth joints
            AddFrozen(GetComponent<ImportedGameplayAnimation>());
            var animators = GetComponentsInChildren<Animator>(true);
            if (animators.Length > 32) throw new InvalidOperationException("animator freeze budget exceeded");
            foreach (var animator in animators) AddFrozen(animator);
            var living = GetComponentInChildren<ImportedVisualMarker>(true);
            if (living != null)
            {
                var visualBehaviours = living.GetComponentsInChildren<Behaviour>(true);
                if (visualBehaviours.Length > 128) throw new InvalidOperationException("visual freeze budget exceeded");
                foreach (var behaviour in visualBehaviours)
                {
                    // Immutable metadata/ownership components do not animate and must not
                    // inflate the frozen-control count or lose their normal lifecycle.
                    if (behaviour is ImportedVisualMarker || behaviour is ImportedPartColors || behaviour is SourceFaceResourceOwner) continue;
                    AddFrozen(behaviour);
                }
            }
        }

        private void AddFrozen(Behaviour? component)
        {
            try
            {
                if (component == null) return;
                foreach (var existing in _frozenBehaviours)
                    if (existing.Component == component) return;
                _frozenBehaviours.Add(new FrozenEntry { Component = component, CapturedEnabled = component.enabled });
            }
            catch (Exception) { }
        }

        private void FreezeFrozen()
        {
            if (!_ownsBehaviourBlocker)
            {
                var plexer = GetComponent<SlimeSubbehaviourPlexer>();
                if (plexer != null)
                {
                    // Register first, then force-deselect: a non-rethinkable retained action
                    // otherwise continues even while behaviorBlockers > 0 in native SR1.
                    plexer.RegisterBehaviorBlocker();
                    _blockedPlexer = plexer;
                    _ownsBehaviourBlocker = true;
                    plexer.ForceRethink();
                }
            }
            _controlsFrozen = true;
            foreach (var entry in _frozenBehaviours)
            {
                try { if (entry.Component != null) entry.Component.enabled = false; }
                catch (Exception) { }
            }
        }

        private void RestoreFrozen()
        {
            if (!_controlsFrozen && !_ownsBehaviourBlocker)
            {
                // Capture may have failed its budget before any control was changed.
                _captured = false;
                _frozenBehaviours.Clear();
                return;
            }
            foreach (var entry in _frozenBehaviours)
            {
                try { if (entry.Component != null) entry.Component.enabled = entry.CapturedEnabled; }
                catch (Exception) { }
            }
            if (_ownsBehaviourBlocker)
            {
                try { if (_blockedPlexer != null) _blockedPlexer.UnregisterBehaviorBlocker(); }
                finally { _ownsBehaviourBlocker = false; _blockedPlexer = null; }
            }
            _controlsFrozen = false;
            _captured = false;
            _frozenBehaviours.Clear();
        }

        private void OnDestroy()
        {
            try { RestoreFrozen(); }
            catch (Exception) { }
        }

        private void PurgeProtectingVolumes()
        {
            if (_protectingVolumes.Count == 0) return;
            _protectingVolumes.RemoveWhere(IsDestroyedVolume);
        }

        private static bool IsDestroyedVolume(GameObject volume) => volume == null;

        private void ResetExposureEpisode()
        {
            _exposureEpisode = false;
            _exposureMinutes = 0f;
            _exposureTargetMinutes = 0f;
        }

        private static float DrawExposureTargetMinutes()
        {
            return UnityEngine.Random.Range(MinMinutesToTransform, MaxMinutesToTransform);
        }

        private static TimeDirector? ResolveTimeDirector()
        {
            try
            {
                var scene = SceneContext.Instance;
                return scene != null ? scene.TimeDirector : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>LAB rest-pose payload (read-only): the machine's externally visible state.</summary>
        internal string ToJson()
        {
            var state = _petrified ? "stone" : "alive";
            var daylight = _daylightKnown ? (_daylight ? "true" : "false") : "null";
            var livingMarker = gameObject.GetComponentInChildren<ImportedVisualMarker>(true);
            var stoneVisible = EnabledRendererCount(_stoneVisual) > 0;
            var liveVisible = EnabledRendererCount(livingMarker != null ? livingMarker.gameObject : null) > 0;
            return "{\"stone_visible\": " + (stoneVisible ? "true" : "false") +
                ", \"live_visible\": " + (liveVisible ? "true" : "false") +
                ", \"state\": \"" + state + "\", \"exposure_minutes\": " +
                _exposureMinutes.ToString("R", CultureInfo.InvariantCulture) +
                ", \"protected\": " + (LiveProtectionCount() > 0 ? "true" : "false") +
                ", \"daylight\": " + daylight +
                ", \"frozen_controls_disabled\": " + (FrozenControlsDisabled() ? "true" : "false") +
                ", \"frozen_count\": " + CountFrozenControls().ToString(CultureInfo.InvariantCulture) + "}";
        }

        private static int EnabledRendererCount(GameObject? visual)
        {
            const int MaxVisualWalk = 512;
            if (visual == null || !visual.activeInHierarchy) return 0;
            var stack = new Stack<Transform>();
            stack.Push(visual.transform);
            var count = 0;
            var visited = 0;
            while (stack.Count > 0)
            {
                if (++visited > MaxVisualWalk) throw new InvalidOperationException("diagnostic visual budget exceeded");
                var current = stack.Pop();
                if (current == null || !current.gameObject.activeInHierarchy) continue;
                var renderer = current.gameObject.GetComponent<Renderer>();
                if (renderer != null && renderer.enabled) count++;
                if (visited + stack.Count + current.childCount > MaxVisualWalk)
                    throw new InvalidOperationException("diagnostic visual budget exceeded");
                for (var i = 0; i < current.childCount; i++) stack.Push(current.GetChild(i));
            }
            return count;
        }

        private int LiveProtectionCount()
        {
            var count = 0;
            foreach (var volume in _protectingVolumes)
            {
                if (volume != null) count++;
            }
            return count;
        }

        private bool FrozenControlsDisabled()
        {
            if (!_petrified || _frozenBehaviours.Count == 0) return false;
            foreach (var entry in _frozenBehaviours)
                if (entry.Component != null && entry.Component.enabled) return false;
            return true;
        }

        private int CountFrozenControls()
        {
            var count = 0;
            foreach (var entry in _frozenBehaviours)
            {
                try
                {
                    if (entry.Component != null && entry.CapturedEnabled && !entry.Component.enabled) count++;
                }
                catch (Exception) { }
            }
            return count;
        }
    }
}
