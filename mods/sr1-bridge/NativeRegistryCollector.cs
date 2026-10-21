using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using SlimeBridge.Shared;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// LAB-only, read-only runtime traversal of GameContext.Instance.SlimeDefinitions.Slimes.
    /// Ticked from BridgeEntry.Update (Unity main thread). Writes a snapshot and a separate traversal
    /// receipt once, never touches saves. Enabled only when the oracle sets SLIMEBRIDGE_LAB_REGISTRY_OUT
    /// for the exact LAB executable.
    /// </summary>
    internal sealed class NativeRegistryCollector
    {
        private const string Realm = "SR1";
        private const string RegistryIdentity = "GameContext.SlimeDefinitions.Slimes";
        private readonly string _outDir;
        private readonly string _runId;
        private bool _done;
        private readonly int _mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;
        private float _readySince = -1f;

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

        public void Tick()
        {
            if (_done) return;
            if (System.Threading.Thread.CurrentThread.ManagedThreadId != _mainThreadId) return;
            try { TickOnMainThread(); }
            catch (Exception ex)
            {
                _done = true;
                UnityEngine.Debug.Log("[SlimeBridge] registry collector failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private void TickOnMainThread()
        {
            if (_done) return;
            var context = GameContext.Instance;
            if (context == null || context.SlimeDefinitions == null || context.LookupDirector == null ||
                SRML.SRModLoader.CurrentLoadingStep != SRML.SRModLoader.LoadingStep.FINISHED)
            {
                _readySince = -1f;
                return;
            }
            // Let one more second of frames pass after all registries exist (SRML late registration).
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_readySince < 0f) { _readySince = now; return; }
            if (now - _readySince < 1f) return;
            _done = true;
            try
            {
                Collect(context);
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] registry collector failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static string IdName(Identifiable.Id id) =>
            Enum.IsDefined(typeof(Identifiable.Id), id) ? id.ToString() : "Identifiable.Id:" + ((int)id).ToString(System.Globalization.CultureInfo.InvariantCulture);

        private void Collect(GameContext context)
        {
            var errors = new List<string>();
            var slimes = context.SlimeDefinitions.Slimes ?? new SlimeDefinition[0];
            var counts = new Dictionary<string, long> { { "slimes_array", slimes.Length } };
            var index = typeof(SlimeDefinitions).GetField("slimeDefinitionsByIdentifiable",
                BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public)?.GetValue(context.SlimeDefinitions) as IDictionary;
            if (index == null) errors.Add("slimeDefinitionsByIdentifiable index unavailable");
            else counts["by_identifiable"] = index.Count;
            var countMismatch = index != null && index.Count != slimes.Length;

            var rows = new List<RegistryRow>();
            foreach (var def in slimes)
            {
                if (def == null) { errors.Add("null SlimeDefinition entry"); continue; }
                if (index != null && !ReferenceEquals(index[def.IdentifiableId], def))
                { countMismatch = true; errors.Add("definition does not match registry index"); }
                var row = new RegistryRow
                {
                    ObjectRef = "SR1:SlimeDefinition:" + IdName(def.IdentifiableId),
                    NativeId = (int)def.IdentifiableId,
                    ReferenceId = IdName(def.IdentifiableId),
                    Name = def.Name ?? "",
                    IsLargo = def.IsLargo,
                    CanLargofy = def.CanLargofy,
                };
                foreach (var b in def.BaseSlimes ?? new SlimeDefinition[0])
                {
                    if (b == null) errors.Add("null base slime on " + row.ReferenceId);
                    else row.BaseSlimes.Add(IdName(b.IdentifiableId));
                }
                foreach (var p in def.Diet?.Produces ?? new Identifiable.Id[0])
                {
                    if (Identifiable.IsPlort(p)) row.ProducedPlorts.Add(IdName(p));
                }
                try
                {
                    var prefab = context.LookupDirector.GetPrefab(def.IdentifiableId);
                    if (prefab != null) row.PrefabRefs.Add("SR1:Prefab:" + prefab.name);
                }
                catch (Exception ex)
                {
                    errors.Add("prefab lookup failed for " + row.ReferenceId + ": " + ex.GetType().Name);
                }
                rows.Add(row);
            }

            var assemblyPath = Path.GetFullPath(typeof(SlimeDefinitions).Assembly.Location);
            var buildSha = RegistrySnapshotFormat.Sha256Hex(File.ReadAllBytes(assemblyPath));
            var completeness = RegistrySnapshotFormat.Completeness(slimes.Length, rows, errors, countMismatch);
            var snapshot = RegistrySnapshotFormat.SnapshotJson(Realm, _runId, buildSha, buildSha, assemblyPath,
                "plugin hashed loaded Assembly-CSharp.dll (typeof(SlimeDefinitions).Assembly.Location)",
                RegistryIdentity, slimes.Length, rows, errors, counts, completeness);
            var receipt = RegistrySnapshotFormat.ReceiptJson(Realm, _runId, buildSha, RegistryIdentity, slimes.Length, rows, counts);
            // Diet first: the oracle may return as soon as the two awaited registry files are readable.
            Publish(DietSnapshotFormat.FileName, DietJson(slimes, buildSha));
            Publish(RegistrySnapshotFormat.SnapshotFileName, snapshot);
            Publish(RegistrySnapshotFormat.ReceiptFileName, receipt);
            UnityEngine.Debug.Log("[SlimeBridge] registry collector wrote " + rows.Count + "/" + slimes.Length +
                " definitions completeness=" + completeness + " errors=" + errors.Count);
        }

        private string DietJson(SlimeDefinition[] slimes, string buildSha)
        {
            var entries = new List<DietEntry>();
            var dietErrors = new List<string>();
            foreach (var def in slimes)
            {
                if (def == null) continue;
                var entry = new DietEntry
                {
                    NativeRef = IdName(def.IdentifiableId),
                    NumericId = (int)def.IdentifiableId,
                    DisplayName = def.Name ?? "",
                    IsLargo = def.IsLargo,
                    CanLargofy = def.CanLargofy,
                };
                var diet = def.Diet;
                if (diet == null) dietErrors.Add("null diet on " + entry.NativeRef);
                else
                {
                    foreach (var group in diet.MajorFoodGroups ?? new SlimeEat.FoodGroup[0]) entry.MajorFoodGroups.Add(group.ToString());
                    foreach (var favorite in diet.Favorites ?? new Identifiable.Id[0]) entry.Favorites.Add(IdName(favorite));
                    entry.FavoriteProductionCount = diet.FavoriteProductionCount;
                    foreach (var produces in diet.Produces ?? new Identifiable.Id[0]) entry.Produces.Add(IdName(produces));
                    foreach (var additional in diet.AdditionalFoods ?? new Identifiable.Id[0]) entry.AdditionalFoods.Add(IdName(additional));
                }
                entries.Add(entry);
            }
            return DietSnapshotFormat.Json(Realm, _runId, buildSha, RegistrySnapshotFormat.CollectorVersion, entries, dietErrors);
        }

        private void Publish(string name, string text) =>
            RegistrySnapshotFormat.PublishCaptureFile(_outDir, name, text);
    }
}
