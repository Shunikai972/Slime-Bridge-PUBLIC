using Il2CppMonomiPark.SlimeRancher;
using Il2CppMonomiPark.SlimeRancher.Cutscene;
using Il2CppMonomiPark.SlimeRancher.Economy;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Slime Rancher 2 1.x (Unity 6) moved the save services the plugin reads. One place maps the old 0.6 notions:
    /// <list type="bullet">
    /// <item>the loaded <c>SavedGame</c> (name + slot) is now <c>_currentGameMetadata</c> (<see cref="GameMetadata"/>);</item>
    /// <item>the <c>loadedGame</c> flag (current game loaded from a save) is "a current game exists"; a pending
    /// <c>newGameMetadata</c> cannot be observed any more;</item>
    /// <item><c>StorageProvider</c>, <c>ProfileManager</c>, <c>SaveSlotCount</c> became private fields / configuration;</item>
    /// <item>the cutscene director is a singleton instead of a <c>SceneContext</c> member;</item>
    /// <item>currencies are <see cref="ICurrency"/> definitions; Newbucks is the default currency.</item>
    /// </list>
    /// </summary>
    internal static class Sr2Compat
    {
        // A value type behind an Il2CppSystem.Nullable: every read is a fresh copy, so identity is name + slot, never a pointer.
        internal static GameMetadata? CurrentGame(this AutoSaveDirector director)
        {
            var current = director._currentGameMetadata;
            return current == null || !current.HasValue ? null : current.Value;
        }

        internal static string? CurrentGameName(this AutoSaveDirector director) => director.CurrentGame()?.GameName;

        internal static int CurrentSlot(this AutoSaveDirector director) => director.CurrentGame()?.SlotIndex ?? -1;

        // 0.6 `loadedGame`: the current game came from a native save. In 1.x every current game is a native game.
        internal static bool GameLoaded(this AutoSaveDirector director) => director.CurrentGame() != null;

        internal static bool NewGamePending(this AutoSaveDirector director) => false;

        internal static Il2Cpp.StorageProvider? Storage(this AutoSaveDirector director) => director._storageProvider;

        internal static bool ProfileLoaded(this AutoSaveDirector director) => director._profile != null;

        internal static int SlotCount(this AutoSaveDirector director) =>
            director._configuration == null ? 0 : director._configuration.SaveSlotCount;

        internal static void BeginLoad(this AutoSaveDirector director, string gameName, string saveName) =>
            director.BeginLoad(new GameSaveIdentifier(gameName, saveName), null);

        internal static CutsceneDirector? Cutscene() => CutsceneDirector.Instance;

        /// <summary>
        /// Rebuilds the save serializer's IdentifiableType -> persistence id tables from the live identifiable group, so
        /// imported species and Largos registered after boot can be saved (0.6 called this ResetLookupTables).
        /// Returns the number of types handed to the game.
        /// </summary>
        internal static int RefreshPersistence(this AutoSaveDirector director)
        {
            var translation = director._saveReferenceTranslation == null ? null : director._saveReferenceTranslation.TryCast<SaveReferenceTranslation>();
            var group = director._configuration == null ? null : director._configuration.IdentifiableTypes;
            if (translation == null || group == null) throw new System.InvalidOperationException("save translation or identifiable group unavailable");
            var all = new Il2CppSystem.Collections.Generic.List<Il2Cpp.IdentifiableType>();
            var members = group.GetAllMembers().GetEnumerator();
            var cursor = members.Cast<Il2CppSystem.Collections.IEnumerator>();
            while (cursor.MoveNext()) { var type = members.Current; if (type != null) all.Add(type); }
            translation.OnIdentifiableTypesListChanged(all.Cast<Il2CppSystem.Collections.Generic.IEnumerable<Il2Cpp.IdentifiableType>>());
            return all.Count;
        }

        internal static ICurrency? Newbucks()
        {
            var lookup = Il2Cpp.GameContext.Instance == null ? null : Il2Cpp.GameContext.Instance.LookupDirector;
            var list = lookup == null ? null : lookup.CurrencyList;
            var currency = list == null ? null : list.GetDefaultCurrency();
            return currency == null ? null : currency.Cast<ICurrency>();
        }

        internal static int Newbucks(this Il2Cpp.PlayerState state)
        {
            var currency = Newbucks();
            return currency == null ? 0 : state.GetCurrency(currency);
        }

        /// <summary>Spends Newbucks through the game's own path; false when the currency cannot be resolved.</summary>
        internal static bool SpendNewbucks(this Il2Cpp.PlayerState state, int amount)
        {
            var currency = Newbucks();
            if (currency == null) return false;
            state.SpendCurrency(currency, amount, null);
            return true;
        }

        // Vacpack: 1.x keys every slot operation on an AmmoMetadata (type + emotions + radiant) instead of the bare type.
        internal static bool CouldAdd(this Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type) =>
            ammo.CouldAddToAnySlot(new Il2CppMonomiPark.SlimeRancher.Player.AmmoSlot.AmmoMetadata(type));

        internal static bool AddToSlot(this Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type, int slot, int count) =>
            ammo.MaybeAddToSpecificSlot(new Il2CppMonomiPark.SlimeRancher.Player.AmmoSlot.AmmoMetadata(type), slot, count, false);
    }
}
