using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Il2CppInterop.Runtime;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Q008: native SR2 registration of imported species. Reads the Bridge-owned
    /// bridge-imported-species-spec v1 (only when an oracle targets the exact LAB executable),
    /// builds SlimeDefinition/IdentifiableType objects plus diets, registers every live index
    /// the SR2 runtime reads (Slimes array + _slimeDefinitionsByIdentifiable + group membership),
    /// clones the target-native placeholder prefab, and publishes a registration receipt with
    /// native read-back for each check. One failed species never blocks the others.
    /// In SR2 the identity model is object-based: IdentifiableType is a ScriptableObject and
    /// SlimeDefinition derives from it, so the imported definition object IS the identity.
    /// </summary>
    internal sealed class Sr2SpeciesRegistrar
    {
        private const string Realm = "SR2";
        private readonly string _specPath;
        private readonly string _outDir;
        private readonly string _runId;
        private readonly int _mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;
        private readonly Dictionary<string, Il2Cpp.SlimeDefinition> _defs = new Dictionary<string, Il2Cpp.SlimeDefinition>(StringComparer.Ordinal);
        private readonly Dictionary<string, Il2Cpp.IdentifiableType> _types = new Dictionary<string, Il2Cpp.IdentifiableType>(StringComparer.Ordinal);
        private readonly Dictionary<string, UnityEngine.GameObject> _prefabs = new Dictionary<string, UnityEngine.GameObject>(StringComparer.Ordinal);
        private readonly Dictionary<string, List<Il2Cpp.IdentifiableTypeGroup>> _defMirrorGroups = new Dictionary<string, List<Il2Cpp.IdentifiableTypeGroup>>(StringComparer.Ordinal);
        private readonly Dictionary<string, (Il2Cpp.IdentifiableType Type, List<Il2Cpp.IdentifiableTypeGroup> Groups)> _plortMirrorGroups = new Dictionary<string, (Il2Cpp.IdentifiableType, List<Il2Cpp.IdentifiableTypeGroup>)>(StringComparer.Ordinal);
        private readonly Dictionary<string, Il2Cpp.SlimeDefinition> _templates = new Dictionary<string, Il2Cpp.SlimeDefinition>(StringComparer.Ordinal);
        private bool _done;
        private float _readySince = -1f;
        private int _lastCount = -1;

        private Sr2SpeciesRegistrar(string specPath, string outDir, string runId)
        {
            _specPath = specPath;
            _outDir = outDir;
            _runId = runId;
        }

        internal static bool Requested => Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_SPEC") != null || PlayerContent.TryGet("SR2", out _);

        public static Sr2SpeciesRegistrar? TryCreate(bool labExecutable)
        {
            var spec = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_SPEC");
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OUT");
            var runId = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_RUN") ?? "";
            if (!labExecutable || string.IsNullOrEmpty(spec) || string.IsNullOrEmpty(outDir))
            {
                // Normal install: the launcher's pointer file, validated by PlayerContent (same containment as below).
                if (!PlayerContent.TryGet("SR2", out var player))
                {
                    if (PlayerContent.Problem.Length > 0) UnityEngine.Debug.Log("[SlimeBridge] imported content disabled: " + PlayerContent.Problem);
                    return null;
                }
                spec = player.Spec; runId = PlayerContent.RunId("SR2"); outDir = PlayerContent.RunOutput(player, runId);
            }
            if (!Path.IsPathRooted(spec) || !File.Exists(spec) || !Path.IsPathRooted(outDir) || !Directory.Exists(outDir)) return null;
            // Runtime containment: the mod enforces the Bridge-owned roots itself (the Python
            // callers are not trusted), with reparse refusal so junctions cannot redirect the spec
            // read or the receipt writes outside Lab/.
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (repoRoot == null)
            {
                UnityEngine.Debug.Log("[SlimeBridge] import spec refused: Bridge repo root not found");
                return null;
            }
            if (!LabSpecPathGuard.IsAllowedFile(Path.Combine(repoRoot, LabSpecPathGuard.SpecRelativeRoot), spec, out var specReason))
            {
                UnityEngine.Debug.Log("[SlimeBridge] import spec refused: " + specReason);
                return null;
            }
            if (!LabSpecPathGuard.IsAllowedDirectory(Path.Combine(repoRoot, LabSpecPathGuard.OutRelativeRoot), outDir, out var outReason))
            {
                UnityEngine.Debug.Log("[SlimeBridge] import output refused: " + outReason);
                return null;
            }
            if (runId.Length < 8 || runId.Length > 64 || runId.Any(c => !char.IsLetterOrDigit(c) && c != '-')) return null;
            Sr2ImportedSpawner.Configure(labExecutable);
            return new Sr2SpeciesRegistrar(spec, outDir, runId);
        }

        internal bool Done => _done;

        public bool TryResolveType(string canonicalId, out Il2Cpp.IdentifiableType type) => _types.TryGetValue(canonicalId, out type);

        public bool TryResolveDefinition(string canonicalId, out Il2Cpp.SlimeDefinition def) => _defs.TryGetValue(canonicalId, out def);

        public bool TryResolvePrefab(string canonicalId, out UnityEngine.GameObject prefab) => _prefabs.TryGetValue(canonicalId, out prefab);

        /// <summary>
        /// Re-assert imported membership in every live runtime group cache. Runtime caches are
        /// created lazily (some only after the world loads) and may be rebuilt from serialized
        /// data; world-time callers invoke this before imported-species operations. Idempotent.
        /// </summary>
        internal int EnsureRuntimeMembership()
        {
            var added = 0;
            var refDict = Il2Cpp.GameContext.Instance?.LookupDirector?._identifiableTypeByRefId;
            foreach (var pair in _defs)
            {
                try
                {
                    if (refDict != null && !string.IsNullOrEmpty(pair.Value.referenceId) &&
                        (!refDict.TryGetValue(pair.Value.referenceId, out var existing) || existing == null || existing.Pointer != pair.Value.Pointer))
                    { refDict[pair.Value.referenceId] = pair.Value; added++; }
                }
                catch (Exception) { }
                if (!_defMirrorGroups.TryGetValue(pair.Key, out var groups)) continue;
                foreach (var group in groups)
                {
                    try
                    {
                        var members = group.ImmediateMemberTypes;
                        if (members != null && !members.Contains(pair.Value)) members.Add(pair.Value);
                        var runtimeMembers = group._runtimeObject?._memberTypes;
                        if (runtimeMembers != null && !runtimeMembers.Contains(pair.Value)) { runtimeMembers.Add(pair.Value); added++; }
                    }
                    catch (Exception) { }
                }
            }
            foreach (var pair in _plortMirrorGroups)
            {
                try
                {
                    if (refDict != null && !string.IsNullOrEmpty(pair.Value.Type.referenceId) &&
                        (!refDict.TryGetValue(pair.Value.Type.referenceId, out var existingPlort) || existingPlort == null || existingPlort.Pointer != pair.Value.Type.Pointer))
                    { refDict[pair.Value.Type.referenceId] = pair.Value.Type; added++; }
                }
                catch (Exception) { }
                foreach (var group in pair.Value.Groups)
                {
                    try
                    {
                        var members = group.ImmediateMemberTypes;
                        if (members != null && !members.Contains(pair.Value.Type)) members.Add(pair.Value.Type);
                        var runtimeMembers = group._runtimeObject?._memberTypes;
                        if (runtimeMembers != null && !runtimeMembers.Contains(pair.Value.Type)) { runtimeMembers.Add(pair.Value.Type); added++; }
                    }
                    catch (Exception) { }
                }
            }
            return added;
        }

        /// <summary>
        /// Give every imported definition the template's placeholder appearances and register
        /// them with every live SlimeAppearanceDirector (HUD/inventory views). World-time
        /// callers invoke this before imported-species operations; the SR2 slot HUD resolves a
        /// chosen appearance per slime and NREs on an imported slime that has none. Idempotent.
        /// </summary>
        internal int EnsureAppearances()
        {
            var directors = UnityEngine.Object.FindObjectsOfType<Il2Cpp.SlimeAppearanceDirector>();
            var applied = 0;
            foreach (var pair in _defs)
            {
                if (!_templates.TryGetValue(pair.Key, out var template) || template == null) continue;
                var appearances = template.AppearancesDefault;
                if (appearances == null || appearances.Length == 0) continue;
                try { pair.Value.AppearancesDefault = appearances; }
                catch (Exception) { continue; }
                if (directors == null) continue;
                foreach (var director in directors)
                {
                    if (director == null) continue;
                    try
                    {
                        if (!director.IsAppearanceUnlocked(pair.Value, appearances[0]))
                            director.UnlockAppearance(pair.Value, appearances[0]);
                        if (director.GetChosenSlimeAppearance(pair.Value) == null)
                            director.UpdateChosenSlimeAppearance(pair.Value, appearances[0]);
                        applied++;
                    }
                    catch (Exception) { }
                }
            }
            return applied;
        }

        public void Tick(Action<string> log)
        {
            if (_done) return;
            if (System.Threading.Thread.CurrentThread.ManagedThreadId != _mainThreadId) return;
            try { TickOnMainThread(log); }
            catch (Exception ex)
            {
                _done = true;
                Publish("", new List<string> { "registrar failed: " + ex.GetType().Name + ": " + ex.Message }, new List<string>());
                log("species registrar failed: " + ex.GetType().Name + ": " + ex.Message);
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
            // Require the native registry to stay stable for two seconds, exactly like the collector.
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_readySince < 0f || slimes.Length != _lastCount)
            {
                _readySince = now;
                _lastCount = slimes.Length;
                return;
            }
            if (now - _readySince < 2f) return;
            _done = true;
            Register(context, definitions, log);
        }

        private sealed class EntryResult
        {
            internal ImportedSpeciesEntry Entry = null!;
            internal string Status = "FAILED";
            internal readonly List<string> Problems = new List<string>();
            internal readonly Dictionary<string, object?> Detail = new Dictionary<string, object?>();
            internal Il2Cpp.SlimeDefinition? Def;
            internal Il2Cpp.SlimeDefinition? Template;
            internal List<Il2Cpp.SlimeDefinition>? Parents;
            internal Il2Cpp.IdentifiableType? Plort;
            internal UnityEngine.GameObject? Clone;
            internal readonly List<Il2Cpp.IdentifiableTypeGroup> DefGroupsAdded = new List<Il2Cpp.IdentifiableTypeGroup>();
            internal readonly List<Il2Cpp.IdentifiableTypeGroup> PlortGroupsAdded = new List<Il2Cpp.IdentifiableTypeGroup>();
        }

        private void Register(Il2Cpp.GameContext context, Il2Cpp.SlimeDefinitions definitions, Action<string> log)
        {
            var errors = new List<string>();
            var results = new List<EntryResult>();
            var specSha = "";
            ImportedSpeciesSpecDoc? spec;
            try
            {
                var bytes = File.ReadAllBytes(_specPath);
                specSha = RegistrySnapshotFormat.Sha256Hex(bytes);
                var problems = new List<string>();
                spec = ImportedSpeciesSpecLoader.Load(Encoding.UTF8.GetString(bytes), problems);
                if (spec == null || problems.Count > 0)
                {
                    errors.Add("spec did not load cleanly: " + string.Join("; ", problems));
                    Publish(specSha, errors, new List<string>());
                    log("species registrar: spec refused");
                    return;
                }
                if (spec.schema != "bridge-imported-species-spec" || spec.schema_version != 1 || spec.realm != Realm)
                {
                    errors.Add("spec schema/realm mismatch");
                    Publish(specSha, errors, new List<string>());
                    return;
                }
            }
            catch (Exception ex)
            {
                errors.Add("spec read failed: " + ex.GetType().Name + ": " + ex.Message);
                Publish(specSha, errors, new List<string>());
                return;
            }

            var lookup = new RegistryLookup(context, definitions);
            lookup.Build();
            foreach (var entry in spec.entries) results.Add(RegisterEntry(definitions, lookup, entry));

            if (results.Any(r => r.Status == "REGISTERED"))
            {
                try { definitions.RefreshDefinitions(); }
                catch (Exception ex)
                {
                    errors.Add("RefreshDefinitions failed: " + ex.GetType().Name + ": " + ex.Message);
                    // Name the definitions whose eat map cannot be built (the native pass stops at the first one).
                    try
                    {
                        var failing = new List<string>();
                        foreach (var def in definitions.Slimes)
                        {
                            if (def == null) continue;
                            try { def.Diet?.RefreshEatMap(definitions, def); }
                            catch (Exception) { failing.Add((def.name ?? "?") + (results.Any(r => r.Def != null && r.Def.Pointer == def.Pointer) ? "(imported)" : "(native)")); }
                        }
                        errors.Add("eat map refresh failing for: " + (failing.Count == 0 ? "none on retry" : string.Join(", ", failing)));
                    }
                    catch (Exception probe) { errors.Add("eat map probe failed: " + probe.GetType().Name); }
                }
            }
            foreach (var result in results)
                if (result.Status == "REGISTERED" && result.Def != null && result.Parents != null)
                    result.Detail["largo_index"] = IndexLargo(definitions, result.Def, result.Parents);
            // The pairs above are added after the refresh built every eat map: rebuild the parents' maps so "a parent eats the
            // other's plort" really becomes the Largo (without this no parent ever turned into an added Largo).
            var parentsRefreshed = new HashSet<IntPtr>();
            foreach (var result in results)
            {
                if (result.Status != "REGISTERED" || result.Def == null || result.Parents == null) continue;
                foreach (var parent in result.Parents)
                {
                    if (parent == null || !parentsRefreshed.Add(parent.Pointer)) continue;
                    try { parent.Diet?.RefreshEatMap(definitions, parent); }
                    catch (Exception ex) { errors.Add("parent eat map refresh failed for " + (parent.name ?? "?") + ": " + ex.GetType().Name); }
                }
            }
            // Eat maps only exist after the refresh; a READY entry must have a working diet or it
            // fails closed and is rolled back so its receipt can never disagree with operability.
            foreach (var result in results)
            {
                if (result.Status != "REGISTERED" || result.Def == null) continue;
                var eatMap = result.Def.Diet?.EatMap;
                var count = eatMap?.Count ?? 0;
                string? refreshError = null;
                if (count < 1 && result.Def.Diet != null)
                {
                    // SR2 builds a definition's EatMap through SlimeDiet.RefreshEatMap; the global
                    // RefreshDefinitions() pass does not populate imported definitions.
                    try { result.Def.Diet.RefreshEatMap(definitions, result.Def); }
                    catch (Exception ex) { refreshError = ex.GetType().Name + ": " + ex.Message; }
                    eatMap = result.Def.Diet.EatMap;
                    count = eatMap?.Count ?? 0;
                }
                if (count < 1 && result.Def.Diet != null)
                {
                    // Fallback: compose the entries directly through the public EatMapEntry field surface.
                    // Entry shapes are copied from the template's native map where the food matches.
                    try
                    {
                        var diet = result.Def.Diet;
                        if (diet.EatMap == null)
                            diet.EatMap = new Il2CppSystem.Collections.Generic.List<Il2Cpp.SlimeDiet.EatMapEntry>();
                        diet.EatMap.Clear();
                        var templateMap = result.Template?.Diet?.EatMap;
                        Il2Cpp.SlimeDiet.EatMapEntry? ShapeFor(Il2Cpp.IdentifiableType food)
                        {
                            if (templateMap == null || food == null) return null;
                            for (var i = 0; i < templateMap.Count; i++)
                            {
                                var candidate = templateMap[i];
                                if (candidate?.EatsIdent != null && candidate.EatsIdent.Pointer == food.Pointer) return candidate;
                            }
                            return null;
                        }
                        var favoriteSet = new HashSet<IntPtr>();
                        var favoriteList = diet.FavoriteIdents;
                        if (favoriteList != null)
                            for (var f = 0; f < favoriteList.Length; f++)
                                if (favoriteList[f] != null) favoriteSet.Add(favoriteList[f].Pointer);
                        var seen = new HashSet<IntPtr>();
                        void AddEntry(Il2Cpp.IdentifiableType food)
                        {
                            if (food == null || !seen.Add(food.Pointer)) return;
                            var shape = ShapeFor(food);
                            var entry = new Il2Cpp.SlimeDiet.EatMapEntry
                            {
                                EatsIdent = food,
                                IsFavorite = favoriteSet.Contains(food.Pointer),
                                ProductionCount = shape != null && shape.ProductionCount > 0 ? shape.ProductionCount : 1,
                                FavoriteProductionCount = shape != null && shape.FavoriteProductionCount > 0
                                    ? shape.FavoriteProductionCount
                                    : (diet.FavoriteProductionCount > 0 ? diet.FavoriteProductionCount : 2),
                                ProducesIdent = result.Plort,
                                BecomesIdent = null,
                                Driver = shape != null ? shape.Driver : Il2CppMonomiPark.SlimeRancher.Slime.SlimeEmotions.Emotion.HUNGER,
                                ExtraDrive = shape?.ExtraDrive ?? 0f,
                                MinDrive = shape?.MinDrive ?? 0f,
                            };
                            diet.EatMap.Add(entry);
                        }
                        var dietGroups = diet.MajorFoodIdentifiableTypeGroups;
                        if (dietGroups != null)
                            for (var g = 0; g < dietGroups.Length; g++)
                            {
                                var members = dietGroups[g]?.ImmediateMemberTypes;
                                if (members == null) continue;
                                for (var m = 0; m < members.Count; m++) AddEntry(members[m]);
                            }
                        if (favoriteList != null)
                            for (var f = 0; f < favoriteList.Length; f++) AddEntry(favoriteList[f]);
                        var additionalList = diet.AdditionalFoodIdents;
                        if (additionalList != null)
                            for (var a = 0; a < additionalList.Length; a++) AddEntry(additionalList[a]);
                    }
                    catch (Exception ex) { result.Problems.Add("manual eat map failed: " + ex.GetType().Name + ": " + ex.Message); }
                    eatMap = result.Def.Diet.EatMap;
                    count = eatMap?.Count ?? 0;
                }
                result.Detail["eat_map_source"] = count >= 1 ? (refreshError == null ? "native_refresh" : "manual_fallback") : "none";
                if (count < 1 && refreshError != null)
                    result.Problems.Add("RefreshEatMap threw and no eat map could be built: " + refreshError);
                var templateDiet = result.Template?.Diet;
                if (templateDiet != null)
                {
                    try
                    {
                        result.Detail["template_diet_probe"] =
                            "groups=" + (templateDiet.MajorFoodIdentifiableTypeGroups?.Length ?? -1) +
                            "/fav=" + (templateDiet.FavoriteIdents?.Length ?? -1) +
                            "/add=" + (templateDiet.AdditionalFoodIdents?.Length ?? -1) +
                            "/prod=" + (templateDiet.ProduceIdents?.Length ?? -1) +
                            "/eatmap=" + (templateDiet.EatMap?.Count ?? -1);
                        // Decisive probe: can the native method run on the NATIVE template definition?
                        templateDiet.RefreshEatMap(definitions, result.Template!);
                        result.Detail["template_refresh_probe"] = "ok/eatmap=" + (templateDiet.EatMap?.Count ?? -1);
                    }
                    catch (Exception ex) { result.Detail["template_refresh_probe"] = "threw: " + ex.GetType().Name; }
                }
                // Post-refresh integrity: did the global refresh keep the imported definition registered?
                var postRefreshOk = false;
                try
                {
                    var stillInArray = ArrayContains(definitions.Slimes, result.Def);
                    var stillIndexed = definitions._slimeDefinitionsByIdentifiable.TryGetValue(result.Def, out var again) &&
                        again != null && again.Pointer == result.Def.Pointer;
                    result.Detail["post_refresh_in_array"] = stillInArray;
                    result.Detail["post_refresh_indexed"] = stillIndexed;
                    postRefreshOk = stillInArray && stillIndexed;
                }
                catch (Exception ex) { result.Problems.Add("post-refresh check threw: " + ex.GetType().Name); }
                if (!postRefreshOk)
                    result.Problems.Add("definition missing from the live registries after RefreshDefinitions; rolling back");
                result.Detail["eat_map_entries"] = count;
                if (count < 1)
                    result.Problems.Add("empty eat map after refresh; entry failed closed and rolled back");
                if (count < 1 || !postRefreshOk)
                {
                    result.Status = "FAILED";
                    _defs.Remove(result.Entry.canonical_id);
                    _defMirrorGroups.Remove(result.Entry.canonical_id);
                    _types.Remove(result.Entry.canonical_id);
                    if (result.Entry.plort != null && !string.IsNullOrEmpty(result.Entry.plort.canonical_id))
                    {
                        _types.Remove(result.Entry.plort.canonical_id);
                        // The runtime re-assertion loop must not resurrect a failed plort after rollback.
                        _plortMirrorGroups.Remove(result.Entry.plort.canonical_id);
                    }
                    _prefabs.Remove(result.Entry.canonical_id);
                    Rollback(definitions, result.Def, result.Plort, result.Clone, result.DefGroupsAdded, result.PlortGroupsAdded, result.Problems);
                }
            }

            var rows = results.Select(SerializeRow).ToList();
            Publish(specSha, errors, rows);
            log("species registrar: " + _defs.Count + " imported species registered, " + errors.Count + " errors");
        }

        private EntryResult RegisterEntry(Il2Cpp.SlimeDefinitions definitions, RegistryLookup lookup, ImportedSpeciesEntry entry)
        {
            var result = new EntryResult { Entry = entry };
            Il2Cpp.SlimeDefinition? def = null;
            Il2Cpp.IdentifiableType? plortType = null;
            UnityEngine.GameObject? clone = null;
            var plortRetargeted = true;
            var mutated = false;
            try
            {
                var entryProblems = entry.Problems();
                if (entryProblems.Count > 0) { result.Problems.AddRange(entryProblems); return result; }
                if (entry.status != "READY") { result.Problems.Add("entry status " + entry.status + " is not READY"); return result; }
                if (_defs.ContainsKey(entry.canonical_id)) { result.Problems.Add("duplicate canonical id in one run"); return result; }

                // Templates: symbolic "SlimeDefinition.<referenceId>" / "IdentifiableType.<referenceId>".
                if (!lookup.TrySlimeTemplate(entry.template.slime_native_ref, out var templateDef) || templateDef == null)
                { result.Problems.Add("unknown slime template " + entry.template.slime_native_ref); return result; }
                result.Template = templateDef;
                // The imported slime must satisfy the same group predicates the native template
                // does: mirror every group whose member list directly contains the template
                // definition (this is what vacpack slot predicates consult), with groupType as
                // the fallback when the template is in no direct membership list.
                var defMirrorGroups = lookup.GroupsContaining(templateDef);
                if (defMirrorGroups.Count == 0 && templateDef.groupType != null)
                    defMirrorGroups.Add(templateDef.groupType);
                if (defMirrorGroups.Count == 0)
                { result.Problems.Add("template slime has no group membership to mirror"); return result; }
                var hasPlort = !string.IsNullOrEmpty(entry.plort.canonical_id);
                Il2Cpp.IdentifiableType? templatePlort = null;
                if (hasPlort && (!lookup.TryTypeTemplate(entry.plort.template_native_ref, out templatePlort) || templatePlort == null))
                { result.Problems.Add("unknown plort template " + entry.plort.template_native_ref); return result; }
                // The imported plort must satisfy the same group predicates the native plort does:
                // mirror every group whose member list directly contains the template plort.
                var plortMirrorGroups = new List<Il2Cpp.IdentifiableTypeGroup>();
                if (hasPlort)
                {
                    plortMirrorGroups = lookup.GroupsContaining(templatePlort!);
                    if (plortMirrorGroups.Count == 0 && templatePlort!.groupType != null)
                        plortMirrorGroups.Add(templatePlort.groupType);
                    if (plortMirrorGroups.Count == 0)
                    { result.Problems.Add("template plort has no group membership to mirror"); return result; }
                }

                // A Largo takes its two parents (an imported species registered earlier in this run, else a native SR2 slime) and
                // lives on their combined diet; a species resolves its own diet rows.
                var isLargo = entry.largo != null && entry.largo.parents != null && entry.largo.parents.Length == 2;
                var parents = new List<Il2Cpp.SlimeDefinition>();
                if (isLargo)
                {
                    foreach (var slug in entry.largo!.parents)
                    {
                        Il2Cpp.SlimeDefinition? parent = null;
                        if (_defs.TryGetValue("slime." + slug, out var imported)) parent = imported;
                        else if (slug.Length > 0 && lookup.TrySlimeTemplate("SlimeDefinition." + char.ToUpperInvariant(slug[0]) + slug.Substring(1), out var native)) parent = native;
                        if (parent == null || parent.Diet == null || parent.Diet.ProduceIdents == null || parent.Diet.ProduceIdents.Length == 0)
                        { result.Problems.Add("largo parent '" + slug + "' is not a registered slime with a plort"); return result; }
                        parents.Add(parent);
                    }
                    result.Parents = parents;
                }

                // Diet resolution happens BEFORE any native mutation: unknown rows are a hard refusal.
                var dietProblems = new List<string>();
                var groups = lookup.ResolveGroups(entry.diet.major_food_groups, dietProblems);
                var favorites = lookup.ResolveTypes(entry.diet.favorites, dietProblems);
                var additional = lookup.ResolveTypes(entry.diet.additional_foods, dietProblems);
                var largoProduces = new List<Il2Cpp.IdentifiableType>();
                var largoFavoriteCount = 2;
                if (isLargo)
                {
                    void Merge<T>(List<T> into, Il2CppReferenceArray<T>? from) where T : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
                    {
                        if (from == null) return;
                        for (var i = 0; i < from.Length; i++)
                            if (from[i] != null && !into.Exists(x => x.Pointer == from[i].Pointer)) into.Add(from[i]);
                    }
                    foreach (var parent in parents)
                    {
                        Merge(groups, parent.Diet.MajorFoodIdentifiableTypeGroups);
                        Merge(favorites, parent.Diet.FavoriteIdents);
                        Merge(additional, parent.Diet.AdditionalFoodIdents);
                        Merge(largoProduces, parent.Diet.ProduceIdents);
                        largoFavoriteCount = Math.Max(largoFavoriteCount, parent.Diet.FavoriteProductionCount);
                    }
                }
                if (dietProblems.Count > 0)
                { result.Problems.Add("diet refused before any native mutation: " + string.Join("; ", dietProblems)); return result; }
                if (groups.Count == 0)
                { result.Problems.Add("diet refused before any native mutation: no major food groups resolved"); return result; }

                // ---- Build imported objects (no native mutation yet). ----
                def = UnityEngine.ScriptableObject.CreateInstance(Il2CppType.Of<Il2Cpp.SlimeDefinition>())
                    .TryCast<Il2Cpp.SlimeDefinition>();
                if (def == null) { result.Problems.Add("SlimeDefinition allocation failed"); return result; }
                def.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
                def.name = entry.display_name;
                def.referenceId = entry.native_ref_id ?? "";
                def.stableHashedId = entry.native_id;
                def.initializedHashId = true;
                def.IsLargo = isLargo;
                def.CanLargofy = !isLargo && entry.flags.can_largofy;
                def.PrefabScale = templateDef.PrefabScale;
                def.BaseSlimes = isLargo ? new Il2CppReferenceArray<Il2Cpp.SlimeDefinition>(parents.ToArray())
                                         : new Il2CppReferenceArray<Il2Cpp.SlimeDefinition>(0);
                def.AppearancesDefault = new Il2CppReferenceArray<Il2Cpp.SlimeAppearance>(0);
                def.AppearancesDynamic = new Il2CppSystem.Collections.Generic.List<Il2Cpp.SlimeAppearance>();
                def.FavoriteToyIdents = new Il2CppReferenceArray<Il2Cpp.ToyDefinition>(0);
                def.SlimeModules = new Il2CppReferenceArray<UnityEngine.GameObject>(0);
                def.NativeZones = new Il2CppReferenceArray<Il2CppMonomiPark.SlimeRancher.World.ZoneDefinition>(0);
                def.groupType = templateDef.groupType;
                def.Categories = templateDef.Categories;

                var diet = new Il2Cpp.SlimeDiet();
                diet.MajorFoodIdentifiableTypeGroups = lookup.ToGroupArray(groups);
                diet.FavoriteIdents = lookup.ToTypeArray(favorites);
                diet.AdditionalFoodIdents = lookup.ToTypeArray(additional);
                // Native construction is required because RefreshEatMap appends into this list.
                diet.EatMap = new Il2CppSystem.Collections.Generic.List<Il2Cpp.SlimeDiet.EatMapEntry>();
                diet.FavoriteProductionCount = isLargo ? largoFavoriteCount
                    : entry.diet.favorite_production_count > 0 ? entry.diet.favorite_production_count : 2;
                // SR2 1.x RefreshEatMap reads the shared native groups (edible plorts, stable/unstable resources) and the
                // Tarr transformation; a diet built from scratch left them null and every imported refresh threw.
                var nativeDiet = templateDef.Diet;
                if (nativeDiet != null)
                {
                    diet.EdiblePlortIdentifiableTypeGroup = nativeDiet.EdiblePlortIdentifiableTypeGroup;
                    diet.StableResourceIdentifiableTypeGroup = nativeDiet.StableResourceIdentifiableTypeGroup;
                    diet.UnstableResourceIdentifiableTypeGroup = nativeDiet.UnstableResourceIdentifiableTypeGroup;
                    diet.BecomesOnTarrifyIdentifiableType = nativeDiet.BecomesOnTarrifyIdentifiableType;
                    diet.UnstablePlort = nativeDiet.UnstablePlort;
                }

                if (hasPlort)
                {
                    plortType = UnityEngine.ScriptableObject.CreateInstance(Il2CppType.Of<Il2Cpp.IdentifiableType>())
                        .TryCast<Il2Cpp.IdentifiableType>();
                    if (plortType == null) { result.Problems.Add("plort IdentifiableType allocation failed"); return result; }
                    plortType.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
                    plortType.name = entry.plort.display_name;
                    plortType.referenceId = entry.plort.native_ref_id ?? "";
                    plortType.stableHashedId = entry.plort.native_id;
                    plortType.initializedHashId = true;
                    plortType.IsPlort = true;
                    plortType.groupType = templatePlort!.groupType;
                    plortType.Categories = templatePlort.Categories;
                    if (templatePlort.prefab == null) { result.Problems.Add("template plort prefab unavailable"); return result; }
                    var plortClone = UnityEngine.Object.Instantiate(templatePlort.prefab);
                    plortClone.name = "SlimeBridgeImport_" + entry.canonical_id + "_plort";
                    plortClone.SetActive(false);
                    UnityEngine.Object.DontDestroyOnLoad(plortClone);
                    plortRetargeted = RetargetIdentifiables(plortClone, plortType);
                    plortType.prefab = plortClone;
                    result.Detail["plort_prefab_retargeted"] = plortRetargeted;
                }
                diet.ProduceIdents = isLargo ? new Il2CppReferenceArray<Il2Cpp.IdentifiableType>(largoProduces.ToArray())
                    : plortType != null
                    ? new Il2CppReferenceArray<Il2Cpp.IdentifiableType>(new Il2Cpp.IdentifiableType[] { plortType })
                    : new Il2CppReferenceArray<Il2Cpp.IdentifiableType>(0);
                def.Diet = diet;

                // ---- Placeholder prefab clone. ----
                if (templateDef.prefab == null)
                {
                    if (plortType != null) DestroyQuietly(plortType.prefab);
                    result.Problems.Add("template prefab unavailable");
                    return result;
                }
                clone = UnityEngine.Object.Instantiate(templateDef.prefab);
                clone.name = "SlimeBridgeImport_" + entry.canonical_id;
                clone.SetActive(false);
                UnityEngine.Object.DontDestroyOnLoad(clone);
                var retargeted = RetargetIdentifiables(clone, def);
                def.prefab = clone;

                // ---- Native mutations (rolled back on any failure below). ----
                var old = definitions.Slimes;
                var grown = new Il2CppReferenceArray<Il2Cpp.SlimeDefinition>(old.Length + 1);
                for (var i = 0; i < old.Length; i++) grown[i] = old[i];
                grown[old.Length] = def;
                definitions.Slimes = grown;
                mutated = true;
                definitions._slimeDefinitionsByIdentifiable[def] = def;
                // The save serializer/reader resolves types by reference id through this dict;
                // runtime-created types must be present or saved ammo entries cannot resolve.
                var refDict = Il2Cpp.GameContext.Instance?.LookupDirector?._identifiableTypeByRefId;
                if (refDict != null && !string.IsNullOrEmpty(def.referenceId))
                    refDict[def.referenceId] = def;
                if (def.groupType != null && !def.groupType.ImmediateMemberTypes.Contains(def)) def.groupType.ImmediateMemberTypes.Add(def);
                foreach (var mirrorGroup in defMirrorGroups)
                {
                    try
                    {
                        var members = mirrorGroup.ImmediateMemberTypes;
                        if (members != null && !members.Contains(def)) members.Add(def);
                        // IdentifiableTypeGroup.IsMember consults the group's runtime object cache
                        // (built at game init), not the serialized member list: mirror into both.
                        var runtime = mirrorGroup._runtimeObject;
                        var runtimeMembers = runtime?._memberTypes;
                        if (runtime != null && runtimeMembers != null && !runtimeMembers.Contains(def))
                            runtimeMembers.Add(def);
                        result.DefGroupsAdded.Add(mirrorGroup);
                    }
                    catch (Exception ex) { result.Problems.Add("group mirror failed on " + (mirrorGroup.name ?? "") + ": " + ex.GetType().Name); }
                }
                if (plortType != null)
                {
                    foreach (var mirrorGroup in plortMirrorGroups)
                    {
                        try
                        {
                            var members = mirrorGroup.ImmediateMemberTypes;
                            if (members != null && !members.Contains(plortType)) members.Add(plortType);
                            var runtime = mirrorGroup._runtimeObject;
                            var runtimeMembers = runtime?._memberTypes;
                            if (runtime != null && runtimeMembers != null && !runtimeMembers.Contains(plortType))
                                runtimeMembers.Add(plortType);
                            result.PlortGroupsAdded.Add(mirrorGroup);
                        }
                        catch (Exception ex) { result.Problems.Add("plort group mirror failed on " + (mirrorGroup.name ?? "") + ": " + ex.GetType().Name); }
                    }
                    if (refDict != null && !string.IsNullOrEmpty(plortType.referenceId))
                        refDict[plortType.referenceId] = plortType;
                }

                // ---- Read-back through native accessors. ----
                var arrayHas = ArrayContains(definitions.Slimes, def);
                var indexHas = definitions._slimeDefinitionsByIdentifiable.TryGetValue(def, out var indexed) &&
                    indexed != null && indexed.Pointer == def.Pointer;
                var groupHas = true;
                foreach (var mirrorGroup in defMirrorGroups)
                {
                    var members = mirrorGroup.ImmediateMemberTypes;
                    if (members == null || !members.Contains(def)) { groupHas = false; break; }
                }
                var plortGroupHas = true;
                if (plortType != null)
                {
                    foreach (var mirrorGroup in plortMirrorGroups)
                    {
                        var members = mirrorGroup.ImmediateMemberTypes;
                        if (members == null || !members.Contains(plortType)) { plortGroupHas = false; break; }
                    }
                }
                result.Detail["definition_in_array"] = arrayHas;
                result.Detail["definition_indexed"] = indexHas;
                result.Detail["group_member"] = groupHas;
                result.Detail["plort_group_member"] = plortGroupHas;
                result.Detail["prefab_retargeted"] = retargeted;
                result.Detail["plort_expected"] = plortType != null;

                if (!arrayHas || !indexHas || !groupHas || !plortGroupHas || !retargeted || !plortRetargeted)
                {
                    result.Detail["gate_groups"] = DescribeGroups(defMirrorGroups, def);
                    if (plortType != null) result.Detail["gate_plort_groups"] = DescribeGroups(plortMirrorGroups, plortType);
                    result.Problems.Add("native read-back failed after mutation; rolling back");
                    Rollback(definitions, def, plortType, clone, result.DefGroupsAdded, result.PlortGroupsAdded, result.Problems);
                    mutated = false;
                    return result;
                }

                _defs[entry.canonical_id] = def;
                _types[entry.canonical_id] = def;
                if (plortType != null && !string.IsNullOrEmpty(entry.plort.canonical_id)) _types[entry.plort.canonical_id] = plortType;
                _prefabs[entry.canonical_id] = clone;
                _defMirrorGroups[entry.canonical_id] = defMirrorGroups;
                _templates[entry.canonical_id] = templateDef;
                if (plortType != null && !string.IsNullOrEmpty(entry.plort.canonical_id))
                    _plortMirrorGroups[entry.plort.canonical_id] = (plortType, plortMirrorGroups);
                result.Detail["imported_visual_setup"] = Sr2ImportedSpawner.Register(def, entry, clone, templateDef);
                result.Detail["imported_visual"] = Sr2ImportedSpawner.Proof(clone);
                result.Def = def;
                result.Plort = plortType;
                result.Clone = clone;
                result.Status = "REGISTERED";
                return result;
            }
            catch (Exception ex)
            {
                result.Problems.Add("registration threw: " + ex.GetType().Name + ": " + ex.Message);
                if (mutated)
                {
                    try { Rollback(definitions, def, plortType, clone, result.DefGroupsAdded, result.PlortGroupsAdded, result.Problems); }
                    catch (Exception rex) { result.Problems.Add("rollback threw: " + rex.GetType().Name); }
                }
                else if (clone != null || (plortType != null && plortType.prefab != null))
                {
                    DestroyQuietly(clone);
                    if (plortType != null) DestroyQuietly(plortType.prefab);
                }
                _defs.Remove(entry.canonical_id);
                _types.Remove(entry.canonical_id);
                _prefabs.Remove(entry.canonical_id);
                return result;
            }
        }
        /// <summary>
        /// SlimeDefinitions maps "a slime eats the other parent's plort" / "the two parent slimes" to the Largo; the game builds those
        /// dictionaries from its own definitions, so a registered Largo is added by hand (both key orders) when the refresh did not.
        /// Imported parents also become largofiable. Returns what happened for the receipt.
        /// </summary>
        private static string IndexLargo(Il2Cpp.SlimeDefinitions definitions, Il2Cpp.SlimeDefinition largo, List<Il2Cpp.SlimeDefinition> parents)
        {
            try
            {
                var plorts = new[] { parents[0].Diet.ProduceIdents[0], parents[1].Diet.ProduceIdents[0] };
                var byPlorts = definitions._largoDefinitionByBasePlorts;
                var byDefinitions = definitions._largoDefinitionByBaseDefinitions;
                if (byPlorts == null || byDefinitions == null) return "dictionaries unavailable";
                var added = 0;
                foreach (var order in new[] { new[] { 0, 1 }, new[] { 1, 0 } })
                {
                    var plortKey = new Il2Cpp.SlimeDefinitions.PlortPair(plorts[order[0]], plorts[order[1]]);
                    if (!byPlorts.ContainsKey(plortKey)) { byPlorts[plortKey] = largo; added++; }
                    var definitionKey = new Il2Cpp.SlimeDefinitions.SlimeDefinitionPair(parents[order[0]], parents[order[1]]);
                    if (!byDefinitions.ContainsKey(definitionKey)) { byDefinitions[definitionKey] = largo; added++; }
                }
                foreach (var parent in parents)
                    if (!parent.CanLargofy) parent.CanLargofy = true;
                return "indexed (" + added + " new keys)";
            }
            catch (Exception ex) { return "failed: " + ex.GetType().Name + ": " + ex.Message; }
        }

        private static void DestroyQuietly(UnityEngine.GameObject? go)
        {
            if (go == null) return;
            try { UnityEngine.Object.Destroy(go); } catch { }
        }

        private static bool RetargetIdentifiables(UnityEngine.GameObject clone, Il2Cpp.IdentifiableType def)
        {
            var found = false;
            var identifiables = clone.GetComponentsInChildren<Il2Cpp.Identifiable>(true);
            if (identifiables != null)
            {
                for (var i = 0; i < identifiables.Length; i++)
                {
                    var ident = identifiables[i];
                    if (ident == null) continue;
                    ident.identType = def;
                    found = true;
                }
            }
            return found;
        }

        private static string DescribeGroups(List<Il2Cpp.IdentifiableTypeGroup> groups, Il2Cpp.IdentifiableType member)
        {
            var sb = new StringBuilder();
            foreach (var group in groups)
            {
                if (sb.Length > 0) sb.Append(" | ");
                sb.Append(group.name ?? "?");
                try
                {
                    var contains = group.ImmediateMemberTypes != null && group.ImmediateMemberTypes.Contains(member);
                    var runtimeMembers = group._runtimeObject?._memberTypes;
                    sb.Append(":contains=").Append(contains ? "1" : "0");
                    sb.Append(":runtime=").Append(runtimeMembers == null ? "null" : runtimeMembers.Count.ToString(CultureInfo.InvariantCulture));
                    sb.Append(":rcontains=").Append(runtimeMembers != null && runtimeMembers.Contains(member) ? "1" : "0");
                    sb.Append(":isMember=").Append(group.IsMember(member) ? "1" : "0");
                }
                catch (Exception ex) { sb.Append(":threw=").Append(ex.GetType().Name); }
            }
            return sb.ToString();
        }

        private static bool ArrayContains(Il2CppReferenceArray<Il2Cpp.SlimeDefinition> array, Il2Cpp.SlimeDefinition def)
        {
            for (var i = 0; i < array.Length; i++)
                if (array[i] != null && array[i].Pointer == def.Pointer) return true;
            return false;
        }

        private static void Rollback(Il2Cpp.SlimeDefinitions definitions, Il2Cpp.SlimeDefinition? def,
            Il2Cpp.IdentifiableType? plortType, UnityEngine.GameObject? clone,
            List<Il2Cpp.IdentifiableTypeGroup> defGroupsAdded,
            List<Il2Cpp.IdentifiableTypeGroup> plortGroupsAdded, List<string> problems)
        {
            if (def == null) return;
            Sr2ImportedSpawner.Unregister(def);
            try
            {
                var current = definitions.Slimes;
                var kept = new List<Il2Cpp.SlimeDefinition>();
                for (var i = 0; i < current.Length; i++)
                    if (current[i] != null && current[i].Pointer != def.Pointer) kept.Add(current[i]);
                var shrunk = new Il2CppReferenceArray<Il2Cpp.SlimeDefinition>(kept.Count);
                for (var i = 0; i < kept.Count; i++) shrunk[i] = kept[i];
                definitions.Slimes = shrunk;
                var index = definitions._slimeDefinitionsByIdentifiable;
                if (index.ContainsKey(def)) index.Remove(def);
                var refDict = Il2Cpp.GameContext.Instance?.LookupDirector?._identifiableTypeByRefId;
                if (refDict != null && !string.IsNullOrEmpty(def.referenceId) &&
                    refDict.TryGetValue(def.referenceId, out var existingRef) && existingRef != null && existingRef.Pointer == def.Pointer)
                    refDict.Remove(def.referenceId);
                if (def.groupType != null && def.groupType.ImmediateMemberTypes.Contains(def)) def.groupType.ImmediateMemberTypes.Remove(def);
                foreach (var group in defGroupsAdded)
                {
                    try
                    {
                        if (group.ImmediateMemberTypes != null && group.ImmediateMemberTypes.Contains(def)) group.ImmediateMemberTypes.Remove(def);
                        var runtimeMembers = group._runtimeObject?._memberTypes;
                        if (runtimeMembers != null && runtimeMembers.Contains(def)) runtimeMembers.Remove(def);
                    }
                    catch (Exception) { }
                }
            }
            catch (Exception ex) { problems.Add("rollback partial: " + ex.GetType().Name); }
            if (plortType != null)
            {
                try
                {
                    foreach (var mirrorGroup in plortGroupsAdded)
                    {
                        if (mirrorGroup.ImmediateMemberTypes != null && mirrorGroup.ImmediateMemberTypes.Contains(plortType))
                            mirrorGroup.ImmediateMemberTypes.Remove(plortType);
                        var runtimeMembers = mirrorGroup._runtimeObject?._memberTypes;
                        if (runtimeMembers != null && runtimeMembers.Contains(plortType)) runtimeMembers.Remove(plortType);
                    }
                    var plortRefDict = Il2Cpp.GameContext.Instance?.LookupDirector?._identifiableTypeByRefId;
                    if (plortRefDict != null && !string.IsNullOrEmpty(plortType.referenceId) &&
                        plortRefDict.TryGetValue(plortType.referenceId, out var existingPlortRef) && existingPlortRef != null && existingPlortRef.Pointer == plortType.Pointer)
                        plortRefDict.Remove(plortType.referenceId);
                    DestroyQuietly(plortType.prefab);
                }
                catch (Exception ex) { problems.Add("plort rollback partial: " + ex.GetType().Name); }
            }
            if (clone != null)
            {
                try { UnityEngine.Object.Destroy(clone); } catch { }
            }
        }

        private static string SerializeRow(EntryResult result)
        {
            var entry = result.Entry;
            var sb = new StringBuilder(512);
            sb.Append("    {\"canonical_id\": ").Append(RegistrySnapshotFormat.Quote(entry.canonical_id));
            sb.Append(", \"display_name\": ").Append(RegistrySnapshotFormat.Quote(entry.display_name));
            sb.Append(", \"status\": ").Append(RegistrySnapshotFormat.Quote(result.Status));
            sb.Append(", \"native_id\": ").Append(entry.native_id.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"native_ref_id\": ").Append(RegistrySnapshotFormat.Quote(entry.native_ref_id ?? ""));
            sb.Append(", \"problems\": [");
            for (var i = 0; i < result.Problems.Count; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(RegistrySnapshotFormat.Quote(result.Problems[i]));
            }
            sb.Append("]");
            if (result.Detail.Count > 0)
            {
                sb.Append(", \"checks\": {");
                var first = true;
                foreach (var pair in result.Detail.OrderBy(p => p.Key, StringComparer.Ordinal))
                {
                    if (!first) sb.Append(", ");
                    first = false;
                    sb.Append(RegistrySnapshotFormat.Quote(pair.Key)).Append(": ");
                    if (pair.Value is bool flag) sb.Append(flag ? "true" : "false");
                    else if (pair.Value is int number) sb.Append(number.ToString(CultureInfo.InvariantCulture));
                    else sb.Append(RegistrySnapshotFormat.Quote(Convert.ToString(pair.Value, CultureInfo.InvariantCulture) ?? ""));
                }
                sb.Append("}");
            }
            sb.Append("}");
            return sb.ToString();
        }

        private void Publish(string specSha, List<string> errors, List<string> rows)
        {
            var sb = new StringBuilder(4096);
            sb.Append("{\n  \"schema\": \"bridge-imported-species-receipt\",\n  \"schema_version\": 1");
            sb.Append(",\n  \"realm\": \"").Append(Realm).Append("\"");
            sb.Append(",\n  \"run_id\": ").Append(RegistrySnapshotFormat.Quote(_runId));
            sb.Append(",\n  \"spec_sha256\": ").Append(RegistrySnapshotFormat.Quote(specSha));
            sb.Append(",\n  \"registered_count\": ").Append(_defs.Count.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\n  \"entries\": [");
            for (var i = 0; i < rows.Count; i++) sb.Append(i == 0 ? "\n" : ",\n").Append(rows[i]);
            sb.Append(rows.Count == 0 ? "]" : "\n  ]");
            sb.Append(",\n  \"errors\": [");
            for (var i = 0; i < errors.Count; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(RegistrySnapshotFormat.Quote(errors[i]));
            }
            sb.Append("]\n}\n");
            RegistrySnapshotFormat.PublishCaptureFile(_outDir, RegistrySnapshotFormat.ImportedSpeciesReceiptFileName, sb.ToString());
        }

        /// <summary>Reference index over live SR2 registries; built once per run.</summary>
        private sealed class RegistryLookup
        {
            private readonly Il2Cpp.GameContext _context;
            private readonly Il2Cpp.SlimeDefinitions _definitions;
            private readonly Dictionary<string, Il2Cpp.SlimeDefinition> _slimesByRef = new Dictionary<string, Il2Cpp.SlimeDefinition>(StringComparer.Ordinal);
            private readonly Dictionary<string, Il2Cpp.IdentifiableType> _typesByRef = new Dictionary<string, Il2Cpp.IdentifiableType>(StringComparer.Ordinal);
            private readonly Dictionary<string, Il2Cpp.IdentifiableTypeGroup> _groupsByRef = new Dictionary<string, Il2Cpp.IdentifiableTypeGroup>(StringComparer.Ordinal);
            private readonly List<Il2Cpp.IdentifiableTypeGroup> _allGroups = new List<Il2Cpp.IdentifiableTypeGroup>();

            internal RegistryLookup(Il2Cpp.GameContext context, Il2Cpp.SlimeDefinitions definitions)
            {
                _context = context;
                _definitions = definitions;
            }

            internal void Build()
            {
                var slimes = _definitions.Slimes;
                for (var i = 0; i < slimes.Length; i++)
                {
                    var def = slimes[i];
                    if (def == null) continue;
                    var reference = def.referenceId;
                    if (string.IsNullOrEmpty(reference)) continue;
                    if (!_slimesByRef.ContainsKey(reference)) _slimesByRef[reference] = def;
                    if (!_typesByRef.ContainsKey(reference)) _typesByRef[reference] = def;
                    var diet = def.Diet;
                    if (diet == null) continue;
                    AddTypes(diet.ProduceIdents);
                    AddTypes(diet.FavoriteIdents);
                    AddTypes(diet.AdditionalFoodIdents);
                }
                var groups = _context.LookupDirector?._allIdentifiableTypeGroups?.items;
                if (groups != null)
                {
                    for (var g = 0; g < groups.Count; g++)
                    {
                        var group = groups[g];
                        if (group == null) continue;
                        string name;
                        try { name = group.ReferenceId; } catch (Exception) { name = null; }
                        if (string.IsNullOrEmpty(name)) name = group.name ?? "";
                        if (!string.IsNullOrEmpty(name) && !_groupsByRef.ContainsKey(name)) _groupsByRef[name] = group;
                        _allGroups.Add(group);
                        var members = group.ImmediateMemberTypes;
                        if (members == null) continue;
                        for (var m = 0; m < members.Count; m++)
                        {
                            var member = members[m];
                            if (member == null) continue;
                            var reference = member.referenceId;
                            if (!string.IsNullOrEmpty(reference) && !_typesByRef.ContainsKey(reference)) _typesByRef[reference] = member;
                        }
                    }
                }
            }

            private void AddTypes(Il2CppReferenceArray<Il2Cpp.IdentifiableType>? array)
            {
                if (array == null) return;
                for (var i = 0; i < array.Length; i++)
                {
                    var item = array[i];
                    if (item == null) continue;
                    var reference = item.referenceId;
                    if (!string.IsNullOrEmpty(reference) && !_typesByRef.ContainsKey(reference)) _typesByRef[reference] = item;
                }
            }

            /// <summary>Groups whose member list directly contains the given type (pointer identity).</summary>
            internal List<Il2Cpp.IdentifiableTypeGroup> GroupsContaining(Il2Cpp.IdentifiableType member)
            {
                var groups = new List<Il2Cpp.IdentifiableTypeGroup>();
                foreach (var group in _allGroups)
                {
                    var members = group.ImmediateMemberTypes;
                    if (members == null) continue;
                    for (var m = 0; m < members.Count; m++)
                    {
                        var candidate = members[m];
                        if (candidate != null && candidate.Pointer == member.Pointer) { groups.Add(group); break; }
                    }
                }
                return groups;
            }

            internal bool TrySlimeTemplate(string reference, out Il2Cpp.SlimeDefinition? def)
            {
                def = null;
                if (string.IsNullOrEmpty(reference)) return false;
                // SR2 referenceId values carry the type prefix ("SlimeDefinition.Tabby"); accept the
                // full string first and a bare suffix as a fallback.
                if (_slimesByRef.TryGetValue(reference, out def) && def != null) return true;
                var suffix = StripPrefix(reference, "SlimeDefinition.");
                return suffix != null && _slimesByRef.TryGetValue(suffix, out def) && def != null;
            }

            internal bool TryTypeTemplate(string reference, out Il2Cpp.IdentifiableType? type)
            {
                type = null;
                if (string.IsNullOrEmpty(reference)) return false;
                if (_typesByRef.TryGetValue(reference, out type) && type != null) return true;
                var suffix = StripPrefix(reference, "IdentifiableType.");
                return suffix != null && _typesByRef.TryGetValue(suffix, out type) && type != null;
            }

            private static string? StripPrefix(string reference, string prefix) =>
                !string.IsNullOrEmpty(reference) && reference.StartsWith(prefix, StringComparison.Ordinal)
                    ? reference.Substring(prefix.Length) : null;

            internal List<Il2Cpp.IdentifiableTypeGroup> ResolveGroups(string[]? refs, List<string> problems)
            {
                var resolved = new List<Il2Cpp.IdentifiableTypeGroup>();
                foreach (var reference in refs ?? new string[0])
                {
                    if (!string.IsNullOrEmpty(reference) && _groupsByRef.TryGetValue(reference, out var group))
                        resolved.Add(group);
                    else
                        problems.Add("unknown food group " + reference);
                }
                return resolved;
            }

            internal List<Il2Cpp.IdentifiableType> ResolveTypes(string[]? refs, List<string> problems)
            {
                var resolved = new List<Il2Cpp.IdentifiableType>();
                foreach (var reference in refs ?? new string[0])
                {
                    if (!string.IsNullOrEmpty(reference) && _typesByRef.TryGetValue(reference, out var type))
                        resolved.Add(type);
                    else
                        problems.Add("unknown identifiable reference " + reference);
                }
                return resolved;
            }

            internal Il2CppReferenceArray<Il2Cpp.IdentifiableTypeGroup> ToGroupArray(List<Il2Cpp.IdentifiableTypeGroup> groups)
            {
                var array = new Il2CppReferenceArray<Il2Cpp.IdentifiableTypeGroup>(groups.Count);
                for (var i = 0; i < groups.Count; i++) array[i] = groups[i];
                return array;
            }

            internal Il2CppReferenceArray<Il2Cpp.IdentifiableType> ToTypeArray(List<Il2Cpp.IdentifiableType> types)
            {
                var array = new Il2CppReferenceArray<Il2Cpp.IdentifiableType>(types.Count);
                for (var i = 0; i < types.Count; i++) array[i] = types[i];
                return array;
            }
        }
    }
}
