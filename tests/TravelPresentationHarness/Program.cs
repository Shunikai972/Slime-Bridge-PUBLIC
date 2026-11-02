using System;
using System.Reflection;
using SlimeBridge.Shared;
using UnityEngine;

static class Program
{
    static void Require(bool ok, string why) { if (!ok) throw new Exception(why); }
    static void Reset() { Application.targetFrameRate = 75; QualitySettings.vSyncCount = 1; QualitySettings.Level = 3;
        Time.timeScale = 1f; AudioListener.volume = .37f; AudioListener.pause = false; QualitySettings.FailRestore = false; }
    static object? Invoke(TravelThrottle throttle, string method, params object[] arguments)
    {
        var info = typeof(TravelThrottle).GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic);
        Require(info != null, "Missing production behavior: " + method);
        return info!.Invoke(throttle, arguments);
    }
    static int Main(string[] args)
    {
        Reset(); var throttle = new TravelThrottle();
        if (args[0] == "waiting")
        {
            Invoke(throttle, "MuteAudio"); Require(throttle.Active, "Silent waiting needs host-loss recovery");
            Require(AudioListener.volume == 0f, "Destination music leaked during loading");
            Require(!AudioListener.pause && Time.timeScale == 1f && Application.targetFrameRate == 75 && QualitySettings.Level == 3,
                "Audio-only waiting must not pause or throttle destination loading");
            AudioListener.volume = .8f; Invoke(throttle, "MuteAudio"); throttle.Restore();
            Require(AudioListener.volume == .37f && !throttle.Active, "Repeated mute replaced the user's volume snapshot");
        }
        else if (args[0] == "maintain")
        {
            throttle.Apply(true); AudioListener.volume = 1f; AudioListener.pause = false;
            Invoke(throttle, "Maintain"); Require(AudioListener.volume == 0f && AudioListener.pause, "Game reset its audio while parked");
            throttle.Restore(); Require(AudioListener.volume == .37f && !AudioListener.pause, "Source volume not restored");
        }
        else if (args[0] == "quality-fault")
        {
            throttle.Apply(true); QualitySettings.FailRestore = true; throttle.Restore();
            Require(AudioListener.volume == .37f && !AudioListener.pause && Application.targetFrameRate == 75,
                "Quality restore failure prevented independent audio/FPS restore");
        }
        else if (args[0] == "quality-writer")
        {
            throttle.Apply(true); throttle.Restore();
            Require(AudioListener.volume == .37f, "Quality restoration overwrote the restored audio volume");
        }
        else if (args[0] == "focus-edge")
        {
            throttle.Apply(true);
            Require(!(bool)Invoke(throttle, "ReturnedByHand", true, 0f)!, "Fresh hold must not resume");
            Require(!(bool)Invoke(throttle, "ReturnedByHand", true, 20f)!, "Stale source foreground unmuted a parked game");
            Require(!(bool)Invoke(throttle, "ReturnedByHand", false, 21f)!, "Background is not a return");
            Require(!(bool)Invoke(throttle, "ReturnedByHand", true, 22f)!, "Return dwell not met");
            Require((bool)Invoke(throttle, "ReturnedByHand", true, 27f)!, "Genuine manual return should recover");
        }
        else throw new Exception("Unknown case");
        Console.WriteLine("PASS " + args[0]); return 0;
    }
}
namespace UnityEngine
{
    static class Application { public static int targetFrameRate; }
    static class Time { public static float timeScale; }
    static class AudioListener { public static float volume; public static bool pause; }
    static class QualitySettings
    {
        public static int vSyncCount, Level; public static bool FailRestore;
        public static int GetQualityLevel() => Level;
        public static void SetQualityLevel(int level, bool expensive)
        {
            if (FailRestore && level == 3) throw new Exception("synthetic quality restoration fault");
            Level = level;
            if (level == 3) AudioListener.volume = .9f; // Model a native settings writer, not evidence it occurs in-game.
        }
    }
    class Camera { public static Camera[] allCameras => Array.Empty<Camera>(); public bool enabled; public object? targetTexture; }
}
