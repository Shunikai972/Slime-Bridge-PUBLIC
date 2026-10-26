using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// LAB-only, read-only runtime traversal of GameContext.Instance.SlimeDefinitions.Slimes (IL2CPP).
    /// Ticked from BridgeMod.OnUpdate (Unity main thread). Reads the serialized referenceId field, never
    /// the lazy ReferenceId accessor. Count disagreement with _slimeDefinitionsByIdentifiable fails closed.
    /// Build anchor is the loaded native GameAssembly.dll, not the regenerated interop assembly.
    /// </summary>
    internal sealed class NativeRegistryCollector
    {
        private const string Realm = "SR2";
        private const string RegistryIdentity = "GameContext.SlimeDefinitions.Slimes";
        private readonly string _outDir;
        private readonly string _runId;
        private bool _done;
        private readonly int _mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;
        private float _readySince = -1f;
        private int _lastCount = -1;

        private NativeRegistryCollector(string outDir, string runId)
        {
            _outDir = outDir;
            _runId = runId;
        }

        internal static bool Requested => Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_REGISTRY_OUT") != null;

        public static NativeRegistryCollector? TryCreate(bool labExecutable)
        {
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_REGISTRY_OUT");
            if (!labExecutable || string.IsNullOrEmpty(outDir) || !Path.IsPathRooted(outDir) || !Directory.Exists(outDir))
                return null;
            var runId = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_REGISTRY_RUN") ?? "";
            if (runId.Length < 8 || runId.Length > 64 || runId.Any(c => !char.IsLetterOrDigit(c) && c != '-'))
                return null;
            try
            {
                var actual = System.Diagnostics.Process.GetCurrentProcess().MainModule?.FileName ?? "";
                var expected = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE") ?? "";
                outDir = RegistrySnapshotFormat.ValidateCaptureLocation(Realm, actual, expected, outDir!, runId);
                return new NativeRegistryCollector(outDir, runId);
            }
            catch { return null; }
        }

        public void Tick(Action<string> log)
        {
            if (_done) return;
            if (System.Threading.Thread.CurrentThread.ManagedThreadId != _mainThreadId) return;
            try { TickOnMainThread(log); }
            catch (Exception ex)
            {
                _done = true;
                log("registry collector failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private void TickOnMainThread(Action<string> log)
        {
            if (_done) return;
            var context = Il2Cpp.GameContext.Instance;
            var definitions = context?.SlimeDefinitions;
            var slimes = definitions?.Slimes;
            if (context == null || context.LookupDirector == null || definitions == null || slimes == null || slimes.Length == 0)
            {
                _readySince = -1f;
                return;
            }
            // Require the array length to stay stable for two seconds before traversing.
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_readySince < 0f || slimes.Length != _lastCount)
            {
                _readySince = now;
                _lastCount = slimes.Length;
                return;
            }
            if (now - _readySince < 2f) return;
            _done = true;
            try
            {
                Collect(definitions, log);
            }
            catch (Exception ex)
            {
                log("registry collector failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static string NativeBuildPath()
        {
            using (var process = Process.GetCurrentProcess())
                foreach (ProcessModule module in process.Modules)
                    if (string.Equals(module.ModuleName, "GameAssembly.dll", StringComparison.OrdinalIgnoreCase))
                    {
                        var file = module.FileName;
                        if (string.IsNullOrEmpty(file)) throw new FileNotFoundException("loaded module path unavailable");
                        return Path.GetFullPath(file);
                    }
            throw new FileNotFoundException("loaded GameAssembly.dll module not found");
        }

        private void Collect(Il2Cpp.SlimeDefinitions definitions, Action<string> log)
        {
            var errors = new List<string>();
            var slimes = definitions.Slimes;
            var counts = new Dictionary<string, long> { { "slimes_array", slimes.Length } };
            var index = definitions._slimeDefinitionsByIdentifiable;
            if (index == null) errors.Add("_slimeDefinitionsByIdentifiable index unavailable");
            else counts["by_identifiable"] = index.Count;
            var countMismatch = index != null && index.Count != slimes.Length;

            var rows = new List<RegistryRow>();
            for (var i = 0; i < slimes.Length; i++)
            {
                var def = slimes[i];
                if (def == null) { errors.Add("null SlimeDefinition entry at " + i); continue; }
                if (index != null && (!index.TryGetValue(def, out var indexed) || indexed == null || indexed.Pointer != def.Pointer))
                { countMismatch = true; errors.Add("definition does not match registry index"); }
                var reference = def.referenceId;
                if (string.IsNullOrEmpty(reference)) { errors.Add("empty referenceId at " + i); continue; }
                var row = new RegistryRow
                {
                    ObjectRef = "SR2:SlimeDefinition:" + reference,
                    NativeId = null,
                    ReferenceId = reference,
                    Name = def.name ?? "",
                    IsLargo = def.IsLargo,
                    CanLargofy = def.CanLargofy,
                };
                var bases = def.BaseSlimes;
                if (bases != null)
                    for (var b = 0; b < bases.Length; b++)
                    {
                        var baseDef = bases[b];
                        if (baseDef == null || string.IsNullOrEmpty(baseDef.referenceId)) errors.Add("unresolved base slime on " + reference);
                        else row.BaseSlimes.Add(baseDef.referenceId);
                    }
                var produce = def.Diet?.ProduceIdents;
                if (produce != null)
                    for (var p = 0; p < produce.Length; p++)
                    {
                        var item = produce[p];
                        if (item == null || string.IsNullOrEmpty(item.referenceId)) { errors.Add("unresolved produce on " + reference); continue; }
                        if (item.IsPlort) row.ProducedPlorts.Add(item.referenceId);
                    }
                var prefab = def.prefab;
                if (prefab != null) row.PrefabRefs.Add("SR2:Prefab:" + prefab.name);
                rows.Add(row);
            }

            var buildPath = NativeBuildPath();
            var buildSha = RegistrySnapshotFormat.Sha256Hex(File.ReadAllBytes(buildPath));
            var completeness = RegistrySnapshotFormat.Completeness(slimes.Length, rows, errors, countMismatch);
            var snapshot = RegistrySnapshotFormat.SnapshotJson(Realm, _runId, buildSha, buildSha, buildPath,
                "plugin hashed loaded native GameAssembly.dll process module", RegistryIdentity, slimes.Length,
                rows, errors, counts, completeness);
            var receipt = RegistrySnapshotFormat.ReceiptJson(Realm, _runId, buildSha, RegistryIdentity, slimes.Length, rows, counts);
            // Diet first: the oracle may return as soon as the two awaited registry files are readable.
            Publish(DietSnapshotFormat.FileName, DietJson(slimes, buildSha));
            Publish(AppearanceColorsFormat.FileName, AppearanceJson(slimes, buildSha));
            Publish(RegistrySnapshotFormat.SnapshotFileName, snapshot);
            Publish(RegistrySnapshotFormat.ReceiptFileName, receipt);
            log("registry collector wrote " + rows.Count + "/" + slimes.Length + " definitions completeness=" +
                completeness + " errors=" + errors.Count);
        }

        private string DietJson(Il2CppReferenceArray<Il2Cpp.SlimeDefinition> slimes, string buildSha)
        {
            var entries = new List<DietEntry>();
            var dietErrors = new List<string>();
            var emptySkipped = 0;
            for (var i = 0; i < slimes.Length; i++)
            {
                var def = slimes[i];
                if (def == null) continue;
                var entry = new DietEntry
                {
                    NativeRef = def.referenceId ?? "",
                    NumericId = null,
                    DisplayName = def.name ?? "",
                    IsLargo = def.IsLargo,
                    CanLargofy = def.CanLargofy,
                };
                var diet = def.Diet;
                if (diet == null) dietErrors.Add("null diet on " + entry.NativeRef);
                else
                {
                    var groups = diet.MajorFoodIdentifiableTypeGroups;
                    if (groups != null)
                        for (var g = 0; g < groups.Length; g++)
                        {
                            var group = groups[g];
                            if (group == null) { dietErrors.Add("null food group on " + entry.NativeRef); continue; }
                            string name;
                            try { name = group.ReferenceId; } catch (Exception) { name = null; }
                            name = !string.IsNullOrEmpty(name) ? name : (group.name ?? "");
                            entry.MajorFoodGroups.Add(name);
                            var members = new DietEntry.GroupMembers { Group = name };
                            try
                            {
                                var memberTypes = group.ImmediateMemberTypes;
                                if (memberTypes != null)
                                    for (var mi = 0; mi < memberTypes.Count && members.Members.Count < 16; mi++)
                                    {
                                        var memberId = memberTypes[mi]?.referenceId;
                                        if (!string.IsNullOrEmpty(memberId)) members.Members.Add(memberId);
                                    }
                            }
                            catch (Exception ex) { dietErrors.Add("group members failed on " + entry.NativeRef + ": " + ex.GetType().Name); }
                            entry.MajorFoodGroupMembers.Add(members);
                        }
                    var favorites = diet.FavoriteIdents;
                    if (favorites != null)
                        for (var f = 0; f < favorites.Length; f++)
                        {
                            var item = favorites[f];
                            if (item == null) { dietErrors.Add("null favorite on " + entry.NativeRef); continue; }
                            var id = item.referenceId;
                            if (string.IsNullOrEmpty(id)) { emptySkipped++; continue; }
                            entry.Favorites.Add(id);
                        }
                    entry.FavoriteProductionCount = diet.FavoriteProductionCount;
                    var produces = diet.ProduceIdents;
                    if (produces != null)
                        for (var p = 0; p < produces.Length; p++)
                        {
                            var item = produces[p];
                            if (item == null) { dietErrors.Add("null produce on " + entry.NativeRef); continue; }
                            var id = item.referenceId;
                            if (string.IsNullOrEmpty(id)) { emptySkipped++; continue; }
                            entry.Produces.Add(id);
                        }
                    var additional = diet.AdditionalFoodIdents;
                    if (additional != null)
                        for (var a = 0; a < additional.Length; a++)
                        {
                            var item = additional[a];
                            if (item == null) { dietErrors.Add("null additional food on " + entry.NativeRef); continue; }
                            var id = item.referenceId;
                            if (string.IsNullOrEmpty(id)) { emptySkipped++; continue; }
                            entry.AdditionalFoods.Add(id);
                        }
                }
                entries.Add(entry);
            }
            if (emptySkipped > 0) dietErrors.Add("empty (unresolved) food references skipped: " + emptySkipped);
            return DietSnapshotFormat.Json(Realm, _runId, buildSha, RegistrySnapshotFormat.CollectorVersion, entries, dietErrors);
        }

        private string AppearanceJson(Il2CppReferenceArray<Il2Cpp.SlimeDefinition> slimes, string buildSha)
        {
            var entries = new List<AppearanceEntry>();
            var errors = new List<string>();
            for (var i = 0; i < slimes.Length; i++)
            {
                var def = slimes[i];
                if (def == null) continue;
                var entry = new AppearanceEntry
                {
                    NativeRef = def.referenceId ?? "",
                    DisplayName = def.name ?? "",
                };
                try
                {
                    Il2Cpp.SlimeAppearance appearance = null;
                    try { appearance = def.GetAppearanceForSet(Il2Cpp.SlimeAppearance.AppearanceSaveSet.CLASSIC); }
                    catch (Exception) { }
                    if (appearance == null)
                    {
                        var defaults = def.AppearancesDefault;
                        if (defaults != null && defaults.Length > 0) appearance = defaults[0];
                    }
                    if (appearance == null) { errors.Add("no appearance on " + entry.NativeRef); continue; }
                    entry.SaveSet = appearance.SaveSet.ToString();
                    try { entry.LegacyPalette = PaletteFloats(appearance.ColorPalette); }
                    catch (Exception ex) { errors.Add("legacy palette failed on " + entry.NativeRef + ": " + ex.GetType().Name); }
                    try { entry.NewPalette = PaletteFloats(appearance.ColorPaletteNew); }
                    catch (Exception) { }
                }
                catch (Exception ex) { errors.Add("appearance failed on " + entry.NativeRef + ": " + ex.GetType().Name); }
                entries.Add(entry);
            }
            return AppearanceColorsFormat.Json(Realm, _runId, buildSha, RegistrySnapshotFormat.CollectorVersion, entries, errors);
        }

        private static float[] PaletteFloats(Il2Cpp.SlimeAppearance.Palette palette)
        {
            return new[]
            {
                palette.Top.r, palette.Top.g, palette.Top.b, palette.Top.a,
                palette.Middle.r, palette.Middle.g, palette.Middle.b, palette.Middle.a,
                palette.Bottom.r, palette.Bottom.g, palette.Bottom.b, palette.Bottom.a,
                palette.Ammo.r, palette.Ammo.g, palette.Ammo.b, palette.Ammo.a,
            };
        }

        private void Publish(string name, string text) =>
            RegistrySnapshotFormat.PublishCaptureFile(_outDir, name, text);
    }
}
