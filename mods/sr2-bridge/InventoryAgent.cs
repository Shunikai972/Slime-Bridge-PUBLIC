using System;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Executes BridgeHost inventory commands against the native SR2 vacpack (Il2Cpp.Ammo).
    /// HTTP runs on worker tasks; every native read/mutation happens on the Unity main thread in Tick().
    /// Mutating ops are journaled (INTENT fsynced before the native call, DONE after) so a redelivered
    /// or replayed command is never applied twice.
    /// </summary>
    internal sealed class InventoryAgent
    {
        private static readonly int Pid = Environment.ProcessId;
        private static readonly HttpClient Http = new HttpClient(new HttpClientHandler { UseProxy = false })
        {
            Timeout = TimeSpan.FromMilliseconds(1500),
        };
        private readonly string _url;
        private readonly string _token;
        private readonly bool _labGiveAllowed;
        private readonly bool _labCreate;
        private readonly Func<Il2Cpp.AutoSaveDirector, bool>? _storageBinding;
        private CounterpartCreator? _creation;
        private readonly Func<bool>? _background;
        private BackgroundIntroSkipper? _intro;
        private int _introEpoch; // Cancellation can occur during constructor callbacks, before publication.
        // A fresh non-null Begin is the only source of this request. It is NOT intro permission
        // until the same CREATE has completed and journaled a verified current owned witness.
        private string? _introOp, _introCreationId;
        private IntPtr _introDirector;
        private bool _shutdown;
        private readonly OpJournal? _journal;
        private readonly Action<string> _log;
        private BridgeCommand? _incoming;
        private int _polling;
        private int _posting;
        private float _nextPoll;
        private string? _pendingLog;

        private InventoryAgent(string url, string token, bool labGiveAllowed, OpJournal? journal, Action<string> log)
            : this(url, token, labGiveAllowed, journal, log, false) { }

        private InventoryAgent(string url, string token, bool labGiveAllowed, OpJournal? journal, Action<string> log, bool labCreate,
            Func<Il2Cpp.AutoSaveDirector, bool>? storageBinding = null)
            : this(url, token, labGiveAllowed, journal, log, labCreate, storageBinding, null) { }

        private InventoryAgent(string url, string token, bool labGiveAllowed, OpJournal? journal, Action<string> log, bool labCreate,
            Func<Il2Cpp.AutoSaveDirector, bool>? storageBinding, Func<bool>? background)
        {
            _url = url.TrimEnd('/');
            _token = token;
            _labGiveAllowed = labGiveAllowed;
            _labCreate = labCreate && storageBinding != null;
            _storageBinding = storageBinding;
            _background = background;
            _journal = journal;
            _log = log;
        }

        public static InventoryAgent? Create(bool labOracle, Action<string> log, bool labCreate = false,
            Func<Il2Cpp.AutoSaveDirector, bool>? storageBinding = null)
        {
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token)) return null;
            var journal = OpJournal.Open(Environment.GetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL"), out var error);
            log("inventory agent: journal=" + (journal != null ? "open" : "unavailable (" + error + "); mutations refused") + " labGive=" + labOracle);
            return new InventoryAgent(url!, token!, labOracle, journal, log, labCreate, storageBinding);
        }

        /// <summary>Main thread only.</summary>
        public void Tick()
        {
            if (_shutdown) return;
            var intro = _intro;
            intro?.Tick(); // Main thread, before POST/poll/network gates; survives successful _creation=null.
            if (intro?.Stopped == true && ReferenceEquals(_intro, intro)) _intro = null;
            var message = Interlocked.Exchange(ref _pendingLog, null);
            if (message != null) _log(message);
            if (Volatile.Read(ref _posting) != 0) return;
            if (_creation != null)
            {
                var completed = _creation.Step();
                if (completed == null) return;
                _creation = null;
                var finalJson = completed.ToJson(Pid);
                var journaled = false;
                try
                {
                    if (_journal != null) { _journal.WriteDone(completed.OpId, finalJson); journaled = true; }
                }
                catch (Exception) { CancelBackgroundIntro(); }
                // A receipt alone, existing-save Begin, Reconcile, or replay cannot reach here.
                if (journaled && _introOp != null && _introCreationId != null && _introDirector != IntPtr.Zero &&
                    completed.Realm == "SR2" && completed.Kind == "create_save" && completed.OpId == _introOp &&
                    completed.CreationId == _introCreationId && completed.SaveId == CounterpartCreator.GameNameFor(_introCreationId) &&
                    completed.Ok && completed.Applied && completed.Persisted && completed.Verified == true && !completed.Uncertain)
                {
                    var epoch = _introEpoch;
                    _log("background intro arm attempt save=" + completed.SaveId + " op=" + completed.OpId);
                    var candidate = new BackgroundIntroSkipper(completed.SaveId, _introDirector, _log, _background);
                    if (_shutdown || epoch != _introEpoch) candidate.Cancel();
                    else if (!candidate.Stopped) _intro = candidate;
                }
                else
                {
                    _log("background intro not armed: completion is not a journaled verified fresh owned CREATE op=" + completed.OpId);
                    CancelBackgroundIntro();
                }
                _introOp = _introCreationId = null; _introDirector = IntPtr.Zero;
                Post(finalJson);
                return;
            }
            var command = Interlocked.Exchange(ref _incoming, null);
            if (command != null)
            {
                string json;
                if (!command.IsCreation && _journal != null && _journal.TryGetDone(command.OpId, out var stored))
                    json = CommandResult.ReplayInventory(command, "SR2", stored, RecheckAfterState).ToJson(Pid);
                else
                {
                    var result = command.IsCreation ? ExecuteCreation(command) : Execute(command);
                    if (result == null) return; // native coroutine is pending; do not poll or acknowledge early
                    json = result.ToJson(Pid);
                    if (command.Mutates && _journal != null && !_journal.TryGetDone(command.OpId, out _))
                    {
                        try { _journal.WriteDone(command.OpId, json); } catch (Exception) { }
                    }
                }
                Post(json);
                return;
            }
            var now = UnityEngine.Time.realtimeSinceStartup;
            if (now < _nextPoll || Interlocked.Exchange(ref _polling, 1) != 0) return;
            _nextPoll = now + 0.5f;
            _ = Task.Run(Poll);
        }

        private CommandResult? ExecuteCreation(BridgeCommand command)
        {
            var refusal = new CommandResult { Realm = "SR2", OpId = command.OpId, Kind = command.Kind,
                CreationId = command.CreationId ?? "", Verified = false, CanCreate = false };
            if (!_labCreate) { refusal.Reason = "native creation disabled outside protected lab opt-in"; return refusal; }
            if (command.Kind == "reconcile_save")
            {
                var result = CounterpartCreator.Reconcile(command, _storageBinding);
                if (_journal == null || _journal.HasAny("create:" + command.CreationId)) result.CanCreate = false;
                return result;
            }
            // Native state is always re-read: an old journal ACK is not native-save proof.
            if (_journal == null) { refusal.Reason = "no op journal; native creation refused"; return refusal; }
            CancelBackgroundIntro();
            _introOp = _introCreationId = null; _introDirector = IntPtr.Zero;
            _creation = CounterpartCreator.Begin(command, _journal, Pid, out var immediate, _storageBinding);
            if (_creation != null)
            {
                _introOp = command.OpId; _introCreationId = command.CreationId;
                _introDirector = Il2Cpp.GameContext.Instance?.AutoSaveDirector?.Pointer ?? IntPtr.Zero;
            }
            return immediate;
        }

        // Pending CREATE must leave its menu scene and still finish through Step's unchanged
        // proof. The epoch also catches unload/deinit DURING helper construction, before _intro
        // is published. It does not prohibit ordinary pending-CREATE menu unloads.
        internal void CancelBackgroundIntro()
        { unchecked { _introEpoch++; } var intro = _intro; _intro = null; intro?.Cancel(); }

        internal void Shutdown()
        {
            _shutdown = true;
            CancelBackgroundIntro();
            _introOp = _introCreationId = null; _introDirector = IntPtr.Zero;
        }

        private CommandResult Execute(BridgeCommand command)
        {
            var result = new CommandResult { Realm = "SR2", OpId = command.OpId, Kind = command.Kind, Item = command.Item, Count = command.Count };
            if (command.IsCreation) return Refuse(result, "save creation is not supported by the inventory adapter");
            if (!System.Text.RegularExpressions.Regex.IsMatch(command.SaveId ?? "", "\\A[A-Za-z0-9_-]{1,128}\\z"))
                return Refuse(result, "exact nonempty save binding is required");
            try
            {
                if (!CounterpartCreator.TryReadWorld(out var world)) return Refuse(result, "world not ready");
                var game = world!.Game; var director = world.Director; var state = world.Scene.PlayerState!;
                result.SaveId = world.Name;
                if (!string.Equals(command.SaveId, result.SaveId, StringComparison.Ordinal))
                    return Refuse(result, "loaded save does not match command");
                if (state.Ammo == null || game.LookupDirector == null) return Refuse(result, "world not ready");
                if (!TryResolveItem(game, command.Item, out var type))
                    return Refuse(result, "native item identity unavailable: " + command.Item);
                var ammo = state.Ammo;
                result.Before = Count(ammo, type);
                result.After = result.Before;
                result.CanAdd = ammo.CouldAddToSlot(type);
                if (command.Kind == "query") { result.Ok = true; return result; }

                if (command.Kind == "lab_give" && !_labGiveAllowed) return Refuse(result, "lab_give refused outside lab oracle");
                if (_journal == null) return Refuse(result, "no op journal; mutation refused");
                if (_journal.HasIntentOnly(command.OpId))
                {
                    result.Uncertain = true;
                    result.Reason = "intent journaled without completion; reconcile by count";
                    _journal.WriteDone(command.OpId, result.ToJson(Pid));
                    return result;
                }
                if (command.Count < 1) return Refuse(result, "count must be positive");
                if (command.Kind == "remove" && result.Before < command.Count) return Refuse(result, "insufficient native count");

                Sr2InventoryPersistence persistence;
                try { persistence = new Sr2InventoryPersistence(game, world.Scene, result.SaveId, type); }
                catch (Exception ex) { return Refuse(result, "native persistence unavailable: " + ex.GetType().Name); }
                _journal.WriteIntent(command.OpId);
                bool nativeOk;
                if (command.Kind == "remove")
                {
                    var idx = FindSlot(ammo, type);
                    nativeOk = idx >= 0 && ammo.GetSlotCount(idx) >= command.Count;
                    if (nativeOk) ammo.Decrement(idx, command.Count);
                }
                else
                {
                    nativeOk = AddToVac(ammo, type, command.Count);
                }
                result.After = Count(ammo, type);
                var expected = command.Kind == "remove" ? result.Before - command.Count : result.Before + command.Count;
                result.Applied = result.After == expected;
                result.Ok = nativeOk && result.Applied;
                if (!result.Ok && result.After != result.Before) result.Uncertain = true;
                if (!result.Ok) result.Reason = nativeOk ? "count mismatch after native call" : "native call refused";
                if (result.Applied)
                {
                    var proof = persistence.Save(result.After);
                    result.Persisted = proof.Verified;
                    result.Verified = proof.Verified;
                    result.SaveFile = proof.SaveFile;
                    result.Sha256 = proof.Sha256;
                    if (!proof.Verified)
                    {
                        result.Ok = false;
                        result.Uncertain = true;
                        result.Reason = proof.Reason;
                    }
                }
                _journal.WriteDone(command.OpId, result.ToJson(Pid));
                _pendingLog = "op " + command.OpId + " " + command.Kind + " " + command.Item + " x" + command.Count +
                              ": " + result.Before + " -> " + result.After + " ok=" + result.Ok + " persisted=" + result.Persisted;
                return result;
            }
            catch (Exception ex)
            {
                CounterpartCreator.ClearWorldReadiness();
                result.Ok = false;
                result.Uncertain = result.Before >= 0 && command.Mutates;
                result.Reason = "exception " + ex.GetType().Name + ": " + ex.Message;
                try { _journal?.WriteDone(command.OpId, result.ToJson(Pid)); } catch { }
                return result;
            }
        }

        // Main-thread replay only: no inventory mutation, native save, CREATE or journal rewrite.
        private static bool RecheckAfterState(CommandResult receipt)
        {
            try
            {
                if (!CounterpartCreator.TryReadWorld(out var world) || world == null || world.Name != receipt.SaveId) return false;
                if (!TryResolveItem(world.Game, receipt.Item, out var type) ||
                    world.Scene.PlayerState?.Ammo == null || Count(world.Scene.PlayerState.Ammo, type) != receipt.After) return false;
                return new Sr2InventoryPersistence(world.Game, world.Scene, receipt.SaveId, type).Recheck(receipt).Verified;
            }
            catch { return false; }
        }

        /// <summary>Resolve only verified canonical/native identities, on Unity's main thread.</summary>
        internal static bool TryResolveItem(Il2Cpp.GameContext game, string canonicalId, out Il2Cpp.IdentifiableType type)
        {
            type = null!;
            string reference;
            if (canonicalId == "slime.pink") reference = BridgeMod.PinkSlimeRef;
            else if (canonicalId == "plort.pink") reference = BridgeMod.PinkPlortRef;
            else if (canonicalId == "slime.ringtail") reference = "SlimeDefinition.Ringtail";
            else return false;
            if (game.LookupDirector == null ||
                !game.LookupDirector.TryFindIdentifiableTypeByReferenceId(reference, out var resolved) || resolved == null ||
                !string.Equals(resolved.ReferenceId, reference, StringComparison.Ordinal)) return false;
            type = resolved;
            return true;
        }

        private static CommandResult Refuse(CommandResult result, string reason)
        {
            result.Ok = false;
            result.Applied = false;
            result.Reason = reason;
            return result;
        }

        private static int Count(Il2Cpp.Ammo ammo, Il2Cpp.IdentifiableType type)
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

        private static int FindSlot(Il2Cpp.Ammo ammo, Il2Cpp.IdentifiableType type)
        {
            var slots = ammo.Slots;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot != null && slot.Id != null && slot.Id.Pointer == type.Pointer && slot.Count > 0) return i;
            }
            return -1;
        }

        /// <summary>Native add of the whole count to one slot: the existing stack, else the first empty unlocked slot.</summary>
        private static bool AddToVac(Il2Cpp.Ammo ammo, Il2Cpp.IdentifiableType type, int count)
        {
            var existing = FindSlot(ammo, type);
            if (existing >= 0) return ammo.MaybeAddToSpecificSlot(type, null, existing, count, false);
            var slots = ammo.Slots;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot == null || !slot.IsUnlocked) continue;
                if (slot.Id != null && slot.Count > 0) continue;
                if (ammo.MaybeAddToSpecificSlot(type, null, i, count, false)) return true;
            }
            return false;
        }

        private async Task Poll()
        {
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, _url + "/v1/commands/next?realm=SR2");
                request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + _token);
                using var response = await Http.SendAsync(request).ConfigureAwait(false);
                if (!response.IsSuccessStatusCode) return;
                var command = BridgeCommand.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
                if (command != null) Interlocked.Exchange(ref _incoming, command);
            }
            catch (Exception) { }
            finally { Interlocked.Exchange(ref _polling, 0); }
        }

        private void Post(string json)
        {
            Interlocked.Exchange(ref _posting, 1);
            _ = Task.Run(async () =>
            {
                try
                {
                    for (var attempt = 0; attempt < 120; attempt++)
                    {
                        try
                        {
                            using var request = new HttpRequestMessage(HttpMethod.Post, _url + "/v1/commands/result")
                            {
                                Content = new StringContent(json, Encoding.UTF8, "application/json"),
                            };
                            request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + _token);
                            using var response = await Http.SendAsync(request).ConfigureAwait(false);
                            if (response.IsSuccessStatusCode) return;
                            if ((int)response.StatusCode == 400) { _pendingLog = "host rejected command result as invalid"; return; }
                        }
                        catch (Exception) { }
                        await Task.Delay(500).ConfigureAwait(false);
                    }
                }
                finally { Interlocked.Exchange(ref _posting, 0); }
            });
        }
    }
}
