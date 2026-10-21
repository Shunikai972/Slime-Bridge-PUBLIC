#if SLIMEBRIDGE_SR2
using System;
using System.Text.RegularExpressions;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Slime Rancher 2 1.x: world readiness for travel, inventory and imported species, and an explicit refusal of native
    /// save creation. The 0.6 creator (CounterpartCreator.cs, kept for its harness) hooked LoadNewGame internals that 1.x
    /// removed; until a 1.x creation path is mapped and proven, the player links an existing SR2 save instead.
    /// Every game the player is in is a loaded native game in 1.x (there is no transitional "new game" flag to wait for):
    /// a world is readable when a current game exists, the gameplay scene is idle and the player is alive.
    /// </summary>
    internal sealed class CounterpartCreator
    {
        private static readonly Regex CreationId = new Regex(@"\A[0-9a-f]{32}\z", RegexOptions.CultureInvariant);
        private static readonly Regex NativeName = new Regex(@"\A[A-Za-z0-9_-]{1,128}\z", RegexOptions.CultureInvariant);
        private const string Unsupported = "native SR2 save creation is not available on Slime Rancher 2 1.x: link an existing SR2 save";
        private static object? _witness;

        private CounterpartCreator() { }

        public static string GameNameFor(string creationId) => "sb2_" + creationId;

        public static CounterpartCreator? Begin(BridgeCommand command, OpJournal journal, int pid, out CommandResult? immediate,
            Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool>? storageBinding = null)
        {
            immediate = NewResult(command);
            immediate.CanCreate = false;
            immediate.Reason = command.Kind != "create_save" || !CreationId.IsMatch(command.CreationId ?? "")
                ? "invalid creation command or id" : Unsupported;
            if (CreationId.IsMatch(command.CreationId ?? "")) immediate.SaveId = GameNameFor(command.CreationId!);
            return null;
        }

        public CommandResult? Step() => null;

        public static CommandResult Reconcile(BridgeCommand command, Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool>? storageBinding = null)
        {
            var result = NewResult(command);
            result.CanCreate = false;
            result.Reason = CreationId.IsMatch(command.CreationId ?? "") ? Unsupported : "invalid creation id";
            return result;
        }

        private static CommandResult NewResult(BridgeCommand command) => new CommandResult
        { Realm = "SR2", OpId = command.OpId, Kind = command.Kind, CreationId = command.CreationId ?? "", Verified = false };

        internal sealed class WorldReading
        {
            internal readonly Il2Cpp.GameContext Game;
            internal readonly Il2Cpp.SceneContext Scene;
            internal readonly Il2CppMonomiPark.SlimeRancher.AutoSaveDirector Director;
            internal readonly string Name;
            internal WorldReading(Il2Cpp.GameContext game, Il2Cpp.SceneContext scene, Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, string name)
            { Game = game; Scene = scene; Director = director; Name = name; }
        }

        private sealed class Witness
        {
            internal readonly IntPtr Director, Scene;
            internal readonly string Name;
            internal readonly int Slot;
            internal Witness(IntPtr director, IntPtr scene, string name, int slot) { Director = director; Scene = scene; Name = name; Slot = slot; }
        }

        internal static void ClearWorldReadiness() => _witness = null;

        // Opaque identity of the current world; replaced whenever the game, slot, director or scene changes.
        internal static object? CurrentWorldWitness => _witness;

        internal static bool TryReadWorld(out WorldReading? reading)
        {
            reading = null;
            try
            {
                var game = Il2Cpp.GameContext.Instance; var director = game?.AutoSaveDirector;
                var scene = Il2Cpp.SceneContext.Instance; var loader = Il2Cpp.SystemContext.Instance?.SceneLoader;
                if (game == null || director == null || director.Pointer == IntPtr.Zero) { _witness = null; return false; }
                var current = director.CurrentGame();
                var name = current?.GameName;
                if (current == null || name == null || !NativeName.IsMatch(name)) { _witness = null; return false; }
                if (loader == null || loader.IsSceneLoadInProgress || !loader.IsCurrentSceneGroupGameplay() ||
                    loader._currentSceneGroup?.name?.StartsWith("MainMenu", StringComparison.OrdinalIgnoreCase) == true) return false;
                if (scene == null || scene.Pointer == IntPtr.Zero || scene.PlayerState == null || scene.Player == null ||
                    !scene.Player.activeInHierarchy || scene.PlayerState.GetCurrHealth() <= 0) return false;
                var slot = director.CurrentSlot();
                if (!(_witness is Witness known) || known.Director != director.Pointer || known.Scene != scene.Pointer ||
                    known.Name != name || known.Slot != slot)
                    _witness = new Witness(director.Pointer, scene.Pointer, name, slot);
                reading = new WorldReading(game, scene, director, name);
                return true;
            }
            catch (Exception) { _witness = null; return false; }
        }
    }
}
#endif
