using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using MonomiPark.SlimeRancher.Regions;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Owns the sanctuary at runtime: waits for a loaded world, builds it from the layout, wires plort catching, the gold gate
    /// and the portal to the Bridge sidecar progress, and (LAB only) runs one-shot oracle ops with screenshots and a receipt.
    /// Never edits native save data and never touches anything outside its own world root and sidecar.
    /// </summary>
    internal sealed class SanctuaryHost
    {
        private const float SettleSeconds = 4f;
        private readonly SanctuaryLayout _layout;
        private readonly bool _lab;
        private readonly bool _dev;
        private readonly string _stateDirectory;
        private readonly string? _outDirectory;
        private readonly LinkedList<string> _ops;
        private readonly SanctuaryNative _native = new SanctuaryNative();
        private readonly List<string> _events = new List<string>();
        private readonly List<string> _shots = new List<string>();
        private SanctuaryBuilder? _builder;
        private SanctuaryProgress _progress = new SanctuaryProgress();
        private float _readySince = -1f;
        private float _waitUntil;
        private float _lastCrossRequest = -100f;
        private bool _failed;
        private bool _receiptWritten;
        private string _saveId = "";
        private string? _pendingShot;
        private int _shotStage;
        private readonly bool _newGame;
        private bool _newGameRequested;

        internal static SanctuaryHost? Instance { get; private set; }
        internal bool Built => _builder != null;
        /// <summary>The portal is usable once its statues are done, or at once when the statues are only decoration (setting).</summary>
        private bool PortalOpen => !_layout.Portal.RequiresStatues || _progress.PortalUnlocked;
        internal string SaveId => _saveId;

        private SanctuaryHost(SanctuaryLayout layout, bool lab, bool dev, string stateDirectory, string? outDirectory, IEnumerable<string> ops)
        {
            _layout = layout; _lab = lab; _dev = dev; _stateDirectory = stateDirectory; _outDirectory = outDirectory;
            _newGame = lab && string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_SANCTUARY_NEWGAME"), "1", StringComparison.Ordinal);
            _ops = new LinkedList<string>(ops);
        }

        // ----- creation
        internal static SanctuaryHost? TryCreate(bool labExecutable)
        {
            try
            {
                var layoutPath = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SANCTUARY_LAYOUT");
                if (string.IsNullOrEmpty(layoutPath))
                {
                    var candidate = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "SlimeBridge", "sanctuary", "sanctuary-layout.json"));
                    if (!File.Exists(candidate)) return null;       // feature simply not installed
                    layoutPath = candidate;
                }
                else if (!labExecutable || !LayoutPathAllowed(layoutPath!))
                {
                    Debug.Log("[SlimeBridge] sanctuary layout path refused (needs the exact LAB executable and Lab/GeneratedContent/sanctuary)");
                    return null;
                }
                if (IsReparse(layoutPath!)) { Debug.Log("[SlimeBridge] sanctuary layout is a reparse point: refused"); return null; }
                var layout = SanctuaryLayout.Parse(File.ReadAllText(layoutPath!, Encoding.UTF8));
                var stateDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SANCTUARY_STATE_DIR");
                if (string.IsNullOrEmpty(stateDir) || !labExecutable) stateDir = Path.Combine(Application.persistentDataPath, "SlimeBridge");
                string? outDir = null;
                var ops = new List<string>();
                if (labExecutable)
                {
                    outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SANCTUARY_OUT");
                    var rawOps = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SANCTUARY_OPS");
                    if (!string.IsNullOrEmpty(rawOps) && !string.IsNullOrEmpty(outDir) && OutAllowed(outDir!))
                        foreach (var item in (List<object?>)MiniJson.Parse(rawOps!)!) ops.Add((string)item!);
                    else if (!string.IsNullOrEmpty(rawOps)) Debug.Log("[SlimeBridge] sanctuary ops ignored: output folder must be under Lab/RuntimeArtifacts");
                }
                var dev = labExecutable || string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_DEV"), "1", StringComparison.Ordinal);
                if (!labExecutable && dev)
                {
                    // Developer mode on a real install: the same commands the console allows there ("sanctuary unlock",
                    // "sanctuary tp portal_in", ...), queued from SLIMEBRIDGE_DEV_OPS; nothing else (no shots, no materials).
                    var rawDevOps = Environment.GetEnvironmentVariable("SLIMEBRIDGE_DEV_OPS");
                    if (!string.IsNullOrEmpty(rawDevOps))
                        foreach (var item in (List<object?>)MiniJson.Parse(rawDevOps!)!)
                            if (item is string op && DevOpAllowed(op)) ops.Add(op);
                }
                var host = new SanctuaryHost(layout, labExecutable, dev, stateDir!, outDir, ops);
                Instance = host;
                Debug.Log($"[SlimeBridge] sanctuary armed: {layout.Pieces.Count} pieces, {layout.Statues.Count} statues, {ops.Count} oracle ops");
                return host;
            }
            catch (Exception ex)
            {
                Debug.Log("[SlimeBridge] sanctuary disabled: " + ex.GetType().Name + ": " + ex.Message);
                return null;
            }
        }

        private static bool DevOpAllowed(string op) =>
            op == "status" || op == "wait" || op == "unlock" || op.StartsWith("wait:", StringComparison.Ordinal) || op.StartsWith("tp:", StringComparison.Ordinal) ||
            op == "vac" || op.StartsWith("give:", StringComparison.Ordinal) || op.StartsWith("gold:", StringComparison.Ordinal) || op.StartsWith("fill:", StringComparison.Ordinal);

        private static bool IsReparse(string path)
        {
            for (var cursor = new FileInfo(path).Directory; cursor != null; cursor = cursor.Parent)
                if ((cursor.Attributes & FileAttributes.ReparsePoint) != 0) return true;
            return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
        }

        private static bool LayoutPathAllowed(string path)
        {
            var root = LabSpecPathGuard.FindRepoRoot();
            return root != null && LabSpecPathGuard.IsUnderRoot(Path.Combine(root, "Lab", "GeneratedContent", "sanctuary"), path, out _);
        }

        private static bool OutAllowed(string path)
        {
            var root = LabSpecPathGuard.FindRepoRoot();
            return root != null && LabSpecPathGuard.IsUnderRoot(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), path, out _);
        }

        // ----- world readiness
        private static bool WorldReady()
        {
            try
            {
                var game = GameContext.Instance;
                var scene = SceneContext.Instance;
                return game != null && scene != null && scene.Player != null && scene.PlayerState != null && !Levels.isMainMenu() &&
                    !game.AutoSaveDirector.IsLoadingGame() && game.AutoSaveDirector.SavedGame != null;
            }
            catch (Exception) { return false; }
        }

        internal void Tick()
        {
            if (_failed) return;
            try
            {
                if (!WorldReady())
                {
                    if (_builder != null) Teardown("world left");
                    _readySince = -1f;
                    MaybeStartNewGame();
                    return;
                }
                if (_readySince < 0f) _readySince = Time.realtimeSinceStartup;
                SamplePerf();
                if (_builder == null)
                {
                    if (Time.realtimeSinceStartup - _readySince >= SettleSeconds) Build();
                    return;
                }
                RunOps();
            }
            catch (Exception ex)
            {
                _failed = true;
                _events.Add("FAILED " + ex.GetType().Name + ": " + ex.Message);
                Debug.Log("[SlimeBridge] sanctuary failed: " + ex);
                WriteReceipt();
            }
        }

        // LAB only: start a throw-away native new game from the main menu (the protected oracle removes the save afterwards).
        private void MaybeStartNewGame()
        {
            if (!_newGame || _newGameRequested || !_lab) return;
            try
            {
                var game = GameContext.Instance;
                if (game == null || game.AutoSaveDirector == null || !Levels.isMainMenu() || game.AutoSaveDirector.IsLoadingGame()) return;
                if (Time.realtimeSinceStartup < 12f) return;           // let SRML/SRMP finish their menu setup
                _newGameRequested = true;
                _events.Add("starting a throw-away native new game");
                game.AutoSaveDirector.LoadNewGame("SlimeBridge Sanctuary Test", Identifiable.Id.PINK_SLIME, PlayerState.GameMode.CLASSIC,
                    () => { _events.Add("new game failed to load"); Debug.Log("[SlimeBridge] sanctuary new game failed"); });
            }
            catch (Exception ex) { _newGameRequested = true; _events.Add("new game threw " + ex.GetType().Name); }
        }

        private void Teardown(string why)
        {
            _events.Add("teardown: " + why);
            try { _builder?.Destroy(); } catch (Exception) { }
            _builder = null;
        }

        // ----- build + wiring
        private void Build()
        {
            _saveId = GameContext.Instance.AutoSaveDirector.SavedGame.GetName() ?? "";
            ZoneDirector? sea = null;
            ZoneDirector.zones.TryGetValue(ZoneDirector.Zone.SEA, out sea);
            _progress = SanctuaryProgress.LoadOrCreate(_stateDirectory, _saveId, out var problem);
            if (problem != null) _events.Add("progress: " + problem);
            _progress.EnsureManifest(_layout.RequiredSlimes, _layout.KitSha256, _layout.DynamicRequirements);
            var started = Time.realtimeSinceStartup;
            _builder = new SanctuaryBuilder(_layout, _native);
            _builder.Build(sea);
            foreach (var line in _builder.Log) _events.Add(line);
            if (_builder.GateDepositor != null) _builder.GateDepositor.Catcher.OnPlort = _ => OnGatePlort();
            ApplyProgress(false);
            _events.Add($"built in {(Time.realtimeSinceStartup - started):F2}s for save '{_saveId}' (meshes {_native.MeshCount}, materials {_native.MaterialCount})");
            Debug.Log("[SlimeBridge] sanctuary built for save '" + _saveId + "'");
        }

        /// <summary>Hooks the island's statues and portal once the island exists (it is built when the gate opens).</summary>
        private void WireExtension()
        {
            if (_builder == null) return;
            foreach (var line in _builder.Log.Skip(_loggedLines)) _events.Add(line);
            _loggedLines = _builder.Log.Count;
            foreach (var built in _builder.Statues.Values)
            {
                var captured = built;
                captured.Catcher.OnPlort = _ => OnStatuePlort(captured);
            }
            if (_builder.Portal != null)
            {
                _builder.Portal.Trigger.OnPlayerEnter = OnPortalEntered;
                _builder.Portal.Trigger.OnPlayerExit = () => { };
                var fx = _native.ReferenceSlot()?.changeFX;
                var center = _builder.Portal.Membrane.transform;
                _builder.Portal.Glow.OnBurst = () =>
                {
                    try { if (fx != null) SRBehaviour.SpawnAndPlayFX(fx, center.position, center.rotation); }
                    catch (Exception ex) { Debug.Log("[SlimeBridge] portal burst fx skipped: " + ex.GetType().Name); }
                };
            }
        }

        private int _loggedLines;
        private bool _portalWasOpen;

        private void ApplyProgress(bool announce)
        {
            if (_builder == null) return;
            var gateOpen = !_layout.Gate.Enabled || _progress.GatePaid;
            // The walkway extension, the island and the portal are not part of the world until the gate is paid (access mode
            // ALWAYS has no gate and shows them from the start). Paying it live plays the reveal animation; a save that is
            // already open shows everything at once.
            if (gateOpen && _builder.EnsureExtension()) WireExtension();
            if (_builder.Extension != null)
            {
                var wasActive = _builder.Extension.activeSelf;
                if (gateOpen && !wasActive)
                {
                    _builder.Extension.SetActive(true);
                    if (announce && _builder.Reveal != null) _builder.Reveal.Play(); else _builder.Reveal?.Skip();
                    if (announce) Debug.Log("[SlimeBridge] sanctuary: the walkway and the island appear");
                }
                else if (!gateOpen && wasActive) _builder.Extension.SetActive(false);
            }
            foreach (var built in _builder.Statues.Values) SetStatueFilled(built, _progress.FilledStatues.Contains(built.Slime));
            if (_builder.GateDoor != null) _builder.GateDoor.SetActive(false);
            if (_builder.GateFrame != null) _builder.GateFrame.SetActive(false);
            if (_builder.GateDepositor != null)
            {
                _builder.GateDepositor.Root.SetActive(_layout.Gate.Enabled);
                if (_builder.GateExtras != null) _builder.GateExtras.SetActive(_layout.Gate.Enabled);
                SetStatueFilled(_builder.GateDepositor, _progress.GatePaid);
            }
            if (_builder.Portal != null)
            {
                var open = PortalOpen && gateOpen;
                if (!announce || _builder.Reveal != null && _builder.Reveal.Playing) { if (open != _portalWasOpen || !announce) _builder.Portal.Glow.Snap(open); }
                else if (open != _portalWasOpen) _builder.Portal.Glow.Open = open;       // animated: the ring runs with light, the membrane unfolds
                _portalWasOpen = open;
            }
            if (announce) Debug.Log($"[SlimeBridge] sanctuary: gate {(gateOpen ? "open" : "closed")}, statues {_progress.FilledRequiredCount()}/{_progress.RequiredManifest.Count}, portal {(_progress.PortalUnlocked ? "open" : "dormant")}");
        }

        private static void SetStatueFilled(BuiltStatue built, bool filled)
        {
            if (built.Toggle != null) built.Toggle.SetActive(filled);
        }

        private bool OnStatuePlort(BuiltStatue built)
        {
            if (!_progress.FillStatue(built.Slime, built.Spec.Required)) return false;
            SetStatueFilled(built, true);
            var unlocked = _progress.TryUnlockPortal();
            Persist();
            ApplyProgress(true);
            _events.Add("statue filled: " + built.Slime + (unlocked ? " (portal unlocked)" : ""));
            return true;
        }

        private bool OnGatePlort()
        {
            if (!_layout.Gate.Enabled || _progress.GatePaid) return false;
            var paid = _progress.DepositGold(1, _layout.Gate.Cost);
            Persist();
            ApplyProgress(true);
            _events.Add($"gold deposited {_progress.GoldDeposited}/{_layout.Gate.Cost}" + (paid ? " (gate open)" : ""));
            return true;
        }

        private void Persist()
        {
            try { _progress.Save(_stateDirectory); }
            catch (Exception ex) { _events.Add("progress save failed: " + ex.GetType().Name); Debug.Log("[SlimeBridge] sanctuary progress save failed: " + ex.Message); }
        }

        private void OnPortalEntered()
        {
            if (!PortalOpen)
            {
                Debug.Log($"[SlimeBridge] the portal is dormant: {_progress.FilledRequiredCount()}/{_progress.RequiredManifest.Count} statues filled");
                if (Time.realtimeSinceStartup - _lastDormantHint > 6f)
                {
                    _lastDormantHint = Time.realtimeSinceStartup;
                    TravelNotice.Post(TravelText.Dormant(_progress.FilledRequiredCount(), _progress.RequiredManifest.Count));
                }
                return;
            }
            if (Time.realtimeSinceStartup - _lastCrossRequest < 8f) return;
            _lastCrossRequest = Time.realtimeSinceStartup;
            _progress.PortalUsed = true;
            Persist();
            _events.Add("portal entered: crossing requested");
            PostCrossing();
        }

        private float _lastDormantHint = -100f;

        private void PostCrossing()
        {
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token))
            {
                Debug.Log("[SlimeBridge] portal crossing needs the Bridge launcher (no BridgeHost URL/token)");
                TravelNotice.Post(TravelText.Failure("NO_LAUNCHER", "SR2"));
                return;
            }
            TravelNotice.Post(TravelText.Opening("SR2"));            // instant feedback; the launcher's answer follows within a second
            var body = "{\"protocol_version\":1,\"message_type\":\"portal_cross\",\"realm\":\"SR1\",\"portal_id\":\"" +
                       BridgeEntry.JsonEscape(_layout.Portal.PortalId) + "\",\"save_id\":\"" + BridgeEntry.JsonEscape(_saveId) +
                       "\",\"request_id\":\"" + Guid.NewGuid().ToString("N") + "\"}";
            Task.Run(() =>
            {
                try
                {
                    var request = (HttpWebRequest)WebRequest.Create(url!.TrimEnd('/') + "/v1/portal/cross");
                    request.Method = "POST";
                    request.ContentType = "application/json";
                    request.Headers[HttpRequestHeader.Authorization] = "Bearer " + token;
                    request.Proxy = null; request.Timeout = 3000; request.ReadWriteTimeout = 3000;
                    var payload = Encoding.UTF8.GetBytes(body);
                    request.ContentLength = payload.Length;
                    using (var stream = request.GetRequestStream()) stream.Write(payload, 0, payload.Length);
                    using (var response = (HttpWebResponse)request.GetResponse()) { }
                }
                catch (Exception ex)
                {
                    // BridgeHost absent or too old: the player stays in SR1 and is told why
                    Debug.Log("[SlimeBridge] portal crossing request failed: " + ex.GetType().Name + ": " + ex.Message);
                    TravelNotice.Post(TravelText.Failure("HOST_UNREACHABLE", "SR2"));
                }
            });
        }

        // ----- travel: where the body waits and where it comes out
        /// <summary>Put the local player in front of the portal: facing it while waiting, facing away when emerging.</summary>
        internal bool PlacePlayerAtPortal(bool emerge)
        {
            try
            {
                if (_builder == null || _builder.Portal == null) return false;
                var scene = SceneContext.Instance;
                var player = scene.Player.GetComponent<TeleportablePlayer>();
                if (player == null) return false;
                var p = _layout.Portal.Position;
                var facing = Quaternion.Euler(0f, _layout.Portal.Yaw, 0f);
                var position = new Vector3(p[0], p[1], p[2]) + facing * Vector3.forward * (emerge ? 3.4f : 5.5f);
                var yaw = emerge ? _layout.Portal.Yaw : _layout.Portal.Yaw + 180f;
                player.TeleportTo(position, scene.RegionRegistry.GetCurrentRegionSetId(), new Vector3(4f, yaw, 0f), false, false);
                _lastCrossRequest = Time.realtimeSinceStartup;      // standing at the portal must not start a new crossing
                _events.Add(emerge ? "player emerged from the portal" : "player parked at the portal");
                return true;
            }
            catch (Exception ex) { _events.Add("portal placement failed: " + ex.GetType().Name); return false; }
        }

        internal void PulsePortal()
        {
            if (_builder != null && _builder.Portal != null) _builder.Portal.Glow.FlashUntil = Time.realtimeSinceStartup + 2.5f;
        }

        // ----- console helpers
        internal string Status()
        {
            if (_builder == null) return Built ? "building" : "not built (waiting for a loaded world)";
            var gate = !_layout.Gate.Enabled ? "off" : _progress.GatePaid ? "open" : $"{_progress.GoldDeposited}/{_layout.Gate.Cost} gold";
            return $"sanctuary save '{_saveId}': gate {gate}, statues {_progress.FilledRequiredCount()}/{_progress.RequiredManifest.Count}, portal {(_progress.PortalUnlocked ? "open" : "dormant")}, pieces {_builder.PiecesBuilt}, missing {_native.Missing.Count}";
        }

        internal bool DevAllowed => _dev;

        internal string DevCommand(string[] args)
        {
            if (!_dev) return "dev commands are disabled (set SLIMEBRIDGE_DEV=1)";
            if (_builder == null) return "the sanctuary is not built yet";
            var verb = args.Length > 0 ? args[0].ToLowerInvariant() : "";
            switch (verb)
            {
                case "vac": return "vac: " + Sr1Carry.Describe();
                case "give":
                {
                    if (args.Length < 3 || !int.TryParse(args[2], out var amount)) return "usage: give <IDENTIFIABLE_ID> <count>";
                    var got = Sr1Carry.Receive(new Dictionary<string, int> { [args[1].ToUpperInvariant()] = Math.Max(1, amount) });
                    return got.Count > 0 ? "gave " + string.Join(", ", got.Select(kv => kv.Key + " x" + kv.Value)) : "nothing fitted or unknown id " + args[1];
                }
                case "unlock": _progress.DevUnlockAll(); Persist(); ApplyProgress(true); return "gate, statues and portal unlocked";
                case "reset": _progress.DevReset(); Persist(); ApplyProgress(true); return "sanctuary progress reset";
                case "gold": _progress.DepositGold(args.Length > 1 && int.TryParse(args[1], out var n) ? n : 1, _layout.Gate.Cost); Persist(); ApplyProgress(true); return Status();
                case "fill":
                    if (args.Length < 2) return "usage: fill <SLIME>";
                    var slime = args[1].ToUpperInvariant();
                    var spec = _layout.FindStatue(slime);
                    if (spec == null) return "unknown statue " + slime;
                    _progress.FillStatue(slime, spec.Required); _progress.TryUnlockPortal(); Persist(); ApplyProgress(true); return Status();
                case "tp":
                    var target = args.Length > 1 ? args[1] : "spawn";
                    return Teleport(target) ? "teleported to " + target : "unknown target " + target + " (spawn, portal, gate, a shot name or a statue id)";
                default: return "usage: sanctuary <unlock|reset|gold [n]|fill <SLIME>|tp [target]>";
            }
        }

        private bool Teleport(string target)
        {
            Vector3 position; float yaw = _layout.Spawn.Yaw, pitch = 4f;
            if (target == "spawn") position = new Vector3(_layout.Spawn.Position[0], _layout.Spawn.Position[1], _layout.Spawn.Position[2]);
            else if (target == "portal") { var p = _layout.Portal.Position; position = new Vector3(p[0] + 0f, p[1], p[2]) + Quaternion.Euler(0f, _layout.Portal.Yaw, 0f) * Vector3.forward * 6f; yaw = _layout.Portal.Yaw + 180f; }
            else if (target == "portal_in") { var p = _layout.Portal.Position; position = new Vector3(p[0], p[1] + 0.4f, p[2]); yaw = _layout.Portal.Yaw + 180f; }
            else if (target == "gate") { position = FramePoint(2.5f, 0f, _layout.DeckTopY + 0.3f); yaw = _layout.HeadingY; }
            else if (target.StartsWith("statue.", StringComparison.Ordinal))
            {
                var spec = _layout.Statues.FirstOrDefault(x => x.Id == target);
                if (spec == null) return false;
                var front = Quaternion.Euler(0f, spec.Yaw, 0f) * Vector3.forward * 4.5f;
                position = new Vector3(spec.Position[0], spec.Position[1] + 0.3f, spec.Position[2]) + front;
                yaw = spec.Yaw + 180f;
            }
            else
            {
                var shot = _layout.Shots.FirstOrDefault(s => s.Name == target);
                if (shot == null) return false;
                position = new Vector3(shot.Position[0], shot.Position[1], shot.Position[2]);
                var look = new Vector3(shot.LookAt[0], shot.LookAt[1], shot.LookAt[2]) - position;
                yaw = Mathf.Atan2(look.x, look.z) * Mathf.Rad2Deg;
                pitch = -Mathf.Asin(Mathf.Clamp(look.normalized.y, -1f, 1f)) * Mathf.Rad2Deg;
            }
            var scene = SceneContext.Instance;
            var player = scene.Player.GetComponent<TeleportablePlayer>();
            if (player == null) return false;
            player.TeleportTo(position, scene.RegionRegistry.GetCurrentRegionSetId(), new Vector3(pitch, yaw, 0f), false, false);
            return true;
        }

        // ----- LAB oracle ops (one at a time; screenshots need frames to settle)
        private void RunOps()
        {
            if (_ops.Count == 0 && _pendingShot == null)
            {
                if (_outDirectory != null && !_receiptWritten) WriteReceipt();
                return;
            }
            if (Time.realtimeSinceStartup < _waitUntil) return;
            if (_pendingShot != null) { FinishShot(); return; }
            var op = _ops.First!.Value;
            _ops.RemoveFirst();
            _events.Add("op: " + op);
            if (op == "status") { _events.Add("status: " + Status()); return; }
            if (op == "wait") { _waitUntil = Time.realtimeSinceStartup + 3f; return; }
            if (op.StartsWith("wait:", StringComparison.Ordinal) && float.TryParse(op.Substring(5), NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds))
            { _waitUntil = Time.realtimeSinceStartup + Mathf.Clamp(seconds, 0f, 120f); return; }
            if (op.StartsWith("mat:", StringComparison.Ordinal)) { _events.Add(SetMembraneMaterial(op.Substring(4))); return; }
            if (op.StartsWith("fshot:", StringComparison.Ordinal)) { FreeShot(op.Substring(6)); return; }
            if (op.StartsWith("actors:", StringComparison.Ordinal)) { ActorShots(op.Substring(7)); return; }
            if (op == "fshots" || op.StartsWith("fshots:", StringComparison.Ordinal))
            {
                var prefix = op.Length > 7 ? op.Substring(7) + "/" : "";
                PushFront(_layout.Shots.Select(shot => "fshot:" + prefix + shot.Name));
                return;
            }
            if (op.StartsWith("tp_check:", StringComparison.Ordinal)) { TeleportAndCheck(op.Substring(9)); return; }
            if (op.StartsWith("shot:", StringComparison.Ordinal)) { StartShot(op.Substring(5)); return; }
            if (op == "shots") { PushFront(_layout.Shots.Select(shot => "shot:" + shot.Name)); return; }
            if (op.StartsWith("tp:", StringComparison.Ordinal))
            {
                var moved = Teleport(op.Substring(3));
                _events.Add(moved ? "teleported" : "teleport target unknown");
                if (_outDirectory == null) Debug.Log("[SlimeBridge] dev op " + op + " -> " + (moved ? "teleported" : "unknown target"));
                _waitUntil = Time.realtimeSinceStartup + 2f; return;
            }
            if (op.StartsWith("tp_report:", StringComparison.Ordinal)) { ReportPlayer(op.Substring(10)); return; }
            if (op.StartsWith("plort:", StringComparison.Ordinal)) { SpawnPlort(op.Substring(6)); return; }
            if (op.StartsWith("perf:", StringComparison.Ordinal)) { StartPerf(op.Substring(5)); return; }
            if (op.StartsWith("materials:", StringComparison.Ordinal)) { ListMaterials(op.Substring(10)); return; }
            if (op.StartsWith("toggle:", StringComparison.Ordinal)) { _events.Add(Toggle(op.Substring(7))); return; }
            var parts = op.Split(':');
            var outcome = DevCommand(parts);
            _events.Add(outcome);
            if (_outDirectory == null) Debug.Log("[SlimeBridge] dev op " + op + " -> " + outcome);   // developer mode: no receipt, so the log says it
        }

        /// <summary>LAB only: list the native materials whose name contains the text (name | shader | main texture).</summary>
        private void ListMaterials(string needle)
        {
            var found = 0;
            foreach (var material in Resources.FindObjectsOfTypeAll<Material>())
            {
                if (material == null || material.name == null || material.name.IndexOf(needle, StringComparison.OrdinalIgnoreCase) < 0) continue;
                var texture = material.HasProperty("_MainTex") ? material.GetTexture("_MainTex") : null;
                _events.Add("material: " + material.name + " | " + (material.shader != null ? material.shader.name : "?") + " | " + (texture != null ? texture.name : "-"));
                if (++found >= 60) break;
            }
            _events.Add("materials '" + needle + "': " + found + " listed");
        }

        // ----- LAB perf probe: frame times over a window, with the scene's cost drivers counted next to them
        private readonly List<float> _perfFrames = new List<float>();
        private float _perfUntil = -1f;
        private string _perfLabel = "";

        private void StartPerf(string arg)
        {
            var parts = arg.Split(':');
            float.TryParse(parts[0], NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds);
            _perfLabel = parts.Length > 1 ? parts[1] : "perf";
            _perfFrames.Clear();
            _perfUntil = Time.realtimeSinceStartup + Mathf.Clamp(seconds, 1f, 60f);
            _waitUntil = _perfUntil + 0.2f;
        }

        private void SamplePerf()
        {
            if (_perfUntil < 0f) return;
            _perfFrames.Add(Time.unscaledDeltaTime * 1000f);
            if (Time.realtimeSinceStartup < _perfUntil) return;
            _perfUntil = -1f;
            var sorted = _perfFrames.OrderBy(x => x).ToArray();
            var avg = sorted.Length > 0 ? sorted.Average() : 0f;
            var p95 = sorted.Length > 0 ? sorted[(int)(sorted.Length * 0.95f)] : 0f;
            var max = sorted.Length > 0 ? sorted[sorted.Length - 1] : 0f;
            var renderers = UnityEngine.Object.FindObjectsOfType<Renderer>().Count(r => r != null && r.enabled && r.gameObject.activeInHierarchy);
            var lights = UnityEngine.Object.FindObjectsOfType<Light>().Count(l => l != null && l.enabled && l.gameObject.activeInHierarchy);
            var colliders = UnityEngine.Object.FindObjectsOfType<Collider>().Count(c => c != null && c.enabled);
            var bodies = UnityEngine.Object.FindObjectsOfType<Rigidbody>().Length;
            var actors = UnityEngine.Object.FindObjectsOfType<Identifiable>().Length;
            _events.Add(string.Format(CultureInfo.InvariantCulture,
                "perf[{0}]: {1} frames avg {2:F1} ms ({3:F0} fps) p95 {4:F1} ms max {5:F1} ms | renderers {6} lights {7} colliders {8} rigidbodies {9} actors {10} | managed {11:F0} MB",
                _perfLabel, sorted.Length, avg, avg > 0 ? 1000f / avg : 0f, p95, max, renderers, lights, colliders, bodies, actors, GC.GetTotalMemory(false) / 1048576.0));
        }

        /// <summary>LAB only: switch one cost driver off or on to see what it costs (extension, lights, shadows, flora).</summary>
        private string Toggle(string arg)
        {
            var parts = arg.Split(':');
            var on = parts.Length < 2 || parts[1] != "0";
            switch (parts[0])
            {
                case "extension": if (_builder?.Extension != null) _builder.Extension.SetActive(on); return "toggle extension " + on;
                case "lights":
                    foreach (var light in UnityEngine.Object.FindObjectsOfType<Light>()) if (light != null && light.type != LightType.Directional) light.enabled = on;
                    return "toggle point/spot lights " + on;
                case "shadows": QualitySettings.shadows = on ? ShadowQuality.All : ShadowQuality.Disable; return "toggle shadows " + on;
                case "flora":
                    if (_builder?.Extension == null) return "toggle flora: no extension";
                    var flora = _builder.Extension.transform.Find("flora");
                    if (flora != null) flora.gameObject.SetActive(on);
                    return "toggle flora " + on;
                case "face":
                {
                    // LAB readback: set one expression on every imported actor and report what its face renderer now shows
                    if (parts.Length < 2 || !int.TryParse(parts[1], out var expression)) return "toggle face: usage toggle:face:<expression id>";
                    var seen = 0; var report = "";
                    foreach (var applicator in UnityEngine.Object.FindObjectsOfType<SlimeAppearanceApplicator>())
                    {
                        if (string.IsNullOrEmpty(ImportedSpawner.ResolveImportedCanonical(applicator))) continue;
                        applicator.SetExpression((SlimeFace.SlimeExpression)expression);
                        if (seen++ > 0) continue;
                        var visual = ImportedSpawner.FindImportedVisual(applicator.GetComponentInParent<Identifiable>().gameObject);
                        var colors = visual != null ? visual.GetComponentsInChildren<ImportedPartColors>(true).FirstOrDefault(c => c.SourceFace) : null;
                        var renderer = colors != null ? colors.GetComponent<Renderer>() : null;
                        if (renderer != null)
                            foreach (var material in renderer.sharedMaterials)
                                if (material != null && material.HasProperty("_FaceAtlas")) report += " " + material.name + "=" + (material.GetTexture("_FaceAtlas") != null ? material.GetTexture("_FaceAtlas").name : "none");
                    }
                    return "face " + expression + " on " + seen + " imported actors:" + report;
                }
                case "matinfo":
                {
                    // LAB: shader and textures of every imported renderer's materials (first actor only)
                    var report = "";
                    foreach (var marker in UnityEngine.Object.FindObjectsOfType<ImportedVisualMarker>())
                    {
                        foreach (var renderer in marker.GetComponentsInChildren<Renderer>(true))
                            foreach (var material in renderer.sharedMaterials)
                            {
                                if (material == null) continue;
                                report += " | " + renderer.gameObject.name + ": " + material.name + " [" + (material.shader != null ? material.shader.name : "?") + "]";
                                foreach (var property in material.GetTexturePropertyNames())
                                {
                                    var texture = material.GetTexture(property);
                                    if (texture != null) report += " " + property + "=" + texture.name + "(" + texture.width + ")";
                                }
                            }
                        break;
                    }
                    return "matinfo" + report;
                }
                case "part":
                {
                    // LAB: switch the imported renderers whose name contains the text (toggle:part:tail:0)
                    if (parts.Length < 3) return "toggle part: usage toggle:part:<text>:<0|1>";
                    var count = 0; var names = "";
                    foreach (var marker in UnityEngine.Object.FindObjectsOfType<ImportedVisualMarker>())
                        foreach (var renderer in marker.GetComponentsInChildren<Renderer>(true))
                            if (renderer.gameObject.name.IndexOf(parts[1], StringComparison.OrdinalIgnoreCase) >= 0) { renderer.enabled = parts[2] != "0"; if (count++ < 4) names += " " + renderer.gameObject.name; }
                    return "toggle part " + parts[1] + " -> " + count + " renderers" + names;
                }
                case "imported":
                {
                    // the imported SR2 visuals' renderers only (what the GPU and the skinning cost)
                    var count = 0;
                    foreach (var marker in UnityEngine.Object.FindObjectsOfType<ImportedVisualMarker>())
                        foreach (var renderer in marker.GetComponentsInChildren<Renderer>(true)) { renderer.enabled = on; count++; }
                    return "toggle imported renderers " + on + " (" + count + ")";
                }
                case "behaviours":
                {
                    // the imported visuals' own scripts and animation (what the CPU costs)
                    var count = 0;
                    foreach (var marker in UnityEngine.Object.FindObjectsOfType<ImportedVisualMarker>())
                    {
                        foreach (var behaviour in marker.GetComponentsInChildren<Behaviour>(true))
                            if (behaviour != null && !(behaviour is ImportedVisualMarker) && !(behaviour is ImportedPartColors) && !(behaviour is SourceFaceResourceOwner)) { behaviour.enabled = on; count++; }
                    }
                    return "toggle imported behaviours " + on + " (" + count + ")";
                }
                default: return "toggle: unknown " + parts[0];
            }
        }

        /// <summary>LAB only: swap the portal membrane for another native material to judge how it looks.</summary>
        private string SetMembraneMaterial(string name)
        {
            if (_builder == null || _builder.Portal == null) return "mat: no portal";
            var material = _native.Clone(name);
            if (material == null) return "mat: native material not found: " + name;
            foreach (var renderer in _builder.Portal.Root.GetComponentsInChildren<MeshRenderer>())
                if (renderer.name.StartsWith("membrane", StringComparison.Ordinal)) renderer.sharedMaterial = material;
            return "mat: membrane now " + name;
        }

        /// <summary>Teleport the player, give physics a few seconds, then record where the player actually stands.</summary>
        private void TeleportAndCheck(string target)
        {
            if (!Teleport(target)) { _events.Add("tp_check: unknown target " + target); return; }
            PushFront(new[] { "wait:3", "tp_report:" + target });
        }

        private void ReportPlayer(string target)
        {
            var player = SceneContext.Instance.Player;
            var position = player.transform.position;
            var health = SceneContext.Instance.PlayerState.GetCurrHealth();
            var ground = _layout.DeckTopY;
            var grounded = position.y > ground - 1.5f && position.y < ground + 4f;
            _events.Add(string.Format(CultureInfo.InvariantCulture, "player at {0}: ({1:F1}, {2:F1}, {3:F1}) health {4} grounded-on-sanctuary {5}",
                target, position.x, position.y, position.z, health, grounded));
        }

        /// <summary>Render the scene from a free camera (no player, no gravity): the world root is switched on first so culling regions cannot hide it.</summary>
        private void FreeShot(string request)
        {
            var slash = request.IndexOf('/');
            var name = slash < 0 ? request : request.Substring(slash + 1);
            var tag = slash < 0 ? "" : request.Substring(0, slash) + "-";
            var shot = _layout.Shots.FirstOrDefault(x => x.Name == name);
            if (shot == null || _builder == null) { _events.Add("fshot unknown: " + name); return; }
            _builder.Root.SetActive(true);
            if (_builder.EnsureExtension()) WireExtension();
            if (_builder.Extension != null && !_builder.Extension.activeSelf) { _builder.Extension.SetActive(true); _builder.Reveal?.Skip(); }
            var main = Camera.main ?? UnityEngine.Object.FindObjectOfType<Camera>();
            if (main == null) { _events.Add("fshot: no camera to copy"); return; }
            var go = new GameObject("SlimeBridge shot camera");
            try
            {
                var cam = go.AddComponent<Camera>();
                cam.CopyFrom(main);
                cam.enabled = false;
                cam.farClipPlane = Mathf.Max(cam.farClipPlane, 600f);
                go.transform.position = new Vector3(shot.Position[0], shot.Position[1], shot.Position[2]);
                go.transform.rotation = Quaternion.LookRotation(new Vector3(shot.LookAt[0], shot.LookAt[1], shot.LookAt[2]) - go.transform.position, Vector3.up);
                var rt = new RenderTexture(1600, 900, 24, RenderTextureFormat.ARGB32);
                cam.targetTexture = rt;
                cam.Render();
                var previous = RenderTexture.active;
                RenderTexture.active = rt;
                var tex = new Texture2D(rt.width, rt.height, TextureFormat.RGB24, false);
                tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
                tex.Apply();
                RenderTexture.active = previous;
                cam.targetTexture = null;
                var folder = Path.Combine(_outDirectory!, "shots");
                Directory.CreateDirectory(folder);
                var file = Path.Combine(folder, "free-" + tag + name.Replace('.', '-') + ".png");
                File.WriteAllBytes(file, tex.EncodeToPNG());
                _shots.Add(file);
                UnityEngine.Object.Destroy(tex);
                rt.Release();
                UnityEngine.Object.Destroy(rt);
                _events.Add("free shot saved: " + name);
            }
            catch (Exception ex) { _events.Add("fshot failed " + name + ": " + ex.GetType().Name + " " + ex.Message); }
            finally { UnityEngine.Object.Destroy(go); }
        }

        /// <summary>LAB: one close-up picture of every live imported actor (slimes and Largos), taken from the player's side. `tag[:distance]`.</summary>
        private void ActorShots(string request)
        {
            var parts = request.Split(':');
            var tag = parts[0].Length == 0 ? "actors" : parts[0];
            var distance = 3.2f;
            if (parts.Length > 1) float.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out distance);
            var main = Camera.main ?? UnityEngine.Object.FindObjectOfType<Camera>();
            if (main == null || _outDirectory == null) { _events.Add("actors: no camera or output"); return; }
            var folder = Path.Combine(_outDirectory, "shots");
            Directory.CreateDirectory(folder);
            var count = 0;
            foreach (var applicator in UnityEngine.Object.FindObjectsOfType<SlimeAppearanceApplicator>())
            {
                var canonical = ImportedSpawner.ResolveImportedCanonical(applicator);
                var identifiable = applicator != null ? applicator.GetComponentInParent<Identifiable>() : null;
                if (string.IsNullOrEmpty(canonical) || identifiable == null) continue;
                var bounds = new Bounds(identifiable.transform.position + Vector3.up * 0.5f, Vector3.one * 0.5f);
                foreach (var renderer in identifiable.GetComponentsInChildren<Renderer>(false))
                    if (renderer != null && renderer.enabled && renderer.GetType().Name != "ParticleSystemRenderer") bounds.Encapsulate(renderer.bounds);
                var size = Mathf.Max(0.8f, bounds.extents.magnitude);
                var target = bounds.center;
                var toCamera = main.transform.position - target; toCamera.y = 0f;
                if (toCamera.sqrMagnitude < 0.01f) toCamera = Vector3.back;
                var from = target + toCamera.normalized * size * distance * 0.85f + Vector3.up * size * 0.45f;
                var go = new GameObject("SlimeBridge actor camera");
                try
                {
                    var cam = go.AddComponent<Camera>();
                    cam.CopyFrom(main);
                    cam.enabled = false;
                    go.transform.position = from;
                    go.transform.rotation = Quaternion.LookRotation(target - from, Vector3.up);
                    var rt = new RenderTexture(1000, 800, 24, RenderTextureFormat.ARGB32);
                    cam.targetTexture = rt;
                    cam.Render();
                    var previous = RenderTexture.active;
                    RenderTexture.active = rt;
                    var tex = new Texture2D(rt.width, rt.height, TextureFormat.RGB24, false);
                    tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
                    tex.Apply();
                    RenderTexture.active = previous;
                    cam.targetTexture = null;
                    try
                    {
                        var faceReport = "";
                        foreach (var fr in identifiable.GetComponentsInChildren<Renderer>(false))
                            foreach (var fm in fr.sharedMaterials)
                                if (fm != null && fm.shader != null && (fm.shader.name ?? "").Contains("Face/") && fm.HasProperty("_FaceAtlas"))
                                {
                                    var atlas = fm.GetTexture("_FaceAtlas");
                                    faceReport += " [" + (fr.GetComponentInParent<ImportedVisualMarker>() != null ? "IMPORTED " : "native ") + (fr.enabled ? "on " : "off ") + fr.gameObject.name + "/" + fm.name + " atlas=" + (atlas != null ? atlas.name : "none") + " st=" + fm.GetTextureScale("_FaceAtlas") + "/" + fm.GetTextureOffset("_FaceAtlas") +
                                        (fm.HasProperty("_ScaleAmplitude") ? " amp=" + fm.GetFloat("_ScaleAmplitude") : "") + "]";
                                }
                        _events.Add("face " + tag + ":" + faceReport);
                    }
                    catch (Exception) { }
                    var file = Path.Combine(folder, "actor-" + tag + "-" + (++count).ToString("D2") + "-" + canonical.Replace('.', '-') + ".png");
                    File.WriteAllBytes(file, tex.EncodeToPNG());
                    _shots.Add(file);
                    UnityEngine.Object.Destroy(tex);
                    rt.Release();
                    UnityEngine.Object.Destroy(rt);
                }
                catch (Exception ex) { _events.Add("actor shot failed " + canonical + ": " + ex.GetType().Name + " " + ex.Message); }
                finally { UnityEngine.Object.Destroy(go); }
            }
            _events.Add("actor shots: " + count);
            ReportOrientation();
        }

        /// <summary>LAB: for every slime (native and imported) where its face points relative to the actor's forward: +1 faces forward, -1 faces backward.</summary>
        private void ReportOrientation()
        {
            foreach (var applicator in UnityEngine.Object.FindObjectsOfType<SlimeAppearanceApplicator>())
            {
                try
                {
                    var identifiable = applicator != null ? applicator.GetComponentInParent<Identifiable>() : null;
                    if (identifiable == null) continue;
                    var canonical = ImportedSpawner.ResolveImportedCanonical(applicator);
                    Renderer face = null;
                    var all = new Bounds(identifiable.transform.position, Vector3.zero);
                    var first = true;
                    foreach (var renderer in identifiable.GetComponentsInChildren<Renderer>(false))
                    {
                        if (renderer == null || !renderer.enabled) continue;
                        if (renderer.GetType().Name == "ParticleSystemRenderer") continue;
                        if (first) { all = renderer.bounds; first = false; } else all.Encapsulate(renderer.bounds);
                        foreach (var material in renderer.sharedMaterials)
                            if (material != null && material.shader != null && (material.shader.name ?? "").Contains("Face")) face = renderer;
                    }
                    if (face == null) continue;
                    var direction = face.bounds.center - all.center; direction.y = 0f;
                    var forward = identifiable.transform.forward; forward.y = 0f;
                    var dot = direction.sqrMagnitude < 1e-6f ? 0f : Vector3.Dot(direction.normalized, forward.normalized);
                    _events.Add("orient " + (string.IsNullOrEmpty(canonical) ? "native " + identifiable.id : canonical) + " dot=" + dot.ToString("F2", CultureInfo.InvariantCulture) +
                        " faceOffset=" + direction.magnitude.ToString("F2", CultureInfo.InvariantCulture));
                }
                catch (Exception) { }
            }
        }

        private void PushFront(IEnumerable<string> ops)
        {
            var list = ops.ToList();
            for (var i = list.Count - 1; i >= 0; i--) _ops.AddFirst(list[i]);
        }

        private Vector3 FramePoint(float u, float v, float y)
        {
            var h = _layout.HeadingY * Mathf.Deg2Rad;
            var forward = new Vector3(Mathf.Sin(h), 0f, Mathf.Cos(h));
            var right = new Vector3(Mathf.Cos(h), 0f, -Mathf.Sin(h));
            return new Vector3(_layout.FrameOrigin[0], y, _layout.FrameOrigin[2]) + forward * u + right * v;
        }

        /// <summary>Spawn the statue's plort natively just above its catch trigger, exactly as a thrown plort would arrive.</summary>
        private void SpawnPlort(string slime)
        {
            var key = slime.ToUpperInvariant();
            int plortId; Vector3 position;
            var spec = _layout.FindStatue(key);
            if (key == "GOLD" && _layout.Gate.Enabled)
            {
                plortId = _layout.Gate.PlortId;
                position = new Vector3(_layout.Gate.Position[0], _layout.Gate.Position[1] + 2.2f, _layout.Gate.Position[2]) + Quaternion.Euler(0f, _layout.Gate.Yaw, 0f) * new Vector3(0f, 0f, 1.5f);
            }
            else if (spec != null)
            {
                plortId = spec.PlortId;
                position = new Vector3(spec.Position[0], spec.Position[1] + 2.2f, spec.Position[2]) + Quaternion.Euler(0f, spec.Yaw, 0f) * new Vector3(0f, 0f, 1.5f);
            }
            else { _events.Add("plort: unknown statue " + key); return; }
            var game = GameContext.Instance;
            var prefab = game.LookupDirector.GetPrefab((Identifiable.Id)plortId);
            if (prefab == null) { _events.Add("plort: no native prefab for " + key); return; }
            SRBehaviour.InstantiateActor(prefab, SceneContext.Instance.RegionRegistry.GetCurrentRegionSetId(), position, Quaternion.identity);
            _events.Add("plort spawned for " + key);
        }

        private void StartShot(string name)
        {
            if (!Teleport(name)) { _events.Add("shot target unknown: " + name); return; }
            _pendingShot = name;
            _shotStage = 0;
            _waitUntil = Time.realtimeSinceStartup + 3.0f;     // let streaming, LOD and the camera settle
        }

        private void FinishShot()
        {
            var name = _pendingShot!;
            if (_shotStage == 0)
            {
                var folder = Path.Combine(_outDirectory!, "shots");
                Directory.CreateDirectory(folder);
                var file = Path.Combine(folder, name.Replace('.', '-') + ".png");
                ScreenCapture.CaptureScreenshot(file, 1);
                _shots.Add(file);
                _shotStage = 1;
                _waitUntil = Time.realtimeSinceStartup + 1.0f;
                return;
            }
            _pendingShot = null;
            _events.Add("shot saved: " + name);
        }

        private void WriteReceipt()
        {
            if (_outDirectory == null) return;
            _receiptWritten = true;
            try
            {
                Directory.CreateDirectory(_outDirectory);
                var sb = new StringBuilder("{\"schema\":\"bridge-sanctuary-receipt\",\"schema_version\":1");
                sb.Append(",\"save_id\":\"").Append(BridgeEntry.JsonEscape(_saveId)).Append('"');
                sb.Append(",\"built\":").Append(_builder != null ? "true" : "false");
                sb.Append(",\"failed\":").Append(_failed ? "true" : "false");
                sb.Append(",\"status\":\"").Append(BridgeEntry.JsonEscape(_builder != null ? Status() : "not built")).Append('"');
                sb.Append(",\"missing\":[").Append(string.Join(",", _native.Missing.Distinct().Select(m => "\"" + BridgeEntry.JsonEscape(m) + "\""))).Append(']');
                sb.Append(",\"shots\":[").Append(string.Join(",", _shots.Select(m => "\"" + BridgeEntry.JsonEscape(m) + "\""))).Append(']');
                sb.Append(",\"events\":[").Append(string.Join(",", _events.Select(m => "\"" + BridgeEntry.JsonEscape(m) + "\""))).Append("]}");
                File.WriteAllText(Path.Combine(_outDirectory, "sanctuary-receipt.json"), sb.ToString(), new UTF8Encoding(false));
                Debug.Log("[SlimeBridge] sanctuary receipt written");
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] sanctuary receipt failed: " + ex.Message); }
        }

        internal void Unload()
        {
            try { _builder?.Destroy(); } catch (Exception) { }
            Instance = null;
        }
    }

    /// <summary>In-game console command: <c>sanctuary status</c> for everyone, dev verbs only when dev mode is on.</summary>
    internal sealed class SanctuaryCommand : SRML.Console.ConsoleCommand
    {
        public override string ID => "sanctuary";
        public override string Usage => "sanctuary <status|unlock|reset|gold [n]|fill <SLIME>|tp [target]>";
        public override string Description => "SlimeBridge sanctuary status and dev tools";

        public override bool Execute(string[] args)
        {
            var host = SanctuaryHost.Instance;
            if (host == null) { SRML.Console.Console.LogError("[SlimeBridge] the sanctuary is not installed (no layout)."); return false; }
            if (args == null || args.Length == 0 || args[0].Equals("status", StringComparison.OrdinalIgnoreCase))
            {
                SRML.Console.Console.Log("[SlimeBridge] " + host.Status());
                return true;
            }
            var message = host.DevCommand(args);
            SRML.Console.Console.Log("[SlimeBridge] " + message);
            return true;
        }
    }
}
