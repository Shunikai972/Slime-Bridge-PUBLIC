// Synthetic full payloads exercise the linked production adapter and real shared disk verifier.
internal static class PersistenceChecks
{
    internal static IEnumerable<(string Name, Action Test)> All()
    {
        yield return ("real Windows root junction refuses before INTENT/mutation/save", () => ReparseBeforeIntent("root"));
        yield return ("real Windows ancestor junction refuses before INTENT/mutation/save", () => ReparseBeforeIntent("ancestor"));
        yield return ("real Windows root junction with no revisions refuses before INTENT", () => ReparseBeforeIntent("empty"));
        yield return ("real Windows revision directory junction refuses before INTENT/mutation/save", () => ReparseBeforeIntent("revision"));
        yield return ("real Windows revision file symlink refuses before INTENT/mutation/save", () => ReparseBeforeIntent("file-symlink"));
        yield return ("root junction introduced after mutation refuses immediately before native save", () => ReparseBeforeSave(false));
        yield return ("revision junction introduced after mutation refuses immediately before native save", () => ReparseBeforeSave(true));
        yield return ("successful replay after a new root junction refuses without repeat effects", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            using var paths = new ReparseFixture(w, "safe");
            var command = w.Command(count: 3);
            var first = w.Run(command); Program.Require(first.Ok && first.Verified == true, "replay fixture needs original full proof");
            var journal = w.JournalBytes(); paths.RedirectAfterMutation(false);
            var method = typeof(Program).GetMethod("TickWire", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic)!;
            var json = (string)method.Invoke(null, new object[] { w.Journal, command })!;
            var replay = System.Text.Json.JsonSerializer.Deserialize<System.Text.Json.JsonElement>(json);
            paths.RequireOutsideUnchanged();
            Program.Require(!replay.GetProperty("ok").GetBoolean() && replay.GetProperty("uncertain").GetBoolean() &&
                !replay.GetProperty("persisted").GetBoolean() && !replay.GetProperty("verified").GetBoolean() &&
                w.Ammo.Mutations.Count == 1 && w.Director.SaveCalls == 1 && journal.SequenceEqual(w.JournalBytes()),
                "reparse replay claimed stale proof or repeated effects/journal");
        });
        yield return ("SaveAllNow false is not persistence proof", () => Bad(w => w.Director.OnSave = () => false));
        yield return ("SaveAllNow true with unchanged names is skipped, not proof", () => Bad(w => w.Director.OnSave = () => true));
        yield return ("ambiguous new revisions cannot prove persistence", () => Bad(w => w.Director.OnSave = () =>
        { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_1", w.Payload()); w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_2", w.Payload()); return true; }));
        yield return ("duplicate revision in entire before set refuses before mutation", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Provider.Names.Add(FixtureWorld.Identity + "_0");
            var r = w.Run(w.Command(count: 3));
            Program.Require(!r.Ok && !r.Uncertain && !r.Applied && !w.HasIntent && w.Ammo.Mutations.Count == 0,
                "ambiguous initial revisions reached intent");
        });
        yield return ("duplicate revision in entire after set fails proof", () => Bad(w => w.Director.OnSave = () =>
        { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_1", w.Payload()); w.Provider.Names.Add(FixtureWorld.Identity + "_0"); return true; }));
        yield return ("reused lower suffix is selected by exact raw difference", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.Provider.Names.Clear(); w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_9", w.Payload());
            w.Director.OnSave = () => { w.Provider.Names.Clear(); w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_0", w.Payload()); return true; };
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.SaveFile == FixtureWorld.Identity + "_0" &&
                w.Provider.PathRequests.Contains(r.SaveFile), "highest suffix selected instead of new revision");
        });
        yield return ("same native raw filename overwritten with new full payload proves persistence", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.Director.OnSave = () => { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_0", w.Payload()); return true; };
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.SaveFile == FixtureWorld.Identity + "_0", "native recycled filename was not detected by full-byte fingerprint");
        });
        yield return ("populated native rotation overwritten lower suffix proves persistence", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_1", w.Payload());
            w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_2", w.Payload());
            w.Director.OnSave = () => { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_0", w.Payload()); return true; };
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.SaveFile == FixtureWorld.Identity + "_0", "populated native rotation became permanent persistence refusal");
        });
        yield return ("foreign new revision never substituted for exact bound save", () => Bad(w => w.Director.OnSave = () =>
        { w.Provider.AddSyntheticRevision("different_1", w.Payload()); return true; }));
        yield return ("saved full parsed count mismatch fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 2) }))));
        foreach (var locked in new[] { true, false })
            yield return ($"matching positive saved ammo in {(locked ? "locked" : "out-of-range")} slot cannot be ignored", () =>
            {
                using var w = locked ? new FixtureWorld((0, 10, true, true), (0, 10, true, false)) : new FixtureWorld((0, 10, true, true));
                var bytes = w.Payload(entries: new (int, int)?[] { (0, 3), (0, 99) });
                w.SavePayload(() => bytes);
                var r = w.Run(w.Command(count: 3));
                Console.WriteLine($"P1 {(locked ? "locked" : "out-of-range")}: saved positive target entries=3,99; expected=3; receipt=" + r.ToJson(Environment.ProcessId));
                var revision = FixtureWorld.Identity + "_1";
                Program.Require(w.Provider.Payloads[revision].SequenceEqual(File.ReadAllBytes(Path.Combine(w.Root, revision + ".sav"))) &&
                    w.Provider.Summaries[revision].Name == FixtureWorld.Identity && FixtureTrace.Loads == 1,
                    "count regression did not use a valid complete payload with identical disk bytes and summary");
                Program.Require(!r.Ok && r.Applied && r.Uncertain && !r.Persisted && r.Verified == false && r.After == 3 &&
                    r.Reason == "native readback identity or count mismatch" && w.HasIntent &&
                    w.Ammo.Mutations.Count == 1 && w.Director.SaveCalls == 1,
                    "matching positive saved ammo outside usable live slots was ignored: incomplete target count verified");
            });
        yield return ("whole target total includes matching live locked ammo", () =>
        {
            using var w = new FixtureWorld((1, 10, true, true), (99, 100, true, false));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.Persisted && r.Before == 100 && r.After == 103,
                "exact total incorrectly projected onto unlocked live slots");
        });
        yield return ("whole saved target total may equal expected across extra slots", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 2), (0, 1) }));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.Persisted && r.After == 3, "exact aggregate saved count semantics changed");
        });
        yield return ("resolved non-target extra ammo does not alter target count", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true), (0, 10, true, false));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (2, 99), (2, 7) }));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.Persisted, "known non-target saved ammo changed exact target count");
        });
        yield return ("unresolved positive out-of-range saved ammo fails closed", () => Bad(w =>
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (1234, 99) }))));
        yield return ("null out-of-range saved ammo fails closed", () => Bad(w =>
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), null }))));
        yield return ("negative out-of-range saved ammo fails closed", () => Bad(w =>
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (0, -1) }))));
        yield return ("whole saved target count overflow fails closed", () => Bad(w =>
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (0, int.MaxValue) }))));
        yield return ("saved GameName mismatch fails proof", () => Bad(w => w.SavePayload(() => w.Payload(name: "different"))));
        yield return ("managed provider bytes must hash-match exact physical file", () => Bad(w => w.SavePayload(() => w.Payload(), () => new byte[] { 1, 2, 3 })));
        yield return ("provider exact disk basename mismatch fails proof", () => Bad(w =>
        { w.Director.OnSave = () => { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_1", w.Payload()); w.Provider.PathOverride = Path.Combine(w.Root, "wrong.sav"); return true; }; }));
        yield return ("full detached load validates payload terminator", () => Bad(w => w.SavePayload(() => w.Payload(end: false))));
        yield return ("full detached load rejects trailing bytes at EOF", () => Bad(w => w.SavePayload(() => w.Payload(trailing: true))));
        yield return ("null saved Player fails proof", () => Bad(w => w.SavePayload(() => w.Payload(player: false))));
        yield return ("null saved ammo collection fails proof", () => Bad(w => w.SavePayload(() => w.Payload(ammo: false))));
        yield return ("null saved ammo entry fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { null }))));
        yield return ("negative saved count fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, -1) }))));
        yield return ("null saved index structure fails proof", () => Bad(w => w.SavePayload(() => w.Payload(table: false))));
        yield return ("null saved index table fails proof", () => Bad(w => w.SavePayload(() => w.Payload(indexTable: false))));
        yield return ("unresolved positive saved persistence id fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (1234, 3) }))));
        yield return ("unresolved positive non-target saved entry fails proof", () => Bad(w =>
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (1234, 1) }))));
        yield return ("unresolved positive locked saved entry also fails proof", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true), (9, 20, true, false));
            w.Ammo.Slots[1]!.Id = w.Other; // Keep unchanged one-slot mutation semantics out of this proof-only test.
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (1234, 9) })); AssertBad(w);
        });
        yield return ("missing save entry for unlocked live slot fails proof", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true), (0, 10, true, true));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3) })); AssertBad(w);
        });
        yield return ("persistence mapping comes from saved index table and live lookup cast", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (1, 3) }, index: new[] { w.Other.ReferenceId, w.Pink.ReferenceId }));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && FixtureTrace.Loads == 1 && FixtureTrace.LookupCasts > 0 &&
                FixtureTrace.SkipPayloadEnd == false && FixtureTrace.Disposals == 2, "detached complete native API route not used");
        });
        yield return ("native read stream EOF is copied into fresh zero-position payload", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.Sha256.Length == 64 && w.Provider.PayloadReads == 1,
                "did not parse provider bytes independently of its stream position");
        });
        yield return ("loaded identity change during save invalidates proof", () => Bad(w =>
        {
            var save = w.Director.OnSave!; w.Director.OnSave = () => { var ok = save(); w.Director._currentGameName = "different"; return ok; };
        }));
        yield return ("loaded saved-game identity change during readback invalidates proof", () => Bad(w =>
            w.Provider.OnRead = () => w.Director._savedGame!.Name = "different"));
        yield return ("live count change during readback invalidates proof", () => Bad(w =>
            w.Provider.OnRead = () => w.Ammo.Slots[0]!.Count++));
        yield return ("player disappears during proof invalidates loaded-world recheck", () => Bad(w =>
            w.Provider.OnRead = () => w.Scene.Player = null));
    }
    private static void ReparseBeforeIntent(string shape)
    {
        using var w = new FixtureWorld((0, 10, true, true));
        using var paths = new ReparseFixture(w, shape);
        var journal = w.JournalBytes();
        var result = w.Run(w.Command(count: 3));
        Console.WriteLine($"P2 preflight {shape}: intent={w.HasIntent}; mutations={w.Ammo.Mutations.Count}; native_saves={w.Director.SaveCalls}; receipt=" + result.ToJson(Environment.ProcessId));
        paths.RequireOutsideUnchanged();
        Program.Require(!result.Ok && !result.Applied && !result.Uncertain && !result.Persisted && result.Verified != true &&
            !w.HasIntent && journal.SequenceEqual(w.JournalBytes()) && w.Ammo.Mutations.Count == 0 && w.Director.SaveCalls == 0,
            "pre-existing reparse path reached INTENT/native mutation/save");
    }
    private static void ReparseBeforeSave(bool revision)
    {
        using var w = new FixtureWorld((0, 10, true, true));
        using var paths = new ReparseFixture(w, "safe");
        w.Ammo.AfterMutation = () => paths.RedirectAfterMutation(revision);
        var result = w.Run(w.Command(count: 3));
        Console.WriteLine($"P2 pre-save {(revision ? "revision" : "root")}: intent={w.HasIntent}; mutations={w.Ammo.Mutations.Count}; native_saves={w.Director.SaveCalls}; receipt=" + result.ToJson(Environment.ProcessId));
        paths.RequireOutsideUnchanged();
        Program.Require(!result.Ok && result.Applied && result.Uncertain && !result.Persisted && result.Verified == false &&
            w.HasIntent && w.Ammo.Mutations.Count == 1 && w.Director.SaveCalls == 0,
            "path was not revalidated immediately before native SaveAllNow");
    }
    // All targets are owned scratch. Mandatory real junctions need no symlink privilege.
    private sealed class ReparseFixture : IDisposable
    {
        private readonly FixtureWorld _world;
        private readonly string _outside, _storage, _parked, _revision, _link;
        private readonly Dictionary<string, byte[]> _witness;
        private string? _ownedLink;
        private bool _fileLink;
        internal ReparseFixture(FixtureWorld world, string shape)
        {
            if (!OperatingSystem.IsWindows()) throw new InvalidOperationException("real Windows junction fixture required");
            _world = world;
            _outside = world.Root + " outside witness";
            _storage = Path.Combine(world.Root, "plain native saves");
            _parked = Path.Combine(world.Root, "parked native saves");
            _link = Path.Combine(world.Root, "linked path with spaces");
            Directory.CreateDirectory(_outside); Directory.CreateDirectory(_storage);
            File.WriteAllBytes(Path.Combine(_outside, "sentinel.bin"), new byte[] { 91, 42, 17 });
            _revision = FixtureWorld.Identity + "_0";
            var original = world.Provider.Payloads[_revision];
            File.WriteAllBytes(Path.Combine(_storage, _revision + ".sav"), original);
            world.Provider.savePath = _storage;
            world.Director.OnSave = () => { world.Provider.AddSyntheticRevision(_revision, world.Payload()); return true; };
            try
            {
                if (shape is "root" or "empty")
                {
                    File.WriteAllBytes(Path.Combine(_outside, _revision + ".sav"), original);
                    Junction(_link, _outside); world.Provider.savePath = _link;
                    if (shape == "empty") world.Provider.Names.Clear();
                }
                else if (shape == "ancestor")
                {
                    var child = Path.Combine(_outside, "child saves"); Directory.CreateDirectory(child);
                    File.WriteAllBytes(Path.Combine(child, _revision + ".sav"), original);
                    Junction(_link, _outside); world.Provider.savePath = Path.Combine(_link, "child saves");
                }
                else if (shape == "revision")
                {
                    var path = Path.Combine(_storage, _revision + ".sav"); File.Delete(path); Junction(path, _outside);
                }
                else if (shape == "file-symlink")
                {
                    var witness = Path.Combine(_outside, "outside native witness.sav"); File.WriteAllBytes(witness, original);
                    var path = Path.Combine(_storage, _revision + ".sav"); File.Delete(path);
                    try { File.CreateSymbolicLink(path, witness); _ownedLink = path; _fileLink = true; }
                    catch (Exception ex) when ((ex is UnauthorizedAccessException || ex is IOException) && (ex.HResult & 0xffff) == 1314)
                    { throw new UnavailableFixtureException("file symlink privilege unavailable (Windows 1314), mandatory junctions still run"); }
                    Program.Require((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 &&
                        new FileInfo(path).ResolveLinkTarget(true)?.FullName == witness, "file symlink fixture not real");
                    Console.WriteLine("REAL file symlink verified: " + path);
                }
                else if (shape != "safe") throw new InvalidOperationException("unexpected reparse fixture shape");
                _witness = SnapshotOutside();
            }
            catch { Dispose(); throw; }
        }
        internal void RedirectAfterMutation(bool revision)
        {
            if (revision)
            {
                var path = Path.Combine(_storage, _revision + ".sav"); File.Delete(path); Junction(path, _outside);
            }
            else
            {
                Directory.Move(_storage, _parked); Junction(_storage, _outside);
            }
        }
        private void Junction(string link, string target)
        {
            var start = new System.Diagnostics.ProcessStartInfo("cmd.exe") { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
            foreach (var argument in new[] { "/d", "/c", "mklink", "/J", link, target }) start.ArgumentList.Add(argument);
            using var process = System.Diagnostics.Process.Start(start) ?? throw new InvalidOperationException("mklink process unavailable");
            using var output = new MemoryStream(); using var error = new MemoryStream();
            var readOutput = process.StandardOutput.BaseStream.CopyToAsync(output); var readError = process.StandardError.BaseStream.CopyToAsync(error);
            process.WaitForExit(); Task.WaitAll(readOutput, readError);
            Console.WriteLine($"mklink /J exit={process.ExitCode}; stdout_base64={Convert.ToBase64String(output.ToArray())}; stderr_base64={Convert.ToBase64String(error.ToArray())}");
            if (process.ExitCode != 0) throw new InvalidOperationException("mandatory Windows junction creation failed");
            _ownedLink = link;
            Program.Require((File.GetAttributes(link) & FileAttributes.ReparsePoint) != 0 &&
                new DirectoryInfo(link).ResolveLinkTarget(true)?.FullName == target, "junction target/attributes not real");
            Console.WriteLine("REAL Windows junction verified: " + link + " -> " + target);
        }
        private Dictionary<string, byte[]> SnapshotOutside() => Directory.EnumerateFiles(_outside, "*", SearchOption.AllDirectories)
            .ToDictionary(path => Path.GetRelativePath(_outside, path), File.ReadAllBytes, StringComparer.Ordinal);
        internal void RequireOutsideUnchanged()
        {
            var current = SnapshotOutside();
            Program.Require(current.Count == _witness.Count && _witness.All(pair => current.TryGetValue(pair.Key, out var bytes) && pair.Value.SequenceEqual(bytes)),
                "outside witness bytes/files changed through reparse path");
            Console.WriteLine("outside witness unchanged; files=" + current.Count);
        }
        public void Dispose()
        {
            if (_ownedLink != null)
            {
                if (_fileLink) File.Delete(_ownedLink); else Directory.Delete(_ownedLink);
                _ownedLink = null;
            }
            _world.Provider.savePath = _storage;
            if (Directory.Exists(_outside)) Directory.Delete(_outside, true);
        }
    }
    internal sealed class UnavailableFixtureException : Exception
    { internal UnavailableFixtureException(string message) : base(message) { } }
    private static void Bad(Action<FixtureWorld> change)
    {
        using var w = new FixtureWorld((0, 10, true, true)); change(w); AssertBad(w);
    }
    private static void AssertBad(FixtureWorld w)
    {
        var r = w.Run(w.Command(count: 3));
        Program.Require(!r.Ok && r.Uncertain && r.Applied && !r.Persisted && r.Verified != true && r.Count == 3 &&
            w.Ammo.Mutations.Count == 1 && w.Director.SaveCalls == 1, "save boolean/header became persistence proof");
    }
}
