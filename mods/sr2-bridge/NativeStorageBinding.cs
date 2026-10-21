using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Il2CppInterop.Runtime;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Read-only, main-thread observation of the storage actually owned by the active director.
    /// A first initialized observation is pinned for this run; loss/replacement is irreversible.
    /// Signed LAB diagnostics attest this observation, not filesystem confinement or native runtime proof.
    /// </summary>
    internal sealed class NativeStorageBinding
    {
        private const long SampleIntervalMs = 500;
        private const long MaxSampleId = 9007199254740991; // exact integer in the host JSON consumer
        private readonly int _thread = Environment.CurrentManagedThreadId;
        private readonly byte[] _key;
        private readonly string _executable, _journal;
        private readonly int _pid;
        private readonly Action<string> _log;
        private readonly Func<long> _utcMs, _elapsedMs;
        private Observation _pinned;
        private bool _established, _revoked;
        private long _sampleId, _lastEmissionMs;

        internal NativeStorageBinding(string token, string executable, string journal, int pid,
            Action<string> log, Func<long> utcMs, Func<long> elapsedMs)
        {
            _key = new UTF8Encoding(false, true).GetBytes(token);
            _executable = executable; _journal = journal; _pid = pid;
            _log = log; _utcMs = utcMs; _elapsedMs = elapsedMs;
        }

        /// <summary>The caller's exact-LAB executable/opt-in gate is mandatory. No disk discovery.</summary>
        public static NativeStorageBinding? TryCreate(bool labAuthorized, Action<string> log)
        {
            if (!labAuthorized) return null;
            try
            {
                var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
                var journal = Environment.GetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL");
                using var process = Process.GetCurrentProcess();
                var executable = process.MainModule?.FileName;
                // OpJournal.Open can own a fresh descriptor before its first durable WriteIntent
                // creates the file. File.Exists here would deadlock the pre-CREATE root proof.
                // InventoryAgent still requires the actual journal; CREATE journals both intents.
                if (string.IsNullOrEmpty(token) || string.IsNullOrWhiteSpace(journal) ||
                    !Path.IsPathFullyQualified(journal) ||
                    string.IsNullOrWhiteSpace(executable) || !Path.IsPathFullyQualified(executable)) return null;
                var clock = Stopwatch.StartNew();
                return new NativeStorageBinding(token, executable, journal, process.Id, log,
                    () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), () => clock.ElapsedMilliseconds);
            }
            catch (Exception) { return null; } // Never expose token-bearing exception text.
        }

        /// <summary>Called by BridgeMod.OnUpdate BEFORE any inventory/creation tick.</summary>
        public void Tick()
        {
            if (Environment.CurrentManagedThreadId != _thread) return;
            if (_revoked) { EmitIfDue(false); return; }
            if (!TryObserve(out var current)) { Revoke(); return; }
            if (!_established)
            {
                _pinned = current; _established = true;
                Emit(true);
                return;
            }
            if (!Matches(current)) { Revoke(); return; }
            EmitIfDue(true);
        }

        /// <summary>Fresh native re-read, NOT authorization by the last signed sample.</summary>
        public bool IsCurrent(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director)
        {
            if (Environment.CurrentManagedThreadId != _thread || !_established || _revoked) return false;
            if (!TryObserve(out var current) || !Matches(current)) { Revoke(); return false; }
            // An unrelated argument is never authority for the active/pinned owner.
            return director != null && director.Pointer != IntPtr.Zero && director.Pointer == current.Director;
        }

        private bool Matches(Observation current) => current.Director == _pinned.Director &&
            current.Provider == _pinned.Provider && string.Equals(current.Root, _pinned.Root, StringComparison.Ordinal);

        private static bool TryObserve(out Observation observation)
        {
            observation = default;
            try
            {
                var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
                if (director == null || director.Pointer == IntPtr.Zero) return false;
                // The backing-field getter reads the existing provider; no lazy platform getter/constructor.
                var provider = director._storageProvider;
                if (provider == null || provider.Pointer == IntPtr.Zero) return false;
                var expected = Il2CppClassPointerStore<Il2Cpp.FileStorageProvider>.NativeClassPtr;
                if (expected == IntPtr.Zero || provider.ObjectClass != expected) return false;
                // TryCast accepts subclasses: exact native ObjectClass equality above is independently required.
                var file = provider.TryCast<Il2Cpp.FileStorageProvider>();
                if (file == null || file.Pointer != provider.Pointer || !file.isInitialized) return false;
                var root = file.savePath; // Readonly native field; NEVER use the generated wrapper setter.
                if (string.IsNullOrWhiteSpace(root) || !Path.IsPathFullyQualified(root)) return false;
                _ = Path.GetFullPath(root); // Validate syntax only; preserve the exact native field value on wire.
                observation = new Observation(director.Pointer, provider.Pointer, root);
                return true;
            }
            catch (Exception) { return false; }
        }

        private void Revoke()
        {
            if (!_established || _revoked) return;
            _revoked = true;
            Emit(false); // Fresh invalidation immediately, even inside the healthy sampling interval.
        }
        private void EmitIfDue(bool valid)
        {
            if (_elapsedMs() - _lastEmissionMs >= SampleIntervalMs) Emit(valid);
        }
        private void Emit(bool valid)
        {
            try
            {
                if (_sampleId >= MaxSampleId) { _revoked = true; return; }
                var raw = JsonSerializer.SerializeToUtf8Bytes(new
                {
                    version = 1,
                    realm = "SR2",
                    pid = _pid,
                    executable = _executable,
                    journal = _journal,
                    director_pointer = unchecked((ulong)_pinned.Director.ToInt64()).ToString("x16"),
                    storage_pointer = unchecked((ulong)_pinned.Provider.ToInt64()).ToString("x16"),
                    provider_type = valid ? "FileStorageProvider" : "UNAVAILABLE",
                    initialized = valid,
                    native_root = valid ? _pinned.Root : "",
                    sampled_utc_ms = _utcMs(),
                    sample_id = ++_sampleId,
                    binding_valid = valid,
                });
                using var hmac = new HMACSHA256(_key);
                var signature = Convert.ToHexString(hmac.ComputeHash(raw)).ToLowerInvariant();
                _log("SLIMEBRIDGE_STORAGE_BINDING_V1 " + Convert.ToBase64String(raw) + " " + signature);
                _lastEmissionMs = _elapsedMs();
            }
            catch (Exception) { _revoked = true; } // No stale baseline can authorize CREATE after publication failure.
        }
        private readonly struct Observation
        {
            internal readonly IntPtr Director, Provider;
            internal readonly string Root;
            internal Observation(IntPtr director, IntPtr provider, string root)
            { Director = director; Provider = provider; Root = root; }
        }
    }
}
