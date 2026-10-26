using System;
using UnityEngine;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Optional, player-chosen performance profile (launcher option, passed as <c>SLIMEBRIDGE_PERF</c>: off | balanced | fast). Off (the default)
    /// touches nothing. It only moves global quality knobs the game's own menu already exposes, on the player's machine, and re-applies them when
    /// the player switches quality level:
    /// <list type="bullet">
    /// <item><b>balanced</b>: shadow distance capped at 30, soft particles off, a larger texture upload slice (fewer streaming hitches), and one
    /// garbage collection once the game has settled (the cost moves from the middle of play to a quiet moment).</item>
    /// <item><b>fast</b>: balanced plus no multisampling, low shadow resolution, at most two shadow cascades and one pixel light.</item>
    /// </list>
    /// Same file in both plugins (QualitySettings and GC exist in the Mono and IL2CPP builds).
    /// </summary>
    internal static class PerformanceTuner
    {
        private const float SettleSeconds = 12f;
        private static int _appliedLevel = -1;
        private static string? _mode;

        internal static string Mode
        {
            get
            {
                if (_mode == null)
                {
                    var raw = (Environment.GetEnvironmentVariable("SLIMEBRIDGE_PERF") ?? "").Trim().ToLowerInvariant();
                    _mode = raw == "balanced" || raw == "fast" ? raw : "off";
                }
                return _mode;
            }
        }

        /// <summary>Call every frame (cheap): applies the profile once after start-up and again after a quality-level change.</summary>
        internal static void Tick(float realtimeSinceStartup, Action<string> log)
        {
            if (Mode == "off" || realtimeSinceStartup < SettleSeconds) return;
            try
            {
                var level = QualitySettings.GetQualityLevel();
                if (level == _appliedLevel) return;
                var first = _appliedLevel < 0;
                _appliedLevel = level;
                log(Apply(Mode, level) + (first ? "" : " (quality level changed)"));
            }
            catch (Exception ex) { _appliedLevel = int.MaxValue; log("[SlimeBridge] performance profile failed: " + ex.GetType().Name); }
        }

        internal static string Apply(string mode, int level)
        {
            var before = Describe();
            if (QualitySettings.shadowDistance > 30f) QualitySettings.shadowDistance = 30f;
            QualitySettings.softParticles = false;
            if (QualitySettings.asyncUploadTimeSlice < 4) QualitySettings.asyncUploadTimeSlice = 4;
            if (QualitySettings.asyncUploadBufferSize < 16) QualitySettings.asyncUploadBufferSize = 16;
            if (mode == "fast")
            {
                QualitySettings.antiAliasing = 0;
                if (QualitySettings.shadowResolution > ShadowResolution.Low) QualitySettings.shadowResolution = ShadowResolution.Low;
                if (QualitySettings.shadowCascades > 2) QualitySettings.shadowCascades = 2;
                if (QualitySettings.pixelLightCount > 1) QualitySettings.pixelLightCount = 1;
            }
            GC.Collect();
            return "[SlimeBridge] performance profile '" + mode + "' (quality level " + level + "): " + before + " -> " + Describe();
        }

        private static string Describe() =>
            "shadows " + QualitySettings.shadowDistance.ToString("F0") + " m/" + QualitySettings.shadowResolution + "/" + QualitySettings.shadowCascades +
            " cascades, msaa " + QualitySettings.antiAliasing + ", pixel lights " + QualitySettings.pixelLightCount +
            ", soft particles " + QualitySettings.softParticles + ", upload " + QualitySettings.asyncUploadTimeSlice + " ms/" + QualitySettings.asyncUploadBufferSize + " MB";
    }
}
