using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    // Temporary explicit LAB observer. No materials, transforms, native held state or physics are changed.
    internal sealed class LabSr1VacuumProbe : MonoBehaviour
    {
        private Vacuumable _vac;
        private string _out = "";
        private int _rows, _thread;
        private float _next = -1f;
        private int _deferred;
        private bool _subscribed;

        internal static void TryAttach(GameObject instance, string canonical)
        {
            if (!BridgeEntry.OracleExecutableMatches() || canonical != "largo.cotton.quantum") return;
            var output = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR1_VAC_TRACE_OUT");
            if (string.IsNullOrEmpty(output) || instance == null || instance.GetComponent<LabSr1VacuumProbe>() != null) return;
            var root = LabSpecPathGuard.FindRepoRoot();
            if (root == null || !LabSpecPathGuard.IsAllowedDirectory(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), output, out _)) return;
            var vac = instance.GetComponent<Vacuumable>();
            if (vac == null) return;
            var probe = instance.AddComponent<LabSr1VacuumProbe>();
            probe._vac = vac; probe._out = output; probe._thread = System.Threading.Thread.CurrentThread.ManagedThreadId;
            vac.onSetHeld += probe.HeldChanged;
            probe._subscribed = true;
            probe.Capture("spawn");
        }

        private void HeldChanged(bool held)
        {
            Capture(held ? "native.held.after" : "native.release.after");
            _deferred = 3; _next = Time.realtimeSinceStartup + 0.15f;
        }
        private void Update()
        {
            if (_deferred <= 0 || _next < 0f || Time.realtimeSinceStartup < _next) return;
            _deferred--; _next = Time.realtimeSinceStartup + 0.6f;
            Capture("native.deferred");
        }
        private void OnDestroy()
        {
            if (_subscribed && _vac != null) _vac.onSetHeld -= HeldChanged;
            _subscribed = false; _out = ""; _deferred = 0;
        }
        private static string Q(string value) => RegistrySnapshotFormat.Quote(value);
        private static string F(float value) => value.ToString("R", CultureInfo.InvariantCulture);
        private static string V(Vector4 v) => "[" + F(v.x) + "," + F(v.y) + "," + F(v.z) + "," + F(v.w) + "]";
        private static string B(bool value) => value ? "true" : "false";
        private static string Block(MaterialPropertyBlock block)
        {
            return "{\"empty\":" + B(block.isEmpty) + ",\"held_raw\":" + F(block.GetFloat("_HeldInVac")) +
                ",\"SHAr\":" + V(block.GetVector("unity_SHAr")) + ",\"SHAg\":" + V(block.GetVector("unity_SHAg")) +
                ",\"SHAb\":" + V(block.GetVector("unity_SHAb")) + "}";
        }
        private void Capture(string phase)
        {
            if (string.IsNullOrEmpty(_out) || _vac == null || _rows >= 16 ||
                System.Threading.Thread.CurrentThread.ManagedThreadId != _thread) return;
            try
            {
                var sb = new StringBuilder(16384);
                sb.Append("{\"phase\":").Append(Q(phase)).Append(",\"frame\":").Append(Time.frameCount);
                sb.Append(",\"actor\":").Append(gameObject.GetInstanceID()).Append(",\"held\":").Append(B(_vac.isHeld()));
                var pos = transform.position;
                sb.Append(",\"position\":").Append(V(new Vector4(pos.x, pos.y, pos.z, 1f)));
                var body = GetComponent<Rigidbody>();
                if (body != null)
                    sb.Append(",\"rigidbody\":{\"mass\":").Append(F(body.mass)).Append(",\"drag\":").Append(F(body.drag))
                        .Append(",\"angular_drag\":").Append(F(body.angularDrag)).Append(",\"kinematic\":").Append(B(body.isKinematic)).Append("}");
                sb.Append(",\"renderers\":[");
                var first = true; var count = 0;
                foreach (var renderer in GetComponentsInChildren<Renderer>(false))
                {
                    if (renderer == null || !renderer.enabled || count++ >= 24) continue;
                    if (!first) sb.Append(','); first = false;
                    var wide = new MaterialPropertyBlock(); renderer.GetPropertyBlock(wide);
                    sb.Append("{\"id\":").Append(renderer.GetInstanceID()).Append(",\"name\":").Append(Q(renderer.name))
                        .Append(",\"light_probe_usage\":").Append(Q(renderer.lightProbeUsage.ToString()))
                        .Append(",\"wide\":").Append(Block(wide)).Append(",\"slots\":[");
                    var materials = renderer.sharedMaterials;
                    for (var slot = 0; slot < materials.Length && slot < 8; slot++)
                    {
                        if (slot > 0) sb.Append(',');
                        var mat = materials[slot]; var indexed = new MaterialPropertyBlock(); renderer.GetPropertyBlock(indexed, slot);
                        sb.Append("{\"slot\":").Append(slot).Append(",\"indexed\":").Append(Block(indexed));
                        if (mat != null)
                        {
                            sb.Append(",\"material_id\":").Append(mat.GetInstanceID()).Append(",\"material\":").Append(Q(mat.name))
                                .Append(",\"shader\":").Append(Q(mat.shader != null ? mat.shader.name : ""));
                            if (mat.HasProperty("_Color")) { var c = mat.GetColor("_Color"); sb.Append(",\"color\":").Append(V(c)); }
                            if (mat.HasProperty("_MainTex")) { var tex = mat.GetTexture("_MainTex"); sb.Append(",\"texture_id\":").Append(tex != null ? tex.GetInstanceID() : 0); }
                        }
                        sb.Append('}');
                    }
                    sb.Append("]}");
                }
                sb.Append("]}");
                var row = _rows++;
                RegistrySnapshotFormat.PublishCaptureFile(_out, "visual-diagnostics-sr1-vacuum-" + gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture) + "-" + row.ToString("D2", CultureInfo.InvariantCulture) + ".json", sb.ToString());
            }
            catch (Exception error)
            {
                _rows = 16;
                Debug.LogWarning("[SlimeBridge] SR1 vacuum observer refused: " + error.GetType().Name + ": " + error.Message);
            }
        }
    }
}
