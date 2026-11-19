using System;
using System.Linq;
using System.Runtime.CompilerServices;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// "slimebridge" in Starlight's console (Ctrl+Tab), like Slime Rancher's SRML command: list the imported species,
    /// list the Largos, spawn one in front of the player. Starlight ships with Ranching Together; it is referenced at build
    /// time only and never shipped. Without it this class is never loaded.
    /// </summary>
    internal sealed class Sr2ConsoleCommand : Starlight.StarlightCommand
    {
        private static Func<Sr2SpeciesRegistrar?> _registrar = () => null;

        public override string ID => "slimebridge";
        public override string Usage => "slimebridge <list|largos|spawn> [id] [count]";
        public override string Description => "SlimeBridge: list the imported species and Largos, spawn one in front of you";

        public override bool Execute(string[] args)
        {
            var registrar = _registrar();
            if (registrar == null || !registrar.Done) return SendError("SlimeBridge: no imported species registered yet");
            var verb = args.Length > 0 ? args[0].ToLowerInvariant() : "help";
            var all = registrar.Canonicals.OrderBy(c => c, StringComparer.Ordinal).ToList();
            var allSpawnable = registrar.AllCanonicals.OrderBy(c => c, StringComparer.Ordinal).ToList();
            switch (verb)
            {
                case "list":
                    SendMessage("Species: " + string.Join(", ", all.Where(c => !c.StartsWith("largo.", StringComparison.Ordinal))));
                    return true;
                case "largos":
                    SendMessage("Largos (" + all.Count(c => c.StartsWith("largo.", StringComparison.Ordinal)) + "): " +
                                string.Join(", ", all.Where(c => c.StartsWith("largo.", StringComparison.Ordinal))));
                    return true;
                case "spawn":
                    if (args.Length < 2) return SendError("usage: " + Usage);
                    var id = allSpawnable.FirstOrDefault(c => c.Equals(args[1], StringComparison.OrdinalIgnoreCase));
                    if (id == null) return SendError("unknown id " + args[1] + " (slimebridge list / slimebridge largos)");
                    var count = args.Length > 2 && int.TryParse(args[2], out var n) ? Math.Max(1, Math.Min(10, n)) : 1;
                    for (var i = 0; i < count; i++)
                    {
                        var problem = Sr2DevSpawner.SpawnCanonical(registrar, id, i);
                        if (problem.Length > 0) return SendError("spawn " + id + ": " + problem);
                    }
                    SendMessage("spawned " + count + " x " + id);
                    return true;
                default:
                    SendMessage("usage: " + Usage);
                    return true;
            }
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        internal static bool Register(Func<Sr2SpeciesRegistrar?> registrar)
        {
            _registrar = registrar;
            // false only when "slimebridge" is already registered: either way the command is there, never retried
            Starlight.Managers.StarlightCommandManager.RegisterCommand(new Sr2ConsoleCommand());
            MelonLoader.MelonLogger.Msg("console: \"slimebridge\" command added to Starlight's console (Ctrl+Tab)");
            return true;
        }
    }

    /// <summary>Loads the Starlight-based command only when Starlight is in the game (no Starlight type is touched otherwise).</summary>
    internal static class Sr2Console
    {
        internal static bool TryRegister(Func<Sr2SpeciesRegistrar?> registrar)
        {
            if (!AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "Starlight")) return false;
            try { return Load(registrar); }
            catch (Exception) { return false; }
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static bool Load(Func<Sr2SpeciesRegistrar?> registrar) => Sr2ConsoleCommand.Register(registrar);
    }
}
