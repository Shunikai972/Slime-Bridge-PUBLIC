using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using SlimeBridge.Shared;

namespace SlimeBridge.SR1
{
    /// <summary>Pure LAB timeline format and config. No Unity objects or state changes.</summary>
    internal static class DiagnosticsTimeline
    {
        internal const string SecondsVariable = "SLIMEBRIDGE_DIAG_TIMELINE_SECONDS";
        internal const int MinSeconds = 5;
        internal const int MaxSeconds = 900;
        internal const int MaxWrites = 256;

        internal static bool TryParseSeconds(string? value, out int seconds, out string? problem)
        {
            seconds = 0;
            problem = null;
            if (string.IsNullOrEmpty(value)) return false;
            if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var parsed) ||
                parsed < MinSeconds || parsed > MaxSeconds)
            {
                problem = SecondsVariable + " must be an integer in [5, 900]";
                return false;
            }
            seconds = parsed;
            return true;
        }

        internal static bool IsSafeFileToken(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Length > 128 || value.Contains("..")) return false;
            foreach (var c in value)
                if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_'))
                    return false;
            return true;
        }

        internal static string FormatLine(float elapsedSeconds, List<KeyValuePair<string, string>> entries)
        {
            var output = new StringBuilder(2048);
            output.Append('{').Append(RegistrySnapshotFormat.Quote("utc")).Append(": ")
                .Append(RegistrySnapshotFormat.Quote(DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture)))
                .Append(", ").Append(RegistrySnapshotFormat.Quote("elapsed_seconds")).Append(": ")
                .Append(elapsedSeconds.ToString("R", CultureInfo.InvariantCulture))
                .Append(", ").Append(RegistrySnapshotFormat.Quote("entries")).Append(": [");
            for (var i = 0; i < entries.Count; i++)
            {
                if (i > 0) output.Append(", ");
                output.Append('{').Append(RegistrySnapshotFormat.Quote("instance")).Append(": ")
                    .Append(RegistrySnapshotFormat.Quote(entries[i].Key)).Append(", ")
                    .Append(RegistrySnapshotFormat.Quote("petrification")).Append(": ").Append(entries[i].Value).Append('}');
            }
            return output.Append("]}").ToString();
        }
    }
}
