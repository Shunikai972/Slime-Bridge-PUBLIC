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
        public void OnGUI() { Overlay?.Draw(); }
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
        private float _nextHeal, _focusedSince = -1f;
        private double _heldWorldTime = -1, _heldRealTime;
        private GameObject? _gui;
        private TravelDone? _pendingDone;

        public bool Held => _held;

        private Sr1TravelRealm(string url, string token, bool destinationMode)
        {
            _destinationMode = destinationMode;
            var transport = new HttpTravelTransport(url, token, "SR1", () => (double)Time.realtimeSinceStartup);
            _agent = new TravelAgent(transport, this, text => Debug.Log(text));
        }

        internal static Sr1TravelRealm? TryCreate()
        {
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token)) return null;
            var destination = string.Equals(Environment.GetEnvironmentVariable("SLIMEBRIDGE_TRAVEL_MODE"), "DESTINATION", StringComparison.Ordinal);
            return new Sr1TravelRealm(url!, token!, destination);
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

        private void EnsureGui()
        {
            if (_gui != null) return;
            TravelGui.Overlay = _overlay;
            _gui = new GameObject("SlimeBridge_TravelGui");
            UnityEngine.Object.DontDestroyOnLoad(_gui);
            _gui.AddComponent<TravelGui>();
        }

        internal void Tick()
        {
            EnsureGui();
            var now = (double)Time.realtimeSinceStartup;
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
                // The player came back to this window by hand: do not leave them in a frozen world.
                if (Application.isFocused) { if (_focusedSince < 0f) _focusedSince = Time.realtimeSinceStartup; }
                else _focusedSince = -1f;
            }
            _agent.Tick(now);
            if (_held && _focusedSince >= 0f && Time.realtimeSinceStartup - _focusedSince > 4f) { _focusedSince = -1f; ReturnByHand(); }
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
                    _overlay.Close(TravelText.Departing(message.Destination.Length > 0 ? message.Destination : "SR2"), TravelText.Waiting(message.Destination), 1.7f, () => Report(done, true, offer.Count > 0 ? offer.Count + " kinds offered" : "", offer));
                    break;
                case "hold":
                    Hold(message.Policy, message.Carry, done);
                    break;
                case "resume":
                case "abort":
                    Resume(message.Reason, done);
                    break;
                case "arrive":
                    Arrive(message.Carry, done);
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
            // The body stays in this running world, standing by the portal, so anyone else here sees it where it left.
            SanctuaryHost.Instance?.PlacePlayerAtPortal(false);
            LockInput(true);
            _overlay.CoverNow(TravelText.Parked("SR1"), TravelText.Waiting("SR1"));
            _heldWorldTime = WorldClock(); _heldRealTime = Time.realtimeSinceStartup;
            _throttle.Apply(!string.Equals(policy, "THROTTLE", StringComparison.Ordinal));
            _held = true; _focusedSince = -1f; _nextHeal = 0f;
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
            _throttle.Restore();
            var report = HeldReport();
            _held = false;
            if (!_overlay.IsCovering) { LockInput(false); done?.Invoke(true, report); return; }
            _overlay.Open(1.2f, () => { LockInput(false); done?.Invoke(true, report); });
        }

        private void Arrive(Dictionary<string, int> offered, TravelDone done)
        {
            if (!WorldReady()) { done(false, "no_world"); return; }
            var accepted = offered.Count > 0 ? Sr1Carry.Receive(offered) : new Dictionary<string, int>();
            _throttle.Restore();
            var report = HeldReport();
            _held = false; _arrived = true;
            var placed = SanctuaryHost.Instance?.PlacePlayerAtPortal(true) ?? false;
            SanctuaryHost.Instance?.PulsePortal();
            var how = (placed ? "portal" : "in_place") + (report.Length > 0 ? "; " + report : "");
            if (!_overlay.IsCovering) { LockInput(false); done(true, how, accepted); return; }
            _overlay.Open(2.2f, () => { LockInput(false); done(true, how, accepted); });
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
