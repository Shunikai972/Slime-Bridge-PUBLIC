using System;
using System.Collections.Generic;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>SR2's side of "what you carry through the portal" (see Sr1Carry): the vac is read and written through the native Ammo only.</summary>
    internal static class Sr2Carry
    {
        private static Dictionary<string, Il2Cpp.IdentifiableType>? _byCanonical;

        private static Dictionary<string, Il2Cpp.IdentifiableType> ByCanonical()
        {
            if (_byCanonical != null) return _byCanonical;
            var map = new Dictionary<string, Il2Cpp.IdentifiableType>(StringComparer.Ordinal);
            var lookup = Il2Cpp.GameContext.Instance?.LookupDirector;
            if (lookup == null || lookup._identifiableTypeByRefId == null) return map;
            foreach (var entry in lookup._identifiableTypeByRefId)
            {
                if (entry.Key == null || entry.Value == null) continue;
                var key = CarryNames.Canonical(entry.Key);
                if (key.Length == 0) continue;
                if (!map.TryGetValue(key, out var existing) || entry.Key.Length < (existing.ReferenceId ?? "").Length) map[key] = entry.Value;
            }
            return _byCanonical = map;
        }

        private static Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager? Vac() => Il2Cpp.SceneContext.Instance?.PlayerState?.Ammo;

        internal static Dictionary<string, int> Snapshot(string mode)
        {
            var result = new Dictionary<string, int>(StringComparer.Ordinal);
            if (mode == "NONE" || mode.Length == 0) return result;
            try
            {
                var ammo = Vac();
                if (ammo == null) return result;
                foreach (var slot in ammo.Slots)
                {
                    if (slot == null || slot.Id == null || slot.Count <= 0 || result.Count >= 60) continue;
                    var name = slot.Id.ReferenceId ?? "";
                    if (!CarryNames.Allowed(mode, name)) continue;
                    result[name] = Math.Min(9999, (result.TryGetValue(name, out var have) ? have : 0) + slot.Count);
                }
            }
            catch (Exception ex) { MelonLoader.MelonLogger.Msg("carry snapshot failed: " + ex.GetType().Name); }
            return result;
        }

        internal static Dictionary<string, int> Receive(Dictionary<string, int> offered)
        {
            var accepted = new Dictionary<string, int>(StringComparer.Ordinal);
            try
            {
                var ammo = Vac();
                if (ammo == null) return accepted;
                var table = ByCanonical();
                foreach (var item in offered)
                {
                    if (!table.TryGetValue(CarryNames.Canonical(item.Key), out var type) || !ammo.CouldAdd(type)) continue;
                    var remaining = item.Value;
                    var slots = ammo.Slots;
                    for (var pass = 0; pass < 2 && remaining > 0; pass++)
                        for (var i = 0; i < slots.Length && remaining > 0; i++)
                        {
                            var slot = slots[i];
                            if (slot == null || !slot.IsUnlocked) continue;
                            var match = slot.Id != null && slot.Id.Pointer == type.Pointer && slot.Count > 0;
                            var empty = slot.Id == null || slot.Count <= 0;
                            if (pass == 0 ? !match : !empty) continue;
                            // The native add refuses what does not fit: try the whole remainder, then smaller amounts.
                            for (var attempt = remaining; attempt > 0; attempt /= 2)
                                if (ammo.AddToSlot(type, i, attempt)) { remaining -= attempt; break; }
                        }
                    var added = item.Value - remaining;
                    if (added > 0) accepted[item.Key] = added;
                }
            }
            catch (Exception ex) { MelonLoader.MelonLogger.Msg("carry receive failed: " + ex.GetType().Name + ": " + ex.Message); }
            return accepted;
        }

        internal static Dictionary<string, int> Remove(Dictionary<string, int> items)
        {
            var removed = new Dictionary<string, int>(StringComparer.Ordinal);
            try
            {
                var ammo = Vac();
                var lookup = Il2Cpp.GameContext.Instance?.LookupDirector;
                if (ammo == null || lookup == null) return removed;
                foreach (var item in items)
                {
                    if (!lookup.TryFindIdentifiableTypeByReferenceId(item.Key, out var type) || type == null) continue;
                    var remaining = item.Value;
                    var slots = ammo.Slots;
                    for (var i = 0; i < slots.Length && remaining > 0; i++)
                    {
                        var slot = slots[i];
                        if (slot == null || slot.Id == null || slot.Id.Pointer != type.Pointer || slot.Count <= 0) continue;
                        var take = Math.Min(slot.Count, remaining);
                        ammo.Decrement(i, take);
                        remaining -= take;
                    }
                    if (remaining < item.Value) removed[item.Key] = item.Value - remaining;
                }
            }
            catch (Exception ex) { MelonLoader.MelonLogger.Msg("carry remove failed: " + ex.GetType().Name + ": " + ex.Message); }
            return removed;
        }
    }
}
