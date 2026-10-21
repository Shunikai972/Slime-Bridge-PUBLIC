using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>One native slime definition's diet as observed at runtime (read-only LAB capture).</summary>
    public sealed class DietEntry
    {
        public sealed class GroupMembers
        {
            public string Group = "";
            public List<string> Members = new List<string>();
        }

        public string NativeRef = "";
        public long? NumericId;
        public string DisplayName = "";
        public bool IsLargo;
        public bool CanLargofy;
        public List<string> MajorFoodGroups = new List<string>();
        public List<GroupMembers> MajorFoodGroupMembers = new List<GroupMembers>();
        public List<string> Favorites = new List<string>();
        public int FavoriteProductionCount;
        public List<string> Produces = new List<string>();
        public List<string> AdditionalFoods = new List<string>();
    }

    /// <summary>
    /// Serializer for the additive, read-only diet capture (bridge-diet-snapshot v1). Deliberately
    /// separate from the pinned registry snapshot pair so its canonical hash format stays untouched.
    /// </summary>
    public static class DietSnapshotFormat
    {
        public const string FileName = RegistrySnapshotFormat.DietSnapshotFileName;
        public const string Schema = "bridge-diet-snapshot";
        public const int SchemaVersion = 1;

        public static string Json(string realm, string runId, string activeSha256, string collectorVersion,
                                  IList<DietEntry> entries, IList<string> errors)
        {
            var sb = new StringBuilder(entries.Count * 512 + 256);
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
                sb.Append(", \"numeric_id\": ").Append(e.NumericId.HasValue ? e.NumericId.Value.ToString(CultureInfo.InvariantCulture) : "null");
                sb.Append(", \"display_name\": ").Append(RegistrySnapshotFormat.Quote(e.DisplayName));
                sb.Append(", \"is_largo\": ").Append(e.IsLargo ? "true" : "false");
                sb.Append(", \"can_largofy\": ").Append(e.CanLargofy ? "true" : "false");
                sb.Append(", \"major_food_groups\": [").Append(StringList(e.MajorFoodGroups)).Append("]");
                sb.Append(", \"major_food_group_members\": [");
                for (var m = 0; m < e.MajorFoodGroupMembers.Count; m++)
                {
                    if (m > 0) sb.Append(", ");
                    sb.Append("{\"group\": ").Append(RegistrySnapshotFormat.Quote(e.MajorFoodGroupMembers[m].Group))
                      .Append(", \"members\": [").Append(StringList(e.MajorFoodGroupMembers[m].Members)).Append("]}");
                }
                sb.Append("]");
                sb.Append(", \"favorites\": [").Append(StringList(e.Favorites)).Append("]");
                sb.Append(", \"favorite_production_count\": ").Append(e.FavoriteProductionCount.ToString(CultureInfo.InvariantCulture));
                sb.Append(", \"produces\": [").Append(StringList(e.Produces)).Append("]");
                sb.Append(", \"additional_foods\": [").Append(StringList(e.AdditionalFoods)).Append("]}");
            }
            sb.Append(entries.Count == 0 ? "]" : "\n  ]");
            sb.Append(",\n  \"errors\": [").Append(StringList(errors)).Append("]\n}\n");
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
