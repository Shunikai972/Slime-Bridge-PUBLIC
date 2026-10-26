// Compile-only no-op for existing creator/health harnesses whose native fixtures deliberately
// do not model intro UI. The actual helper is production-linked in SR2BackgroundIntroHarness.
namespace SlimeBridge.SR2
{
    internal sealed class BackgroundIntroSkipper
    {
        internal BackgroundIntroSkipper(string name, System.IntPtr director, System.Action<string> log, System.Func<bool>? background = null) { }
        internal bool Stopped => false;
        internal void Tick() { }
        internal void Cancel() { }
    }
}
