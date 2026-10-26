// Test double of mods/sr2-bridge/Sr2Compat.cs over the synthetic native fixtures: the same extension surface the ported
// production code calls, answered from the fixture's members (the fixtures keep the 0.6 shapes these harnesses were written
// against, re-homed in the SR2 1.x namespaces).
namespace SlimeBridge.SR2
{
    internal sealed class FixtureGameMetadata
    {
        internal readonly string GameName;
        internal readonly int SlotIndex;
        internal FixtureGameMetadata(string name, int slot) { GameName = name; SlotIndex = slot; }
    }

    internal static partial class Sr2Compat
    {
        internal static FixtureGameMetadata? CurrentGame(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director)
        {
            var saved = director.SavedGame;
            return saved?.GameState == null ? null : new FixtureGameMetadata(saved.GetName(), saved.GameState.SaveSlotIndex);
        }
        internal static string? CurrentGameName(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.CurrentGame()?.GameName;
        internal static int CurrentSlot(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.CurrentGame()?.SlotIndex ?? -1;
        internal static bool GameLoaded(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.loadedGame;
        internal static bool NewGamePending(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.newGameMetadata != null;
        internal static Il2Cpp.StorageProvider? Storage(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.StorageProvider;
        internal static bool ProfileLoaded(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.ProfileManager != null;
        internal static int SlotCount(this Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) => director.SaveSlotCount;
        internal static bool CouldAdd(this Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type) => ammo.CouldAddToSlot(type);
        internal static bool AddToSlot(this Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type, int slot, int count) =>
            ammo.MaybeAddToSpecificSlot(type, null, slot, count, false);
    }
}
