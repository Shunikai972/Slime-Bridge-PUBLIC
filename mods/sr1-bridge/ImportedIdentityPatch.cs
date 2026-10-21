using System;
using System.Collections.Generic;
using HarmonyLib;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Native Identifiable.GetName builds its lookup key from Enum.GetName, which returns null for
    /// registry-reserved (non-enum) ids and would throw inside the game's own UI/logging paths.
    /// Imported identities answer from the registrar's display-name map instead.
    /// </summary>
    internal static class ImportedIdentityPatch
    {
        private static readonly Dictionary<int, string> Names = new Dictionary<int, string>();
        private static Harmony? _harmony;

        public static void EnsurePatched()
        {
            if (_harmony != null) return;
            var harmony = new Harmony("slimebridge.sr1.imported-identities");
            harmony.Patch(AccessTools.Method(typeof(Identifiable), nameof(Identifiable.GetName)),
                prefix: new HarmonyMethod(typeof(ImportedIdentityPatch), nameof(GetNamePrefix)));
            harmony.Patch(AccessTools.Method(typeof(PlayerState), nameof(PlayerState.GetPotentialAmmo)),
                postfix: new HarmonyMethod(typeof(ImportedIdentityPatch), nameof(PotentialAmmoPostfix)));
            _harmony = harmony;
        }

        /// <summary>
        /// Imported identities join the player's potential-ammo set: the serialized
        /// PlayerState.potentialAmmo prefab array is asset data and can never contain
        /// runtime-registered prefabs, so the vacpack would otherwise refuse them.
        /// </summary>
        private static void PotentialAmmoPostfix(ref HashSet<Identifiable.Id> __result)
        {
            if (__result == null || Names.Count == 0) return;
            foreach (var nativeId in Names.Keys) __result.Add((Identifiable.Id)nativeId);
        }

        public static void Register(int nativeId, string displayName)
        {
            if (!string.IsNullOrEmpty(displayName)) Names[nativeId] = displayName;
        }

        /// <summary>True when the native id belongs to a Bridge-registered imported species.</summary>
        internal static bool IsImportedId(int nativeId) => Names.ContainsKey(nativeId);

        public static void Unregister(int nativeId) => Names.Remove(nativeId);

        private static bool GetNamePrefix(Identifiable.Id id, ref string __result)
        {
            if (Enum.IsDefined(typeof(Identifiable.Id), id)) return true;
            if (Names.TryGetValue((int)id, out var name) && !string.IsNullOrEmpty(name))
            {
                __result = name;
                return false;
            }
            if ((int)id >= 900000)
            {
                // Registry-reserved identity without a display name yet: never let the original
                // Enum.GetName-based body NRE on it.
                __result = "Bridge " + ((int)id).ToString(System.Globalization.CultureInfo.InvariantCulture);
                return false;
            }
            return true;
        }
    }
}
