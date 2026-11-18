using System;
using System.Reflection;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Slime Rancher's online side, through SRMP's <c>SRMultiplayer.SlimeBridgeLink</c> (SlimeBridge's SRMP build): host
    /// under the session code, join with it, and report the lobby code. Reflection only: without SRMP (or with an SRMP that
    /// predates the link) everything reports "not available" and nothing else changes.
    /// </summary>
    internal static class Sr1Online
    {
        private static Type? _link;
        private static float _nextSearch, _nextRefresh;

        private static Type? Link()
        {
            if (_link != null || Time.realtimeSinceStartup < _nextSearch) return _link;
            _nextSearch = Time.realtimeSinceStartup + 5f;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type? type;
                try { type = assembly.GetType("SRMultiplayer.SlimeBridgeLink", false); }
                catch (Exception) { continue; }
                if (type != null) return _link = type;
            }
            return null;
        }

        private static object? Get(string property)
        {
            try { return Link()?.GetProperty(property, BindingFlags.Public | BindingFlags.Static)?.GetValue(null, null); }
            catch (Exception) { return null; }
        }

        private static string Call(string method, string code)
        {
            var link = Link();
            if (link == null) return "srmp_missing";
            try
            {
                var m = link.GetMethod(method, BindingFlags.Public | BindingFlags.Static, null, new[] { typeof(string) }, null);
                return m == null ? "srmp_too_old" : (m.Invoke(null, new object[] { code }) as string ?? "");
            }
            catch (TargetInvocationException ex) { return method.ToLowerInvariant() + "_failed:" + (ex.InnerException?.GetType().Name ?? "error"); }
            catch (Exception ex) { return method.ToLowerInvariant() + "_failed:" + ex.GetType().Name; }
        }

        // A host / join asked before SRMP's sign-in finished: run once signed in (Refresh), never twice.
        private static string? _pendingKind, _pendingCode, _pendingName;
        private static float _pendingUntil, _nextSignIn;

        private static bool SignedIn() => Get("IsSignedIn") is bool s && s;

        /// <summary>Open the lobby (under <paramref name="code"/> when given). "" when started or queued until sign-in, else why not.</summary>
        internal static string Host(string code, string name) => Request("Host", code, name);

        internal static string Join(string code, string name) => Request("Join", code, name);

        private static string Request(string kind, string code, string name)
        {
            var link = Link();
            if (link == null) { Refresh(true, "srmp_missing"); return "srmp_missing"; }
            if (link.GetMethod("SignIn", BindingFlags.Public | BindingFlags.Static) == null && !SignedIn())
            {
                Refresh(true, "srmp_too_old");
                return "srmp_too_old";
            }
            if (!SignedIn())
            {
                // SRMP signs in under the player's own name only: the one typed in SlimeBridge, else the one in its menu.
                _nextSignIn = 0f;
                _pendingName = name;
                var signIn = SignIn();
                if (signIn.Length > 0) { _pendingName = null; Refresh(true, signIn); return signIn; }
                _pendingKind = kind; _pendingCode = code; _pendingUntil = Time.realtimeSinceStartup + 90f;
                Debug.Log("[SlimeBridge] online: " + kind.ToLowerInvariant() + " waits for SRMP's sign-in");
                Refresh(true, "");
                return "";
            }
            return Run(kind, code);
        }

        private static string SignIn()
        {
            if (Time.realtimeSinceStartup < _nextSignIn) return "";
            _nextSignIn = Time.realtimeSinceStartup + 5f;
            var result = Call("SignIn", _pendingName ?? "");
            if (result.Length > 0) Debug.Log("[SlimeBridge] online: SRMP sign-in refused: " + result);
            return result;
        }

        private static string Run(string kind, string code)
        {
            var result = Call(kind, code);
            Debug.Log("[SlimeBridge] online: " + kind.ToLowerInvariant() + (code.Length > 0 && kind == "Host" ? " with the session code" : "") +
                      (result.Length > 0 ? " refused: " + result : " started"));
            Refresh(true, result);
            return result;
        }

        private static string _lastError = "";
        private static string? _startupName = StartupName();
        private static string? StartupName() { var n = OnlineCodes.CleanName(Environment.GetEnvironmentVariable("SLIMEBRIDGE_ONLINE_NAME")); return n.Length > 0 ? n : null; }
        private static float _nextName;

        private static void ApplyStartupName()
        {
            if (_startupName == null || Time.realtimeSinceStartup < _nextName) return;
            if (Time.realtimeSinceStartup > 180f || Link() == null && Time.realtimeSinceStartup > 60f) { _startupName = null; return; }
            _nextName = Time.realtimeSinceStartup + 5f;
            var link = Link();
            if (link == null || link.GetMethod("SetName", BindingFlags.Public | BindingFlags.Static) == null) return;
            var result = Call("SetName", _startupName);
            if (result == "not_ready") return;
            Debug.Log("[SlimeBridge] online: SRMP name " + (result.Length == 0 ? "set from SlimeBridge" : "not set: " + result));
            _startupName = null;
        }

        /// <summary>Main thread, about once a second: what the heartbeat tells the launcher.</summary>
        internal static void Refresh(bool now = false, string? error = null)
        {
            if (error != null) _lastError = error;
            ApplyStartupName();
            if (_pendingKind != null)
            {
                if (SignedIn()) { var pendingKind = _pendingKind; var pendingCode = _pendingCode ?? ""; _pendingKind = _pendingCode = _pendingName = null; Run(pendingKind, pendingCode); }
                else if (Time.realtimeSinceStartup > _pendingUntil) { _pendingKind = _pendingCode = _pendingName = null; _lastError = "sign_in_timeout"; }
                else SignIn();
            }
            if (!now && Time.realtimeSinceStartup < _nextRefresh) return;
            _nextRefresh = Time.realtimeSinceStartup + 1f;
            var link = Link();
            var code = Get("CurrentCode") as string ?? "";
            var hosting = Get("IsHost") is bool h && h;
            OnlineStatus.Set(new OnlineState
            {
                mod = link != null,
                signed_in = Get("IsSignedIn") is bool s && s,
                hosting = hosting,
                connected = code.Length > 0 && !hosting,
                code = code,
                error = code.Length > 0 ? "" : _lastError,
            });
        }
    }
}
