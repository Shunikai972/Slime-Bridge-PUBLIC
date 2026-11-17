using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    // Explicit LAB-only read-only observer. No pose pin, impulse, native hook or actor state write.
    internal sealed class LabUserLargoMotionProbe : MonoBehaviour
    {
        private readonly List<string> _rows = new List<string>();
        private RubberBoneEffect[] _effects = new RubberBoneEffect[0];
        private Rigidbody? _body;
        private string _output = "";
        private int _thread;
        private float _next;
        private bool _done;

        internal static void TryAttach(GameObject actor, string canonical)
        {
            if (canonical != "largo.cotton.quantum" || !BridgeEntry.OracleExecutableMatches() ||
                Environment.GetEnvironmentVariable("SLIMEBRIDGE_USER_LARGO_MOTION") != "1") return;
            if (BridgeEntry.LabRestPoseRequested || actor == null || actor.GetComponent<LabRestPosePin>() != null)
                throw new InvalidOperationException("user largo motion proof refuses a pose pin");
            var root = LabSpecPathGuard.FindRepoRoot();
            var output = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR1_VAC_TRACE_OUT");
            if (root == null || string.IsNullOrEmpty(output) ||
                !LabSpecPathGuard.IsAllowedDirectory(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), output, out _))
                throw new IOException("user largo motion output refused");
            if (actor.GetComponent<LabUserLargoMotionProbe>() != null) return;
            var effects = actor.GetComponentsInChildren<RubberBoneEffect>(true);
            if (effects.Length > 6) throw new InvalidDataException("motion effect budget exceeded");
            foreach (var effect in effects)
                if (effect != null && effect.bones != null && effect.bones.Length > 32)
                    throw new InvalidDataException("motion bone budget exceeded");
            var probe = actor.AddComponent<LabUserLargoMotionProbe>();
            probe._body = actor.GetComponent<Rigidbody>(); probe._effects = effects; probe._output = output;
            probe._thread = System.Threading.Thread.CurrentThread.ManagedThreadId;
        }

        private void LateUpdate()
        {
            if (_done || Time.realtimeSinceStartup < _next ||
                System.Threading.Thread.CurrentThread.ManagedThreadId != _thread) return;
            _next = Time.realtimeSinceStartup + .5f;
            try
            {
                _rows.Add(Sample());
                if (_rows.Count < 120) return;
                _done = true;
                RegistrySnapshotFormat.PublishCaptureFile(_output, "visual-diagnostics-user-largo-motion.json",
                    "{\"schema\":\"slimebridge-user-largo-motion\",\"canonical\":\"largo.cotton.quantum\",\"actor\":" +
                    gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture) +
                    ",\"pinned\":false,\"samples\":[" + string.Join(",", _rows) + "]}");
            }
            catch (Exception error)
            {
                _done = true;
                Debug.LogWarning("[SlimeBridge] user largo motion observation failed: " + error.GetType().Name + ": " + error.Message);
            }
        }

        private string Sample()
        {
            var effects = new List<string>();
            foreach (var effect in _effects)
            {
                if (effect == null) continue;
                var bones = new List<string>();
                if (effect.bones != null)
                    foreach (var entry in effect.bones)
                        if (entry != null && entry.trans != null)
                            bones.Add("{\"name\":" + Q(entry.trans.name) + ",\"intensity\":" + F(entry.intensity) +
                                ",\"local_position\":" + V(entry.trans.localPosition) + ",\"world_position\":" + V(entry.trans.position) + "}");
                effects.Add("{\"id\":" + effect.GetInstanceID().ToString(CultureInfo.InvariantCulture) +
                    ",\"name\":" + Q(effect.name) + ",\"enabled\":" + B(effect.enabled) +
                    ",\"imported\":" + B(effect.GetComponent<ImportedVisualMarker>() != null) +
                    ",\"preset\":" + Q(effect.Presets.ToString()) + ",\"mass\":" + F(effect.mass) +
                    ",\"stiffness\":" + F(effect.stiffness) + ",\"damping\":" + F(effect.damping) +
                    ",\"gravity\":" + F(effect.gravity) + ",\"bones\":[" + string.Join(",", bones) + "]}");
            }
            var animation = gameObject.GetComponent<ImportedGameplayAnimation>();
            return "{\"time\":" + F(Time.realtimeSinceStartup) + ",\"frame\":" + Time.frameCount.ToString(CultureInfo.InvariantCulture) +
                ",\"position\":" + V(transform.position) + ",\"scale\":" + V(transform.localScale) +
                ",\"velocity\":" + (_body != null ? V(_body.velocity) : "null") +
                ",\"body_mass\":" + (_body != null ? F(_body.mass) : "null") +
                ",\"gravity\":" + B(_body != null && _body.useGravity) + ",\"kinematic\":" + B(_body != null && _body.isKinematic) +
                ",\"gameplay_animation\":" + (animation != null ? animation.ToJson() : "null") +
                ",\"quantum_binding\":" + QuantumBinding() +
                ",\"effects\":[" + string.Join(",", effects) + "]}";
        }
        private string QuantumBinding()
        {
            var generator = gameObject.GetComponent<GenerateQuantumQubit>();
            if (generator == null) return "null";
            var applicator = generator.AppearanceApplicator;
            var appearance = applicator != null ? applicator.Appearance : null;
            return "{\"generator_id\":" + generator.GetInstanceID().ToString(CultureInfo.InvariantCulture) +
                ",\"generator_enabled\":" + B(generator.enabled) +
                ",\"applicator_managed_null\":" + B(ReferenceEquals(applicator, null)) +
                ",\"applicator_unity_null\":" + B(applicator == null) +
                ",\"applicator_actor_local\":" + B(applicator != null &&
                    (applicator.transform == transform || applicator.transform.IsChildOf(transform))) +
                ",\"applicator_enabled\":" + B(applicator != null && applicator.enabled) +
                ",\"appearance_null\":" + B(appearance == null) +
                ",\"qubit_appearance_null\":" + B(appearance == null || appearance.QubitAppearance == null) +
                ",\"qubit_prefab_null\":" + B(generator.QubitPrefab == null) + "}";
        }
        private static string F(float x) => float.IsNaN(x) || float.IsInfinity(x) ? "null" : x.ToString("R", CultureInfo.InvariantCulture);
        private static string V(Vector3 x) => "[" + F(x.x) + "," + F(x.y) + "," + F(x.z) + "]";
        private static string B(bool x) => x ? "true" : "false";
        private static string Q(string x) => RegistrySnapshotFormat.Quote(x);
    }
}
