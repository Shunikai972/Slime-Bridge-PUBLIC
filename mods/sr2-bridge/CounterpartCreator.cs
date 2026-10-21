// Slime Rancher 2 0.6 native counterpart creator (LoadNewGame internals). Compiled by its test harness only; the shipped
// plugin targets SR2 1.x and uses CounterpartCreator.V1.cs (SLIMEBRIDGE_SR2), where these internals no longer exist.
#if !SLIMEBRIDGE_SR2
using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using HarmonyLib;
using Il2CppMonomiPark.SlimeRancher;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Persist;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>Main-thread native creation. Issued intents are never dispatched twice.
    /// Compilation/fixture evidence is not IL2CPP hook proof: a protected runtime oracle remains required.</summary>
    internal sealed class CounterpartCreator
    {
        private const float ReadyTimeoutSeconds = 240f;
        private static readonly Regex CreationId = new Regex(@"\A[0-9a-f]{32}\z", RegexOptions.CultureInvariant);
        private static readonly Regex NativeName = new Regex(@"\A[A-Za-z0-9_-]{1,128}\z", RegexOptions.CultureInvariant);
        private static readonly Regex Reference = new Regex(@"\A[A-Za-z0-9_.:-]{1,256}\z", RegexOptions.CultureInvariant);
        private static HarmonyLib.Harmony? _harmony;
        // Roots exact director/metadata even after uncertain timeout or dispatch exception.
        private static Association? _association;
        private static CompletedWorld? _completedWorld;
        [ThreadStatic] private static Association? _scope;
        private readonly Association _owned;
        private readonly CommandResult _result;
        private readonly float _startedAt;
        private bool _saveIssued, _finished, _errored;
        public static string GameNameFor(string creationId) => "sb2_" + creationId;
        private CounterpartCreator(Association owned, CommandResult result)
        { _owned = owned; _result = result; _startedAt = UnityEngine.Time.realtimeSinceStartup; }

        public static CounterpartCreator? Begin(BridgeCommand command, OpJournal journal, int pid, out CommandResult? immediate,
            Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool>? storageBinding = null)
        {
            immediate = NewResult(command);
            var result = immediate;
            if (command.Kind != "create_save" || !CreationId.IsMatch(command.CreationId ?? ""))
            { result.Reason = "invalid creation command or id"; return null; }
            var name = GameNameFor(command.CreationId!);
            result.SaveId = name;
            var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
            try
            {
                if (director == null || !BindingCurrent(storageBinding, director))
                { result.Uncertain = journal.HasAny("create:" + command.CreationId) || journal.HasAny(command.OpId);
                  result.Reason = "owned native storage binding unavailable"; return null; }
                if (!StorageReady(director)) { result.Reason = "native storage not ready"; return null; }
                var traversal = Capture(director);
                var stock = StockSettings();
                if (Matching(traversal, name).Count > 0)
                {
                    Verify(director, traversal, name, null, stock, result);
                    result.Ok = result.Verified == true; result.Uncertain = !result.Ok; result.Persisted = result.Ok;
                    if (result.Ok) result.Reason = "creation already exists natively; not re-created";
                    return null;
                }
                if (journal.HasAny("create:" + command.CreationId) || journal.HasAny(command.OpId))
                { result.Uncertain = true; result.Reason = "creation was previously dispatched; reconcile, never create again"; return null; }
                if (!MenuIdle(director) || _association != null) { result.Reason = "not at an idle main menu or another creation is associated"; return null; }
                if (!EnsurePatched(out var error)) { result.Reason = "native creation hooks unavailable: " + error; return null; }
                // Fresh COMPLETE traversal immediately before intent/dispatch, never cached capability data or fixed slots.
                traversal = Capture(director);
                if (!MenuIdle(director) || _association != null || traversal.FreeSlot == null || Matching(traversal, name).Count != 0)
                { result.Reason = "fresh native vacancy or idle-menu recheck failed"; return null; }
                var slot = traversal.FreeSlot.Value;
                var metadata = new Il2CppMonomiPark.SlimeRancher.AutoSaveDirector.LoadNewGameMetadata { saveSlotIndex = slot, gameSettingsModel = stock.Model };
                var association = new Association(director, metadata, name, slot, stock, storageBinding!);
                if (!BindingCurrent(storageBinding, director))
                { result.Reason = "owned native storage binding lost before intent"; return null; }
                var creator = new CounterpartCreator(association, result);
                // Partial journaling closes the creation permanently; dispatch follows BOTH durable intents.
                journal.WriteIntent("create:" + command.CreationId);
                journal.WriteIntent(command.OpId);
                if (!BindingCurrent(storageBinding, director))
                { result.Uncertain = true; result.Reason = "owned native storage binding lost after intent"; return null; }
                _association = association;
                try { director.LoadNewGame(metadata, (Il2CppSystem.Action)(Action)(() => creator._errored = true)); }
                catch (Exception ex) { creator.FinishUncertain("LoadNewGame threw " + ex.GetType().Name); return null; }
                immediate = null; return creator;
            }
            catch (Exception ex)
            {
                result.Uncertain = journal.HasAny("create:" + command.CreationId) || journal.HasAny(command.OpId);
                result.Reason = "native creation preparation failed: " + ex.GetType().Name + ": " + ex.Message;
                return null;
            }
        }

        public CommandResult? Step()
        {
            if (_finished) return _result;
            try
            {
                if (_owned.AbortReason != null) return FinishUncertain(_owned.AbortReason);
                if (_errored) return FinishUncertain("native new-game error callback");
                var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
                if (director == null || !BindingCurrent(_owned.StorageBinding, director))
                    return FinishUncertain("owned native storage binding lost during creation");
                var loader = Il2Cpp.SystemContext.Instance?.SceneLoader;
                var scene = Il2Cpp.SceneContext.Instance;
                if (UnityEngine.Time.realtimeSinceStartup - _startedAt > ReadyTimeoutSeconds)
                    return FinishUncertain("native creation timeout: directorMatch=" + (director != null && director.Pointer == _owned.Director.Pointer) +
                        " loaded=" + (director != null && director.loadedGame) + " saved=" + (director?.SavedGame != null) +
                        " loader=" + (loader != null) + " busy=" + (loader != null && loader.IsSceneLoadInProgress) +
                        " scene=" + (scene != null) + " playerState=" + (scene?.PlayerState != null) +
                        " player=" + (scene?.Player != null) + " active=" + (scene?.Player != null && scene.Player.activeInHierarchy) +
                        " iterator=" + (_owned.Iterator != IntPtr.Zero) + " named=" + _owned.NameConsumed + " flush=" + _saveIssued +
                        " metadataPending=" + (director?.newGameMetadata != null) + " gameplay=" + (loader != null && loader.IsCurrentSceneGroupGameplay()) +
                        " gameModel=" + (scene?.GameModel != null) + " settings=" + (scene?.GameModel?.gameSettings != null));
                // Native NEW GAME keeps loadedGame=false. OnNextSceneLoaded_NewGame applies settings
                // and clears newGameMetadata; a callback or player presence alone is not completion.
                if (director == null || director.Pointer != _owned.Director.Pointer || loader == null || loader.IsSceneLoadInProgress ||
                    !loader.IsCurrentSceneGroupGameplay() || director.loadedGame || director.newGameMetadata != null || director.SavedGame == null ||
                    scene?.PlayerState == null || scene.Player == null || !scene.Player.activeInHierarchy) return null;
                var appliedSettings = scene.GameModel?.gameSettings;
                if (appliedSettings == null) return null;
                if (!_owned.NameConsumed || _owned.Iterator == IntPtr.Zero || director.SavedGame.GetName() != _owned.GameName || director.SavedGame.GameState == null ||
                    director.SavedGame.GameState.SaveSlotIndex != _owned.Slot) return FinishUncertain("live creation name, slot or scoped hook evidence mismatched");
                if (!AppliedStockSettings(appliedSettings, _owned.Stock)) return FinishUncertain("applied native new-game stock settings mismatched");
                if (_saveIssued) return FinishUncertain("native flush already issued without final proof");
                var preFlushTraversal = Capture(director);
                if (preFlushTraversal.Rows.Exists(row => row.SaveSlotIndex == _owned.Slot && row.Name != _owned.GameName))
                    return FinishUncertain("native creation slot occupied by another game before flush");
                var liveSavePointer = director.SavedGame.Pointer;
                if (liveSavePointer == IntPtr.Zero) return FinishUncertain("native live save identity unavailable");
                _saveIssued = true; // BEFORE a void native call that may throw after writing
                var beforeFlush = Fingerprints(director, Matching(preFlushTraversal, _owned.GameName));
                if (!BindingCurrent(_owned.StorageBinding, director))
                    return FinishUncertain("owned native storage binding lost before flush");
                director.SaveGameAndFlush();
                _result.Applied = true;
                if (!BindingCurrent(_owned.StorageBinding, director))
                    return FinishUncertain("owned native storage binding lost during flush");
                Verify(director, Capture(director), _owned.GameName, _owned.Slot, _owned.Stock, _result, beforeFlush);
                if (!BindingCurrent(_owned.StorageBinding, director))
                    return FinishUncertain("owned native storage binding lost during readback");
                if (_result.Verified != true) return FinishUncertain(_result.Reason);
                if (!CompletionCurrent(_owned, liveSavePointer))
                    return FinishUncertain("owned live world changed during flush/readback");
                _result.Ok = _result.Persisted = true; // logical provider readback, NOT power-loss/fsync proof
                if (_result.Applied && _result.Verified == true && !_result.Uncertain && _saveIssued)
                    _completedWorld = new CompletedWorld(_owned, liveSavePointer);
                Finish(); return _result;
            }
            catch (Exception ex) { return FinishUncertain("native completion failed: " + ex.GetType().Name + ": " + ex.Message); }
        }

        /// <summary>Read-only traversal and detached read; never dispatches or flushes.</summary>
        public static CommandResult Reconcile(BridgeCommand command, Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool>? storageBinding = null)
        {
            var result = NewResult(command); result.CanCreate = false;
            if (!CreationId.IsMatch(command.CreationId ?? "")) { result.Reason = "invalid creation id"; return result; }
            result.SaveId = GameNameFor(command.CreationId!);
            try
            {
                var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
                if (director == null || !StorageReady(director)) { result.Reason = "native storage not ready"; return result; }
                var traversal = Capture(director); var stock = StockSettings();
                Verify(director, traversal, result.SaveId, null, stock, result);
                var absent = result.Before == 0 && result.Reason == "no native save for creation";
                result.Ok = result.Verified == true || absent; result.Uncertain = !result.Ok; result.Persisted = result.Verified == true;
                result.CanCreate = absent && MenuIdle(director) && traversal.FreeSlot != null && _association == null && BindingCurrent(storageBinding, director);
            }
            catch (Exception ex) { result.Reason = "native reconciliation failed: " + ex.GetType().Name + ": " + ex.Message; }
            return result;
        }
        private static CommandResult NewResult(BridgeCommand command) => new CommandResult
        { Realm = "SR2", OpId = command.OpId, Kind = command.Kind, CreationId = command.CreationId ?? "", Verified = false };
        private CommandResult FinishUncertain(string reason)
        { _result.Ok = _result.Persisted = false; _result.Verified = false; _result.Uncertain = true; _result.Reason = reason; Finish(); return _result; }
        private void Finish()
        { _finished = true; _owned.Terminal = true; if (_owned.NameConsumed && ReferenceEquals(_association, _owned)) _association = null; }
        private static bool BindingCurrent(Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool>? binding, Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director)
        { try { return binding != null && binding(director); } catch (Exception) { return false; } }
        private static bool StorageReady(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director) =>
            director._StorageProvider_k__BackingField != null && director._StorageProvider_k__BackingField.IsInitialized() && director.ProfileManager != null;
        private static bool MenuIdle(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director)
        {
            var loader = Il2Cpp.SystemContext.Instance?.SceneLoader; var group = loader?._currentSceneGroup;
            return StorageReady(director) && !director.loadedGame && Il2Cpp.SceneContext.Instance?.PlayerState == null && loader != null &&
                !loader.IsSceneLoadInProgress && group != null && group.name.StartsWith("MainMenu", StringComparison.Ordinal);
        }

        private static Traversal Capture(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director)
        {
            if (!StorageReady(director)) throw new InvalidOperationException("native storage not ready");
            var count = director.SaveSlotCount; var raw = director._StorageProvider_k__BackingField!.GetAvailableGames();
            if (raw == null || raw.Count > 4096) throw new InvalidOperationException("incomplete/excessive raw native traversal");
            var rows = new List<Il2Cpp.GameData.Summary>();
            var names = new List<string>(); var games = new List<string>(); var slots = new List<int>(); var invalid = new List<bool>();
            foreach (var name in raw)
            {
                if (name == null || !NativeName.IsMatch(name) || !director._StorageProvider_k__BackingField!.HasGameData(name)) throw new InvalidOperationException("invalid raw native name/data");
                var row = director.LoadSummary(name);
                if (row == null || row.IsInvalid || row.SaveName != name || (row.Name == null || !NativeName.IsMatch(row.Name)) || row.IconId == null ||
                    !Reference.IsMatch(row.IconId.ReferenceId ?? "")) throw new InvalidOperationException("invalid native summary");
                rows.Add(row); names.Add(name); games.Add(row.Name); slots.Add(row.SaveSlotIndex); invalid.Add(row.IsInvalid);
            }
            int? free;
            try { free = SaveSlotSelection.SelectFree(count, names.ToArray(), games.ToArray(), slots.ToArray(), invalid.ToArray()); }
            catch (InvalidOperationException ex) when (ex.Message == "No free native save slot") { free = null; }
            return new Traversal(rows, free);
        }
        private static Stock StockSettings()
        {
            var game = Il2Cpp.GameContext.Instance; var items = game?.OptionsDirector?._optionsItemDefinitions?.GameBasedDefinitions;
            var icon = game?.GameSettingsDirector?.GameIcon;
            if (items == null || icon == null || !Reference.IsMatch(icon.ReferenceId ?? "")) throw new InvalidOperationException("stock definitions/icon unavailable");
            var definitions = new Dictionary<string, IntPtr>(StringComparer.Ordinal);
            foreach (var item in NativeStockDefinitions.Capture(items))
            {
                if (definitions.Count >= 4096 || item == null || item.IsProfileSetting || (item.ReferenceId == null || !Reference.IsMatch(item.ReferenceId)) ||
                    definitions.ContainsKey(item.ReferenceId)) throw new InvalidOperationException("invalid stock definition identity");
                definitions.Add(item.ReferenceId, item.Pointer);
            }
            if (definitions.Count == 0) throw new InvalidOperationException("empty stock definitions");
            // Detached constructor/icon only: no apply/reset/push/global UI mutation.
            var model = new GameSettingsModel(items); model.SetGameIconForNewGame(icon);
            if (model.gameIconDefinition == null || model.gameIconDefinition.Pointer != icon.Pointer || model.optionsById == null)
                throw new InvalidOperationException("stock model/icon unavailable");
            var keys = new HashSet<string>(StringComparer.Ordinal);
            foreach (var entry in model.optionsById)
                if (!definitions.TryGetValue(entry.Key, out var pointer) || !keys.Add(entry.Key) || entry.Value == null ||
                    entry.Value.optionsItemDefinition == null || entry.Value.optionsItemDefinition.Pointer != pointer ||
                    entry.Value.optionsItemDefinition.ReferenceId != entry.Key || string.IsNullOrWhiteSpace(entry.Value.OptionValueKey))
                    throw new InvalidOperationException("stock model identity/default mismatch");
            if (keys.Count != definitions.Count) throw new InvalidOperationException("stock model coverage mismatch");
            // Native lookup-aware serializer supplies persistence keys/values; one-argument Pull is an error path.
            var icons = new Il2CppSystem.Collections.Generic.List<GameIconDefinition>(); icons.Add(icon);
            var lookup = new PersistenceIdLookupTable<GameIconDefinition>(icons.Cast<Il2CppSystem.Collections.Generic.IEnumerable<GameIconDefinition>>());
            var serialized = new GameSettingsV01(); model.Pull(ref serialized, lookup); var expected = Options(serialized);
            if (expected.Count != definitions.Count) throw new InvalidOperationException("stock serialization coverage mismatch");
            return new Stock(model, icon.ReferenceId!, serialized.GameIconId, expected);
        }
        private static bool AppliedStockSettings(GameSettingsModel model, Stock stock)
        {
            // Compare persisted semantics using the same lookup-aware native serializer as StockSettings.
            // No pointer-identity assumption: reconstructed models with identical contents are valid.
            var icon = model.gameIconDefinition;
            if (icon == null || icon.ReferenceId != stock.Icon) return false;
            var icons = new Il2CppSystem.Collections.Generic.List<GameIconDefinition>(); icons.Add(icon);
            var lookup = new PersistenceIdLookupTable<GameIconDefinition>(icons.Cast<Il2CppSystem.Collections.Generic.IEnumerable<GameIconDefinition>>());
            var serialized = new GameSettingsV01(); model.Pull(ref serialized, lookup);
            if (serialized.GameIconId != stock.IconIndex) return false; // same single-icon lookup as the stock serializer
            var options = Options(serialized);
            if (options.Count != stock.Options.Count) return false;
            foreach (var pair in stock.Options)
                if (!options.TryGetValue(pair.Key, out var value) || value != pair.Value) return false;
            return true;
        }
        private static Dictionary<string, string> Options(GameSettingsV01 settings)
        {
            if (settings?.OptionItems == null) throw new InvalidOperationException("native option payload missing");
            var result = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var option in settings.OptionItems)
            {
                if (result.Count >= 4096 || option == null || (option.PersistenceKey == null || !Reference.IsMatch(option.PersistenceKey)) ||
                    string.IsNullOrWhiteSpace(option.OptionValueKey) || result.ContainsKey(option.PersistenceKey)) throw new InvalidOperationException("invalid/duplicate persisted options");
                result.Add(option.PersistenceKey, option.OptionValueKey);
            }
            if (result.Count == 0) throw new InvalidOperationException("empty persisted options");
            return result;
        }
        private static List<Il2Cpp.GameData.Summary> Matching(Traversal traversal, string name) =>
            traversal.Rows.FindAll(row => row.Name == name || row.SaveName == name || row.SaveName.StartsWith(name + "_", StringComparison.Ordinal));
        private static void Verify(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, Traversal traversal, string name, int? expectedSlot, Stock stock, CommandResult result, Dictionary<string, string>? beforeFlush = null)
        {
            result.Verified = false; result.SaveFile = result.Sha256 = "";
            var rows = Matching(traversal, name); result.Before = result.After = rows.Count;
            if (rows.Count == 0) { result.Reason = "no native save for creation"; return; }
            var revision = new Regex(@"\A" + Regex.Escape(name) + @"_(0|[1-9][0-9]{0,9})\z", RegexOptions.CultureInvariant);
            var slot = expectedSlot ?? rows[0].SaveSlotIndex; var display = director.GetGameSaveDisplayName(slot);
            foreach (var row in rows)
                if (row.Name != name || !revision.IsMatch(row.SaveName) || row.SaveSlotIndex != slot || row.DisplayName != display || row.IconId!.ReferenceId != stock.Icon)
                { result.Reason = "ambiguous/mismatched native creation summary group"; return; }
            // Numeric filename suffixes are rotating available filenames, NOT chronological revisions.
            rows.Sort((a, b) => b.SaveTimestamp.UtcDateTime.Ticks.CompareTo(a.SaveTimestamp.UtcDateTime.Ticks));
            if (beforeFlush == null && rows.Count > 1 && rows[0].SaveTimestamp.UtcDateTime.Ticks == rows[1].SaveTimestamp.UtcDateTime.Ticks)
            { result.Reason = "ambiguous native latest timestamp"; return; }
            var newest = beforeFlush == null ? rows[0].SaveName : "";
            var changed = 0; string digest = "";
            foreach (var row in rows)
            {
                var stream = new Il2CppSystem.IO.MemoryStream();
                try
                {
                    director._StorageProvider_k__BackingField!.GetGameData(row.SaveName, stream);
                    if (stream.Length <= 0 || stream.Length > 64 * 1024 * 1024) throw new InvalidOperationException("invalid native save size");
                    var bytes = (byte[])stream.ToArray(); stream.Position = 0;
                    var detached = new GameV06 { _isLoadSummary = false }; detached.Load(stream, false);
                    if (detached._isLoadSummary || detached.GameName != name || detached.SaveSlotIndex != slot || detached.DisplayName != display ||
                        detached.World == null || detached.Player == null || detached.Ranch == null || detached.Actors == null || detached.GameSettings == null ||
                        detached.GameIconIndex?.IndexTable == null || detached.GameSettings.GameIconId < 0 ||
                        detached.GameSettings.GameIconId >= detached.GameIconIndex.IndexTable.Length ||
                        detached.GameIconIndex.IndexTable[detached.GameSettings.GameIconId] != stock.Icon) throw new InvalidOperationException("incomplete/mismatched full native creation payload");
                    var options = Options(detached.GameSettings);
                    if (options.Count != stock.Options.Count) throw new InvalidOperationException("persisted settings coverage mismatch");
                    foreach (var pair in stock.Options)
                        if (!options.TryGetValue(pair.Key, out var value) || value != pair.Value) throw new InvalidOperationException("persisted stock setting mismatch");
                    var hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                    if (beforeFlush != null && (!beforeFlush.TryGetValue(row.SaveName, out var oldHash) || oldHash != hash))
                    { changed++; newest = row.SaveName; digest = hash; }
                    else if (beforeFlush == null && row.SaveName == newest) digest = hash;
                }
                finally { stream.Dispose(true); }
            }
            if (beforeFlush != null && changed != 1)
            { result.Reason = "single native flush did not produce one exact changed/new payload"; return; }
            result.SaveFile = newest; result.Sha256 = digest; result.Verified = true; result.Reason = "";
        }
        private static Dictionary<string, string> Fingerprints(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, List<Il2Cpp.GameData.Summary> rows)
        {
            var hashes = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var row in rows)
            {
                var stream = new Il2CppSystem.IO.MemoryStream();
                try
                {
                    director._StorageProvider_k__BackingField!.GetGameData(row.SaveName, stream);
                    if (stream.Length <= 0 || stream.Length > 64 * 1024 * 1024) throw new InvalidOperationException("invalid pre-flush native size");
                    hashes.Add(row.SaveName, Convert.ToHexString(SHA256.HashData((byte[])stream.ToArray())).ToLowerInvariant());
                }
                finally { stream.Dispose(true); }
            }
            return hashes;
        }

        private static bool EnsurePatched(out string error)
        {
            error = ""; if (_harmony != null) return true;
            var harmony = new HarmonyLib.Harmony("slimebridge.sr2.counterpart");
            try
            {
                var move = AccessTools.Method(typeof(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector._LoadNewGame_Coroutine_d__53), "MoveNext", Type.EmptyTypes);
                var name = AccessTools.Method(typeof(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector), "GetGameSaveFileName", new[] { typeof(string) });
                if (move == null || move.ReturnType != typeof(bool) || name == null || name.ReturnType != typeof(string)) throw new InvalidOperationException("exact hook signatures unavailable");
                // Native LoadNewGame DIRECTLY allocates its iterator. A coroutine factory hook misses it.
                harmony.Patch(move, prefix: new HarmonyMethod(typeof(CounterpartCreator), nameof(MovePrefix)), finalizer: new HarmonyMethod(typeof(CounterpartCreator), nameof(MoveFinalizer)));
                harmony.Patch(name, prefix: new HarmonyMethod(typeof(CounterpartCreator), nameof(NamePrefix)));
                _harmony = harmony; return true;
            }
            catch (Exception ex) { try { harmony.UnpatchSelf(); } catch { } error = ex.GetType().Name + ": " + ex.Message; return false; }
        }
        private static bool MovePrefix(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector._LoadNewGame_Coroutine_d__53 __instance, out Association? __state, ref bool __result)
        {
            __state = _scope; _scope = null; // Unrelated/nested iterator masks any matched outer scope.
            var armed = _association;
            if (armed == null) return true;
            if (__instance.__4__this != null && __instance.__4__this.Pointer == armed.Director.Pointer &&
                __instance.metadata != null && __instance.metadata.Pointer == armed.Metadata.Pointer &&
                !BindingCurrent(armed.StorageBinding, armed.Director))
            {
                armed.AbortReason = "owned native iterator lost storage binding";
                __instance.__1__state = -1; __result = false; return false;
            }
            if (armed.NameConsumed || __instance.__1__state != 0) return true;
            var director = __instance.__4__this; var metadata = __instance.metadata;
            if (director == null || metadata == null || director.Pointer != armed.Director.Pointer || metadata.Pointer != armed.Metadata.Pointer ||
                metadata.saveSlotIndex != armed.Slot || metadata.gameSettingsModel == null || metadata.gameSettingsModel.Pointer != armed.Stock.Model.Pointer ||
                (armed.Iterator != IntPtr.Zero && armed.Iterator != __instance.Pointer)) return true;
            try
            {
                // A retained association may execute late: stale slot authorization is never sufficient.
                var fresh = Capture(director);
                if (!MenuIdle(director) || fresh.Rows.Exists(row => row.SaveSlotIndex == armed.Slot) || Matching(fresh, armed.GameName).Count != 0 ||
                    !BindingCurrent(armed.StorageBinding, director))
                    throw new InvalidOperationException("owned native creation lost exact slot/menu authorization");
            }
            catch (Exception ex)
            {
                armed.AbortReason = "owned initial native iterator suppressed: " + ex.GetType().Name + ": " + ex.Message;
                __instance.__1__state = -1; __result = false; return false;
            }
            armed.Iterator = __instance.Pointer; _scope = armed; return true;
        }
        private static Exception? MoveFinalizer(Association? __state, Exception? __exception)
        { _scope = __state; return __exception; } // normal AND exceptional exits: no scope leak or swallowed error
        private static bool NamePrefix(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector __instance, ref string __result)
        {
            var armed = _scope;
            if (armed == null || armed.NameConsumed || !ReferenceEquals(_association, armed) || __instance.Pointer != armed.Director.Pointer) return true;
            armed.NameConsumed = true; __result = armed.GameName; // one-shot BEFORE unchanged native collision loop
            if (armed.Terminal) _association = null;
            return false;
        }
        private sealed class Association
        {
            internal readonly Il2CppMonomiPark.SlimeRancher.AutoSaveDirector Director;
            internal readonly Il2CppMonomiPark.SlimeRancher.AutoSaveDirector.LoadNewGameMetadata Metadata;
            internal readonly string GameName;
            internal readonly int Slot;
            internal readonly Stock Stock;
            internal readonly Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool> StorageBinding;
            internal IntPtr Iterator;
            internal bool NameConsumed, Terminal;
            internal string? AbortReason;
            internal Association(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, Il2CppMonomiPark.SlimeRancher.AutoSaveDirector.LoadNewGameMetadata metadata, string name, int slot, Stock stock, Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool> storageBinding)
            { Director = director; Metadata = metadata; GameName = name; Slot = slot; Stock = stock; StorageBinding = storageBinding; }
        }
        private sealed class Stock
        {
            internal readonly GameSettingsModel Model;
            internal readonly string Icon;
            internal readonly int IconIndex;
            internal readonly Dictionary<string, string> Options;
            internal Stock(GameSettingsModel model, string icon, int iconIndex, Dictionary<string, string> options) { Model = model; Icon = icon; IconIndex = iconIndex; Options = options; }
        }
        // Main thread only. This is an operation-scoped volatile witness, never journal/receipt authority.
        private sealed class CompletedWorld
        {
            internal readonly IntPtr Director, Saved, Metadata, Iterator;
            internal readonly string Name;
            internal readonly int Slot;
            internal readonly Stock Stock;
            internal readonly Func<Il2CppMonomiPark.SlimeRancher.AutoSaveDirector, bool> Guard;
            internal CompletedWorld(Association owned, IntPtr saved)
            {
                Director = owned.Director.Pointer; Saved = saved; Metadata = owned.Metadata.Pointer; Iterator = owned.Iterator;
                Name = owned.GameName; Slot = owned.Slot; Guard = owned.StorageBinding;
                // Copy complete persisted values, not mutable live model identity.
                Stock = new Stock(owned.Stock.Model, owned.Stock.Icon, owned.Stock.IconIndex,
                    new Dictionary<string, string>(owned.Stock.Options, StringComparer.Ordinal));
            }
        }
        internal sealed class WorldReading
        {
            internal readonly Il2Cpp.GameContext Game;
            internal readonly Il2Cpp.SceneContext Scene;
            internal readonly Il2CppMonomiPark.SlimeRancher.AutoSaveDirector Director;
            internal readonly string Name;
            internal WorldReading(Il2Cpp.GameContext game, Il2Cpp.SceneContext scene, Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, string name)
            { Game = game; Scene = scene; Director = director; Name = name; }
        }
        private static bool CompletionCurrent(Association owned, IntPtr saved)
        {
            var director = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
            var scene = Il2Cpp.SceneContext.Instance; var loader = Il2Cpp.SystemContext.Instance?.SceneLoader;
            return owned.AbortReason == null && owned.NameConsumed && owned.Iterator != IntPtr.Zero && owned.Metadata.Pointer != IntPtr.Zero &&
                director != null && director.Pointer != IntPtr.Zero && director.Pointer == owned.Director.Pointer && !director.loadedGame &&
                director.SavedGame != null && director.SavedGame.Pointer == saved && director.SavedGame.GetName() == owned.GameName &&
                director.SavedGame.GameState?.SaveSlotIndex == owned.Slot && director.newGameMetadata == null &&
                loader != null && !loader.IsSceneLoadInProgress && loader.IsCurrentSceneGroupGameplay() &&
                scene?.PlayerState != null && scene.PlayerState.GetCurrHealth() > 0 && scene.Player != null && scene.Player.activeInHierarchy &&
                scene.GameModel?.gameSettings != null && AppliedStockSettings(scene.GameModel.gameSettings, owned.Stock) &&
                BindingCurrent(owned.StorageBinding, director) &&
                OwnedIdentityCurrent(director, saved, owned.GameName, owned.Slot) &&
                Il2Cpp.SceneContext.Instance?.Pointer == scene.Pointer && GameplayIdle();
        }
        private static bool NamedIdentityCurrent(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, IntPtr saved, string name)
        {
            var current = Il2Cpp.GameContext.Instance?.AutoSaveDirector;
            return current != null && current.Pointer == director.Pointer && current.SavedGame?.Pointer == saved &&
                current.SavedGame.GetName() == name && (!current.loadedGame || current._currentGameName == name);
        }
        private static bool OwnedIdentityCurrent(Il2CppMonomiPark.SlimeRancher.AutoSaveDirector director, IntPtr saved, string name, int slot) =>
            NamedIdentityCurrent(director, saved, name) && !director.loadedGame && director.newGameMetadata == null &&
            director.SavedGame!.GameState?.SaveSlotIndex == slot;
        private static bool GameplayIdle()
        {
            var loader = Il2Cpp.SystemContext.Instance?.SceneLoader;
            return loader != null && !loader.IsSceneLoadInProgress && loader.IsCurrentSceneGroupGameplay() &&
                loader._currentSceneGroup?.name?.StartsWith("MainMenu", StringComparison.OrdinalIgnoreCase) != true;
        }
        internal static void ClearWorldReadiness() => _completedWorld = null;
        // Opaque, callback-free identity of the current volatile owned witness. Never authority by
        // itself: callers must still pass TryReadWorld; this only detects a later clear/replace.
        internal static object? CurrentWorldWitness => _completedWorld;
        private static bool LostWorld() { ClearWorldReadiness(); return false; }
        internal static bool TryReadWorld(out WorldReading? reading)
        {
            reading = null;
            try
            {
                var game = Il2Cpp.GameContext.Instance; var director = game?.AutoSaveDirector;
                var scene = Il2Cpp.SceneContext.Instance; var loader = Il2Cpp.SystemContext.Instance?.SceneLoader;
                if (game == null || director == null || director.Pointer == IntPtr.Zero || director.SavedGame == null ||
                    director.SavedGame.Pointer == IntPtr.Zero) return LostWorld();
                var savedPointer = director.SavedGame.Pointer;
                // Native Load populates this director cache; NEWGAME leaves it null/stale.
                // False-flag worlds still require the exact verified owned witness below.
                var name = director.loadedGame ? director._currentGameName : director.SavedGame.GetName();
                if (name == null || !NativeName.IsMatch(name) || director.SavedGame.GetName() != name) return LostWorld();
                if (loader == null || loader._currentSceneGroup?.name?.StartsWith("MainMenu", StringComparison.OrdinalIgnoreCase) == true)
                    return LostWorld();
                var proof = _completedWorld;
                // LOADSAVE is independent authority. It cannot preserve an old false-flag witness.
                if (director.loadedGame) { ClearWorldReadiness(); proof = null; }
                else
                {
                    if (proof == null) return false;
                    // Identity/storage/metadata losses are permanent, even during a temporary load.
                    if (proof.Director != director.Pointer || proof.Saved != director.SavedGame.Pointer || proof.Name != name ||
                        director.SavedGame.GameState?.SaveSlotIndex != proof.Slot || director.newGameMetadata != null ||
                        !BindingCurrent(proof.Guard, director)) return LostWorld();
                    var settings = scene?.GameModel?.gameSettings;
                    if (settings != null && !AppliedStockSettings(settings, proof.Stock)) return LostWorld();
                    if (scene != null && settings == null && !loader.IsSceneLoadInProgress && loader.IsCurrentSceneGroupGameplay())
                        return LostWorld();
                }
                // Native serializers/guards may call back. Recheck identity after those reads.
                if (!NamedIdentityCurrent(director, savedPointer, name) ||
                    (!director.loadedGame && (!OwnedIdentityCurrent(director, savedPointer, name, proof!.Slot) || !BindingCurrent(proof.Guard, director)))) return LostWorld();
                if (scene == null || scene.Pointer == IntPtr.Zero || Il2Cpp.SceneContext.Instance?.Pointer != scene.Pointer) return false;
                // Scene/player liveness is temporary: never reuse an old scene or its counts.
                if (!GameplayIdle() ||
                    scene.PlayerState == null || scene.Player == null || !scene.Player.activeInHierarchy || scene.PlayerState.GetCurrHealth() <= 0) return false;
                reading = new WorldReading(game, scene, director, name); return true;
            }
            catch (Exception) { return LostWorld(); }
        }

        private sealed class Traversal
        {
            internal readonly List<Il2Cpp.GameData.Summary> Rows;
            internal readonly int? FreeSlot;
            internal Traversal(List<Il2Cpp.GameData.Summary> rows, int? freeSlot) { Rows = rows; FreeSlot = freeSlot; }
        }
    }
}
#endif
