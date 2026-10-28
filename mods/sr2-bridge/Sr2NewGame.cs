#if SLIMEBRIDGE_SR2
using System;
using System.Collections.Generic;
using Il2CppMonomiPark.SlimeRancher;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Options;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Slime Rancher 2 1.x: start a brand-new native game the way the game's own "New Game" screen does
    /// (NewGameOptionsUIRoot): the platform's game-based option definitions with their default values, the default game
    /// icon, the first free save slot, then AutoSaveDirector.StartNewGame. Used when the launcher starts SR2 as the
    /// destination of a portal crossing for a ranch that has no SR2 save yet. Never touches an existing save.
    /// </summary>
    internal static class Sr2NewGame
    {
        /// <summary>The first slot no existing save uses, from a complete read of the native save summaries.</summary>
        internal static int FreeSlot(AutoSaveDirector director)
        {
            var storage = director.Storage();
            if (storage == null || !storage.IsInitialized()) throw new InvalidOperationException("native storage not initialized");
            var raw = storage.GetAvailableGames();
            if (raw == null) throw new InvalidOperationException("native save enumeration unavailable");
            var names = new List<string>(); var games = new List<string>(); var slots = new List<int>(); var invalid = new List<bool>();
            foreach (var name in raw)
            {
                if (string.IsNullOrWhiteSpace(name) || !storage.HasGameData(name)) continue;
                var summary = director.LoadSummary(name);
                if (summary == null) continue;
                names.Add(name); games.Add(summary.Name ?? name); slots.Add(summary.SaveSlotIndex); invalid.Add(summary.IsInvalid);
            }
            // invalid or unreadable saves still occupy their slot: never reuse it
            var occupied = new HashSet<int>(slots);
            var count = director.SlotCount();
            for (var i = 0; i < count; i++)
                if (!occupied.Contains(i)) return i;
            throw new InvalidOperationException("every Slime Rancher 2 save slot is used: delete a save to make room");
        }

        /// <summary>Default settings of a new game, built from the same definitions as the New Game screen.</summary>
        internal static GameSettingsModel DefaultSettings()
        {
            var game = Il2Cpp.GameContext.Instance ?? throw new InvalidOperationException("no game context");
            var definitions = game.OptionsDirector?._optionsItemDefinitions ?? throw new InvalidOperationException("option definitions unavailable");
            var gameBased = definitions.GetRuntime()?.GameBasedDefinitions ?? throw new InvalidOperationException("game-based option definitions unavailable");
            var model = new GameSettingsModel(gameBased.Cast<Il2CppSystem.Collections.Generic.IEnumerable<OptionsItemDefinition>>());
            var icon = game.GameSettingsDirector?.GameIcon;
            if (icon != null) model.SetGameIconForNewGame(icon);
            return model;
        }

        /// <summary>Starts the new game; the game loads it like a New Game from its menu. Returns the slot used.</summary>
        internal static int Start(AutoSaveDirector director, Action<string> log)
        {
            string step = "free slot";
            try
            {
                var slot = FreeSlot(director);
                step = "default settings";
                var settings = DefaultSettings();
                step = "StartNewGame";
                log("travel: new game in slot " + slot + " (" + settings.optionsById?.Count + " settings)");
                director.StartNewGame(slot, settings);
                return slot;
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("new game failed at '" + step + "': " + ex, ex);
            }
        }
    }
}
#endif
