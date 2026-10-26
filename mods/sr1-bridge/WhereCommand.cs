using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using BridgeConsole = SRML.Console.Console;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// In-game console command <c>where [radius]</c>: prints the player's position, heading and region, and
    /// lists the objects around (scene path, distance) in the console and Player.log so a spot can be located
    /// and its native objects identified. Read-only.
    /// </summary>
    internal sealed class WhereCommand : SRML.Console.ConsoleCommand
    {
        public override string ID => "where";
        public override string Usage => "where [radius]";
        public override string Description => "Prints your position, region and the objects around you (default radius 12 m)";

        public override bool Execute(string[] args)
        {
            try
            {
                var scene = SceneContext.Instance;
                if (scene == null || scene.Player == null) { BridgeConsole.LogError("[SlimeBridge] no active game session."); return false; }
                float radius = 12f;
                if (args != null && args.Length > 0) float.TryParse(args[0], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out radius);
                radius = Mathf.Clamp(radius, 2f, 40f);

                var player = scene.Player.transform;
                var p = player.position;
                var cam = Camera.main;
                var look = cam != null ? cam.transform.forward : player.forward;
                var inv = System.Globalization.CultureInfo.InvariantCulture;
                string region = "?";
                try
                {
                    var registry = UnityEngine.Object.FindObjectOfType<MonomiPark.SlimeRancher.Regions.RegionRegistry>();
                    if (registry != null) region = registry.GetCurrentRegionSetId().ToString();
                }
                catch (Exception) { }
                var head = string.Format(inv, "[SlimeBridge] where: pos=({0:F1}, {1:F1}, {2:F1}) yaw={3:F0} look=({4:F2}, {5:F2}, {6:F2}) region={7}",
                    p.x, p.y, p.z, player.eulerAngles.y, look.x, look.y, look.z, region);
                BridgeConsole.Log(head);
                UnityEngine.Debug.Log(head);

                var seen = new Dictionary<string, float>();
                foreach (var c in Physics.OverlapSphere(p, radius, ~0, QueryTriggerInteraction.Collide))
                {
                    if (c == null) continue;
                    var path = PathOf(c.transform);
                    var d = Vector3.Distance(p, c.bounds.ClosestPoint(p));
                    if (!seen.TryGetValue(path, out var old) || d < old) seen[path] = d;
                }
                foreach (var r in UnityEngine.Object.FindObjectsOfType<Renderer>())
                {
                    if (r == null || !r.enabled) continue;
                    var d = Vector3.Distance(p, r.bounds.ClosestPoint(p));
                    if (d > radius) continue;
                    var path = PathOf(r.transform);
                    if (!seen.TryGetValue(path, out var old) || d < old) seen[path] = d;
                }
                var lines = seen.OrderBy(kv => kv.Value).Take(80).ToList();
                var sb = new StringBuilder("[SlimeBridge] where: " + seen.Count + " objects within " + radius.ToString("F0", inv) + " m (nearest " + lines.Count + "):");
                foreach (var kv in lines) sb.Append("\n  ").Append(kv.Value.ToString("F1", inv)).Append(" m  ").Append(kv.Key);
                UnityEngine.Debug.Log(sb.ToString());
                BridgeConsole.Log("[SlimeBridge] " + seen.Count + " nearby objects written to Player.log.");
                return true;
            }
            catch (Exception ex)
            {
                BridgeConsole.LogError("[SlimeBridge] where failed: " + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
        }

        private static string PathOf(Transform t)
        {
            var parts = new List<string>();
            for (int i = 0; t != null && i < 5; i++, t = t.parent) parts.Add(t.name);
            parts.Reverse();
            return string.Join("/", parts);
        }
    }
}
