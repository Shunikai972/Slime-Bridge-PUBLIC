// Authored behavioral doubles for the exact statically verified normal branch.
// Native waits, destruction, and release are separate steps; no immediate-close fake.
using Il2CppMonomiPark.SlimeRancher.UI.IntroSequence;
namespace UnityEngine
{
    public class Object : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public bool NativeAlive = true;
        public static implicit operator bool(Object? value) => value is not null && value.NativeAlive && value.Pointer != IntPtr.Zero;
    }
    public enum RuntimePlatform { WindowsPlayer, LinuxPlayer }
    public static class Application
    { public static bool isFocused = false; public static RuntimePlatform platform = RuntimePlatform.WindowsPlayer; }
}
namespace Il2CppMonomiPark.SlimeRancher.Cutscene
{
    public sealed class CutsceneDirector : UnityEngine.Object { public IntroSequenceUIRoot? _uiRoot; }
}
namespace Il2CppMonomiPark.SlimeRancher.UI
{
    public class BaseUI : UnityEngine.Object
    { public UnityEngine.GameObject? gameObject = new(); public bool enabled = true; public bool isActiveAndEnabled => enabled && gameObject?.activeInHierarchy == true; }
    public sealed class UIDirector : UnityEngine.Object
    { public BaseUI? TopStackableUI; public int Pops; }
}
namespace Il2CppMonomiPark.SlimeRancher.UI.IntroSequence
{
    public sealed class TextPanelView : UnityEngine.Object
    {
        public bool Animating = true;
        public int Interrupts, IndicatorEnds;
        public bool IsAnimatingDisplayText() => Animating;
        public void Interrupt() { Interrupts++; Animating = false; }
    }
    public sealed class IntroSequenceUIRoot : Il2CppMonomiPark.SlimeRancher.UI.BaseUI
    {
        public sealed class FrameConfig : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
        { public TextPanelView? TextPanelView = new(); }
        public Il2CppSystem.Collections.Generic.List<FrameConfig>? _frameConfigs = new() { new(), new() };
        public int _frameIndex;
        public bool _isInputEnabled = true;
        public Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase? _currentTween;
        public int Helpers, EndFrames, StartFrames, BackgroundStops, MusicDequeues, EndCoroutines, Closes, Destroys;
        public bool Frozen, CameraEnabled;
        public System.Action? OnHelper;
        public void TryToIterateFrame(int index)
        {
            Helpers++; OnHelper?.Invoke();
            if (!this || !isActiveAndEnabled || !_isInputEnabled || _currentTween != null || index != _frameIndex || index < 0 || index >= _frameConfigs!.Count)
                throw new InvalidOperationException("unguarded native helper invocation");
            var text = _frameConfigs[index].TextPanelView!;
            if (text.IsAnimatingDisplayText()) { text.Interrupt(); return; }
            EndFrames++; text.IndicatorEnds++; BackgroundStops++; _frameIndex++;
            if (_frameIndex >= _frameConfigs.Count)
            { _isInputEnabled = false; MusicDequeues++; EndCoroutines++; return; }
            StartFrames++; _isInputEnabled = false; _currentTween = new();
        }
        public void FinishCameraTransition() { _currentTween = null; } // Later real-time text wait still gates input.
        public void BindTextAfterWait() { _isInputEnabled = true; }
        public void FinishNativeEndWait()
        {
            if (EndCoroutines != 1 || _isInputEnabled) throw new Exception("native end must be scheduled exactly once");
            Closes++; enabled = false; Destroys++; NativeAlive = false;
            Il2Cpp.GameContext.Instance!.UIDirector!.TopStackableUI = null;
            Il2Cpp.GameContext.Instance.UIDirector.Pops++;
            CameraEnabled = true; Frozen = true;
            Il2Cpp.SceneContext.Instance!.CutsceneDirector!._uiRoot = null;
        }
        public void FinishGameplayFade() { Frozen = false; }
        public void EndSequence() => throw new Exception("direct EndSequence is forbidden");
    }
}
