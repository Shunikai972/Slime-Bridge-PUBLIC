using System;
using UnityEngine;

sealed class Portal
{
    private object? _root;
    private bool _visualFocused;
    private float _visualRetryAt;
    private int _visualAttempts;
    private const float NativeRetrySeconds = 8f;
    private const int NativeRetryLimit = 4;
    private int _scans;
    private bool _nativePartsLoaded;
    private readonly TravelRealm? _travel = new();
    private readonly TravelOverlay _ownOverlay = new();
    private TravelOverlay Overlay => _travel?.Overlay ?? _ownOverlay;
    private bool _markerReady, _inside, _paid;
    private float _lastCross = -100f, _nextHint;
    private const float PadRadius = 2.3f, HintRadius = 14f;
    private readonly int _cost = 10;
    private readonly string _saveId = "fixture";
    private readonly Vector3 _anchor = default;
    private const string PortalId = "fixture-portal";

    private Transform PlayerTransform() => Il2Cpp.SceneContext.Instance.Player;
    private void SaveState() { }
    private void Note(string _) { }

    // PRODUCTION_METHOD

    private void Build()
    {
        _scans++;
        if (_nativePartsLoaded) _root = new object();
    }

    private static void Require(bool value, string reason)
    {
        if (!value) throw new InvalidOperationException(reason);
    }

    public static void Main()
    {
        var portal = new Portal();
        portal.RefreshVisual(100f, false);
        Require(portal._scans == 0 && portal._root == null, "never scan native renderers in the background");
        portal.RefreshVisual(101f, true);
        Require(portal._scans == 1 && portal._root == null, "missing native parts must not cache an empty visual");
        portal._markerReady = true;
        portal.Gui();
        Require(GUI.Draws == 1 && GUI.Text.Contains("Portal to Slime Rancher"), "no marker drawn with zero native parts");
        Camera.main!.Point = new Vector3(300f, 220f, -3f);
        portal.Gui();
        Require(GUI.Draws == 1, "drew a marker behind the camera");
        Camera.main.Point = new Vector3(300f, 220f, 3f);
        Application.isFocused = false;
        portal.Gui();
        Require(GUI.Draws == 1, "unfocused marker drawn");
        Application.isFocused = true;
        portal.RefreshVisual(102f, true);
        Require(portal._scans == 1, "failed build cools down");
        portal.RefreshVisual(109f, true);
        portal.RefreshVisual(117f, true);
        portal.RefreshVisual(125f, true);
        portal.RefreshVisual(140f, true);
        Require(portal._scans == 4 && portal._root == null, "missing parts must not cause unbounded scanning");
        portal._nativePartsLoaded = true;
        portal.RefreshVisual(141f, false);
        portal.RefreshVisual(142f, true);
        portal.RefreshVisual(145f, true);
        Require(portal._scans == 5 && portal._root != null, "restoring the game retries and retains the complete portal");
        portal.Gui();
        Require(GUI.Draws == 1, "fallback drawn over native portal");

        var scene = Il2Cpp.SceneContext.Instance;
        scene.Player.position = new Vector3(6, 0, 0);
        portal.Interact();
        Application.isFocused = false;
        scene.Player.position = default;
        portal.Interact();
        Application.isFocused = true;
        portal.Interact();
        portal.Interact();
        Require(portal._travel!.Crossings == 0 && scene.PlayerState.Newbucks() == 10, "focus change alone charged or crossed the portal");
        scene.Player.position = new Vector3(6, 0, 0);
        portal.Interact();
        scene.Player.position = default;
        portal.Interact();
        Require(portal._travel!.Crossings == 1 && scene.PlayerState.Newbucks() == 0, "real off-pad to on-pad entry did not cross exactly once");
        Console.WriteLine("portal visual retry OK");
    }
}

sealed class TravelRealm
{
    public bool Busy { get; set; }
    public TravelOverlay Overlay { get; } = new();
    public int Crossings;
    public void RequestCrossing(string _, string __) => Crossings++;
}
sealed class TravelOverlay
{
    public void Draw() { }
    public void ShowHint(string _, float __) { }
}
static class TravelText
{
    public static bool IsFrench() => false;
    public static string Failure(string reason, string realm) => reason;
    public static string Opening(string realm) => realm;
}
namespace Il2Cpp
{
    sealed class SceneContext
    {
        public static SceneContext Instance { get; } = new();
        public Transform Player { get; } = new();
        public PlayerState PlayerState { get; } = new();
    }
    sealed class PlayerState
    {
        private int _money = 10;
        public int Newbucks() => _money;
        public bool SpendNewbucks(int cost) { _money -= cost; return true; }
    }
}
namespace UnityEngine
{
    readonly record struct Vector3(float x, float y, float z)
    {
        public static Vector3 up => new(0, 1, 0);
        public float magnitude => MathF.Sqrt(x * x + y * y + z * z);
        public static Vector3 operator +(Vector3 a, Vector3 b) => new(a.x + b.x, a.y + b.y, a.z + b.z);
        public static Vector3 operator *(Vector3 a, float b) => new(a.x * b, a.y * b, a.z * b);
    }
    sealed class Transform { public Vector3 position; }
    sealed class Camera
    {
        public static Camera? main = new();
        public Vector3 Point = new(300f, 220f, 3f);
        public Vector3 WorldToScreenPoint(Vector3 _) => Point;
    }
    sealed class Event
    {
        public static Event current { get; } = new();
        public EventType type => EventType.Repaint;
    }
    enum EventType { Repaint }
    enum SystemLanguage { English, French }
    static class Application
    {
        public static bool isFocused = true;
        public static SystemLanguage systemLanguage => SystemLanguage.English;
    }
    static class Screen { public const int width = 800, height = 600; }
    readonly record struct Rect(float x, float y, float width, float height);
    static class GUI
    {
        public static int Draws;
        public static string Text = "";
        public static void Box(Rect _, string label) { Draws++; Text = label; }
    }
    static class Time { public static float realtimeSinceStartup => 100f; }
    static class Mathf { public static float Abs(float value) => MathF.Abs(value); }
}
