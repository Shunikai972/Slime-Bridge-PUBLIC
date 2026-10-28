using System;
using System.Diagnostics;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using MelonLoader;

[assembly: MelonInfo(typeof(SlimeBridge.SR2.BridgeMod), "SlimeBridge SR2 Core", SlimeBridge.SR2.BridgeMod.PluginVersion, "SlimeBridge contributors")]
[assembly: MelonGame("MonomiPark", "SlimeRancher2")]

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Minimal SR2 BridgeHost heartbeat. Uses only MelonLoader + BCL APIs so it does not depend on
    /// generated Il2Cpp interop assemblies. Network I/O runs on a background task.
    /// </summary>
    public sealed class BridgeMod : MelonMod
    {
        public const string PluginVersion = "0.1.7";
        private static readonly int Pid = Environment.ProcessId;
        private static readonly HttpClient Http = new HttpClient(new HttpClientHandler { UseProxy = false })
        {
            Timeout = TimeSpan.FromMilliseconds(1500),
        };

        private CancellationTokenSource? _stop;
        private InventoryAgent? _inventory;
        private NativeStorageBinding? _storageBinding;
        private CounterpartCapabilityProbe? _capabilityProbe;
        private LabFocusDiagnostics? _focusDiagnostics;
        private bool _focusDiagnosticAllowed;
        private int _focusEpoch;
        private string? _pendingLog;
        private StampedStatus? _status;
        private DateTime _nextSnapshot = DateTime.MinValue;
        private static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions
        {
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };

        private sealed class GameStatus
        {
            public bool player_alive { get; init; }
            public string save_id { get; init; } = "";
            // The SR2 Pink reference strings are only candidate mappings for this game build.
            // Null counts are omitted by JsonOptions; an unknown count must never become zero.
            public int? pink_slime_count { get; init; }
            public int? pink_plort_count { get; init; }
        }

        internal const string PinkSlimeRef = "SlimeDefinition.Pink";
        internal const string PinkPlortRef = "IdentifiableType.PinkPlort";

        // Exact executable match avoids unattended Continue on a normal launch with an
        // inherited AUTOLOAD environment variable.
        private static readonly bool AutoloadContinue = OracleAutoloadRequested();
        private static readonly string? RequestedSaveGroup = AutoloadContinue ?
            Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_SAVE_ID") : null;
        // The launcher started this game as the other side of a portal crossing: load the linked save (or Continue) by itself.
        // RESUME: the player pressed "Continue where you were" in the launcher: the same save is loaded, without a crossing.
        private static readonly bool TravelAutoload = (string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_MODE"), "DESTINATION", StringComparison.Ordinal) ||
                                                       string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_MODE"), "RESUME", StringComparison.Ordinal)) &&
            !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL"));
        private static readonly string? TravelSave = TravelAutoload ? Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_SAVE") : null;
        // ...or, for a ranch with no SR2 save yet, start a new native game for it (the launcher links it once it exists)
        private static readonly bool TravelNewGame = TravelAutoload && string.IsNullOrEmpty(TravelSave) &&
            !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_NEW"));
        private static readonly bool LabCreationAllowed =
            string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_CREATE"), "1", StringComparison.Ordinal) && OracleExecutableMatches();

        private static bool OracleAutoloadRequested() =>
            string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_AUTOLOAD"), "continue", StringComparison.Ordinal) && OracleExecutableMatches();

        private static bool OracleExecutableMatches()
        {
            try
            {
                var expected = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
                var actual = Process.GetCurrentProcess().MainModule?.FileName;
                if (string.IsNullOrWhiteSpace(expected) || string.IsNullOrWhiteSpace(actual)) return false;
                return string.Equals(System.IO.Path.GetFullPath(expected!), System.IO.Path.GetFullPath(actual!),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }
        // Read-only LAB look at the live world (return-portal placement): exact LAB executable + Lab/RuntimeArtifacts output only.
        private readonly Sr2SanctuaryProbe? _worldProbe = Sr2SanctuaryProbe.TryCreate(OracleExecutableMatches());
        private Sr2TravelRealm? _travel;
        private Sr2ReturnPortal? _returnPortal;
        // Read-only LAB registry capture: exact LAB executable + explicit output directory only.
        private readonly NativeRegistryCollector? _registryCollector =
            NativeRegistryCollector.TryCreate(OracleExecutableMatches());
        // LAB-only native imported-species registration (Q008): exact LAB executable + spec/output env only.
        private readonly Sr2SpeciesRegistrar? _speciesRegistrar =
            Sr2SpeciesRegistrar.TryCreate(OracleExecutableMatches());
        // LAB-only one-shot ops edge for imported species: same guards + armed registrar only.
        private readonly Sr2LabImportedOps? _speciesOps;

        public BridgeMod()
        {
            // Field initializers (including _speciesRegistrar) run before this constructor body.
            _speciesOps = Sr2LabImportedOps.TryCreate(OracleExecutableMatches(), _speciesRegistrar);
        }
        private bool _autoloadRequested;
        private float _targetLookupStartedSeconds = -1f;
        private float _nextTargetLookupSeconds;
        private bool _diagnosticsLogged;
        private bool _saveDiagnosticsLogged;
        private static readonly bool DiagnoseSaves = AutoloadContinue &&
            string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_DIAG_SAVES"), "1", StringComparison.Ordinal);
        private static readonly DateTime ProcessStartUtc = Process.GetCurrentProcess().StartTime.ToUniversalTime();
        internal static string Since() => ((DateTime.UtcNow - ProcessStartUtc).TotalMilliseconds / 1000.0).ToString("F1") + "s";
        private string? _autoloadReason;
        private static string _snapshotReason = "";
        private static string? _pendingReason;

        private static GameStatus? Omit(string reason)
        {
            if (reason != _snapshotReason) { _snapshotReason = reason; _pendingReason = "status omitted: " + reason + " t+" + Since(); }
            return null;
        }

        /// <summary>One-shot lab-only read of native save services before Continue changes scenes.</summary>
        private void LogSaveServicesOnce()
        {
            if (!DiagnoseSaves || _saveDiagnosticsLogged) return;
            try
            {
                var game = Il2Cpp.GameContext.Instance;
                var director = game == null ? null : game.AutoSaveDirector;
                var loader = Il2Cpp.SystemContext.Instance == null ? null : Il2Cpp.SystemContext.Instance.SceneLoader;
                var group = loader == null ? null : loader._currentSceneGroup;
                var name = group == null ? "" : group.name;
                if (director == null || loader == null || loader.IsSceneLoadInProgress ||
                    !name.StartsWith("MainMenu", StringComparison.Ordinal)) return;
                var storage = director.Storage();
                var initialized = storage != null && storage.IsInitialized();
                if (!initialized || !director.ProfileLoaded() || director.GameLoaded()) return;
                _saveDiagnosticsLogged = true;
                LoggerInstance.Msg("lab native slot probe: " + SaveSlotProbe.Capture(director));
                var available = director.AvailableGamesByGameName();
                var hasContinue = director.HasContinue();
                var selected = hasContinue ? director.GetSaveToContinue() : null;
                LoggerInstance.Msg("lab save services: menu=" + name + " loaded=" + director.GameLoaded() +
                    " profile=" + (director.ProfileLoaded()) + " storageReady=" + initialized +
                    " groups=" + (available == null ? "unknown" : available.Count.ToString()) +
                    " hasContinue=" + hasContinue + " selected=" +
                    (selected == null ? "none" : selected.Name + "/" + selected.SaveName) + " t+" + Since());
            }
            catch (Exception ex)
            {
                _saveDiagnosticsLogged = true;
                LoggerInstance.Msg("lab save services error: " + ex.GetType().Name + ": " + ex.Message + " t+" + Since());
            }
        }

        private void WaitForTargetLookup(string reason)
        {
            // Unscaled Unity uptime is independent of changes to the Windows wall clock.
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_targetLookupStartedSeconds < 0f)
                _targetLookupStartedSeconds = now;
            if (now - _targetLookupStartedSeconds >= 20f)
            {
                _autoloadRequested = true;
                LoggerInstance.Msg("lab autoload refused: requested native save group not found after storage grace period t+" + Since());
            }
            else
            {
                _nextTargetLookupSeconds = now + 0.5f;
                if (_autoloadReason != reason)
                {
                    _autoloadReason = reason;
                    LoggerInstance.Msg("lab autoload waiting: " + reason + " t+" + Since());
                }
            }
        }

        /// <summary>Lab-only: load the profile's Continue save when the oracle asks for it.</summary>
        private void MaybeAutoload()
        {
            if ((!AutoloadContinue && !TravelAutoload) || _autoloadRequested) return;
            try
            {
                var wanted = AutoloadContinue ? RequestedSaveGroup : TravelSave;
                try { MaybeSkipStartScreen(); }
                catch (Exception ex) { LoggerInstance.Msg("boot skip error: " + ex.GetType().Name + ": " + ex.Message); }
                var game = Il2Cpp.GameContext.Instance;
                var director = game == null ? null : game.AutoSaveDirector;
                var inGame = Il2Cpp.SceneContext.Instance != null && Il2Cpp.SceneContext.Instance.PlayerState != null;
                var sceneLoader = Il2Cpp.SystemContext.Instance == null ? null : Il2Cpp.SystemContext.Instance.SceneLoader;
                // Run 11: after boot the current group is "MainMenuFromBoot", for which
                // IsCurrentSceneGroupMainMenu() is false; accept any group named MainMenu*.
                var group = sceneLoader == null ? null : sceneLoader._currentSceneGroup;
                var groupName = group == null ? "" : group.name;
                var menuSettled = sceneLoader != null && !sceneLoader.IsSceneLoadInProgress &&
                                  (sceneLoader.IsCurrentSceneGroupMainMenu() || groupName.StartsWith("MainMenu", StringComparison.Ordinal));
                var reason = game == null ? "no game context" : director == null ? "no autosave director"
                    : inGame ? "already in game" : director.GameLoaded() ? "game already loaded"
                    : !menuSettled ? "main menu scene group not settled (group '" + groupName + "', busy=" + (sceneLoader != null && sceneLoader.IsSceneLoadInProgress) + ")"
                    : wanted != null && (director.Storage() == null || !director.Storage().IsInitialized())
                        ? "native storage not initialized"
                    : (wanted != null || TravelNewGame) && (director.Storage() == null || !director.Storage().IsInitialized() || !director.ProfileLoaded())
                        ? "native storage not initialized"
                    : wanted == null && !TravelNewGame && !director.HasContinue() ? "no continue save" : null;
                if (reason != null)
                {
                    if (reason != _autoloadReason) { _autoloadReason = reason; LoggerInstance.Msg("lab autoload waiting: " + reason + " t+" + Since()); }
                    return;
                }
                if (director == null) return; // The readiness reason implies this, but the compiler cannot infer it.
                if (!AutoloadContinue && TravelNewGame)
                {
                    // a few attempts: right after the menu settles some option services can still be warming up
                    if (UnityEngine.Time.realtimeSinceStartup < _nextNewGameTry) return;
                    try
                    {
                        Volatile.Write(ref _storyBlocking, true);
                        var slot = Sr2NewGame.Start(director, m => LoggerInstance.Msg(m));
                        _autoloadRequested = true;
                        LoggerInstance.Msg("travel: started a new native game in save slot " + slot + " t+" + Since());
                    }
                    catch (Exception ex)
                    {
                        _newGameTries++;
                        _nextNewGameTry = UnityEngine.Time.realtimeSinceStartup + 3f;
                        LoggerInstance.Msg("travel: " + ex.Message + (_newGameTries >= 5 ? " (giving up)" : " (retrying)"));
                        if (_newGameTries >= 5) _autoloadRequested = true;
                    }
                    return;
                }
                // Only the lab oracle can request a particular existing native save group.
                // Never turn a missing/invalid target into a load of the user's Continue game.
                if (wanted != null)
                {
                    if (!System.Text.RegularExpressions.Regex.IsMatch(wanted,
                            @"\A[A-Za-z0-9_-]{1,128}\z"))
                    {
                        _autoloadRequested = true;
                        LoggerInstance.Msg("lab autoload refused: invalid native save group ID");
                        return;
                    }
                    if (UnityEngine.Time.realtimeSinceStartup < _nextTargetLookupSeconds) return;
                    Il2CppSystem.Collections.Generic.List<Il2CppMonomiPark.SlimeRancher.Persist.Summary>? saves;
                    try { saves = director.GetSavesByGameName(wanted); }
                    catch (Exception ex)
                    {
                        WaitForTargetLookup("native save lookup not ready (" + ex.GetType().Name + ")");
                        return;
                    }
                    if (saves == null || saves.Count == 0 || saves[0] == null ||
                        string.IsNullOrWhiteSpace(saves[0].SaveName))
                    {
                        WaitForTargetLookup("requested native save group not yet listed");
                        return;
                    }
                    if (!string.Equals(saves[0].Name, wanted, StringComparison.Ordinal))
                    {
                        _autoloadRequested = true;
                        LoggerInstance.Msg("lab autoload refused: native summary identity does not match requested group");
                        return;
                    }
                    _autoloadRequested = true;
                    var selected = saves[0]; // Director sorts newest save first within a group.
                    LoggerInstance.Msg("lab autoload: loading requested game '" + selected.Name +
                        "' save '" + selected.SaveName + "' t+" + Since());
                    director.BeginLoad(selected.Name, selected.SaveName);
                    return;
                }
                // State-based: director initialized and the profile reports a Continue save.
                _autoloadRequested = true;
                var save = director.GetSaveToContinue();
                LoggerInstance.Msg("lab autoload: continuing game '" + save.Name + "' save '" + save.SaveName + "' t+" + Since());
                director.BeginLoad(save.Name, save.SaveName);
            }
            catch (Exception ex)
            {
                _autoloadRequested = true;
                LoggerInstance.Msg("lab autoload error: " + ex);
            }
        }

        private bool _introEnded;
        private int _introPresses;
        private float _nextIntroPress;
        private float _newGameStartedAt = -1f;
        private int _introIndex = -1, _introIndexPresses;
        private static bool _storyBlocking = false;  // written on the main thread, read by the heartbeat

        /// <summary>
        /// A game created for a portal crossing opens on the new-game story cards, which wait for key presses. The player is
        /// coming through a portal: advance the cards exactly like a key press does (TryToIterateFrame on the current card
        /// whenever the screen accepts input), so the story ends and fades into gameplay the normal way.
        /// </summary>
        private void MaybeEndNewGameIntro()
        {
            if (_introEnded || !TravelNewGame || !_autoloadRequested) { Volatile.Write(ref _storyBlocking, false); return; }
            if (_newGameStartedAt < 0f) _newGameStartedAt = UnityEngine.Time.realtimeSinceStartup;
            try
            {
                var intro = UnityEngine.Object.FindObjectOfType<Il2CppMonomiPark.SlimeRancher.UI.IntroSequence.IntroSequenceUIRoot>();
                if (intro == null)
                {
                    if (_introPresses > 0) { _introEnded = true; LoggerInstance.Msg("travel: new-game story done (" + _introPresses + " cards) t+" + Since()); }
                    // no story ever showed up: do not hold the arrival forever
                    else if (UnityEngine.Time.realtimeSinceStartup - _newGameStartedAt > 90f) { _introEnded = true; LoggerInstance.Msg("travel: no new-game story seen"); }
                    Volatile.Write(ref _storyBlocking, !_introEnded);
                    return;
                }
                Volatile.Write(ref _storyBlocking, true);
                if (UnityEngine.Time.realtimeSinceStartup < _nextIntroPress || !intro._isInputEnabled) return;
                // the story ends with a fade into the gameplay scene: never get there before that scene has finished loading
                // (ending it during the load left the game on a black screen for good)
                if (!CounterpartCreator.TryReadWorld(out _)) return;
                // like a patient player: at most two presses per card (the first may only finish the text), a second apart,
                // so the last card is never "ended" twice (that froze the load when the game ran slowly in the background)
                var index = intro._frameIndex;
                if (index != _introIndex) { _introIndex = index; _introIndexPresses = 0; }
                if (_introIndexPresses >= 2) return;
                _introIndexPresses++;
                _nextIntroPress = UnityEngine.Time.realtimeSinceStartup + 1.0f;
                intro.TryToIterateFrame(index);
                if (_introPresses++ == 0) LoggerInstance.Msg("travel: advancing the new-game story (the player arrives through the portal) t+" + Since());
                if (_introPresses > 200) { _introEnded = true; Volatile.Write(ref _storyBlocking, false); LoggerInstance.Msg("travel: new-game story did not end after 200 presses"); }
            }
            catch (Exception ex)
            {
                _introEnded = true;
                Volatile.Write(ref _storyBlocking, false);
                LoggerInstance.Msg("travel: could not advance the new-game story: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private int _newGameTries;
        private float _nextNewGameTry;
        private bool _startScreenSkipped;
        private string? _bootState;

        /// <summary>
        /// The StandaloneStart "press any key" screen waits for player input before loading the main
        /// menu scene group (runtime runs 3-4: SR2 stayed there while unfocused). Instead of sending
        /// input, ask the game's own SceneLoader to load the main menu group, exactly once, when no
        /// scene load is in flight.
        /// </summary>
        private void MaybeSkipStartScreen()
        {
            if (_startScreenSkipped) return;
            // The active scene may be a core scene; check every loaded scene for the start screen.
            var names = new System.Collections.Generic.List<string>();
            var onStart = false;
            for (var i = 0; i < UnityEngine.SceneManagement.SceneManager.sceneCount; i++)
            {
                var n = UnityEngine.SceneManagement.SceneManager.GetSceneAt(i).name;
                names.Add(n);
                if (n == "StandaloneEngagementPrompt") onStart = true; // runtime-verified scene name (log calls the group StandaloneStart)
            }
            var system = Il2Cpp.SystemContext.Instance;
            var loader = system == null ? null : system.SceneLoader;
            var bootState = "scenes=[" + string.Join(",", names) + "] loader=" + (loader == null ? "null" : loader.IsSceneLoadInProgress ? "busy" : "idle");
            if (bootState != _bootState) { _bootState = bootState; LoggerInstance.Msg("boot state: " + bootState + " t+" + Since()); }
            if (!onStart || loader == null || loader.IsSceneLoadInProgress) return;
            _startScreenSkipped = true;
            // Load exactly the group the start screen itself would load after a key press
            // (run 7: LoadMainMenuSceneGroup gave "MainMenu", not the boot "MainMenuFromBoot",
            // and the following Continue load never completed).
            // Run 8: the prompt component is StandaloneEngagementPrompt, not StandaloneStartScreen.
            // Invoke its own interaction handler (platform user setup, then its SceneGroupToLoad),
            // i.e. the code a key press reaches - no synthetic input event is generated.
            var prompt = UnityEngine.Object.FindObjectOfType<Il2CppMonomiPark.SlimeRancher.UI.MainMenu.StandaloneEngagementPrompt>();
            if (prompt == null || !prompt.ShouldShowInteractionPrompt()) { _startScreenSkipped = false; return; }
            var target = prompt.SceneGroupToLoad == null ? "?" : prompt.SceneGroupToLoad.name;
            LoggerInstance.Msg("boot: engagement prompt ready, loading its scene group '" + target + "' t+" + Since());
            try
            {
                // Run 10: OnInteract(default) throws (needs a real InputAction context). LoadSceneGroup()
                // is the prompt's own method that loads SceneGroupToLoad (MainMenuFromBoot).
                prompt.LoadSceneGroup();
            }
            catch (Exception ex)
            {
                LoggerInstance.Msg("boot: prompt LoadSceneGroup failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>Record which Pink reference IDs exist and what the vac holds (once, for verification).</summary>
        private void LogDiagnosticsOnce()
        {
            if (_diagnosticsLogged) return;
            try
            {
                var game = Il2Cpp.GameContext.Instance;
                var state = Il2Cpp.SceneContext.Instance == null ? null : Il2Cpp.SceneContext.Instance.PlayerState;
                if (game == null || game.LookupDirector == null || state == null || state.Ammo == null || !game.AutoSaveDirector.GameLoaded())
                    return;
                _diagnosticsLogged = true;
                var refs = new System.Collections.Generic.List<string>();
                foreach (var entry in game.LookupDirector._identifiableTypeByRefId)
                    if (entry.Key != null && entry.Key.Contains("Pink")) refs.Add(entry.Key);
                LoggerInstance.Msg("diag pink reference ids: " + string.Join(", ", refs));
                var slots = new System.Collections.Generic.List<string>();
                foreach (var slot in state.Ammo.Slots)
                    if (slot != null && slot.Id != null) slots.Add(slot.Id.ReferenceId + "=" + slot.Count);
                LoggerInstance.Msg("diag vac slots: " + string.Join(", ", slots));
            }
            catch (Exception ex)
            {
                _diagnosticsLogged = true;
                LoggerInstance.Msg("diag error: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static GameStatus? SnapshotOnMainThread()
        {
            try
            {
                if (!CounterpartCreator.TryReadWorld(out var world)) return Omit("world not ready");
                var game = world!.Game; var saveId = world.Name; var state = world.Scene.PlayerState!;
                int? pinkSlimes = null, pinkPlorts = null;
                // Counts are reported only when BOTH canonical types resolve by reference ID in this
                // build; then a zero is a real zero. Otherwise they are omitted, never guessed.
                var lookup = game.LookupDirector;
                if (lookup != null && state.Ammo != null &&
                    lookup.TryFindIdentifiableTypeByReferenceId(PinkSlimeRef, out var pinkSlime) && pinkSlime != null &&
                    lookup.TryFindIdentifiableTypeByReferenceId(PinkPlortRef, out var pinkPlort) && pinkPlort != null)
                {
                    int slimes = 0, plorts = 0;
                    foreach (var slot in state.Ammo.Slots)
                    {
                        if (slot == null || slot.Id == null || slot.Count <= 0) continue;
                        if (slot.Id.Pointer == pinkSlime.Pointer) slimes = checked(slimes + slot.Count);
                        else if (slot.Id.Pointer == pinkPlort.Pointer) plorts = checked(plorts + slot.Count);
                    }
                    if (slimes <= 1_000_000 && plorts <= 1_000_000) { pinkSlimes = slimes; pinkPlorts = plorts; }
                }
                else Omit("pink reference ids unresolved (counts omitted)");
                if (_snapshotReason != "ok" && pinkSlimes != null)
                {
                    _snapshotReason = "ok";
                    _pendingReason = "status live t+" + Since() + ": save '" + saveId + "' pinkSlime=" + pinkSlimes + " pinkPlort=" + pinkPlorts;
                }
                return new GameStatus
                {
                    // a game made for a crossing is not "ready" while its new-game story still runs: the player must arrive in the world
                    player_alive = state.GetCurrHealth() > 0 && !Volatile.Read(ref _storyBlocking), save_id = saveId,
                    pink_slime_count = pinkSlimes, pink_plort_count = pinkPlorts,
                };
            }
            catch (Exception ex)
            {
                CounterpartCreator.ClearWorldReadiness();
                // Scenes can be torn down at any time. Keep the process heartbeat, omit gameplay fields.
                return Omit("exception " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        public override void OnInitializeMelon()
        {
            CounterpartCreator.ClearWorldReadiness();
            LoggerInstance.Msg("SlimeBridge SR2 core " + PluginVersion + " loaded (pid " + Pid + ") t+" + Since());
            _capabilityProbe = CounterpartCapabilityProbe.TryCreate(message => LoggerInstance.Msg(message));
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token))
            {
                LoggerInstance.Msg("SLIMEBRIDGE_URL/SLIMEBRIDGE_TOKEN not set; heartbeat disabled");
                return;
            }
            // Bridge mode: keep ticking while unfocused (runtime run 3 proved SR2 froze at
            // StandaloneStart when SR1 held focus). Native Unity setting, no input automation.
            UnityEngine.Application.runInBackground = true;
            _travel = Sr2TravelRealm.TryCreate();
            _returnPortal = Sr2ReturnPortal.TryCreate(_travel, OracleExecutableMatches());
            if (!LabFocusDiagnostics.Requested && !CounterpartCapabilityProbe.Requested && !NativeRegistryCollector.Requested)
                _storageBinding = NativeStorageBinding.TryCreate(LabCreationAllowed, message => LoggerInstance.Msg(message));
            if (!LabFocusDiagnostics.Requested && !CounterpartCapabilityProbe.Requested && !NativeRegistryCollector.Requested) _inventory = InventoryAgent.Create(AutoloadContinue &&
                string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_GIVE"), "1", StringComparison.Ordinal),
                message => LoggerInstance.Msg(message), LabCreationAllowed,
                _storageBinding == null ? null : _storageBinding.IsCurrent);
            if (LabFocusDiagnostics.Requested && !CounterpartCapabilityProbe.Requested && !NativeRegistryCollector.Requested)
                _focusDiagnosticAllowed = LabFocusDiagnostics.Eligible(LabFocusDiagnostics.Requested,
                    OracleExecutableMatches(), AutoloadContinue, RequestedSaveGroup);
            _stop = new CancellationTokenSource();
            _ = Task.Run(() => Loop(url!.TrimEnd('/') + "/v1/heartbeat", token!, _stop.Token));
        }

        private Action<string>? _perfLog;

        /// <summary>The optional performance profile (launcher setting): applied once the game has settled, again when the quality level changes.</summary>
        public override void OnLateUpdate()
        {
            SlimeBridge.Shared.PerformanceTuner.Tick(UnityEngine.Time.realtimeSinceStartup, _perfLog ??= text => LoggerInstance.Msg(text));
        }

        public override void OnUpdate()
        {
            var message = Interlocked.Exchange(ref _pendingLog, null);
            if (message != null) LoggerInstance.Msg(message);
            _worldProbe?.Tick(text => LoggerInstance.Msg(text));
            _travel?.Tick();
            _returnPortal?.Tick();
            MaybeEndNewGameIntro();
            // Imported-species registration runs BEFORE any save autoload: saved imported ids must
            // re-resolve at every startup. The mode is exclusive until registration completes.
            if (Sr2SpeciesRegistrar.Requested)
            {
                if (_speciesRegistrar != null)
                {
                    _speciesRegistrar.Tick(text => LoggerInstance.Msg(text));
                    if (!_speciesRegistrar.Done)
                    {
                        try { MaybeSkipStartScreen(); }
                        catch (Exception) { /* Registration waits for the native registry regardless. */ }
                        return;
                    }
                }
                else
                {
                    // Refused registration configuration stays exclusive: it must never fall
                    // through into other LAB paths or the creation flow.
                    return;
                }
            }
            if (Sr2LabImportedOps.Requested)
            {
                if (_speciesOps != null)
                {
                    if (!_speciesOps.Done)
                    {
                        MaybeAutoload();
                        _speciesOps.Tick(text => LoggerInstance.Msg(text));
                        return;
                    }
                }
                else
                {
                    // Refused ops configuration stays exclusive, exactly like its siblings.
                    return;
                }
            }
            if (Sr2MeshDump.Requested)
            {
                try { MaybeSkipStartScreen(); }
                catch (Exception) { /* The dump waits for real world readiness regardless. */ }
                MaybeAutoload();
                Sr2MeshDump.Tick(OracleExecutableMatches());
                return;
            }
            if (NativeRegistryCollector.Requested)
            {
                if (_registryCollector != null)
                {
                    try { MaybeSkipStartScreen(); }
                    catch (Exception) { /* Capture still waits for real native registry readiness. */ }
                    _registryCollector.Tick(text => LoggerInstance.Msg(text));
                }
                return;
            }
            // Diagnostic mode excludes world autoload and all inventory/creation commands,
            // even when its output/executable guard refused initialization.
            if (CounterpartCapabilityProbe.Requested)
            {
                if (_capabilityProbe != null)
                {
                    try { MaybeSkipStartScreen(); }
                    catch (Exception) { /* The bounded diagnostic reports menu-readiness failure. */ }
                    _capabilityProbe.Tick();
                }
                return;
            }
            if (LabFocusDiagnostics.Requested)
            {
                // Exclusive read-only mode: even a refused probe cannot enter command/creation handling.
                if (_focusDiagnosticAllowed && AutoloadContinue)
                {
                    // Same existing-save path as Continue: the boot engagement prompt is required to reach
                    // the menu while unfocused. It is not the NEWGAME intro skipper (InventoryAgent only).
                    MaybeAutoload();
                    TickFocusDiagnostics();
                }
            }
            else
            {
                // The protected creation target stays at the menu; it deliberately has no
                // Continue/autoload request, so it must still advance the native boot prompt.
                if (LabCreationAllowed && !AutoloadContinue)
                {
                    try { MaybeSkipStartScreen(); }
                    catch (Exception ex) { LoggerInstance.Msg("creation menu boot error: " + ex.GetType().Name); }
                }
                LogSaveServicesOnce();
                MaybeAutoload();
                _storageBinding?.Tick();
                _inventory?.Tick();
            }
            if (_stop == null || DateTime.UtcNow < _nextSnapshot) return;
            LogDiagnosticsOnce();
            _nextSnapshot = DateTime.UtcNow.AddSeconds(2);
            var status = SnapshotOnMainThread();
            var reasonLog = Interlocked.Exchange(ref _pendingReason, null);
            if (reasonLog != null) LoggerInstance.Msg(reasonLog);
            Volatile.Write(ref _status, status == null ? null : new StampedStatus(status, DateTime.UtcNow));
        }

        private void TickFocusDiagnostics()
        {
            if (!_focusDiagnosticAllowed || _focusDiagnostics?.Stopped == true) return;
            var epoch = _focusEpoch;
            var exact = CounterpartCreator.TryReadWorld(out var world) &&
                string.Equals(world!.Name, RequestedSaveGroup, StringComparison.Ordinal);
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (!_focusDiagnosticAllowed || epoch != _focusEpoch) return;
            // Boot/menu unloads precede the requested world; no sampler owns those scenes.
            // Once published in the exact world, every unload permanently stops this instance.
            if (exact && _focusDiagnostics == null)
                _focusDiagnostics = LabFocusDiagnostics.TryCreate(OracleExecutableMatches(), AutoloadContinue,
                    RequestedSaveGroup, message => LoggerInstance.Msg(message));
            _focusDiagnostics?.Tick(now, exact);
        }

        // Gameplay status is only sent while fresh: if the main-thread sampler stalls (loading,
        // hang, deinit), the network loop must not keep reporting an old save/inventory as live.
        private static readonly TimeSpan StatusMaxAge = TimeSpan.FromSeconds(10);

        private sealed class StampedStatus
        {
            public StampedStatus(GameStatus status, DateTime sampledUtc) { Status = status; SampledUtc = sampledUtc; }
            public GameStatus Status { get; }
            public DateTime SampledUtc { get; }
        }

        internal static bool IsFresh(DateTime sampledUtc, DateTime nowUtc) =>
            nowUtc - sampledUtc <= StatusMaxAge && sampledUtc <= nowUtc + TimeSpan.FromSeconds(1);

        public override void OnSceneWasUnloaded(int buildIndex, string sceneName)
        {
            _focusEpoch++;
            _inventory?.CancelBackgroundIntro();
            _focusDiagnostics?.Stop("scene-unloaded");
        }

        public override void OnGUI() { _travel?.Gui(); }

        public override void OnDeinitializeMelon()
        {
            _travel?.Unload();
            _focusDiagnosticAllowed = false;
            _focusEpoch++;
            _inventory?.Shutdown();
            _focusDiagnostics?.Stop("deinitialize");
            CounterpartCreator.ClearWorldReadiness();
            _stop?.Cancel();
        }

        private async Task Loop(string endpoint, string token, CancellationToken cancel)
        {
            var acknowledged = false;
            while (!cancel.IsCancellationRequested)
            {
                try
                {
                    var stamped = Volatile.Read(ref _status);
                    var fresh = stamped != null && IsFresh(stamped.SampledUtc, DateTime.UtcNow) ? stamped.Status : null;
                    var body = JsonSerializer.Serialize(new
                    {
                        protocol_version = 1,
                        message_type = "heartbeat",
                        realm = "SR2",
                        request_id = Guid.NewGuid().ToString("N"),
                        pid = Pid,
                        plugin_version = PluginVersion,
                        status = fresh,
                    }, JsonOptions);
                    using var request = new HttpRequestMessage(HttpMethod.Post, endpoint)
                    {
                        Content = new StringContent(body, Encoding.UTF8, "application/json"),
                    };
                    request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + token);
                    using var response = await Http.SendAsync(request, cancel).ConfigureAwait(false);
                    if (response.IsSuccessStatusCode && !acknowledged)
                    {
                        acknowledged = true;
                        _pendingLog = "SR2 heartbeat acknowledged by BridgeHost";
                    }
                }
                catch (OperationCanceledException) when (cancel.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception)
                {
                    // BridgeHost absent or restarting: retry next interval.
                }
                try { await Task.Delay(TimeSpan.FromSeconds(5), cancel).ConfigureAwait(false); }
                catch (OperationCanceledException) { return; }
            }
        }
    }
}
