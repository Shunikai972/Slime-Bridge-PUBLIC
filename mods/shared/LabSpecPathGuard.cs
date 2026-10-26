using System;
using System.IO;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Fail-closed containment + reparse guard for Bridge-owned import paths.
    ///
    /// The Python tooling validates spec/output paths before launching, but the runtime consumer
    /// (the mod itself) must enforce the same boundary: the import spec must be a file under
    /// &lt;repo&gt;/Lab/GeneratedContent/species-import and the output directory a directory under
    /// &lt;repo&gt;/Lab/RuntimeArtifacts, with no reparse point on any component (a junction would
    /// redirect reads/writes outside the intended tree).
    /// </summary>
    public static class LabSpecPathGuard
    {
        public const string SpecRelativeRoot = "Lab/GeneratedContent/species-import";
        public const string OutRelativeRoot = "Lab/RuntimeArtifacts";
        /// <summary>Where the launcher extracts the player-built model caches (read-only input for the plugins).</summary>
        public const string AssetCacheRelativeRoot = "Lab/AssetCache";

        /// <summary>Locate the Bridge repository root by walking up from several process anchors
        /// (the oracle-provided LAB executable path, the current process image, the app base
        /// directory, the working directory) to the first ancestor directory whose child
        /// Lab/GeneratedContent/species-import exists. Fail closed (null) when none matches.</summary>
        public static string? FindRepoRoot()
        {
            foreach (var anchor in Anchors())
            {
                try
                {
                    var current = Directory.Exists(anchor) ? new DirectoryInfo(anchor) : new DirectoryInfo(Path.GetDirectoryName(anchor) ?? anchor);
                    for (var depth = 0; depth < 12 && current != null; depth++)
                    {
                        var candidate = Path.Combine(current.FullName, "Lab", "GeneratedContent", "species-import");
                        if (Directory.Exists(candidate)) return current.FullName;
                        current = current.Parent;
                    }
                }
                catch (Exception) { }
            }
            return null;
        }

        private static System.Collections.Generic.IEnumerable<string> Anchors()
        {
            var labExe = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
            if (!string.IsNullOrEmpty(labExe)) yield return labExe;
            var playerHome = PlayerContent.Home;          // normal installs: the launcher's <game>/SlimeBridge/content.json
            if (!string.IsNullOrEmpty(playerHome)) yield return playerHome!;
            string? mainModule = null;
            try { mainModule = System.Diagnostics.Process.GetCurrentProcess().MainModule?.FileName; }
            catch (Exception) { }
            if (!string.IsNullOrEmpty(mainModule)) yield return mainModule;
            var baseDir = AppDomain.CurrentDomain.BaseDirectory;
            if (!string.IsNullOrEmpty(baseDir)) yield return baseDir;
            var cwd = Environment.CurrentDirectory;
            if (!string.IsNullOrEmpty(cwd)) yield return cwd;
        }

        /// <summary>Lexical containment: path strictly under root (the root itself is refused).</summary>
        public static bool IsUnderRoot(string root, string path, out string reason)
        {
            reason = "";
            string rootFull, pathFull;
            try
            {
                rootFull = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                pathFull = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            }
            catch (Exception ex) { reason = "path normalization failed: " + ex.GetType().Name; return false; }
            if (string.Equals(rootFull, pathFull, StringComparison.OrdinalIgnoreCase))
            {
                reason = "path equals the root";
                return false;
            }
            if (!pathFull.StartsWith(rootFull + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            {
                reason = "path outside the Bridge-owned root";
                return false;
            }
            return true;
        }

        /// <summary>True when any existing component from the path up to its drive root is a reparse
        /// point. Missing components are not reparse points; any other attributes error counts as a
        /// reparse/unknown and therefore refuses (fail closed).</summary>
        public static bool HasReparsePoint(string path, out string reason)
        {
            reason = "";
            try
            {
                var current = Path.GetFullPath(path);
                for (var depth = 0; depth < 64; depth++)
                {
                    try
                    {
                        if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                        {
                            reason = "reparse point: " + current;
                            return true;
                        }
                    }
                    catch (FileNotFoundException) { }
                    catch (DirectoryNotFoundException) { }
                    var parent = Path.GetDirectoryName(current);
                    if (string.IsNullOrEmpty(parent) || parent == current) break;
                    current = parent;
                }
                return false;
            }
            catch (Exception ex)
            {
                reason = "attributes unavailable (" + ex.GetType().Name + ")";
                return true;
            }
        }

        public static bool IsAllowedFile(string root, string path, out string reason)
        {
            if (!IsUnderRoot(root, path, out reason)) return false;
            // Reparse check BEFORE any existence probe: a junction at the leaf must be rejected
            // without the probe following it.
            if (HasReparsePoint(path, out reason)) return false;
            try
            {
                if (!File.Exists(path)) { reason = "file missing"; return false; }
            }
            catch (Exception ex) { reason = "file probe failed: " + ex.GetType().Name; return false; }
            return true;
        }

        public static bool IsAllowedDirectory(string root, string path, out string reason)
        {
            if (!IsUnderRoot(root, path, out reason)) return false;
            // Reparse check BEFORE any existence probe (see IsAllowedFile).
            if (HasReparsePoint(path, out reason)) return false;
            try
            {
                if (!Directory.Exists(path)) { reason = "directory missing"; return false; }
            }
            catch (Exception ex) { reason = "directory probe failed: " + ex.GetType().Name; return false; }
            return true;
        }
    }
}
