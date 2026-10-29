using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// The return portal in Slime Rancher 2: a pad of ancient bricks with a column of light (cloned from the game's own labyrinth
    /// portal leaf objects), placed once per save on flat free ground near where the player lives and remembered in a Bridge
    /// sidecar. Stepping into the light takes the player to Slime Rancher through the launcher; the price (default 250 000
    /// newbucks, paid once per save, 0 or disabled = free) and the whole portal can be switched off in the launcher.
    /// Nothing in the native save is edited.
    /// </summary>
    internal sealed class Sr2ReturnPortal
    {
        internal const string PortalId = "portal.sr2.return_portal";
        private const float PadRadius = 2.3f, HintRadius = 14f, SettleSeconds = 5f;
        private static readonly string[] NativeParts = { "labyPortal_bricksOuter_01_trim", "labyPortal_bricksSunken_01", "PortalLightPillar_Short" };

        private readonly Sr2TravelRealm? _travel;          // null when SR2 was not started by the launcher
        private readonly TravelOverlay _ownOverlay = new TravelOverlay();
        private TravelOverlay Overlay => _travel != null ? _travel.Overlay : _ownOverlay;
        internal void Gui() { if (_travel == null) _ownOverlay.Draw(); }
        private readonly string _stateDir;
        private readonly int _cost;
        private readonly string? _labOut;
        private GameObject? _root;
        private Vector3 _anchor;
        private float _yaw;
        private bool _hasAnchor, _paid, _inside;
        private string _saveId = "";
        private float _readySince = -1f, _lastCross = -100f, _nextHint;
        private readonly List<string> _events = new List<string>();
        private void Note(string text)
        {
            if (_events.Count >= 80) return;
            _events.Add(text);
            MelonLoader.MelonLogger.Msg("return portal: " + text);
        }

        private float _retryAt;
        private int _labStage;
        private float _labWait;

        internal static Sr2ReturnPortal? Instance { get; private set; }

        private Sr2ReturnPortal(Sr2TravelRealm? travel, string stateDir, int cost, string? labOut)
        {
            _travel = travel; _stateDir = stateDir; _cost = cost; _labOut = labOut;
        }

        internal static Sr2ReturnPortal? TryCreate(Sr2TravelRealm? travel, bool labExecutable)
        {
            try
            {
                // the portal exists in every game with the plugin; without the launcher it explains how to travel
                if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR2_PORTAL") == "0") return null;
                var cost = 250000;
                if (int.TryParse(Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR2_GATE_COST"), NumberStyles.Integer, CultureInfo.InvariantCulture, out var configured))
                    cost = Math.Max(0, Math.Min(configured, 1_000_000_000));
                string? labOut = null;
                var stateDir = Path.Combine(Application.persistentDataPath, "SlimeBridge");
                if (labExecutable)
                {
                    var root = LabSpecPathGuard.FindRepoRoot();
                    var requested = Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR2_PORTAL_LAB_OUT");
                    if (root != null && !string.IsNullOrEmpty(requested) && Directory.Exists(requested) &&
                        LabSpecPathGuard.IsUnderRoot(Path.Combine(root, LabSpecPathGuard.OutRelativeRoot), requested!, out _))
                    {
                        labOut = requested; stateDir = Path.Combine(requested!, "state");
                        if (int.TryParse(Environment.GetEnvironmentVariable("SLIMEBRIDGE_SR2_PORTAL_LAB_COST"), NumberStyles.Integer, CultureInfo.InvariantCulture, out var labCost)) cost = labCost;
                    }
                }
                var portal = new Sr2ReturnPortal(travel, stateDir, cost, labOut);
                Instance = portal;
                Sr2ReturnPortalHook.PlacePlayer = portal.PlacePlayer;
                MelonLoader.MelonLogger.Msg("return portal armed (price " + cost + (labOut != null ? ", LAB" : "") + ")");
                return portal;
            }
            catch (Exception ex) { MelonLoader.MelonLogger.Msg("return portal disabled: " + ex.GetType().Name + ": " + ex.Message); return null; }
        }

        // ----- world access
        private static bool WorldReady()
        {
            try
            {
                var game = Il2Cpp.GameContext.Instance; var scene = Il2Cpp.SceneContext.Instance;
                return game != null && scene != null && scene.Player != null && scene.PlayerState != null && game.AutoSaveDirector != null && game.AutoSaveDirector.CurrentGame() != null;
            }
            catch (Exception) { return false; }
        }

        private static string CurrentSave()
        {
            try { return Il2Cpp.GameContext.Instance.AutoSaveDirector.CurrentGameName() ?? ""; } catch (Exception) { return ""; }
        }

        private static Transform PlayerTransform() => Il2Cpp.SceneContext.Instance.Player.transform;

        // ----- sidecar (per save: paid flag + anchor)
        private string StateFile => Path.Combine(_stateDir, "sr2-portal-progress.json");

        private Dictionary<string, object?> ReadSaves()
        {
            try
            {
                if (File.Exists(StateFile) && MiniJson.Parse(File.ReadAllText(StateFile, Encoding.UTF8)) is Dictionary<string, object?> root &&
                    root.TryGetValue("saves", out var saves) && saves is Dictionary<string, object?> map) return map;
            }
            catch (Exception) { /* unreadable sidecar: start fresh, never touch the game's save */ }
            return new Dictionary<string, object?>();
        }

        private void LoadState()
        {
            _hasAnchor = false; _paid = false;
            if (!ReadSaves().TryGetValue(_saveId, out var raw) || !(raw is Dictionary<string, object?> entry)) return;
            _paid = entry.TryGetValue("paid", out var paid) && paid is bool b && b;
            if (entry.TryGetValue("anchor", out var a) && a is List<object?> list && list.Count == 3)
            {
                _anchor = new Vector3(F(list[0]), F(list[1]), F(list[2]));
                _yaw = entry.TryGetValue("yaw", out var y) ? F(y) : 0f;
                _hasAnchor = true;
            }
        }

        private static float F(object? v) => v is double d ? (float)d : v is long l ? l : 0f;

        private void SaveState()
        {
            try
            {
                var saves = ReadSaves();
                var entry = new Dictionary<string, object?> { ["paid"] = _paid };
                if (_hasAnchor)
                {
                    entry["anchor"] = new List<object?> { (double)_anchor.x, (double)_anchor.y, (double)_anchor.z };
                    entry["yaw"] = (double)_yaw;
                }
                saves[_saveId] = entry;
                var sb = new StringBuilder("{\"schema\":\"bridge-sr2-portal-progress\",\"schema_version\":1,\"saves\":{");
                var first = true;
                foreach (var save in saves)
                {
                    if (!(save.Value is Dictionary<string, object?> e)) continue;
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append('"').Append(BridgeJson.Escape(save.Key)).Append("\":{\"paid\":").Append(e.TryGetValue("paid", out var p) && p is bool pb && pb ? "true" : "false");
                    if (e.TryGetValue("anchor", out var an) && an is List<object?> al && al.Count == 3)
                        sb.Append(",\"anchor\":[").Append(F(al[0]).ToString("0.###", CultureInfo.InvariantCulture)).Append(',').Append(F(al[1]).ToString("0.###", CultureInfo.InvariantCulture)).Append(',')
                          .Append(F(al[2]).ToString("0.###", CultureInfo.InvariantCulture)).Append("],\"yaw\":").Append((e.TryGetValue("yaw", out var yv) ? F(yv) : 0f).ToString("0.###", CultureInfo.InvariantCulture));
                    sb.Append('}');
                }
                sb.Append("}}");
                Directory.CreateDirectory(_stateDir);
                var temp = StateFile + ".tmp";
                File.WriteAllText(temp, sb.ToString(), new UTF8Encoding(false));
                if (File.Exists(StateFile)) File.Delete(StateFile);
                File.Move(temp, StateFile);
            }
            catch (Exception ex) { Note("state save failed: " + ex.GetType().Name); MelonLoader.MelonLogger.Msg("portal state not saved: " + ex.Message); }
        }

        // ----- placement: flat, free ground near the player the first time; remembered afterwards
        /// <summary>Looks for room for the pad around <paramref name="around"/>: strict (flat, free air), then relaxed, then simply the ground in front of the player.</summary>
        private bool FindSpot(Vector3 around, out Vector3 spot, out float yaw)
        {
            spot = around; yaw = 0f;
            var mask = Physics.DefaultRaycastLayers;
            // normal.y, max height difference, flatness tolerance, clearance radius, radii to try
            var levels = new[]
            {
                new { Name = "strict", Normal = 0.985f, Dy = 1.5f, Flat = 0.4f, Clear = 3.0f, Low = 0.8f, High = 4.2f, Radii = new[] { 9f, 12f, 16f, 21f, 28f, 36f, 46f } },
                new { Name = "relaxed", Normal = 0.95f, Dy = 2.5f, Flat = 0.8f, Clear = 2.4f, Low = 0.9f, High = 3.4f, Radii = new[] { 6f, 8f, 11f, 15f, 20f, 27f, 36f, 48f, 62f } },
                new { Name = "loose", Normal = 0.9f, Dy = 3f, Flat = 1.6f, Clear = 1.3f, Low = 1.3f, High = 3.0f, Radii = new[] { 4f, 6f, 8f, 11f, 15f, 20f, 27f, 36f } },
            };
            foreach (var level in levels)
            {
                var best = float.MaxValue; var found = false; var grounded = 0; int badNormal = 0, badHeight = 0, blocked = 0, uneven = 0;
                foreach (var radius in level.Radii)
                    for (var k = 0; k < 24; k++)
                    {
                        var angle = k * 15f * Mathf.Deg2Rad;
                        var probe = around + new Vector3(Mathf.Sin(angle), 0f, Mathf.Cos(angle)) * radius;
                        // Start just above the player's own floor level: a start far above would hit ceilings and roofs first.
                        if (!Physics.Raycast(new Vector3(probe.x, around.y + 2.5f, probe.z), Vector3.down, out var hit, 9f, mask, QueryTriggerInteraction.Ignore)) continue;
                        grounded++;
                        if (hit.normal.y < level.Normal) { badNormal++; continue; }
                        if (Mathf.Abs(hit.point.y - around.y) > level.Dy) { badHeight++; continue; }
                        // The pad and the column of light need free air above the ground, and the ground must be flat all across the pad.
                        if (Physics.CheckCapsule(hit.point + Vector3.up * level.Low, hit.point + Vector3.up * level.High, level.Clear, mask, QueryTriggerInteraction.Ignore)) { blocked++; continue; }
                        var flat = true;
                        foreach (var edge in new[] { Vector3.forward, Vector3.back, Vector3.left, Vector3.right })
                        {
                            var at = hit.point + edge * 3.4f;
                            if (!Physics.Raycast(at + Vector3.up * 1.5f, Vector3.down, out var h2, 5f, mask, QueryTriggerInteraction.Ignore) || Mathf.Abs(h2.point.y - hit.point.y) > level.Flat) { flat = false; break; }
                        }
                        if (!flat) { uneven++; continue; }
                        var score = radius + k * 0.01f;
                        if (score < best) { best = score; spot = hit.point; found = true; }
                    }
                Note("placement " + level.Name + ": " + (found ? "found" : "nothing") + " (" + grounded + " ground probes; slope " + badNormal + ", height " + badHeight + ", blocked " + blocked + ", uneven " + uneven + ")");
                if (found) { yaw = Mathf.Atan2(around.x - spot.x, around.z - spot.z) * Mathf.Rad2Deg; return true; }
            }
            // Last resort: the ground a few steps in front of the player, whatever stands around it.
            var ahead = PlayerTransform().position + PlayerTransform().forward * 5f;
            if (Physics.Raycast(ahead + Vector3.up * 2.5f, Vector3.down, out var last, 9f, mask, QueryTriggerInteraction.Ignore))
            {
                spot = last.point; yaw = Mathf.Atan2(around.x - spot.x, around.z - spot.z) * Mathf.Rad2Deg;
                Note("placement fallback: in front of the player");
                return true;
            }
            return false;
        }

        private bool EnsureAnchor()
        {
            if (_hasAnchor) return true;
            var origin = PlayerTransform().position;
            if (!FindSpot(origin, out var spot, out var yaw))
            {
                Note("no flat free ground near the player: portal not built (retrying)");
                _retryAt = Time.realtimeSinceStartup + 10f;
                return false;
            }
            _anchor = spot; _yaw = yaw; _hasAnchor = true;
            SaveState();
            Note(string.Format(CultureInfo.InvariantCulture, "portal anchored at ({0:F1}, {1:F1}, {2:F1})", spot.x, spot.y, spot.z));
            return true;
        }

        // ----- build (clones of the game's own labyrinth-portal leaf objects; materials are shared, nothing native is edited)
        /// <summary>One pass over the loaded renderers for every wanted part (each pass walks all MeshRenderers of the game: three passes were a visible hitch).</summary>
        private static Dictionary<string, GameObject> NativeAll(string[] names)
        {
            var wanted = new HashSet<string>(names, StringComparer.Ordinal);
            var found = new Dictionary<string, GameObject>(StringComparer.Ordinal);
            foreach (var renderer in Resources.FindObjectsOfTypeAll<MeshRenderer>())
            {
                if (renderer == null) continue;
                var name = renderer.name;
                if (!wanted.Contains(name) || found.ContainsKey(name)) continue;
                var go = renderer.gameObject;
                if (go.scene.IsValid() && go.scene.isLoaded) { found[name] = go; if (found.Count == wanted.Count) break; }
            }
            return found;
        }

        private void Build()
        {
            _root = new GameObject("SlimeBridge_SR2_PortalRoot");
            _root.transform.position = _anchor;
            _root.transform.rotation = Quaternion.Euler(0f, _yaw, 0f);
            var built = 0;
            var natives = NativeAll(NativeParts);
            foreach (var name in NativeParts)
            {
                natives.TryGetValue(name, out var source);
                if (source == null) { Note("native part missing: " + name); continue; }
                var clone = UnityEngine.Object.Instantiate(source, _root.transform);
                clone.name = "portal-" + name;
                clone.transform.localPosition = new Vector3(0f, name.Contains("trim") ? 0.04f : name.Contains("Sunken") ? 0.06f : 0f, 0f);
                clone.transform.localRotation = Quaternion.identity;
                clone.transform.localScale = name.Contains("Pillar") ? new Vector3(0.45f, 0.5f, 0.45f) : Vector3.one * 0.3f;
                clone.SetActive(true);
                foreach (var behaviour in clone.GetComponentsInChildren<MonoBehaviour>()) if (behaviour != null) behaviour.enabled = false;   // appearance only
                built++;
            }
            Note("portal built from " + built + " native parts");
            MelonLoader.MelonLogger.Msg("return portal built (" + built + "/" + NativeParts.Length + " native parts) at " + _anchor);
        }

        // ----- per frame
        private float _nextTick;

        internal void Tick()
        {
            // The portal only reacts to where the player stands: ten checks a second are plenty, and every check crosses into the game
            // (save name string, player transform, currency). LAB scripts keep their per-frame cadence.
            if (_labOut == null)
            {
                var now = Time.realtimeSinceStartup;
                if (now < _nextTick) return;
                _nextTick = now + 0.1f;
            }
            try
            {
                if (!WorldReady())
                {
                    _readySince = -1f; _inside = false;
                    if (_root != null) { UnityEngine.Object.Destroy(_root); _root = null; }
                    _saveId = "";
                    return;
                }
                if (_readySince < 0f) _readySince = Time.realtimeSinceStartup;
                if (Time.realtimeSinceStartup - _readySince < SettleSeconds) return;
                var save = CurrentSave();
                if (save.Length == 0) return;
                if (save != _saveId)
                {
                    _saveId = save; LoadState();
                    if (_root != null) { UnityEngine.Object.Destroy(_root); _root = null; }
                }
                if (_root == null)
                {
                    if (Time.realtimeSinceStartup < _retryAt || !EnsureAnchor()) return;
                    Build();
                }
                Interact();
                if (_labOut != null) LabScript();
            }
            catch (Exception ex)
            {
                Note("tick failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private void Interact()
        {
            if (_travel != null && _travel.Busy) return;
            var player = PlayerTransform().position;
            var flat = new Vector3(player.x - _anchor.x, 0f, player.z - _anchor.z).magnitude;
            var near = flat < HintRadius && Mathf.Abs(player.y - _anchor.y) < 8f;
            var onPad = flat < PadRadius && Mathf.Abs(player.y - _anchor.y) < 3.2f;
            var french = Application.systemLanguage == SystemLanguage.French;
            if (near && !onPad && Time.realtimeSinceStartup >= _nextHint)
            {
                _nextHint = Time.realtimeSinceStartup + 3f;
                var money = Il2Cpp.SceneContext.Instance.PlayerState.Newbucks();
                var gate = _cost > 0 && !_paid;
                Overlay.ShowHint(gate
                    ? (french ? $"Portail vers Slime Rancher : {_cost:N0} newbucks pour l’ouvrir (tu as {money:N0})" : $"Portal to Slime Rancher: {_cost:N0} newbucks to open it (you have {money:N0})")
                    : (french ? "Entre dans la lumière pour retourner dans Slime Rancher" : "Step into the light to return to Slime Rancher"), 3.4f);
            }
            if (!onPad) { _inside = false; return; }
            if (_inside || Time.realtimeSinceStartup - _lastCross < 8f) return;
            _inside = true;
            if (_cost > 0 && !_paid)
            {
                var state = Il2Cpp.SceneContext.Instance.PlayerState;
                var before = state.Newbucks();
                if (before < _cost)
                {
                    Overlay.ShowHint(french ? $"Il te manque {_cost - before:N0} newbucks pour ouvrir le portail" : $"You need {_cost - before:N0} more newbucks to open the portal", 4f);
                    return;
                }
                if (!state.SpendNewbucks(_cost) || state.Newbucks() >= before) { Note("payment refused by the game"); return; }
                _paid = true; SaveState();
                Note("portal paid: " + _cost);
            }
            _lastCross = Time.realtimeSinceStartup;
            if (_travel == null)
            {
                Note("portal entered without the launcher");
                Overlay.ShowHint(TravelText.Failure("NO_LAUNCHER", "SR1"), 6f);
                return;
            }
            Note("portal entered: crossing requested");
            Overlay.ShowHint(TravelText.Opening("SR1"), 4f);                 // instant feedback; the launcher answers within a second
            _travel.RequestCrossing(PortalId, _saveId);
        }

        // ----- arrival from the other game: come out of the light, facing away from the pad
        internal bool PlacePlayer()
        {
            try
            {
                if (!WorldReady()) return false;
                _saveId = CurrentSave();
                if (_saveId.Length == 0) return false;
                if (!_hasAnchor) LoadState();
                if (!EnsureAnchor()) return false;
                var forward = Quaternion.Euler(0f, _yaw, 0f) * Vector3.forward;      // the pad faces the player's home: come out on that side
                var position = _anchor + forward * 4.2f + Vector3.up * 0.3f;
                var sceneGroup = Il2Cpp.SystemContext.Instance?.SceneLoader?._currentSceneGroup;
                var teleport = Il2Cpp.SceneContext.Instance.Player.GetComponent<Il2Cpp.TeleportablePlayer>();
                if (teleport == null || sceneGroup == null) return false;
                teleport.TeleportTo(position, sceneGroup, new Il2CppSystem.Nullable<Vector3>(new Vector3(0f, _yaw, 0f)), false);
                _lastCross = Time.realtimeSinceStartup;
                // the player came through this portal: it is open for this save (never stranded behind the newbucks price)
                if (!_paid) { _paid = true; SaveState(); }
                Note("player emerged from the portal");
                return true;
            }
            catch (Exception ex) { Note("arrival placement failed: " + ex.GetType().Name + ": " + ex.Message); return false; }
        }

        // ----- LAB-only script: look at the portal, check the gate, write a receipt
        private void LabScript()
        {
            if (Time.realtimeSinceStartup < _labWait) return;
            var player = Il2Cpp.SceneContext.Instance.Player;
            var teleport = player.GetComponent<Il2Cpp.TeleportablePlayer>();
            var sceneGroup = Il2Cpp.SystemContext.Instance?.SceneLoader?._currentSceneGroup;
            if (teleport == null || sceneGroup == null) return;
            Vector3 At(float distance, float side) => _anchor + Quaternion.Euler(0f, _yaw, 0f) * new Vector3(side, 0f, distance) + Vector3.up * 0.3f;
            switch (_labStage)
            {
                case 0:      // far view
                    teleport.TeleportTo(At(4.6f, 0f), sceneGroup, new Il2CppSystem.Nullable<Vector3>(new Vector3(8f, _yaw + 180f, 0f)), false);
                    _labWait = Time.realtimeSinceStartup + 4f; _labStage = 1; break;
                case 1: Shot("sr2-portal-far.png"); _labWait = Time.realtimeSinceStartup + 2.5f; _labStage = 2; break;
                case 2:      // close view
                    teleport.TeleportTo(At(3.0f, 0f), sceneGroup, new Il2CppSystem.Nullable<Vector3>(new Vector3(6f, _yaw + 180f, 0f)), false);
                    _labWait = Time.realtimeSinceStartup + 4f; _labStage = 3; break;
                case 3: Shot("sr2-portal-close.png"); _labWait = Time.realtimeSinceStartup + 2.5f; _labStage = 4; break;
                case 4: WriteReceipt(); _labStage = 5; break;
            }
        }

        private void Shot(string name)
        {
            try { ScreenCapture.CaptureScreenshot(Path.Combine(_labOut!, name), 1); Note("shot " + name); }
            catch (Exception ex) { Note("shot failed: " + ex.GetType().Name); }
        }

        private void WriteReceipt()
        {
            var money = 0;
            try { money = Il2Cpp.SceneContext.Instance.PlayerState.Newbucks(); } catch (Exception) { }
            var sb = new StringBuilder("{\"schema\":\"bridge-sr2-portal-receipt\",\"schema_version\":1");
            sb.Append(",\"save\":\"").Append(BridgeJson.Escape(_saveId)).Append("\",\"cost\":").Append(_cost).Append(",\"paid\":").Append(_paid ? "true" : "false").Append(",\"currency\":").Append(money);
            sb.Append(",\"anchor\":[").Append(_anchor.x.ToString("0.###", CultureInfo.InvariantCulture)).Append(',').Append(_anchor.y.ToString("0.###", CultureInfo.InvariantCulture)).Append(',').Append(_anchor.z.ToString("0.###", CultureInfo.InvariantCulture)).Append(']');
            sb.Append(",\"events\":[");
            for (var i = 0; i < _events.Count; i++) sb.Append(i > 0 ? "," : "").Append('"').Append(BridgeJson.Escape(_events[i])).Append('"');
            sb.Append("]}");
            File.WriteAllText(Path.Combine(_labOut!, "sr2-portal-receipt.json"), sb.ToString(), new UTF8Encoding(false));
        }
    }
}
