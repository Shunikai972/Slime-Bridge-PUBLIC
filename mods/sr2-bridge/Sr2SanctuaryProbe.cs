using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// LAB-only, read-only look at the live SR2 world around the player (named renderers, meshes, materials, positions) so a
    /// return portal can be placed from facts. Exact LAB executable + an output folder under Lab/RuntimeArtifacts only.
    /// Writes <c>sr2-world-probe.json</c> and one screenshot, then stays idle.
    /// </summary>
    internal sealed class Sr2SanctuaryProbe
    {
        private readonly string _out;
        private float _readySince = -1f;
        private bool _done, _shot;
        private float _shotAt;

        private Sr2SanctuaryProbe(string outDir) { _out = outDir; }

        internal static Sr2SanctuaryProbe? TryCreate(bool labExecutable)
        {
            if (!labExecutable) return null;
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR2_PROBE_OUT");
            if (string.IsNullOrEmpty(outDir) || !Directory.Exists(outDir)) return null;
            var root = LabSpecPathGuard.FindRepoRoot();
            if (root == null || !LabSpecPathGuard.IsUnderRoot(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), outDir, out _)) return null;
            return new Sr2SanctuaryProbe(outDir);
        }

        private static bool WorldReady()
        {
            try
            {
                var scene = Il2Cpp.SceneContext.Instance;
                var game = Il2Cpp.GameContext.Instance;
                return game != null && scene != null && scene.Player != null && scene.PlayerState != null;
            }
            catch (Exception) { return false; }
        }

        internal void Tick(Action<string> log)
        {
            if (_done) return;
            try
            {
                if (!WorldReady()) { _readySince = -1f; return; }
                if (_readySince < 0f) _readySince = Time.realtimeSinceStartup;
                if (Time.realtimeSinceStartup - _readySince < 30f) return;
                if (!_shot)
                {
                    var file = Path.Combine(_out, "sr2-view.png");
                    ScreenCapture.CaptureScreenshot(file, 1);
                    _shot = true; _shotAt = Time.realtimeSinceStartup;
                    return;
                }
                if (Time.realtimeSinceStartup - _shotAt < 2f) return;
                Probe(log);
                _done = true;
            }
            catch (Exception ex)
            {
                _done = true;
                log("[SlimeBridge] SR2 probe failed: " + ex);
                File.WriteAllText(Path.Combine(_out, "sr2-world-probe-error.txt"), ex.ToString());
            }
        }

        private static string Esc(string s) => BridgeJson.Escape(s ?? "");
        private static string F(float v) => v.ToString("0.###", CultureInfo.InvariantCulture);

        private void Probe(Action<string> log)
        {
            var scene = Il2Cpp.SceneContext.Instance;
            var player = scene.Player.transform;
            var p = player.position;
            var sb = new StringBuilder("{\"schema\":\"bridge-sr2-world-probe\",\"schema_version\":1");
            sb.Append(",\"player\":{\"position\":[").Append(F(p.x)).Append(',').Append(F(p.y)).Append(',').Append(F(p.z)).Append("],\"yaw\":").Append(F(player.eulerAngles.y)).Append('}');
            var groupName = "";
            try { groupName = Il2Cpp.GameContext.Instance.AutoSaveDirector.CurrentGameName() ?? ""; } catch (Exception) { }
            sb.Append(",\"save\":\"").Append(Esc(groupName)).Append('"');
            sb.Append(",\"active_scene\":\"").Append(Esc(UnityEngine.SceneManagement.SceneManager.GetActiveScene().name)).Append('"');
            var renderers = UnityEngine.Object.FindObjectsOfType<MeshRenderer>();
            var rows = new List<(float d, string json)>();
            foreach (var r in renderers)
            {
                if (r == null) continue;
                var go = r.gameObject;
                var pos = go.transform.position;
                var d = Vector3.Distance(pos, p);
                if (d > 140f) continue;
                var filter = go.GetComponent<MeshFilter>();
                var mesh = filter != null && filter.sharedMesh != null ? filter.sharedMesh : null;
                var mats = new List<string>();
                foreach (var m in r.sharedMaterials) mats.Add(m != null ? m.name : "");
                var path = new StringBuilder(go.name);
                var t = go.transform.parent;
                for (var depth = 0; t != null && depth < 6; depth++, t = t.parent) path.Insert(0, t.name + "/");
                var ls = go.transform.lossyScale;
                var b = mesh != null ? mesh.bounds : default(Bounds);
                rows.Add((d, "{\"name\":\"" + Esc(go.name) + "\",\"path\":\"" + Esc(path.ToString()) + "\",\"mesh\":\"" + Esc(mesh != null ? mesh.name : "") +
                    "\",\"materials\":[" + string.Join(",", mats.Select(m => "\"" + Esc(m) + "\"")) + "],\"position\":[" + F(pos.x) + "," + F(pos.y) + "," + F(pos.z) +
                    "],\"scale\":[" + F(ls.x) + "," + F(ls.y) + "," + F(ls.z) + "],\"yaw\":" + F(go.transform.eulerAngles.y) +
                    ",\"bounds\":[" + F(b.size.x) + "," + F(b.size.y) + "," + F(b.size.z) + "],\"distance\":" + F(d) + "}"));
            }
            // Anything portal-like anywhere in the loaded world (native SR2 portals / telepads), with its materials and bounds.
            var portalRows = new List<string>();
            foreach (var r in renderers)
            {
                if (r == null) continue;
                var go = r.gameObject;
                var filter = go.GetComponent<MeshFilter>();
                var mesh = filter != null && filter.sharedMesh != null ? filter.sharedMesh : null;
                var mats = new List<string>();
                foreach (var m in r.sharedMaterials) mats.Add(m != null ? m.name : "");
                var key = (go.name + "|" + (mesh != null ? mesh.name : "") + "|" + string.Join("|", mats)).ToLowerInvariant();
                if (!(key.Contains("portal") || key.Contains("telepad") || key.Contains("teleport") || key.Contains("ancientteleporter") || key.Contains("orbital"))) continue;
                var pos = go.transform.position; var ls = go.transform.lossyScale; var b = mesh != null ? mesh.bounds : default(Bounds);
                var path = new StringBuilder(go.name);
                var t = go.transform.parent;
                for (var depth = 0; t != null && depth < 6; depth++, t = t.parent) path.Insert(0, t.name + "/");
                portalRows.Add("{\"name\":\"" + Esc(go.name) + "\",\"path\":\"" + Esc(path.ToString()) + "\",\"mesh\":\"" + Esc(mesh != null ? mesh.name : "") +
                    "\",\"materials\":[" + string.Join(",", mats.Select(m => "\"" + Esc(m) + "\"")) + "],\"position\":[" + F(pos.x) + "," + F(pos.y) + "," + F(pos.z) +
                    "],\"scale\":[" + F(ls.x) + "," + F(ls.y) + "," + F(ls.z) + "],\"yaw\":" + F(go.transform.eulerAngles.y) +
                    ",\"bounds\":[" + F(b.size.x) + "," + F(b.size.y) + "," + F(b.size.z) + "],\"distance\":" + F(Vector3.Distance(pos, p)) + "}");
            }
            sb.Append(",\"portal_like\":[").Append(string.Join(",", portalRows.Take(300))).Append(']');
            sb.Append(",\"renderers\":[").Append(string.Join(",", rows.OrderBy(r => r.d).Take(900).Select(r => r.json))).Append(']');
            var names = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var mesh in Resources.FindObjectsOfTypeAll<Mesh>())
            {
                if (mesh == null || string.IsNullOrEmpty(mesh.name)) continue;
                var n = mesh.name.ToLowerInvariant();
                if (n.Contains("ruin") || n.Contains("portal") || n.Contains("gate") || n.Contains("arch") || n.Contains("temple") || n.Contains("tele") ||
                    n.Contains("pillar") || n.Contains("statue") || n.Contains("conserv") || n.Contains("plat") || n.Contains("bridge") || n.Contains("rock")) names.Add(mesh.name);
            }
            sb.Append(",\"interesting_meshes\":[").Append(string.Join(",", names.Take(1500).Select(n => "\"" + Esc(n) + "\""))).Append(']');
            var mats2 = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var m in Resources.FindObjectsOfTypeAll<Material>())
            {
                if (m == null || string.IsNullOrEmpty(m.name)) continue;
                var n = m.name.ToLowerInvariant();
                if (n.Contains("ruin") || n.Contains("portal") || n.Contains("gate") || n.Contains("temple") || n.Contains("tele") || n.Contains("glow") || n.Contains("stone")) mats2.Add(m.name);
            }
            sb.Append(",\"interesting_materials\":[").Append(string.Join(",", mats2.Take(800).Select(n => "\"" + Esc(n) + "\""))).Append("]}");
            File.WriteAllText(Path.Combine(_out, "sr2-world-probe.json"), sb.ToString(), new UTF8Encoding(false));
            log("[SlimeBridge] SR2 world probe written (" + rows.Count + " renderers within 140 m)");
        }
    }

    internal static class BridgeJson
    {
        internal static string Escape(string value)
        {
            var sb = new StringBuilder(value.Length + 8);
            foreach (var c in value)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                else sb.Append(c);
            }
            return sb.ToString();
        }
    }
}
