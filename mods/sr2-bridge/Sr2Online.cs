using System;
using System.Net;
using System.Reflection;
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
        private static bool _autoRequested, _manual;

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
        private static bool ClientConnected() => Prop(_main?.GetProperty("Client", Any)?.GetValue(null), "IsConnected") is bool b && b;

        /// <summary>Start Ranching Together's Auto Host; the code arrives a few seconds later (heartbeat). "" when started.</summary>
        internal static string Host()
        {
            string result;
            try
            {
                if (!Find()) result = "ranching_together_missing";
                else if (ServerRunning()) result = "";
                else if (ClientConnected()) result = "already_connected";
                else
                {
                    var ui = Ui();
                    var start = _ui!.GetMethod("StartAutoHost", Any, null, Type.EmptyTypes, null);
                    if (ui == null || start == null) result = "ranching_together_too_old";
                    else { _autoRequested = true; _manual = false; start.Invoke(ui, null); result = ""; }
                }
            }
            catch (TargetInvocationException ex) { result = "host_failed:" + (ex.InnerException?.GetType().Name ?? "error"); }
            catch (Exception ex) { result = "host_failed:" + ex.GetType().Name; }
            MelonLoader.MelonLogger.Msg("online: auto host " + (result.Length > 0 ? "refused: " + result : "started"));
            Refresh(true, result);
            return result;
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
        private static void FallBackToManualHost(object ui)
        {
            _autoRequested = false;
            try
            {
                var autoHost = Type.GetType("SR2MP.Shared.Utils.AutoHost, " + _ui!.Assembly.FullName, false);
                var external = autoHost?.GetMethod("TryGetExternalIp", Any, null, Type.EmptyTypes, null)?.Invoke(null, null) as IPAddress;
                var manual = _ui.GetMethod("TryHostManual", Any, null, new[] { typeof(string), typeof(ushort), typeof(ushort) }, null);
                if (external == null || manual == null) { _lastError = "auto_host: no UPnP and no public address"; return; }
                manual.Invoke(ui, new object[] { external.ToString(), ManualPort, ManualPort });
                _manual = true;
                MelonLoader.MelonLogger.Msg("online: no UPnP router: hosting on UDP " + ManualPort + " (forward it on the router for friends to join)");
            }
            catch (Exception ex) { _lastError = "auto_host: manual fallback failed " + ex.GetType().Name; }
        }

        /// <summary>Main thread, about once a second: what the heartbeat tells the launcher.</summary>
        internal static void Refresh(bool now = false, string? error = null)
        {
            if (error != null) _lastError = error;
            if (!now && Time.realtimeSinceStartup < _nextRefresh) return;
            _nextRefresh = Time.realtimeSinceStartup + 1f;
            try
            {
                if (_autoRequested && Find() && !ServerRunning())
                {
                    var ui = Ui();
                    var failed = (Field(ui, "hostAutoError") as string ?? "").Length > 0 && !(Field(ui, "hostAutoInProgress") is bool busy && busy);
                    if (ui != null && failed) FallBackToManualHost(ui);
                }
            }
            catch (Exception) { _autoRequested = false; }
            var state = new OnlineState();
            try
            {
                if (Find())
                {
                    var ui = Ui();
                    state.mod = true;
                    state.signed_in = ui != null;
                    state.hosting = ServerRunning();
                    state.connected = ClientConnected();
                    state.code = state.hosting ? (Field(ui, "hostJoinCode") as string ?? "") : "";
                    var autoError = Field(ui, "hostAutoError") as string ?? "";
                    if (state.code.Length > 0) state.error = _manual ? "needs_udp_1919" : "";
                    else if (_autoRequested && autoError.Length > 0) state.error = "";              // falling back to UDP 1919
                    else state.error = autoError.Length > 0 && !_manual ? "auto_host: " + autoError.Replace('\n', ' ') : _lastError;
                }
            }
            catch (Exception ex) { state.error = "status_failed:" + ex.GetType().Name; }
            OnlineStatus.Set(state);
        }
    }
}
