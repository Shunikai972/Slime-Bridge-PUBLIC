using System;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>Opt-in LAB telemetry only. Never supplies an intro authorization decision.</summary>
    internal sealed class LabFocusDiagnostics
    {
        internal static readonly bool Requested = string.Equals(
            Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_FOCUS_DIAGNOSTICS"), "1", StringComparison.Ordinal);
        private const int RowLimit = 120;
        private const double DurationSeconds = 30;
        private const double IntervalSeconds = .25;
        private readonly IFocusReader _reader;
        private readonly Action<string> _log;
        private readonly int _pid;
        private double _start, _next, _last;
        private int _samples;
        internal bool Started { get; private set; }
        internal bool Stopped { get; private set; }

        internal static bool Eligible(bool requested, bool exactLab, bool existingContinue, string? saveGroup) =>
            requested && exactLab && existingContinue && saveGroup != null &&
            Regex.IsMatch(saveGroup, @"\A[A-Za-z0-9_-]{1,128}\z");

        internal static LabFocusDiagnostics? TryCreate(bool exactLab, bool existingContinue, string? saveGroup, Action<string> log) =>
            Eligible(Requested, exactLab, existingContinue, saveGroup)
                ? new LabFocusDiagnostics(new NativeFocusReader(), log, Environment.ProcessId) : null;

        // Test seam is telemetry-only; none of these callbacks enter a production focus gate.
        internal LabFocusDiagnostics(IFocusReader reader, Action<string> log, int pid)
        {
            _reader = reader;
            _log = log;
            _pid = pid;
        }

        internal void Tick(double now, bool exactWorldLoaded)
        {
            if (Stopped) return;
            if (!exactWorldLoaded)
            {
                if (Started) Stop("world-scope-lost");
                return;
            }
            if (!double.IsFinite(now)) { Stop("invalid-clock"); return; }
            if (!Started) { Started = true; _start = _last = now; _next = now; }
            if (now < _last) { Stop("clock-reversed"); return; }
            _last = now;
            if (now - _start >= DurationSeconds) { Stop("deadline"); return; }
            if (_samples >= RowLimit) { Stop("row-limit"); return; }
            if (now < _next) return;
            _next = now + IntervalSeconds;
            var row = Capture();
            row.sample = ++_samples;
            row.time_seconds = now;
            if (!Stopped) Write("lab-focus " + JsonSerializer.Serialize(row));
            if (_samples >= RowLimit) Stop("row-limit");
        }

        internal void Stop(string reason)
        {
            if (Stopped) return;
            Stopped = true;
            Write("lab-focus-end " + JsonSerializer.Serialize(new { samples = _samples, reason = Bound(reason, 80) }));
        }

        private void Write(string line)
        {
            try { _log(line); }
            catch (Exception) { /* Logger failure must not escape a Unity lifecycle callback. */ }
        }

        private FocusRow Capture()
        {
            var row = new FocusRow { own_pid = _pid, utc_time = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture) };
            var stage = "unity_platform";
            try
            {
                row.unity_platform = Bound(_reader.UnityPlatform(), 80);
                stage = "unity_focus_before"; row.unity_focus_before = _reader.UnityFocused();
                stage = "run_in_background"; row.run_in_background = _reader.RunInBackground();
                stage = "hwnd_before"; var before = _reader.ForegroundWindow(); row.hwnd_before = Hex(before);
                stage = "window_owner"; var owner = _reader.WindowOwner(before);
                row.foreground_thread = owner.Thread;
                row.foreground_pid = owner.Pid;
                row.win32_error = owner.Error;
                stage = "hwnd_after"; var after = _reader.ForegroundWindow(); row.hwnd_after = Hex(after);
                row.hwnd_stable = before == after;
                stage = "unity_focus_after"; row.unity_focus_after = _reader.UnityFocused();
            }
            catch (Exception ex)
            {
                row.error_stage = stage;
                row.error_type = Bound(ex.GetType().Name, 128);
                row.error_message = Bound(ex.Message, 160);
            }
            return row;
        }

        private static string Hex(IntPtr handle) => "0x" + unchecked((ulong)handle.ToInt64()).ToString("x", CultureInfo.InvariantCulture);
        private static string Bound(string text, int limit)
        {
            var result = new StringBuilder(Math.Min(text.Length, limit));
            for (var i = 0; i < text.Length && i < limit; i++)
                result.Append(char.IsControl(text[i]) ? ' ' : text[i]);
            return result.ToString();
        }

        internal interface IFocusReader
        {
            string UnityPlatform();
            bool UnityFocused();
            bool RunInBackground();
            IntPtr ForegroundWindow();
            OwnerResult WindowOwner(IntPtr window);
        }

        internal readonly struct OwnerResult
        {
            internal OwnerResult(uint thread, uint pid, int error) { Thread = thread; Pid = pid; Error = error; }
            internal uint Thread { get; }
            internal uint Pid { get; }
            internal int Error { get; }
        }

        // Separate read-only P/Invokes keep the approved shared authorization implementation unchanged.
        private sealed class NativeFocusReader : IFocusReader
        {
            [DllImport("user32.dll", ExactSpelling = true)]
            private static extern IntPtr GetForegroundWindow();
            [DllImport("user32.dll", ExactSpelling = true, SetLastError = true)]
            private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
            public string UnityPlatform()
            {
                var platform = Application.platform;
                return platform + ":" + ((int)platform).ToString(CultureInfo.InvariantCulture);
            }
            public bool UnityFocused() => Application.isFocused;
            public bool RunInBackground() => Application.runInBackground;
            public IntPtr ForegroundWindow() => GetForegroundWindow();
            public OwnerResult WindowOwner(IntPtr window)
            {
                var thread = GetWindowThreadProcessId(window, out var pid);
                return new OwnerResult(thread, pid, thread == 0 ? Marshal.GetLastWin32Error() : 0);
            }
        }

        // Null means not read, never a guessed false/zero. Component values are not atomic authorization.
        private sealed class FocusRow
        {
            public int sample { get; set; }
            public double time_seconds { get; set; }
            public string utc_time { get; set; } = "";
            public int own_pid { get; set; }
            public string? unity_platform { get; set; }
            public bool? unity_focus_before { get; set; }
            public bool? run_in_background { get; set; }
            public string? hwnd_before { get; set; }
            public uint? foreground_thread { get; set; }
            public uint? foreground_pid { get; set; }
            public int? win32_error { get; set; }
            public string? hwnd_after { get; set; }
            public bool? hwnd_stable { get; set; }
            public bool? unity_focus_after { get; set; }
            public string? error_stage { get; set; }
            public string? error_type { get; set; }
            public string? error_message { get; set; }
        }
    }
}
