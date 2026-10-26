using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// LAB-only proof recorder for continuous source animation. Attached only on the exact LAB
    /// executable in rest-pose mode, never in a normal game. Every 0.1 s (LateUpdate, after the
    /// legacy Animation pass of the same frame) it records the playing clip name, its state time and
    /// the local TRS of every imported bone, so a host-side oracle can compare the live Unity result
    /// against the decoded source clip at the reported clip time. It writes nothing itself.
    /// </summary>
    internal sealed class LabSourceAnimationTrace : MonoBehaviour
    {
        private const int MaxRows = 40;
        private const float Interval = 0.1f;
        private const int MaxBones = 64;
        private static int _attached;
        private static int _completed;

        private readonly List<string> _rows = new List<string>(MaxRows);
        private ImportedSourceAnimation? _player;
        private Transform[]? _bones;
        private float _next;
        private bool _counted;
        private bool _registered;

        internal static bool HasAttached => _attached > 0;
        internal static bool AllComplete => _attached > 0 && _completed >= _attached;

        internal static void Attach(GameObject visual)
        {
            if (visual == null || visual.GetComponent<ImportedSourceAnimation>() == null ||
                visual.GetComponent<LabSourceAnimationTrace>() != null) return;
            visual.AddComponent<LabSourceAnimationTrace>();
        }

        // Registration happens on first enable: the registered template's visual sits under an
        // inactive container and never enables, so it must neither consume the budget nor block
        // completion. Two live actors are enough to compare independent clocks.
        private void OnEnable()
        {
            if (_registered) return;
            if (_attached >= 2) { Destroy(this); return; }
            _registered = true;
            _attached++;
        }

        private void LateUpdate()
        {
            if (!_registered || _rows.Count >= MaxRows) return;
            var now = Time.realtimeSinceStartup;
            if (now < _next) return;
            if (_player == null) _player = GetComponent<ImportedSourceAnimation>();
            if (_player == null || !_player.TryReadPlayback(out var clipName, out var clipTime, out var length)) return;
            if (_bones == null)
            {
                var found = new List<Transform>();
                foreach (var child in GetComponentsInChildren<Transform>(true))
                {
                    if (child == null || !child.name.StartsWith("bone_", System.StringComparison.Ordinal)) continue;
                    if (found.Count >= MaxBones) break;
                    found.Add(child);
                }
                _bones = found.ToArray();
            }
            _next = now + Interval;
            var sb = new StringBuilder(2048);
            sb.Append("{\"t\": ").Append(F(now)).Append(", \"clip\": ").Append(Quote(clipName))
              .Append(", \"state_time\": ").Append(F(clipTime)).Append(", \"length\": ").Append(F(length))
              .Append(", \"locals\": [");
            for (var i = 0; i < _bones.Length; i++)
            {
                var bone = _bones[i];
                if (i > 0) sb.Append(", ");
                var p = bone != null ? bone.localPosition : Vector3.zero;
                var q = bone != null ? bone.localRotation : Quaternion.identity;
                sb.Append("[").Append(F(p.x)).Append(", ").Append(F(p.y)).Append(", ").Append(F(p.z)).Append(", ")
                  .Append(F(q.x)).Append(", ").Append(F(q.y)).Append(", ").Append(F(q.z)).Append(", ").Append(F(q.w)).Append("]");
            }
            sb.Append("]}");
            _rows.Add(sb.ToString());
            if (_rows.Count >= MaxRows && !_counted) { _counted = true; _completed++; }
        }

        internal string ToJson()
        {
            var names = new List<string>();
            if (_bones != null)
                foreach (var bone in _bones) names.Add(Quote(bone != null ? bone.name : ""));
            return "{\"complete\": " + (_rows.Count >= MaxRows ? "true" : "false") +
                   ", \"interval\": " + F(Interval) +
                   ", \"bone_names\": [" + string.Join(", ", names.ToArray()) + "]" +
                   ", \"rows\": [" + string.Join(", ", _rows.ToArray()) + "]}";
        }

        private static string F(float value) => value.ToString("R", CultureInfo.InvariantCulture);

        private static string Quote(string value) => "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
