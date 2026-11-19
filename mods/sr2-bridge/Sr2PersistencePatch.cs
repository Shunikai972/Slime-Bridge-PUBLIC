using System;
using HarmonyLib;
using Il2CppMonomiPark.SlimeRancher;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Ensures every imported slime, largo, and plort is present in SR2's SaveReferenceTranslation
    /// immediately before any native save (PullGame / PullPlayer / PullActors — portal crossing,
    /// timed autosave, manual Save &amp; Quit, or multiplayer backup) and before any native load
    /// (CreateLoadReferenceTranslation / PushGame). Fast O(N_imported) probe when already synced.
    /// </summary>
    internal static class Sr2PersistencePatch
    {
        private static HarmonyLib.Harmony? _harmony;

        internal static void Install(Action<string>? log = null)
        {
            if (_harmony != null) return;
            var harmony = new HarmonyLib.Harmony("slimebridge.sr2.persistence");
            try
            {
                var patched = 0;
                var saveImpl = AccessTools.Method(typeof(AutoSaveDirector), "SaveGameImpl");
                if (saveImpl != null)
                {
                    harmony.Patch(saveImpl, prefix: new HarmonyMethod(typeof(Sr2PersistencePatch), nameof(BeforeSave)));
                    patched++;
                }
                var loadTrans = AccessTools.Method(typeof(SaveReferenceTranslation), "CreateLoadReferenceTranslation");
                if (loadTrans != null)
                {
                    harmony.Patch(loadTrans, prefix: new HarmonyMethod(typeof(Sr2PersistencePatch), nameof(BeforeLoadTranslation)));
                    patched++;
                }
                if (patched == 0)
                {
                    harmony.UnpatchSelf();
                    log?.Invoke("species registrar: persistence hooks skipped (signatures unavailable)");
                    return;
                }
                _harmony = harmony;
                log?.Invoke("species registrar: save/load persistence hooks installed (" + patched + ")");
            }
            catch (Exception ex)
            {
                try { harmony.UnpatchSelf(); } catch { }
                log?.Invoke("species registrar: persistence hooks failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static void BeforeSave(AutoSaveDirector __instance)
        {
            try { Sr2SpeciesRegistrar.Active?.EnsurePersistence(__instance); }
            catch (Exception) { }
        }

        private static void BeforeLoadTranslation()
        {
            try { Sr2SpeciesRegistrar.Active?.EnsurePersistence(Il2Cpp.GameContext.Instance?.AutoSaveDirector); }
            catch (Exception) { }
        }
    }
}
