using System;
using System.Collections.Generic;
using System.IO;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Imported species on a normal game install (not a developer LAB copy).
    ///
    /// The launcher writes <c>&lt;game folder&gt;/SlimeBridge/content.json</c> when it prepares a game:
    /// <code>{"schema": "slimebridge-player-content", "schema_version": 1, "realm": "SR1",
    ///   "home": "&lt;SlimeBridge folder&gt;", "spec": "&lt;home&gt;/Lab/GeneratedContent/species-import/.../import-spec-SR1.json",
    ///   "cache_root": "&lt;home&gt;/Lab/AssetCache/sr1-to-sr2" (SR2 only), "out": "&lt;home&gt;/Lab/RuntimeArtifacts/player-SR1"}</code>
    /// so the plugin finds the content the player extracted from their own games even when the game is started from Steam or
    /// Epic. Every path must sit inside the SlimeBridge folder's Lab tree (same containment and reparse-point refusal as the
    /// LAB path guard); anything else disables imported content and is logged.
    /// </summary>
    internal static class PlayerContent
    {
        internal const string FileName = "content.json";
        private static bool _loaded;
        private static Pointer? _pointer;
        private static string _problem = "";

        internal sealed class Pointer
        {
            internal readonly string Realm, Home, Spec, Out;
            internal readonly string? CacheRoot;
            internal Pointer(string realm, string home, string spec, string? cacheRoot, string output)
            { Realm = realm; Home = home; Spec = spec; CacheRoot = cacheRoot; Out = output; }
        }

        /// <summary>Why the pointer was refused (empty when it loaded or when there is none).</summary>
        internal static string Problem { get { Load(); return _problem; } }

        /// <summary>The SlimeBridge folder of a validated pointer, else null (used as an extra repo-root anchor).</summary>
        internal static string? Home { get { Load(); return _pointer?.Home; } }

        internal static bool TryGet(string realm, out Pointer pointer)
        {
            Load();
            pointer = _pointer!;
            return _pointer != null && string.Equals(_pointer.Realm, realm, StringComparison.Ordinal);
        }

        internal static string? GameRoot()
        {
            try
            {
                var exe = System.Diagnostics.Process.GetCurrentProcess().MainModule?.FileName;
                return string.IsNullOrEmpty(exe) ? null : Path.GetDirectoryName(Path.GetFullPath(exe!));
            }
            catch (Exception) { return null; }
        }

        private static void Load()
        {
            if (_loaded) return;
            _loaded = true;
            try
            {
                var root = GameRoot();
                if (root == null) return;
                var file = Path.Combine(root, "SlimeBridge", FileName);
                if (!File.Exists(file)) return;
                if (LabSpecPathGuard.HasReparsePoint(file, out var reason)) { _problem = "pointer refused: " + reason; return; }
                if (!(MiniJson.Parse(File.ReadAllText(file)) is Dictionary<string, object?> doc)) { _problem = "pointer is not a JSON object"; return; }
                string Text(string key) => doc.TryGetValue(key, out var v) && v is string s ? s : "";
                if (Text("schema") != "slimebridge-player-content") { _problem = "pointer schema mismatch"; return; }
                var realm = Text("realm");
                if (realm != "SR1" && realm != "SR2") { _problem = "pointer realm invalid"; return; }
                var home = Text("home");
                if (home.Length == 0 || !Path.IsPathRooted(home) || !Directory.Exists(home)) { _problem = "SlimeBridge folder missing"; return; }
                home = Path.GetFullPath(home).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                if (LabSpecPathGuard.HasReparsePoint(home, out reason)) { _problem = "SlimeBridge folder refused: " + reason; return; }
                var spec = Text("spec");
                if (!Path.IsPathRooted(spec) || !LabSpecPathGuard.IsAllowedFile(Path.Combine(home, LabSpecPathGuard.SpecRelativeRoot), spec, out reason))
                { _problem = "spec refused: " + reason; return; }
                var output = Text("out");
                if (!Path.IsPathRooted(output) || !LabSpecPathGuard.IsAllowedDirectory(Path.Combine(home, LabSpecPathGuard.OutRelativeRoot), output, out reason))
                { _problem = "output folder refused: " + reason; return; }
                string? cacheRoot = Text("cache_root");
                if (cacheRoot.Length == 0) cacheRoot = null;
                else if (!Path.IsPathRooted(cacheRoot) ||
                         !LabSpecPathGuard.IsAllowedDirectory(Path.Combine(home, LabSpecPathGuard.AssetCacheRelativeRoot), cacheRoot, out reason))
                { _problem = "cache root refused: " + reason; return; }
                _pointer = new Pointer(realm, home, Path.GetFullPath(spec), cacheRoot == null ? null : Path.GetFullPath(cacheRoot), Path.GetFullPath(output));
            }
            catch (Exception ex) { _pointer = null; _problem = "pointer unreadable: " + ex.GetType().Name; }
        }

        /// <summary>A fresh per-session receipt folder under the pointer's output folder (receipts are created exclusively).</summary>
        /// Empty (imported content disabled by the caller's checks) when the folder cannot be made or is refused.
        internal static string RunOutput(Pointer pointer, string runId)
        {
            try
            {
                var dir = Path.Combine(pointer.Out, runId);
                Directory.CreateDirectory(dir);
                if (LabSpecPathGuard.IsAllowedDirectory(pointer.Out, dir, out var reason)) return dir;
                _problem = "player receipt folder refused: " + reason;
            }
            catch (Exception ex) { _problem = "player receipt folder unavailable: " + ex.GetType().Name; }
            return "";
        }

        /// <summary>A run id for receipts written by a player session (same alphabet the LAB run ids use).</summary>
        internal static string RunId(string realm) => "player-" + realm.ToLowerInvariant() + "-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss");
    }
}
