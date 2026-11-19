using SlimeBridge.Shared;
using SlimeBridge.SR1;
using SlimeBridge.SR2;
using SR2MP.Components.UI;
using UnityEngine;

internal static class Program
{
    private const string Code = "Rk7mP2xQa";
    private static void Require(bool value, string error)
    {
        if (!value) throw new Exception(error);
    }

    private static void CheckSr1Name(string mode)
    {
        Environment.SetEnvironmentVariable("SLIMEBRIDGE_ONLINE_NAME", mode == "startup" || mode == "explicit" ? "StartupName" : null);
        var link = typeof(SRMultiplayer.SlimeBridgeLink);
        if (mode == "saved") SRMultiplayer.SlimeBridgeLink.SetName("NativeName");
        var result = Sr1Online.Host("", mode == "explicit" ? "TypedName" : "");
        var expected = mode switch { "startup" => "StartupName", "explicit" => "TypedName", "saved" => "NativeName", _ => "" };
        Require(SRMultiplayer.SlimeBridgeLink.UsedName == expected, $"{link.Name}: wrong name for {mode}");
        Require(result == (mode == "missing" ? "needs_name" : ""), "wrong native sign-in result");
    }

    private static void CheckSr2()
    {
        var ui = MultiplayerUI.Instance;
        var events = new List<(bool Ok, string Detail)>();
        void Start() => Sr2Online.Host((ok, detail, carry) => events.Add((ok, detail)));
        void Refresh() => Sr2Online.Refresh(true);
        Start(); Refresh();
        Require(events.Count == 0 && ui.StartCalls == 1, "acknowledged the invocation before the listener");
        ui.Complete(Code, true); Refresh(); Refresh();
        Require(events.Count == 1 && events[^1].Ok && OnlineStatus.Current?.hosting == true, "delayed host not confirmed once");
        Start();
        Require(events.Count == 2 && events[^1].Ok && ui.StartCalls == 1, "restarted a valid existing lobby");
        SR2MP.Main.Server.IsRunning = false;
        Start(); ui.Complete(Code, false); Refresh();
        Require(events.Count == 3 && !events[^1].Ok && OnlineStatus.Current?.hosting == false, "join code without listener treated as host");
        Start(); Time.realtimeSinceStartup = 100f; Refresh();
        Require(events.Count == 4 && !events[^1].Ok && events[^1].Detail.Contains("timed out"), "hanging host not bounded");
        ui.Complete("", false);
        Start(); Sr2Online.CancelHost(); ui.Complete(Code, true); Refresh();
        Require(events.Count == 4, "obsolete host callback reported");
        SR2MP.Main.Server.IsRunning = false;
        SR2MP.Main.Client.IsConnected = true; Refresh();
        Require(OnlineStatus.Current?.connected == false, "unapproved client reported connected");
        SR2MP.Main.Client.IsJoined = true; Refresh();
        Require(OnlineStatus.Current?.connected == true, "approved client not reported connected");
    }

    private static void Main(string[] args)
    {
        if (args[0] == "sr2") CheckSr2(); else CheckSr1Name(args[0]);
        Console.WriteLine("online adapter OK: " + args[0]);
    }
}

namespace SlimeBridge.Shared
{
    internal delegate void TravelDone(bool ok, string detail, Dictionary<string, int>? carry = null);
}
namespace UnityEngine
{
    public static class Time { public static float realtimeSinceStartup; }
    public static class Debug { public static void Log(string text) { } }
}
namespace MelonLoader
{
    public static class MelonLogger { public static void Msg(string text) { } }
}
namespace SRMultiplayer
{
    public static class SlimeBridgeLink
    {
        private static string _nativeName = "";
        public static string UsedName = "";
        public static string CurrentCode => "";
        public static bool IsSignedIn => false;
        public static string SetName(string name) { _nativeName = name; return ""; }
        public static string SignIn(string name)
        {
            UsedName = name.Length > 0 ? name : _nativeName;
            return UsedName.Length == 0 ? "needs_name" : "";
        }
    }
}
namespace SR2MP
{
    public static class Main
    {
        public static ServerState Server { get; } = new();
        public static ClientState Client { get; } = new();
    }
    public sealed class ServerState { public bool IsRunning { get; set; } }
    public sealed class ClientState
    {
        public bool IsConnected { get; set; }
        public bool IsConnecting { get; set; }
        internal bool IsJoined { get; set; }
    }
}
namespace SR2MP.Components.UI
{
    public sealed class MultiplayerUI
    {
        public static MultiplayerUI Instance { get; } = new();
        private bool hostAutoInProgress;
        private string hostAutoError = "", hostJoinCode = "";
        public int StartCalls { get; private set; }
        internal void StartAutoHost()
        {
            if (hostAutoInProgress) return;
            StartCalls++;
            hostAutoInProgress = true;
            hostAutoError = hostJoinCode = "";
        }
        internal void Complete(string code, bool running)
        {
            hostJoinCode = code;
            SR2MP.Main.Server.IsRunning = running;
            hostAutoInProgress = false;
        }
    }
}
namespace SR2MP.Shared.Utils { public static class JoinCode { } }
