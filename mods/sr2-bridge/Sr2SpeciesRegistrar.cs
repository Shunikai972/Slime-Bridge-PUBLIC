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
        private readonly Dictionary<string, List<Il2Cpp.IdentifiableTypeGroup>> _defAncestorGroups = new Dictionary<string, List<Il2Cpp.IdentifiableTypeGroup>>(StringComparer.Ordinal);
        private readonly Dictionary<string, (Il2Cpp.IdentifiableType Type, List<Il2Cpp.IdentifiableTypeGroup> Groups, List<Il2Cpp.IdentifiableTypeGroup> Ancestors)> _plortMirrorGroups =
            new Dictionary<string, (Il2Cpp.IdentifiableType, List<Il2Cpp.IdentifiableTypeGroup>, List<Il2Cpp.IdentifiableTypeGroup>)>(StringComparer.Ordinal);
        private readonly Dictionary<string, Il2Cpp.SlimeDefinition> _templates = new Dictionary<string, Il2Cpp.SlimeDefinition>(StringComparer.Ordinal);
        private readonly Dictionary<string, Il2Cpp.SlimeAppearance> _ownedAppearances = new Dictionary<string, Il2Cpp.SlimeAppearance>(StringComparer.Ordinal);
        private IntPtr _syncedScenePtr;
        private bool _done;
        private float _readySince = -1f;
        private int _lastCount = -1;

        internal static Sr2SpeciesRegistrar? Active { get; private set; }

        internal static void ClearActive() { Active = null; }

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

        /// <summary>Canonical ids registered in this session (dev tools).</summary>
        internal IEnumerable<string> Canonicals => _defs.Keys;

        /// <summary>All registered canonical ids including imported plorts.</summary>
        internal IEnumerable<string> AllCanonicals => _types.Keys;

        public bool TryResolvePrefab(string canonicalId, out UnityEngine.GameObject prefab)
        {
            if (_prefabs.TryGetValue(canonicalId, out prefab) && prefab != null) return true;
            if (_types.TryGetValue(canonicalId, out var type) && type?.prefab != null)
            {
                prefab = type.prefab;
                return true;
            }
            prefab = null!;
            return false;
        }

        private static bool AddTypeToGroup(Il2Cpp.IdentifiableTypeGroup group, Il2Cpp.IdentifiableType type, bool addImmediate)
        {
            var added = false;
            try
            {
                if (addImmediate)
                {
                    var members = group.ImmediateMemberTypes;
                    if (members != null && !members.Contains(type)) { members.Add(type); added = true; }
                }
                var runtime = group._runtimeObject;
                var runtimeMembers = runtime?._memberTypes;
                if (runtime != null && runtimeMembers != null)
                {
                    if (!runtimeMembers.Contains(type)) { runtimeMembers.Add(type); added = true; }
                    var indices = runtime._memberIndices;
                    if (indices != null && !indices.ContainsKey(type))
                        indices[type] = runtimeMembers.Count - 1;
                }
            }
            catch (Exception) { }
            return added;
        }

        private static void RemoveTypeFromGroup(Il2Cpp.IdentifiableTypeGroup group, Il2Cpp.IdentifiableType type, bool removeImmediate)
        {
            try
            {
                if (removeImmediate && group.ImmediateMemberTypes != null && group.ImmediateMemberTypes.Contains(type))
                    group.ImmediateMemberTypes.Remove(type);
                var runtime = group._runtimeObject;
                if (runtime?._memberTypes != null && runtime._memberTypes.Contains(type))
                    runtime._memberTypes.Remove(type);
                if (runtime?._memberIndices != null && runtime._memberIndices.ContainsKey(type))
                    runtime._memberIndices.Remove(type);
            }
            catch (Exception) { }
        }

        /// <summary>
        /// Re-assert imported membership in every live runtime group cache (leaf and ancestor groups, plus
        /// AutoSaveDirector._configuration.IdentifiableTypes). Idempotent.
        /// </summary>
        internal int EnsureRuntimeMembership()
        {
            var added = 0;
            var context = Il2Cpp.GameContext.Instance;
            var refDict = context?.LookupDirector?._identifiableTypeByRefId;
            var configGroup = context?.AutoSaveDirector?._configuration?.IdentifiableTypes;
            foreach (var pair in _defs)
            {
                try
                {
                    if (refDict != null && !string.IsNullOrEmpty(pair.Value.referenceId) &&
                        (!refDict.TryGetValue(pair.Value.referenceId, out var existing) || existing == null || existing.Pointer != pair.Value.Pointer))
                    { refDict[pair.Value.referenceId] = pair.Value; added++; }
                }
                catch (Exception) { }
                if (configGroup != null && AddTypeToGroup(configGroup, pair.Value, false)) added++;
                if (_defMirrorGroups.TryGetValue(pair.Key, out var groups))
                    foreach (var group in groups)
                        if (AddTypeToGroup(group, pair.Value, true)) added++;
                if (_defAncestorGroups.TryGetValue(pair.Key, out var ancestors))
                    foreach (var group in ancestors)
                        if (AddTypeToGroup(group, pair.Value, false)) added++;
            }
            foreach (var pair in _plortMirrorGroups)
            {
                var plort = pair.Value.Type;
                try
                {
                    if (refDict != null && !string.IsNullOrEmpty(plort.referenceId) &&
                        (!refDict.TryGetValue(plort.referenceId, out var existingPlort) || existingPlort == null || existingPlort.Pointer != plort.Pointer))
                    { refDict[plort.referenceId] = plort; added++; }
                }
                catch (Exception) { }
                if (configGroup != null && AddTypeToGroup(configGroup, plort, false)) added++;
                foreach (var group in pair.Value.Groups)
                    if (AddTypeToGroup(group, plort, true)) added++;
                foreach (var group in pair.Value.Ancestors)
                    if (AddTypeToGroup(group, plort, false)) added++;
            }
            return added;
        }

        /// <summary>
        /// Give every imported definition a valid SlimeAppearance (owned clone with custom icon when available)
        /// and register it with every live SlimeAppearanceDirector so HUD vacpack slots never NRE. Idempotent.
        /// </summary>
        internal int EnsureAppearances()
        {
            var directors = new List<Il2Cpp.SlimeAppearanceDirector>();
            var seen = new HashSet<IntPtr>();
            void AddDirector(Il2Cpp.SlimeAppearanceDirector? candidate)
            {
                if (candidate == null || candidate.Pointer == IntPtr.Zero || !seen.Add(candidate.Pointer)) return;
                directors.Add(candidate);
            }
            // SlimeAppearanceDirector is a ScriptableObject on SceneContext, so Object.FindObjectsOfType skips it.
            try { AddDirector(Il2Cpp.SceneContext.Instance?.SlimeAppearanceDirector); }
            catch (Exception) { }
            try
            {
                var found = UnityEngine.Object.FindObjectsOfType<Il2Cpp.SlimeAppearanceDirector>();
                if (found != null)
                    foreach (var item in found) AddDirector(item);
            }
            catch (Exception) { }
            var applied = 0;
            foreach (var pair in _defs)
            {
                Il2Cpp.SlimeAppearance? primary = null;
                if (_ownedAppearances.TryGetValue(pair.Key, out var owned) && owned != null)
                {
                    primary = owned;
                    try
                    {
                        var cur = pair.Value.AppearancesDefault;
                        if (cur == null || cur.Length == 0 || cur[0] == null || cur[0].Pointer != owned.Pointer)
                            pair.Value.AppearancesDefault = new Il2CppReferenceArray<Il2Cpp.SlimeAppearance>(new[] { owned });
                    }
                    catch (Exception) { continue; }
                }
                else
                {
                    if (!_templates.TryGetValue(pair.Key, out var template) || template == null) continue;
                    var appearances = template.AppearancesDefault;
                    if (appearances == null || appearances.Length == 0 || appearances[0] == null) continue;
                    primary = appearances[0];
                    try
                    {
                        var cur = pair.Value.AppearancesDefault;
                        if (cur == null || cur.Length == 0 || cur[0] == null)
                            pair.Value.AppearancesDefault = appearances;
                    }
                    catch (Exception) { continue; }
                }
                if (primary == null) continue;
                foreach (var director in directors)
                {
                    try
                    {
                        if (!director.IsAppearanceUnlocked(pair.Value, primary))
                            director.UnlockAppearance(pair.Value, primary);
                        var chosen = director.GetChosenSlimeAppearance(pair.Value);
                        if (chosen == null || chosen.Pointer != primary.Pointer)
                        {
                            director.UpdateChosenSlimeAppearance(pair.Value, primary);
                            applied++;
                        }
                    }
                    catch (Exception) { }
                }
            }
            return applied;
        }

        /// <summary>
        /// Guarantees every registered imported slime, largo and plort is present in AutoSaveDirector's
        /// SaveReferenceTranslation (both lookup for Push/load and reverse persistence-id table for Pull/save).
        /// Fast O(N_imported) dictionary probe when already synced; rebuilds cleanly while preserving all
        /// native and third-party modded types whenever any imported type is missing.
        /// </summary>
        internal bool EnsurePersistence(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector? director, Action<string>? log = null)
        {
            if (_types.Count == 0 || director == null) return false;
            try
            {
                var translation = director._saveReferenceTranslation?.TryCast<Il2CppMonomiPark.SlimeRancher.SaveReferenceTranslation>();
                if (translation == null) return false;
                var lookup = translation._identifiableTypeLookup;
                var reverse = translation._identifiableTypeToPersistenceId?._reverseIndex;
                var allPresent = lookup != null && reverse != null;
                if (allPresent)
                {
                    foreach (var type in _types.Values)
                    {
                        var id = type?.referenceId;
                        if (string.IsNullOrEmpty(id)) continue;
                        if (!lookup!.ContainsKey(id) || !reverse!.ContainsKey(id)) { allPresent = false; break; }
                    }
                }
                if (allPresent) return true;

                var combined = new Il2CppSystem.Collections.Generic.List<Il2Cpp.IdentifiableType>();
                var seenIds = new HashSet<string>(StringComparer.Ordinal);
                void Include(Il2Cpp.IdentifiableType? t)
                {
                    if (t == null) return;
                    var id = t.ReferenceId;
                    if (string.IsNullOrEmpty(id) || !seenIds.Add(id)) return;
                    combined.Add(t);
                }
                var group = director._configuration?.IdentifiableTypes;
                if (group != null)
                {
                    var members = group.GetAllMembers().GetEnumerator();
                    var cursor = members.Cast<Il2CppSystem.Collections.IEnumerator>();
                    while (cursor.MoveNext()) Include(members.Current);
                }
                if (lookup != null)
                {
                    foreach (var entry in lookup) Include(entry.Value);
                }
                foreach (var imported in _types.Values) Include(imported);
                translation.OnIdentifiableTypesListChanged(
                    combined.Cast<Il2CppSystem.Collections.Generic.IEnumerable<Il2Cpp.IdentifiableType>>());
                log?.Invoke("species registrar: save persistence synced (" + combined.Count + " types, " + _types.Count + " imported)");
                return true;
            }
            catch (Exception ex)
            {
                log?.Invoke("species registrar: save persistence sync failed: " + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
        }

        /// <summary>Re-asserts groups, appearances and save persistence for the live world. Idempotent.</summary>
        internal void EnsureWorldBindings(Action<string>? log = null)
        {
            if (!_done || _defs.Count == 0) return;
            EnsureRuntimeMembership();
            EnsureAppearances();
            EnsurePersistence(Il2Cpp.GameContext.Instance?.AutoSaveDirector, log);
        }

        /// <summary>Zero-allocation per-frame check: syncs once whenever a gameplay SceneContext loads.</summary>
        internal void TickWorldSync(Action<string>? log = null)
        {
            if (!_done || _defs.Count == 0) return;
            var scene = Il2Cpp.SceneContext.Instance;
            if (scene == null || scene.PlayerState == null || scene.SlimeAppearanceDirector == null)
            {
                _syncedScenePtr = IntPtr.Zero;
                return;
            }
            var ptr = scene.Pointer;
            if (ptr == _syncedScenePtr) return;
            _syncedScenePtr = ptr;
            EnsureWorldBindings(log);
        }

        private static UnityEngine.Sprite? TryLoadIconSprite(string canonicalId)
        {
            try
            {
                var folder = Sr2ImportedSpawner.CacheDirFor(canonicalId);
                if (string.IsNullOrEmpty(folder)) return null;
                string? file = null;
                var manifestPath = Path.Combine(folder, "cache-manifest.json");
                if (File.Exists(manifestPath))
                {
                    var root = MiniJson.Parse(File.ReadAllText(manifestPath)) as Dictionary<string, object>;
                    if (root != null && root.TryGetValue("icon", out var iconObj) && iconObj is Dictionary<string, object> icon &&
                        icon.TryGetValue("file", out var fileObj) && fileObj is string f && !string.IsNullOrEmpty(f))
                        file = f;
                }
                if (string.IsNullOrEmpty(file))
                {
                    var descPath = Path.Combine(folder, "icon-descriptor.json");
                    if (File.Exists(descPath))
                    {
                        var desc = MiniJson.Parse(File.ReadAllText(descPath)) as Dictionary<string, object>;
                        if (desc != null && desc.TryGetValue("file", out var fileObj) && fileObj is string f && !string.IsNullOrEmpty(f))
                            file = f;
                    }
                }
                if (string.IsNullOrEmpty(file) && File.Exists(Path.Combine(folder, "icon.png")))
                    file = "icon.png";
                if (string.IsNullOrEmpty(file) || Path.GetFileName(file) != file) return null;
                var pngPath = Path.Combine(folder, file);
                if (!File.Exists(pngPath)) return null;
                var bytes = File.ReadAllBytes(pngPath);
                var texture = Sr2Png.Load(bytes, "SlimeBridgeIconTex_" + canonicalId, false, false);
                texture.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
                UnityEngine.Object.DontDestroyOnLoad(texture);
                var sprite = UnityEngine.Sprite.Create(texture, new UnityEngine.Rect(0f, 0f, texture.width, texture.height), new UnityEngine.Vector2(0.5f, 0.5f), 100f);
                sprite.name = "SlimeBridgeIcon_" + canonicalId;
                sprite.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
                UnityEngine.Object.DontDestroyOnLoad(sprite);
                return sprite;
            }
            catch (Exception) { return null; }
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
            internal Il2Cpp.SlimeAppearance? OwnedAppearance;
            internal readonly List<Il2Cpp.IdentifiableTypeGroup> DefGroupsAdded = new List<Il2Cpp.IdentifiableTypeGroup>();
            internal readonly List<Il2Cpp.IdentifiableTypeGroup> DefAncestorGroupsAdded = new List<Il2Cpp.IdentifiableTypeGroup>();
            internal readonly List<Il2Cpp.IdentifiableTypeGroup> PlortGroupsAdded = new List<Il2Cpp.IdentifiableTypeGroup>();
            internal readonly List<Il2Cpp.IdentifiableTypeGroup> PlortAncestorGroupsAdded = new List<Il2Cpp.IdentifiableTypeGroup>();
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
                    _defAncestorGroups.Remove(result.Entry.canonical_id);
                    _ownedAppearances.Remove(result.Entry.canonical_id);
                    _types.Remove(result.Entry.canonical_id);
                    if (result.Entry.plort != null && !string.IsNullOrEmpty(result.Entry.plort.canonical_id))
                    {
                        _types.Remove(result.Entry.plort.canonical_id);
                        // The runtime re-assertion loop must not resurrect a failed plort after rollback.
                        _plortMirrorGroups.Remove(result.Entry.plort.canonical_id);
                    }
                    _prefabs.Remove(result.Entry.canonical_id);
                    Rollback(definitions, result.Def, result.Plort, result.Clone, result.OwnedAppearance,
                        result.DefGroupsAdded, result.DefAncestorGroupsAdded,
                        result.PlortGroupsAdded, result.PlortAncestorGroupsAdded, result.Problems);
                }
            }

            if (_defs.Count > 0)
            {
                Active = this;
                // Synchronize runtime groups, appearances, and save/load persistence lookup tables BEFORE any save loads:
                // otherwise reloading a save with imported slimes/plorts cannot resolve their referenceIds, and saving throws
                // KeyNotFoundException in GameModelPullHelpers.PullPlayer / PullActors.
                try { EnsureRuntimeMembership(); } catch (Exception) { }
                try { EnsureAppearances(); } catch (Exception) { }
                EnsurePersistence(context.AutoSaveDirector, log);
                Sr2PersistencePatch.Install(log);
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
            Il2Cpp.SlimeAppearance? ownedAppearance = null;
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
                // does: mirror every leaf group whose ImmediateMemberTypes contains the template,
                // AND every ancestor/composite group (vacpack slots, silos, AutoSaveDirector) where IsMember(template) is true.
                var defMirrorGroups = lookup.GroupsContaining(templateDef);
                if (defMirrorGroups.Count == 0 && templateDef.groupType != null)
                    defMirrorGroups.Add(templateDef.groupType);
                if (defMirrorGroups.Count == 0)
                { result.Problems.Add("template slime has no group membership to mirror"); return result; }
                var defAncestorGroups = lookup.AncestorGroupsContaining(templateDef, defMirrorGroups);
                var hasPlort = !string.IsNullOrEmpty(entry.plort.canonical_id);
                Il2Cpp.IdentifiableType? templatePlort = null;
                if (hasPlort && (!lookup.TryTypeTemplate(entry.plort.template_native_ref, out templatePlort) || templatePlort == null))
                { result.Problems.Add("unknown plort template " + entry.plort.template_native_ref); return result; }
                var plortMirrorGroups = new List<Il2Cpp.IdentifiableTypeGroup>();
                var plortAncestorGroups = new List<Il2Cpp.IdentifiableTypeGroup>();
                if (hasPlort)
                {
                    plortMirrorGroups = lookup.GroupsContaining(templatePlort!);
                    if (plortMirrorGroups.Count == 0 && templatePlort!.groupType != null)
                        plortMirrorGroups.Add(templatePlort.groupType);
                    if (plortMirrorGroups.Count == 0)
                    { result.Problems.Add("template plort has no group membership to mirror"); return result; }
                    plortAncestorGroups = lookup.AncestorGroupsContaining(templatePlort!, plortMirrorGroups);
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
                UnityEngine.Object.DontDestroyOnLoad(def);
                def.name = entry.display_name;
                def.Name = entry.display_name;
                def.referenceId = entry.native_ref_id ?? "";
                def.stableHashedId = entry.native_id;
                def.initializedHashId = true;
                def.IsLargo = isLargo;
                def.CanLargofy = !isLargo && entry.flags.can_largofy;
                def.PrefabScale = templateDef.PrefabScale;
                def.Sounds = templateDef.Sounds;
                def.BaseModule = templateDef.BaseModule;
                def.localizedName = templateDef.localizedName;
                def.color = templateDef.color;
                def.amountAcquiredPerVac = templateDef.amountAcquiredPerVac > 0 ? templateDef.amountAcquiredPerVac : 1;
                def.needsInstantiation = templateDef.needsInstantiation;
                def.IsAnimal = templateDef.IsAnimal;
                def.properties = templateDef.properties;
                def.defaultPropertyValues = templateDef.defaultPropertyValues;
                def.debugIcon = templateDef.debugIcon;
                def._fullArt = templateDef._fullArt;
                def._requiresFullArt = false;
                def.RadiantBase = templateDef.RadiantBase;
                def.RadiantLargo0 = templateDef.RadiantLargo0;
                def.RadiantLargo1 = templateDef.RadiantLargo1;
                def.BaseSlimes = isLargo ? new Il2CppReferenceArray<Il2Cpp.SlimeDefinition>(parents.ToArray())
                                         : new Il2CppReferenceArray<Il2Cpp.SlimeDefinition>(0);
                // Populate AppearancesDefault + icon immediately (owned clone, never mutating templateDef's native appearance)
                // so HUD vacpack slots (AmmoSlotViewHolder.UpdateAmmoDisplay) and SlimeAppearanceDirector never see null.
                var customIcon = TryLoadIconSprite(entry.canonical_id);
                var templateApps = templateDef.AppearancesDefault;
                if (templateApps != null && templateApps.Length > 0 && templateApps[0] != null)
                {
                    try
                    {
                        var appClone = UnityEngine.Object.Instantiate(templateApps[0]);
                        if (appClone != null)
                        {
                            appClone.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
                            UnityEngine.Object.DontDestroyOnLoad(appClone);
                            appClone.name = "SlimeBridgeAppearance_" + entry.canonical_id;
                            if (customIcon != null)
                            {
                                appClone._icon = customIcon;
                                appClone.IconNew = customIcon;
                            }
                            ownedAppearance = appClone;
                            def.AppearancesDefault = new Il2CppReferenceArray<Il2Cpp.SlimeAppearance>(new[] { appClone });
                        }
                        else
                        {
                            def.AppearancesDefault = templateApps;
                        }
                    }
                    catch (Exception) { def.AppearancesDefault = templateApps; }
                }
                else
                {
                    def.AppearancesDefault = new Il2CppReferenceArray<Il2Cpp.SlimeAppearance>(0);
                }
                def.icon = customIcon ?? ownedAppearance?.Icon ?? templateDef.icon;
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
                    UnityEngine.Object.DontDestroyOnLoad(plortType);
                    plortType.name = entry.plort.display_name;
                    plortType.referenceId = entry.plort.native_ref_id ?? "";
                    plortType.stableHashedId = entry.plort.native_id;
                    plortType.initializedHashId = true;
                    plortType.IsPlort = true;
                    plortType.groupType = templatePlort!.groupType;
                    plortType.Categories = templatePlort.Categories;
                    plortType.icon = templatePlort.icon;
                    plortType.debugIcon = templatePlort.debugIcon;
                    plortType._fullArt = templatePlort._fullArt;
                    plortType._requiresFullArt = false;
                    plortType.localizedName = templatePlort.localizedName;
                    plortType.color = templatePlort.color;
                    plortType.amountAcquiredPerVac = templatePlort.amountAcquiredPerVac > 0 ? templatePlort.amountAcquiredPerVac : 1;
                    plortType.needsInstantiation = templatePlort.needsInstantiation;
                    plortType.properties = templatePlort.properties;
                    plortType.defaultPropertyValues = templatePlort.defaultPropertyValues;
                    if (templatePlort.prefab == null) { result.Problems.Add("template plort prefab unavailable"); return result; }
                    var plortClone = Sr2PrefabCloner.Clone(templatePlort.prefab);
                    plortClone.name = "SlimeBridgeImport_" + entry.canonical_id + "_plort";
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
                clone = Sr2PrefabCloner.Clone(templateDef.prefab);
                clone.name = "SlimeBridgeImport_" + entry.canonical_id;
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
                        AddTypeToGroup(mirrorGroup, def, true);
                        result.DefGroupsAdded.Add(mirrorGroup);
                    }
                    catch (Exception ex) { result.Problems.Add("group mirror failed on " + (mirrorGroup.name ?? "") + ": " + ex.GetType().Name); }
                }
                foreach (var ancestorGroup in defAncestorGroups)
                {
                    try
                    {
                        AddTypeToGroup(ancestorGroup, def, false);
                        result.DefAncestorGroupsAdded.Add(ancestorGroup);
                    }
                    catch (Exception) { }
                }
                if (plortType != null)
                {
                    foreach (var mirrorGroup in plortMirrorGroups)
                    {
                        try
                        {
                            AddTypeToGroup(mirrorGroup, plortType, true);
                            result.PlortGroupsAdded.Add(mirrorGroup);
                        }
                        catch (Exception ex) { result.Problems.Add("plort group mirror failed on " + (mirrorGroup.name ?? "") + ": " + ex.GetType().Name); }
                    }
                    foreach (var ancestorGroup in plortAncestorGroups)
                    {
                        try
                        {
                            AddTypeToGroup(ancestorGroup, plortType, false);
                            result.PlortAncestorGroupsAdded.Add(ancestorGroup);
                        }
                        catch (Exception) { }
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
                    Rollback(definitions, def, plortType, clone, ownedAppearance, result.DefGroupsAdded, result.DefAncestorGroupsAdded, result.PlortGroupsAdded, result.PlortAncestorGroupsAdded, result.Problems);
                    mutated = false;
                    return result;
                }

                _defs[entry.canonical_id] = def;
                _types[entry.canonical_id] = def;
                if (plortType != null && !string.IsNullOrEmpty(entry.plort.canonical_id)) _types[entry.plort.canonical_id] = plortType;
                _prefabs[entry.canonical_id] = clone;
                _defMirrorGroups[entry.canonical_id] = defMirrorGroups;
                _defAncestorGroups[entry.canonical_id] = defAncestorGroups;
                _templates[entry.canonical_id] = templateDef;
                if (ownedAppearance != null) _ownedAppearances[entry.canonical_id] = ownedAppearance;
                if (plortType != null && !string.IsNullOrEmpty(entry.plort.canonical_id))
                    _plortMirrorGroups[entry.plort.canonical_id] = (plortType, plortMirrorGroups, plortAncestorGroups);
                result.Detail["imported_visual_setup"] = Sr2ImportedSpawner.Register(def, entry, clone, templateDef);
                result.Detail["imported_visual"] = Sr2ImportedSpawner.Proof(clone);
                result.Def = def;
                result.Plort = plortType;
                result.Clone = clone;
                result.OwnedAppearance = ownedAppearance;
                result.Status = "REGISTERED";
                return result;
            }
            catch (Exception ex)
            {
                result.Problems.Add("registration threw: " + ex.GetType().Name + ": " + ex.Message);
                if (mutated)
                {
                    try { Rollback(definitions, def, plortType, clone, ownedAppearance, result.DefGroupsAdded, result.DefAncestorGroupsAdded, result.PlortGroupsAdded, result.PlortAncestorGroupsAdded, result.Problems); }
                    catch (Exception rex) { result.Problems.Add("rollback threw: " + rex.GetType().Name); }
                }
                else if (clone != null || ownedAppearance != null || (plortType != null && plortType.prefab != null))
                {
                    DestroyQuietly(clone);
                    if (ownedAppearance != null) { try { UnityEngine.Object.Destroy(ownedAppearance); } catch { } }
                    if (plortType != null) DestroyQuietly(plortType.prefab);
                }
                _defs.Remove(entry.canonical_id);
                _defMirrorGroups.Remove(entry.canonical_id);
                _defAncestorGroups.Remove(entry.canonical_id);
                _ownedAppearances.Remove(entry.canonical_id);
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
            if (def is Il2Cpp.SlimeDefinition slimeDefinition)
            {
                // The inactive reference clone has a separate serialized SlimeEat definition.
                // Retarget it before native Awake builds drive/eat state, even without a visual cache.
                // Plorts deliberately stay on the identity-only path below.
                if (clone.activeInHierarchy) return false;
                var eaters = clone.GetComponentsInChildren<Il2Cpp.SlimeEat>(true);
                if (eaters == null || eaters.Length == 0) return false;
                var bound = false;
                foreach (var eater in eaters)
                {
                    if (eater == null) continue;
                    eater.SlimeDefinition = slimeDefinition;
                    if (eater.SlimeDefinition == null || eater.SlimeDefinition.Pointer != slimeDefinition.Pointer)
                        return false;
                    bound = true;
                }
                if (!bound) return false;
            }
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
            Il2Cpp.SlimeAppearance? ownedAppearance,
            List<Il2Cpp.IdentifiableTypeGroup> defGroupsAdded,
            List<Il2Cpp.IdentifiableTypeGroup> defAncestorGroupsAdded,
            List<Il2Cpp.IdentifiableTypeGroup> plortGroupsAdded,
            List<Il2Cpp.IdentifiableTypeGroup> plortAncestorGroupsAdded, List<string> problems)
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
                foreach (var group in defGroupsAdded) RemoveTypeFromGroup(group, def, true);
                foreach (var group in defAncestorGroupsAdded) RemoveTypeFromGroup(group, def, false);
            }
            catch (Exception ex) { problems.Add("rollback partial: " + ex.GetType().Name); }
            if (plortType != null)
            {
                try
                {
                    foreach (var mirrorGroup in plortGroupsAdded) RemoveTypeFromGroup(mirrorGroup, plortType, true);
                    foreach (var ancestorGroup in plortAncestorGroupsAdded) RemoveTypeFromGroup(ancestorGroup, plortType, false);
                    var plortRefDict = Il2Cpp.GameContext.Instance?.LookupDirector?._identifiableTypeByRefId;
                    if (plortRefDict != null && !string.IsNullOrEmpty(plortType.referenceId) &&
                        plortRefDict.TryGetValue(plortType.referenceId, out var existingPlortRef) && existingPlortRef != null && existingPlortRef.Pointer == plortType.Pointer)
                        plortRefDict.Remove(plortType.referenceId);
                    DestroyQuietly(plortType.prefab);
                }
                catch (Exception ex) { problems.Add("plort rollback partial: " + ex.GetType().Name); }
            }
            if (ownedAppearance != null)
            {
                try { UnityEngine.Object.Destroy(ownedAppearance); } catch { }
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
                var configGroup = _context.AutoSaveDirector?._configuration?.IdentifiableTypes;
                if (configGroup != null && !_allGroups.Exists(g => g.Pointer == configGroup.Pointer))
                    _allGroups.Add(configGroup);
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

            /// <summary>Composite/ancestor groups (vacpack slots, silos, AutoSaveDirector) containing the template via child groups.</summary>
            internal List<Il2Cpp.IdentifiableTypeGroup> AncestorGroupsContaining(Il2Cpp.IdentifiableType member,
                List<Il2Cpp.IdentifiableTypeGroup> immediate)
            {
                var immediatePtrs = new HashSet<IntPtr>();
                foreach (var g in immediate) immediatePtrs.Add(g.Pointer);
                var ancestors = new List<Il2Cpp.IdentifiableTypeGroup>();
                foreach (var group in _allGroups)
                {
                    if (immediatePtrs.Contains(group.Pointer)) continue;
                    try
                    {
                        if (group.IsMember(member)) ancestors.Add(group);
                    }
                    catch (Exception) { }
                }
                return ancestors;
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
