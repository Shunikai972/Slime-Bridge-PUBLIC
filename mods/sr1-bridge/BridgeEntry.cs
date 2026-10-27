using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SRML;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Minimal SR1 BridgeHost heartbeat. Network I/O runs off the Unity main thread; the game never
    /// waits on the BridgeHost and an absent host is silently tolerated.
    /// </summary>
    public sealed class BridgeEntry : ModEntryPoint
    {
        // Must stay a plain X.Y[.Z] integer string: SRML's ModVersion.Parse rejects suffixes like "-dev".
        internal const string PluginVersion = "0.1.2";
        private static readonly int Pid = Process.GetCurrentProcess().Id;

        private DateTime _nextHeartbeat = DateTime.MinValue;
        private int _sending;
        private int _acknowledged;
        private volatile bool _unloaded;
        private string? _pendingLog;

        [Serializable]
        private sealed class GameStatus
        {
            public bool player_alive;
            public string save_id = "";
            public int pink_slime_count;
            public int pink_plort_count;
        }

        [Serializable]
        private sealed class Heartbeat
        {
            public int protocol_version = 1;
            public string message_type = "heartbeat";
            public string realm = "SR1";
            public string request_id = Guid.NewGuid().ToString("N");
            public int pid = Pid;
            public string plugin_version = PluginVersion;
            public GameStatus? status;
        }

        // Last reason a gameplay snapshot was omitted; logged only when it changes (lab diagnostics).
        private static string _snapshotReason = "";

        private static GameStatus? Omit(string reason)
        {
            if (reason != _snapshotReason)
            {
                _snapshotReason = reason;
                UnityEngine.Debug.Log("[SlimeBridge] status omitted: " + reason);
            }
            return null;
        }

        private static GameStatus? SnapshotOnMainThread()
        {
            try
            {
                var scene = SRSingleton<SceneContext>.Instance;
                var game = SRSingleton<GameContext>.Instance;
                if (game == null || game.AutoSaveDirector == null) return Omit("no game context");
                if (scene == null) return Omit("no scene context");
                if (game.AutoSaveDirector.IsLoadingGame()) return Omit("game loading");
                if (scene.Player == null || scene.PlayerState == null) return Omit("no player");
                if (!scene.Player.activeInHierarchy) return Omit("player inactive");
                if (game.AutoSaveDirector.SavedGame == null) return Omit("no saved game");
                var saveId = game.AutoSaveDirector.SavedGame.GetName();
                if (string.IsNullOrWhiteSpace(saveId) || saveId.Length > 128) return Omit("invalid save id");
                var state = scene.PlayerState;
                var ammo = state.GetAmmo(PlayerState.AmmoMode.DEFAULT);
                if (ammo == null) return Omit("no default ammo");
                var snapshot = new GameStatus { player_alive = state.GetCurrHealth() > 0, save_id = saveId };
                for (var i = 0; i < ammo.GetUsableSlotCount(); i++)
                {
                    var count = ammo.GetSlotCount(i);
                    if (count < 0) return null;
                    var id = ammo.GetSlotName(i);
                    if (id == Identifiable.Id.PINK_SLIME) snapshot.pink_slime_count = checked(snapshot.pink_slime_count + count);
                    if (id == Identifiable.Id.PINK_PLORT) snapshot.pink_plort_count = checked(snapshot.pink_plort_count + count);
                }
                // Preserve a process-only heartbeat if an unusual inventory exceeds the host schema.
                if (snapshot.pink_slime_count > 1_000_000 || snapshot.pink_plort_count > 1_000_000)
                    return Omit("count out of range");
                if (_snapshotReason != "ok")
                {
                    _snapshotReason = "ok";
                    UnityEngine.Debug.Log("[SlimeBridge] status live t+" + Since() + ": save '" + saveId + "' alive=" + snapshot.player_alive +
                                          " pinkSlime=" + snapshot.pink_slime_count + " pinkPlort=" + snapshot.pink_plort_count);
                }
                return snapshot;
            }
            catch (Exception ex)
            {
                // A scene may be loading or unloading; omit gameplay fields, keep the process heartbeat.
                return Omit("exception " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static string BuildPayload()
        {
            var heartbeat = new Heartbeat { status = SnapshotOnMainThread() };
            // Built by hand: runtime proof showed JsonUtility dropping the nested status object.
            // Protocol v1 omits status entirely at the menu.
            var head = "{\"protocol_version\":1,\"message_type\":\"heartbeat\",\"realm\":\"SR1\",\"request_id\":\"" +
                       heartbeat.request_id + "\",\"pid\":" + Pid + ",\"plugin_version\":\"" + PluginVersion + "\"";
            var s = heartbeat.status;
            if (s == null) return head + "}";
            return head + ",\"status\":{\"player_alive\":" + (s.player_alive ? "true" : "false") +
                   ",\"save_id\":\"" + JsonEscape(s.save_id) + "\",\"pink_slime_count\":" + s.pink_slime_count +
                   ",\"pink_plort_count\":" + s.pink_plort_count + "}}";
        }

        internal static string JsonEscape(string value)
        {
            var sb = new StringBuilder(value.Length + 8);
            foreach (var c in value)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            return sb.ToString();
        }

        private static readonly DateTime ProcessStartUtc = Process.GetCurrentProcess().StartTime.ToUniversalTime();

        internal static string Since() => ((DateTime.UtcNow - ProcessStartUtc).TotalMilliseconds / 1000.0).ToString("F1") + "s";

        public override void PostLoad()
        {
            UnityEngine.Debug.Log("[SlimeBridge] SR1 core " + PluginVersion + " loaded (pid " + Pid + ") t+" + Since());
            // In-game dev console command (slimebridge list|spawn <canonical>): registered once;
            // execution is registrar-gated, so non-LAB installs fail closed with a clear message.
            try
            {
                SRML.Console.Console.RegisterCommand(new DevSpawnCommand(
                    () => _speciesRegistrar,
                    () =>
                    {
                        try
                        {
                            var game = GameContext.Instance;
                            return game != null && SceneContext.Instance != null && SceneContext.Instance.Player != null &&
                                !Levels.isMainMenu() && !game.AutoSaveDirector.IsLoadingGame();
                        }
                        catch (Exception) { return false; }
                    }));
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] dev console command registration skipped: " + ex.GetType().Name);
            }
            try { SRML.Console.Console.RegisterCommand(new WhereCommand()); }
            catch (Exception ex) { UnityEngine.Debug.Log("[SlimeBridge] where command registration skipped: " + ex.GetType().Name); }
            // Sanctuary (island, statues, gold gate, portal): only when a layout is installed or a LAB oracle armed it.
            try
            {
                _sanctuary = SanctuaryHost.TryCreate(OracleExecutableMatches());
                _travel = Sr1TravelRealm.TryCreate();
                if (_sanctuary != null) SRML.Console.Console.RegisterCommand(new SanctuaryCommand());
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] sanctuary wiring skipped: " + ex.GetType().Name);
            }
            // Bridge mode: a background (unfocused) game must keep ticking, or its world never becomes
            // ready and warm transitions are impossible. Native Unity setting, no input automation.
            if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL")))
                UnityEngine.Application.runInBackground = true;
            // lab_give (controlled test inventory) is only honored for the exact lab executable.
            try
            {
                if (!NativeRegistryCollector.Requested) _inventory = InventoryAgent.Create(AutoloadContinue &&
                    string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_GIVE"), "1", StringComparison.Ordinal),
                    LabCreationAllowed);
                if (_inventory == null) UnityEngine.Debug.Log("[SlimeBridge] inventory agent disabled (no BridgeHost URL/token)");
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] inventory agent failed to start: " + ex);
            }
            if (_speciesRegistrar != null)
            {
                try
                {
                    ImportedIdentityPatch.EnsurePatched();
                    ImportedAppearancePatch.EnsurePatched();
                    ImportedGameplayAnimationPatch.EnsurePatched();
                    ImportedEconomyPatch.EnsurePatched(_speciesRegistrar);
                    ImportedFaceExpressions.EnsurePatched();
                    _importedOps = LabImportedOps.TryCreate(OracleExecutableMatches(), _speciesRegistrar);
                }
                catch (Exception ex)
                {
                    UnityEngine.Debug.Log("[SlimeBridge] imported-species wiring failed: " + ex);
                }
            }
        }

        private InventoryAgent? _inventory;
        private SanctuaryHost? _sanctuary;
        private Sr1TravelRealm? _travel;
        private bool _inventoryErrorLogged;
        // Read-only LAB registry capture: exact LAB executable + explicit output directory only.
        private readonly NativeRegistryCollector? _registryCollector =
            NativeRegistryCollector.TryCreate(OracleExecutableMatches());
        // Imported-species native registration (Q007) and the one-shot LAB ops edge (Q033):
        // both require the exact LAB executable plus an oracle-provided spec/output directory.
        private readonly SpeciesRegistrar? _speciesRegistrar = SpeciesRegistrar.TryCreate(OracleExecutableMatches());
        private LabImportedOps? _importedOps;

        // Only an oracle targeting this exact executable may request unattended native loading.
        // An inherited AUTOLOAD flag alone must never load a normal game install's save.
        private static readonly bool AutoloadContinue = OracleAutoloadRequested();
        private static readonly string? RequestedSaveGroup = AutoloadContinue ?
            Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_SAVE_ID") : null;
        // The launcher started this game as the other side of a portal crossing: load the linked save (or Continue) by itself.
        private static readonly bool TravelAutoload = string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_MODE"), "DESTINATION", StringComparison.Ordinal) &&
            !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL"));
        private static readonly string? TravelSave = TravelAutoload ? Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_SAVE") : null;
        // Asked by the launcher when the player chose "new save" for this crossing: a brand new native game, never an existing one.
        private static readonly string? TravelNewName = TravelAutoload && TravelSave == null ? CleanNewName(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_NEW")) : null;
        private static string? CleanNewName(string? raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return null;
            var name = System.Text.RegularExpressions.Regex.Replace(raw!, @"[^A-Za-z0-9 _-]", "").Trim();
            return name.Length == 0 ? null : (name.Length > 40 ? name.Substring(0, 40) : name);
        }
        private bool _newGameStarted;
        private static bool OracleAutoloadRequested()
        {
            return string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_AUTOLOAD"), "continue", StringComparison.Ordinal) &&
                OracleExecutableMatches();
        }
        private static readonly bool LabCreationAllowed =
            string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_CREATE"), "1", StringComparison.Ordinal) && OracleExecutableMatches();

        [Serializable]
        private sealed class OracleLabManifest
        {
            public string game = "";
            public string source = "";
        }

        internal static bool LabRestPoseRequested => OracleExecutableMatches() &&
            string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_REST_POSE"), "1", StringComparison.Ordinal);

        internal static bool OracleExecutableMatches()
        {
            try
            {
                var expected = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
                var actual = Process.GetCurrentProcess().MainModule?.FileName;
                if (string.IsNullOrWhiteSpace(expected) || string.IsNullOrWhiteSpace(actual) ||
                    !string.Equals(Path.GetFullPath(expected), Path.GetFullPath(actual), StringComparison.OrdinalIgnoreCase)) return false;
                var directory = new DirectoryInfo(Path.GetDirectoryName(actual));
                if (!string.Equals(directory.Name, "SR1", StringComparison.OrdinalIgnoreCase) ||
                    !string.Equals(directory.Parent?.Name, "Games", StringComparison.OrdinalIgnoreCase) ||
                    !string.Equals(directory.Parent?.Parent?.Name, "Lab", StringComparison.OrdinalIgnoreCase)) return false;
                for (var cursor = directory; cursor != null; cursor = cursor.Parent)
                    if ((cursor.Attributes & FileAttributes.ReparsePoint) != 0) return false;
                var marker = Path.Combine(directory.FullName, "slimebridge-lab.json");
                if (!File.Exists(marker) || (File.GetAttributes(marker) & FileAttributes.ReparsePoint) != 0 ||
                    (File.GetAttributes(actual) & FileAttributes.ReparsePoint) != 0) return false;
                var manifest = UnityEngine.JsonUtility.FromJson<OracleLabManifest>(File.ReadAllText(marker));
                if (manifest == null || manifest.game != "SR1" || string.IsNullOrEmpty(manifest.source)) return false;
                var source = Path.GetFullPath(manifest.source).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                return !string.Equals(directory.FullName, source, StringComparison.OrdinalIgnoreCase) &&
                    !directory.FullName.StartsWith(source + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }
        private bool _autoloadRequested;
        private float _targetLookupStartedSeconds = -1f;
        private float _nextTargetLookupSeconds;

        private void WaitForTargetLookup(string reason)
        {
            // Unscaled Unity uptime is independent of changes to the Windows wall clock.
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_targetLookupStartedSeconds < 0f)
            {
                _targetLookupStartedSeconds = now;
                UnityEngine.Debug.Log("[SlimeBridge] lab autoload waiting: " + reason);
            }
            if (now - _targetLookupStartedSeconds >= 20f)
            {
                _autoloadRequested = true;
                UnityEngine.Debug.Log("[SlimeBridge] lab autoload refused: requested native save group not found after 20 seconds");
            }
            else
            {
                _nextTargetLookupSeconds = now + 0.5f;
            }
        }

        private void MaybeAutoload()
        {
            if ((!AutoloadContinue && !TravelAutoload) || _autoloadRequested) return;
            try
            {
                if (!AutoloadContinue && TravelNewName != null) { StartTravelNewGame(); return; }
                var wanted = AutoloadContinue ? RequestedSaveGroup : TravelSave;
                var director = SRSingleton<GameContext>.Instance?.AutoSaveDirector;
                if (!Levels.isMainMenu() || director == null || director.IsLoadingGame()) return;
                if (wanted != null)
                {
                    if (!System.Text.RegularExpressions.Regex.IsMatch(wanted,
                            @"\A[A-Za-z0-9_-]{1,128}\z"))
                    {
                        _autoloadRequested = true;
                        UnityEngine.Debug.Log("[SlimeBridge] lab autoload refused: invalid native save group ID");
                        return;
                    }
                    if (UnityEngine.Time.realtimeSinceStartup < _nextTargetLookupSeconds) return;
                    if (director.StorageProvider == null)
                    {
                        WaitForTargetLookup("native save storage not ready");
                        return;
                    }
                    System.Collections.Generic.Dictionary<string, System.Collections.Generic.List<GameData.Summary>> available;
                    try { available = director.AvailableGamesByGameName(); }
                    catch (Exception ex)
                    {
                        WaitForTargetLookup("native save lookup not ready (" + ex.GetType().Name + ")");
                        return;
                    }
                    if (available == null || !available.TryGetValue(wanted, out var saves) ||
                        saves == null || saves.Count == 0 || saves[0] == null)
                    {
                        WaitForTargetLookup("requested native save group not yet listed");
                        return;
                    }
                    var selected = saves[0]; // Native director sorts by save number/timestamp, descending.
                    if (!string.Equals(selected.name, wanted, StringComparison.Ordinal) ||
                        selected.isInvalid || string.IsNullOrWhiteSpace(selected.saveName))
                    {
                        _autoloadRequested = true;
                        UnityEngine.Debug.Log("[SlimeBridge] lab autoload refused: invalid or mismatched native save summary");
                        return;
                    }
                    bool hasSaveData;
                    try { hasSaveData = director.StorageProvider.HasGameData(selected.saveName); }
                    catch (Exception ex)
                    {
                        WaitForTargetLookup("native save file check not ready (" + ex.GetType().Name + ")");
                        return;
                    }
                    if (!hasSaveData)
                    {
                        WaitForTargetLookup("requested native save file not yet available");
                        return;
                    }
                    _autoloadRequested = true;
                    UnityEngine.Debug.Log("[SlimeBridge] lab autoload: loading requested game '" + selected.name +
                        "' save '" + selected.saveName + "' t+" + Since());
                    director.BeginLoad(selected.name, selected.saveName,
                        () => UnityEngine.Debug.Log("[SlimeBridge] lab autoload failed for requested save group"));
                    return;
                }
                // Continue uses the loaded profile's ContinueGameName, set by LoadProfile in OnSceneLoaded.
                if (!director.HasContinue()) return;
                _autoloadRequested = true;
                var save = director.GetSaveToContinue();
                UnityEngine.Debug.Log("[SlimeBridge] lab autoload: continuing game '" + save.name + "' save '" + save.saveName + "' t+" + Since());
                director.BeginLoad(save.name, save.saveName, () => UnityEngine.Debug.Log("[SlimeBridge] lab autoload failed"));
            }
            catch (Exception ex)
            {
                _autoloadRequested = true;
                UnityEngine.Debug.Log("[SlimeBridge] lab autoload error: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private void StartTravelNewGame()
        {
            if (_newGameStarted) return;
            var director = SRSingleton<GameContext>.Instance?.AutoSaveDirector;
            if (!Levels.isMainMenu() || director == null || director.IsLoadingGame() || UnityEngine.Time.realtimeSinceStartup < 10f) return;   // let SRML finish its menu setup
            _newGameStarted = true; _autoloadRequested = true;
            UnityEngine.Debug.Log("[SlimeBridge] travel: creating a new native game '" + TravelNewName + "'");
            director.LoadNewGame(TravelNewName, Identifiable.Id.PINK_SLIME, PlayerState.GameMode.CLASSIC, () => UnityEngine.Debug.Log("[SlimeBridge] travel: the new game failed to load"));
        }

        private static readonly Action<string> PerfLog = message => UnityEngine.Debug.Log(message);

        public override void Update()
        {
            var message = Interlocked.Exchange(ref _pendingLog, null);
            if (message != null) UnityEngine.Debug.Log(message);
            if (!_unloaded) FrameWatch.Tick();
            if (!_unloaded) SlimeBridge.Shared.PerformanceTuner.Tick(UnityEngine.Time.realtimeSinceStartup, PerfLog);
            if (!_unloaded) _registryCollector?.Tick();
            if (!_unloaded) { _speciesRegistrar?.Tick(); _importedOps?.Tick(); _sanctuary?.Tick(); _travel?.Tick(); }
            if (!NativeRegistryCollector.Requested) MaybeAutoload();

            if (!NativeRegistryCollector.Requested && !_unloaded && _inventory != null)
            {
                try { _inventory.Tick(); }
                catch (Exception ex)
                {
                    _inventory.CancelBackgroundIntro(); // An uncertain tick cannot retain automation ownership.
                    if (!_inventoryErrorLogged) UnityEngine.Debug.Log("[SlimeBridge] inventory agent tick failed: " + ex);
                    _inventoryErrorLogged = true;
                }
            }
            if (_unloaded || DateTime.UtcNow < _nextHeartbeat || Interlocked.Exchange(ref _sending, 1) != 0)
                return;
            _nextHeartbeat = DateTime.UtcNow.AddSeconds(5);
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token))
            {
                Interlocked.Exchange(ref _sending, 0);
                return;
            }
            try
            {
                var body = BuildPayload();
                Task.Run(() => Send(url!, token!, body));
            }
            catch (Exception)
            {
                // Serialization or task scheduling failed synchronously: Send's finally never runs,
                // so release the in-flight flag here or every later heartbeat would be suppressed.
                Interlocked.Exchange(ref _sending, 0);
            }
        }

        private void Send(string url, string token, string body)
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(url.TrimEnd('/') + "/v1/heartbeat");
                request.Method = "POST";
                request.ContentType = "application/json";
                request.Headers[HttpRequestHeader.Authorization] = "Bearer " + token;
                request.Proxy = null;
                request.Timeout = 1500;
                request.ReadWriteTimeout = 1500;
                var payload = Encoding.UTF8.GetBytes(body);
                request.ContentLength = payload.Length;
                using (var stream = request.GetRequestStream()) stream.Write(payload, 0, payload.Length);
                using (var response = (HttpWebResponse)request.GetResponse())
                {
                    if (response.StatusCode == HttpStatusCode.OK && Interlocked.Exchange(ref _acknowledged, 1) == 0)
                        _pendingLog = "[SlimeBridge] SR1 heartbeat acknowledged by BridgeHost";
                }
            }
            catch (Exception)
            {
                // BridgeHost absent or restarting: retry on the next interval, no per-frame logging.
            }
            finally
            {
                Interlocked.Exchange(ref _sending, 0);
            }
        }

        public override void Unload()
        {
            _unloaded = true;
            _sanctuary?.Unload();
            _travel?.Unload();
            _inventory?.CancelBackgroundIntro();
            ImportedGameplayAnimationPatch.Unload();
        }
    }
}
