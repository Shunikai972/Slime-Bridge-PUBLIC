// Authored synthetic test doubles: no SR2 format/code, IL2CPP or game execution.
using System.Reflection;
using System.Text.Json;
using Il2CppMonomiPark.SlimeRancher;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Options;
using Il2CppMonomiPark.SlimeRancher.Persist;

namespace Il2CppInterop.Runtime.InteropTypes
{
    public class Il2CppObjectBase
    {
        private static long _next;
        public IntPtr Pointer { get; set; } = new(Interlocked.Increment(ref _next));
        public IntPtr NativeClass;
        public System.Action? OnClassRead;
        public IntPtr ObjectClass { get { OnClassRead?.Invoke(); return NativeClass; } }
        public T Cast<T>() where T : class => (T)(object)this;
        public virtual T? TryCast<T>() where T : class => this as T;
    }
}
namespace Il2CppInterop.Runtime
{
    public static class Il2CppClassPointerStore<T> { public static IntPtr NativeClassPtr = new(0x1234); }
}
namespace UnityEngine
{
    public sealed class GameObject { public bool activeInHierarchy = true; }
    public static class Time { public static float realtimeSinceStartup; }
}
namespace Il2CppSystem
{
    public sealed class Action
    {
        private readonly System.Action _action;
        public Action(System.Action action) { _action = action; }
        public void Invoke() => _action();
        public static implicit operator Action(System.Action action) => new(action);
    }
}
namespace Il2CppSystem.Collections
{
    public interface IEnumerator { bool MoveNext(); }
}
namespace Il2CppSystem.Collections.Generic
{
    public interface IEnumerable<T> { }
    public sealed class List<T> : System.Collections.Generic.List<T>, IEnumerable<T>
    {
        public int _version;
        public System.Action<int>? OnIndexRead;
        public new T this[int index]
        {
            get { OnIndexRead?.Invoke(index); return base[index]; }
            set { base[index] = value; _version++; }
        }
        public TCast Cast<TCast>() where TCast : class => (TCast)(object)this;
    }
    public sealed class Enumerable<T> : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public readonly List<T> Items = new();
        public bool RejectBoxedEnumerator;
        public bool NativeListAvailable = true;
        public override TCast? TryCast<TCast>() where TCast : class => NativeListAvailable ? Items as TCast : null;
        public Cursor<T> GetEnumerator() => new(Items, RejectBoxedEnumerator);
    }
    public sealed class Cursor<T> : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase, Il2CppSystem.Collections.IEnumerator
    {
        private readonly System.Collections.Generic.List<T> _items;
        private readonly bool _rejectBoxedEnumerator;
        private int _index = -1;
        public Cursor(System.Collections.Generic.List<T> items, bool rejectBoxedEnumerator = false)
        { _items = items; _rejectBoxedEnumerator = rejectBoxedEnumerator; }
        public T Current => _items[_index];
        public bool MoveNext()
        {
            if (_rejectBoxedEnumerator)
                throw new InvalidOperationException("Collection was modified; enumeration operation may not execute (synthetic boxed native enumerator)");
            return ++_index < _items.Count;
        }
    }
}
namespace Il2CppSystem.IO
{
    public sealed class MemoryStream
    {
        public readonly System.IO.MemoryStream Inner = new();
        public long Position { get => Inner.Position; set => Inner.Position = value; }
        public long Length => Inner.Length;
        public byte[] ToArray() => Inner.ToArray();
        public void Dispose(bool disposing) { Inner.Dispose(); Fixture.Disposals++; }
    }
}
namespace HarmonyLib
{
    public static class AccessTools
    {
        public static MethodInfo? Method(Type type, string name, Type[]? arguments = null) =>
            arguments == null ? type.GetMethod(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static) :
            type.GetMethod(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static, null, arguments, null);
    }
    public sealed class HarmonyMethod
    {
        public readonly MethodInfo Method;
        public HarmonyMethod(Type type, string name) { Method = AccessTools.Method(type, name)!; }
    }
    public sealed class Harmony
    {
        public static readonly Dictionary<string, (HarmonyMethod? Prefix, HarmonyMethod? Finalizer)> Hooks = new();
        public static bool FailPatch;
        public Harmony(string id) { }
        public void Patch(MethodInfo original, HarmonyMethod? prefix = null, HarmonyMethod? postfix = null,
            HarmonyMethod? transpiler = null, HarmonyMethod? finalizer = null)
        {
            if (FailPatch) throw new InvalidOperationException("synthetic hook refusal");
            Hooks[original.Name] = (prefix, finalizer);
        }
        public void UnpatchSelf() => Hooks.Clear();
    }
}
namespace Il2CppMonomiPark.SlimeRancher
{
    public sealed class GameIconDefinition : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    { public string ReferenceId = "Fixture.Icon"; }
    public sealed class GameSettingsDirector { public GameIconDefinition? GameIcon = new(); }
}
namespace Il2CppMonomiPark.SlimeRancher.Options
{
    public sealed class OptionsItemDefinition : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    { public string ReferenceId = "Fixture.Option", PersistenceKey = "Fixture.PersistedOption"; public bool IsProfileSetting; public string DefaultValue = "fixture-default"; }
    public sealed class OptionsItemDefinitionList
    { public Il2CppSystem.Collections.Generic.Enumerable<OptionsItemDefinition>? GameBasedDefinitions = new(); }
    public sealed class OptionsDirector { public OptionsItemDefinitionList? _optionsItemDefinitions = new(); }
}
namespace Il2CppMonomiPark.SlimeRancher.DataModel
{
    public sealed class GameModel
    { public GameSettingsModel? gameSettings; }
    public sealed class OptionsItemModel
    { public OptionsItemDefinition? optionsItemDefinition; public string OptionValueKey = ""; }
    public sealed class GameSettingsModel : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public static System.Action<GameSettingsModel>? AfterConstruction;
        public GameIconDefinition? gameIconDefinition;
        public Dictionary<string, OptionsItemModel>? optionsById = new();
        public GameSettingsModel(Il2CppSystem.Collections.Generic.Enumerable<OptionsItemDefinition> items)
        {
            foreach (var item in items.Items) optionsById!.Add(item.ReferenceId, new() { optionsItemDefinition = item, OptionValueKey = item.DefaultValue });
            AfterConstruction?.Invoke(this);
        }
        public void SetGameIconForNewGame(GameIconDefinition icon) => gameIconDefinition = icon;
        public System.Action? OnPull;
        public System.Action<GameSettingsV01>? OnSerialized;
        public void Pull(ref GameSettingsV01 data, PersistenceIdLookupTable<GameIconDefinition> lookup)
        {
            OnPull?.Invoke();
            if (gameIconDefinition == null) throw new InvalidOperationException("fixture missing settings icon");
            var iconIndex = lookup.Items.FindIndex(icon => icon.Pointer == gameIconDefinition.Pointer);
            if (iconIndex < 0) throw new InvalidOperationException("fixture icon absent from native lookup");
            data = new() { GameIconId = iconIndex };
            foreach (var entry in optionsById!) data.OptionItems!.Add(new() { PersistenceKey = entry.Value.optionsItemDefinition!.PersistenceKey, OptionValueKey = entry.Value.OptionValueKey });
            OnSerialized?.Invoke(data);
        }
    }
}
namespace Il2CppMonomiPark.SlimeRancher.Persist
{
    public sealed class PersistenceIdLookupTable<T>
    {
        public readonly System.Collections.Generic.List<T> Items;
        public PersistenceIdLookupTable(Il2CppSystem.Collections.Generic.IEnumerable<T> items)
        { Items = new((Il2CppSystem.Collections.Generic.List<T>)items); }
    }
    public sealed class OptionItemDataV01 { public string PersistenceKey = ""; public string OptionValueKey = ""; }
    public sealed class GameSettingsV01
    { public int GameIconId; public System.Collections.Generic.List<OptionItemDataV01>? OptionItems = new(); }
    public sealed class PersistenceIndex { public string[]? IndexTable = new[] { "Fixture.Icon" }; }
    public sealed class GameV06
    {
        public bool _isLoadSummary;
        public string GameName = "", DisplayName = "";
        public int SaveSlotIndex;
        public object? World = new(), Player = new(), Ranch = new(), Actors = new();
        public GameSettingsV01? GameSettings = new();
        public PersistenceIndex? GameIconIndex = new();
        public void Load(Il2CppSystem.IO.MemoryStream stream, bool skipPayloadEnd)
        {
            Fixture.Loads++; Fixture.SkipPayloadEnd = skipPayloadEnd;
            if (skipPayloadEnd || stream.Position != 0) throw new InvalidOperationException("must fully read from start");
            var data = JsonSerializer.Deserialize<GameV06>(stream.ToArray(), Fixture.Json)!;
            GameName = data.GameName; DisplayName = data.DisplayName; SaveSlotIndex = data.SaveSlotIndex;
            World = data.World; Player = data.Player; Ranch = data.Ranch; Actors = data.Actors;
            GameSettings = data.GameSettings; GameIconIndex = data.GameIconIndex; _isLoadSummary = data._isLoadSummary;
        }
    }
}
namespace Il2Cpp
{
    public sealed class PlayerState { public Ammo? Ammo; public int Health = 100; public int GetCurrHealth() => Health; }
    // LAB wrapper SceneContext : SRSingleton -> SRBehaviour -> MonoBehaviour, native Pointer.
    public sealed class SceneContext : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public static SceneContext? Instance; public PlayerState? PlayerState; public UnityEngine.GameObject? Player;
        private GameModel? _model;
        public System.Action? OnModelRead;
        public GameModel? GameModel { get { OnModelRead?.Invoke(); return _model; } set => _model = value; }
    }
    public sealed class SceneGroup { public string name = "MainMenuFromBoot"; }
    public sealed class SceneLoader
    {
        public bool IsSceneLoadInProgress; public SceneGroup? _currentSceneGroup = new();
        public bool IsCurrentSceneGroupGameplay() => _currentSceneGroup?.name == "FixtureWorld";
    }
    public sealed class SystemContext { public static SystemContext? Instance; public SceneLoader? SceneLoader = new(); }
    public sealed class GameContext
    {
        public static GameContext? Instance;
        public AutoSaveDirector? AutoSaveDirector;
        public LookupDirector? LookupDirector;
        public OptionsDirector? OptionsDirector = new();
        public GameSettingsDirector? GameSettingsDirector = new();
    }
    public sealed class GameData
    {
        public sealed class Summary
        {
            public string Name = "", SaveName = "", DisplayName = "";
            public int SaveSlotIndex;
            public DateTimeOffset SaveTimestamp = DateTimeOffset.UtcNow;
            public bool IsInvalid;
            public GameIconDefinition? IconId = new();
        }
    }
    // Verified LAB wrapper inherits native Pointer.
    public sealed class SavedGame : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    { public GameV06? GameState; public string GetName() => GameState?.GameName ?? ""; }
    public class StorageProvider : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public bool CastUnavailable;
        public FileStorageProvider? CastOverride;
        public override T? TryCast<T>() where T : class => CastUnavailable ? null : (CastOverride ?? this) as T;
        public bool Initialized = true;
        public int NameReads;
        public System.Action? OnNames;
        public readonly System.Collections.Generic.List<string> Names = new();
        public readonly Dictionary<string, byte[]> Payloads = new();
        public bool IsInitialized() => Initialized;
        public bool HasGameData(string name) => Payloads.ContainsKey(name);
        public Il2CppSystem.Collections.Generic.List<string> GetAvailableGames()
        { NameReads++; OnNames?.Invoke(); var copy = new Il2CppSystem.Collections.Generic.List<string>(); copy.AddRange(Names); return copy; }
        public void GetGameData(string name, Il2CppSystem.IO.MemoryStream stream) => stream.Inner.Write(Payloads[name]);
    }
    public class FileStorageProvider : StorageProvider
    {
        public FileStorageProvider() { NativeClass = Il2CppInterop.Runtime.Il2CppClassPointerStore<FileStorageProvider>.NativeClassPtr; }
        public System.Action? OnRootRead, OnInitializedRead;
        public string Root = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "SR2", "Steam", "native-fixture");
        public bool isInitialized { get { OnInitializedRead?.Invoke(); return Initialized; } }
        public string savePath { get { OnRootRead?.Invoke(); return Root; } }
    }
    public sealed class DerivedFileStorageProvider : FileStorageProvider
    { public DerivedFileStorageProvider() { NativeClass = new(0x5678); } }
    public sealed class AutoSaveDirector : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public sealed class LoadNewGameMetadata : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
        { public int saveSlotIndex; public GameSettingsModel? gameSettingsModel; }
        public sealed class _LoadNewGame_Coroutine_d__53 : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
        {
            public int __1__state;
            public AutoSaveDirector? __4__this;
            public LoadNewGameMetadata? metadata;
            public bool MoveNext() => Fixture.Move(this);
        }
        public int SaveSlotCount = 3, DispatchCalls, SaveCalls;
        public bool loadedGame;
        public LoadNewGameMetadata? newGameMetadata;
        public object? ProfileManager = new();
        private StorageProvider? _storage = new FileStorageProvider();
        public System.Action? OnProviderRead, OnPublicProviderRead;
        public StorageProvider? StorageProvider
        { get { OnPublicProviderRead?.Invoke(); return _storage; } set { _storage = value; } }
        public StorageProvider? _StorageProvider_k__BackingField { get { OnProviderRead?.Invoke(); return _storage; } }
        public SavedGame? SavedGame;
        public SavedGame? _savedGame => SavedGame;
        // Independent native +0xC8 field: only existing-save Load populates it.
        // NEWGAME and normal save/flush leave null or stale values unchanged.
        public string? _currentGameName;
        public bool SaveAllNow() => throw new InvalidOperationException("transfer outside counterpart harness");
        public _LoadNewGame_Coroutine_d__53? Pending;
        public readonly Dictionary<string, GameData.Summary> Summaries = new();
        public System.Action? OnDispatch, OnSave;
        public GameData.Summary? LoadSummary(string name) => Summaries.TryGetValue(name, out var row) ? row : null;
        public string GetGameSaveDisplayName(int slot) => (slot + 1).ToString();
        public string GetGameSaveFileName(string displayName)
        {
            var result = "fixture-native-" + displayName;
            if (HarmonyLib.Harmony.Hooks.TryGetValue(nameof(GetGameSaveFileName), out var hook))
            { var args = new object?[] { this, result }; hook.Prefix!.Method.Invoke(null, args); result = (string)args[1]!; }
            return result;
        }
        public void LoadNewGame(LoadNewGameMetadata metadata, Il2CppSystem.Action onError)
        { DispatchCalls++; Pending = new() { __1__state = 0, __4__this = this, metadata = metadata }; OnDispatch?.Invoke(); }
        public void SaveGameAndFlush()
        {
            SaveCalls++;
            if (Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress)
                throw new InvalidOperationException("fixture flush before scene idle");
            var model = Il2Cpp.SceneContext.Instance?.GameModel?.gameSettings
                ?? throw new InvalidOperationException("fixture flush without applied settings");
            var icons = new Il2CppSystem.Collections.Generic.List<GameIconDefinition> { model.gameIconDefinition! };
            var serialized = new GameSettingsV01(); model.Pull(ref serialized, new(icons));
            // Native SavedGame.Pull allocates a new nested GameV06 but retains SavedGame.
            var previous = SavedGame!.GameState!;
            SavedGame.GameState = new()
            {
                GameName = previous.GameName, DisplayName = previous.DisplayName, SaveSlotIndex = previous.SaveSlotIndex,
                World = previous.World, Player = previous.Player, Ranch = previous.Ranch, Actors = previous.Actors,
                GameSettings = serialized, GameIconIndex = new() { IndexTable = new[] { icons[0].ReferenceId } }
            };
            OnSave?.Invoke();
            if (SavedGame?.GameState != null) Fixture.Add(this, SavedGame.GameState, SavedGame.GetName() + "_0");
        }
    }
}
internal static class Fixture
{
    public static readonly JsonSerializerOptions Json = new() { IncludeFields = true };
    public static int Loads, Disposals;
    public static bool? SkipPayloadEnd;
    public static System.Action? WithinMove;
    public static Il2Cpp.AutoSaveDirector Reset()
    {
        var type = Assembly.GetExecutingAssembly().GetType("SlimeBridge.SR2.CounterpartCreator")!;
        foreach (var name in new[] { "_association", "_scope", "_harmony", "_completedWorld" }) type.GetField(name, BindingFlags.NonPublic | BindingFlags.Static)?.SetValue(null, null);
        HarmonyLib.Harmony.Hooks.Clear(); HarmonyLib.Harmony.FailPatch = false;
        GameSettingsModel.AfterConstruction = null; Loads = Disposals = 0; SkipPayloadEnd = null; WithinMove = null;
        UnityEngine.Time.realtimeSinceStartup = 0;
        Il2CppInterop.Runtime.Il2CppClassPointerStore<Il2Cpp.FileStorageProvider>.NativeClassPtr = new(0x1234);
        var director = new Il2Cpp.AutoSaveDirector();
        Il2Cpp.GameContext.Instance = new() { AutoSaveDirector = director };
        Il2Cpp.GameContext.Instance.OptionsDirector!._optionsItemDefinitions!.GameBasedDefinitions!.Items.Add(new());
        Il2Cpp.GameContext.Instance.OptionsDirector._optionsItemDefinitions.GameBasedDefinitions.Items.Add(new() { ReferenceId = "Fixture.Second", PersistenceKey = "Fixture.PersistedSecond" });
        Il2Cpp.SystemContext.Instance = new(); Il2Cpp.SceneContext.Instance = null;
        return director;
    }
    public static bool Move(Il2Cpp.AutoSaveDirector._LoadNewGame_Coroutine_d__53 iterator)
    {
        var hook = HarmonyLib.Harmony.Hooks[nameof(iterator.MoveNext)];
        var args = new object?[] { iterator, null, false };
        var run = (bool)hook.Prefix!.Method.Invoke(null, args)!;
        if (!run) { hook.Finalizer!.Method.Invoke(null, new[] { args[1], null }); return (bool)args[2]!; }
        Exception? error = null;
        try
        {
            WithinMove?.Invoke();
            var director = iterator.__4__this!; var metadata = iterator.metadata!;
            director.loadedGame = false; director.newGameMetadata = metadata;
            var display = director.GetGameSaveDisplayName(metadata.saveSlotIndex);
            var name = director.GetGameSaveFileName(display);
            if (director.StorageProvider!.HasGameData(name)) name += "_1";
            var table = new Il2CppSystem.Collections.Generic.List<GameIconDefinition> { metadata.gameSettingsModel!.gameIconDefinition! };
            var settings = new GameSettingsV01(); metadata.gameSettingsModel.Pull(ref settings, new(table));
            director.SavedGame = new() { GameState = new() { GameName = name, DisplayName = display,
                SaveSlotIndex = metadata.saveSlotIndex, GameSettings = settings,
                GameIconIndex = new() { IndexTable = new[] { table[0].ReferenceId } } } };
            iterator.__1__state = 1;
            Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = true;
            return true;
        }
        catch (Exception ex) { error = ex; throw; }
        finally { hook.Finalizer!.Method.Invoke(null, new[] { args[1], error }); }
    }
    // Separate native load phases: settings are applied and metadata consumed only at completion.
    public static void ActivateScene()
    {
        Il2Cpp.SceneContext.Instance = new() { Player = new(), PlayerState = new(), GameModel = new() };
        Il2Cpp.SystemContext.Instance!.SceneLoader!._currentSceneGroup!.name = "FixtureWorld";
    }
    public static void ApplySettings(Il2Cpp.AutoSaveDirector director)
    { Il2Cpp.SceneContext.Instance!.GameModel!.gameSettings = director.newGameMetadata!.gameSettingsModel; }
    public static void CompleteNewGame(Il2Cpp.AutoSaveDirector director)
    {
        ActivateScene(); ApplySettings(director);
        director.newGameMetadata = null;
        Il2Cpp.SystemContext.Instance!.SceneLoader!.IsSceneLoadInProgress = false;
    }
    public static void Add(Il2Cpp.AutoSaveDirector director, GameV06 game, string file)
    {
        director.StorageProvider!.Names.Add(file);
        director.StorageProvider.Payloads[file] = JsonSerializer.SerializeToUtf8Bytes(game, Json);
        director.Summaries[file] = new() { Name = game.GameName, SaveName = file, DisplayName = game.DisplayName,
            SaveSlotIndex = game.SaveSlotIndex, IconId = new() { ReferenceId = game.GameIconIndex!.IndexTable![game.GameSettings!.GameIconId] } };
    }
}
