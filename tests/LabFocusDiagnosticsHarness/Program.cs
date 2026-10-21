// Synthetic production-linked diagnostic tests. No Unity or User32 calls.
using System.Text.Json;
using SlimeBridge.SR2;

var checks = 0;
void Check(bool value, string label) { checks++; if (!value) throw new Exception(label); }
List<JsonElement> Rows(List<string> lines) => lines.Where(x => x.StartsWith("lab-focus ", StringComparison.Ordinal))
    .Select(x => JsonDocument.Parse(x[10..]).RootElement.Clone()).ToList();

foreach (var requested in new[] { false, true })
foreach (var lab in new[] { false, true })
foreach (var continued in new[] { false, true })
    Check(LabFocusDiagnostics.Eligible(requested, lab, continued, "existing_1") == (requested && lab && continued), "mode gate");
foreach (var bad in new string?[] { null, "", "../x", "x.sav", "x\n", new string('x', 129) })
    Check(!LabFocusDiagnostics.Eligible(true, true, true, bad), "invalid existing save identity");

var reader = new FakeReader(); var lines = new List<string>();
var sampler = new LabFocusDiagnostics(reader, lines.Add, 17);
sampler.Tick(0, false); Check(reader.Reads == 0 && lines.Count == 0, "no reads before exact world");
sampler.Tick(10, true); var row = Rows(lines).Single();
Check(row.GetProperty("own_pid").GetInt32() == 17, "own pid");
Check(row.GetProperty("unity_platform").GetString() == "WindowsPlayer:2", "raw platform");
Check(!row.GetProperty("unity_focus_before").GetBoolean() && !row.GetProperty("unity_focus_after").GetBoolean(), "both focus reads");
Check(row.GetProperty("run_in_background").GetBoolean(), "raw run-in-background");
Check(row.GetProperty("hwnd_stable").GetBoolean(), "stable hwnd");
Check(row.GetProperty("foreground_pid").GetUInt32() == 23 && row.GetProperty("foreground_thread").GetUInt32() == 29, "owner fields");
Check(!row.TryGetProperty("authorized", out _), "diagnostics never grant authorization");
sampler.Tick(10.1, true); Check(Rows(lines).Count == 1, "cadence does not catch up");
sampler.Tick(10.25, true); Check(Rows(lines).Count == 2, "quarter-second sample");
sampler.Tick(10.5, false); var reads = reader.Reads;
Check(sampler.Stopped, "world loss stops"); sampler.Tick(11, true); Check(reader.Reads == reads, "no read after world loss");

foreach (var zero in new[] { "hwnd", "thread", "pid" }) {
    reader = new FakeReader { Zero = zero }; lines = new(); sampler = new(reader, lines.Add, 17); sampler.Tick(0, true);
    row = Rows(lines).Single();
    Check(zero == "hwnd" ? row.GetProperty("hwnd_before").GetString() == "0x0" :
          zero == "thread" ? row.GetProperty("foreground_thread").GetUInt32() == 0 :
          row.GetProperty("foreground_pid").GetUInt32() == 0, "zero remains zero: " + zero);
}
reader = new FakeReader { Changed = true, FocusChanged = true, Platform = "LinuxPlayer:13" };
lines = new(); sampler = new(reader, lines.Add, 17); sampler.Tick(0, true); row = Rows(lines).Single();
Check(!row.GetProperty("hwnd_stable").GetBoolean(), "changed hwnd visible");
Check(row.GetProperty("unity_focus_after").GetBoolean(), "focus change visible");
Check(row.GetProperty("unity_platform").GetString() == "LinuxPlayer:13", "unsupported platform remains visible");

foreach (var stage in new[] { "unity_platform", "unity_focus_before", "run_in_background", "hwnd_before", "window_owner", "hwnd_after", "unity_focus_after" }) {
    reader = new FakeReader { ThrowAt = stage }; lines = new(); sampler = new(reader, lines.Add, 17); sampler.Tick(0, true);
    row = Rows(lines).Single();
    Check(row.GetProperty("error_stage").GetString() == stage, "exception stage: " + stage);
    Check(row.GetProperty("error_type").GetString() == nameof(InvalidOperationException), "exception type");
    var message = row.GetProperty("error_message").GetString()!;
    Check(message.Length <= 160 && !message.Any(char.IsControl), "bounded single-line message");
    Check(row.GetProperty("unity_focus_after").ValueKind == JsonValueKind.Null, "unread value remains unknown");
}
reader = new(); lines = new(); sampler = new(reader, lines.Add, 17);
for (var i = 0; i < 500; i++) sampler.Tick(i * .25, true);
Check(Rows(lines).Count == 120 && sampler.Stopped, "hard row cap");
Check(reader.Reads == 120 * 7, "no native reads after row cap");
reader = new(); lines = new(); sampler = new(reader, lines.Add, 17); sampler.Tick(5, true); sampler.Tick(35, true);
Check(sampler.Stopped && Rows(lines).Count == 1, "hard thirty-second deadline");
reader = new(); lines = new(); sampler = new(reader, lines.Add, 17); sampler.Tick(5, true); sampler.Tick(4, true);
Check(sampler.Stopped && Rows(lines).Count == 1, "clock reversal stops");
foreach (var badClock in new[] { double.NaN, double.PositiveInfinity }) {
    reader = new(); sampler = new(reader, _ => { }, 17); sampler.Tick(badClock, true);
    Check(sampler.Stopped && reader.Reads == 0, "nonfinite clock refuses");
}
reader = new(); sampler = new(reader, _ => throw new IOException("synthetic closed logger"), 17);
sampler.Tick(0, true); sampler.Stop("unload"); reads = reader.Reads; sampler.Tick(1, true);
Check(sampler.Stopped && reader.Reads == reads, "logger failure/unload contained");
Console.WriteLine($"SYNTHETIC LAB focus diagnostic checks={checks} failures=0; no Unity/User32/input/CREATE exercised");

sealed class FakeReader : LabFocusDiagnostics.IFocusReader {
    public int Reads;
    public string? ThrowAt, Zero;
    public string Platform = "WindowsPlayer:2";
    public bool Changed, FocusChanged;
    int windows, focus;
    void Read(string stage) { Reads++; if (ThrowAt == stage) throw new InvalidOperationException("line1\nline2\r" + new string('x', 300)); }
    public string UnityPlatform() { Read("unity_platform"); return Platform; }
    public bool UnityFocused() { Read(focus++ == 0 ? "unity_focus_before" : "unity_focus_after"); return FocusChanged && focus > 1; }
    public bool RunInBackground() { Read("run_in_background"); return true; }
    public IntPtr ForegroundWindow() { Read(windows++ == 0 ? "hwnd_before" : "hwnd_after"); return new IntPtr(Zero == "hwnd" ? 0 : Changed && windows > 1 ? 8 : 7); }
    public LabFocusDiagnostics.OwnerResult WindowOwner(IntPtr handle) { Read("window_owner"); return new(Zero == "thread" ? 0u : 29u, Zero == "pid" ? 0u : 23u, 0); }
}
namespace UnityEngine {
    public enum RuntimePlatform { WindowsPlayer = 2 }
    public static class Application {
        public static RuntimePlatform platform => throw new Exception("real Unity adapter must not run in synthetic tests");
        public static bool isFocused => throw new Exception("real Unity adapter must not run in synthetic tests");
        public static bool runInBackground => throw new Exception("real Unity adapter must not run in synthetic tests");
    }
}
