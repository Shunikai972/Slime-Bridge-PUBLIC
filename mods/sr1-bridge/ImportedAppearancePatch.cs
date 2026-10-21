using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Persistent appearance guard for imported-species actors. Imported actors are rebuilt by
    /// the game outside the spawn command (vacpack release, region reload, appearance re-apply);
    /// each rebuild re-creates the placeholder appearance objects AND an enabled
    /// SlimeAppearanceApplicator whose private RecalculateBoundsHelper dereferences state that
    /// only the placeholder pipeline maintains. The patches below re-apply the imported visual
    /// on every rebuild and keep the helper from resurfacing (its Update is the source of the
    /// recurring NullReferenceExceptions on imported actors).
    /// </summary>
    internal static class ImportedAppearancePatch
    {
        private static Harmony? _harmony;
        private static FieldInfo? _parentField;

        public static void EnsurePatched()
        {
            if (_harmony != null) return;
            try
            {
                var harmony = new Harmony("slimebridge.sr1.imported-appearance");
                var kit = typeof(SlimeAppearanceApplicator);
                harmony.Patch(AccessTools.Method(kit, "Awake"),
                    postfix: new HarmonyMethod(typeof(ImportedAppearancePatch), nameof(ApplicatorPostfix)));
                harmony.Patch(AccessTools.Method(kit, "ApplyAppearance"),
                    postfix: new HarmonyMethod(typeof(ImportedAppearancePatch), nameof(ApplicatorPostfix)));
                var helper = AccessTools.Inner(kit, "RecalculateBoundsHelper");
                if (helper != null)
                {
                    _parentField = AccessTools.Field(helper, "parent");
                    var recalculate = AccessTools.Method(helper, "RecalculateBounds");
                    if (recalculate != null)
                    {
                        harmony.Patch(recalculate,
                            prefix: new HarmonyMethod(typeof(ImportedAppearancePatch), nameof(RecalculateBoundsPrefix)));
                    }
                    var update = AccessTools.Method(helper, "Update");
                    if (update != null)
                    {
                        harmony.Patch(update,
                            prefix: new HarmonyMethod(typeof(ImportedAppearancePatch), nameof(HelperUpdatePrefix)));
                    }
                }
                _harmony = harmony;
                UnityEngine.Debug.Log("[SlimeBridge] imported appearance guard patched (applicator awake/apply + bounds helper)");
            }
            catch (Exception ex)
            {
                // Fail open: the spawn command still applies the visual one-shot; the guard is an
                // additional persistence layer, never a hard dependency.
                UnityEngine.Debug.LogError("[SlimeBridge] appearance guard patch failed: " + ex);
            }
        }

        /// <summary>Re-apply the imported visual whenever the game (re)initializes an imported
        /// actor's appearance. No-op for every native species (resolved canonical is empty).</summary>
        private static void ApplicatorPostfix(SlimeAppearanceApplicator __instance)
        {
            try
            {
                ImportedSpawner.GuardApplicator(__instance);
            }
            catch (Exception) { }
        }

        /// <summary>Skip the native bounds-helper creation for imported actors: the helper exists
        /// only to wait for the placeholder animator state and NREs on imported instances that
        /// keep a null animator state. The LODGroup bounds are recalculated directly instead.</summary>
        private static bool RecalculateBoundsPrefix(SlimeAppearanceApplicator parent)
        {
            try
            {
                if (parent != null && !string.IsNullOrEmpty(ImportedSpawner.ResolveImportedCanonical(parent)))
                {
                    if (parent.LODGroup != null) parent.LODGroup.RecalculateBounds();
                    return false;
                }
            }
            catch (Exception) { }
            return true;
        }

        /// <summary>Last-resort kill switch: a helper that still exists on an imported actor is
        /// destroyed before its Update can throw (the throw is the recurring per-frame NRE).</summary>
        private static bool HelperUpdatePrefix(object __instance)
        {
            try
            {
                var parent = _parentField != null ? _parentField.GetValue(__instance) as SlimeAppearanceApplicator : null;
                if (parent != null && !string.IsNullOrEmpty(ImportedSpawner.ResolveImportedCanonical(parent)))
                {
                    if (parent.LODGroup != null) parent.LODGroup.RecalculateBounds();
                    if (__instance is UnityEngine.Object component) UnityEngine.Object.Destroy(component);
                    return false;
                }
            }
            catch (Exception) { }
            return true;
        }
    }
}
