// Synthetic fixture harness; not IL2CPP, SR2 payload or in-game proof.
using System.Reflection;

if (args.Contains("--storage")) { StorageBindingChecks.Run(); return; }
if (args.Contains("--focused")) { FocusedChecks.Run(); return; }

var creator = Assembly.GetExecutingAssembly().GetType("SlimeBridge.SR2.CounterpartCreator");
if (creator == null) throw new Exception("FAIL: SR2 counterpart creator is missing");
var name = creator.GetMethod("GameNameFor", BindingFlags.Public | BindingFlags.Static)!;
var actual = (string)name.Invoke(null, new object[] { "0123456789abcdef0123456789abcdef" })!;
if (actual != "sb2_0123456789abcdef0123456789abcdef") throw new Exception("FAIL: full creation marker not preserved");
Console.WriteLine("PASS: full SR2 creation marker preserved");
var director = Fixture.Reset();
var begin = creator.GetMethod("Begin", BindingFlags.Public | BindingFlags.Static);
if (begin == null) throw new Exception("FAIL: native SR2 Begin is missing");
var id = "0123456789abcdef0123456789abcdef";
var command = new SlimeBridge.Shared.BridgeCommand { OpId = "create-test", Kind = "create_save", CreationId = id };
var journalPath = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "sr2-counterpart-fixture-" + Guid.NewGuid().ToString("N") + ".journal");
try
{
    var journal = SlimeBridge.Shared.OpJournal.Open(journalPath, out var error)!;
    if (journal == null) throw new Exception(error);
    using var binding = new StorageBindingChecks.Scope(reset: false); binding.Tick();
    var beginArgs = new object?[] { command, journal, Environment.ProcessId, null, binding.Guard };
    var pending = begin.Invoke(null, beginArgs);
    if (pending == null || beginArgs[3] != null || director.DispatchCalls != 1) throw new Exception("FAIL: Begin must dispatch native creation once");
    if (!journal.HasAny(command.OpId) || !journal.HasAny("create:" + id)) throw new Exception("FAIL: both durable intents required");
    director.Pending!.MoveNext();
    if (director.loadedGame || director.newGameMetadata == null || director.SavedGame == null ||
        !Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress)
        throw new Exception("FAIL: realistic NEW GAME initial native phase required");
    if (creator.GetMethod("Step")!.Invoke(pending, null) != null || director.SaveCalls != 0)
        throw new Exception("FAIL: no acknowledgement or flush while native creation is progressing");
    Fixture.CompleteNewGame(director);
    var scene = Il2Cpp.SceneContext.Instance!;
    if (director.loadedGame || director.newGameMetadata != null ||
        scene.PlayerState == null || scene.Player?.activeInHierarchy != true ||
        scene.GameModel?.gameSettings != director.Pending.metadata!.gameSettingsModel ||
        Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress ||
        !Il2Cpp.SystemContext.Instance.SceneLoader.IsCurrentSceneGroupGameplay() ||
        director.SavedGame.GetName() != "sb2_" + id || director.SavedGame.GameState!.SaveSlotIndex != 0)
        throw new Exception("FAIL: invalid realistic NEW GAME completed fixture");
    Console.WriteLine("NEW GAME evidence: loaded=False metadataConsumed=True settingsApplied=True gameplay=True busy=False saved=True name/slot/scopedHook=True");
    var completed = (SlimeBridge.Shared.CommandResult?)creator.GetMethod("Step")!.Invoke(pending, null);
    if (completed == null || !completed.Ok || !completed.Applied || !completed.Persisted || completed.Verified != true ||
        completed.SaveId != "sb2_" + id || completed.SaveFile != "sb2_" + id + "_0" || completed.Sha256.Length != 64 || director.SaveCalls != 1)
        throw new Exception("FAIL: realistic completed NEW GAME with loadedGame=false must proceed to one flush/full readback; Step=" + (completed == null ? "null" : completed.Reason));
    creator.GetMethod("Step")!.Invoke(pending, null);
    if (director.SaveCalls != 1) throw new Exception("FAIL: Step repeated native save");
    Console.WriteLine("PASS: journal -> native dispatch -> live world -> one flush -> detached proof (synthetic)");
}
finally { File.Delete(journalPath); }
FocusedChecks.Run();
StorageBindingChecks.Run();
