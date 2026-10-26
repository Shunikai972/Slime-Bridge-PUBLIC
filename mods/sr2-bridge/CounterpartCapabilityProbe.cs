using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Persist;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>Opt-in menu diagnostic only. Detached objects; no world load, save or reservation.</summary>
    internal sealed class CounterpartCapabilityProbe
    {
        internal static bool Requested => Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_COUNTERPART_DIAGNOSTIC") == "1";
        private static readonly Regex Name = new Regex(@"\A[A-Za-z0-9_-]{1,128}\z");
        private static readonly Regex Reference = new Regex(@"\A[A-Za-z0-9_.:-]{1,256}\z");
        private readonly string _output;
        private readonly string _root;
        private readonly string _runId;
        private readonly Action<string> _log;
        private float _started = -1f;
        private float _next;
        private bool _done;

        private CounterpartCapabilityProbe(string output, string root, string runId, Action<string> log)
        { _output = output; _root = root; _runId = runId; _log = log; }

        internal static CounterpartCapabilityProbe? TryCreate(Action<string> log)
        {
            if (!Requested) return null;
            try
            {
                var expected = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
                var actual = Process.GetCurrentProcess().MainModule?.FileName;
                if (string.IsNullOrWhiteSpace(expected) || string.IsNullOrWhiteSpace(actual) ||
                    !string.Equals(Path.GetFullPath(expected), Path.GetFullPath(actual), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("lab executable mismatch");
                var exe = new FileInfo(actual);
                var sr2 = exe.Directory!;
                if (exe.Name != "SlimeRancher2.exe" || sr2.Name != "SR2" || sr2.Parent?.Name != "Games" ||
                    sr2.Parent.Parent?.Name != "Lab" || !File.Exists(Path.Combine(sr2.FullName, "slimebridge-lab.json")))
                    throw new InvalidOperationException("not a verified SR2 lab layout");
                RefuseReparse(exe.FullName);
                var root = Path.Combine(sr2.Parent.Parent.FullName, "RuntimeArtifacts");
                var output = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_COUNTERPART_OUTPUT") ?? "";
                var runId = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_COUNTERPART_RUN_ID") ?? "";
                if (!Regex.IsMatch(runId, @"\A[0-9a-f]{32}\z")) throw new InvalidOperationException("invalid run id");
                ValidateOutput(output, root);
                return new CounterpartCapabilityProbe(Path.GetFullPath(output), root, runId, log);
            }
            catch (Exception ex)
            {
                log("counterpart diagnostic refused: " + ex.GetType().Name);
                return null;
            }
        }

        internal void Tick()
        {
            if (_done) return;
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (_started < 0f) _started = now;
            if (now < _next) return;
            _next = now + 0.5f;
            var report = new Report { run_id = _runId };
            try
            {
                var game = Il2Cpp.GameContext.Instance;
                var director = game == null ? null : game.AutoSaveDirector;
                var system = Il2Cpp.SystemContext.Instance;
                var loader = system == null ? null : system.SceneLoader;
                var group = loader == null ? null : loader._currentSceneGroup;
                report.menu_ready = loader != null && !loader.IsSceneLoadInProgress && group != null &&
                    group.name.StartsWith("MainMenu", StringComparison.Ordinal) && director != null &&
                    !director.GameLoaded();
                var storage = director == null ? null : director.Storage();
                report.storage_ready = storage != null && storage.IsInitialized() && director!.ProfileLoaded();
                var definitions = game?.OptionsDirector?._optionsItemDefinitions;
                var settingsDirector = game?.GameSettingsDirector;
                if (!report.menu_ready || !report.storage_ready || definitions == null || settingsDirector == null)
                {
                    if (now - _started < 180f) return;
                    report.errors.Add("MENU_SERVICES_TIMEOUT");
                }
                else
                {
                    // Constructor reads actual platform definition-driven defaults into a DETACHED model.
                    // Never apply/reset/push these selections onto global directors or account settings.
                    // SR2 1.x split the options list (_fromConfigurations/_additionalOptions); the stock new-game
                    // definitions are not mapped yet, so this LAB probe reports it instead of guessing.
                    Il2CppSystem.Collections.Generic.IEnumerable<Il2CppMonomiPark.SlimeRancher.Options.OptionsItemDefinition>? items = null;
                    if (items == null) throw new InvalidOperationException("stock definitions unavailable on SR2 1.x (not mapped)");
                    foreach (var item in NativeStockDefinitions.Capture(items))
                    {
                        if (report.stock_definition_ids.Count >= 4096)
                            throw new InvalidOperationException("too many stock definitions");
                        if (item == null || !Reference.IsMatch(item.ReferenceId ?? "") || item.IsProfileSetting ||
                            report.stock_definition_ids.Contains(item.ReferenceId!))
                            throw new InvalidOperationException("invalid stock definition identity");
                        report.stock_definition_ids.Add(item.ReferenceId!);
                    }
                    if (report.stock_definition_ids.Count == 0) throw new InvalidOperationException("empty stock definitions");
                    var model = new GameSettingsModel(items);
                    var icon = settingsDirector.GameIcon;
                    if (icon == null || !Reference.IsMatch(icon.ReferenceId ?? ""))
                        throw new InvalidOperationException("stock icon unavailable");
                    model.SetGameIconForNewGame(icon);
                    report.stock_icon_reference_id = icon.ReferenceId!;
                    report.stock_icon_matches_model = model.gameIconDefinition != null && model.gameIconDefinition.Pointer == icon.Pointer;
                    report.stock_default_keys_ready = true;
                    if (model.optionsById == null) throw new InvalidOperationException("stock model options unavailable");
                    foreach (var entry in model.optionsById)
                    {
                        if (!Reference.IsMatch(entry.Key ?? "") || entry.Value == null ||
                            entry.Value.optionsItemDefinition == null || entry.Value.optionsItemDefinition.ReferenceId != entry.Key)
                            throw new InvalidOperationException("stock model identity mismatch");
                        report.model_definition_ids.Add(entry.Key);
                        // Only presence, never actual option values, enters diagnostic output.
                        if (string.IsNullOrWhiteSpace(entry.Value.OptionValueKey)) report.stock_default_keys_ready = false;
                    }
                    report.stock_definition_ids.Sort(StringComparer.Ordinal);
                    report.model_definition_ids.Sort(StringComparer.Ordinal);
                    if (!string.Equals(string.Join("\n", report.stock_definition_ids), string.Join("\n", report.model_definition_ids), StringComparison.Ordinal))
                        throw new InvalidOperationException("stock model definition coverage mismatch");
                    report.slot_count = director!.SlotCount();
                    var raw = storage!.GetAvailableGames();
                    if (raw == null || raw.Count > 4096) throw new InvalidOperationException("native traversal unavailable or too large");
                    report.raw_save_count = raw.Count;
                    var names = new List<string>();
                    var games = new List<string>();
                    var slots = new List<int>();
                    var invalid = new List<bool>();
                    foreach (var rawName in raw)
                    {
                        try
                        {
                            if (!Name.IsMatch(rawName ?? "") || !storage.HasGameData(rawName))
                                throw new InvalidOperationException("invalid raw native name/data");
                            var summary = director.LoadSummary(rawName);
                            if (summary == null || summary.IsInvalid || summary.SaveName != rawName ||
                                !Name.IsMatch(summary.Name ?? "") || summary.SaveSlotIndex < 0 ||
                                summary.SaveSlotIndex >= report.slot_count || summary.IconId == null ||
                                !Reference.IsMatch(summary.IconId.ReferenceId ?? ""))
                                throw new InvalidOperationException("invalid native summary metadata");
                            var row = new Row { raw_name = rawName!, game_name = summary.Name!,
                                save_name = summary.SaveName, slot_index = summary.SaveSlotIndex,
                                invalid = summary.IsInvalid, icon_reference_id = summary.IconId.ReferenceId! };
                            report.summaries.Add(row);
                            names.Add(rawName!); games.Add(summary.Name!); slots.Add(summary.SaveSlotIndex); invalid.Add(summary.IsInvalid);
                            try
                            {
                                // Provider bytes are READ ONLY. No disk save stream/writer is opened.
                                var stream = new Il2CppSystem.IO.MemoryStream();
                                try
                                {
                                storage.GetGameData(rawName, stream);
                                if (stream.Length <= 0 || stream.Length > 64 * 1024 * 1024)
                                    throw new InvalidOperationException("invalid native save size");
                                var bytes = (byte[])stream.ToArray();
                                stream.Position = 0;
                                var detached = new GameV11 { _isLoadSummary = false };
                                detached.Load(stream, false); // full native payload, never AutoSaveDirector world loading
                                if (detached._isLoadSummary || detached.GameName != summary.Name ||
                                    detached.SaveSlotIndex != summary.SaveSlotIndex || detached.World == null ||
                                    detached.Player == null || detached.Ranch == null || detached.Actors == null ||
                                    detached.GameSettings == null || detached.GameSettings.OptionItems == null ||
                                    detached.GameIconIndex == null || detached.GameIconIndex.Length == 0)
                                    throw new InvalidOperationException("incomplete detached save or mismatched identity");
                                var settings = detached.GameSettings;
                                if (settings.GameIconId < 0 || settings.GameIconId >= detached.GameIconIndex.Length)
                                    throw new InvalidOperationException("invalid persisted icon index");
                                var iconId = detached.GameIconIndex[settings.GameIconId];
                                if (!Reference.IsMatch(iconId ?? "") || iconId != summary.IconId.ReferenceId)
                                    throw new InvalidOperationException("persisted icon identity mismatch");
                                var keys = new HashSet<string>(StringComparer.Ordinal);
                                foreach (var option in settings.OptionItems)
                                    if (option == null || !Reference.IsMatch(option.PersistenceKey ?? "") ||
                                        string.IsNullOrWhiteSpace(option.OptionValueKey) || !keys.Add(option.PersistenceKey!))
                                        throw new InvalidOperationException("invalid persisted game settings");
                                if (keys.Count == 0) throw new InvalidOperationException("empty persisted game settings");
                                row.readback = new Readback { status = "READ_VERIFIED", game_name = detached.GameName,
                                    slot_index = detached.SaveSlotIndex, full_load = true, identity_matches = true,
                                    settings_complete = true, option_count = keys.Count, icon_reference_id = iconId!,
                                    byte_count = bytes.Length, sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant() };
                                }
                                finally { stream.Dispose(true); }
                            }
                            catch (Exception ex) { report.errors.Add("DETACHED_READ_ERROR:" + ex.GetType().Name); }
                        }
                        catch (Exception ex) { report.errors.Add("SUMMARY_ERROR:" + ex.GetType().Name); }
                    }
                    if (report.summaries.Count == raw.Count)
                    {
                        try
                        {
                            report.least_free_slot = SaveSlotSelection.SelectFree(report.slot_count, names.ToArray(), games.ToArray(), slots.ToArray(), invalid.ToArray());
                            report.occupancy_complete = true;
                        }
                        catch (InvalidOperationException ex)
                        {
                            // Shared selector reports all occupied as a refusal, not a corrupt traversal.
                            if (ex.Message == "No free native save slot") report.occupancy_complete = true;
                            else report.errors.Add("SLOT_METADATA_ERROR");
                        }
                    }
                    report.detached_readback_complete = report.summaries.Count == raw.Count &&
                        report.summaries.TrueForAll(row => row.readback.status == "READ_VERIFIED");
                    // Occupancy evidence with any failed read is incomplete for capability purposes.
                    if (report.errors.Count > 0) { report.occupancy_complete = false; report.least_free_slot = null; }
                }
            }
            catch (Exception ex)
            {
                report.errors.Add("NATIVE_ERROR:" + ex.GetType().Name + ":" + ex.Message.Replace('\r', ' ').Replace('\n', ' '));
                _log("counterpart diagnostic exception: " + ex);
            }
            _done = true;
            try { Publish(report); _log("counterpart diagnostic published (DIAGNOSTIC_ONLY)"); }
            catch (Exception ex) { _log("counterpart diagnostic output refused: " + ex.GetType().Name); }
        }

        private void Publish(Report report)
        {
            ValidateOutput(_output, _root);
            var temp = _output + "." + Guid.NewGuid().ToString("N") + ".tmp.json";
            var created = false;
            try
            {
                ValidateOutput(temp, _root);
                using (var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
                {
                    created = true;
                    var bytes = JsonSerializer.SerializeToUtf8Bytes(report);
                    file.Write(bytes, 0, bytes.Length);
                    file.Flush(true);
                }
                RefuseReparse(temp);
                ValidateOutput(_output, _root);
                File.Move(temp, _output); // exclusive publication: existing reports/hardlinks are refused
            }
            finally { if (created && File.Exists(temp)) { RefuseReparse(temp); File.Delete(temp); } }
        }

        private static void ValidateOutput(string path, string root)
        {
            if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\", StringComparison.Ordinal) ||
                !path.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("invalid output path");
            var target = Path.GetFullPath(path);
            var allowed = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!target.StartsWith(allowed, StringComparison.OrdinalIgnoreCase) ||
                target.Substring(Path.GetPathRoot(target)!.Length).Contains(':'))
                throw new InvalidOperationException("output escapes RuntimeArtifacts");
            RefuseReparse(target);
            if (!Directory.Exists(Path.GetDirectoryName(target)) || File.Exists(target) || Directory.Exists(target))
                throw new InvalidOperationException("output parent missing or target already occupied");
        }

        private static void RefuseReparse(string path)
        {
            for (string? part = Path.GetFullPath(path); part != null; part = Path.GetDirectoryName(part))
            {
                try
                {
                    if ((File.GetAttributes(part) & FileAttributes.ReparsePoint) != 0)
                        throw new InvalidOperationException("reparse point refused");
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                if (part == Path.GetPathRoot(part)) break;
            }
        }

        private sealed class Report
        {
            public int schema_version { get; set; } = 1;
            public string realm { get; set; } = "SR2";
            public string status { get; set; } = "DIAGNOSTIC_ONLY";
            public bool read_only { get; set; } = true;
            public bool creation_authorized { get; set; }
            public int native_write_calls { get; set; }
            public int native_world_load_calls { get; set; }
            public int pid { get; set; } = Environment.ProcessId;
            public string run_id { get; set; } = "";
            public bool menu_ready { get; set; }
            public bool storage_ready { get; set; }
            public int slot_count { get; set; }
            public int raw_save_count { get; set; }
            public List<string> stock_definition_ids { get; set; } = new List<string>();
            public List<string> model_definition_ids { get; set; } = new List<string>();
            public bool stock_default_keys_ready { get; set; }
            public string stock_icon_reference_id { get; set; } = "";
            public bool stock_icon_matches_model { get; set; }
            public bool occupancy_complete { get; set; }
            public int? least_free_slot { get; set; }
            public bool detached_readback_complete { get; set; }
            public List<Row> summaries { get; set; } = new List<Row>();
            public List<string> errors { get; set; } = new List<string>();
        }
        private sealed class Row
        {
            public string raw_name { get; set; } = "";
            public string save_name { get; set; } = "";
            public string game_name { get; set; } = "";
            public int slot_index { get; set; }
            public bool invalid { get; set; }
            public string icon_reference_id { get; set; } = "";
            public Readback readback { get; set; } = new Readback();
        }
        private sealed class Readback
        {
            public string status { get; set; } = "NATIVE_ERROR";
            public string game_name { get; set; } = "";
            public int slot_index { get; set; }
            public string sha256 { get; set; } = "";
            public int byte_count { get; set; }
            public bool full_load { get; set; }
            public bool identity_matches { get; set; }
            public bool settings_complete { get; set; }
            public int option_count { get; set; }
            public string icon_reference_id { get; set; } = "";
        }
    }
}
