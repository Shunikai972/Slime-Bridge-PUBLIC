using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Sanctuary quest progress, kept in a Bridge-owned sidecar next to (never inside) the native save:
    /// gate payment, which statues are filled, the frozen puzzle manifest and the portal unlock.
    /// Pure logic + atomic file I/O so the SR1 plugin, the host and the tests share one implementation.
    /// </summary>
    public sealed class SanctuaryProgress
    {
        public const int SchemaVersion = 1;

        public string SaveKey = "";
        public int GoldDeposited;
        public bool GatePaid;
        public bool PortalUnlocked;
        public bool PortalUsed;
        public readonly HashSet<string> FilledStatues = new HashSet<string>(StringComparer.Ordinal);
        /// <summary>Statues the puzzle required when it was first created (frozen unless dynamic requirements are enabled).</summary>
        public readonly List<string> RequiredManifest = new List<string>();
        public string KitSha256 = "";
        public string UpdatedUtc = "";

        public static string SanitizeSaveKey(string? raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return "unknown";
            var sb = new StringBuilder();
            foreach (var c in raw!)
                if (char.IsLetterOrDigit(c) || c == '_' || c == '-') sb.Append(c);
            return sb.Length == 0 ? "unknown" : sb.ToString(0, Math.Min(sb.Length, 96));
        }

        /// <summary>Freeze the requirement list on first use; later layouts do not change a finished puzzle.</summary>
        public void EnsureManifest(IEnumerable<string> required, string kitSha256, bool dynamic)
        {
            if (RequiredManifest.Count == 0 || dynamic)
            {
                RequiredManifest.Clear();
                RequiredManifest.AddRange(required);
                RequiredManifest.Sort(StringComparer.Ordinal);
                KitSha256 = kitSha256;
            }
        }

        /// <summary>Deposit gold plorts toward the gate. Returns true when this deposit completes the payment.</summary>
        public bool DepositGold(int count, int cost)
        {
            if (count <= 0 || GatePaid) return false;
            GoldDeposited = Math.Min(cost, checked(GoldDeposited + count));
            if (GoldDeposited >= cost) { GatePaid = true; return true; }
            return false;
        }

        /// <summary>Mark a statue filled. Returns false when it was already filled or is not part of the manifest.</summary>
        public bool FillStatue(string slime, bool required)
        {
            if (required && RequiredManifest.Count > 0 && !RequiredManifest.Contains(slime)) return false;
            return FilledStatues.Add(slime);
        }

        public bool AllRequiredFilled()
        {
            if (RequiredManifest.Count == 0) return false;
            foreach (var slime in RequiredManifest)
                if (!FilledStatues.Contains(slime)) return false;
            return true;
        }

        public int FilledRequiredCount()
        {
            var n = 0;
            foreach (var slime in RequiredManifest) if (FilledStatues.Contains(slime)) n++;
            return n;
        }

        /// <summary>Unlock the portal once every required statue is filled. Returns true on the transition.</summary>
        public bool TryUnlockPortal()
        {
            if (PortalUnlocked || !AllRequiredFilled()) return false;
            PortalUnlocked = true;
            return true;
        }

        public void DevUnlockAll()
        {
            GatePaid = true;
            foreach (var slime in RequiredManifest) FilledStatues.Add(slime);
            PortalUnlocked = AllRequiredFilled();
        }

        public void DevReset()
        {
            GoldDeposited = 0; GatePaid = false; PortalUnlocked = false; PortalUsed = false;
            FilledStatues.Clear();
        }

        // ----- persistence
        public string ToJson()
        {
            var sb = new StringBuilder();
            sb.Append("{\"schema\":\"bridge-sanctuary-progress\",\"schema_version\":").Append(SchemaVersion);
            sb.Append(",\"save_key\":").Append(Quote(SaveKey));
            sb.Append(",\"kit_sha256\":").Append(Quote(KitSha256));
            sb.Append(",\"gold_deposited\":").Append(GoldDeposited.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"gate_paid\":").Append(GatePaid ? "true" : "false");
            sb.Append(",\"portal_unlocked\":").Append(PortalUnlocked ? "true" : "false");
            sb.Append(",\"portal_used\":").Append(PortalUsed ? "true" : "false");
            sb.Append(",\"required\":").Append(StringArray(RequiredManifest));
            var filled = new List<string>(FilledStatues);
            filled.Sort(StringComparer.Ordinal);
            sb.Append(",\"filled\":").Append(StringArray(filled));
            sb.Append(",\"updated_utc\":").Append(Quote(UpdatedUtc)).Append('}');
            return sb.ToString();
        }

        public static SanctuaryProgress FromJson(string json)
        {
            var root = J.Obj(MiniJson.Parse(json), "progress");
            if (J.Str(root, "schema") != "bridge-sanctuary-progress") throw new FormatException("wrong schema");
            var version = J.Int(root, "schema_version");
            if (version != SchemaVersion) throw new FormatException("unsupported progress schema version " + version);
            var p = new SanctuaryProgress
            {
                SaveKey = J.Str(root, "save_key"), KitSha256 = J.Str(root, "kit_sha256"),
                GoldDeposited = J.Int(root, "gold_deposited"), GatePaid = J.Bool(root, "gate_paid"),
                PortalUnlocked = J.Bool(root, "portal_unlocked"), PortalUsed = J.Bool(root, "portal_used"),
                UpdatedUtc = J.Str(root, "updated_utc"),
            };
            if (p.GoldDeposited < 0 || p.GoldDeposited > 100000) throw new FormatException("gold out of range");
            foreach (var item in J.Arr(root, "required", 256)) p.RequiredManifest.Add(J.AsStr(item, "required"));
            foreach (var item in J.Arr(root, "filled", 256)) p.FilledStatues.Add(J.AsStr(item, "filled"));
            if (p.PortalUnlocked && !p.AllRequiredFilled()) throw new FormatException("portal unlocked without every statue");
            return p;
        }

        public static string PathFor(string directory, string saveKey) =>
            Path.Combine(directory, "sanctuary-" + SanitizeSaveKey(saveKey) + ".json");

        /// <summary>Load progress; a corrupt file is moved aside (never deleted) and a fresh progress is returned.</summary>
        public static SanctuaryProgress LoadOrCreate(string directory, string saveKey, out string? problem)
        {
            problem = null;
            var path = PathFor(directory, saveKey);
            var key = SanitizeSaveKey(saveKey);
            if (File.Exists(path))
            {
                try
                {
                    var loaded = FromJson(File.ReadAllText(path, Encoding.UTF8));
                    if (loaded.SaveKey == key) return loaded;
                    problem = "progress belongs to another save";
                }
                catch (Exception ex) when (ex is FormatException || ex is IOException || ex is UnauthorizedAccessException || ex is OverflowException)
                {
                    problem = "progress unreadable: " + ex.Message;
                }
                try
                {
                    File.Move(path, path + ".corrupt-" + DateTime.UtcNow.ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture));
                }
                catch (IOException) { }
            }
            return new SanctuaryProgress { SaveKey = key };
        }

        public void Save(string directory)
        {
            Directory.CreateDirectory(directory);
            UpdatedUtc = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            var path = PathFor(directory, SaveKey);
            var temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(temp, ToJson(), new UTF8Encoding(false));
            try
            {
                if (File.Exists(path)) File.Replace(temp, path, null);
                else File.Move(temp, path);
            }
            finally
            {
                if (File.Exists(temp)) File.Delete(temp);
            }
        }

        private static string Quote(string value)
        {
            var sb = new StringBuilder("\"");
            foreach (var c in value)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                else sb.Append(c);
            }
            return sb.Append('"').ToString();
        }

        private static string StringArray(List<string> values)
        {
            var sb = new StringBuilder("[");
            for (var i = 0; i < values.Count; i++) { if (i > 0) sb.Append(','); sb.Append(Quote(values[i])); }
            return sb.Append(']').ToString();
        }
    }
}
