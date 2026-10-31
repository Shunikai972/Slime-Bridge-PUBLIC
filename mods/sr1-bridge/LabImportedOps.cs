using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Q033: LAB-only native execution edge for imported species. Runs one-shot, oracle-provided
    /// operations (spawn / vacpack add / remove / release / query) against the real native objects
    /// once the world is ready, and publishes a receipt that distinguishes REFUSED from EXECUTED
    /// with native read-back. Enabled only for the exact LAB executable, after registration.
    /// </summary>
    internal sealed class LabImportedOps
    {
        private static readonly Regex OpPattern = new Regex(
            @"\A(spawn|feed|diet|price|vac_add|vac_remove|release|query|save_roundtrip):((?:slime|plort)\.[a-z0-9_]+|largo\.[a-z0-9_]+\.[a-z0-9_]+)(?::([0-9]{1,2}))?\z",
            RegexOptions.CultureInvariant);

        private readonly SpeciesRegistrar _registrar;
        private readonly string[] _ops;
        private readonly string _outDir;
        private readonly string _runId;
        private readonly int _mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;
        private bool _done;
        private float _readySince = -1f;
        private float _nextWaitLog;
        private int _spawned;
        private bool _diagPending;
        private float _diagDeadline;
        private readonly int _timelineSeconds;
        private readonly string _timelineTarget;
        private float _timelineStart = -1f;
        private float _timelineNext;
        private int _timelineWrites;

        private LabImportedOps(SpeciesRegistrar registrar, string[] ops, string outDir, string runId,
            int timelineSeconds, string timelineTarget)
        {
            _registrar = registrar;
            _ops = ops;
            _outDir = outDir;
            _runId = runId;
            _timelineSeconds = timelineSeconds;
            _timelineTarget = timelineTarget;
        }

        public static LabImportedOps? TryCreate(bool labExecutable, SpeciesRegistrar? registrar)
        {
            var ops = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OPS");
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OUT");
            var runId = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_RUN") ?? "";
            LabImportedOps? Refuse(string why)
            {
                UnityEngine.Debug.Log("[SlimeBridge] imported ops not armed: " + why);
                return null;
            }
            if (!labExecutable) return null;
            if (registrar == null) return Refuse("no species registrar");
            if (string.IsNullOrEmpty(ops)) return null;
            if (string.IsNullOrEmpty(outDir) || !Directory.Exists(outDir)) return Refuse("output directory missing");
            var guardRoot = LabSpecPathGuard.FindRepoRoot();
            if (guardRoot == null) return Refuse("Bridge repo root not found");
            if (!LabSpecPathGuard.IsAllowedDirectory(Path.Combine(guardRoot, LabSpecPathGuard.OutRelativeRoot), outDir, out var outDirReason))
                return Refuse("output directory refused: " + outDirReason);
            if (runId.Length < 8 || runId.Length > 64 || runId.Any(c => !char.IsLetterOrDigit(c) && c != '-'))
                return Refuse("invalid run id");
            object? tree;
            try { tree = MiniJson.Parse(ops); }
            catch (Exception ex) { return Refuse("ops are not valid JSON: " + ex.Message); }
            if (!(tree is Dictionary<string, object?> root) || !root.TryGetValue("ops", out var rawOps) ||
                !(rawOps is List<object?> list) || list.Count == 0 || list.Count > 32)
                return Refuse("ops document shape invalid");
            var parsed = new string[list.Count];
            for (var i = 0; i < list.Count; i++)
            {
                if (!(list[i] is string text) || !OpPattern.IsMatch(text))
                    return Refuse("invalid op at index " + i);
                parsed[i] = text;
            }
            DiagnosticsTimeline.TryParseSeconds(Environment.GetEnvironmentVariable(DiagnosticsTimeline.SecondsVariable),
                out var timelineSeconds, out var timelineProblem);
            if (timelineProblem != null) return Refuse(timelineProblem);
            var timelineTarget = Environment.GetEnvironmentVariable("SLIMEBRIDGE_DIAG") ?? "";
            if (timelineSeconds > 0 && (!BridgeEntry.LabRestPoseRequested || !DiagnosticsTimeline.IsSafeFileToken(timelineTarget)))
                return Refuse("timeline requires LAB rest-pose and a safe diagnostics target");
            return new LabImportedOps(registrar, parsed, outDir, runId, timelineSeconds, timelineTarget);
        }

        public void Tick()
        {
            if (System.Threading.Thread.CurrentThread.ManagedThreadId != _mainThreadId) return;
            TickTimeline();
            TickFeed();
            if (_diagPending)
            {
                // The source-animation trace needs real elapsed frames; the diagnostic is written
                // once it is full, or at the deadline so a stalled trace cannot hang the proof.
                if (LabSourceAnimationTrace.AllComplete || UnityEngine.Time.realtimeSinceStartup >= _diagDeadline)
                {
                    _diagPending = false;
                    WriteDiagnostics();
                }
                return;
            }
            if (_done) return;
            try { TickOnMainThread(); }
            catch (Exception ex)
            {
                _done = true;
                UnityEngine.Debug.Log("[SlimeBridge] imported ops failed: " + ex.GetType().Name + ": " + ex.Message);
                TryPublishFailure(ex);
            }
        }

        private void TickOnMainThread()
        {
            if (_done) return;
            var game = SRSingleton<GameContext>.Instance;
            var scene = SRSingleton<SceneContext>.Instance;
            var director = game == null ? null : game.AutoSaveDirector;
            if (game == null || scene == null || director == null || scene.PlayerState == null || scene.Player == null ||
                !scene.Player.activeInHierarchy || director.IsLoadingGame() || director.SavedGame == null)
            {
                _readySince = -1f;
                var nowLog = UnityEngine.Time.realtimeSinceStartup;
                if (nowLog >= _nextWaitLog)
                {
                    _nextWaitLog = nowLog + 10f;
                    UnityEngine.Debug.Log("[SlimeBridge] imported ops waiting: game=" + (game != null) + " scene=" + (scene != null) +
                        " director=" + (director != null) + " playerState=" + (scene != null && scene.PlayerState != null) +
                        " player=" + (scene != null && scene.Player != null) + " active=" + (scene != null && scene.Player != null && scene.Player.activeInHierarchy) +
                        " loading=" + (director != null && director.IsLoadingGame()) + " savedGame=" + (director != null && director.SavedGame != null));
                }
                return;
            }
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_readySince < 0f) { _readySince = now; return; }
            if (now - _readySince < 2f) return; // let the world settle after load
            _done = true;
            Run(game, scene);
        }

        private void TryPublishFailure(Exception ex)
        {
            try
            {
                var json = "{\n  \"schema\": \"bridge-imported-ops-receipt\",\n  \"schema_version\": 1,\n  \"realm\": \"SR1\",\n  \"run_id\": " +
                    RegistrySnapshotFormat.Quote(_runId) + ",\n  \"fatal\": " +
                    RegistrySnapshotFormat.Quote(ex.GetType().Name + ": " + ex.Message) + ",\n  \"ops\": []\n}\n";
                RegistrySnapshotFormat.PublishCaptureFile(_outDir, RegistrySnapshotFormat.ImportedOpsReceiptFileName, json);
            }
            catch { }
        }

        private void Run(GameContext game, SceneContext scene)
        {
            var rows = new List<string>();
            var ammo = scene.PlayerState.GetAmmo(PlayerState.AmmoMode.DEFAULT);
            foreach (var op in _ops)
            {
                var match = OpPattern.Match(op);
                var kind = match.Groups[1].Value;
                var canonical = match.Groups[2].Value;
                var count = match.Groups[3].Success ? int.Parse(match.Groups[3].Value, CultureInfo.InvariantCulture) : 1;
                rows.Add(Execute(game, scene, ammo, kind, canonical, count));
            }
            var sb = new StringBuilder(4096);
            sb.Append("{\n  \"schema\": \"bridge-imported-ops-receipt\",\n  \"schema_version\": 1");
            sb.Append(",\n  \"realm\": \"SR1\"");
            sb.Append(",\n  \"run_id\": ").Append(RegistrySnapshotFormat.Quote(_runId));
            sb.Append(",\n  \"save\": ").Append(RegistrySnapshotFormat.Quote(game.AutoSaveDirector.SavedGame?.GetName() ?? ""));
            sb.Append(",\n  \"ops\": [");
            for (var i = 0; i < rows.Count; i++) sb.Append(i == 0 ? "\n" : ",\n").Append(rows[i]);
            sb.Append(rows.Count == 0 ? "]" : "\n  ]");
            sb.Append("\n}\n");
            RegistrySnapshotFormat.PublishCaptureFile(_outDir, RegistrySnapshotFormat.ImportedOpsReceiptFileName, sb.ToString());
            UnityEngine.Debug.Log("[SlimeBridge] imported ops: " + rows.Count + " executed");
            if (_timelineSeconds > 0)
            {
                _timelineStart = UnityEngine.Time.realtimeSinceStartup;
                _timelineNext = _timelineStart;
            }
            if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SLIMEBRIDGE_DIAG"))) return;
            if (BridgeEntry.LabRestPoseRequested && LabSourceAnimationTrace.HasAttached)
            {
                _diagPending = true;
                _diagDeadline = UnityEngine.Time.realtimeSinceStartup + 20f;
                return;
            }
            WriteDiagnostics();
        }

        // Optional long-running LAB readback: no native mutation and no hierarchy-wide scan.
        // Keep the hot path allocation-free until the bounded sample deadline is reached.
        private void TickTimeline()
        {
            if (_timelineSeconds == 0 || _timelineStart < 0f ||
                _timelineWrites >= DiagnosticsTimeline.MaxWrites) return;
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (now < _timelineNext) return;
            _timelineNext = now + _timelineSeconds;
            _timelineWrites++;
            try
            {
                // This gate reads the LAB marker: only evaluate it for a due sample.
                if (!BridgeEntry.LabRestPoseRequested)
                {
                    _timelineWrites = DiagnosticsTimeline.MaxWrites;
                    return;
                }
                if (!_registrar.TryResolveNativeId(_timelineTarget, out var nativeId)) return;
                var entries = new List<KeyValuePair<string, string>>();
                foreach (var root in ImportedSpawner.TrackedImportRoots)
                {
                    if (root == null || !root.activeInHierarchy) continue;
                    var identity = root.GetComponent<Identifiable>();
                    if (identity == null || (int)identity.id != nativeId) continue;
                    var machine = root.GetComponent<ImportedPetrification>();
                    if (machine == null) continue;
                    string payload;
                    try { payload = machine.ToJson(); }
                    catch (Exception error) { payload = "{\"read_error\": " + RegistrySnapshotFormat.Quote(error.GetType().Name) + "}"; }
                    var label = root.name + "#" + root.GetInstanceID().ToString(CultureInfo.InvariantCulture);
                    entries.Add(new KeyValuePair<string, string>(label, payload));
                }
                var repoRoot = LabSpecPathGuard.FindRepoRoot();
                if (repoRoot == null || !LabSpecPathGuard.IsAllowedDirectory(
                    Path.Combine(repoRoot, LabSpecPathGuard.OutRelativeRoot), _outDir, out _) ||
                    !DiagnosticsTimeline.IsSafeFileToken(_timelineTarget))
                    throw new IOException("timeline output directory refused");
                var path = Path.Combine(_outDir, "visual-diagnostics-" + _timelineTarget + ".timeline.jsonl");
                if (!LabSpecPathGuard.IsUnderRoot(_outDir, path, out _) || LabSpecPathGuard.HasReparsePoint(path, out _))
                    throw new IOException("timeline output file refused");
                if (_timelineWrites == 1 && File.Exists(path)) throw new IOException("timeline output already exists");
                var line = DiagnosticsTimeline.FormatLine(now - _timelineStart, entries);
                File.AppendAllText(path, line + "\n", new UTF8Encoding(false));
            }
            catch (Exception error)
            {
                _timelineWrites = DiagnosticsTimeline.MaxWrites;
                UnityEngine.Debug.LogWarning("[SlimeBridge] timeline stopped: " + error.GetType().Name + ": " + error.Message);
            }
        }

        private void WriteDiagnostics()
        {
            var diagTarget = Environment.GetEnvironmentVariable("SLIMEBRIDGE_DIAG");
            if (string.IsNullOrEmpty(diagTarget)) return;
            try
            {
                var game = SRSingleton<GameContext>.Instance;
                var scene = SRSingleton<SceneContext>.Instance;
                var diagProblems = new List<string>();
                var written = BridgeDiagnostics.Write(_outDir, diagTarget, game, scene, _registrar, diagProblems);
                UnityEngine.Debug.Log("[SlimeBridge] visual diagnostics written=" + written +
                    (diagProblems.Count > 0 ? " problems=" + string.Join("; ", diagProblems) : ""));
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] visual diagnostics failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private string Execute(GameContext game, SceneContext scene, Ammo ammo, string kind, string canonical, int count)
        {
            var problems = new List<string>();
            var details = new List<string>();
            var executed = false;
            try
            {
                var nativeId = 0;
                var isPlort = canonical.StartsWith("plort.", StringComparison.Ordinal);
                if (!(isPlort ? _registrar.TryResolvePlortId(canonical, out nativeId) : _registrar.TryResolveNativeId(canonical, out nativeId)))
                {
                    problems.Add("species not registered in this session");
                }
                else
                {
                    var id = (Identifiable.Id)nativeId;
                    var before = kind is "vac_add" or "vac_remove" or "release" or "query" or "save_roundtrip" ? Count(ammo, id) : -1;
                    var after = before;
                    switch (kind)
                    {
                        case "query":
                            executed = true;
                            details.Add("\"slots\": " + SlotJson(ammo, id));
                            break;
                        case "vac_add":
                        {
                            var slotJson = SlotJson(ammo, id);
                            var plan = InventoryRules.PlanAdd(Snapshot(ammo, id), count);
                            if (plan == null) { problems.Add("insufficient usable native capacity"); break; }
                            var ok = false;
                            var threw = false;
                            try
                            {
                                ok = InventoryRules.Apply(plan, ammo.GetSlotCount, (index, amount) =>
                                {
                                    var name = ammo.GetSlotName(index);
                                    if (name != id && name != Identifiable.Id.NONE) return false;
                                    if (!ammo.CouldAddToSlot(id, index, false) ||
                                        ammo.GetSlotMaxCount(id, index) - ammo.GetSlotCount(index) < amount) return false;
                                    return ammo.MaybeAddToSpecificSlot(id, null, index, amount, false);
                                }, true);
                            }
                            catch (Exception ex)
                            {
                                threw = true;
                                problems.Add("native add threw: " + ex.Message);
                            }
                            after = Count(ammo, id);
                            executed = ok && after == before + count;
                            if (!executed)
                            {
                                if (!threw) problems.Add("native slot callback or aggregate count mismatch");
                                RevertAdded(ammo, id, before, details, problems);
                            }
                            details.Add("\"slots_before\": " + slotJson);
                            details.Add("\"slots_after\": " + SlotJson(ammo, id));
                            break;
                        }
                        case "vac_remove":
                        case "release":
                        {
                            var plan = InventoryRules.PlanRemove(Snapshot(ammo, id), count);
                            if (plan == null) { problems.Add("insufficient usable native count"); break; }
                            var ok = false;
                            var threw = false;
                            try
                            {
                                ok = InventoryRules.Apply(plan, ammo.GetSlotCount, (index, amount) =>
                                {
                                    if (ammo.GetSlotName(index) != id || ammo.GetSlotCount(index) < amount) return false;
                                    ammo.Decrement(index, amount);
                                    return true;
                                }, false);
                            }
                            catch (Exception ex)
                            {
                                threw = true;
                                problems.Add("native decrement threw: " + ex.Message);
                            }
                            after = Count(ammo, id);
                            executed = ok && after == before - count;
                            if (!executed)
                            {
                                if (!threw) problems.Add("native decrement or aggregate count mismatch");
                                RestoreRemoved(ammo, id, before, "failed decrement", details, problems);
                            }
                            else if (kind == "release")
                            {
                                var spawned = AppendSpawn(game, scene, canonical, id, details, problems);
                                if (!spawned)
                                {
                                    executed = false;
                                    RestoreRemoved(ammo, id, before, "failed release spawn", details, problems);
                                }
                            }
                            break;
                        }
                        case "price":
                            {
                                // Market readback: the imported plort must have a price (ScorePlort refuses unpriced ids) and a saturation entry.
                                var plortCanonical = canonical.Replace("slime.", "plort.");
                                if (!_registrar.TryResolvePlortId(plortCanonical, out var plortNative)) { problems.Add("no plort registered"); break; }
                                var economy = scene.EconomyDirector;
                                var value = economy != null ? economy.GetCurrValue((Identifiable.Id)plortNative) : null;
                                details.Add("\"plort_market_price\": " + (value.HasValue ? value.Value.ToString(CultureInfo.InvariantCulture) : "null"));
                                executed = value.HasValue;
                                if (!executed) problems.Add("plort has no market price");
                                break;
                            }
                        case "diet":
                        {
                            // LAB readback of the registered gameplay: parents, plorts produced, favourite foods, food groups, and which
                            // parent turns into this Largo when it eats the other's plort.
                            var definition = game.SlimeDefinitions.GetSlimeByIdentifiableId(id);
                            executed = definition != null && definition.Diet != null;
                            if (!executed) { problems.Add("no registered definition"); break; }
                            details.Add("\"diet\": " + DietJson(game, definition!, id));
                            break;
                        }
                        case "feed":
                            // LAB readback of the eat -> plort path: the species plus native carrots, reported 25 s later.
                            executed = !isPlort && AppendSpawn(game, scene, canonical, id, details, problems) && StartFeed(game, scene, canonical, id, details, problems);
                            break;
                        case "spawn":
                            if (isPlort)
                            {
                                executed = ImportedSpawner.SpawnPlort(game, scene, _registrar, canonical, problems) != null;
                                details.Add("\"plort_spawn\": " + (executed ? "true" : "false"));
                            }
                            else executed = AppendSpawn(game, scene, canonical, id, details, problems);
                            break;
                        case "save_roundtrip":
                        {
                            var director = game.AutoSaveDirector;
                            var saveId = director?.SavedGame?.GetName();
                            if (director == null || string.IsNullOrEmpty(saveId))
                            { problems.Add("no loaded native save"); break; }
                            var beforeNames = InventoryAgent.RevisionNames(director, saveId!);
                            var expected = Count(ammo, id);
                            var proof = InventoryRules.VerifyPersistence(
                                () => !director.IsLoadingGame() && director.SaveAllNow() && !director.IsLoadingGame(),
                                () => InventoryAgent.ReadbackNew(director, saveId!, id, ammo.GetUsableSlotCount(), beforeNames),
                                saveId!, expected);
                            executed = proof.Verified;
                            if (!executed) problems.Add("native save readback did not verify the imported count: " + proof.Reason);
                            details.Add("\"save_id\": " + RegistrySnapshotFormat.Quote(saveId!));
                            details.Add("\"save_file\": " + RegistrySnapshotFormat.Quote(proof.SaveFile ?? ""));
                            details.Add("\"save_sha256\": " + RegistrySnapshotFormat.Quote(proof.Sha256 ?? ""));
                            details.Add("\"saved_count\": " + expected.ToString(CultureInfo.InvariantCulture));
                            break;
                        }
                    }
                    var sb = new StringBuilder(512);
                    sb.Append("    {\"op\": ").Append(RegistrySnapshotFormat.Quote(kind));
                    sb.Append(", \"item\": ").Append(RegistrySnapshotFormat.Quote(canonical));
                    sb.Append(", \"count\": ").Append(count.ToString(CultureInfo.InvariantCulture));
                    sb.Append(", \"native_id\": ").Append(nativeId.ToString(CultureInfo.InvariantCulture));
                    sb.Append(", \"before\": ").Append(before.ToString(CultureInfo.InvariantCulture));
                    sb.Append(", \"after\": ").Append(after.ToString(CultureInfo.InvariantCulture));
                    sb.Append(", \"executed\": ").Append(executed ? "true" : "false");
                    sb.Append(", \"problems\": [");
                    for (var i = 0; i < problems.Count; i++)
                    {
                        if (i > 0) sb.Append(", ");
                        sb.Append(RegistrySnapshotFormat.Quote(problems[i]));
                    }
                    sb.Append("]");
                    if (details.Count > 0) sb.Append(", ").Append(string.Join(", ", details));
                    sb.Append("}");
                    return sb.ToString();
                }
            }
            catch (Exception ex)
            {
                problems.Add("exception " + ex.GetType().Name + ": " + ex.Message);
            }
            var refused = new StringBuilder(256);
            refused.Append("    {\"op\": ").Append(RegistrySnapshotFormat.Quote(kind));
            refused.Append(", \"item\": ").Append(RegistrySnapshotFormat.Quote(canonical));
            refused.Append(", \"count\": ").Append(count.ToString(CultureInfo.InvariantCulture));
            refused.Append(", \"executed\": false, \"problems\": [");
            for (var i = 0; i < problems.Count; i++)
            {
                if (i > 0) refused.Append(", ");
                refused.Append(RegistrySnapshotFormat.Quote(problems[i]));
            }
            refused.Append("]}");
            return refused.ToString();
        }

        private sealed class FeedWatch
        {
            internal string Canonical = "";
            internal Identifiable.Id PlortId;
            internal readonly List<GameObject> Food = new List<GameObject>();
            internal GameObject? Slime;
            internal float ReportAt;
        }
        private readonly List<FeedWatch> _feeds = new List<FeedWatch>();

        private bool StartFeed(GameContext game, SceneContext scene, string canonical, Identifiable.Id slimeId, List<string> details, List<string> problems)
        {
            var definition = game.SlimeDefinitions.GetSlimeByIdentifiableId(slimeId);
            var produces = definition != null ? definition.Diet.Produces : null;
            var prefab = game.LookupDirector.GetPrefab(Identifiable.Id.CARROT_VEGGIE);
            if (prefab == null) { problems.Add("no carrot prefab"); return false; }
            var player = scene.Player.transform;
            var watch = new FeedWatch { Canonical = canonical, PlortId = produces != null && produces.Length > 0 ? produces[0] : Identifiable.Id.NONE, ReportAt = UnityEngine.Time.realtimeSinceStartup + 25f };
            var regionSetId = MonomiPark.SlimeRancher.Regions.RegionRegistry.RegionSetId.HOME;
            for (var i = 0; i < 4; i++)
            {
                var position = player.position + player.forward * 2.4f + player.right * (0.25f * (i - 1.5f)) + Vector3.up * 0.9f;
                var food = UnityEngine.Object.Instantiate(prefab, position, Quaternion.identity);
                food.SetActive(true);
                scene.GameModel.RegisterStartingActor(food, regionSetId);
                watch.Food.Add(food);
            }
            foreach (var identifiable in UnityEngine.Object.FindObjectsOfType<Identifiable>())
                if (identifiable != null && identifiable.id == slimeId) watch.Slime = identifiable.gameObject;
            _feeds.Add(watch);
            details.Add("\"feed_food\": 4");
            details.Add("\"feed_plort_id\": " + ((int)watch.PlortId).ToString(CultureInfo.InvariantCulture));
            return true;
        }

        private static string Names<T>(IEnumerable<T>? values) =>
            "[" + string.Join(",", (values ?? Enumerable.Empty<T>()).Select(v => RegistrySnapshotFormat.Quote(v?.ToString() ?? ""))) + "]";

        private static string DietJson(GameContext game, SlimeDefinition definition, Identifiable.Id id)
        {
            var diet = definition.Diet;
            var becomes = new List<string>();
            foreach (var parent in definition.BaseSlimes ?? new SlimeDefinition[0])
            {
                if (parent?.Diet?.EatMap == null) continue;
                foreach (var entry in parent.Diet.EatMap)
                    if (entry.becomesId == id) becomes.Add(parent.IdentifiableId + " eats " + entry.eats);
            }
            return "{\"is_largo\": " + (definition.IsLargo ? "true" : "false") +
                ", \"parents\": " + Names((definition.BaseSlimes ?? new SlimeDefinition[0]).Select(p => p.IdentifiableId)) +
                ", \"produces\": " + Names(diet.Produces) + ", \"favorites\": " + Names(diet.Favorites) +
                ", \"favorite_production_count\": " + diet.FavoriteProductionCount.ToString(CultureInfo.InvariantCulture) +
                ", \"food_groups\": " + Names(diet.MajorFoodGroups) +
                ", \"eat_map_entries\": " + (diet.EatMap?.Count ?? 0).ToString(CultureInfo.InvariantCulture) +
                ", \"made_by\": " + Names(becomes) + "}";
        }

        private void TickFeed()
        {
            if (_feeds.Count == 0) return;
            for (var index = _feeds.Count - 1; index >= 0; index--)
            {
                var watch = _feeds[index];
                if (UnityEngine.Time.realtimeSinceStartup < watch.ReportAt) continue;
                _feeds.RemoveAt(index);
                try
                {
                    var food = watch.Food.Count(f => f != null);
                    var plorts = 0;
                    var byKind = new Dictionary<string, int>(StringComparer.Ordinal);
                    foreach (var identifiable in UnityEngine.Object.FindObjectsOfType<Identifiable>())
                    {
                        if (identifiable == null) continue;
                        if (identifiable.id == watch.PlortId && watch.PlortId != Identifiable.Id.NONE) plorts++;
                        if (Identifiable.IsPlort(identifiable.id) || (int)identifiable.id >= 900000 && identifiable.id.ToString().Contains("PLORT"))
                        {
                            var key = identifiable.id.ToString();
                            byKind[key] = byKind.TryGetValue(key, out var n) ? n + 1 : 1;
                        }
                    }
                    UnityEngine.Debug.Log("[SlimeBridge] feed report: species=" + watch.Canonical + " foodStart=" + watch.Food.Count + " foodNow=" + food +
                        " plortsInWorld=" + plorts + " slimeAlive=" + (watch.Slime != null) +
                        " plortKinds=" + string.Join(",", byKind.Select(kv => kv.Key + ":" + kv.Value)));
                }
                catch (Exception ex) { UnityEngine.Debug.Log("[SlimeBridge] feed report failed: " + ex.GetType().Name); }
            }
        }

        private bool AppendSpawn(GameContext game, SceneContext scene, string canonical, Identifiable.Id id,
            List<string> details, List<string> problems)
        {
            var instance = ImportedSpawner.Spawn(game, scene, _registrar, canonical, details, problems);
            if (instance == null) return false;
            if (BridgeEntry.LabRestPoseRequested && !LabRestPosePin.Attach(instance, scene, details, problems))
            {
                UnityEngine.Object.Destroy(instance);
                return false;
            }
            if (!LabActorMotionTrace.TryAttach(instance, canonical, _outDir, out var motionProblem))
            {
                problems.Add(motionProblem);
                UnityEngine.Object.Destroy(instance);
                return false;
            }
            var spawnIndex = _spawned + 1;
            var definition = game.SlimeDefinitions.GetSlimeByIdentifiableId(id);
            details.Add("\"definition_name\": " + RegistrySnapshotFormat.Quote(definition?.Name ?? ""));
            details.Add("\"spawn_index\": " + spawnIndex.ToString(CultureInfo.InvariantCulture));
            details.Add("\"spawn_path\": " + RegistrySnapshotFormat.Quote("native_instantiate_actor"));
            _spawned = spawnIndex;
            return true;
        }

        /// <summary>Removes every item a failed add actually added; mirrors the vac_remove slot logic.</summary>
        private static void RevertAdded(Ammo ammo, Identifiable.Id id, int before,
            List<string> details, List<string> problems)
        {
            var added = Count(ammo, id) - before;
            if (added <= 0) return;
            var reverted = false;
            var plan = InventoryRules.PlanRemove(Snapshot(ammo, id), added);
            if (plan != null)
            {
                reverted = InventoryRules.Apply(plan, ammo.GetSlotCount, (index, amount) =>
                {
                    if (ammo.GetSlotName(index) != id || ammo.GetSlotCount(index) < amount) return false;
                    ammo.Decrement(index, amount);
                    return true;
                }, false);
            }
            var revertedCount = Count(ammo, id);
            details.Add("\"reverted_count\": " + revertedCount.ToString(CultureInfo.InvariantCulture));
            problems.Add(reverted && revertedCount == before
                ? "failed add: " + added.ToString(CultureInfo.InvariantCulture) + " item(s) reverted, operation aborted"
                : "failed add: NATIVE REVERT FAILED, " + (revertedCount - before).ToString(CultureInfo.InvariantCulture) + " item(s) remain (count now " + revertedCount.ToString(CultureInfo.InvariantCulture) + ")");
        }

        /// <summary>Re-adds every item a failed decrement/release actually removed; mirrors the vac_add slot logic.</summary>
        private static void RestoreRemoved(Ammo ammo, Identifiable.Id id, int before, string context,
            List<string> details, List<string> problems)
        {
            var missing = before - Count(ammo, id);
            if (missing <= 0) return;
            var restored = TryRestore(ammo, id, missing);
            var restoredCount = Count(ammo, id);
            details.Add("\"restored_count\": " + restoredCount.ToString(CultureInfo.InvariantCulture));
            problems.Add(restored && restoredCount == before
                ? context + ": " + missing.ToString(CultureInfo.InvariantCulture) + " item(s) restored, operation aborted"
                : context + ": NATIVE RESTORE FAILED, " + (before - restoredCount).ToString(CultureInfo.InvariantCulture) + " item(s) still missing (count now " + restoredCount.ToString(CultureInfo.InvariantCulture) + ")");
        }

        /// <summary>Re-adds ammo removed by an aborted release; mirrors the vac_add slot logic.</summary>
        private static bool TryRestore(Ammo ammo, Identifiable.Id id, int count)
        {
            var plan = InventoryRules.PlanAdd(Snapshot(ammo, id), count);
            if (plan == null) return false;
            return InventoryRules.Apply(plan, ammo.GetSlotCount, (index, amount) =>
            {
                var name = ammo.GetSlotName(index);
                if (name != id && name != Identifiable.Id.NONE) return false;
                if (!ammo.CouldAddToSlot(id, index, false) ||
                    ammo.GetSlotMaxCount(id, index) - ammo.GetSlotCount(index) < amount) return false;
                return ammo.MaybeAddToSpecificSlot(id, null, index, amount, false);
            }, true);
        }

        private static int Count(Ammo ammo, Identifiable.Id id)
        {
            var total = 0;
            for (var i = 0; i < ammo.GetUsableSlotCount(); i++)
                if (ammo.GetSlotName(i) == id) total = checked(total + ammo.GetSlotCount(i));
            return total;
        }

        private static List<SlotSnapshot> Snapshot(Ammo ammo, Identifiable.Id id)
        {
            var slots = new List<SlotSnapshot>();
            for (var i = 0; i < ammo.GetUsableSlotCount(); i++)
            {
                var name = ammo.GetSlotName(i);
                var match = name == id;
                var empty = name == Identifiable.Id.NONE;
                slots.Add(new SlotSnapshot(true, match, empty,
                    (match || empty) && ammo.CouldAddToSlot(id, i, false), ammo.GetSlotCount(i),
                    (match || empty) ? ammo.GetSlotMaxCount(id, i) : 0));
            }
            return slots;
        }

        private static string SlotJson(Ammo ammo, Identifiable.Id id)
        {
            var sb = new StringBuilder("[");
            var first = true;
            for (var i = 0; i < ammo.GetUsableSlotCount(); i++)
            {
                if (ammo.GetSlotName(i) != id) continue;
                if (!first) sb.Append(", ");
                first = false;
                sb.Append("{\"index\": ").Append(i.ToString(CultureInfo.InvariantCulture))
                  .Append(", \"count\": ").Append(ammo.GetSlotCount(i).ToString(CultureInfo.InvariantCulture)).Append("}");
            }
            return sb.Append("]").ToString();
        }
    }
}
