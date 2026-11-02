using System.Threading;
using SlimeBridge.SR2;
using Il2CppMonomiPark.SlimeRancher.UI.IntroSequence;

int checks = 0;
void Check(bool condition, string reason)
{
    if (!condition) throw new InvalidOperationException("FAIL " + reason);
    checks++; Console.WriteLine("PASS " + reason);
}
var root = new IntroSequenceUIRoot();
UnityEngine.Object.Current = root;
CounterpartCreator.Readable = false;
var adapter = new BridgeMod();
UnityEngine.Time.realtimeSinceStartup = 0;
adapter.Step();
Check(root.Calls == 1, "owned native story can begin even when gameplay is not yet readable");
Check(CounterpartCreator.ReadCalls == 0, "story progression does not mint or consult gameplay authority");
UnityEngine.Time.realtimeSinceStartup = 1.1f;
adapter.Step();
Check(root.Calls == 2 && root._frameIndex == 1, "second native press advances the card normally");
adapter.Step();
Check(root.Calls == 2, "cooldown prevents duplicate native advances in one update");
root._isInputEnabled = false;
UnityEngine.Time.realtimeSinceStartup = 2.2f;
adapter.Step();
Check(root.Calls == 2, "native transition input gate remains authoritative");
root._isInputEnabled = true;
adapter.Step();
Check(root.Calls == 3, "next eligible card uses one native call after transition");
var unrequested = new IntroSequenceUIRoot();
UnityEngine.Object.Current = unrequested;
BridgeMod.TravelNewGame = false;
new BridgeMod().Step();
Check(unrequested.Calls == 0, "ordinary player stories are not advanced");
BridgeMod.TravelNewGame = true;
var undispatched = new IntroSequenceUIRoot();
UnityEngine.Object.Current = undispatched;
CounterpartCreator.Readable = true;
new BridgeMod(dispatched: false).Step();
Check(undispatched.Calls == 0, "failed native start never authorizes advancing an unrelated story");
Il2Cpp.GameContext.Instance!.AutoSaveDirector!.Pointer = new IntPtr(43);
new BridgeMod().Step();
Check(undispatched.Calls == 0, "director identity loss retires the local story permission");
Console.WriteLine($"RESULT {checks} passed; 0 failed (managed source-linked contracts only)");

namespace SlimeBridge.SR2
{
    internal static class CounterpartCreator
    {
        internal static bool Readable;
        internal static int ReadCalls;
        internal static bool TryReadWorld(out object? world) { ReadCalls++; world = null; return Readable; }
    }
    internal sealed class Log { internal void Msg(string text) { } }
    internal sealed class BridgeMod
    {
        internal static bool TravelNewGame = true;
        private bool _autoloadRequested = true, _newGameDispatched;
        private IntPtr _newGameDirector = new IntPtr(42);
        private bool _introEnded;
        private int _introPresses;
        private float _nextIntroPress;
        private float _newGameStartedAt = -1f;
        private int _introIndex = -1, _introIndexPresses;
        private static bool _storyBlocking;
        private readonly Log LoggerInstance = new();
        internal BridgeMod(bool dispatched = true) { _newGameDispatched = dispatched; }
        private static string Since() => "synthetic";
        internal void Step() => MaybeEndNewGameIntro();
        // PRODUCTION_METHOD
    }
}
namespace UnityEngine
{
    internal static class Time { internal static float realtimeSinceStartup; }
    internal static class Object
    {
        internal static object? Current;
        internal static T? FindObjectOfType<T>() where T : class => Current as T;
    }
}
namespace Il2Cpp
{
    internal sealed class AutoSaveDirector { internal IntPtr Pointer = new IntPtr(42); }
    internal sealed class GameContext
    {
        internal static GameContext? Instance = new();
        internal AutoSaveDirector? AutoSaveDirector = new();
    }
}
namespace Il2CppMonomiPark.SlimeRancher.UI.IntroSequence
{
    internal sealed class IntroSequenceUIRoot
    {
        internal bool _isInputEnabled = true;
        internal int _frameIndex, Calls;
        internal void TryToIterateFrame(int index) { Calls++; if (Calls % 2 == 0) _frameIndex++; }
    }
}
