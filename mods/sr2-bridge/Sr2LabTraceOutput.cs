using System;
using System.IO;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>New LAB evidence is exclusive; every append rechecks the existing safe target.</summary>
    internal static class Sr2LabTraceOutput
    {
        internal static void Create(string root, string file)
        {
            if (!LabSpecPathGuard.IsUnderRoot(root, file, out var reason) || LabSpecPathGuard.HasReparsePoint(file, out reason))
                throw new IOException("unsafe prospective trace: " + reason);
            var parent = Path.GetDirectoryName(Path.GetFullPath(file));
            if (parent == null || !LabSpecPathGuard.IsAllowedDirectory(root, parent, out reason))
                throw new IOException("unsafe trace directory: " + reason);
            // Never truncate/reuse prior evidence or follow an existing leaf to create a trace.
            using (new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { }
            if (!LabSpecPathGuard.IsAllowedFile(root, file, out reason))
                throw new IOException("new trace readback refused: " + reason);
        }

        internal static bool Append(string root, string file, byte[] row, long maximumBytes)
        {
            if (row == null || maximumBytes < 0) throw new ArgumentException("invalid trace bound");
            if (!LabSpecPathGuard.IsAllowedFile(root, file, out var reason)) throw new IOException("unsafe trace target: " + reason);
            // Open does not recreate a removed trace. The handle denies concurrent writes/deletes.
            using (var stream = new FileStream(file, FileMode.Open, FileAccess.Write, FileShare.Read))
            {
                if (row.LongLength > maximumBytes || stream.Length > maximumBytes - row.LongLength) return false;
                stream.Position = stream.Length;
                stream.Write(row, 0, row.Length);
                stream.Flush();
                return true;
            }
        }
    }
}
