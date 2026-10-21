// Synthetic policy inputs only, not a real foreground-window or Unity test.
using SlimeBridge.Shared;

var checks = 0;
var failures = 0;
foreach (var focused in new[] { false, true })
foreach (var valid in new[] { false, true })
foreach (var own in new uint[] { 0, 11 })
foreach (var other in new uint[] { 0, 11, 22 })
{
    var expected = !focused && valid && own != 0 && other != 0 && own != other;
    var actual = BackgroundGameFocus.Allow(focused, own, other, valid);
    checks++;
    if (actual != expected)
    {
        failures++;
        Console.WriteLine($"FAIL focused={focused} valid={valid} own={own} foreground={other} expected={expected} actual={actual}");
    }
}
Console.WriteLine($"SYNTHETIC focus-policy checks={checks} failures={failures}; OS focus/input not exercised");
Environment.ExitCode = failures == 0 ? 0 : 1;

namespace UnityEngine
{
    public enum RuntimePlatform { WindowsPlayer, LinuxPlayer }
    public static class Application
    {
        public static RuntimePlatform platform = RuntimePlatform.WindowsPlayer;
        public static bool isFocused;
    }
}
