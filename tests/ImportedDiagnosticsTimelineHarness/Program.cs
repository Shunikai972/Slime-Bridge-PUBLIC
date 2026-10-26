// CPU boundary over DiagnosticsTimeline: strict seconds parsing, the token guard and the
// compact JSONL line shape validated with real System.Text.Json. No Unity runtime exercised:
// a pass means the LAB timeline kept its bounded fail-closed contract inside this model.
using System.Text.Json;
using SlimeBridge.SR1;

var checks = 0;
var failures = 0;
void Check(bool condition, string issue)
{
    checks++;
    if (condition) return;
    failures++;
    Console.Error.WriteLine("FAIL: " + issue);
}

Check(DiagnosticsTimeline.MinSeconds == 5 && DiagnosticsTimeline.MaxSeconds == 900 && DiagnosticsTimeline.MaxWrites == 256,
    "bounds constants drifted from the reviewed contract");
Check(DiagnosticsTimeline.SecondsVariable == "SLIMEBRIDGE_DIAG_TIMELINE_SECONDS", "seconds variable drifted");

Check(!DiagnosticsTimeline.TryParseSeconds(null, out var s0, out var p0) && s0 == 0 && p0 == null,
    "absent value must stay disabled without a problem");
Check(!DiagnosticsTimeline.TryParseSeconds("", out var s1, out var p1) && s1 == 0 && p1 == null,
    "empty value must stay disabled without a problem");
Check(DiagnosticsTimeline.TryParseSeconds("60", out var s2, out var p2) && s2 == 60 && p2 == null,
    "60 must enable at 60 seconds");
Check(DiagnosticsTimeline.TryParseSeconds("5", out var s3, out _) && s3 == 5, "5 must enable (lower bound)");
Check(DiagnosticsTimeline.TryParseSeconds("900", out var s4, out _) && s4 == 900, "900 must enable (upper bound)");
Check(!DiagnosticsTimeline.TryParseSeconds("4", out _, out var p5) && p5 != null, "4 must be refused with a problem");
Check(!DiagnosticsTimeline.TryParseSeconds("901", out _, out var p6) && p6 != null, "901 must be refused with a problem");
Check(!DiagnosticsTimeline.TryParseSeconds(" 60", out _, out var p7) && p7 != null, "leading whitespace must be refused");
Check(!DiagnosticsTimeline.TryParseSeconds("60.5", out _, out var p8) && p8 != null, "fractions must be refused");
Check(!DiagnosticsTimeline.TryParseSeconds("abc", out _, out var p9) && p9 != null, "text must be refused");

Check(DiagnosticsTimeline.IsSafeFileToken("slime.ringtail") && DiagnosticsTimeline.IsSafeFileToken("a-b_c.d2"),
    "safe tokens accepted");
Check(!DiagnosticsTimeline.IsSafeFileToken("..") && !DiagnosticsTimeline.IsSafeFileToken("../x") &&
      !DiagnosticsTimeline.IsSafeFileToken("a/b") && !DiagnosticsTimeline.IsSafeFileToken("") &&
      !DiagnosticsTimeline.IsSafeFileToken("a b"), "unsafe tokens refused");

var line = DiagnosticsTimeline.FormatLine(12.5f, new List<KeyValuePair<string, string>>
{
    new("SlimeBridgeImport_slime.ringtail(Clone)", "{\"state\": \"stone\", \"frozen_count\": 5}"),
    new("Second", "{\"state\": \"alive\", \"frozen_count\": 0}"),
});
Check(!line.Contains('\n') && !line.Contains('\r'), "line must stay single-line");
using (var parsed = JsonDocument.Parse(line))
{
    var root = parsed.RootElement;
    Check(root.GetProperty("elapsed_seconds").GetDouble() == 12.5, "elapsed seconds missing or wrong");
    Check(root.TryGetProperty("utc", out var utc) && utc.GetString()!.EndsWith("Z"), "utc timestamp missing");
    var entries = root.GetProperty("entries");
    Check(entries.GetArrayLength() == 2, "entries count wrong");
    Check(entries[0].GetProperty("instance").GetString() == "SlimeBridgeImport_slime.ringtail(Clone)",
        "first instance name wrong");
    Check(entries[0].GetProperty("petrification").GetProperty("state").GetString() == "stone",
        "first entry state wrong");
    Check(entries[1].GetProperty("petrification").GetProperty("frozen_count").GetInt32() == 0,
        "second entry frozen count wrong");
}
var empty = DiagnosticsTimeline.FormatLine(1f, new List<KeyValuePair<string, string>>());
Check(empty.Contains("\"entries\": []"), "empty entries must stay a valid empty array");

Console.WriteLine("diagnostics timeline checks=" + checks + " failures=" + failures + "; no Unity runtime exercised");
return failures == 0 ? 0 : 1;
