using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SlimeBridge.Shared;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Executes BridgeHost inventory commands against the native SR1 vacpack (<see cref="Ammo"/>).
    /// HTTP runs on worker tasks; every native read/mutation happens on Unity's main thread in Tick().
    /// Each mutating op is journaled (INTENT fsynced before the native call, DONE after) so that a
    /// redelivered or replayed command is never applied twice.
    /// </summary>
    internal sealed class InventoryAgent
    {
        private static readonly int Pid = Process.GetCurrentProcess().Id;
        private readonly string _url;
        private readonly string _token;
        private readonly bool _labGiveAllowed;
        private readonly bool _labCreationAllowed;
        private readonly OpJournal? _journal;
        private CounterpartCreator? _creation;
        private readonly BackgroundIntroSkipper _intro;
        private BridgeCommand? _incoming;
        private int _polling;
        private int _posting;
        private string? _outgoing;
        private float _nextPoll;
        private string? _log;

        private InventoryAgent(string url, string token, bool labGiveAllowed, bool labCreationAllowed, OpJournal? journal)
        {
            _url = url.TrimEnd('/');
            _token = token;
            _labGiveAllowed = labGiveAllowed;
            _labCreationAllowed = labCreationAllowed;
            _journal = journal;
            _intro = new BackgroundIntroSkipper(labCreationAllowed);
        }

        public static InventoryAgent? Create(bool labOracle, bool labCreationAllowed)
        {
            var url = Environment.GetEnvironmentVariable("SLIMEBRIDGE_URL");
            var token = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TOKEN");
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token)) return null;
            var journal = OpJournal.Open(Environment.GetEnvironmentVariable("SLIMEBRIDGE_OP_JOURNAL"), out var error);
            UnityEngine.Debug.Log("[SlimeBridge] inventory agent: journal=" + (journal != null ? "open" : "unavailable (" + error + "); mutations refused") +
                                  " labGive=" + labOracle);
            return new InventoryAgent(url!, token!, labOracle, labCreationAllowed, journal);
        }

        /// <summary>Main thread only.</summary>
        public void Tick()
        {
            _intro.Tick(); // Native intro ownership outlives creation result/ACK delivery.
            var log = Interlocked.Exchange(ref _log, null);
            if (log != null) UnityEngine.Debug.Log(log);

            // Never accept a new command while a result is still being delivered.
            if (Volatile.Read(ref _posting) != 0) return;
            if (_creation != null)
            {
                var completed = _creation.Step();
                if (completed == null) return;
                _creation = null;
                Post(FinishCreation(completed));
                return;
            }
            var command = Interlocked.Exchange(ref _incoming, null);
            if (command != null)
            {
                string json;
                if (_journal != null && _journal.TryGetDone(command.OpId, out var stored))
                    json = command.IsCreation ? stored : CommandResult.ReplayInventory(command, "SR1", stored, Recheck).ToJson(Pid);
                else if (command.IsCreation)
                {
                    var result = new CommandResult { Realm = "SR1", OpId = command.OpId, Kind = command.Kind,
                        CreationId = command.CreationId ?? "", Count = command.Count };
                    if (!_labCreationAllowed) result = Refuse(result, "native creation refused outside authorized lab oracle");
                    else if (command.Kind == "reconcile_save")
                    {
                        result = CounterpartCreator.Reconcile(command);
                        // Readiness must not advertise a creation the agent would then refuse.
                        if (_journal == null) result.CanCreate = false;
                    }
                    else if (_journal == null) result = Refuse(result, "no op journal; native creation refused");
                    else
                    {
                        try
                        {
                            _creation = CounterpartCreator.Begin(command, _journal, Pid, out var immediate);
                            if (_creation != null) { _intro.Arm(_creation); return; }
                            result = immediate!;
                        }
                        catch (Exception ex)
                        {
                            result.Uncertain = _journal.HasAny(command.OpId);
                            result.Reason = "creation exception " + ex.GetType().Name + ": " + ex.Message;
                        }
                    }
                    json = command.Mutates ? FinishCreation(result) : result.ToJson(Pid);
                }
                else
                {
                    json = Execute(command).ToJson(Pid);
                    // A refused mutation is final too: a late redelivery must not apply it after the
                    // host has already recorded the refusal.
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
            Task.Run(Poll);
        }

        private string FinishCreation(CommandResult result)
        {
            try { _journal?.WriteDone(result.OpId, result.ToJson(Pid)); }
            catch (Exception ex)
            {
                result.Ok = false;
                result.Uncertain = true;
                result.Reason = "creation result journal failed: " + ex.GetType().Name;
            }
            _intro.CreationFinished(result);
            return result.ToJson(Pid);
        }

        internal void CancelBackgroundIntro() => _intro.Cancel();

        private CommandResult Execute(BridgeCommand command)
        {
            var result = new CommandResult { Realm = "SR1", OpId = command.OpId, Kind = command.Kind,
                Item = command.Item, Count = command.Count, SaveId = command.SaveId ?? "", Verified = false };
            if (!TryResolveItem(command.Item, out var id, out var itemProblem)) return Refuse(result, itemProblem);
            Ammo? liveAmmo = null;
            var intentWritten = false;
            try
            {
                var game = SRSingleton<GameContext>.Instance;
                var scene = SRSingleton<SceneContext>.Instance;
                var director = game == null ? null : game.AutoSaveDirector;
                if (director == null || scene == null || scene.PlayerState == null || director.IsLoadingGame() ||
                    director.SavedGame == null || scene.Player == null || !scene.Player.activeInHierarchy)
                    return Refuse(result, "world not ready");
                result.SaveId = director.SavedGame.GetName() ?? result.SaveId;
                if (command.SaveId == null || !string.Equals(command.SaveId, result.SaveId, StringComparison.Ordinal))
                    return Refuse(result, "loaded save does not match command");
                var ammo = scene.PlayerState.GetAmmo(PlayerState.AmmoMode.DEFAULT);
                if (ammo == null) return Refuse(result, "no default ammo");
                liveAmmo = ammo;
                result.Before = Count(ammo, id);
                result.After = result.Before;
                var slots = Snapshot(ammo, id);
                result.CanAdd = InventoryRules.PlanAdd(slots, command.Count > 0 ? command.Count : 1) != null;
                if (command.Kind == "query") { result.Ok = true; return result; }

                if (command.Count < 1 || command.Count > 30) return Refuse(result, "count outside native transfer range");

                if (command.Kind == "lab_give" && !_labGiveAllowed) return Refuse(result, "lab_give refused outside lab oracle");
                if (_journal == null) return Refuse(result, "no op journal; mutation refused");
                if (_journal.HasIntentOnly(command.OpId))
                {
                    // Crashed between INTENT and DONE: the native effect is unknown. Report, never re-apply.
                    result.Uncertain = true;
                    result.Reason = "intent journaled without completion; reconcile by count";
                    _journal.WriteDone(command.OpId, result.ToJson(Pid));
                    return result;
                }
                var removing = command.Kind == "remove";
                var plan = removing ? InventoryRules.PlanRemove(slots, command.Count) : InventoryRules.PlanAdd(slots, command.Count);
                if (plan == null) return Refuse(result, removing ? "insufficient usable native count" : "insufficient usable native capacity");

                _journal.WriteIntent(command.OpId);
                intentWritten = true;
                var nativeOk = InventoryRules.Apply(plan, ammo.GetSlotCount, (index, amount) =>
                {
                    if (removing)
                    {
                        if (ammo.GetSlotName(index) != id || ammo.GetSlotCount(index) < amount) return false;
                        ammo.Decrement(index, amount);
                        return true;
                    }
                    var name = ammo.GetSlotName(index);
                    if (name != id && name != Identifiable.Id.NONE) return false;
                    if (!ammo.CouldAddToSlot(id, index, false) ||
                        ammo.GetSlotMaxCount(id, index) - ammo.GetSlotCount(index) < amount) return false;
                    return ammo.MaybeAddToSpecificSlot(id, null, index, amount, false);
                }, !removing);
                result.After = Count(ammo, id);
                var expected = removing ? result.Before - command.Count : result.Before + command.Count;
                result.Applied = result.After == expected;
                if (!nativeOk || !result.Applied)
                {
                    result.Uncertain = true;
                    result.Reason = "native slot callback or aggregate count mismatch";
                }
                else
                {
                    // Native autosave always writes a fresh first-free suffix; the single new
                    // revision between these two traversals is the exact file our save produced.
                    var beforeNames = RevisionNames(director, result.SaveId);
                    var proof = InventoryRules.VerifyPersistence(
                        () => !director.IsLoadingGame() && director.SaveAllNow() && !director.IsLoadingGame(),
                        () => ReadbackNew(director, result.SaveId, id, ammo.GetUsableSlotCount(), beforeNames),
                        result.SaveId, result.After);
                    result.Persisted = proof.Verified;
                    result.Verified = proof.Verified;
                    result.SaveFile = proof.SaveFile;
                    result.Sha256 = proof.Sha256;
                    if (!proof.Verified) { result.Uncertain = true; result.Reason = proof.Reason; }
                }
                result.Ok = result.Applied && result.Persisted && result.Verified == true && !result.Uncertain;
                _journal.WriteDone(command.OpId, result.ToJson(Pid));
                _log = "[SlimeBridge] op " + command.OpId + " " + command.Kind + " " + command.Item + " x" + command.Count +
                       ": " + result.Before + " -> " + result.After + " ok=" + result.Ok + " persisted=" + result.Persisted;
                return result;
            }
            catch (Exception ex)
            {
                result.Ok = false;
                result.Persisted = false;
                result.Verified = false;
                if (liveAmmo != null)
                {
                    try
                    {
                        result.After = Count(liveAmmo, id);
                        result.Applied = result.After == result.Before + (command.Kind == "remove" ? -command.Count : command.Count);
                    }
                    catch { }
                }
                result.Uncertain = intentWritten || (result.Before >= 0 && command.Mutates);
                result.Reason = "exception " + ex.GetType().Name + ": " + ex.Message;
                try { _journal?.WriteDone(command.OpId, result.ToJson(Pid)); } catch { }
                return result;
            }
        }

        /// <summary>Main-thread canonical resolution; never substitute another inventory item.</summary>
        internal static bool TryResolveItem(string canonicalId, out Identifiable.Id id, out string problem)
        {
            id = Identifiable.Id.NONE;
            problem = "unsupported native inventory identity: " + canonicalId;
            if (canonicalId == "slime.pink") { id = Identifiable.Id.PINK_SLIME; problem = ""; return true; }
            if (canonicalId == "plort.pink") { id = Identifiable.Id.PINK_PLORT; problem = ""; return true; }
            if (canonicalId != "slime.ringtail") return false;
            var registrar = SpeciesRegistrar.Current;
            if (registrar == null || !registrar.TryResolveNativeId(canonicalId, out var nativeId) || nativeId <= 0)
            { problem = "imported inventory species not registered: " + canonicalId; return false; }
            // Do not debit a source into a placeholder-only destination. TryGet validates the
            // complete local payload hashes and builds only an inactive source visual template.
            if (!ImportedModelLoader.TryGet(canonicalId, out var model, out problem) ||
                model.Root == null || model.VertexTotal <= 0 || model.RendererCount <= 0)
            { if (string.IsNullOrEmpty(problem)) problem = "imported source visual unavailable"; return false; }
            id = (Identifiable.Id)nativeId;
            problem = "";
            return true;
        }

        private static CommandResult Refuse(CommandResult result, string reason)
        {
            result.Ok = false;
            result.Applied = false;
            result.Reason = reason;
            return result;
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

        private static readonly MethodInfo? FullFilePathMethod = typeof(FileStorageProvider).GetMethod(
            "GetFullFilePath", BindingFlags.Instance | BindingFlags.NonPublic);

        /// <summary>Main thread only. All revision names of the group, duplicate-free.</summary>
        internal static List<string> RevisionNames(AutoSaveDirector director, string saveId)
        {
            if (director.StorageProvider == null || !director.StorageProvider.IsInitialized())
                throw new InvalidDataException("native storage unavailable");
            var names = new List<string>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var name in director.StorageProvider.GetAvailableGames())
                if (InventoryRules.IsRevision(name, saveId))
                {
                    if (!seen.Add(name)) throw new InvalidDataException("duplicate native revision");
                    names.Add(name);
                }
            return names;
        }

        /// <summary>Main thread only. In-game save order is the native summary timestamp, never
        /// the reused-first-free suffix order; a tie or an unreadable summary fails closed.</summary>
        private static string Latest(AutoSaveDirector director, string saveId)
        {
            string? latest = null;
            var latestTicks = long.MinValue;
            var tied = false;
            foreach (var name in RevisionNames(director, saveId))
            {
                var summary = director.LoadSummary(name);
                if (summary == null || summary.isInvalid || summary.name != saveId || summary.saveName != name)
                    throw new InvalidDataException("native revision summary identity unavailable");
                var ticks = summary.saveTimestamp.Ticks;
                if (ticks > latestTicks) { latest = name; latestTicks = ticks; tied = false; }
                else if (ticks == latestTicks) tied = true;
            }
            if (latest == null || tied) throw new InvalidDataException("ambiguous latest native revision");
            return latest;
        }

        /// <summary>The provider's exact on-disk path for one revision (private native API).</summary>
        private static string ExactPath(AutoSaveDirector director, string file)
        {
            if (!(director.StorageProvider is FileStorageProvider provider) || FullFilePathMethod == null)
                throw new InvalidDataException("native save path API unavailable");
            if (!(FullFilePathMethod.Invoke(provider, new object[] { file }) is string path))
                throw new InvalidDataException("native save path unavailable");
            return path;
        }

        /// <summary>The single revision our save created must also be the native latest.</summary>
        internal static SaveReadback ReadbackNew(AutoSaveDirector director, string saveId, Identifiable.Id id,
            int usableSlots, IReadOnlyList<string> beforeNames)
        {
            var revision = InventoryRules.SelectNewRevision(beforeNames, RevisionNames(director, saveId), saveId);
            if (revision == null) throw new InvalidDataException("no unambiguous new native revision");
            if (Latest(director, saveId) != revision)
                throw new InvalidDataException("new native revision is not the native latest");
            return Parse(director, saveId, revision, id, usableSlots);
        }

        /// <summary>Re-read exactly the receipt's revision while it is still the native latest.</summary>
        private static SaveReadback ReadbackExact(AutoSaveDirector director, string saveId, string file,
            Identifiable.Id id, int usableSlots)
        {
            if (!InventoryRules.IsRevision(file, saveId))
                throw new InvalidDataException("native revision/world mismatch");
            if (Latest(director, saveId) != file)
                throw new InvalidDataException("receipt revision is not the native latest");
            return Parse(director, saveId, file, id, usableSlots);
        }

        private static SaveReadback Parse(AutoSaveDirector director, string saveId, string file,
            Identifiable.Id id, int usableSlots)
        {
            if (director.IsLoadingGame() || director.SavedGame == null || director.SavedGame.GetName() != saveId)
                throw new InvalidDataException("loaded save identity changed");
            if (director.StorageProvider == null || !director.StorageProvider.IsInitialized())
                throw new InvalidDataException("native storage unavailable");
            using (var stream = new MemoryStream())
            {
                director.StorageProvider.GetGameData(file, stream);
                var bytes = stream.ToArray();
                using (var payload = new MemoryStream(bytes))
                {
                    var game = new MonomiPark.SlimeRancher.Persist.GameV12();
                    game.Load(payload);
                    if (payload.Position != payload.Length || game.player?.ammo == null ||
                        !game.player.ammo.TryGetValue(PlayerState.AmmoMode.DEFAULT, out var data) || data == null)
                        throw new InvalidDataException("native default Vacpack payload missing or trailing data");
                    var total = 0;
                    for (var i = 0; i < data.Count && i < usableSlots; i++)
                        if (data[i] != null && data[i].id == id)
                        {
                            if (data[i].count < 0) throw new InvalidDataException("negative native ammo count");
                            total = checked(total + data[i].count);
                        }
                    return new SaveReadback(file, game.gameName, total, bytes, ExactPath(director, file));
                }
            }
        }

        private bool Recheck(CommandResult receipt)
        {
            var game = SRSingleton<GameContext>.Instance;
            var scene = SRSingleton<SceneContext>.Instance;
            var director = game == null ? null : game.AutoSaveDirector;
            if (director == null || director.IsLoadingGame() || director.SavedGame == null ||
                director.SavedGame.GetName() != receipt.SaveId || scene?.PlayerState == null ||
                scene.Player == null || !scene.Player.activeInHierarchy) return false;
            var ammo = scene.PlayerState.GetAmmo(PlayerState.AmmoMode.DEFAULT);
            if (!TryResolveItem(receipt.Item, out var id, out _)) return false;
            if (ammo == null || Count(ammo, id) != receipt.After) return false;
            var proof = InventoryRules.VerifyPersistence(() => true,
                () => ReadbackExact(director, receipt.SaveId, receipt.SaveFile, id, ammo.GetUsableSlotCount()),
                receipt.SaveId, receipt.After);
            return proof.Verified && proof.SaveFile == receipt.SaveFile && proof.Sha256 == receipt.Sha256;
        }

        private void Poll()
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(_url + "/v1/commands/next?realm=SR1");
                request.Method = "GET";
                request.Headers[HttpRequestHeader.Authorization] = "Bearer " + _token;
                request.Proxy = null;
                request.Timeout = 1500;
                request.ReadWriteTimeout = 1500;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    if (response.StatusCode != HttpStatusCode.OK) return;
                    var command = BridgeCommand.Parse(reader.ReadToEnd());
                    if (command != null) Interlocked.Exchange(ref _incoming, command);
                }
            }
            catch (Exception) { }
            finally { Interlocked.Exchange(ref _polling, 0); }
        }

        private void Post(string json)
        {
            Interlocked.Exchange(ref _posting, 1);
            _outgoing = json;
            Task.Run(() =>
            {
                try
                {
                    // Retry until the host accepts: the result is the only proof of what happened.
                    for (var attempt = 0; attempt < 120; attempt++)
                    {
                        try
                        {
                            var request = (HttpWebRequest)WebRequest.Create(_url + "/v1/commands/result");
                            request.Method = "POST";
                            request.ContentType = "application/json";
                            request.Headers[HttpRequestHeader.Authorization] = "Bearer " + _token;
                            request.Proxy = null;
                            request.Timeout = 1500;
                            request.ReadWriteTimeout = 1500;
                            var payload = Encoding.UTF8.GetBytes(_outgoing!);
                            request.ContentLength = payload.Length;
                            using (var stream = request.GetRequestStream()) stream.Write(payload, 0, payload.Length);
                            using (var response = (HttpWebResponse)request.GetResponse())
                                if (response.StatusCode == HttpStatusCode.OK) return;
                        }
                        catch (WebException ex) when (ex.Response is HttpWebResponse r && r.StatusCode == HttpStatusCode.BadRequest)
                        {
                            _log = "[SlimeBridge] host rejected command result as invalid";
                            return;
                        }
                        catch (Exception) { }
                        Thread.Sleep(500);
                    }
                }
                finally { Interlocked.Exchange(ref _posting, 0); }
            });
        }
    }
}
