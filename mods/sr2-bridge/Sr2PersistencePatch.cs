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
                HarmonyMethod First(string name) => new HarmonyMethod(typeof(Sr2PersistencePatch), name) { priority = Priority.First };
                var saveImpl = AccessTools.Method(typeof(AutoSaveDirector), "SaveGameImpl");
                if (saveImpl != null)
                {
                    harmony.Patch(saveImpl, prefix: First(nameof(BeforeSave)));
                    patched++;
                }
                var loadTrans = AccessTools.Method(typeof(SaveReferenceTranslation), "CreateLoadReferenceTranslation");
                if (loadTrans != null)
                {
                    harmony.Patch(loadTrans, prefix: First(nameof(BeforeLoadTranslation)));
                    patched++;
                }
                var pullGame = AccessTools.Method(typeof(GameModelPullHelpers), "PullGame");
                if (pullGame != null)
                {
                    harmony.Patch(pullGame, prefix: First(nameof(BeforePullGame)));
                    patched++;
                }
                var pushGame = AccessTools.Method(typeof(GameModelPushHelpers), "PushGame");
                if (pushGame != null)
                {
                    harmony.Patch(pushGame, prefix: First(nameof(BeforePushGame)));
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

        private static void BeforeLoadTranslation(SaveReferenceTranslation __instance)
        {
            try
            {
                var active = Sr2SpeciesRegistrar.Active;
                if (active == null) return;
                if (__instance != null) active.EnsurePersistence(__instance);
                else active.EnsurePersistence(Il2Cpp.GameContext.Instance?.AutoSaveDirector);
            }
            catch (Exception) { }
        }

        private static void BeforePullGame(ISaveReferenceTranslation saveReferenceTranslation)
        {
            try
            {
                var active = Sr2SpeciesRegistrar.Active;
                if (active == null) return;
                var concrete = saveReferenceTranslation?.TryCast<SaveReferenceTranslation>();
                if (concrete != null) active.EnsurePersistence(concrete);
                else active.EnsurePersistence(Il2Cpp.GameContext.Instance?.AutoSaveDirector);
            }
            catch (Exception) { }
        }

        private static void BeforePushGame(ISaveReferenceTranslation saveReferenceTranslation)
        {
            try
            {
                var active = Sr2SpeciesRegistrar.Active;
                if (active == null) return;
                var concrete = saveReferenceTranslation?.TryCast<SaveReferenceTranslation>();
                if (concrete != null) active.EnsurePersistence(concrete);
                else active.EnsurePersistence(Il2Cpp.GameContext.Instance?.AutoSaveDirector);
            }
            catch (Exception) { }
        }
    }
}
