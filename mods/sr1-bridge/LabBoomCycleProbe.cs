using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    // Exact LAB, one spawn-owned Cotton Boom. Reads native state; never invokes explosion/AI.
    internal sealed class LabBoomCycleProbe : MonoBehaviour
    {
        private const string PatchId = "slimebridge.sr1.lab-boom-cycle";
        private static LabBoomCycleProbe? _active;
        private BoomSlimeExplode? _boom;
        private FieldInfo? _state;
        private Harmony? _harmony;
        private readonly List<string> _events = new List<string>();
        private string _output = "", _previous = "";
        private float _deadline;
        private int _explosions;
        private bool _preparing, _recovering, _idleAfter, _done;

        internal static void TryAttach(GameObject actor, string canonical)
        {
            if (canonical != "largo.boom.cotton" || !BridgeEntry.OracleExecutableMatches() ||
                Environment.GetEnvironmentVariable("SLIMEBRIDGE_BOOM_CYCLE") != "1") return;
            if (_active != null || BridgeEntry.LabRestPoseRequested) throw new InvalidOperationException("Boom probe requires one unpinned actor");
            var root = LabSpecPathGuard.FindRepoRoot();
            var output = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR1_VAC_TRACE_OUT");
            if (root == null || string.IsNullOrEmpty(output) ||
                !LabSpecPathGuard.IsAllowedDirectory(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), output, out _))
                throw new IOException("Boom probe output refused");
            var boom = actor.GetComponent<BoomSlimeExplode>();
            if (boom == null) throw new InvalidOperationException("Cotton Boom has no native BoomSlimeExplode");
            var state = typeof(BoomSlimeExplode).GetField("state", BindingFlags.Instance | BindingFlags.NonPublic);
            var explode = typeof(BoomSlimeExplode).GetMethod("Explode", BindingFlags.Instance | BindingFlags.NonPublic,
                null, Type.EmptyTypes, null);
            if (state == null || explode == null || explode.ReturnType != typeof(void)) throw new MissingMemberException("Boom native signature mismatch");
            var probe = actor.AddComponent<LabBoomCycleProbe>();
            probe._boom = boom; probe._state = state; probe._output = output;
            probe._deadline = Time.realtimeSinceStartup + 120f;
            probe._harmony = new Harmony(PatchId);
            try
            {
                _active = probe;
                probe._harmony.Patch(explode, postfix: new HarmonyMethod(typeof(LabBoomCycleProbe), nameof(Exploded)));
            }
            catch { probe.Stop(); UnityEngine.Object.Destroy(probe); throw; }
        }
        private static void Exploded(BoomSlimeExplode __instance)
        {
            try
            {
                var probe = _active;
                if (probe == null || !ReferenceEquals(probe._boom, __instance)) return;
                probe._explosions++;
                probe.Record("native.Explode.after");
            }
            catch (Exception) { } // Diagnostic hooks must not change native exceptions or results.
        }
        private void Update()
        {
            if (_done) return;
            try
            {
                var state = _state?.GetValue(_boom)?.ToString() ?? "missing";
                if (state != _previous) { _previous = state; Record(state); }
                if (state == "PREPARING") _preparing = true;
                if (state == "RECOVERING" && _explosions > 0) _recovering = true;
                if (state == "IDLE" && _recovering) _idleAfter = true;
                var accepted = _preparing && _explosions > 0 && _recovering && _idleAfter;
                if (!accepted && Time.realtimeSinceStartup < _deadline) return;
                _done = true; Stop();
                RegistrySnapshotFormat.PublishCaptureFile(_output, "visual-diagnostics-boom-cycle.json",
                    "{\"schema\":\"slimebridge-boom-cycle\",\"actor\":" + gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture) +
                    ",\"canonical\":\"largo.boom.cotton\",\"passed\":" + (accepted ? "true" : "false") +
                    ",\"explosions\":" + _explosions.ToString(CultureInfo.InvariantCulture) +
                    ",\"events\":[" + string.Join(",", _events) + "]}");
            }
            catch (Exception error)
            {
                _done = true;
                try { Stop(); } finally { Debug.LogWarning("[SlimeBridge] Boom probe failed: " + error); }
            }
        }
        private void Record(string phase)
        {
            if (_events.Count >= 64) return;
            _events.Add("{\"phase\":" + RegistrySnapshotFormat.Quote(phase) + ",\"time\":" +
                Time.realtimeSinceStartup.ToString("R", CultureInfo.InvariantCulture) + "}");
        }
        private void Stop()
        {
            if (ReferenceEquals(_active, this)) _active = null;
            var harmony = _harmony; _harmony = null;
            harmony?.UnpatchAll(PatchId);
        }
        private void OnDestroy() { Stop(); }
    }
}
