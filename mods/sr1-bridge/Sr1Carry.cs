using System;
using System.Collections.Generic;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>SR1's side of "what you carry through the portal": read the vac, add what the other game offers, remove what it accepted.</summary>
    internal static class Sr1Carry
    {
        private static Dictionary<string, Identifiable.Id>? _byCanonical;

        private static Dictionary<string, Identifiable.Id> ByCanonical()
        {
            if (_byCanonical != null) return _byCanonical;
            var map = new Dictionary<string, Identifiable.Id>(StringComparer.Ordinal);
            foreach (Identifiable.Id id in Enum.GetValues(typeof(Identifiable.Id)))
            {
                if (id == Identifiable.Id.NONE) continue;
                var key = CarryNames.Canonical(id.ToString());
                if (key.Length == 0) continue;
                // Several enum names can collapse to one key (e.g. a slime and its food form): the plainest name wins.
                if (!map.TryGetValue(key, out var existing) || id.ToString().Length < existing.ToString().Length) map[key] = id;
            }
            return _byCanonical = map;
        }

        private static Ammo? Vac()
        {
            var state = SceneContext.Instance?.PlayerState;
            return state?.GetAmmo(PlayerState.AmmoMode.DEFAULT);
        }

        internal static Dictionary<string, int> Snapshot(string mode)
        {
            var result = new Dictionary<string, int>(StringComparer.Ordinal);
            if (mode == "NONE" || mode.Length == 0) return result;
            try
            {
                var ammo = Vac();
                if (ammo == null) return result;
                for (var i = 0; i < ammo.GetUsableSlotCount() && result.Count < 60; i++)
                {
                    var id = ammo.GetSlotName(i);
                    var count = ammo.GetSlotCount(i);
                    if (id == Identifiable.Id.NONE || count <= 0) continue;
                    var name = id.ToString();
                    if (!CarryNames.Allowed(mode, name)) continue;
                    result[name] = Math.Min(9999, (result.TryGetValue(name, out var have) ? have : 0) + count);
                }
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] carry snapshot failed: " + ex.GetType().Name); }
            return result;
        }

        /// <summary>Put what fits into the vac (existing stacks first, then empty slots). Returns what was really added, by the sender's names.</summary>
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
                    if (!table.TryGetValue(CarryNames.Canonical(item.Key), out var id)) continue;
                    var remaining = item.Value;
                    for (var pass = 0; pass < 2 && remaining > 0; pass++)
                        for (var i = 0; i < ammo.GetUsableSlotCount() && remaining > 0; i++)
                        {
                            var name = ammo.GetSlotName(i);
                            var match = name == id;
                            if (pass == 0 ? !match : name != Identifiable.Id.NONE) continue;
                            if (!ammo.CouldAddToSlot(id, i, false)) continue;
                            var room = ammo.GetSlotMaxCount(id, i) - ammo.GetSlotCount(i);
                            var put = Math.Min(room, remaining);
                            if (put <= 0 || !ammo.MaybeAddToSpecificSlot(id, null, i, put, false)) continue;
                            remaining -= put;
                        }
                    var added = item.Value - remaining;
                    if (added > 0) accepted[item.Key] = added;
                }
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] carry receive failed: " + ex.GetType().Name + ": " + ex.Message); }
            return accepted;
        }

        /// <summary>Take items out of the vac (names are the sender's own, so exactly this game's enum names). Returns what was really removed.</summary>
        internal static Dictionary<string, int> Remove(Dictionary<string, int> items)
        {
            var removed = new Dictionary<string, int>(StringComparer.Ordinal);
            try
            {
                var ammo = Vac();
                if (ammo == null) return removed;
                foreach (var item in items)
                {
                    if (!Enum.TryParse<Identifiable.Id>(item.Key, out var id) || id == Identifiable.Id.NONE) continue;
                    var remaining = item.Value;
                    for (var i = 0; i < ammo.GetUsableSlotCount() && remaining > 0; i++)
                    {
                        if (ammo.GetSlotName(i) != id) continue;
                        var take = Math.Min(ammo.GetSlotCount(i), remaining);
                        if (take <= 0) continue;
                        ammo.Decrement(i, take);
                        remaining -= take;
                    }
                    if (remaining < item.Value) removed[item.Key] = item.Value - remaining;
                }
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] carry remove failed: " + ex.GetType().Name + ": " + ex.Message); }
            return removed;
        }
    }
}
