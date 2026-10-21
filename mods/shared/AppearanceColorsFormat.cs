using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>One slime's canonical appearance colors as observed at runtime (read-only LAB capture).</summary>
    public sealed class AppearanceEntry
    {
        public string NativeRef = "";
        public string DisplayName = "";
        public string SaveSet = "";
        /// <summary>Legacy SlimeAppearance.ColorPalette (top, middle, bottom, ammo as rgba floats).</summary>
        public float[] LegacyPalette = new float[0];
        /// <summary>SlimeAppearance.ColorPaletteNew when present on the same appearance.</summary>
        public float[] NewPalette = new float[0];
    }

    /// <summary>
    /// Serializer for the additive, read-only appearance-colors capture (bridge-appearance-colors
    /// v1). Deliberately separate from the pinned registry snapshot pair and the diet snapshot so
    /// their formats stay untouched. Colors are the runtime values of the chosen default appearance
    /// (palette structs only; no textures, no geometry).
    /// </summary>
    public static class AppearanceColorsFormat
    {
        public const string FileName = RegistrySnapshotFormat.AppearanceColorsFileName;
        public const string Schema = "bridge-appearance-colors";
        public const int SchemaVersion = 1;

        public static string Json(string realm, string runId, string activeSha256, string collectorVersion,
                                  IList<AppearanceEntry> entries, IList<string> errors)
        {
            var sb = new StringBuilder(entries.Count * 256 + 256);
            sb.Append("{\n  \"schema\": ").Append(RegistrySnapshotFormat.Quote(Schema));
            sb.Append(",\n  \"schema_version\": ").Append(SchemaVersion);
            sb.Append(",\n  \"realm\": ").Append(RegistrySnapshotFormat.Quote(realm));
            sb.Append(",\n  \"run_id\": ").Append(RegistrySnapshotFormat.Quote(runId));
            sb.Append(",\n  \"collector_version\": ").Append(RegistrySnapshotFormat.Quote(collectorVersion));
            sb.Append(",\n  \"active_sha256\": ").Append(RegistrySnapshotFormat.Quote(activeSha256));
            sb.Append(",\n  \"entries\": [");
            for (var i = 0; i < entries.Count; i++)
            {
                var e = entries[i];
                sb.Append(i == 0 ? "\n" : ",\n");
                sb.Append("    {\"native_ref\": ").Append(RegistrySnapshotFormat.Quote(e.NativeRef));
                sb.Append(", \"display_name\": ").Append(RegistrySnapshotFormat.Quote(e.DisplayName));
                sb.Append(", \"save_set\": ").Append(RegistrySnapshotFormat.Quote(e.SaveSet));
                sb.Append(", \"palette_legacy\": ").Append(Floats(e.LegacyPalette));
                sb.Append(", \"palette_new\": ").Append(Floats(e.NewPalette)).Append("}");
            }
            sb.Append(entries.Count == 0 ? "]" : "\n  ]");
            sb.Append(",\n  \"errors\": [").Append(StringList(errors)).Append("]\n}\n");
            return sb.ToString();
        }

        private static string Floats(float[] values)
        {
            if (values == null || values.Length == 0) return "null";
            var sb = new StringBuilder(48);
            sb.Append('[');
            for (var i = 0; i < values.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(values[i].ToString("R", CultureInfo.InvariantCulture));
            }
            sb.Append(']');
            return sb.ToString();
        }

        private static string StringList(IList<string> items)
        {
            var sb = new StringBuilder();
            for (var i = 0; i < items.Count; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(RegistrySnapshotFormat.Quote(items[i]));
            }
            return sb.ToString();
        }
    }
}
