using System;
using System.Net;
using System.Reflection;
using System.Threading.Tasks;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Slime Rancher 2's online side, through Ranching Together (SR2MP): its own Auto Host (UPnP) makes the join code that
    /// is also the session code, and joining decodes a code with Ranching Together's own decoder. Reflection only: without
    /// the mod (or with a version whose members moved) everything reports "not available" and nothing else changes.
    /// </summary>
    internal static class Sr2Online
    {
        private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
        private static Type? _ui, _main, _joinCode;
        private static float _nextSearch, _nextRefresh;
        private static string _lastError = "";
        private const ushort ManualPort = 1919;                 // Ranching Together's default host port
        private static bool _manual;
        private static TravelDone? _hostDone;
        private static object? _hostUi;
        private static float _hostUntil;
        private static Task<IPAddress?>? _manualAddress;

        private static bool Find()
        {
            if (_ui != null && _main != null && _joinCode != null) return true;
            if (Time.realtimeSinceStartup < _nextSearch) return false;
            _nextSearch = Time.realtimeSinceStartup + 5f;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    _ui ??= assembly.GetType("SR2MP.Components.UI.MultiplayerUI", false);
                    _main ??= assembly.GetType("SR2MP.Main", false);
                    _joinCode ??= assembly.GetType("SR2MP.Shared.Utils.JoinCode", false);
                }
                catch (Exception) { /* a dynamic or broken assembly: skip it */ }
            }
            return _ui != null && _main != null && _joinCode != null;
        }

        private static object? Ui() => _ui?.GetProperty("Instance", Any)?.GetValue(null);
        private static object? Field(object? target, string name) => target?.GetType().GetField(name, Any)?.GetValue(target);
        private static object? Prop(object? target, string name) => target?.GetType().GetProperty(name, Any)?.GetValue(target);

        private static bool ServerRunning() => Prop(_main?.GetProperty("Server", Any)?.GetValue(null), "IsRunning") is bool b && b;
        private static object? Client() => _main?.GetProperty("Client", Any)?.GetValue(null);
        private static bool ClientConnected() => Prop(Client(), "IsConnected") is true || Prop(Client(), "IsConnecting") is true;

        // Cancelling SlimeBridge's callback does not cancel Ranching Together's own Auto Host.
        internal static bool CanQuiesce()
        {
            try
            {
                if (!Find()) return _main == null && _ui == null && _joinCode == null;
                var server = _main!.GetProperty("Server", Any)?.GetValue(null);
                var client = Client();
                return Prop(server, "IsRunning") is false && Prop(client, "IsConnected") is false &&
                    Prop(client, "IsConnecting") is false && Prop(client, "IsJoined") is false && Field(Ui(), "hostAutoInProgress") is false;
            }
            catch (Exception) { return false; }
        }

        /// <summary>Complete only once the native listener and its join code are both ready.</summary>
        internal static void Host(TravelDone done)
        {
            string result;
            try
            {
                if (!Find()) result = "ranching_together_missing";
                else if (_hostDone != null) result = "auto_host_already_pending";
                else if (ClientConnected()) result = "already_connected";
                else
                {
                    var ui = Ui();
                    var start = _ui!.GetMethod("StartAutoHost", Any, null, Type.EmptyTypes, null);
                    if (ui == null || start == null || _ui.GetField("hostAutoInProgress", Any) == null ||
                        _ui.GetField("hostJoinCode", Any) == null) result = "ranching_together_too_old";
                    else
                    {
                        if (!ServerRunning())
                        {
                            _manual = false;
                            start.Invoke(ui, null);
                        }
                        _hostUi = ui;
                        _hostUntil = Time.realtimeSinceStartup + 90f;
                        _hostDone = done;
                        Refresh(true, "");
                        return;
                    }
                }
            }
            catch (TargetInvocationException ex) { result = "host_failed:" + (ex.InnerException?.GetType().Name ?? "error"); }
            catch (Exception ex) { result = "host_failed:" + ex.GetType().Name; }
            _lastError = result;
            done(false, result);
        }

        internal static void CancelHost()
        {
            _hostDone = null;
            _hostUi = null;
            _manualAddress = null;
        }

        private static void FinishHost(string error)
        {
            var done = _hostDone;
            CancelHost();
            _lastError = error;
            done?.Invoke(error.Length == 0, error);
        }

        /// <summary>Connect to the host behind a session code. "" when the connection started.</summary>
        internal static string Join(string code)
        {
            string result;
            try
            {
                if (!Find()) result = "ranching_together_missing";
                else if (!OnlineCodes.IsSession(code)) result = "bad_code";
                else if (ServerRunning() || ClientConnected()) result = "already_online";
                else
                {
                    var decode = _joinCode!.GetMethod("TryDecode", BindingFlags.Public | BindingFlags.Static);
                    var connect = _ui!.GetMethod("Connect", Any, null, new[] { typeof(string), typeof(ushort) }, null);
                    var ui = Ui();
                    if (decode == null || connect == null || ui == null) result = "ranching_together_too_old";
                    else
                    {
                        var args = new object?[] { code, null, (ushort)0, null };
                        if (!(decode.Invoke(null, args) is bool ok) || !ok || !(args[1] is IPAddress address) || !(args[2] is ushort port))
                            result = "bad_code";
                        else { connect.Invoke(ui, new object[] { address.ToString(), port }); result = ""; }
                    }
                }
            }
            catch (TargetInvocationException ex) { result = "join_failed:" + (ex.InnerException?.GetType().Name ?? "error"); }
            catch (Exception ex) { result = "join_failed:" + ex.GetType().Name; }
            MelonLoader.MelonLogger.Msg("online: join " + (result.Length > 0 ? "refused: " + result : "started"));
            Refresh(true, result);
            return result;
        }

        /// <summary>
        /// Auto Host found no UPnP router: host on UDP 1919 with the public address Ranching Together already found, as its
        /// own "manual host" does. The code works once the player forwards UDP 1919 on the router (or uses a tunnel).
        /// </summary>
        private static void CheckHost(object? ui)
        {
            if (_hostDone == null) return;
            if (!ReferenceEquals(ui, _hostUi)) { FinishHost("auto_host: menu changed"); return; }
            if (Time.realtimeSinceStartup >= _hostUntil) { FinishHost("auto_host: timed out"); return; }
            if (Field(ui, "hostAutoInProgress") is true) return;
            var code = Field(ui, "hostJoinCode") as string ?? "";
            if (ServerRunning()) { FinishHost(OnlineCodes.IsSession(code) ? "" : "auto_host: no usable join code"); return; }
            if (code.Length > 0) { FinishHost("auto_host: listener did not start"); return; }
            if (string.IsNullOrEmpty(Field(ui, "hostAutoError") as string)) { FinishHost("auto_host: not started"); return; }
            if (_manualAddress == null)
            {
                var external = _ui!.Assembly.GetType("SR2MP.Shared.Utils.AutoHost", false)?
                    .GetMethod("TryGetExternalIp", Any, null, Type.EmptyTypes, null);
                _manualAddress = Task.Run(() =>
                {
                    try { return external?.Invoke(null, null) as IPAddress; }
                    catch (Exception) { return null; }
                });
                return;
            }
            if (!_manualAddress.IsCompleted) return;
            var address = _manualAddress.GetAwaiter().GetResult();
            var manual = _ui!.GetMethod("TryHostManual", Any, null, new[] { typeof(string), typeof(ushort), typeof(ushort) }, null);
            if (address == null || manual == null) { FinishHost("auto_host: no UPnP and no public address"); return; }
            if (ClientConnected()) { FinishHost("already_connected"); return; }
            manual.Invoke(ui, new object[] { address.ToString(), ManualPort, ManualPort });
            _manual = ServerRunning() && OnlineCodes.IsSession(Field(ui, "hostJoinCode") as string ?? "");
            FinishHost(_manual ? "" : "auto_host: manual listener or code unavailable");
        }

        /// <summary>Main thread, about once a second: what the heartbeat tells the launcher.</summary>
        internal static void Refresh(bool now = false, string? error = null)
        {
            if (error != null) _lastError = error;
            if (!now && Time.realtimeSinceStartup < _nextRefresh) return;
            _nextRefresh = Time.realtimeSinceStartup + 1f;
            var state = new OnlineState();
            try
            {
                if (Find())
                {
                    var ui = Ui();
                    CheckHost(ui);
                    state.mod = true;
                    state.signed_in = ui != null;
                    var code = Field(ui, "hostJoinCode") as string ?? "";
                    state.hosting = !(Field(ui, "hostAutoInProgress") is true) && ServerRunning() && OnlineCodes.IsSession(code);
                    state.connected = Prop(Client(), "IsJoined") is true;
                    state.code = state.hosting ? code : "";
                    state.error = state.hosting ? (_manual ? "needs_udp_1919" : "") : _lastError;
                }
            }
            catch (Exception ex) { state.error = "status_failed:" + ex.GetType().Name; if (_hostDone != null) FinishHost(state.error); }
            OnlineStatus.Set(state);
        }
    }
}
