using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using HarmonyLib;
using MonomiPark.SlimeRancher.Persist;
using SlimeBridge.Shared;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Bridge-owned native SR1 counterpart creation.
    ///
    /// The internal native game name is "sb1_&lt;creation_id&gt;" (forced by a one-shot Harmony prefix on the
    /// private AutoSaveDirector.GetGameSaveFileName), so the save itself carries Bridge provenance and a
    /// repeated or post-crash creation is detected by name instead of producing a duplicate.
    /// Sequence (main thread, state-based waits): refuse if any "sb1_&lt;id&gt;" save already exists ->
    /// journal INTENT -> LoadNewGame -> wait until loading finished, the expected name is current and the
    /// player exists -> SaveAllNow -> enumerate native saves -> full GameV12 read of the exact file ->
    /// report name/file/sha256. Anything else is reported uncertain; it is never retried here.
    /// </summary>
    internal sealed class CounterpartCreator
    {
        private const float ReadyTimeoutSeconds = 240f;
        private static readonly Regex CreationIdRe = new Regex("\\A[0-9a-f]{32}\\z", RegexOptions.CultureInvariant);
        private static Harmony? _harmony;
        private static string? _armedDisplayName;
        private static string? _armedGameName;
        private static AutoSaveDirector? _armedDirector;

        private readonly CommandResult _result;
        private readonly string _gameName;
        private AutoSaveDirector? _introDirector;
        private object? _introSavedGame;
        internal AutoSaveDirector? IntroDirector => _introDirector;
        internal object? IntroSavedGame => _introSavedGame;
        internal string IntroGameName => _gameName;
        internal string IntroCreationId => _result.CreationId;
        internal string IntroOpId => _result.OpId;
        internal bool IntroFailed => _errored;
        private float _startedAt;
        private bool _errored;
        private string _phase = "";

        public static string GameNameFor(string creationId) => "sb1_" + creationId;

        private CounterpartCreator(CommandResult result, string gameName)
        {
            _result = result;
            _gameName = gameName;
            // Only Begin writes the durable dispatch intent; the agent journals final results.
        }

        /// <summary>Start a creation, or return a final result immediately (refusal / already exists).</summary>
        public static CounterpartCreator? Begin(BridgeCommand command, OpJournal journal, int pid, out CommandResult? immediate)
        {
            immediate = null;
            var result = new CommandResult { Realm = "SR1", OpId = command.OpId, Kind = command.Kind, CreationId = command.CreationId ?? "" };
            if (command.CreationId == null || !CreationIdRe.IsMatch(command.CreationId))
            {
                immediate = Refuse(result, "invalid creation id");
                return null;
            }
            var gameName = GameNameFor(command.CreationId);
            result.SaveId = gameName;
            var director = SRSingleton<GameContext>.Instance == null ? null : SRSingleton<GameContext>.Instance.AutoSaveDirector;
            if (director == null || director.StorageProvider == null || !director.StorageProvider.IsInitialized() ||
                !Levels.isMainMenu() || director.IsLoadingGame() || _armedGameName != null)
            {
                immediate = Refuse(result, "not at an idle main menu");
                return null;
            }
            List<string> existing;
            try { existing = MatchingSaves(director, gameName); }
            catch (Exception ex)
            {
                immediate = Refuse(result, "native save listing failed: " + ex.GetType().Name);
                return null;
            }
            if (existing.Count > 0)
            {
                // Idempotence: this creation already produced a native save. Verify it, never create again.
                Verify(director, gameName, result);
                result.Applied = false;
                result.Ok = result.Verified == true;
                result.Uncertain = !result.Ok;
                if (result.Ok) result.Reason = "creation already exists natively; not re-created";
                immediate = result;
                return null;
            }
            // A different delivery op id must not bypass a prior creation intent. Even with no
            // native file yet, the prior coroutine may still be running or may have crashed.
            if (journal.HasAny("create:" + command.CreationId) || journal.HasAny(command.OpId))
            {
                result.Uncertain = true;
                immediate = Refuse(result, "creation was previously dispatched; reconcile, never create again");
                return null;
            }
            if (!EnsurePatched(out var patchError))
            {
                immediate = Refuse(result, "name hook unavailable: " + patchError);
                return null;
            }

            journal.WriteIntent("create:" + command.CreationId);
            journal.WriteIntent(command.OpId);
            var creator = new CounterpartCreator(result, gameName) { _startedAt = UnityEngine.Time.realtimeSinceStartup,
                _introDirector = director, _introSavedGame = director.SavedGame };
            var display = "SlimeBridge " + command.CreationId.Substring(0, 8);
            _armedDisplayName = display;
            _armedGameName = gameName;
            _armedDirector = director;
            try
            {
                creator._phase = "loading";
                director.LoadNewGame(display, Identifiable.Id.PINK_SLIME, PlayerState.GameMode.CLASSIC, () => creator._errored = true);
            }
            catch (Exception ex)
            {
                // Native dispatch may already have scheduled work; preserve the armed creation
                // marker if the call throws after scheduling, and never permit a retry.
                result.Uncertain = true;
                result.Reason = "LoadNewGame threw " + ex.GetType().Name + ": " + ex.Message;
                immediate = result;
                journal.WriteDone(command.OpId, result.ToJson(pid));
                return null;
            }
            UnityEngine.Debug.Log("[SlimeBridge] counterpart creation dispatched: " + gameName);
            return creator;
        }

        /// <summary>Main thread. Returns the final result once the creation is verified or uncertain.</summary>
        public CommandResult? Step()
        {
            var now = UnityEngine.Time.realtimeSinceStartup;
            var director = SRSingleton<GameContext>.Instance == null ? null : SRSingleton<GameContext>.Instance.AutoSaveDirector;
            if (_errored) return Uncertain("native new-game load reported an error");
            if (now - _startedAt > ReadyTimeoutSeconds) return Uncertain("world not ready after " + ReadyTimeoutSeconds + "s");
            if (director == null) return null;
            var scene = SRSingleton<SceneContext>.Instance;
            var ready = !director.IsLoadingGame() && director.SavedGame != null &&
                        string.Equals(director.SavedGame.GetName(), _gameName, StringComparison.Ordinal) &&
                        scene != null && scene.PlayerState != null && scene.Player != null && scene.Player.activeInHierarchy;
            if (!ready) return null;
            // Native NoteGameFullyLoaded is the readiness gate; no arbitrary readiness sleep.

            _phase = "saving";
            bool saved;
            try { saved = director.SaveAllNow(); }
            catch (Exception ex) { return Uncertain("SaveAllNow threw " + ex.GetType().Name); }
            if (!saved) return Uncertain("SaveAllNow returned false");
            Verify(director, _gameName, _result);
            _result.Applied = true;
            _result.Ok = _result.Verified == true;
            _result.Persisted = _result.Ok; // logical native readback, NOT a power-loss durability claim
            if (!_result.Ok)
            {
                _result.Uncertain = true;
                if (_result.Reason.Length == 0) _result.Reason = "saved but native readback failed";
            }
            UnityEngine.Debug.Log("[SlimeBridge] counterpart creation " + _gameName + ": verified=" + _result.Verified +
                                  " file=" + _result.SaveFile + " sha256=" + _result.Sha256);
            return _result;
        }

        private CommandResult Uncertain(string reason)
        {
            // Keep the one-shot name hook armed if native loading is still pending: a late
            // coroutine must retain its creation marker instead of creating an unowned name.
            _result.Ok = false;
            _result.Uncertain = true;
            _result.Reason = reason + " (phase " + _phase + ")";
            UnityEngine.Debug.Log("[SlimeBridge] counterpart creation uncertain: " + _result.Reason);
            return _result;
        }

        /// <summary>Read-only: report the native save for a creation id (used for CREATION_UNCERTAIN).</summary>
        public static CommandResult Reconcile(BridgeCommand command)
        {
            var result = new CommandResult { Realm = "SR1", OpId = command.OpId, Kind = command.Kind, CreationId = command.CreationId ?? "" };
            if (command.CreationId == null || !CreationIdRe.IsMatch(command.CreationId)) return Refuse(result, "invalid creation id");
            var gameName = GameNameFor(command.CreationId);
            result.SaveId = gameName;
            var director = SRSingleton<GameContext>.Instance == null ? null : SRSingleton<GameContext>.Instance.AutoSaveDirector;
            if (director == null || director.StorageProvider == null) return Refuse(result, "native storage not ready");
            result.CanCreate = Levels.isMainMenu() && !director.IsLoadingGame() &&
                director.StorageProvider.IsInitialized() && _armedGameName == null;
            Verify(director, gameName, result);
            result.Ok = result.Verified == true || (result.Before == 0 && result.Reason == "no native save for creation");
            return result;
        }

        private static void Verify(AutoSaveDirector director, string gameName, CommandResult result)
        {
            result.Verified = false;
            List<string> files;
            try { files = MatchingSaves(director, gameName); }
            catch (Exception ex) { result.Reason = "native save listing failed: " + ex.GetType().Name; return; }
            result.Before = files.Count;
            result.After = files.Count;
            if (files.Count == 0) { result.Reason = "no native save for creation"; return; }
            var revision = new Regex("\\A" + Regex.Escape(gameName) + "_(0|[1-9][0-9]{0,5})\\z", RegexOptions.CultureInvariant);
            foreach (var candidate in files)
                if (!revision.IsMatch(candidate))
                {
                    result.Reason = "ambiguous native filename group for creation";
                    return;
                }
            files.Sort((a, b) => SaveNumber(b).CompareTo(SaveNumber(a)));
            var file = files[0];
            result.SaveFile = file;
            try
            {
                foreach (var candidate in files)
                {
                    using (var stream = new MemoryStream())
                    {
                        director.StorageProvider.GetGameData(candidate, stream);
                        var bytes = stream.ToArray();
                        using (var readback = new MemoryStream(bytes))
                        {
                            var game = new GameV12();
                            game.Load(readback); // full native payload, not merely its summary
                            if (!string.Equals(game.gameName, gameName, StringComparison.Ordinal) ||
                                !string.Equals(game.displayName, "SlimeBridge " + gameName.Substring(4, 8), StringComparison.Ordinal) ||
                                game.summary.gameMode != PlayerState.GameMode.CLASSIC || game.summary.iconId != Identifiable.Id.PINK_SLIME)
                            {
                                result.Reason = "ambiguous or mismatched persisted creation metadata";
                                return;
                            }
                        }
                        if (candidate == file)
                            using (var sha = SHA256.Create())
                                result.Sha256 = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
                    }
                }
                result.Verified = true;
            }
            catch (Exception ex)
            {
                result.Reason = "native readback failed: " + ex.GetType().Name + ": " + ex.Message;
            }
        }

        private static List<string> MatchingSaves(AutoSaveDirector director, string gameName)
        {
            var pattern = new Regex("\\A" + Regex.Escape(gameName) + "(_[0-9]{1,6}){0,2}\\z", RegexOptions.CultureInvariant);
            var matches = new List<string>();
            foreach (var name in director.StorageProvider.GetAvailableGames())
                if (name != null && pattern.IsMatch(name)) matches.Add(name);
            return matches;
        }

        private static int SaveNumber(string file)
        {
            var idx = file.LastIndexOf('_');
            return idx >= 0 && int.TryParse(file.Substring(idx + 1), out var n) ? n : -1;
        }

        private static CommandResult Refuse(CommandResult result, string reason)
        {
            result.Ok = false;
            result.Applied = false;
            result.Reason = reason;
            return result;
        }

        private static bool EnsurePatched(out string error)
        {
            error = "";
            if (_harmony != null) return true;
            try
            {
                var target = AccessTools.Method(typeof(AutoSaveDirector), "GetGameSaveFileName", new[] { typeof(string) });
                if (target == null || target.ReturnType != typeof(string)) { error = "GetGameSaveFileName(string) not found"; return false; }
                var harmony = new Harmony("slimebridge.sr1.counterpart");
                harmony.Patch(target, prefix: new HarmonyMethod(typeof(CounterpartCreator), nameof(NamePrefix)));
                _harmony = harmony;
                return true;
            }
            catch (Exception ex) { error = ex.GetType().Name + ": " + ex.Message; return false; }
        }

        // One-shot: only the dispatched creation (matched by its display name) gets the Bridge name.
        private static bool NamePrefix(AutoSaveDirector __instance, string displayName, ref string __result)
        {
            var armed = _armedGameName;
            if (armed == null || __instance != _armedDirector || !string.Equals(displayName, _armedDisplayName, StringComparison.Ordinal)) return true;
            _armedDirector = null;
            _armedDisplayName = null;
            _armedGameName = null;
            __result = armed;
            return false;
        }
    }
}
