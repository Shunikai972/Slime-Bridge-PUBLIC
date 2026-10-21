using System;
using System.Collections.Generic;
using System.Text.Json;

namespace SlimeBridge.SR2
{
    /// <summary>Read-only occupancy evidence. A gap is not authorization to create a game.</summary>
    internal static class SaveSlotProbe
    {
        public static string Capture(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director)
        {
            var storage = director.Storage();
            if (director.GameLoaded() || !director.ProfileLoaded() || storage == null || !storage.IsInitialized())
                throw new InvalidOperationException("native menu save services not ready");
            var raw = storage.GetAvailableGames();
            if (raw == null) throw new InvalidOperationException("native raw save enumeration unavailable");
            var rows = new List<object>();
            var occupied = new SortedSet<int>();
            var errors = new List<string>();
            foreach (var name in raw)
            {
                try
                {
                    if (string.IsNullOrWhiteSpace(name) || !storage.HasGameData(name))
                        throw new InvalidOperationException("listed native save has no data");
                    var summary = director.LoadSummary(name);
                    if (summary == null) throw new InvalidOperationException("null native summary");
                    rows.Add(new { raw_name = name, game_name = summary.Name, save_name = summary.SaveName,
                        slot_index = summary.SaveSlotIndex, invalid = summary.IsInvalid });
                    if (summary.IsInvalid || summary.SaveSlotIndex < 0)
                        errors.Add(name + ": invalid native summary or slot index");
                    else occupied.Add(summary.SaveSlotIndex);
                }
                catch (Exception ex)
                {
                    errors.Add((name ?? "<null>") + ": " + ex.GetType().Name + ": " + ex.Message);
                }
            }
            var groups = director.AvailableGamesByGameName();
            var groupNames = new List<string>();
            if (groups == null) errors.Add("group enumeration unavailable");
            else foreach (var group in groups) groupNames.Add(group.Key);
            return JsonSerializer.Serialize(new { schema_version = 1, read_only = true,
                pid = System.Diagnostics.Process.GetCurrentProcess().Id,
                save_slot_count_observed = director.SlotCount(), raw_save_count = raw.Count,
                summaries = rows, occupied_slot_indices = occupied, group_names = groupNames,
                errors, occupancy_complete = errors.Count == 0, permitted_slot_range_verified = false,
                creation_authorized = false });
        }
    }
}
