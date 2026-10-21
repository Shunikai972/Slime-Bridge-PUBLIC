// SYNTHETIC NATIVE API FIXTURES ONLY. These are authored test doubles, not game code.
// This executable links the real InventoryAgent and shared rules, but cannot prove
// IL2CPP runtime behavior or native save compatibility. The payload format below
// is deliberately NOT SR2's format. Scratch files are synthetic, never user saves.
using System.Text;
using Il2CppInterop.Runtime.InteropTypes.Arrays;

namespace SlimeBridge.SR2
{
    internal static class BridgeMod
    {
        internal const string PinkSlimeRef = "SlimeDefinition.Pink";
        internal const string PinkPlortRef = "IdentifiableType.PinkPlort";
    }
}
namespace UnityEngine
{
    public sealed class GameObject { }
    public static class Time { public static float realtimeSinceStartup => 0; }
}
namespace Il2CppInterop.Runtime.InteropTypes
{
    public class Il2CppObjectBase
    {
        private static long _next;
        public IntPtr Pointer { get; } = new IntPtr(Interlocked.Increment(ref _next));
        public T? TryCast<T>() where T : class => this as T;
        public virtual T Cast<T>() where T : class => this as T ?? throw new InvalidCastException();
    }
}
namespace Il2CppInterop.Runtime.InteropTypes.Arrays
{
    public sealed class Il2CppStructArray<T>
    {
        internal readonly T[] Items;
        public Il2CppStructArray(T[] items) { Items = items; }
        public static implicit operator T[](Il2CppStructArray<T> array) => (T[])array.Items.Clone();
        public static implicit operator Il2CppStructArray<T>(T[] array) => new((T[])array.Clone());
        public int Length => Items.Length;
    }
    public sealed class Il2CppStringArray
    {
        internal readonly string[] Items;
        public Il2CppStringArray(string[] items) { Items = items; }
        public int Length => Items.Length;
        public string this[int i] => Items[i];
    }
    public sealed class Il2CppReferenceArray<T>
    {
        internal readonly T?[] Items;
        public Il2CppReferenceArray(T?[] items) { Items = items; }
        public int Length => Items.Length;
        public T? this[int i] { get => Items[i]; set => Items[i] = value; }
    }
}
namespace Il2CppSystem.Collections.Generic
{
    public class List<T> : System.Collections.Generic.List<T> { }
    public class IReadOnlyDictionary<K, V> where K : notnull
    {
        internal readonly System.Collections.Generic.Dictionary<K, V> Items;
        internal IReadOnlyDictionary(System.Collections.Generic.Dictionary<K, V> items) { Items = items; }
    }
    public sealed class Dictionary<K, V> : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase where K : notnull
    {
        internal readonly System.Collections.Generic.Dictionary<K, V> Items = new();
        public V this[K key] { get => Items[key]; set => Items[key] = value; }
        public bool TryGetValue(K key, out V value) => Items.TryGetValue(key, out value!);
        public override T Cast<T>() where T : class
        {
            FixtureTrace.CheckThread(); FixtureTrace.LookupCasts++;
            if (typeof(T) == typeof(IReadOnlyDictionary<K, V>)) return (T)(object)new IReadOnlyDictionary<K, V>(Items);
            return base.Cast<T>();
        }
    }
}
namespace Il2CppSystem.IO
{
    public sealed class MemoryStream : IDisposable
    {
        internal readonly System.IO.MemoryStream Inner;
        public MemoryStream() { Inner = new(); }
        public MemoryStream(Il2CppStructArray<byte> bytes, bool writable) { Inner = new(bytes, writable); }
        public long Position { get => Inner.Position; set => Inner.Position = value; }
        public long Length => Inner.Length;
        public Il2CppStructArray<byte> ToArray() => Inner.ToArray();
        public void Dispose() { Inner.Dispose(); FixtureTrace.Disposals++; }
    }
}
internal static class FixtureTrace
{
    public static readonly int MainThread = Environment.CurrentManagedThreadId;
    public static int LookupCasts, Loads, Disposals;
    public static bool? SkipPayloadEnd;
    public static void CheckThread()
    {
        if (Environment.CurrentManagedThreadId != MainThread) throw new InvalidOperationException("native API used off main thread");
    }
    public static void Reset() { LookupCasts = Loads = Disposals = 0; SkipPayloadEnd = null; }
}
namespace Il2Cpp
{
    public sealed class IdentifiableType : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public string ReferenceId { get; }
        public IdentifiableType(string referenceId) { ReferenceId = referenceId; }
    }
    public sealed class Ammo : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public sealed class Slot
        {
            public IdentifiableType? Id;
            public int Count;
            public bool IsUnlocked = true, Accepts = true;
            public int Capacity = 10;
        }
        public Il2CppReferenceArray<Slot> Slots = new(Array.Empty<Slot?>());
        public readonly System.Collections.Generic.List<(int Index, int Amount)> Mutations = new();
        public readonly System.Collections.Generic.List<int> CapacityChecks = new(), AcceptanceChecks = new();
        public int RefuseMutationAt = -1, ThrowBeforeMutationAt = -1, ThrowAfterMutationAt = -1;
        public bool WrongSlotDelta, ChangeOtherSlot;
        public Action? AfterMutation;
        public int GetSlotCount(int index) { FixtureTrace.CheckThread(); return Slots[index]!.Count; }
        public int GetSlotMaxCount(IdentifiableType id, int index)
        { FixtureTrace.CheckThread(); CapacityChecks.Add(index); return Slots[index]!.Capacity; }
        public bool CouldAddToSlot(IdentifiableType id) => Enumerable.Range(0, Slots.Length).Any(i => CouldAddToSlot(id, i, false));
        public bool CouldAddToSlot(IdentifiableType id, int index, bool overflow)
        {
            FixtureTrace.CheckThread(); AcceptanceChecks.Add(index);
            if (overflow) throw new InvalidOperationException("overflow must never be requested");
            var slot = Slots[index];
            return slot != null && slot.IsUnlocked && slot.Accepts && (slot.Count == 0 || slot.Id?.Pointer == id.Pointer) && slot.Count < slot.Capacity;
        }
        public void Decrement(int index, int count)
        {
            if (!Mutate(index, count, false, null)) return;
        }
        public bool MaybeAddToSpecificSlot(IdentifiableType id, object? emotion, int index, int count, bool overflow)
        {
            if (overflow) throw new InvalidOperationException("overflow must never be requested");
            return Mutate(index, count, true, id);
        }
        private bool Mutate(int index, int count, bool adding, IdentifiableType? id)
        {
            FixtureTrace.CheckThread();
            var number = Mutations.Count;
            Mutations.Add((index, count));
            if (number == ThrowBeforeMutationAt) throw new IOException("synthetic pre-mutation exception");
            if (number == RefuseMutationAt) return false;
            var slot = Slots[index]!;
            if (!slot.IsUnlocked || (adding && (!slot.Accepts || slot.Count + count > slot.Capacity))) return false;
            slot.Count += (adding ? count : -count) + (WrongSlotDelta ? 1 : 0);
            if (adding) slot.Id = id;
            if (ChangeOtherSlot && Slots.Length > 1) Slots[(index + 1) % Slots.Length]!.Count++;
            AfterMutation?.Invoke();
            if (number == ThrowAfterMutationAt) throw new IOException("synthetic post-mutation exception");
            return true;
        }
    }
    public sealed class PlayerState
    {
        public Ammo? Ammo;
        public int Health = 100;
        public int GetCurrHealth() { FixtureTrace.CheckThread(); return Health; }
    }
    public sealed class SceneContext
    {
        public static SceneContext? Instance;
        public UnityEngine.GameObject? Player = new();
        public PlayerState? PlayerState;
    }
    public sealed class GameContext
    {
        public static GameContext? Instance;
        public AutoSaveDirector? AutoSaveDirector;
        public LookupDirector? LookupDirector;
    }
    public sealed class LookupDirector
    {
        public Il2CppSystem.Collections.Generic.Dictionary<string, IdentifiableType>? _identifiableTypeByRefId = new();
        public bool TryFindIdentifiableTypeByReferenceId(string referenceId, out IdentifiableType? type)
        {
            FixtureTrace.CheckThread(); type = null;
            return _identifiableTypeByRefId != null && _identifiableTypeByRefId.TryGetValue(referenceId, out type);
        }
    }
    public sealed class SavedGame
    {
        public string Name = "fixture_save";
        public string GetName() { FixtureTrace.CheckThread(); return Name; }
    }
    public sealed class AutoSaveDirector : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public bool loadedGame = true;
        public string? _currentGameName = "fixture_save";
        public SavedGame? _savedGame = new();
        public SavedGame? SavedGame => _savedGame;
        public StorageProvider? StorageProvider;
        public Func<bool>? OnSave;
        public int SaveCalls;
        public bool SaveAllNow() { FixtureTrace.CheckThread(); SaveCalls++; return OnSave?.Invoke() ?? true; }
    }
    public class StorageProvider : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    {
        public bool Initialized = true;
        public int NameReads, PayloadReads;
        public Action? OnRead, OnNames;
        public readonly System.Collections.Generic.List<string> Names = new();
        public readonly System.Collections.Generic.Dictionary<string, byte[]> Payloads = new(StringComparer.Ordinal);
        public bool IsInitialized() { FixtureTrace.CheckThread(); return Initialized; }
        public Il2CppSystem.Collections.Generic.List<string> GetAvailableGames()
        {
            FixtureTrace.CheckThread(); NameReads++; OnNames?.Invoke();
            var names = new Il2CppSystem.Collections.Generic.List<string>(); names.AddRange(Names); return names;
        }
        public void GetGameData(string revision, Il2CppSystem.IO.MemoryStream destination)
        {
            FixtureTrace.CheckThread(); PayloadReads++; OnRead?.Invoke();
            var bytes = Payloads[revision]; destination.Inner.Write(bytes); // deliberately leaves position at EOF
        }
    }
    public sealed class FileStorageProvider : StorageProvider
    {
        public string savePath;
        public string? PathOverride;
        public readonly System.Collections.Generic.List<string> PathRequests = new();
        public FileStorageProvider(string path) { savePath = path; }
        public string GetFullFilePath(string name)
        { FixtureTrace.CheckThread(); PathRequests.Add(name); return PathOverride ?? Path.Combine(savePath, name + ".sav"); }
        public void AddSyntheticRevision(string name, byte[] bytes, byte[]? physicalBytes = null)
        { Names.Add(name); Payloads[name] = bytes; File.WriteAllBytes(Path.Combine(savePath, name + ".sav"), physicalBytes ?? bytes); }
    }
}
namespace Il2CppMonomiPark.SlimeRancher
{
    public sealed class IdentifiableTypePersistenceIdReverseLookupTable
    {
        private readonly Il2CppStringArray _index;
        private readonly Il2CppSystem.Collections.Generic.IReadOnlyDictionary<string, Il2Cpp.IdentifiableType> _lookup;
        public IdentifiableTypePersistenceIdReverseLookupTable(Il2CppStringArray index, Il2CppSystem.Collections.Generic.IReadOnlyDictionary<string, Il2Cpp.IdentifiableType> lookup)
        { _index = index; _lookup = lookup; }
        public Il2Cpp.IdentifiableType? GetIdentifiableType(int id)
        {
            FixtureTrace.CheckThread();
            return id >= 0 && id < _index.Length && _lookup.Items.TryGetValue(_index[id], out var type) ? type : null;
        }
    }
}
namespace Il2CppMonomiPark.SlimeRancher.Persist
{
    public sealed class AmmoDataV01 { public int ID, Count; }
    public sealed class PlayerV05 { public Il2CppSystem.Collections.Generic.List<AmmoDataV01?>? Ammo; }
    public sealed class IdentifiableTypeIndexTableV01 { public Il2CppStringArray? IndexTable; }
    public sealed class GameV06
    {
        public string GameName = "";
        public PlayerV05? Player;
        public IdentifiableTypeIndexTableV01? IdentTypeIndex;
        public void Load(Il2CppSystem.IO.MemoryStream stream, bool skipPayloadEnd = false)
        {
            FixtureTrace.CheckThread(); FixtureTrace.Loads++; FixtureTrace.SkipPayloadEnd = skipPayloadEnd;
            using var reader = new BinaryReader(stream.Inner, Encoding.UTF8, true);
            if (reader.ReadString() != "SYNTHETIC_SR2_ADAPTER_FIXTURE") throw new InvalidDataException("not a synthetic fixture");
            GameName = reader.ReadString();
            if (reader.ReadBoolean())
            {
                IdentTypeIndex = new();
                if (reader.ReadBoolean())
                {
                    var index = new string[reader.ReadInt32()]; for (var i = 0; i < index.Length; i++) index[i] = reader.ReadString();
                    IdentTypeIndex.IndexTable = new(index);
                }
            }
            if (reader.ReadBoolean())
            {
                Player = new();
                if (reader.ReadBoolean())
                {
                    Player.Ammo = new(); var count = reader.ReadInt32();
                    for (var i = 0; i < count; i++) Player.Ammo.Add(reader.ReadBoolean() ? new() { ID = reader.ReadInt32(), Count = reader.ReadInt32() } : null);
                }
            }
            if (!skipPayloadEnd && reader.ReadString() != "SYNTHETIC_END") throw new InvalidDataException("synthetic end marker missing");
        }
    }
}
