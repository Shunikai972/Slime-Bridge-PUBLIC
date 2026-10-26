using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using MonomiPark.SlimeRancher.DataModel;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Makes imported plorts sellable. The Plort Market's <c>ScorePlort</c> asks <c>EconomyDirector.GetCurrValue(id)</c>;
    /// the director only prices the ids of its prefab-authored <c>baseValueMap</c>, so an imported plort was refused (and a
    /// sale would have thrown on <c>marketSaturation[id]</c>). Before the director builds its price table (InitModel) the
    /// imported plorts are appended to <c>baseValueMap</c>, priced like the Pink plort (value and full saturation), and
    /// after the model is attached (SetModel) every imported plort is guaranteed a saturation entry, also for saves that
    /// were written without it. Fail-open: any problem leaves the native economy untouched.
    /// </summary>
    internal static class ImportedEconomyPatch
    {
        private static Harmony? _harmony;
        private static SpeciesRegistrar? _registrar;

        public static void EnsurePatched(SpeciesRegistrar registrar)
        {
            _registrar = registrar;
            if (_harmony != null) return;
            try
            {
                var harmony = new Harmony("slimebridge.sr1.imported-economy");
                harmony.Patch(AccessTools.Method(typeof(EconomyDirector), "InitModel"),
                    prefix: new HarmonyMethod(typeof(ImportedEconomyPatch), nameof(InitModelPrefix)));
                harmony.Patch(AccessTools.Method(typeof(EconomyDirector), "SetModel"),
                    postfix: new HarmonyMethod(typeof(ImportedEconomyPatch), nameof(SetModelPostfix)));
                harmony.Patch(AccessTools.Method(typeof(MarketUI), "Start"),
                    prefix: new HarmonyMethod(typeof(ImportedEconomyPatch), nameof(MarketStartPrefix)));
                _harmony = harmony;
                UnityEngine.Debug.Log("[SlimeBridge] imported plort economy patched (sellable at the Plort Market)");
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogError("[SlimeBridge] imported plort economy patch failed: " + ex.GetType().Name);
            }
        }

        private static EconomyDirector.ValueMap? Template(EconomyDirector director)
        {
            var map = director.baseValueMap;
            if (map == null || map.Length == 0) return null;
            foreach (var entry in map)
                if (entry != null && entry.accept != null && entry.accept.id == Identifiable.Id.PINK_PLORT) return entry;
            return map.FirstOrDefault(e => e != null && e.accept != null);
        }

        private static void InitModelPrefix(EconomyDirector __instance)
        {
            try
            {
                var registrar = _registrar;
                var game = GameContext.Instance;
                if (registrar == null || game == null) return;
                var template = Template(__instance);
                if (template == null) return;
                var existing = new HashSet<Identifiable.Id>(
                    (__instance.baseValueMap ?? new EconomyDirector.ValueMap[0]).Where(e => e != null && e.accept != null).Select(e => e.accept.id));
                var added = new List<EconomyDirector.ValueMap>();
                foreach (var plort in registrar.RegisteredPlorts)
                {
                    var id = (Identifiable.Id)plort.Value;
                    if (existing.Contains(id)) continue;
                    var prefab = game.LookupDirector.GetPrefab(id);
                    var identifiable = prefab != null ? prefab.GetComponent<Identifiable>() : null;
                    if (identifiable == null) continue;
                    // the plort's own SR2 price when the species cache carries it, the Pink plort's otherwise
                    var value = template.value; var saturation = template.fullSaturation;
                    var species = plort.Key.StartsWith("plort.", StringComparison.Ordinal) ? "slime." + plort.Key.Substring(6) : plort.Key;
                    if (ImportedPlortAppearance.TryGet(species, out var look) && look.MarketValue > 0f)
                    {
                        // SR2's price relative to SR2's Pink plort, applied to SR1's Pink plort: the imported plorts keep their rank
                        // and their ratio to Pink inside SR1's own economy (without the Pink value, SR2's number is used as it is)
                        value = look.MarketPinkValue > 0f ? look.MarketValue * template.value / look.MarketPinkValue : look.MarketValue;
                        saturation = look.MarketFullSaturation;
                    }
                    added.Add(new EconomyDirector.ValueMap { accept = identifiable, value = value, fullSaturation = saturation });
                }
                if (added.Count > 0)
                {
                    __instance.baseValueMap = (__instance.baseValueMap ?? new EconomyDirector.ValueMap[0]).Concat(added).ToArray();
                    UnityEngine.Debug.Log("[SlimeBridge] imported plorts priced at the market: " + added.Count);
                }
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] imported plort pricing failed: " + ex.GetType().Name);
            }
        }

        /// <summary>The Plort Market's price board lists the plorts of its prefab; the imported ones get a row too (their sale price
        /// is already in the economy). The board's panels have a fixed capacity: the last one grows when the rows do not fit.</summary>
        private static void MarketStartPrefix(MarketUI __instance)
        {
            try
            {
                var registrar = _registrar;
                if (registrar == null || __instance.plorts == null || __instance.pricesPanels == null || __instance.pricesPanels.Length == 0) return;
                var known = new HashSet<Identifiable.Id>(__instance.plorts.Where(e => e != null).Select(e => e.id));
                var rows = new List<MarketUI.PlortEntry>(__instance.plorts);
                foreach (var nativeId in registrar.RegisteredPlortNativeIds)
                {
                    var id = (Identifiable.Id)nativeId;
                    if (known.Add(id)) rows.Add(new MarketUI.PlortEntry { id = id, toUnlock = new ProgressDirector.ProgressType[0] });
                }
                if (rows.Count == __instance.plorts.Length) return;
                var capacity = __instance.pricesPanels.Sum(panel => panel.entryCount);
                if (rows.Count > capacity) __instance.pricesPanels[__instance.pricesPanels.Length - 1].entryCount += rows.Count - capacity;
                __instance.plorts = rows.ToArray();
                UnityEngine.Debug.Log("[SlimeBridge] market price board: " + (rows.Count - (rows.Count - registrar.RegisteredPlortNativeIds.Count())) + " imported plort rows");
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] market price board patch failed: " + ex.GetType().Name);
            }
        }

        private static void SetModelPostfix(EconomyDirector __instance, WorldModel model)
        {
            try
            {
                var registrar = _registrar;
                var template = Template(__instance);
                if (registrar == null || template == null || model == null || model.marketSaturation == null) return;
                foreach (var plort in registrar.RegisteredPlorts)
                {
                    var id = (Identifiable.Id)plort.Value;
                    var species = plort.Key.StartsWith("plort.", StringComparison.Ordinal) ? "slime." + plort.Key.Substring(6) : plort.Key;
                    var saturation = ImportedPlortAppearance.TryGet(species, out var look) && look.MarketFullSaturation > 0f ? look.MarketFullSaturation : template.fullSaturation;
                    if (!model.marketSaturation.ContainsKey(id)) model.marketSaturation[id] = saturation * 0.5f;
                }
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] imported plort saturation failed: " + ex.GetType().Name);
            }
        }
    }
}
