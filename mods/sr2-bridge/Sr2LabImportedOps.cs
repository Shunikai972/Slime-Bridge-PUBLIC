using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// LAB-only, oracle-driven op runner for imported species in SR2 — the mirror of the SR1
    /// LabImportedOps edge. Only arms for the exact LAB executable and a registrar that already
    /// registered the canonical ids in this session. Ops are one-shot and fail closed: unknown
    /// or unregistered canonicals are refused, every mutating op records before/requested/actual
    /// native deltas, and failed verification reverts the actual native change (or reports a
    /// loud NATIVE RESTORE/REVERT failure). Receipts write bridge-imported-ops-receipt v1.
    /// </summary>
    internal sealed class Sr2LabImportedOps
    {
        private const string Realm = "SR2";
        private static readonly System.Text.RegularExpressions.Regex OpPattern =
            new System.Text.RegularExpressions.Regex("^([a-z_]+):([a-z0-9_.]+)(?::([0-9]+))?$",
                System.Text.RegularExpressions.RegexOptions.Compiled);
        private readonly int _mainThreadId = System.Threading.Thread.CurrentThread.ManagedThreadId;
        private readonly Sr2SpeciesRegistrar _registrar;
        private readonly string _outDir;
        private readonly string _runId;
        private readonly List<string> _ops = new List<string>();
        private bool _done;
        private float _readySince = -1f;
        private long _spawned;

        private Sr2LabImportedOps(Sr2SpeciesRegistrar registrar, string outDir, string runId, List<string> ops)
        {
            _registrar = registrar;
            _outDir = outDir;
            _runId = runId;
            _ops = ops;
        }

        internal bool Done => _done && _shotAt < 0f;

        internal static bool Requested => Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OPS") != null;

        internal static Sr2LabImportedOps? TryCreate(bool labExecutable, Sr2SpeciesRegistrar? registrar)
        {
            if (!labExecutable || registrar == null) return null;
            var outDir = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OUT");
            var raw = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_OPS");
            if (string.IsNullOrEmpty(outDir) || string.IsNullOrEmpty(raw) || !System.IO.Directory.Exists(outDir)) return null;
            var guardRoot = LabSpecPathGuard.FindRepoRoot();
            if (guardRoot == null || !LabSpecPathGuard.IsAllowedDirectory(System.IO.Path.Combine(guardRoot, LabSpecPathGuard.OutRelativeRoot), outDir, out var _))
                return null;
            var runId = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_RUN") ?? "";
            if (runId.Length < 8 || runId.Length > 64 || runId.Any(c => !char.IsLetterOrDigit(c) && c != '-')) return null;
            List<string> ops;
            try
            {
                var problems = new List<string>();
                var doc = MiniJson.Parse(raw) as Dictionary<string, object?>;
                if (doc == null || !doc.TryGetValue("ops", out var listRaw) || listRaw is not List<object?> list)
                    return null;
                ops = new List<string>();
                foreach (var item in list)
                {
                    if (item is not string op || !OpPattern.IsMatch(op)) return null;
                    ops.Add(op);
                }
            }
            catch (Exception) { return null; }
            if (ops.Count == 0) return null;
            return new Sr2LabImportedOps(registrar, outDir, runId, ops);
        }

        private float _shotAt = -1f;
        private readonly List<UnityEngine.GameObject> _actors = new List<UnityEngine.GameObject>();

        /// <summary>LAB evidence: a close-up of one spawned actor from the freest side, rendered by a temporary camera (the player may be facing a wall).</summary>
        private bool CaptureActor(UnityEngine.GameObject actor, string file)
        {
            var main = UnityEngine.Camera.main;
            if (actor == null || main == null) return false;
            var focus = actor.transform.position + UnityEngine.Vector3.up * 0.5f;
            var bestDir = UnityEngine.Vector3.forward;
            var bestFree = -1f;
            for (var i = 0; i < 8; i++)
            {
                var dir = UnityEngine.Quaternion.Euler(0f, i * 45f, 0f) * UnityEngine.Vector3.forward;
                var free = 3.0f;
                if (UnityEngine.Physics.Raycast(focus, dir, out var hit, 3.0f, ~0, UnityEngine.QueryTriggerInteraction.Ignore)) free = hit.distance;
                if (free > bestFree) { bestFree = free; bestDir = dir; }
            }
            var camObject = new UnityEngine.GameObject("SlimeBridgeLabCam");
            var rt = new UnityEngine.RenderTexture(1024, 768, 24);
            var tex = new UnityEngine.Texture2D(1024, 768, UnityEngine.TextureFormat.RGB24, false);
            try
            {
                var cam = camObject.AddComponent<UnityEngine.Camera>();
                cam.CopyFrom(main);
                cam.transform.position = focus + bestDir * UnityEngine.Mathf.Max(1.3f, bestFree - 0.3f) + UnityEngine.Vector3.up * 0.25f;
                cam.transform.LookAt(focus);
                cam.fieldOfView = 50f;
                cam.nearClipPlane = 0.05f;
                cam.targetTexture = rt;
                cam.Render();
                var previous = UnityEngine.RenderTexture.active;
                UnityEngine.RenderTexture.active = rt;
                tex.ReadPixels(new UnityEngine.Rect(0, 0, 1024, 768), 0, 0);
                tex.Apply();
                UnityEngine.RenderTexture.active = previous;
                cam.targetTexture = null;
                System.IO.File.WriteAllBytes(System.IO.Path.Combine(_outDir, file), UnityEngine.ImageConversion.EncodeToPNG(tex));
                return true;
            }
            finally
            {
                UnityEngine.Object.Destroy(camObject);
                UnityEngine.Object.Destroy(rt);
                UnityEngine.Object.Destroy(tex);
            }
        }

        internal void Tick(Action<string> log)
        {
            if (_shotAt > 0f && UnityEngine.Time.realtimeSinceStartup >= _shotAt)
            {
                _shotAt = -1f;
                for (var i = 0; i < _actors.Count; i++)
                {
                    try { log("species ops: " + (CaptureActor(_actors[i], "sr2-actor-" + (i + 1).ToString(CultureInfo.InvariantCulture) + ".png") ? "close-up " : "close-up failed ") + (i + 1)); }
                    catch (Exception ex) { log("species ops: close-up failed: " + ex.GetType().Name + ": " + ex.Message); }
                }
                try { UnityEngine.ScreenCapture.CaptureScreenshot(System.IO.Path.Combine(_outDir, "sr2-spawn.png"), 1); }
                catch (Exception) { /* the close-ups are the evidence */ }
            }
            if (_done) return;
            if (System.Threading.Thread.CurrentThread.ManagedThreadId != _mainThreadId) return;
            try { TickOnMainThread(log); }
            catch (Exception ex)
            {
                _done = true;
                Publish(null, new List<string> { "ops runner failed: " + ex.GetType().Name + ": " + ex.Message }, new List<string>());
                log("species ops failed: " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        private void TickOnMainThread(Action<string> log)
        {
            if (_done) return;
            if (!_registrar.Done) return;
            if (!CounterpartCreator.TryReadWorld(out var world) || world == null)
            {
                _readySince = -1f;
                return;
            }
            var ammo = world.Scene.PlayerState?.Ammo;
            if (ammo == null || world.Scene.Player == null || !world.Scene.Player.activeInHierarchy)
            {
                _readySince = -1f;
                return;
            }
            // The world must stay stable before the one-shot ops run; thirty seconds clears the
            // post-load window in which the native autosave director refuses SaveAllNow.
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_readySince < 0f) { _readySince = now; return; }
            if (now - _readySince < 30f) return;
            _done = true;
            // World-time group caches may not have existed (or may have been rebuilt) when the
            // registrar ran at the menu; re-assert membership before the one-shot ops.
            try { log("species ops: runtime membership re-assert added " + _registrar.EnsureRuntimeMembership().ToString(CultureInfo.InvariantCulture)); }
            catch (Exception ex) { log("species ops: membership re-assert failed: " + ex.GetType().Name); }
            try { log("species ops: appearance ensure applied " + _registrar.EnsureAppearances().ToString(CultureInfo.InvariantCulture)); }
            catch (Exception ex) { log("species ops: appearance ensure failed: " + ex.GetType().Name); }
            // The save serializer maps IdentifiableType -> persistence id via the director's
            // lookup tables, which were built before the imported definitions existed. Rebuild
            // them from the live content so imported types can serialize (save op otherwise
            // fails/throwing on an imported vacpack item).
            try { log("species ops: persistence lookup tables refreshed (" + world.Director.RefreshPersistence().ToString(CultureInfo.InvariantCulture) + " types)"); }
            catch (Exception ex) { log("species ops: lookup reset failed: " + ex.GetType().Name); }
            Run(world, log);
        }

        private void Run(CounterpartCreator.WorldReading world, Action<string> log)
        {
            var rows = new List<string>();
            foreach (var op in _ops)
            {
                var match = OpPattern.Match(op);
                var kind = match.Groups[1].Value;
                var canonical = match.Groups[2].Value;
                var count = match.Groups[3].Success ? int.Parse(match.Groups[3].Value, CultureInfo.InvariantCulture) : 1;
                rows.Add(Execute(world, kind, canonical, count));
            }
            Publish(world, new List<string>(), rows);
            log("species ops: " + rows.Count + " executed");
            _shotAt = UnityEngine.Time.realtimeSinceStartup + 8f;
        }

        private string Execute(CounterpartCreator.WorldReading world, string kind, string canonical, int count)
        {
            var problems = new List<string>();
            var details = new List<string>();
            var executed = false;
            var before = -1;
            var after = -1;
            var nativeRef = "";
            try
            {
                if (count < 1) problems.Add("count must be at least 1");
                else if (!_registrar.TryResolveType(canonical, out var type))
                    problems.Add("species not registered in this session");
                else
                {
                    nativeRef = type.referenceId ?? "";
                    var ammo = world.Scene.PlayerState!.Ammo!;
                    if (kind is "vac_add" or "vac_remove" or "release" or "query" or "save_roundtrip")
                        before = Count(ammo, type);
                    after = before;
                    switch (kind)
                    {
                        case "query":
                            executed = true;
                            details.Add("\"slots\": " + SlotJson(ammo, type));
                            details.Add("\"membership\": " + MembershipJson(type));
                            var visualRows = new List<string>();
                            foreach (var ident in UnityEngine.Object.FindObjectsOfType<Il2Cpp.Identifiable>())
                                if (ident != null && ident.identType != null && ident.identType.Pointer == type.Pointer && visualRows.Count < 8)
                                {
                                    // Read-only evidence: query must not repair a broken lifecycle hook.
                                    visualRows.Add(Sr2ImportedSpawner.Proof(ident.gameObject));
                                }
                            details.Add("\"imported_visuals\": [" + string.Join(",", visualRows) + "]");
                            if (_registrar.TryResolveDefinition(canonical, out var queryDef) && queryDef != null)
                                details.Add("\"membership_lookup\": " + MembershipJson(queryDef));
                            break;
                        case "vac_add":
                        {
                            var slotJson = SlotJson(ammo, type);
                            try { details.Add("\"could_add_global\": " + (ammo.CouldAdd(type) ? "true" : "false")); }
                            catch (Exception ex) { details.Add("\"could_add_global\": " + RegistrySnapshotFormat.Quote("threw: " + ex.GetType().Name)); }
                            details.Add("\"slots_full\": " + SlotsDetailJson(ammo, type));
                            var ok = AddToVac(ammo, type, count);
                            after = Count(ammo, type);
                            executed = ok && after == before + count;
                            if (!executed)
                            {
                                problems.Add("native add or aggregate count mismatch");
                                RevertAdded(ammo, type, before, details, problems);
                            }
                            details.Add("\"slots_before\": " + slotJson);
                            details.Add("\"slots_after\": " + SlotJson(ammo, type));
                            break;
                        }
                        case "vac_remove":
                        case "release":
                        {
                            var slot = FindSlot(ammo, type);
                            if (slot < 0 || ammo.Slots[slot].Count < count)
                            {
                                problems.Add("insufficient usable native count");
                                break;
                            }
                            ammo.Decrement(slot, count);
                            after = Count(ammo, type);
                            executed = after == before - count;
                            if (!executed)
                            {
                                problems.Add("native decrement or aggregate count mismatch");
                                RestoreRemoved(ammo, type, before, "failed decrement", details, problems);
                            }
                            else if (kind == "release")
                            {
                                var spawned = Spawn(world, canonical, details, problems);
                                if (!spawned)
                                {
                                    executed = false;
                                    RestoreRemoved(ammo, type, before, "failed release spawn", details, problems);
                                }
                            }
                            break;
                        }
                        case "spawn":
                            executed = Spawn(world, canonical, details, problems);
                            break;
                        case "diet":
                            // registered gameplay readback: parents, plorts produced, favourite foods, food groups, which parent becomes it
                            if (!_registrar.TryResolveDefinition(canonical, out var dietDef) || dietDef == null || dietDef.Diet == null)
                            { problems.Add("no registered definition"); break; }
                            details.Add("\"diet\": " + DietJson(dietDef, type));
                            executed = true;
                            break;
                        case "save_roundtrip":
                        {
                            if (world.Director.CurrentGame() == null) { problems.Add("no loaded native save"); break; }
                            details.Add("\"save_preflight\": " + RegistrySnapshotFormat.Quote(
                                "loaded=" + (world.Director.GameLoaded() ? "1" : "0") + "/savedgame=1"));
                            // Direct native probe: distinguishes SaveAllNow refusal from the
                            // persistence wrapper's current-state checks. Runs before the
                            // persistence baseline capture so the wrapper still sees exactly
                            // one new revision from its own save.
                            try { details.Add("\"direct_save_all_now\": " + (world.Director.SaveAllNow() ? "true" : "false")); }
                            catch (Exception ex) { details.Add("\"direct_save_all_now\": " + RegistrySnapshotFormat.Quote("threw:" + ex.GetType().Name)); }
                            try { world.Director.SaveGameAndFlush(); details.Add("\"save_game_and_flush\": \"ok\""); }
                            catch (Exception ex) { details.Add("\"save_game_and_flush\": " + RegistrySnapshotFormat.Quote("threw:" + ex.GetType().Name)); }
                            var expected = Count(ammo, type);
                            var persistence = new Sr2InventoryPersistence(world.Game, world.Scene, world.Name, type);
                            var proof = persistence.Save(expected);
                            executed = proof.Verified;
                            if (!executed) problems.Add("native save readback did not verify the imported count: " + proof.Reason);
                            details.Add("\"save_id\": " + RegistrySnapshotFormat.Quote(world.Name));
                            details.Add("\"save_file\": " + RegistrySnapshotFormat.Quote(proof.SaveFile ?? ""));
                            details.Add("\"save_sha256\": " + RegistrySnapshotFormat.Quote(proof.Sha256 ?? ""));
                            details.Add("\"saved_count\": " + expected.ToString(CultureInfo.InvariantCulture));
                            break;
                        }
                        default:
                            problems.Add("unknown op " + kind);
                            break;
                    }
                }
            }
            catch (Exception ex)
            {
                problems.Add("op threw: " + ex.GetType().Name + ": " + ex.Message);
                executed = false;
            }
            var sb = new StringBuilder(512);
            sb.Append("    {\"op\": ").Append(RegistrySnapshotFormat.Quote(kind));
            sb.Append(", \"item\": ").Append(RegistrySnapshotFormat.Quote(canonical));
            sb.Append(", \"count\": ").Append(count.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"native_ref_id\": ").Append(RegistrySnapshotFormat.Quote(nativeRef));
            if (before >= 0) sb.Append(", \"before\": ").Append(before.ToString(CultureInfo.InvariantCulture));
            if (after >= 0) sb.Append(", \"after\": ").Append(after.ToString(CultureInfo.InvariantCulture));
            sb.Append(", \"executed\": ").Append(executed ? "true" : "false");
            sb.Append(", \"problems\": [");
            for (var i = 0; i < problems.Count; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(RegistrySnapshotFormat.Quote(problems[i]));
            }
            sb.Append("]");
            foreach (var detail in details) sb.Append(", ").Append(detail);
            sb.Append("}");
            return sb.ToString();
        }

        private bool Spawn(CounterpartCreator.WorldReading world, string canonical, List<string> details, List<string> problems)
        {
            if (!_registrar.TryResolvePrefab(canonical, out var prefab) || prefab == null)
            {
                problems.Add("no registered placeholder prefab");
                return false;
            }
            var player = world.Scene.Player!.transform;
            // In front of the player, but never inside a wall: stop short of whatever the view hits first, and face the camera.
            var distance = 3.0f;
            try
            {
                if (UnityEngine.Physics.Raycast(player.position + UnityEngine.Vector3.up * 1.5f, player.forward, out var hit, 3.5f,
                        ~0, UnityEngine.QueryTriggerInteraction.Ignore))
                    distance = UnityEngine.Mathf.Max(1.4f, hit.distance - 0.9f);
            }
            catch (Exception) { /* keep the default distance */ }
            var position = player.position + player.forward * distance + UnityEngine.Vector3.up * (1.0f + _spawned * 0.0f);
            UnityEngine.GameObject? instance = null;
            try
            {
                instance = UnityEngine.Object.Instantiate(prefab, position, UnityEngine.Quaternion.LookRotation(-player.forward));
                instance.SetActive(true);
                var spawned = _spawned + 1;
                var identifiable = instance.GetComponentInChildren<Il2Cpp.Identifiable>(true);
                details.Add("\"instance_name\": " + RegistrySnapshotFormat.Quote(instance.name));
                details.Add("\"instance_ref_id\": " + RegistrySnapshotFormat.Quote(identifiable?.identType?.referenceId ?? ""));
                details.Add("\"instance_active\": " + (instance.activeInHierarchy ? "true" : "false"));
                details.Add("\"spawn_index\": " + spawned.ToString(CultureInfo.InvariantCulture));
                details.Add("\"position\": [" + position.x.ToString("F2", CultureInfo.InvariantCulture) + ", " +
                    position.y.ToString("F2", CultureInfo.InvariantCulture) + ", " + position.z.ToString("F2", CultureInfo.InvariantCulture) + "]");
                if (identifiable == null || identifiable.identType == null ||
                    !_registrar.TryResolveDefinition(canonical, out var def) || def == null ||
                    identifiable.identType.Pointer != def.Pointer)
                {
                    UnityEngine.Object.Destroy(instance);
                    problems.Add("spawned instance does not carry the imported identity; instance destroyed");
                    return false;
                }
                // A raw Instantiate does not run the game's actor initialization, so the native
                // appearance pipeline (and its family materials) may never be applied. Drive it
                // explicitly; the Harmony postfixes re-enter the imported-visual application.
                details.Add("\"native_materials_before_drive\": [" + NativeMaterialSummary(instance) + "]");
                var driven = 0;
                var applicators = 0;
                foreach (var applicator in instance.GetComponentsInChildren<Il2Cpp.SlimeAppearanceApplicator>(true))
                {
                    applicators++;
                    try
                    {
                        applicator.Initialize(true);
                        applicator.ApplyAppearance();
                        driven++;
                    }
                    catch (Exception ex)
                    {
                        details.Add("\"native_appearance_problem\": " + RegistrySnapshotFormat.Quote(
                            ex.GetType().Name + ": " + ex.Message));
                    }
                }
                details.Add("\"native_appearance_driven\": " + driven.ToString(CultureInfo.InvariantCulture));
                details.Add("\"native_appearance_state\": " + RegistrySnapshotFormat.Quote(driven > 0 ? "driven" : "none"));
                if (applicators > 0 && driven == 0)
                    details.Add("\"native_appearance_warning\": " + RegistrySnapshotFormat.Quote(
                        "applicators present but none completed Initialize/ApplyAppearance"));
                details.Add("\"native_materials\": [" + NativeMaterialSummary(instance) + "]");
                Sr2ImportedSpawner.TryApply(instance, out var visualProblem);
                details.Add("\"imported_visual\": " + Sr2ImportedSpawner.Proof(instance));
                if (visualProblem.Length > 0) details.Add("\"visual_note\": " + RegistrySnapshotFormat.Quote(visualProblem));
                _spawned = spawned;
                _actors.Add(instance);
                return true;
            }
            catch (Exception ex)
            {
                if (instance != null) UnityEngine.Object.Destroy(instance);
                problems.Add("spawn threw: " + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
        }

        /// <summary>Bounded native-material inventory for LAB diagnostics (names and capability flags only).</summary>
        private static string NativeMaterialSummary(UnityEngine.GameObject instance)
        {
            var rows = new List<string>();
            foreach (var renderer in instance.GetComponentsInChildren<UnityEngine.Renderer>(true))
            {
                if (renderer == null || rows.Count >= 8) continue;
                var materials = renderer.sharedMaterials;
                if (materials == null) continue;
                foreach (var material in materials)
                {
                    if (material == null || rows.Count >= 8) continue;
                    bool palette = false, eyes = false, mouth = false;
                    try
                    {
                        palette = material.HasProperty("_TopColor") && material.HasProperty("_MiddleColor") && material.HasProperty("_BottomColor");
                        eyes = material.HasProperty("_EyeRed");
                        mouth = material.HasProperty("_MouthMid");
                    }
                    catch (Exception) { }
                    rows.Add("{\"renderer\": " + RegistrySnapshotFormat.Quote(renderer.name) + ", \"material\": " +
                             RegistrySnapshotFormat.Quote(material.name) + ", \"shader\": " +
                             RegistrySnapshotFormat.Quote(material.shader != null ? material.shader.name : "") +
                             ", \"palette\": " + (palette ? "true" : "false") +
                             ", \"eye\": " + (eyes ? "true" : "false") +
                             ", \"mouth\": " + (mouth ? "true" : "false") + "}");
                }
            }
            return string.Join(",", rows);
        }

        /// <summary>Removes every item a failed add actually added.</summary>
        private static void RevertAdded(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type, int before,
            List<string> details, List<string> problems)
        {
            var added = Count(ammo, type) - before;
            if (added <= 0) return;
            var slot = FindSlot(ammo, type);
            var reverted = false;
            if (slot >= 0 && ammo.Slots[slot].Count >= added)
            {
                ammo.Decrement(slot, added);
                reverted = Count(ammo, type) == before;
            }
            var revertedCount = Count(ammo, type);
            details.Add("\"reverted_count\": " + revertedCount.ToString(CultureInfo.InvariantCulture));
            problems.Add(reverted
                ? "failed add: " + added.ToString(CultureInfo.InvariantCulture) + " item(s) reverted, operation aborted"
                : "failed add: NATIVE REVERT FAILED, " + (revertedCount - before).ToString(CultureInfo.InvariantCulture) + " item(s) remain (count now " + revertedCount.ToString(CultureInfo.InvariantCulture) + ")");
        }

        /// <summary>Re-adds every item a failed decrement/release actually removed.</summary>
        private static void RestoreRemoved(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type, int before, string context,
            List<string> details, List<string> problems)
        {
            var missing = before - Count(ammo, type);
            if (missing <= 0) return;
            var restored = AddToVac(ammo, type, missing) && Count(ammo, type) == before;
            var restoredCount = Count(ammo, type);
            details.Add("\"restored_count\": " + restoredCount.ToString(CultureInfo.InvariantCulture));
            problems.Add(restored
                ? context + ": " + missing.ToString(CultureInfo.InvariantCulture) + " item(s) restored, operation aborted"
                : context + ": NATIVE RESTORE FAILED, " + (before - restoredCount).ToString(CultureInfo.InvariantCulture) + " item(s) still missing (count now " + restoredCount.ToString(CultureInfo.InvariantCulture) + ")");
        }

        private static int Count(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type)
        {
            var total = 0;
            var slots = ammo.Slots;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot != null && slot.Id != null && slot.Id.Pointer == type.Pointer && slot.Count > 0)
                    total = checked(total + slot.Count);
            }
            return total;
        }

        private static int FindSlot(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type)
        {
            var slots = ammo.Slots;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot != null && slot.Id != null && slot.Id.Pointer == type.Pointer && slot.Count > 0) return i;
            }
            return -1;
        }

        private static bool AddToVac(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type, int count)
        {
            var existing = FindSlot(ammo, type);
            if (existing >= 0) return ammo.AddToSlot(type, existing, count);
            var slots = ammo.Slots;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot == null || !slot.IsUnlocked) continue;
                if (slot.Id != null && slot.Count > 0) continue;
                if (ammo.AddToSlot(type, i, count)) return true;
            }
            return false;
        }

        private static string Name(Il2Cpp.IdentifiableType? value) => value == null ? "" : (value.name ?? value.referenceId ?? "");
        private static string DietJson(Il2Cpp.SlimeDefinition def, Il2Cpp.IdentifiableType type)
        {
            string Q(string s) => "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
            string List(IEnumerable<string> values) => "[" + string.Join(",", values.Select(Q)) + "]";
            var diet = def.Diet;
            var parents = new List<string>(); var madeBy = new List<string>();
            if (def.BaseSlimes != null)
                foreach (var parent in def.BaseSlimes)
                {
                    if (parent == null) continue;
                    parents.Add(Name(parent));
                    var map = parent.Diet?.EatMap;
                    if (map == null) continue;
                    for (var i = 0; i < map.Count; i++)
                        if (map[i] != null && map[i].BecomesIdent != null && map[i].BecomesIdent.Pointer == type.Pointer) madeBy.Add(Name(parent) + " eats " + Name(map[i].EatsIdent));
                }
            var produces = new List<string>(); var favorites = new List<string>(); var groups = new List<string>();
            if (diet.ProduceIdents != null) foreach (var x in diet.ProduceIdents) produces.Add(Name(x));
            if (diet.FavoriteIdents != null) foreach (var x in diet.FavoriteIdents) favorites.Add(Name(x));
            if (diet.MajorFoodIdentifiableTypeGroups != null) foreach (var g in diet.MajorFoodIdentifiableTypeGroups) if (g != null) groups.Add(g.name ?? "");
            return "{\"is_largo\": " + (def.IsLargo ? "true" : "false") + ", \"parents\": " + List(parents) + ", \"produces\": " + List(produces) +
                ", \"favorites\": " + List(favorites) + ", \"favorite_production_count\": " + diet.FavoriteProductionCount.ToString(CultureInfo.InvariantCulture) +
                ", \"food_groups\": " + List(groups) + ", \"eat_map_entries\": " + (diet.EatMap?.Count ?? 0).ToString(CultureInfo.InvariantCulture) +
                ", \"made_by\": " + List(madeBy) + "}";
        }

        private static string MembershipJson(Il2Cpp.IdentifiableType type)
        {
            var sb = new StringBuilder("[");
            try
            {
                var groups = Il2Cpp.GameContext.Instance?.LookupDirector?._allIdentifiableTypeGroups?.items;
                if (groups != null)
                {
                    var first = true;
                    for (var g = 0; g < groups.Count; g++)
                    {
                        var group = groups[g];
                        if (group == null) continue;
                        var members = group.ImmediateMemberTypes;
                        var contains = members != null && members.Contains(type);
                        string isMember;
                        string hasRuntime;
                        try { isMember = group.IsMember(type) ? "true" : "false"; } catch (Exception ex) { isMember = "threw:" + ex.GetType().Name; }
                        try { hasRuntime = group._runtimeObject != null ? "true" : "false"; } catch (Exception) { hasRuntime = "err"; }
                        if (!contains && isMember != "true") continue;
                        if (!first) sb.Append(", ");
                        first = false;
                        sb.Append("{\"group\": ").Append(RegistrySnapshotFormat.Quote(group.name ?? ""));
                        sb.Append(", \"contains\": ").Append(contains ? "true" : "false");
                        sb.Append(", \"is_member\": ").Append(RegistrySnapshotFormat.Quote(isMember));
                        sb.Append(", \"has_runtime\": ").Append(hasRuntime).Append("}");
                    }
                }
            }
            catch (Exception ex) { sb.Append("{\"error\": \"").Append(ex.GetType().Name).Append("\"}"); }
            sb.Append("]");
            return sb.ToString();
        }

        private static string SlotsDetailJson(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type)
        {
            var sb = new StringBuilder("[");
            var slots = ammo.Slots;
            for (var i = 0; i < slots.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                var slot = slots[i];
                if (slot == null) { sb.Append("{\"index\": ").Append(i.ToString(CultureInfo.InvariantCulture)).Append(", \"null\": true}"); continue; }
                sb.Append("{\"index\": ").Append(i.ToString(CultureInfo.InvariantCulture));
                sb.Append(", \"unlocked\": ").Append(slot.IsUnlocked ? "true" : "false");
                sb.Append(", \"ref_id\": ").Append(RegistrySnapshotFormat.Quote(slot.Id?.referenceId ?? ""));
                sb.Append(", \"count\": ").Append(slot.Count.ToString(CultureInfo.InvariantCulture));
                string defName = "";
                string accepts;
                try
                {
                    var def = slot.Definition;
                    defName = def == null ? "" : (def.name ?? "");
                    accepts = def != null && def.IsAllowed(type) ? "true" : "false";
                }
                catch (Exception ex) { accepts = "threw:" + ex.GetType().Name; }
                sb.Append(", \"slot_def\": ").Append(RegistrySnapshotFormat.Quote(defName));
                sb.Append(", \"accepts\": ").Append(RegistrySnapshotFormat.Quote(accepts)).Append("}");
            }
            sb.Append("]");
            return sb.ToString();
        }

        private static string SlotJson(Il2CppMonomiPark.SlimeRancher.Player.AmmoSlotManager ammo, Il2Cpp.IdentifiableType type)
        {
            var sb = new StringBuilder("[");
            var slots = ammo.Slots;
            var first = true;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot == null) continue;
                var matches = slot.Id != null && slot.Id.Pointer == type.Pointer;
                if (!matches && slot.Count <= 0) continue;
                if (!first) sb.Append(", ");
                first = false;
                sb.Append("{\"index\": ").Append(i.ToString(CultureInfo.InvariantCulture));
                sb.Append(", \"unlocked\": ").Append(slot.IsUnlocked ? "true" : "false");
                sb.Append(", \"ref_id\": ").Append(RegistrySnapshotFormat.Quote(slot.Id?.referenceId ?? ""));
                sb.Append(", \"count\": ").Append(slot.Count.ToString(CultureInfo.InvariantCulture)).Append("}");
            }
            sb.Append("]");
            return sb.ToString();
        }

        private void Publish(CounterpartCreator.WorldReading? world, List<string> errors, List<string> rows)
        {
            var sb = new StringBuilder(4096);
            sb.Append("{\n  \"schema\": \"bridge-imported-ops-receipt\",\n  \"schema_version\": 1");
            sb.Append(",\n  \"realm\": \"").Append(Realm).Append("\"");
            sb.Append(",\n  \"run_id\": ").Append(RegistrySnapshotFormat.Quote(_runId));
            sb.Append(",\n  \"save\": ").Append(RegistrySnapshotFormat.Quote(world?.Name ?? ""));
            sb.Append(",\n  \"ops\": [");
            for (var i = 0; i < rows.Count; i++) sb.Append(i == 0 ? "\n" : ",\n").Append(rows[i]);
            sb.Append(rows.Count == 0 ? "]" : "\n  ]");
            sb.Append(",\n  \"errors\": [");
            for (var i = 0; i < errors.Count; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(RegistrySnapshotFormat.Quote(errors[i]));
            }
            sb.Append("]\n}\n");
            RegistrySnapshotFormat.PublishCaptureFile(_outDir, RegistrySnapshotFormat.ImportedOpsReceiptFileName, sb.ToString());
        }
    }
}
