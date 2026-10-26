using System;
using System.Collections.Generic;
using System.Linq;
using SRML.Console;
using BridgeConsole = SRML.Console.Console;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// In-game dev console command for imported-species LAB sessions:
    ///   slimebridge list
    ///   slimebridge spawn &lt;canonical&gt;
    /// Spawns through the native actor pipeline at the player. Only functional while a LAB session
    /// has armed the import spec; otherwise it fails closed with an explanation.
    /// </summary>
    internal sealed class DevSpawnCommand : ConsoleCommand
    {
        private readonly Func<SpeciesRegistrar?> _registrar;
        private readonly Func<bool> _worldReady;

        internal DevSpawnCommand(Func<SpeciesRegistrar?> registrar, Func<bool> worldReady)
        {
            _registrar = registrar;
            _worldReady = worldReady;
        }

        public override string ID => "slimebridge";
        public override string Usage => "slimebridge <list|largos|spawn|diag> [canonical|ID] [count]";
        public override string Description => "SlimeBridge imported-species LAB tools (list, spawn <canonical>, diag <canonical>)";

        public override bool Execute(string[] args)
        {
            var registrar = _registrar();
            if (registrar == null)
            {
                BridgeConsole.LogError("[SlimeBridge] no LAB import session is armed; run the authorized species-import oracle first.");
                return false;
            }
            if (args == null || args.Length == 0 || args[0].Equals("help", StringComparison.OrdinalIgnoreCase))
            {
                BridgeConsole.Log("[SlimeBridge] usage: " + Usage);
                return true;
            }
            if (args[0].Equals("list", StringComparison.OrdinalIgnoreCase))
            {
                var ids = registrar.RegisteredCanonicalIds.ToList();
                BridgeConsole.Log("[SlimeBridge] registered imported species (" + ids.Count + "): " + string.Join(", ", ids));
                return true;
            }
            if (args[0].Equals("largos", StringComparison.OrdinalIgnoreCase))
            {
                var largos = Identifiable.LARGO_CLASS.Select(id => id.ToString()).OrderBy(n => n, StringComparer.Ordinal).ToList();
                var text = "[SlimeBridge] largos (" + largos.Count + "), spawn with: slimebridge spawn <ID> [count]\n  " + string.Join(", ", largos);
                BridgeConsole.Log(text);
                UnityEngine.Debug.Log(text);
                return true;
            }
            if (args[0].Equals("spawn", StringComparison.OrdinalIgnoreCase))
            {
                if (args.Length < 2) { BridgeConsole.LogError("[SlimeBridge] usage: " + Usage); return false; }
                if (!_worldReady())
                {
                    BridgeConsole.LogError("[SlimeBridge] the world is not ready yet (loading or no player); try again in a moment.");
                    return false;
                }
                var game = GameContext.Instance;
                var scene = SceneContext.Instance;
                if (game == null || scene == null || scene.Player == null)
                {
                    BridgeConsole.LogError("[SlimeBridge] no active game session.");
                    return false;
                }
                // a native identifiable id (PINK_ROCK_LARGO, GOLD_SLIME, ...) with an optional count
                if (!args[1].Contains(".") && Enum.TryParse<Identifiable.Id>(args[1].ToUpperInvariant(), out var nativeId) && nativeId != Identifiable.Id.NONE)
                {
                    var count = 1;
                    if (args.Length > 2) int.TryParse(args[2], out count);
                    count = Math.Max(1, Math.Min(count, 12));
                    var nativeProblems = new List<string>();
                    var made = 0;
                    for (var i = 0; i < count; i++)
                        if (ImportedSpawner.SpawnNative(game, scene, nativeId, i, nativeProblems) != null) made++;
                    if (made == 0) { BridgeConsole.LogError("[SlimeBridge] spawn failed: " + string.Join("; ", nativeProblems)); return false; }
                    BridgeConsole.LogSuccess("[SlimeBridge] spawned " + made + " x " + nativeId + " in front of the player.");
                    return true;
                }
                if (args[1].StartsWith("plort.", StringComparison.Ordinal))
                {
                    var plortProblems = new List<string>();
                    if (ImportedSpawner.SpawnPlort(game, scene, registrar, args[1], plortProblems) == null)
                    {
                        BridgeConsole.LogError("[SlimeBridge] plort spawn failed: " + string.Join("; ", plortProblems));
                        return false;
                    }
                    BridgeConsole.LogSuccess("[SlimeBridge] spawned " + args[1] + " in front of the player.");
                    return true;
                }
                var details = new List<string>();
                var problems = new List<string>();
                var instance = ImportedSpawner.Spawn(game, scene, registrar, args[1], details, problems);
                if (instance == null)
                {
                    BridgeConsole.LogError("[SlimeBridge] spawn failed: " + string.Join("; ", problems));
                    return false;
                }
                BridgeConsole.LogSuccess("[SlimeBridge] spawned " + args[1] + " at the player (native actor path).");
                return true;
            }
            if (args[0].Equals("diag", StringComparison.OrdinalIgnoreCase))
            {
                if (args.Length < 2) { BridgeConsole.LogError("[SlimeBridge] usage: " + Usage); return false; }
                if (!_worldReady())
                {
                    BridgeConsole.LogError("[SlimeBridge] the world is not ready yet; try again in a moment.");
                    return false;
                }
                var game = GameContext.Instance;
                var scene = SceneContext.Instance;
                if (game == null || scene == null)
                {
                    BridgeConsole.LogError("[SlimeBridge] no active game session.");
                    return false;
                }
                var problems = new List<string>();
                var written = BridgeDiagnostics.Write(registrar.OutDir, args[1], game, scene, registrar, problems);
                if (!written)
                {
                    BridgeConsole.LogError("[SlimeBridge] diagnostics failed: " + string.Join("; ", problems));
                    return false;
                }
                BridgeConsole.LogSuccess("[SlimeBridge] visual diagnostics written to the session output directory for " + args[1] + ".");
                return true;
            }
            BridgeConsole.LogError("[SlimeBridge] unknown subcommand; usage: " + Usage);
            return false;
        }

        private static int _autoCompleteLogs;

        public override List<string> GetAutoComplete(int argIndex, string argText)
        {
            List<string>? result = null;
            try
            {
                if (argIndex == 0) result = new List<string> { "list", "largos", "spawn", "diag" };
                else if (argIndex == 1)
                {
                    var registrar = _registrar();
                    if (registrar != null) result = registrar.RegisteredCanonicalIds.ToList();
                }
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] console auto-complete threw: " + ex.GetType().Name);
            }
            if (_autoCompleteLogs < 12)
            {
                _autoCompleteLogs++;
                UnityEngine.Debug.Log("[SlimeBridge] console auto-complete asked: index=" + argIndex + " text='" + argText + "' -> " + (result == null ? "none" : result.Count + " entries"));
            }
            return result!;
        }
    }
}
