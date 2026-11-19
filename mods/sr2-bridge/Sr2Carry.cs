using System;
using System.Collections.Generic;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>SR2's side of "what you carry through the portal" (see Sr1Carry): the vac is read and written through the native Ammo only.</summary>
    internal static class Sr2Carry
    {
        private static Dictionary<string, Il2Cpp.IdentifiableType>? _byCanonical;
        private static int _byCanonicalCount = -1;

        private static Dictionary<string, Il2Cpp.IdentifiableType> ByCanonical()
        {
            var lookup = Il2Cpp.GameContext.Instance?.LookupDirector;
            if (lookup == null || lookup._identifiableTypeByRefId == null) return new Dictionary<string, Il2Cpp.IdentifiableType>(StringComparer.Ordinal);
            // Imported species (Quantum, Rad, Mosaic, the Largos) join the lookup when the world loads: rebuild when it grew.
            var count = lookup._identifiableTypeByRefId.Count;
            if (_byCanonical != null && count == _byCanonicalCount) return _byCanonical;
            _byCanonicalCount = count;
            var map = new Dictionary<string, Il2Cpp.IdentifiableType>(StringComparer.Ordinal);
            foreach (var entry in lookup._identifiableTypeByRefId)
            {
                if (entry.Key == null || entry.Value == null) continue;
                var key = CarryNames.Canonical(entry.Key);
                if (key.Length == 0) continue;
                if (!map.TryGetValue(key, out var existing) || entry.Key.Length < (existing.ReferenceId ?? "").Length) map[key] = entry.Value;
            }
            return _byCanonical = map;
        }

        /// <summary>
        /// An imported species is a member of the groups that directly hold its template, but a vac slot accepts a parent
        /// group whose runtime member cache was built before the import: the slot refuses it. Admit the imported type in
        /// every slot group that already admits its native counterpart (pink slime, pink plort or a pink Largo).
        /// </summary>
        internal static bool AllowInVac(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type)
        {
            var admitted = false;
            try
            {
                var lookup = Il2Cpp.GameContext.Instance?.LookupDirector;
                if (lookup == null) return false;
                var slime = type.TryCast<Il2Cpp.SlimeDefinition>();
                var siblingRef = slime == null ? "IdentifiableType.PinkPlort" : (slime.IsLargo ? "SlimeDefinition.PinkRock" : "SlimeDefinition.Pink");
                if (!lookup.TryFindIdentifiableTypeByReferenceId(siblingRef, out var sibling) || sibling == null) return false;
                foreach (var slot in ammo.Slots)
                {
                    var definition = slot?.Definition;
                    var group = definition?.SlotTypeGroup;
                    if (group == null || !definition!.IsAllowed(sibling) || definition.IsAllowed(type)) continue;
                    var members = group.ImmediateMemberTypes;
                    if (members != null && !members.Contains(type)) members.Add(type);
                    var runtimeObj = group._runtimeObject;
                    var runtime = runtimeObj?._memberTypes;
                    if (runtime != null && !runtime.Contains(type)) runtime.Add(type);
                    var indices = runtimeObj?._memberIndices;
                    if (indices != null && !indices.ContainsKey(type))
                        indices[type] = runtime != null ? runtime.Count - 1 : indices.Count;
                    admitted |= definition.IsAllowed(type);
                }
                MelonLoader.MelonLogger.Msg("carry: " + (type.ReferenceId ?? "?") + (admitted ? " admitted in the vac slots of " : " still refused by the vac slots of ") + siblingRef);
            }
            catch (Exception ex) { MelonLoader.MelonLogger.Msg("carry: vac admission failed: " + ex.GetType().Name); }
            return admitted;
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
                // Ensure imported appearances, vac groups, and save persistence are live BEFORE mutating any slot,
                // so HUD updates (AmmoSlotViewHolder.UpdateAmmoDisplay) and the follow-up save never throw.
                Sr2SpeciesRegistrar.Active?.EnsureWorldBindings();
                var table = ByCanonical();
                foreach (var item in offered)
                {
                    if (item.Value <= 0) continue;
                    if (!table.TryGetValue(CarryNames.Canonical(item.Key), out var type)) continue;
                    if (!ammo.CouldAdd(type) && (!AllowInVac(ammo, type) || !ammo.CouldAdd(type))) continue;
                    var remaining = item.Value;
                    var slots = ammo.Slots;
                    var itemFailed = false;
                    for (var pass = 0; pass < 2 && remaining > 0 && !itemFailed; pass++)
                        for (var i = 0; i < slots.Length && remaining > 0 && !itemFailed; i++)
                        {
                            var slot = slots[i];
                            if (slot == null || !slot.IsUnlocked) continue;
                            var match = slot.Id != null && slot.Id.Pointer == type.Pointer && slot.Count > 0;
                            var empty = slot.Id == null || slot.Count <= 0;
                            if (pass == 0 ? !match : !empty) continue;
                            var beforeCount = match ? slot.Count : 0;
                            int cap;
                            try
                            {
                                var left = match ? ammo.GetSlotSpaceLeft(i) : ammo.GetSlotMaxCount(i);
                                cap = left > 0 ? Math.Min(remaining, left) : remaining;
                            }
                            catch (Exception) { cap = remaining; }
                            // Exact-fit first; if native refuses (e.g. smaller effective cap), step down cleanly.
                            for (var attempt = cap; attempt > 0; attempt = attempt > 1 ? attempt / 2 : 0)
                            {
                                try
                                {
                                    if (ammo.AddToSlot(type, i, attempt))
                                    {
                                        var delta = slot.Id != null && slot.Id.Pointer == type.Pointer ? Math.Max(0, slot.Count - beforeCount) : attempt;
                                        remaining -= Math.Min(remaining, delta > 0 ? delta : attempt);
                                        break;
                                    }
                                }
                                catch (Exception ex)
                                {
                                    MelonLoader.MelonLogger.Msg("carry receive failed for " + item.Key + " (slot " + i + "): " + ex.GetType().Name + ": " + ex.Message);
                                    // Atomicity: if native mutated the slot before throwing (e.g. in HUD UpdateAmmoDisplay),
                                    // roll back the partial mutation so the item is never duplicated or corrupted.
                                    try
                                    {
                                        if (slot.Id != null && slot.Id.Pointer == type.Pointer && slot.Count > beforeCount)
                                        {
                                            if (beforeCount == 0) ammo.Clear(i);
                                            else ammo.Decrement(i, slot.Count - beforeCount);
                                        }
                                    }
                                    catch (Exception rex) { MelonLoader.MelonLogger.Msg("carry receive rollback failed: " + rex.GetType().Name); }
                                    itemFailed = true;
                                    break;
                                }
                            }
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
