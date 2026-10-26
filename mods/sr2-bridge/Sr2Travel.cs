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
        private float _focusedSince = -1f;

        public bool Held => _held;

        private Sr2TravelRealm(string url, string token, bool destinationMode)
        {
            _destinationMode = destinationMode;
            _transport = new HttpTravelTransport(url, token, "SR2", () => (double)Time.realtimeSinceStartup);
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

        internal void Gui() { _overlay.Draw(); }

        internal void Tick()
        {
            if (_destinationMode && !_arrived && WorldReady() && !_overlay.IsCovering && !_held)
                _overlay.CoverNow(TravelText.Arriving(), TravelText.Waiting("SR2"));
            if (_held)
            {
                if (Application.isFocused) { if (_focusedSince < 0f) _focusedSince = Time.realtimeSinceStartup; }
                else _focusedSince = -1f;
            }
            _agent.Tick((double)Time.realtimeSinceStartup);
            if (_held && _focusedSince >= 0f && Time.realtimeSinceStartup - _focusedSince > 4f)
            {
                _focusedSince = -1f;
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
                    _overlay.Close(TravelText.Departing(message.Destination.Length > 0 ? message.Destination : "SR1"), TravelText.Waiting(message.Destination), 1.7f, () => done(true, offer.Count > 0 ? offer.Count + " kinds offered" : "", offer));
                    break;
                case "hold":
                    if (!WorldReady()) { done(false, "no_world"); return; }
                    _overlay.CoverNow(TravelText.Parked("SR2"), TravelText.Waiting("SR2"));
                    var removed = message.Carry.Count > 0 ? Sr2Carry.Remove(message.Carry) : new System.Collections.Generic.Dictionary<string, int>();
                    _throttle.Apply(!string.Equals(message.Policy, "THROTTLE", StringComparison.Ordinal));
                    _held = true; _focusedSince = -1f;
                    done(true, removed.Count != message.Carry.Count ? "carry mismatch" : "", removed);
                    break;
                case "resume":
                case "abort":
                    Resume(done);
                    break;
                case "arrive":
                    if (!WorldReady()) { done(false, "no_world"); return; }
                    var accepted = message.Carry.Count > 0 ? Sr2Carry.Receive(message.Carry) : new System.Collections.Generic.Dictionary<string, int>();
                    _throttle.Restore();
                    _held = false; _arrived = true;
                    var placed = Sr2ReturnPortalHook.PlacePlayer?.Invoke() ?? false;
                    if (!_overlay.IsCovering) { done(true, placed ? "portal" : "in_place", accepted); return; }
                    _overlay.Open(2.2f, () => done(true, placed ? "portal" : "in_place", accepted));
                    break;
                default:
                    done(false, "unknown_kind");
                    break;
            }
        }

        private void Resume(TravelDone? done)
        {
            _throttle.Restore();
            _held = false;
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
