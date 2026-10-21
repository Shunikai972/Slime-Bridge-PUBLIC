using SlimeBridge.Shared;

int checks = 0;
void Check(bool ok, string description)
{
    checks++;
    if (!ok) throw new Exception("FAIL: " + description);
}
var rows = new List<RegistryRow> { new RegistryRow {
    ObjectRef = "R:a", NativeId = 7, ReferenceId = "Aé\"", Name = "Aé\"", CanLargofy = true,
    ProducedPlorts = new List<string> { "P" }, PrefabRefs = new List<string> { "Aé\"Prefab" } } };
Check(RegistrySnapshotFormat.Completeness(0, new List<RegistryRow>(), new List<string>(), false) != "COMPLETE",
    "empty registry never proves COMPLETE");
Check(RegistrySnapshotFormat.Completeness(1, rows, new List<string>(), false) == "COMPLETE", "valid row complete");
Check(RegistrySnapshotFormat.Completeness(2, rows, new List<string>(), false) == "PARTIAL", "truncated array partial");
Check(RegistrySnapshotFormat.Completeness(1, rows, new List<string>(), true) == "FAILED", "index mismatch failed");
Check(RegistrySnapshotFormat.Completeness(1, rows, new List<string> { "missing" }, false) == "PARTIAL", "error partial");
Check(RegistrySnapshotFormat.Quote("é\"\\\n\t\b\f" + (char)13 + "😀") ==
    "\"\\u00e9\\\"\\\\\\n\\t\\b\\f\\r\\ud83d\\ude00\"", "Python-compatible escaping");
var fixture = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "registry-gate-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(fixture);
try
{
    var lab = Path.Combine(fixture, "Lab");
    var game = Path.Combine(lab, "Games", "SR2");
    var output = Path.Combine(lab, "RuntimeArtifacts", "capture-test");
    Directory.CreateDirectory(game);
    Directory.CreateDirectory(output);
    var exe = Path.Combine(game, "SlimeRancher2.exe");
    File.WriteAllText(exe, "synthetic");
    File.WriteAllText(Path.Combine(game, "slimebridge-lab.json"), "synthetic marker");
    Check(RegistrySnapshotFormat.ValidateCaptureLocation("SR2", exe, exe, output, "capture-test") == Path.GetFullPath(output), "exact LAB output accepted");
    foreach (var invalid in new[] { fixture, game, Path.Combine(lab, "RuntimeArtifacts") })
    {
        bool refused = false;
        try { RegistrySnapshotFormat.ValidateCaptureLocation("SR2", exe, exe, invalid, "capture-test"); }
        catch { refused = true; }
        Check(refused, "non-capture output refused");
    }
    bool mismatch = false;
    try { RegistrySnapshotFormat.ValidateCaptureLocation("SR1", exe, exe, output, "capture-test"); }
    catch { mismatch = true; }
    Check(mismatch, "wrong realm layout refused");
    using (RegistrySnapshotFormat.PinCaptureDirectory(output))
    {
        bool locked = false;
        try { Directory.Move(output, output + "-moved"); }
        catch (IOException) { locked = true; }
        Check(locked, "opened directory lease denies rename");
    }
    Directory.Move(output, output + "-moved");
    Directory.Move(output + "-moved", output);
    Check(Directory.Exists(output), "lease handles released deterministically");
    RegistrySnapshotFormat.PublishCaptureFile(output, RegistrySnapshotFormat.SnapshotFileName, "original");
    bool overwrite = false;
    try { RegistrySnapshotFormat.PublishCaptureFile(output, RegistrySnapshotFormat.SnapshotFileName, "replaced"); }
    catch (IOException) { overwrite = true; }
    Check(overwrite && File.ReadAllText(Path.Combine(output, RegistrySnapshotFormat.SnapshotFileName)) == "original", "overwrite refused without changing existing bytes");
    Check(!Directory.EnumerateFiles(output, "*.tmp").Any(), "no publication temporary leftovers");
    var existingDir = Path.Combine(output, RegistrySnapshotFormat.ReceiptFileName);
    Directory.CreateDirectory(existingDir);
    File.WriteAllText(Path.Combine(existingDir, "keep.txt"), "keep");
    bool dirRefused = false;
    try { RegistrySnapshotFormat.PublishCaptureFile(output, RegistrySnapshotFormat.ReceiptFileName, "replaced"); }
    catch (IOException) { dirRefused = true; }
    Check(dirRefused && Directory.Exists(existingDir) && File.ReadAllText(Path.Combine(existingDir, "keep.txt")) == "keep",
        "existing destination directory preserved and refused");
}
finally { Directory.Delete(fixture, true); }
Check(RegistrySnapshotFormat.Completeness(2, new List<RegistryRow> { rows[0], rows[0] }, new List<string>(), false) != "COMPLETE", "duplicate rows never complete");
Check(RegistrySnapshotFormat.Completeness(1, new List<RegistryRow> { new RegistryRow { ObjectRef = "L", ReferenceId = "L", IsLargo = true, BaseSlimes = new List<string> { "missing", "other" } } }, new List<string>(), false) != "COMPLETE", "unresolved Largo never complete");
var invalidLargo = new List<RegistryRow> {
    new RegistryRow { ObjectRef = "A", ReferenceId = "A" },
    new RegistryRow { ObjectRef = "L", ReferenceId = "L", IsLargo = true, BaseSlimes = new List<string> { "A", "L" } } };
Check(RegistrySnapshotFormat.Completeness(2, invalidLargo, new List<string>(), false) != "COMPLETE", "Largo self-parent refused");
RegistryLifetimeChecks.Run(Check);
RegistryCleanupChecks.Run(Check);
RegistryBoundaryChecks.Run(Check);
RegistryRaceChecks.Run(Check);
Console.WriteLine("Canonical: " + RegistrySnapshotFormat.RegistrySha256(rows));
// Optional real C# serialization → independent Python reader cross-language fixture.
if (args.Length == 2 || args.Length == 3)
{
    if (args.Length == 3 && args[2] == "unicode")
        rows = new List<RegistryRow> {
            new RegistryRow { ObjectRef = "R:\ue000", NativeId = 1, ReferenceId = "A", Name = "A", CanLargofy = true },
            new RegistryRow { ObjectRef = "R:😀", NativeId = 2, ReferenceId = "B", Name = "B", CanLargofy = true } };
    var output = Path.GetFullPath(args[0]);
    var buildPath = Path.GetFullPath(args[1]);
    var buildHash = RegistrySnapshotFormat.Sha256Hex(File.ReadAllBytes(buildPath));
    var counts = new Dictionary<string, long> { { "slimes_array", rows.Count }, { "by_identifiable", rows.Count } };
    File.WriteAllText(Path.Combine(output, RegistrySnapshotFormat.SnapshotFileName),
        RegistrySnapshotFormat.SnapshotJson("SR1", "synthetic-run", buildHash, buildHash, buildPath,
            "synthetic C# harness", "synthetic:registry", rows.Count, rows, new List<string>(), counts, "COMPLETE"));
    File.WriteAllText(Path.Combine(output, RegistrySnapshotFormat.ReceiptFileName),
        RegistrySnapshotFormat.ReceiptJson("SR1", "synthetic-run", buildHash, "synthetic:registry", rows.Count, rows, counts));
}
Console.WriteLine("Registry capture harness: " + checks + " checks passed (synthetic)");
