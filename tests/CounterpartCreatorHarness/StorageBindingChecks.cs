// Production-linked storage binding and creation gates; synthetic IL2CPP API only.
using Il2CppMonomiPark.SlimeRancher;
using System.Diagnostics;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Il2Cpp;
using SlimeBridge.Shared;
using SlimeBridge.SR2;

internal static class StorageBindingChecks
{
    internal const string Token = "synthetic-storage-token-é";
    private const string Id = "abcdef0123456789abcdef0123456789";
    private static Type BindingType => typeof(CounterpartCreator).Assembly.GetType("SlimeBridge.SR2.NativeStorageBinding")
        ?? throw new InvalidOperationException("owned native storage binding is missing");
    internal static void Run()
    {
        var tests = new List<(string Name, Action<Scope> Check)>
        {
            ("CREATE without a guard refuses before intent", s => { var c = CounterpartCreator.Begin(s.Command, s.Journal, 1, out var r); s.Refused(c, r); }),
            ("exact native class and initialized owned root establish binding", s => { s.Tick(); Require(s.Current() && s.Lines.Count == 1); }),
            ("no pre-Tick baseline is accepted", s => Require(!s.Current())),
            ("stable healthy owner provider and root remain current", s => { s.Tick(); s.Tick(); Require(s.Current() && s.Lines.Count == 1); }),
            ("derived provider casts but fails exact native class", s => { var p = new DerivedFileStorageProvider(); Require(p.TryCast<FileStorageProvider>() != null); s.Director.StorageProvider = p; s.Unready(); }),
            ("missing native class pointer refuses", s => { Il2CppInterop.Runtime.Il2CppClassPointerStore<FileStorageProvider>.NativeClassPtr = IntPtr.Zero; s.Unready(); }),
            ("native class getter exception refuses", s => { s.Provider.OnClassRead = () => throw new IOException(Token); s.Unready(); }),
            ("null native cast refuses", s => { s.Provider.CastUnavailable = true; s.Unready(); }),
            ("cast pointer is not interchangeable", s => { s.Provider.CastOverride = new(); s.Unready(); }),
            ("zero provider pointer refuses", s => { s.Provider.Pointer = IntPtr.Zero; s.Unready(); }),
            ("zero director pointer refuses", s => { s.Director.Pointer = IntPtr.Zero; s.Unready(); }),
            ("null provider refuses", s => { s.Director.StorageProvider = null; s.Unready(); }),
            ("null active owner refuses", s => { GameContext.Instance!.AutoSaveDirector = null; s.Unready(); }),
            ("uninitialized concrete provider refuses", s => { s.Provider.Initialized = false; s.Unready(); }),
            ("empty root refuses", s => { s.Provider.Root = ""; s.Unready(); }),
            ("relative root refuses", s => { s.Provider.Root = "Steam/native-fixture"; s.Unready(); }),
            ("getter root exception refuses", s => { s.Provider.OnRootRead = () => throw new IOException(Token); s.Unready(); }),
            ("initial unready state may establish when initialized", s => { s.Provider.Initialized = false; s.Unready(); s.Provider.Initialized = true; s.Tick(); Require(s.Current()); }),
            ("passed detached director cannot authorize active owner", s => { s.Tick(); Require(!s.Current(new AutoSaveDirector())); }),
            ("owner replacement revokes binding permanently", s => s.Revoked(() => GameContext.Instance!.AutoSaveDirector = new())),
            ("provider replacement with same root revokes permanently", s => s.Revoked(() => s.Director.StorageProvider = new FileStorageProvider { Root = s.Provider.Root })),
            ("root replacement revokes permanently", s => s.Revoked(() => s.Provider.Root += "-changed")),
            ("native class replacement revokes permanently", s => s.Revoked(() => s.Provider.NativeClass = new(0x5678))),
            ("initialization loss revokes permanently", s => s.Revoked(() => s.Provider.Initialized = false)),
            ("null owner after establishment cannot leave valid baseline", s => s.Revoked(() => GameContext.Instance = null)),
            ("provider getter exception invalidates without secret logs", s => s.Revoked(() => s.Director.OnProviderRead = () => throw new IOException(Token))),
            ("main thread guard refuses worker invocation", s => { s.Tick(); Require(!Task.Run(() => s.Current()).GetAwaiter().GetResult()); }),
            ("wire has exactly thirteen fields and native pointer identities", s =>
            {
                s.Tick(); using var json = s.Last(); var v = json.RootElement;
                Require(v.EnumerateObject().Count() == 13 && v.GetProperty("version").GetInt32() == 1 && v.GetProperty("realm").GetString() == "SR2");
                Require(v.GetProperty("pid").GetInt32() == Environment.ProcessId && v.GetProperty("executable").GetString() == s.Executable && v.GetProperty("journal").GetString() == s.Path);
                Require(v.GetProperty("director_pointer").GetString() == s.Director.Pointer.ToInt64().ToString("x16") && v.GetProperty("storage_pointer").GetString() == s.Provider.Pointer.ToInt64().ToString("x16"));
                Require(v.GetProperty("provider_type").GetString() == "FileStorageProvider" && v.GetProperty("initialized").GetBoolean() && v.GetProperty("binding_valid").GetBoolean());
                Require(v.GetProperty("native_root").GetString() == s.Provider.Root && v.GetProperty("sample_id").GetInt64() == 1 && v.GetProperty("sampled_utc_ms").GetInt64() == s.Utc);
            }),
            ("raw UTF8 JSON and HMAC use the existing realm token", s => { s.Provider.Root += "-é-漢字"; s.Tick(); s.VerifySignature(); using var j = s.Last(); Require(j.RootElement.GetProperty("native_root").GetString() == s.Provider.Root && !s.Lines[0].Contains(Token)); }),
            ("sampling bounds allocations until monotonic 500ms", s => { s.Tick(); s.Elapsed = 499; s.Utc += 499; s.Tick(); Require(s.Lines.Count == 1); s.Elapsed = 500; s.Utc++; s.Tick(); Require(s.Lines.Count == 2); s.Fresh(2); }),
            ("UTC clock changes do not bypass monotonic sampling bound", s => { s.Tick(); s.Utc += 100000; s.Tick(); Require(s.Lines.Count == 1); s.Elapsed = 500; s.Tick(); s.Fresh(2); }),
            ("invalidation is signed immediately inside sampling interval", s => { s.Tick(); s.Elapsed = 1; s.Utc++; s.Provider.Initialized = false; s.Tick(); Require(s.Lines.Count == 2); s.VerifySignature(); using var j = s.Last(); Require(!j.RootElement.GetProperty("binding_valid").GetBoolean() && j.RootElement.GetProperty("native_root").GetString() == "" && j.RootElement.GetProperty("provider_type").GetString() == "UNAVAILABLE"); s.Fresh(2); }),
            ("guard predicate publishes fresh invalidation without next Tick", s => { s.Tick(); s.Utc++; s.Provider.Root += "-lost"; Require(!s.Current() && s.Lines.Count == 2); s.Fresh(2); }),
            ("CREATE rejects changed provider since last healthy sample", s => { s.Tick(); s.Director.StorageProvider = new FileStorageProvider(); var c = s.Begin(out var r); s.Refused(c, r); }),
            ("CREATE rejects changed root since last healthy sample", s => { s.Tick(); s.Provider.Root += "-lost"; var c = s.Begin(out var r); s.Refused(c, r); }),
            ("fresh binding recheck after traversal prevents CREATE", s => { s.Tick(); s.Provider.OnNames = () => { if (s.Provider.NameReads == 2) s.Provider.Root += "-lost"; }; var c = s.Begin(out var r); s.Refused(c, r); }),
            ("pending Step rejects provider change before any flush", s => s.PendingRevoked(() => s.Director.StorageProvider = new FileStorageProvider())),
            ("pending Step rejects root change before any flush", s => s.PendingRevoked(() => s.Provider.Root += "-lost")),
            ("pending Step rejects native getter exception before any flush", s => s.PendingRevoked(() => s.Director.OnProviderRead = () => throw new IOException(Token))),
            ("pending Step waits with no valid storage baseline only fail closed", s => { s.Tick(); var c = s.Begin(out var r); Require(c != null && r == null); s.Provider.Initialized = false; Require(c!.Step()!.Uncertain && s.Director.SaveCalls == 0); }),
            ("pre-flush traversal change cannot reach native flush", s => { s.Tick(); var c = s.Begin(out _)!; s.Complete(); s.Provider.OnNames = () => s.Provider.Root += "-lost"; Require(c.Step()!.Uncertain && s.Director.SaveCalls == 0); }),
            ("late initial iterator cannot mutate after root binding loss", s => { s.Tick(); var c = s.Begin(out _)!; s.Provider.Root += "-lost"; Require(!s.Director.Pending!.MoveNext() && s.Director.SavedGame == null && s.Director.SaveCalls == 0 && c.Step()!.Uncertain); }),
            ("CREATE preflight never calls the lazy public provider getter", s => { s.Tick(); s.Director.OnPublicProviderRead = () => throw new IOException(Token); var c = s.Begin(out var r); Require(c != null && r == null && s.Director.DispatchCalls == 1); }),
            ("root loss during native flush cannot return a valid proof", s => { s.Tick(); var c = s.Begin(out _)!; s.Complete(); s.Director.OnSave = () => s.Provider.Root += "-changed"; var r = c.Step()!; Require(r.Uncertain && !r.Persisted && r.Verified == false && s.Director.SaveCalls == 1 && !s.Current()); }),
            ("root loss during final readback cannot return a valid proof", s => { s.Tick(); var c = s.Begin(out _)!; s.Complete(); s.Provider.OnNames = () => { if (s.Provider.NameReads >= 5) s.Provider.Root += "-changed"; }; var r = c.Step()!; Require(r.Uncertain && !r.Persisted && r.Verified == false && s.Director.SaveCalls == 1 && !s.Current()); }),
            ("native exact class not CLR wrapper label determines admission", s => { var wrapper = new StorageProvider { NativeClass = new(0x1234), CastOverride = new FileStorageProvider() }; wrapper.CastOverride.Pointer = wrapper.Pointer; s.Director.StorageProvider = wrapper; s.Tick(); Require(s.Current()); }),
            ("initialized getter exception revokes after establishment", s => s.Revoked(() => s.Provider.OnInitializedRead = () => throw new IOException(Token))),
            ("provider cast failure after establishment is permanent", s => s.Revoked(() => s.Provider.CastUnavailable = true)),
            ("root getter exception revokes without secret logs", s => s.Revoked(() => s.Provider.OnRootRead = () => throw new IOException(Token))),
            ("worker Tick never reads native state", s => { s.Tick(); var touched = false; s.Director.OnProviderRead = () => touched = true; Task.Run(s.Tick).GetAwaiter().GetResult(); Require(!touched); }),
            ("failed signer publication cannot authorize creation", s => { s.Binding = Activator.CreateInstance(BindingType, BindingFlags.Instance | BindingFlags.NonPublic, null, new object[] { Token, s.Executable, s.Path, Environment.ProcessId, (Action<string>)(_ => throw new IOException(Token)), (Func<long>)(() => s.Utc), (Func<long>)(() => s.Elapsed) }, null)!; s.Tick(); Require(!s.Current()); var c = s.Begin(out var r); s.Refused(c, r); }),
            ("production factory refuses missing token", s => { Environment.SetEnvironmentVariable("SLIMEBRIDGE_TOKEN", null); Require(s.Factory(true) == null); }),
            ("production factory refuses missing journal descriptor", s => { Environment.SetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL", null); Require(s.Factory(true) == null); }),
            ("fresh configured journal binds before its first durable intent", s =>
            {
                var fresh = s.Path + "-fresh";
                var journal = OpJournal.Open(fresh, out var error);
                Require(journal != null && error == "" && !File.Exists(fresh));
                Environment.SetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL", fresh);
                s.Binding = s.Factory(true);
                Require(s.Binding != null);
                s.Tick(); Require(s.Current());
                using var json = s.Last(); Require(json.RootElement.GetProperty("journal").GetString() == fresh);
                Require(!File.Exists(fresh)); // Observation/factory must not append an intent or create a save.
            }),
            ("production factory refuses unauthorized scope", s => Require(s.Factory(false) == null)),
            ("production factory requires absolute journal descriptor", s => { Environment.SetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL", "relative.journal"); Require(s.Factory(true) == null); }),
            ("production factory signs actual executable pid and journal", s => { s.Binding = s.Factory(true)!; Require(s.Binding != null); s.Tick(); s.VerifySignature(); using var j = s.Last(); Require(j.RootElement.GetProperty("executable").GetString() == s.Executable); }),
        };
        var failures = 0;
        foreach (var t in tests)
        {
            try { using var s = new Scope(); t.Check(s); Console.WriteLine("PASS " + t.Name); }
            catch (Exception e) { failures++; Console.WriteLine("FAIL " + t.Name + ": " + e.GetBaseException().Message); }
        }
        Console.WriteLine($"{tests.Count - failures}/{tests.Count} storage checks passed (synthetic)");
        if (failures != 0) throw new InvalidOperationException("storage binding harness failed");
    }
    internal static void Require(bool condition) { if (!condition) throw new InvalidOperationException("assertion failed"); }
    internal sealed class Scope : IDisposable
    {
        internal readonly AutoSaveDirector Director;
        internal FileStorageProvider Provider => (FileStorageProvider)Director.StorageProvider!;
        internal readonly string Path = System.IO.Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "binding-" + Guid.NewGuid().ToString("N") + ".journal");
        internal readonly string Executable = Process.GetCurrentProcess().MainModule!.FileName;
        internal readonly OpJournal Journal;
        internal readonly BridgeCommand Command = new() { Kind = "create_save", CreationId = Id, OpId = "binding-create" };
        internal readonly List<string> Lines = new();
        internal long Utc = 1791302400000, Elapsed;
        internal object? Binding;
        private readonly string? _oldToken, _oldJournal;
        internal Scope(bool reset = true)
        {
            Director = reset ? Fixture.Reset() : GameContext.Instance!.AutoSaveDirector!;
            File.WriteAllText(Path, ""); Journal = OpJournal.Open(Path, out var error) ?? throw new Exception(error);
            _oldToken = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN"); _oldJournal = Environment.GetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL");
            Environment.SetEnvironmentVariable("SLIMEBRIDGE_TOKEN", Token); Environment.SetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL", Path);
        }
        internal object GetBinding() => Binding ??= Activator.CreateInstance(BindingType, BindingFlags.Instance | BindingFlags.NonPublic,
            null, new object[] { Token, Executable, Path, Environment.ProcessId, (Action<string>)Lines.Add, (Func<long>)(() => Utc), (Func<long>)(() => Elapsed) }, null)!;
        internal object? Factory(bool allowed) => BindingType.GetMethod("TryCreate", BindingFlags.Public | BindingFlags.Static)!.Invoke(null, new object[] { allowed, (Action<string>)Lines.Add });
        internal void Tick() => BindingType.GetMethod("Tick")!.Invoke(GetBinding(), null);
        internal bool Current(AutoSaveDirector? director = null) => (bool)BindingType.GetMethod("IsCurrent")!.Invoke(GetBinding(), new object[] { director ?? Director })!;
        internal Func<AutoSaveDirector, bool> Guard => d => Current(d);
        internal CounterpartCreator? Begin(out CommandResult? immediate)
        {
            var method = typeof(CounterpartCreator).GetMethod("Begin")!;
            var a = method.GetParameters().Length == 4 ? new object?[] { Command, Journal, 1, null } : new object?[] { Command, Journal, 1, null, Guard };
            var c = (CounterpartCreator?)method.Invoke(null, a); immediate = (CommandResult?)a[3]; return c;
        }
        internal void Refused(CounterpartCreator? c, CommandResult? r) => Require(c == null && r != null && !r.Ok && Director.DispatchCalls == 0 && Director.SaveCalls == 0 && !Journal.HasAny(Command.OpId));
        internal void Unready() { Tick(); Require(!Current() && Lines.Count == 0); }
        internal void Complete() { Require(Director.Pending!.MoveNext()); Fixture.CompleteNewGame(Director); }
        internal void PendingRevoked(Action change) { Tick(); var c = Begin(out var r); Require(c != null && r == null); Complete(); change(); Require(c!.Step()!.Uncertain && Director.SaveCalls == 0); c.Step(); Require(Director.SaveCalls == 0 && Director.DispatchCalls == 1); }
        internal void Revoked(Action change)
        {
            Tick(); var original = Provider; var root = original.Root; change(); Utc++; Tick(); Require(!Current());
            GameContext.Instance = new() { AutoSaveDirector = Director }; Director.StorageProvider = original; Director.OnProviderRead = null;
            original.Root = root; original.Initialized = true; original.NativeClass = new(0x1234); original.OnClassRead = null;
            original.OnInitializedRead = original.OnRootRead = null; original.CastUnavailable = false;
            Elapsed = 1000; Utc += 1000; Tick(); Require(!Current()); using var j = Last(); Require(!j.RootElement.GetProperty("binding_valid").GetBoolean());
            Require(Lines.All(l => !l.Contains(Token)));
        }
        internal JsonDocument Last() => JsonDocument.Parse(Convert.FromBase64String(Lines.Last().Split(' ')[1]));
        internal void VerifySignature()
        {
            var parts = Lines.Last().Split(' '); Require(parts.Length == 3 && parts[0] == "SLIMEBRIDGE_STORAGE_BINDING_V1");
            var raw = Convert.FromBase64String(parts[1]); using var h = new HMACSHA256(new UTF8Encoding(false, true).GetBytes(Token));
            Require(parts[2] == Convert.ToHexString(h.ComputeHash(raw)).ToLowerInvariant());
            var text = new UTF8Encoding(false, true).GetString(raw); Require(new UTF8Encoding(false, true).GetBytes(text).SequenceEqual(raw));
        }
        internal void Fresh(long sample) { using var j = Last(); Require(j.RootElement.GetProperty("sample_id").GetInt64() == sample && j.RootElement.GetProperty("sampled_utc_ms").GetInt64() == Utc); }
        public void Dispose() { File.Delete(Path); Environment.SetEnvironmentVariable("SLIMEBRIDGE_TOKEN", _oldToken); Environment.SetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL", _oldJournal); }
    }
}
