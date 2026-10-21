using System;
using System.Globalization;
using System.Linq;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Always-on, almost free frame-time watchdog. Every 30 s window it keeps a histogram of frame times; when the window was slow
    /// (average above 20 ms, or one frame in ten above 33 ms) it writes ONE line to the game log with the numbers and what the scene
    /// holds (actors, imported actors, renderers, lights, rigidbodies), at most once a minute. A player's "it lags" then comes with
    /// evidence instead of a guess. <c>SLIMEBRIDGE_FRAMEWATCH=1</c> logs every window (LAB runs).
    /// </summary>
    internal static class FrameWatch
    {
        private const float WindowSeconds = 30f;
        private static readonly float[] Bounds = { 5f, 8f, 12f, 16.7f, 25f, 33f, 50f, 100f };      // ms
        private static readonly int[] Buckets = new int[Bounds.Length + 1];
        private static float _windowStart = -1f, _sum, _max, _nextLog;
        private static int _frames;
        private static readonly bool Always = string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_FRAMEWATCH"), "1", StringComparison.Ordinal);

        internal static void Tick()
        {
            try
            {
                var now = Time.realtimeSinceStartup;
                if (_windowStart < 0f) _windowStart = now;
                var ms = Time.unscaledDeltaTime * 1000f;
                if (ms > 0f && ms < 5000f)
                {
                    _frames++; _sum += ms; if (ms > _max) _max = ms;
                    var i = 0;
                    while (i < Bounds.Length && ms >= Bounds[i]) i++;
                    Buckets[i]++;
                }
                if (now - _windowStart < WindowSeconds) return;
                Report(now);
                _windowStart = now; _sum = 0f; _max = 0f; _frames = 0;
                Array.Clear(Buckets, 0, Buckets.Length);
            }
            catch (Exception) { /* a watchdog never disturbs the game */ }
        }

        private static void Report(float now)
        {
            if (_frames < 30) return;
            var avg = _sum / _frames;
            var slow = 0;
            for (var i = 5; i < Buckets.Length; i++) slow += Buckets[i];                 // frames at 33 ms or more
            var slowShare = slow / (float)_frames;
            // 95th percentile from the histogram (upper bound of its bucket)
            var target = _frames * 0.95f; var running = 0; var p95 = Bounds[Bounds.Length - 1];
            for (var i = 0; i < Buckets.Length; i++)
            {
                running += Buckets[i];
                if (running >= target) { p95 = i < Bounds.Length ? Bounds[i] : _max; break; }
            }
            var slowWindow = avg > 20f || slowShare > 0.10f;
            if (!Always && (!slowWindow || now < _nextLog)) return;
            _nextLog = now + 60f;
            var actors = UnityEngine.Object.FindObjectsOfType<Identifiable>();
            var imported = actors.Count(a => a != null && ImportedIdentityPatch.IsImportedId((int)a.id));
            var renderers = UnityEngine.Object.FindObjectsOfType<Renderer>().Count(r => r != null && r.enabled && r.gameObject.activeInHierarchy);
            var lights = UnityEngine.Object.FindObjectsOfType<Light>().Count(l => l != null && l.enabled && l.gameObject.activeInHierarchy);
            var bodies = UnityEngine.Object.FindObjectsOfType<Rigidbody>().Count(b => b != null && !b.IsSleeping());
            Debug.Log(string.Format(CultureInfo.InvariantCulture,
                "[SlimeBridge] frame watch: {0} frames in {1:F0}s avg {2:F1} ms ({3:F0} fps) p95<={4:F0} ms worst {5:F0} ms, {6:P0} of frames >= 33 ms | actors {7} (imported {8}) renderers {9} lights {10} awake rigidbodies {11} | managed {12:F0} MB{13}",
                _frames, WindowSeconds, avg, 1000f / avg, p95, _max, slowShare, actors.Length, imported, renderers, lights, bodies,
                GC.GetTotalMemory(false) / 1048576.0, slowWindow ? " | SLOW" : ""));
        }
    }
}
