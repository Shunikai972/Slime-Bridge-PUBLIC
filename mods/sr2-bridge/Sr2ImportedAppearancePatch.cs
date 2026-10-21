using System;
using HarmonyLib;
namespace SlimeBridge.SR2
{
    /// <summary>Signature-verified reapplication hooks; lifecycle routing still needs LAB proof.</summary>
    internal static class Sr2ImportedAppearancePatch
    {
        private static HarmonyLib.Harmony? _harmony;
        internal static void Install()
        {
            if (_harmony != null) return;
            var harmony = new HarmonyLib.Harmony("slimebridge.sr2.importedvisuals");
            try
            {
                foreach (var name in new [] {"Awake", "Initialize", "ApplyAppearance", "SetExpression"})
                {
                    var method = AccessTools.Method(typeof(Il2Cpp.SlimeAppearanceApplicator), name);
                    if (method == null) throw new MissingMethodException("SlimeAppearanceApplicator." + name);
                    harmony.Patch(method, postfix: new HarmonyMethod(typeof(Sr2ImportedAppearancePatch), nameof(Reapply)));
                }
                var bounds = AccessTools.Method(typeof(Il2Cpp.SlimeAppearanceApplicator.RecalculateBoundsHelper), "RecalculateBounds");
                var update = AccessTools.Method(typeof(Il2Cpp.SlimeAppearanceApplicator.RecalculateBoundsHelper), "Update");
                if (bounds == null || update == null) throw new MissingMethodException("native bounds helper signatures missing");
                harmony.Patch(bounds, prefix: new HarmonyMethod(typeof(Sr2ImportedAppearancePatch), nameof(BoundsPrefix)));
                harmony.Patch(update, prefix: new HarmonyMethod(typeof(Sr2ImportedAppearancePatch), nameof(BoundsUpdatePrefix)));
                _harmony = harmony;
            }
            catch { harmony.UnpatchSelf(); throw; }
        }
        private static void Reapply(Il2Cpp.SlimeAppearanceApplicator __instance)
        {
            try
            {
                if (__instance != null && Sr2ImportedSpawner.IsImportedFast(__instance))
                    Sr2ImportedSpawner.TryApply(__instance.gameObject, out _);
            }
            catch (Exception ex) { UnityEngine.Debug.Log("[SlimeBridge] imported reapply: " + ex.GetType().Name); }
        }
        private static bool BoundsPrefix(Il2Cpp.SlimeAppearanceApplicator __0)
        {
            try { return __0 == null || !Sr2ImportedSpawner.HasAppliedVisualFast(__0); }
            catch (Exception) { return true; }
        }
        private static bool BoundsUpdatePrefix(Il2Cpp.SlimeAppearanceApplicator.RecalculateBoundsHelper __instance)
        {
            // Copied runtime helpers can have a null _parent. Actor identity still gates strictly.
            try { return __instance == null || !Sr2ImportedSpawner.HasAppliedVisualFast(__instance); }
            catch (Exception) { return true; }
        }
    }
}
