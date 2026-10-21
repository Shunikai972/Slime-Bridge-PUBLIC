using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Q007: native SR1 registration of imported species. Reads the Bridge-owned
    /// bridge-imported-species-spec v1 (only when an oracle targets the exact LAB executable),
    /// reserves identity in the reserved numeric range, builds SlimeDefinition/Diet/VacItem
    /// objects, clones target-native placeholder prefabs, registers every index the game reads,
    /// and publishes a registration receipt with native read-back for each check.
    /// One failed species never blocks the others.
    /// </summary>
    internal sealed class SpeciesRegistrar
    {
        private const string Realm = "SR1";
        private readonly string _specPath;
        private readonly string _outDir;
        private readonly string _runId;
        private readonly int _mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;
        private readonly Dictionary<string, int> _nativeIds = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly Dictionary<string, int> _plortIds = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly Dictionary<string, ImportedSpeciesEntry> _specEntries = new Dictionary<string, ImportedSpeciesEntry>(StringComparer.Ordinal);
        private bool _done;
        private float _readySince = -1f;

        private SpeciesRegistrar(string specPath, string outDir, string runId)
        {
            _specPath = specPath;
            _outDir = outDir;
            _runId = runId;
        }

        internal static bool Requested => Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_SPEC") != null;

        public static SpeciesRegistrar? TryCreate(bool labExecutable)
        {
            var spec = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_SPEC");
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OUT");
            if (!labExecutable || string.IsNullOrEmpty(spec) || string.IsNullOrEmpty(outDir)) return null;
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
            var runId = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_RUN") ?? "";
            if (runId.Length < 8 || runId.Length > 64 || runId.Any(c => !char.IsLetterOrDigit(c) && c != '-')) return null;
            var registrar = new SpeciesRegistrar(spec, outDir, runId);
            Current = registrar;
            return registrar;
        }

        /// <summary>Process-wide registrar handle for runtime guards (appearance fixups run
        /// outside the spawn command and need canonical reverse lookups).</summary>
        internal static SpeciesRegistrar? Current;

        public bool TryResolveNativeId(string canonicalId, out int nativeId) => _nativeIds.TryGetValue(canonicalId, out nativeId);

        /// <summary>Dev convenience: the registered plort id of an imported species ("plort.ringtail").</summary>
        public bool TryResolvePlortId(string plortCanonicalId, out int nativeId) => _plortIds.TryGetValue(plortCanonicalId, out nativeId);

        /// <summary>Native ids of the registered imported plorts (market pricing).</summary>
        public System.Collections.Generic.IEnumerable<int> RegisteredPlortNativeIds => _plortIds.Values;

        /// <summary>Registered imported plorts: canonical plort id (plort.ringtail) and native id.</summary>
        public System.Collections.Generic.IEnumerable<System.Collections.Generic.KeyValuePair<string, int>> RegisteredPlorts => _plortIds;

        /// <summary>Reverse lookup: native id back to the imported canonical species.</summary>
        public bool TryGetCanonical(int nativeId, out string canonicalId)
        {
            foreach (var pair in _nativeIds)
            {
                if (pair.Value == nativeId) { canonicalId = pair.Key; return true; }
            }
            canonicalId = "";
            return false;
        }

        /// <summary>The owned appearance is a copy of the placeholder species' (Tabby's): its palette drives the splats a slime leaves, its eating
        /// effects, the vac and ammo slot colors. The spec's own palette replaces it so they show the species' colors.</summary>
        private static bool ApplySpecPalette(SlimeAppearance appearance, ImportedAppearanceSpec? spec)
        {
            if (appearance == null || spec == null || !spec.HasPalette) return false;
            var ammo = spec.ammo != null && spec.ammo.Length == 4 ? spec.ammo : spec.middle;
            appearance.ColorPalette = new SlimeAppearance.Palette
            {
                Top = new Color(spec.top[0], spec.top[1], spec.top[2], spec.top[3]),
                Middle = new Color(spec.middle[0], spec.middle[1], spec.middle[2], spec.middle[3]),
                Bottom = new Color(spec.bottom[0], spec.bottom[1], spec.bottom[2], spec.bottom[3]),
                Ammo = new Color(ammo[0], ammo[1], ammo[2], ammo[3]),
            };
            return true;
        }

        /// <summary>Canonical palette from the spec's optional appearance block (placeholder tinting).</summary>
        public bool TryGetPalette(string canonicalId, out float[] top, out float[] middle, out float[] bottom)
        {
            top = new float[0];
            middle = new float[0];
            bottom = new float[0];
            if (!_specEntries.TryGetValue(canonicalId, out var entry)) return false;
            var appearance = entry.appearance;
            if (appearance == null || !appearance.HasPalette) return false;
            top = appearance.top;
            middle = appearance.middle;
            bottom = appearance.bottom;
            return true;
        }

        /// <summary>Canonical ids registered natively in this session (read-only view for dev tools).</summary>
        public System.Collections.Generic.IEnumerable<string> RegisteredCanonicalIds => _nativeIds.Keys;

        /// <summary>Bridge-owned output directory for this session's receipts/diagnostics.</summary>
        public string OutDir => _outDir;

        private static GameObject? _templateRoot;

        /// <summary>Inactive DontDestroyOnLoad container holding registered prefab clones. The clones
        /// keep activeSelf=true (prefab-asset semantics) so native Object.Instantiate callers get
        /// ACTIVE copies with Awake run at instantiation; the deactivated container keeps them inert.</summary>
        private static GameObject TemplateRoot()
        {
            if (_templateRoot == null)
            {
                _templateRoot = new GameObject("SlimeBridge_RegisteredPrefabs");
                _templateRoot.SetActive(false);
                UnityEngine.Object.DontDestroyOnLoad(_templateRoot);
            }
            return _templateRoot;
        }

        public void Tick()
        {
            if (_done) return;
            if (System.Threading.Thread.CurrentThread.ManagedThreadId != _mainThreadId) return;
            try { TickOnMainThread(); }
            catch (Exception ex)
            {
                _done = true;
                UnityEngine.Debug.Log("[SlimeBridge] species registrar failed: " + ex.GetType().Name + ": " + ex.Message);
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
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_readySince < 0f) { _readySince = now; return; }
            if (now - _readySince < 1f) return;
            _done = true;
            try { Run(context); }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] species registrar failed: " + ex.GetType().Name + ": " + ex.Message);
                TryPublishFailureReceipt(ex);
            }
        }

        private void TryPublishFailureReceipt(Exception ex)
        {
            try
            {
                var json = "{\n  \"schema\": \"bridge-imported-species-receipt\",\n  \"schema_version\": 1,\n  \"realm\": \"" + Realm +
                    "\",\n  \"run_id\": " + RegistrySnapshotFormat.Quote(_runId) + ",\n  \"fatal\": " +
                    RegistrySnapshotFormat.Quote(ex.GetType().Name + ": " + ex.Message) + ",\n  \"entries\": []\n}\n";
                RegistrySnapshotFormat.PublishCaptureFile(_outDir, RegistrySnapshotFormat.ImportedSpeciesReceiptFileName, json);
            }
            catch { }
        }

        private void Run(GameContext context)
        {
            var errors = new List<string>();
            var rows = new List<string>();
            string specSha;
            ImportedSpeciesSpecDoc? doc;
            try
            {
                var bytes = File.ReadAllBytes(_specPath);
                specSha = RegistrySnapshotFormat.Sha256Hex(bytes);
                var parseProblems = new List<string>();
                doc = ImportedSpeciesSpecLoader.Load(new UTF8Encoding(false).GetString(bytes), parseProblems);
                if (doc == null || parseProblems.Count > 0)
                {
                    errors.AddRange(parseProblems);
                    Publish(context, specSha, errors, rows);
                    return;
                }
            }
            catch (Exception ex)
            {
                errors.Add("spec unreadable: " + ex.GetType().Name + ": " + ex.Message);
                Publish(context, "", errors, rows);
                return;
            }
            if (doc == null) { errors.Add("spec parsed to null"); Publish(context, "", errors, rows); return; }
            var docProblems = doc.Problems();
            if (docProblems.Count > 0) { errors.AddRange(docProblems); Publish(context, specSha, errors, rows); return; }
            if (doc.realm != Realm) { errors.Add("spec realm mismatch: " + doc.realm); Publish(context, specSha, errors, rows); return; }

            var results = new List<(ImportedSpeciesEntry? entry, bool ok, List<string> problems, Dictionary<string, object?>? detail, string canonicalId, string status)>();
            foreach (var entry in doc.entries ?? new ImportedSpeciesEntry[0])
            {
                if (entry == null) { errors.Add("null spec entry"); continue; }
                _specEntries[entry.canonical_id] = entry;
                var entryProblems = entry.Problems();
                if (entryProblems.Count > 0)
                {
                    results.Add((null, false, entryProblems, null, entry.canonical_id, "REFUSED"));
                    continue;
                }
                if (entry.status != "READY")
                {
                    results.Add((null, false, new List<string>(entry.blockers ?? new string[0]), null, entry.canonical_id,
                        "SKIPPED_PENDING_DATA"));
                    continue;
                }
                if (_nativeIds.ContainsKey(entry.canonical_id))
                {
                    results.Add((null, false, new List<string> { "duplicate canonical id" }, null, entry.canonical_id, "REFUSED"));
                    continue;
                }
                var check = RegisterEntry(context, entry, out var entryErrors, out var detail, out var entryMutated);
                if (check)
                {
                    _nativeIds[entry.canonical_id] = entry.native_id;
                    if (!string.IsNullOrEmpty(entry.plort.canonical_id) && entry.plort.native_id != 0)
                        _plortIds[entry.plort.canonical_id] = entry.plort.native_id;
                }
                else if (entryMutated)
                {
                    // Any failure after the first native mutation must not leave the entry
                    // resolvable: roll the native registration back and record the outcome.
                    entryErrors.Add("failed after native mutation; rolling back");
                    RollbackEntry(context, entry, entryErrors);
                }
                results.Add((entry, check, entryErrors, detail, entry.canonical_id, check ? "REGISTERED" : "FAILED"));
            }

            // Eat maps only exist after the definition refresh; a READY entry must have a
            // functioning diet read-back, so an empty map fails the entry closed.
            if (_nativeIds.Count > 0)
            {
                EnableLargofyForParents(context);
                ExtendFoodGroups(_nativeIds.Values, _plortIds.Values);
                context.SlimeDefinitions.RefreshDefinitions();
                LogLargoTransforms(context);
            }
            for (var i = 0; i < results.Count; i++)
            {
                var result = results[i];
                if (result.ok && result.entry != null && result.detail != null)
                {
                    var registered = context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)result.entry.native_id);
                    var eatMap = registered?.Diet?.EatMap;
                    result.detail["eat_map_entries"] = eatMap?.Count ?? 0;
                    // A species the spec declares as eating nothing (Shadow, Yolky: the source game gives them no diet) has an empty eat map on purpose.
                    var eatsNothing = result.entry.largo == null && result.entry.diet != null && (result.entry.diet.major_food_groups?.Length ?? 0) == 0
                        && (result.entry.diet.favorites?.Length ?? 0) == 0 && (result.entry.diet.additional_foods?.Length ?? 0) == 0;
                    if ((eatMap == null || eatMap.Count == 0) && !eatsNothing)
                    {
                        // A READY entry whose refreshed diet cannot work must not stay operable:
                        // drop it from the resolver and roll the native registration back.
                        result.problems.Add("empty eat map after refresh; entry failed closed and rolled back");
                        _nativeIds.Remove(result.entry.canonical_id);
                        RollbackEntry(context, result.entry, result.problems);
                        result = (result.entry, false, result.problems, result.detail, result.canonicalId, "FAILED");
                        results[i] = result;
                    }
                }
                rows.Add(Row(result.canonicalId, result.status, result.entry?.native_id ?? 0, result.problems, result.detail, result.ok));
            }
            Publish(context, specSha, errors, rows);
            UnityEngine.Debug.Log("[SlimeBridge] species registrar: " + _nativeIds.Count + " imported species registered, " +
                results.Count(r => r.status == "FAILED") + " failed, errors=" + errors.Count);
        }

        /// <summary>
        /// SlimeEat builds its food groups once, from the native enum values, so imported slimes and plorts were in none of them:
        /// Tarr (diet: every non-Tarr slime) never ate an imported slime and a Largo never ate an imported plort, so nothing
        /// imported could be turned into Tarr outside peaceful mode. Adds the ids before the eat maps are refreshed from the groups.
        /// </summary>
        private static void ExtendFoodGroups(IEnumerable<int> slimeIds, IEnumerable<int> plortIds)
        {
            try
            {
                SlimeEat.GetFoodGroupIds(SlimeEat.FoodGroup.PLORTS);      // runs the static constructor
                var field = typeof(SlimeEat).GetField("foodGroupIds", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
                if (!(field?.GetValue(null) is Dictionary<SlimeEat.FoodGroup, Identifiable.Id[]> groups)) { UnityEngine.Debug.Log("[SlimeBridge] food groups unavailable (field not found)"); return; }
                void Add(SlimeEat.FoodGroup group, IEnumerable<int> ids)
                {
                    var merged = new List<Identifiable.Id>(groups[group]);
                    var before = merged.Count;
                    foreach (var native in ids)
                        if (!merged.Contains((Identifiable.Id)native)) merged.Add((Identifiable.Id)native);
                    groups[group] = merged.ToArray();
                    UnityEngine.Debug.Log("[SlimeBridge] food group " + group + ": +" + (merged.Count - before) + " imported ids");
                }
                Add(SlimeEat.FoodGroup.NONTARRGOLD_SLIMES, slimeIds);
                Add(SlimeEat.FoodGroup.PLORTS, plortIds);
            }
            catch (Exception ex) { UnityEngine.Debug.Log("[SlimeBridge] food group extension failed: " + ex.GetType().Name); }
        }

        /// <summary>Whether a prefab is registered for the id, WITHOUT LookupDirector.GetPrefab: that logs
        /// "Missing prefab: ..." as a game error for every free id we probe (two per imported species at startup).</summary>
        private static bool HasLivePrefab(LookupDirector director, Identifiable.Id id)
        {
            if (GetField(director, "identifiablePrefabDict") is IDictionary dict)
                return dict.Contains(id) && dict[id] != null;
            return director.GetPrefab(id) != null;      // reflection unavailable: keep the old (noisy but correct) probe
        }

        private bool RegisterEntry(GameContext context, ImportedSpeciesEntry entry, out List<string> problems,
            out Dictionary<string, object?> detail, out bool mutated)
        {
            problems = new List<string>();
            detail = new Dictionary<string, object?>();
            mutated = false;
            var id = (Identifiable.Id)entry.native_id;
            var plortId = (Identifiable.Id)entry.plort.native_id;
            var hasPlort = !string.IsNullOrEmpty(entry.plort.canonical_id) && entry.plort.native_id != 0;
            ImportedIconLoader.OwnedAppearance? ownedAppearance = null;
            SlimeDefinition? ownedDefinition = null;
            try
            {
                if (Enum.IsDefined(typeof(Identifiable.Id), id))
                { problems.Add("native id collides with an existing enum member"); return false; }
                if (context.SlimeDefinitions.GetSlimeByIdentifiableId(id) != null)
                { problems.Add("native id already registered"); return false; }
                if (HasLivePrefab(context.LookupDirector, id))
                { problems.Add("native id already has a live prefab"); return false; }
                if (ImportedIconLoader.WasPublished(entry.native_id))
                { problems.Add("native id previously published; retained appearance requires a process restart before retry"); return false; }
                if (hasPlort)
                {
                    if (Enum.IsDefined(typeof(Identifiable.Id), plortId))
                    { problems.Add("plort native id collides with an existing enum member"); return false; }
                    if (HasLivePrefab(context.LookupDirector, plortId))
                    { problems.Add("plort native id already has a prefab"); return false; }
                }
                var slimeTemplateRef = entry.template.slime_native_ref ?? "";
                if (string.IsNullOrEmpty(slimeTemplateRef) || !char.IsLetter(slimeTemplateRef[0]) ||
                    !Enum.TryParse(slimeTemplateRef, out Identifiable.Id templateSlimeId) ||
                    !Enum.IsDefined(typeof(Identifiable.Id), templateSlimeId))
                { problems.Add("unknown slime template " + entry.template.slime_native_ref); return false; }
                Identifiable.Id templatePlortId = Identifiable.Id.NONE;
                var plortTemplateRef = entry.plort.template_native_ref ?? "";
                if (hasPlort && (string.IsNullOrEmpty(plortTemplateRef) || !char.IsLetter(plortTemplateRef[0]) ||
                                 !Enum.TryParse(plortTemplateRef, out templatePlortId) ||
                                 !Enum.IsDefined(typeof(Identifiable.Id), templatePlortId)))
                { problems.Add("unknown plort template " + entry.plort.template_native_ref); return false; }
                var templateDef = context.SlimeDefinitions.GetSlimeByIdentifiableId(templateSlimeId);
                if (templateDef == null || templateDef.AppearancesDefault == null || templateDef.AppearancesDefault.Length == 0)
                { problems.Add("slime template has no definition/appearance"); return false; }
                var templatePrefab = context.LookupDirector.GetPrefab(templateSlimeId);
                var templatePlortPrefab = hasPlort ? context.LookupDirector.GetPrefab(templatePlortId) : null;
                if (templatePrefab == null || (hasPlort && templatePlortPrefab == null))
                { problems.Add("slime or plort template prefab missing"); return false; }

                var defDict = GetField(context.SlimeDefinitions, "slimeDefinitionsByIdentifiable") as IDictionary;
                var prefabDict = GetField(context.LookupDirector, "identifiablePrefabDict") as IDictionary;
                var vacItemDict = GetField(context.LookupDirector, "vacItemDict") as IDictionary;
                var vacItemDynamic = GetField(context.LookupDirector, "vacItemDefinitionsDynamic") as IList;
                if (defDict == null || prefabDict == null || vacItemDict == null || vacItemDynamic == null)
                { problems.Add("native registration indexes unavailable"); return false; }

                // Diet references resolve BEFORE any native mutation: an unknown group/favorite is a
                // hard refusal, never a partially registered species.
                var dietProblems = new List<string>();
                var groups = ParseGroups(entry.diet.major_food_groups, dietProblems);
                var favorites = ParseIds(entry.diet.favorites, dietProblems, "favorite");
                var additional = ParseIds(entry.diet.additional_foods, dietProblems, "additional food");
                if (dietProblems.Count > 0)
                {
                    problems.Add("diet refused before any native mutation: " + string.Join("; ", dietProblems));
                    return false;
                }
                // A Largo is made from two registered parents: it eats what either eats and makes both plorts.
                var isLargo = entry.largo != null;
                SlimeDefinition[] parents = new SlimeDefinition[0];
                var largoProduces = new Identifiable.Id[0];
                var largoFavoriteCount = 2;
                if (isLargo)
                {
                    if (!TryResolveParents(context, entry.largo!.parents, out parents, out var parentProblem))
                    { problems.Add(parentProblem); return false; }
                    groups = parents.SelectMany(p => p.Diet.MajorFoodGroups ?? new SlimeEat.FoodGroup[0]).Distinct().ToArray();
                    favorites = parents.SelectMany(p => p.Diet.Favorites ?? new Identifiable.Id[0]).Distinct().ToArray();
                    additional = parents.SelectMany(p => p.Diet.AdditionalFoods ?? new Identifiable.Id[0]).Distinct().ToArray();
                    largoProduces = parents.SelectMany(p => p.Diet.Produces).Distinct().ToArray();
                    largoFavoriteCount = parents.Max(p => p.Diet.FavoriteProductionCount > 0 ? p.Diet.FavoriteProductionCount : 2);
                }

                // Prepare the owned CLASSIC copy and mandatory source icon BEFORE any native
                // publication. A declared invalid icon must fail, never borrow Tabby's icon.
                if (!ImportedIconLoader.TryPrepare(entry.canonical_id, templateDef, out ownedAppearance, out var iconProblem))
                { problems.Add(iconProblem); return false; }
                detail["palette_applied"] = ApplySpecPalette(ownedAppearance!.Appearance, entry.appearance);

                // --- Slime definition (diet from the reviewed captures; plort relation preserved).
                var def = ScriptableObject.CreateInstance<SlimeDefinition>();
                ownedDefinition = def;
                def.name = "SlimeBridge_" + entry.canonical_id;
                def.Name = entry.display_name;
                def.IdentifiableId = id;
                def.BaseSlimes = isLargo ? parents : new SlimeDefinition[0];
                def.IsLargo = isLargo;
                def.CanLargofy = !isLargo && entry.flags != null && entry.flags.can_largofy;
                def.PrefabScale = templateDef.PrefabScale;
                def.AppearancesDefault = new[] { ownedAppearance!.Appearance };
                def.AppearancesDynamic = new List<SlimeAppearance>();
                detail["appearance_owned_classic"] = true;
                detail["source_icon_loaded"] = ownedAppearance.Sprite != null;
                def.Sounds = templateDef.Sounds;
                def.FavoriteToys = new Identifiable.Id[0];
                def.Diet = new SlimeDiet
                {
                    MajorFoodGroups = groups,
                    Favorites = favorites,
                    AdditionalFoods = additional,
                    Produces = isLargo ? largoProduces : (hasPlort ? new[] { plortId } : new Identifiable.Id[0]),
                    FavoriteProductionCount = isLargo ? largoFavoriteCount : (entry.diet.favorite_production_count > 0 ? entry.diet.favorite_production_count : 2),
                };
                detail["definition_name"] = def.Name;
                detail["can_largofy"] = def.CanLargofy;

                // --- Plort: target-native placeholder prefab clone with the imported logical id.
                GameObject? plortClone = null;
                if (hasPlort)
                {
                    var clone = UnityEngine.Object.Instantiate(templatePlortPrefab!);
                    clone.name = "SlimeBridgeImport_" + entry.canonical_id + "_plort";
                    // Registered clones must behave like Unity prefab assets: activeSelf=true so any
                    // native Object.Instantiate (production, spawners, our ops) yields an ACTIVE copy
                    // whose Awake runs at instantiation. They stay inert under a deactivated container.
                    clone.transform.SetParent(TemplateRoot().transform, false);
                    SetIdentifiableIds(clone, plortId);
                    mutated = true;
                    prefabDict[plortId] = clone;
                    Identifiable.PLORT_CLASS.Add(plortId);
                    Identifiable.NON_SLIMES_CLASS.Add(plortId);
                    var templateVac = vacItemDict.Contains(templatePlortId) ? vacItemDict[templatePlortId] as VacItemDefinition : null;
                    var plortColor = templateVac != null ? templateVac.Color : Color.magenta;
                    var plortIcon = templateVac != null ? templateVac.Icon : null;
                    // The species' own plort look (SR2 palette/stripe/icon from the converted cache); absent = template look.
                    if (ImportedPlortAppearance.TryGet(entry.canonical_id, out var plortLook))
                    {
                        detail["plort_materials_recolored"] = ImportedPlortAppearance.ApplyToPlort(clone, plortLook);
                        plortColor = plortLook.Middle;
                        if (plortLook.Icon != null) plortIcon = plortLook.Icon;
                        detail["plort_icon_source"] = plortLook.Icon != null ? "sr2-cache" : "template";
                    }
                    else detail["plort_icon_source"] = "template";
                    var vac = VacItemDefinition.CreateVacItemDefinition(plortId, plortColor, plortIcon);
                    vacItemDict[plortId] = vac;
                    vacItemDynamic.Add(vac);
                    plortClone = clone;
                }

                // --- Slime prefab: clone template, retarget every definition reference and the root id.
                var slimeClone = UnityEngine.Object.Instantiate(templatePrefab);
                slimeClone.name = "SlimeBridgeImport_" + entry.canonical_id;
                slimeClone.transform.SetParent(TemplateRoot().transform, false);
                SetIdentifiableIds(slimeClone, id);
                foreach (var component in slimeClone.GetComponentsInChildren<Component>(true))
                    if (component != null) RetargetDefinitionFields(component, templateDef, def);
                mutated = true;
                // Retargeted prefab components can reference the definition from this point.
                // Keep ownership even on later rollback; a director may retain the appearance.
                ImportedIconLoader.Retain(def, ownedAppearance);
                prefabDict[id] = slimeClone;
                if (isLargo) Identifiable.LARGO_CLASS.Add(id); else Identifiable.SLIME_CLASS.Add(id);
                Identifiable.EATERS_CLASS.Add(id);

                // --- Registry + appearance selection (directors referenced by the prefab and lookup).
                var slimes = context.SlimeDefinitions.Slimes ?? new SlimeDefinition[0];
                var next = new SlimeDefinition[slimes.Length + 1];
                Array.Copy(slimes, next, slimes.Length);
                next[slimes.Length] = def;
                context.SlimeDefinitions.Slimes = next;
                defDict[id] = def;
                if (isLargo) detail["largo_pair_registered"] = RegisterLargoPair(context, def, parents);
                ImportedIdentityPatch.Register(entry.native_id, entry.display_name);
                if (hasPlort)
                    ImportedIdentityPatch.Register(entry.plort.native_id,
                        string.IsNullOrEmpty(entry.plort.display_name) ? entry.display_name + " Plort" : entry.plort.display_name);
                var appearance = def.AppearancesDefault[0];
                foreach (var director in DirectorsOf(slimeClone, GetField(context.LookupDirector, "slimeAppearanceDirector")))
                {
                    director.UnlockAppearance(def, appearance);
                    director.UpdateChosenSlimeAppearance(def, appearance);
                }

                // --- Native read-back checks (the game's own lookups must now answer with our objects).
                var indexed = context.SlimeDefinitions.GetSlimeByIdentifiableId(id);
                detail["definition_indexed"] = ReferenceEquals(indexed, def);
                detail["prefab_registered"] = ReferenceEquals(context.LookupDirector.GetPrefab(id), slimeClone);
                detail["plort_expected"] = hasPlort;
                detail["plort_prefab_registered"] = !hasPlort ||
                    (plortClone != null && ReferenceEquals(context.LookupDirector.GetPrefab(plortId), plortClone));
                detail["slime_class"] = Identifiable.IsSlime(id);
                detail["plort_class"] = !hasPlort || Identifiable.IsPlort(plortId);
                var chosen = DirectorsOf(slimeClone, GetField(context.LookupDirector, "slimeAppearanceDirector")).FirstOrDefault();
                detail["appearance_chosen"] = chosen != null && ReferenceEquals(chosen.GetChosenSlimeAppearance(def), appearance);
                var ok = (bool)detail["definition_indexed"]! && (bool)detail["prefab_registered"]! &&
                         (bool)detail["plort_prefab_registered"]! && (bool)detail["slime_class"]! &&
                         (bool)detail["plort_class"]! && (bool)detail["appearance_chosen"]!;
                if (!ok) problems.Add("native read-back check failed");
                return ok;
            }
            catch (Exception ex)
            {
                problems.Add("exception " + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
            finally
            {
                // Dispose only unpublished objects. Published bundles deliberately outlive
                // this registrar and any failed late selection/rollback.
                ownedAppearance?.Dispose();
                if (ownedDefinition != null && (ownedAppearance == null || !ownedAppearance.Retained))
                    UnityEngine.Object.Destroy(ownedDefinition);
            }
        }

        /// <summary>The registered definition of a Largo parent: an imported species ("slime.angler") or a native SR1 slime ("PINK_SLIME").</summary>
        private bool TryResolveParents(GameContext context, string[] slugs, out SlimeDefinition[] parents, out string problem)
        {
            parents = new SlimeDefinition[slugs.Length];
            problem = "";
            for (var i = 0; i < slugs.Length; i++)
            {
                SlimeDefinition? parent = null;
                if (_nativeIds.TryGetValue("slime." + slugs[i], out var nativeId))
                    parent = context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)nativeId);
                else if (Enum.TryParse(slugs[i].ToUpperInvariant() + "_SLIME", out Identifiable.Id id) && Enum.IsDefined(typeof(Identifiable.Id), id))
                    parent = context.SlimeDefinitions.GetSlimeByIdentifiableId(id);
                if (parent == null || parent.Diet == null || parent.Diet.Produces == null || parent.Diet.Produces.Length == 0 || parent.IsLargo)
                { problem = "largo parent '" + slugs[i] + "' is not a registered base slime with a plort"; return false; }
                parents[i] = parent;
            }
            return true;
        }

        /// <summary>
        /// SlimeDefinitions indexes Largos by their parents' plorts / definitions once, when it is enabled (RefreshIndexes); a Largo registered
        /// later must be added by hand or no slime eating a plort would ever become it. Both key orders are inserted (the game's own
        /// definition-pair key is order sensitive).
        /// </summary>
        private static bool RegisterLargoPair(GameContext context, SlimeDefinition largo, SlimeDefinition[] parents)
        {
            try
            {
                var definitions = context.SlimeDefinitions;
                var byPlorts = GetField(definitions, "largoDefinitionByBasePlorts") as IDictionary;
                var byDefinitions = GetField(definitions, "largoDefinitionByBaseDefinitions") as IDictionary;
                var plortPair = typeof(SlimeDefinitions).GetNestedType("PlortPair", BindingFlags.NonPublic);
                var definitionPair = typeof(SlimeDefinitions).GetNestedType("SlimeDefinitionPair", BindingFlags.NonPublic);
                if (byPlorts == null || byDefinitions == null || plortPair == null || definitionPair == null) return false;
                byPlorts[Activator.CreateInstance(plortPair, parents[0].Diet.Produces[0], parents[1].Diet.Produces[0])!] = largo;
                byDefinitions[Activator.CreateInstance(definitionPair, parents[0], parents[1])!] = largo;
                byDefinitions[Activator.CreateInstance(definitionPair, parents[1], parents[0])!] = largo;
                return true;
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.Log("[SlimeBridge] largo pair registration failed: " + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
        }

        /// <summary>Evidence line: how many plort-eating transformations into a Largo the base slimes now carry in their eat maps.</summary>
        private void LogLargoTransforms(GameContext context)
        {
            try
            {
                var perSpecies = new List<string>();
                foreach (var pair in _nativeIds)
                {
                    if (!pair.Key.StartsWith("slime.", StringComparison.Ordinal)) continue;
                    var definition = context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)pair.Value);
                    var count = definition?.Diet?.EatMap == null ? 0 : definition.Diet.EatMap.Count(e => e.becomesId != Identifiable.Id.NONE && Identifiable.IsLargo(e.becomesId));
                    perSpecies.Add(pair.Key + 
"=" + count);
                }
                var pink = context.SlimeDefinitions.GetSlimeByIdentifiableId(Identifiable.Id.PINK_SLIME);
                var pinkCount = pink?.Diet?.EatMap == null ? 0 : pink.Diet.EatMap.Count(e => e.becomesId != Identifiable.Id.NONE && (int)e.becomesId >= 900000);
                UnityEngine.Debug.Log("[SlimeBridge] largo transformations in eat maps: " + string.Join(", ", perSpecies) + "; native pink -> imported largos: " + pinkCount);
            }
            catch (Exception ex) { UnityEngine.Debug.Log("[SlimeBridge] largo transform log failed: " + ex.GetType().Name); }
        }

        /// <summary>An imported species can only become a Largo (by eating another species' plort) once a Largo of it exists.</summary>
        private void EnableLargofyForParents(GameContext context)
        {
            var enabled = 0;
            foreach (var entry in _specEntries.Values)
            {
                if (entry.largo == null || !_nativeIds.ContainsKey(entry.canonical_id)) continue;
                foreach (var slug in entry.largo.parents)
                {
                    if (!_nativeIds.TryGetValue("slime." + slug, out var nativeId)) continue;
                    var parent = context.SlimeDefinitions.GetSlimeByIdentifiableId((Identifiable.Id)nativeId);
                    if (parent != null && !parent.CanLargofy) { parent.CanLargofy = true; enabled++; }
                }
            }
            if (enabled > 0) UnityEngine.Debug.Log("[SlimeBridge] largofy enabled on " + enabled + " imported species");
        }

        /// <summary>
        /// Best-effort undo of a failed READY entry: removed from the resolver and from every
        /// native index it was registered into, so it can never be spawned or vacuumed while its
        /// receipt says FAILED. Any incompleteness is reported on the receipt.
        /// </summary>
        private static void RollbackEntry(GameContext context, ImportedSpeciesEntry entry, List<string> problems)
        {
            try
            {
                var id = (Identifiable.Id)entry.native_id;
                var hasPlort = !string.IsNullOrEmpty(entry.plort.canonical_id) && entry.plort.native_id != 0;
                var plortId = (Identifiable.Id)entry.plort.native_id;
                var defDict = GetField(context.SlimeDefinitions, "slimeDefinitionsByIdentifiable") as IDictionary;
                var prefabDict = GetField(context.LookupDirector, "identifiablePrefabDict") as IDictionary;
                var vacItemDict = GetField(context.LookupDirector, "vacItemDict") as IDictionary;
                var vacItemDynamic = GetField(context.LookupDirector, "vacItemDefinitionsDynamic") as IList;
                var slimes = context.SlimeDefinitions.Slimes ?? new SlimeDefinition[0];
                var kept = new List<SlimeDefinition>();
                foreach (var slime in slimes)
                    if (slime == null || slime.IdentifiableId != id) kept.Add(slime);
                context.SlimeDefinitions.Slimes = kept.ToArray();
                defDict?.Remove(id);
                prefabDict?.Remove(id);
                Identifiable.SLIME_CLASS.Remove(id);
                Identifiable.LARGO_CLASS.Remove(id);
                Identifiable.EATERS_CLASS.Remove(id);
                if (hasPlort)
                {
                    prefabDict?.Remove(plortId);
                    vacItemDict?.Remove(plortId);
                    if (vacItemDynamic != null)
                        for (var i = vacItemDynamic.Count - 1; i >= 0; i--)
                            if (vacItemDynamic[i] is VacItemDefinition vac && vac.Id == plortId) vacItemDynamic.RemoveAt(i);
                    Identifiable.PLORT_CLASS.Remove(plortId);
                    Identifiable.NON_SLIMES_CLASS.Remove(plortId);
                }
                ImportedIdentityPatch.Unregister(entry.native_id);
                if (hasPlort) ImportedIdentityPatch.Unregister(entry.plort.native_id);
                context.SlimeDefinitions.RefreshDefinitions();
                problems.Add("native registration rolled back (definition, prefabs, classes, vac item, name)");
            }
            catch (Exception ex)
            {
                problems.Add("rollback incomplete: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private static object? GetField(object target, string name) =>
            target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetValue(target);

        private static IEnumerable<SlimeAppearanceDirector> DirectorsOf(GameObject clone, object? lookupDirector)
        {
            var directors = new List<SlimeAppearanceDirector>();
            foreach (var applicator in clone.GetComponentsInChildren<SlimeAppearanceApplicator>(true))
            {
                if (applicator != null && applicator.SlimeAppearanceDirector != null &&
                    !directors.Contains(applicator.SlimeAppearanceDirector))
                    directors.Add(applicator.SlimeAppearanceDirector);
            }
            if (lookupDirector is SlimeAppearanceDirector fromLookup && !directors.Contains(fromLookup))
                directors.Add(fromLookup);
            return directors;
        }

        private static void SetIdentifiableIds(GameObject target, Identifiable.Id id)
        {
            foreach (var identifiable in target.GetComponentsInChildren<Identifiable>(true)) identifiable.id = id;
        }

        private static void RetargetDefinitionFields(Component component, SlimeDefinition from, SlimeDefinition to)
        {
            foreach (var field in component.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
            {
                if (field.FieldType != typeof(SlimeDefinition)) continue;
                try
                {
                    if (ReferenceEquals(field.GetValue(component), from)) field.SetValue(component, to);
                }
                catch { }
            }
        }

        private static SlimeEat.FoodGroup[] ParseGroups(string[]? names, List<string> problems)
        {
            var groups = new List<SlimeEat.FoodGroup>();
            foreach (var name in names ?? new string[0])
            {
                // Numeric strings parse but are not symbolic members; require a defined name.
                if (!string.IsNullOrEmpty(name) && char.IsLetter(name[0]) &&
                    Enum.TryParse<SlimeEat.FoodGroup>(name, out var group) &&
                    Enum.IsDefined(typeof(SlimeEat.FoodGroup), group)) groups.Add(group);
                else problems.Add("unknown food group " + name);
            }
            return groups.ToArray();
        }

        private static Identifiable.Id[] ParseIds(string[]? names, List<string> problems, string what)
        {
            var ids = new List<Identifiable.Id>();
            foreach (var name in names ?? new string[0])
            {
                if (!string.IsNullOrEmpty(name) && char.IsLetter(name[0]) &&
                    Enum.TryParse<Identifiable.Id>(name, out var id) &&
                    Enum.IsDefined(typeof(Identifiable.Id), id)) ids.Add(id);
                else problems.Add("unknown " + what + " " + name);
            }
            return ids.ToArray();
        }

        private static string Row(string canonicalId, string status, int nativeId, List<string> problems,
            Dictionary<string, object?>? detail, bool ok)
        {
            var sb = new StringBuilder(512);
            sb.Append("    {\"canonical_id\": ").Append(RegistrySnapshotFormat.Quote(canonicalId ?? ""));
            sb.Append(", \"status\": ").Append(RegistrySnapshotFormat.Quote(status));
            sb.Append(", \"native_id\": ").Append(nativeId.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"problems\": [");
            for (var i = 0; i < problems.Count; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(RegistrySnapshotFormat.Quote(problems[i]));
            }
            sb.Append("]");
            if (detail != null && detail.Count > 0)
            {
                sb.Append(", \"checks\": {");
                var first = true;
                foreach (var pair in detail.OrderBy(p => p.Key, StringComparer.Ordinal))
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

        private void Publish(GameContext context, string specSha, List<string> errors, List<string> rows)
        {
            var sb = new StringBuilder(4096);
            sb.Append("{\n  \"schema\": \"bridge-imported-species-receipt\",\n  \"schema_version\": 1");
            sb.Append(",\n  \"realm\": \"").Append(Realm).Append("\"");
            sb.Append(",\n  \"run_id\": ").Append(RegistrySnapshotFormat.Quote(_runId));
            sb.Append(",\n  \"spec_sha256\": ").Append(RegistrySnapshotFormat.Quote(specSha));
            sb.Append(",\n  \"registered_count\": ").Append(_nativeIds.Count.ToString(CultureInfo.InvariantCulture));
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
    }
}
