using System;
using System.Collections.Generic;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>IMGUI host for the shared transition overlay (a plain MonoBehaviour: SRML mods have no OnGUI of their own).</summary>
    internal sealed class TravelGui : MonoBehaviour
    {
        internal static TravelOverlay? Overlay;
        private static GameObject? _host;
        /// <summary>Set by the travel realm: the player's world is saved when the game closes (window closed, Alt+F4).</summary>
        internal static Action? QuitHook;
        public void OnGUI() { Overlay?.Draw(); }
        public void OnApplicationQuit() { try { QuitHook?.Invoke(); } catch (Exception) { /* closing anyway */ } }

        /// <summary>Shows pending player messages; works without travel too (a game started outside SlimeBridge).</summary>
        internal static void Pump()
        {
            var text = TravelNotice.Take();
            if (text == null) return;
            if (Overlay == null) Overlay = new TravelOverlay();
            if (_host == null)
            {
                _host = new GameObject("SlimeBridge_TravelGui");
                UnityEngine.Object.DontDestroyOnLoad(_host);
                _host.AddComponent<TravelGui>();
            }
            Overlay.ShowHint(text, 6f);
        }

        internal static void Adopt(TravelOverlay overlay, GameObject host) { Overlay = overlay; _host = host; }
    }

    /// <summary>
    /// SR1's half of a crossing: the portal animation, the player parked in front of the portal while the other game has the
    /// player (health kept full, input locked, simulation frozen in single player), and the emergence from the portal on return.
    /// </summary>
    internal sealed class Sr1TravelRealm : ITravelRealm
    {
        private readonly TravelOverlay _overlay = new TravelOverlay();
        private readonly TravelThrottle _throttle = new TravelThrottle();
        private readonly TravelAgent _agent;
        private readonly bool _destinationMode;
        private bool _held, _arrived, _inputLocked;
        private float _nextHeal;
        private double _heldWorldTime = -1, _heldRealTime;
        private GameObject? _gui;
        private TravelDone? _pendingDone;
        private float _quitAt = -1f;
        private string? _quitSave;
        private static readonly int OwnPid = System.Diagnostics.Process.GetCurrentProcess().Id;

        public bool Held => _held || _throttle.Active;

        private Sr1TravelRealm(string url, string token, bool destinationMode)
        {
            _destinationMode = destinationMode;
            if (destinationMode) _throttle.MuteAudio();
            var transport = new HttpTravelTransport(url, token, "SR1", () => (double)Time.realtimeSinceStartup);
            _agent = new TravelAgent(transport, this, text => Debug.Log(text));
            // A game started by SlimeBridge is one half of a session: closing it keeps what the vac holds now.
            TravelGui.QuitHook = () => { if (_quitSave == null) SaveNow("game closing"); };
        }

        internal static Sr1TravelRealm? TryCreate()
        {
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token)) return null;
            var destination = string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_MODE"), "DESTINATION", StringComparison.Ordinal);
            return new Sr1TravelRealm(url!, token!, destination);
        }

        /// <summary>Write this world's save now (after items moved, or before closing with the other game).</summary>
        private static bool SaveNow(string why)
        {
            try
            {
                var director = GameContext.Instance?.AutoSaveDirector;
                if (director == null || !WorldReady()) return false;
                var ok = director.SaveAllNow();
                Debug.Log("[SlimeBridge] travel: save (" + why + ") " + (ok ? "written" : "refused by the game"));
                return ok;
            }
            catch (Exception ex)
            {
                Debug.Log("[SlimeBridge] travel: save (" + why + ") failed: " + ex.GetType().Name);
                return false;
            }
        }

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

        private static string? CurrentSave()
        {
            try { return WorldReady() ? GameContext.Instance.AutoSaveDirector.SavedGame.GetName() : null; }
            catch (Exception) { return null; }
        }

        private void EnsureGui()
        {
            if (_gui != null) return;
            _gui = new GameObject("SlimeBridge_TravelGui");
            UnityEngine.Object.DontDestroyOnLoad(_gui);
            _gui.AddComponent<TravelGui>();
            TravelGui.Adopt(_overlay, _gui);
        }

        internal void Tick()
        {
            EnsureGui();
            var now = (double)Time.realtimeSinceStartup;
            if (_destinationMode && !_arrived) _throttle.MuteAudio();
            _throttle.Maintain();
            // A freshly started destination waits behind the card until the launcher brings the player in.
            if (_destinationMode && !_arrived && WorldReady() && !_overlay.IsCovering && !_held)
            {
                _overlay.CoverNow(TravelText.Arriving(), TravelText.Waiting("SR1"));
                LockInput(true);
            }
            if (_held)
            {
                if (Time.realtimeSinceStartup >= _nextHeal)
                {
                    _nextHeal = Time.realtimeSinceStartup + 1f;
                    try { var state = SceneContext.Instance?.PlayerState; if (state != null) state.SetHealth(state.GetMaxHealth()); } catch (Exception) { }
                }
            }
            Sr1Online.Refresh();
            _agent.Tick(now);
            if (_quitAt > 0f)
            {
                if (Time.realtimeSinceStartup >= _quitAt)
                {
                    _quitAt = -1f;
                    if (_quitSave == CurrentSave() && _quitSave != null) Application.Quit();
                    else { _quitSave = null; Resume("SESSION_CLOSE_TARGET_CHANGED", null); }
                }
                return;                                      // closing: never resume by hand on the way out
            }
            if (_held && _throttle.ReturnedByHand(Application.isFocused, Time.realtimeSinceStartup)) ReturnByHand();
        }

        private void ReturnByHand()
        {
            Debug.Log("[SlimeBridge] travel: the player returned to this window by hand; resuming");
            Resume(null, null);
        }

        private void LockInput(bool lockIt)
        {
            try
            {
                var player = SceneContext.Instance?.Player;
                var input = player == null ? null : player.GetComponent<vp_FPInput>();
                if (input != null) { input.AllowGameplayInput = !lockIt; _inputLocked = lockIt; }
            }
            catch (Exception) { /* input is restored by the next unlock; a failure here only means the player could move while covered */ }
        }

        public void Cancel()
        {
            _pendingDone = null;
        }

        public void Execute(TravelMessage message, TravelDone done)
        {
            switch (message.Kind)
            {
                case "prepare_departure":
                    if (!WorldReady()) { done(false, "no_world"); return; }
                    EnsureGui();
                    LockInput(true);
                    _pendingDone = done;
                    var offer = Sr1Carry.Snapshot(message.CarryMode);
                    _overlay.Close(TravelText.Departing(message.Destination.Length > 0 ? message.Destination : "SR2"), TravelText.Waiting(message.Destination), 0.18f, () =>
                    {
                        try
                        {
                            // Once covered, stop competing with the destination's startup. No inventory is removed here.
                            _throttle.Apply(true);
                            Report(done, true, offer.Count > 0 ? offer.Count + " kinds offered" : "", offer);
                        }
                        catch (Exception)
                        {
                            _throttle.Restore(); LockInput(false);
                            Report(done, false, "departure_standby_failed");
                        }
                    });
                    break;
                case "hold":
                    Hold(message.Policy, message.Carry, done);
                    break;
                case "resume":
                case "abort":
                    Resume(message.Reason, done);
                    break;
                case "arrive":
                    Arrive(message.Carry, message.Source, done);
                    break;
                case "save_quit":
                    SaveAndQuit(message, done);
                    break;
                case "online_host":
                    // the session code from Slime Rancher 2, or none: SRMP then makes its own 7-character code
                    var hosted = Sr1Online.Host(message.Code, message.Name);
                    done(hosted.Length == 0, hosted);
                    break;
                case "online_join":
                    var joined = Sr1Online.Join(message.Code, message.Name);
                    done(joined.Length == 0, joined);
                    break;
                default:
                    done(false, "unknown_kind");
                    break;
            }
        }

        private static void Report(TravelDone done, bool ok, string detail, Dictionary<string, int>? carry = null) { done(ok, detail, carry); }

        private void Hold(string policy, Dictionary<string, int> moved, TravelDone done)
        {
            if (!WorldReady()) { done(false, "no_world"); return; }
            // What the other game accepted leaves this inventory now: the items moved, they were not copied.
            var removed = moved.Count > 0 ? Sr1Carry.Remove(moved) : new Dictionary<string, int>();
            // Saved at once: closing this game later (or a crash) can neither bring the items back nor lose them.
            if (removed.Count > 0) SaveNow("carried items left");
            // The body stays in this running world, standing by the portal, so anyone else here sees it where it left.
            SanctuaryHost.Instance?.PlacePlayerAtPortal(false);
            LockInput(true);
            _overlay.CoverNow(TravelText.Parked("SR1"), TravelText.Waiting("SR1"));
            _heldWorldTime = WorldClock(); _heldRealTime = Time.realtimeSinceStartup;
            _throttle.Apply(!string.Equals(policy, "THROTTLE", StringComparison.Ordinal));
            _held = true; _nextHeal = 0f;
            done(true, removed.Count != moved.Count ? "carry mismatch" : "", removed);
        }

        private static double WorldClock()
        {
            try { return SceneContext.Instance.TimeDirector.WorldTime(); } catch (Exception) { return -1; }
        }

        /// <summary>How much in-game time passed while this world was held (a frozen world must report about zero).</summary>
        private string HeldReport()
        {
            if (_heldWorldTime < 0) return "";
            var report = "held " + (Time.realtimeSinceStartup - _heldRealTime).ToString("F0", System.Globalization.CultureInfo.InvariantCulture) + "s, world clock +" +
                         (WorldClock() - _heldWorldTime).ToString("F0", System.Globalization.CultureInfo.InvariantCulture) + "s";
            _heldWorldTime = -1;
            return report;
        }

        private void Resume(string? reason, TravelDone? done)
        {
            // a crossing that could not happen: say why (a return by hand or a normal resume has no reason)
            if (!string.IsNullOrEmpty(reason)) TravelNotice.Post(TravelText.Failure(reason!, "SR2"));
            _throttle.Restore();
            var report = HeldReport();
            _held = false; _arrived = true;
            if (!_overlay.IsCovering) { LockInput(false); done?.Invoke(true, report); return; }
            _overlay.Open(1.2f, () => { LockInput(false); done?.Invoke(true, report); });
        }

        private void Arrive(Dictionary<string, int> offered, string source, TravelDone done)
        {
            if (!WorldReady()) { done(false, "no_world"); return; }
            var accepted = offered.Count > 0 ? Sr1Carry.Receive(offered) : new Dictionary<string, int>();
            if (accepted.Count > 0) SaveNow("carried items arrived");
            var left = CarryNames.Left(offered, accepted);
            if (left.Length > 0)
            {
                Debug.Log("[SlimeBridge] travel: left in the other vac (no room, or no such item here): " + left);
                TravelNotice.Post(TravelText.LeftBehind(source.Length > 0 ? source : "SR2", left));
            }
            _throttle.Restore();
            var report = HeldReport();
            _held = false; _arrived = true;
            var placed = SanctuaryHost.Instance?.PlacePlayerAtPortal(true) ?? false;
            SanctuaryHost.Instance?.PulsePortal();
            var how = (placed ? "portal" : "in_place") + (report.Length > 0 ? "; " + report : "");
            if (!_overlay.IsCovering) { LockInput(false); done(true, how, accepted); return; }
            _overlay.Open(0.22f, () => { LockInput(false); done(true, how, accepted); });
        }

        /// <summary>The game the player was in closed: save this world and close too (the two games are one session).</summary>
        private void SaveAndQuit(TravelMessage message, TravelDone done)
        {
            EnsureGui();
            var error = message.SaveForClose(OwnPid, CurrentSave, () => SaveNow("closing with the other game"));
            if (error.Length > 0) { Resume("SESSION_SAVE_FAILED", null); done(false, error); return; }
            _throttle.Restore();
            _overlay.CoverNow(TravelText.SavingAndClosing("SR2"), "");
            _quitSave = message.TargetSave;
            _quitAt = Time.realtimeSinceStartup + 1.5f;      // lets the report reach the launcher first
            done(true, "saved");
        }

        internal void Unload()
        {
            try { _throttle.Restore(); } catch (Exception) { }
            try { if (_inputLocked) LockInput(false); } catch (Exception) { }
            _overlay.ClearNow();
            if (_gui != null) UnityEngine.Object.Destroy(_gui);
        }
    }
}
