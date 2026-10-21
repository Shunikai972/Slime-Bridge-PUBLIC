using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Opt-in LAB observer. Never positions the actor or changes its physics/AI.
    /// Native Action prefixes only count calls; they never suppress or replace an action.</summary>
    internal sealed class LabActorMotionTrace : MonoBehaviour
    {
        private const int MaxSamples = 3000;
        private const float SamplePeriod = 0.1f;
        private const string HarmonyId = "SlimeBridge.SR1.LabActorMotionTrace";
        private static Harmony? _harmony;
        private static readonly Dictionary<MethodBase, string> ActionLabels = new Dictionary<MethodBase, string>();
        private static int _activeTraces;
        private static readonly FieldInfo? CurrentBehaviourField = typeof(SlimeSubbehaviourPlexer).GetField(
            "currBehavior", BindingFlags.Instance | BindingFlags.NonPublic);
        private readonly Dictionary<string, int> _actionCalls = new Dictionary<string, int>();
        private readonly Dictionary<int, Collider> _contacts = new Dictionary<int, Collider>();
        private Rigidbody? _body;
        private Vacuumable? _vacuumable;
        private ImportedPetrification? _machine;
        private SlimeSubbehaviourPlexer? _plexer;
        private Transform? _player;
        private Transform? _visual;
        private Transform? _stoneVisual;
        private LabMotionClockScenario? _scenario;
        private MonoBehaviour[] _rootScripts = new MonoBehaviour[0];
        private MonoBehaviour[] _visualScripts = new MonoBehaviour[0];
        private Animator[] _animators = new Animator[0];
        private StreamWriter? _writer;
        private float _started;
        private float _nextSample;
        private float _nextFlush;
        private int _samples;
        private bool _registered;

        internal static bool TryAttach(GameObject actor, string canonical, string outDir, out string problem)
        {
            problem = "";
            if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_MOTION_TRACE") != "1") return true;
            if (canonical != "slime.ringtail") { problem = "motion proof only supports ringtail"; return false; }
            if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_REST_POSE") == "1" || actor.GetComponent<LabRestPosePin>() != null)
            { problem = "motion proof refuses a rest-pose actor pin"; return false; }
            LabActorMotionTrace? trace = null;
            try
            {
                var root = LabSpecPathGuard.FindRepoRoot();
                var actual = Process.GetCurrentProcess().MainModule?.FileName;
                var requested = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
                if (root == null || string.IsNullOrEmpty(actual) || string.IsNullOrEmpty(requested) ||
                    !string.Equals(Path.GetFullPath(actual), Path.GetFullPath(requested), StringComparison.OrdinalIgnoreCase) ||
                    !string.Equals(Path.GetFullPath(actual), Path.GetFullPath(Path.Combine(root, "Lab", "Games", "SR1", "SlimeRancher.exe")), StringComparison.OrdinalIgnoreCase) ||
                    !LabSpecPathGuard.IsAllowedDirectory(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), outDir, out problem))
                    return false;
                if (actor.GetComponent<LabActorMotionTrace>() != null) return true;
                var path = Path.Combine(outDir, "motion-" + actor.GetInstanceID().ToString(CultureInfo.InvariantCulture) + ".jsonl");
                if (!LabSpecPathGuard.IsUnderRoot(outDir, path, out problem) || LabSpecPathGuard.HasReparsePoint(path, out problem)) return false;
                trace = actor.AddComponent<LabActorMotionTrace>();
                trace._writer = new StreamWriter(new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false));
                trace._body = actor.GetComponent<Rigidbody>();
                trace._vacuumable = actor.GetComponent<Vacuumable>();
                trace._machine = actor.GetComponent<ImportedPetrification>();
                trace._plexer = actor.GetComponent<SlimeSubbehaviourPlexer>();
                trace._player = SceneContext.Instance != null && SceneContext.Instance.Player != null ? SceneContext.Instance.Player.transform : null;
                trace._rootScripts = actor.GetComponents<MonoBehaviour>();
                var marker = actor.GetComponentInChildren<ImportedVisualMarker>(true);
                trace._visual = marker != null ? marker.transform : null;
                var stone = actor.GetComponentInChildren<ImportedStoneFormMarker>(true);
                trace._stoneVisual = stone != null ? stone.transform : null;
                trace._visualScripts = marker != null ? marker.GetComponentsInChildren<MonoBehaviour>(true) : new MonoBehaviour[0];
                trace._animators = actor.GetComponentsInChildren<Animator>(true);
                if (trace._rootScripts.Length > 128 || trace._visualScripts.Length > 128 || trace._animators.Length > 32)
                    throw new InvalidDataException("motion proof component budget exceeded");
                trace._started = Time.realtimeSinceStartup;
                trace._nextSample = trace._started;
                trace._nextFlush = trace._started;
                trace.InstallActionObservers(actor);
                trace._registered = true;
                _activeTraces++;
                LabMotionClockScenario.Attach(actor);
                trace._scenario = actor.GetComponent<LabMotionClockScenario>();
                UnityEngine.Debug.Log("[SlimeBridge] unpinned motion trace: " + path);
                return true;
            }
            catch (Exception error)
            {
                trace?.Close();
                if (trace != null) UnityEngine.Object.Destroy(trace);
                problem = "motion trace refused: " + error.GetType().Name + ": " + error.Message;
                return false;
            }
        }

        private void InstallActionObservers(GameObject actor)
        {
            if (_harmony == null) _harmony = new Harmony(HarmonyId);
            var prefix = new HarmonyMethod(typeof(LabActorMotionTrace).GetMethod(nameof(ObserveNativeAction), BindingFlags.Static | BindingFlags.NonPublic));
            var behaviours = actor.GetComponents<SlimeSubbehaviour>();
            if (behaviours.Length > 64) throw new InvalidDataException("subbehaviour budget exceeded");
            foreach (var behaviour in behaviours)
            {
                if (behaviour == null) continue;
                var method = behaviour.GetType().GetMethod("Action", BindingFlags.Instance | BindingFlags.Public, null, Type.EmptyTypes, null);
                if (method == null || method.IsAbstract || ActionLabels.ContainsKey(method)) continue;
                ActionLabels.Add(method, method.DeclaringType?.Name + ".Action");
                _harmony.Patch(method, prefix: prefix);
            }
            var update = typeof(SlimeSubbehaviourPlexer).GetMethod("RegistryFixedUpdate", BindingFlags.Instance | BindingFlags.Public);
            if (update != null && !ActionLabels.ContainsKey(update))
            {
                ActionLabels.Add(update, "SlimeSubbehaviourPlexer.RegistryFixedUpdate");
                _harmony.Patch(update, prefix: prefix);
            }
        }

        private static void ObserveNativeAction(object __instance, MethodBase __originalMethod)
        {
            // Count only the specifically instrumented actor; all native actions remain intact.
            try
            {
                var component = __instance as Component;
                var trace = component != null ? component.GetComponent<LabActorMotionTrace>() : null;
                if (trace == null || trace._writer == null || !ActionLabels.TryGetValue(__originalMethod, out var label)) return;
                trace._actionCalls.TryGetValue(label, out var count);
                if (count < int.MaxValue) trace._actionCalls[label] = count + 1;
            }
            catch (Exception) { }
        }

        private void OnCollisionStay(Collision collision)
        {
            if (collision.collider != null && _contacts.Count < 16)
                _contacts[collision.collider.GetInstanceID()] = collision.collider;
        }

        private void OnCollisionExit(Collision collision)
        {
            if (collision.collider != null) _contacts.Remove(collision.collider.GetInstanceID());
        }

        private void FixedUpdate()
        {
            if (_writer == null || _samples >= MaxSamples) return;
            var now = Time.realtimeSinceStartup;
            if (now < _nextSample) return;
            _nextSample = now + SamplePeriod;
            try
            {
                _writer.WriteLine(FormatObservation(now));
                _samples++;
                if (now >= _nextFlush)
                {
                    _writer.Flush();
                    _nextFlush = now + 1f;
                }
                if (_samples >= MaxSamples) Close();
            }
            catch (Exception error)
            {
                UnityEngine.Debug.LogWarning("[SlimeBridge] motion trace stopped: " + error.GetType().Name);
                Close();
            }
        }

        private string FormatObservation(float now)
        {
            var output = new StringBuilder(4096);
            output.Append("{\"elapsed\": ").Append(F(now - _started))
                .Append(", \"frame\": ").Append(Time.frameCount.ToString(CultureInfo.InvariantCulture))
                .Append(", \"instance\": ").Append(gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture))
                .Append(", \"pinned\": ").Append(GetComponent<LabRestPosePin>() != null ? "true" : "false")
                .Append(", \"explicit_external_impulse\": ").Append(_scenario != null && _scenario.ExternalImpulseApplied ? "true" : "false")
                .Append(", \"root_position\": ").Append(V(transform.position))
                .Append(", \"root_rotation\": ").Append(Q(transform.rotation))
                .Append(", \"root_scale\": ").Append(V(transform.localScale))
                .Append(", \"vacuum_held\": ").Append(_vacuumable != null && _vacuumable.isHeld() ? "true" : "false")
                .Append(", \"vacuum_captive\": ").Append(_vacuumable != null && _vacuumable.isCaptive() ? "true" : "false")
                .Append(", \"rigidbody\": ").Append(BodyJson())
                .Append(", \"petrification\": ").Append(_machine != null ? _machine.ToJson() : "null")
                .Append(", \"root_behaviours\": ").Append(ScriptsJson(_rootScripts))
                .Append(", \"visual_behaviours\": ").Append(ScriptsJson(_visualScripts));
            var current = _plexer != null && CurrentBehaviourField != null ? CurrentBehaviourField.GetValue(_plexer) as Behaviour : null;
            output.Append(", \"selected_behaviour\": ").Append(current != null ? RegistrySnapshotFormat.Quote(current.GetType().Name) : "null")
                .Append(", \"selected_enabled\": ").Append(current != null ? (current.enabled ? "true" : "false") : "null")
                .Append(", \"source_visual_local_position\": ").Append(_visual != null ? V(_visual.localPosition) : "null")
                .Append(", \"source_visual_local_rotation\": ").Append(_visual != null ? Q(_visual.localRotation) : "null")
                .Append(", \"source_visual_local_scale\": ").Append(_visual != null ? V(_visual.localScale) : "null")
                .Append(", \"stone_local_position\": ").Append(_stoneVisual != null ? V(_stoneVisual.localPosition) : "null")
                .Append(", \"stone_local_rotation\": ").Append(_stoneVisual != null ? Q(_stoneVisual.localRotation) : "null")
                .Append(", \"stone_local_scale\": ").Append(_stoneVisual != null ? V(_stoneVisual.localScale) : "null")
                .Append(", \"animators\": [");
            for (var i = 0; i < _animators.Length; i++)
            {
                if (i > 0) output.Append(',');
                var animator = _animators[i];
                if (animator == null) { output.Append("null"); continue; }
                output.Append("{\"name\": ").Append(RegistrySnapshotFormat.Quote(animator.name))
                    .Append(", \"enabled\": ").Append(animator.enabled ? "true" : "false")
                    .Append(", \"active\": ").Append(animator.isActiveAndEnabled ? "true" : "false")
                    .Append(", \"apply_root_motion\": ").Append(animator.applyRootMotion ? "true" : "false");
                if (animator.isActiveAndEnabled && animator.runtimeAnimatorController != null && animator.layerCount > 0)
                {
                    var state = animator.GetCurrentAnimatorStateInfo(0);
                    output.Append(", \"state_hash\": ").Append(state.fullPathHash.ToString(CultureInfo.InvariantCulture))
                        .Append(", \"normalized_time\": ").Append(F(state.normalizedTime));
                }
                output.Append('}');
            }
            output.Append("], \"native_calls\": {");
            var index = 0;
            foreach (var entry in _actionCalls)
            {
                if (index++ > 0) output.Append(',');
                output.Append(RegistrySnapshotFormat.Quote(entry.Key)).Append(':').Append(entry.Value.ToString(CultureInfo.InvariantCulture));
            }
            var contacts = 0;
            var external = 0;
            foreach (var collider in _contacts.Values)
            {
                if (collider == null || !collider.enabled || !collider.gameObject.activeInHierarchy) continue;
                contacts++;
                var otherBody = collider.attachedRigidbody;
                if ((otherBody != null && otherBody != _body) || (_player != null && collider.transform.IsChildOf(_player))) external++;
            }
            return output.Append("}, \"contacts\": ").Append(contacts.ToString(CultureInfo.InvariantCulture))
                .Append(", \"external_contacts\": ").Append(external.ToString(CultureInfo.InvariantCulture)).Append('}').ToString();
        }

        private string BodyJson()
        {
            if (_body == null) return "null";
            return "{\"velocity\": " + V(_body.velocity) + ", \"angular_velocity\": " + V(_body.angularVelocity) +
                ", \"is_kinematic\": " + (_body.isKinematic ? "true" : "false") +
                ", \"use_gravity\": " + (_body.useGravity ? "true" : "false") +
                ", \"constraints\": " + ((int)_body.constraints).ToString(CultureInfo.InvariantCulture) +
                ", \"mass\": " + F(_body.mass) + ", \"drag\": " + F(_body.drag) +
                ", \"angular_drag\": " + F(_body.angularDrag) +
                ", \"sleeping\": " + (_body.IsSleeping() ? "true" : "false") + "}";
        }

        private static string ScriptsJson(MonoBehaviour[] scripts)
        {
            var output = new StringBuilder("{");
            var index = 0;
            foreach (var script in scripts)
            {
                if (script == null) continue;
                if (index++ > 0) output.Append(',');
                output.Append(RegistrySnapshotFormat.Quote(script.GetType().Name)).Append(':').Append(script.enabled ? "true" : "false");
            }
            return output.Append('}').ToString();
        }

        private static string F(float value) => float.IsNaN(value) || float.IsInfinity(value) ? "null" : value.ToString("R", CultureInfo.InvariantCulture);
        private static string V(Vector3 value) => "[" + F(value.x) + "," + F(value.y) + "," + F(value.z) + "]";
        private static string Q(Quaternion value) => "[" + F(value.x) + "," + F(value.y) + "," + F(value.z) + "," + F(value.w) + "]";

        private void Close()
        {
            try { _writer?.Dispose(); }
            finally
            {
                _writer = null;
                if (_registered) { _registered = false; _activeTraces--; }
                if (_activeTraces == 0)
                {
                    _harmony?.UnpatchAll(HarmonyId);
                    _harmony = null;
                    ActionLabels.Clear();
                }
            }
        }

        private void OnDestroy()
        {
            try { Close(); } catch (Exception) { }
        }
    }
}
