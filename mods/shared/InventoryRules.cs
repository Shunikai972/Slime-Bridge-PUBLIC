using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace SlimeBridge.Shared
{
    // Pure planning and receipt rules. Native adapters supply slot snapshots and full parsed save data.
    internal readonly struct SlotSnapshot
    {
        public readonly bool Usable, Matching, Empty, Accepts;
        public readonly int Count, Capacity;
        public SlotSnapshot(bool usable, bool matching, bool empty, bool accepts, int count, int capacity)
        { Usable = usable; Matching = matching; Empty = empty; Accepts = accepts; Count = count; Capacity = capacity; }
    }

    internal readonly struct SlotStep
    {
        public readonly int Index, Amount;
        public SlotStep(int index, int amount) { Index = index; Amount = amount; }
    }

    internal readonly struct SaveReadback
    {
        public readonly string File, Identity, DiskPath;
        public readonly int Count;
        public readonly byte[] Bytes;
        public SaveReadback(string file, string identity, int count, byte[] bytes, string diskPath = "")
        { File = file; Identity = identity; Count = count; Bytes = bytes; DiskPath = diskPath; }
    }

    internal readonly struct SaveProof
    {
        public readonly bool Verified;
        public readonly string SaveFile, Sha256, Reason;
        public SaveProof(bool verified, string file, string hash, string reason)
        { Verified = verified; SaveFile = file; Sha256 = hash; Reason = reason; }
    }

    internal static class InventoryRules
    {
        public static List<SlotStep>? PlanRemove(IReadOnlyList<SlotSnapshot> slots, int count)
        {
            if (count < 1 || count > 30) return null;
            var steps = new List<SlotStep>();
            var remaining = count;
            for (var i = 0; i < slots.Count && remaining > 0; i++)
            {
                var s = slots[i];
                if (!s.Usable || !s.Matching || s.Count <= 0) continue;
                var take = Math.Min(s.Count, remaining);
                steps.Add(new SlotStep(i, take));
                remaining -= take;
            }
            return remaining == 0 ? steps : null;
        }

        public static List<SlotStep>? PlanAdd(IReadOnlyList<SlotSnapshot> slots, int count)
        {
            if (count < 1 || count > 30) return null;
            var steps = new List<SlotStep>();
            var remaining = count;
            // Native add prefers existing matching stacks, then empty usable slots.
            for (var pass = 0; pass < 2 && remaining > 0; pass++)
                for (var i = 0; i < slots.Count && remaining > 0; i++)
                {
                    var s = slots[i];
                    if (!s.Usable || !s.Accepts || (pass == 0 ? !s.Matching : !s.Empty)) continue;
                    var free = s.Capacity - s.Count;
                    if (free <= 0) continue;
                    var put = Math.Min(free, remaining);
                    steps.Add(new SlotStep(i, put));
                    remaining -= put;
                }
            return remaining == 0 ? steps : null;
        }

        // Returns false on a callback refusal, throw, or an unexpected slot delta. Caller treats
        // any such failure after INTENT as uncertain, including a callback that returned false.
        public static bool Apply(IReadOnlyList<SlotStep> plan, Func<int, int> slotCount,
            Func<int, int, bool> mutate, bool adding)
        {
            foreach (var step in plan)
            {
                try
                {
                    var before = slotCount(step.Index);
                    if (!mutate(step.Index, step.Amount)) return false;
                    if (slotCount(step.Index) != before + (adding ? step.Amount : -step.Amount)) return false;
                }
                catch { return false; }
            }
            return true;
        }

        public static string? NewestRevision(IEnumerable<string> names, string group)
        {
            if (string.IsNullOrEmpty(group)) return null;
            var pattern = new Regex("\\A" + Regex.Escape(group) + "_(0|[1-9][0-9]{0,9})\\z", RegexOptions.CultureInvariant);
            var seen = new HashSet<long>();
            string? latest = null;
            foreach (var name in names)
            {
                var match = pattern.Match(name ?? "");
                if (!match.Success) continue;
                if (!long.TryParse(match.Groups[1].Value, out var revision) || !seen.Add(revision)) return null;
                if (latest != null) return null;
                latest = name;
            }
            return latest;
        }

        // Native autosave reuses the first available suffix, so suffix sorting is not time order.
        // Only a single newly appeared raw revision can be attributed to our save call.
        public static string? SelectNewRevision(IEnumerable<string> before, IEnumerable<string> after, string group)
        {
            if (string.IsNullOrEmpty(group)) return null;
            var old = RevisionSet(before, group);
            var current = RevisionSet(after, group);
            if (old == null || current == null) return null;
            string? candidate = null;
            foreach (var name in current)
                if (!old.Contains(name))
                {
                    if (candidate != null) return null;
                    candidate = name;
                }
            return candidate;
        }

        public static bool IsRevision(string name, string group) => !string.IsNullOrEmpty(group) &&
            Regex.IsMatch(name ?? "", "\\A" + Regex.Escape(group) + "_(0|[1-9][0-9]{0,9})\\z", RegexOptions.CultureInvariant);

        private static HashSet<string>? RevisionSet(IEnumerable<string> names, string group)
        {
            var result = new HashSet<string>(StringComparer.Ordinal);
            foreach (var name in names)
                if (IsRevision(name, group) && !result.Add(name)) return null;
            return result;
        }

        // This verifies the provider's exact local file, not a summary/cache or a guessed LocalLow path.
        // Opening r/w is required for OS flush; no save bytes are written. Hold an exclusive-writer
        // handle through both hashes and Flush(true), and reject reparse ancestors before opening.
        private static string VerifyLocalDisk(SaveReadback disk)
        {
            var path = Path.GetFullPath(disk.DiskPath);
            if (!Path.IsPathRooted(disk.DiskPath) ||
                !string.Equals(Path.GetFileName(path), disk.File + ".sav", StringComparison.Ordinal))
                throw new InvalidDataException("native disk filename mismatch");
            for (var component = path; !string.IsNullOrEmpty(component); component = Path.GetDirectoryName(component))
                if ((File.GetAttributes(component) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("reparse point in native disk path");
            using (var sha = SHA256.Create())
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.Read))
            {
                var expected = BitConverter.ToString(sha.ComputeHash(disk.Bytes)).Replace("-", "").ToLowerInvariant();
                var actual = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (stream.Length != disk.Bytes.Length || actual != expected)
                    throw new InvalidDataException("native disk hash mismatch");
                stream.Flush(true);
                stream.Position = 0;
                if (BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant() != expected)
                    throw new InvalidDataException("native disk changed during OS flush");
                return expected;
            }
        }

        public static SaveProof VerifyPersistence(Func<bool> save, Func<SaveReadback> readback,
            string identity, int expectedCount)
        {
            try
            {
                if (!save()) return new SaveProof(false, "", "", "native save returned false or skipped");
                var disk = readback();
                if (disk.Bytes == null || disk.Bytes.Length == 0 || !IsRevision(disk.File, identity) || expectedCount < 0 ||
                    !string.Equals(disk.Identity, identity, StringComparison.Ordinal) || disk.Count != expectedCount)
                    return new SaveProof(false, "", "", "native readback identity or count mismatch");
                if (string.IsNullOrEmpty(disk.DiskPath))
                    return new SaveProof(false, "", "", "no exact local native disk path");
                return new SaveProof(true, disk.File, VerifyLocalDisk(disk), "");
            }
            catch (Exception ex) { return new SaveProof(false, "", "", "native readback failed: " + ex.GetType().Name + ": " + ex.Message); }
        }
    }
}
