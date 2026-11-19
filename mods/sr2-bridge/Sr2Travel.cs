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
        private float _quitUntil;
        private TravelMessage? _quitRequest;
        private string? _quitCommitId;
        private bool _verifiedQuit;
        internal bool Quiescing => _quitRequest != null || _verifiedQuit;
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
                MelonLoader.MelonLogger.Msg("travel: save (" + why + ") " + (ok ? "native call accepted (not verified on disk)" : "refused by the game"));
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
            if (!_verifiedQuit) SaveNow("game closing");
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
            {
                _overlay.CoverNow(TravelText.Arriving(), TravelText.Waiting("SR2"));
                _throttle.Apply(false);                      // waiting behind the portal: few frames, lowest quality, muted
            }
            if (!Quiescing) Sr2Online.Refresh();
            _agent.Tick((double)Time.realtimeSinceStartup);
            if (TickClose()) return;
            // Alt+Tab back to this parked game changes nothing: the vac and the player are in the other game, and only the
            // portal brings them back (resuming here would show a world without its vac).
        }

        public void Cancel() => Sr2Online.CancelHost();

        public void Execute(TravelMessage message, TravelDone done)
        {
            if (_verifiedQuit)
            {
                done(false, "quit_already_requested");
                return;
            }
            if (_quitRequest != null && message.Kind != "close_commit" && message.Kind != "close_abort")
            {
                if (message.TransitionId == "local" && message.Reason == "host_lost") AbortClose();
                else { done(false, "close_in_progress"); return; }
            }
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
                        MelonLoader.MelonLogger.Msg("travel: left in the other vac (no room, or no such item here): " + left);
                        TravelNotice.Post(TravelText.LeftBehind(message.Source.Length > 0 ? message.Source : "SR1", left));
                    }
                    _throttle.Restore();
                    _held = false; _arrived = true;
                    TravelScreen.FullscreenAgainOnArrival();          // started small behind the other game: the player's fullscreen comes back
                    var placed = Sr2ReturnPortalHook.PlacePlayer?.Invoke() ?? false;
                    if (!_overlay.IsCovering) { done(true, placed ? "portal" : "in_place", accepted); return; }
                    _overlay.Open(0.22f, () => done(true, placed ? "portal" : "in_place", accepted));
                    break;
                case "online_host":
                    Sr2Online.Host(done);
                    break;
                case "online_join":
                    // Ranching Together can reset the currently loaded save. The player
                    // must deliberately pick an empty save and join in its native UI.
                    done(false, "sr2_join_in_game");
                    break;
                case "save_quit":
                    done(false, "close_protocol_required");
                    break;
                case "close_prepare":
                    PrepareClose(message, done);
                    break;
                case "close_commit":
                    CommitClose(message, done);
                    break;
                case "close_abort":
                    if (message.TargetPid != OwnPid || (_quitRequest != null && !MatchesClose(message)))
                    { done(false, "close_target_changed"); return; }
                    if (_quitRequest != null) AbortClose();
                    done(true, "close_aborted");
                    break;
                default:
                    done(false, "unknown_kind");
                    break;
            }
        }

        private void PrepareClose(TravelMessage message, TravelDone done)
        {
            if (!message.Targets(OwnPid, CurrentSave()) || !Sr2Online.CanQuiesce())
            { Resume(null); done(false, "close_target_or_online_busy"); return; }
            try
            {
                _quitRequest = message; // suppress all bridge gameplay writers before obtaining proof
                _throttle.Apply(true);
                _held = true;
                _overlay.CoverNow(TravelText.SavingAndClosing("SR1"), "");
                var error = message.SaveForClose(OwnPid, CurrentSave, () => VerifyCloseSave(message));
                if (error.Length > 0) { AbortClose(); done(false, error); return; }
                _quitUntil = Time.realtimeSinceStartup + 20f;
                done(true, "verified_native_save");
            }
            catch (Exception)
            {
                AbortClose();
                done(false, "save_not_confirmed");
            }
        }

        private void CommitClose(TravelMessage message, TravelDone done)
        {
            if (!MatchesClose(message) || _quitCommitId != null)
            { done(false, "close_not_prepared"); return; }
            if (Time.realtimeSinceStartup >= _quitUntil || !message.Targets(OwnPid, CurrentSave()) || !Sr2Online.CanQuiesce())
            { AbortClose(); done(false, "close_expired_or_target_changed"); return; }
            _quitCommitId = message.MessageId;
            _quitUntil = Time.realtimeSinceStartup + 10f;
            done(true, "saved");
        }

        private bool TickClose()
        {
            if (_verifiedQuit) return true;
            if (_quitRequest == null) return false;
            if (Time.realtimeSinceStartup >= _quitUntil || !_quitRequest.Targets(OwnPid, CurrentSave()) || !Sr2Online.CanQuiesce())
                AbortClose();
            else if (_quitCommitId != null && _transport.SavedAcknowledged(_quitCommitId))
            {
                _verifiedQuit = true;
                _quitRequest = null;
                Application.Quit();
            }
            return true;
        }

        private bool MatchesClose(TravelMessage message) => _quitRequest != null &&
            _quitRequest.TransitionId == message.TransitionId && _quitRequest.TargetPid == message.TargetPid &&
            string.Equals(_quitRequest.TargetSave, message.TargetSave, StringComparison.Ordinal);

        private void AbortClose()
        {
            _quitRequest = null;
            _quitCommitId = null;
            Resume(null);
        }

        private static bool VerifyCloseSave(TravelMessage message)
        {
            try
            {
                if (!CounterpartCreator.TryReadWorld(out var world) || world == null || !message.Targets(OwnPid, world.Name)) return false;
                var proof = new Sr2InventoryPersistence(world.Game, world.Scene, world.Name).SaveForClose();
                if (!proof.Verified) MelonLoader.MelonLogger.Warning("travel: close cancelled: native save could not be verified");
                return proof.Verified;
            }
            catch (Exception ex)
            {
                MelonLoader.MelonLogger.Warning("travel: close cancelled: " + ex.GetType().Name);
                return false;
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
            _quitRequest = null;
            Sr2Online.CancelHost();
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
