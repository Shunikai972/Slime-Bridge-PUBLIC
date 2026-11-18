using System;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// SR2's half of a crossing (see Sr1TravelRealm): the portal animation over the screen, the throttled "parked" state while the
    /// player is in SR1, and the emergence when the launcher brings the player back. The world itself is never edited here.
    /// </summary>
    internal sealed class Sr2TravelRealm : ITravelRealm
    {
        private readonly TravelOverlay _overlay = new TravelOverlay();
        private readonly TravelThrottle _throttle = new TravelThrottle();
        private readonly TravelAgent _agent;
        private readonly HttpTravelTransport _transport;
        internal TravelOverlay Overlay => _overlay;
        internal bool Busy => _overlay.IsCovering || _held;
        internal void RequestCrossing(string portalId, string saveId) => _transport.RequestCrossing(portalId, saveId);
        private readonly bool _destinationMode;
        private bool _held, _arrived;
        private float _quitAt = -1f;
        private string? _quitSave;
        private static readonly int OwnPid = System.Diagnostics.Process.GetCurrentProcess().Id;


        public bool Held => _held || _throttle.Active;

        private Sr2TravelRealm(string url, string token, bool destinationMode)
        {
            _destinationMode = destinationMode;
            if (destinationMode) _throttle.MuteAudio();
            _transport = new HttpTravelTransport(url, token, "SR2", () => (double)Time.realtimeSinceStartup);
            _transport.CrossingFailed = () => TravelNotice.Post(TravelText.Failure("HOST_UNREACHABLE", "SR1"));
            _agent = new TravelAgent(_transport, this, text => MelonLoader.MelonLogger.Msg(text));
        }

        internal static Sr2TravelRealm? TryCreate()
        {
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token)) return null;
            var destination = string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_MODE"), "DESTINATION", StringComparison.Ordinal);
            return new Sr2TravelRealm(url!, token!, destination);
        }

        /// <summary>Write this world's save now (after items moved, or before closing with the other game).</summary>
        private static bool SaveNow(string why)
        {
            try
            {
                var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
                if (director == null || !WorldReady()) return false;
                var ok = director.SaveAllNow();
                MelonLoader.MelonLogger.Msg("travel: save (" + why + ") " + (ok ? "written" : "refused by the game"));
                return ok;
            }
            catch (Exception ex)
            {
                MelonLoader.MelonLogger.Msg("travel: save (" + why + ") failed: " + ex.GetType().Name);
                return false;
            }
        }

        /// <summary>The game is closing (window closed, Alt+F4): keep what the vac holds now.</summary>
        internal void SaveOnQuit()
        {
            if (_quitSave == null) SaveNow("game closing");
        }

        private static bool WorldReady()
        {
            try
            {
                var game = Il2Cpp.GameContext.Instance;
                var scene = Il2Cpp.SceneContext.Instance;
                return game != null && scene != null && scene.Player != null && scene.PlayerState != null;
            }
            catch (Exception) { return false; }
        }

        private static string? CurrentSave() => CounterpartCreator.TryReadWorld(out var world) ? world!.Name : null;

        internal void Gui() { _overlay.Draw(); }

        internal void Tick()
        {
            if (_destinationMode && !_arrived) _throttle.MuteAudio();
            _throttle.Maintain();
            var notice = TravelNotice.Take();
            if (notice != null) _overlay.ShowHint(notice, 6f);
            if (_destinationMode && !_arrived && WorldReady() && !_overlay.IsCovering && !_held)
                _overlay.CoverNow(TravelText.Arriving(), TravelText.Waiting("SR2"));
            Sr2Online.Refresh();
            _agent.Tick((double)Time.realtimeSinceStartup);
            if (_quitAt > 0f)
            {
                if (Time.realtimeSinceStartup >= _quitAt)
                {
                    _quitAt = -1f;
                    if (_quitSave == CurrentSave() && _quitSave != null) Application.Quit();
                    else { _quitSave = null; Resume(null); }
                }
                return;                                      // closing: never resume by hand on the way out
            }
            if (_held && _throttle.ReturnedByHand(Application.isFocused, Time.realtimeSinceStartup))
            {
                MelonLoader.MelonLogger.Msg("travel: the player returned to this window by hand; resuming");
                Resume(null);
            }
        }

        public void Cancel() { }

        public void Execute(TravelMessage message, TravelDone done)
        {
            switch (message.Kind)
            {
                case "prepare_departure":
                    if (!WorldReady()) { done(false, "no_world"); return; }
                    var offer = Sr2Carry.Snapshot(message.CarryMode);
                    _overlay.Close(TravelText.Departing(message.Destination.Length > 0 ? message.Destination : "SR1"), TravelText.Waiting(message.Destination), 0.18f, () =>
                    {
                        try
                        {
                            // Covered departure is recoverable standby, not a completed item transfer.
                            _throttle.Apply(true);
                            done(true, offer.Count > 0 ? offer.Count + " kinds offered" : "", offer);
                        }
                        catch (Exception)
                        {
                            _throttle.Restore();
                            done(false, "departure_standby_failed");
                        }
                    });
                    break;
                case "hold":
                    if (!WorldReady()) { done(false, "no_world"); return; }
                    _overlay.CoverNow(TravelText.Parked("SR2"), TravelText.Waiting("SR2"));
                    var removed = message.Carry.Count > 0 ? Sr2Carry.Remove(message.Carry) : new System.Collections.Generic.Dictionary<string, int>();
                    // Saved at once: closing this game later (or a crash) can neither bring the items back nor lose them.
                    if (removed.Count > 0) SaveNow("carried items left");
                    _throttle.Apply(!string.Equals(message.Policy, "THROTTLE", StringComparison.Ordinal));
                    _held = true;
                    done(true, removed.Count != message.Carry.Count ? "carry mismatch" : "", removed);
                    break;
                case "resume":
                case "abort":
                    // a crossing that could not happen: say why (a return by hand has no reason)
                    if (!string.IsNullOrEmpty(message.Reason)) _overlay.ShowHint(TravelText.Failure(message.Reason, "SR1"), 6f);
                    Resume(done);
                    break;
                case "arrive":
                    if (!WorldReady()) { done(false, "no_world"); return; }
                    var accepted = message.Carry.Count > 0 ? Sr2Carry.Receive(message.Carry) : new System.Collections.Generic.Dictionary<string, int>();
                    if (accepted.Count > 0) SaveNow("carried items arrived");
                    var left = CarryNames.Left(message.Carry, accepted);
                    if (left.Length > 0)
                    {
                        MelonLoader.MelonLogger.Msg("travel: left in the other vac (no such item here): " + left);
                        TravelNotice.Post(TravelText.LeftBehind(message.Source.Length > 0 ? message.Source : "SR1", left));
                    }
                    _throttle.Restore();
                    _held = false; _arrived = true;
                    var placed = Sr2ReturnPortalHook.PlacePlayer?.Invoke() ?? false;
                    if (!_overlay.IsCovering) { done(true, placed ? "portal" : "in_place", accepted); return; }
                    _overlay.Open(0.22f, () => done(true, placed ? "portal" : "in_place", accepted));
                    break;
                case "online_host":
                    // Ranching Together's Auto Host makes the session code (sent with the heartbeat once it exists)
                    var hosted = Sr2Online.Host();
                    done(hosted.Length == 0, hosted);
                    break;
                case "online_join":
                    // Ranching Together can reset the currently loaded save. The player
                    // must deliberately pick an empty save and join in its native UI.
                    done(false, "sr2_join_in_game");
                    break;
                case "save_quit":
                    var error = message.SaveForClose(OwnPid, CurrentSave, () => SaveNow("closing with the other game"));
                    if (error.Length > 0) { Resume(null); done(false, error); return; }
                    _throttle.Restore();
                    _overlay.CoverNow(TravelText.SavingAndClosing("SR1"), "");
                    _quitSave = message.TargetSave;
                    _quitAt = Time.realtimeSinceStartup + 1.5f;      // lets the report reach the launcher first
                    done(true, "saved");
                    break;
                default:
                    done(false, "unknown_kind");
                    break;
            }
        }

        private void Resume(TravelDone? done)
        {
            _throttle.Restore();
            _held = false; _arrived = true;
            if (!_overlay.IsCovering) { done?.Invoke(true, ""); return; }
            _overlay.Open(1.2f, () => done?.Invoke(true, ""));
        }

        internal void Unload()
        {
            try { _throttle.Restore(); } catch (Exception) { }
            _overlay.ClearNow();
        }
    }

    /// <summary>Set by the return-portal feature to put the arriving player in front of the SR2 portal.</summary>
    internal static class Sr2ReturnPortalHook
    {
        internal static Func<bool>? PlacePlayer;
    }
}
