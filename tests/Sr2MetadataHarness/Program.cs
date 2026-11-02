using System;
using SlimeBridge.SR2;
using Il2CppMonomiPark.SlimeRancher;

static class Program
{
    static int Main(string[] args)
    {
        // Report test failures without opening the platform's unhandled-exception crash reporter.
        try { Run(args); return 0; }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }

    static void Run(string[] args)
    {
        var actual = new GameMetadata { GameName="20261102034742_1", SlotIndex=0 };
        var native = new Il2CppSystem.Nullable<GameMetadata> { ForbidLayoutReads=true };
        var director = new AutoSaveDirector { Metadata=native };
        int expectedHasReads=0, expectedValueReads=0;
        switch(args[0])
        {
            case "boxed-false": native.Boxed=actual; native.Has=false; break;
            case "boxed-true": native.Boxed=actual; native.Has=true; native.Stored=new GameMetadata { GameName="wrong-layout" }; break;
            case "empty": native.ForbidLayoutReads=false; native.Has=false; expectedHasReads=1; actual=null!; break;
            case "genuine-nullable": native.ForbidLayoutReads=false; native.Has=true; native.Stored=actual; expectedHasReads=1; expectedValueReads=1; break;
            case "getter-empty": director.Throws=true; actual=null!; break;
            case "null-wrapper": director.Metadata=null; actual=null!; break;
            case "unexpected-box": native.Boxed=new Il2CppSystem.Object(); native.Has=true; native.Stored=new GameMetadata { GameName="must-not-read" }; actual=null!; break;
            case "assignable-but-not-exact": native.AssignmentOnly=true; native.Has=true; native.Stored=new GameMetadata { GameName="unsafe-layout" }; actual=null!; break;
            case "uninitialized-nullable-type": Il2CppInterop.Runtime.Il2CppClassPointerStore<Il2CppSystem.Nullable<GameMetadata>>.NativeClassPtr=IntPtr.Zero; native.Has=true; native.Stored=new GameMetadata { GameName="type-not-ready" }; actual=null!; break;
            // Adversarial guard isolation only: no claim that a real live IL2CPP object has a zero class pointer.
            case "zero-type-and-object": Il2CppInterop.Runtime.Il2CppClassPointerStore<Il2CppSystem.Nullable<GameMetadata>>.NativeClassPtr=IntPtr.Zero; native.ClassOverride=IntPtr.Zero; native.Has=true; native.Stored=new GameMetadata { GameName="synthetic-zero-class" }; actual=null!; break;
            default: throw new Exception("Unknown test case");
        }
        var read=director.CurrentGame();
        if (!ReferenceEquals(read,actual)) throw new Exception("Wrong metadata identity for "+args[0]+": "+read?.GameName);
        if (native.HasReads!=expectedHasReads || native.ValueReads!=expectedValueReads)
            throw new Exception("Wrong Nullable layout access count for "+args[0]+": HasValue="+native.HasReads+", Value="+native.ValueReads);
        Console.WriteLine("PASS "+args[0]);
    }
}
namespace Il2CppSystem
{
    public class Object
    {
        public virtual IntPtr ObjectClass => new IntPtr(3);
        public virtual T? TryCast<T>() where T:class => this as T;
        public T Cast<T>() where T:class => (this as T)!;
    }
    // Models actual observed Unity boxing: the managed wrapper can point at a boxed underlying T.
    public class Nullable<T>:Object where T:class
    {
        public Object? Boxed; public T? Stored; public bool Has, AssignmentOnly, ForbidLayoutReads; public int HasReads, ValueReads;
        public IntPtr? ClassOverride;
        public override IntPtr ObjectClass => ClassOverride ?? Boxed?.ObjectClass ?? new IntPtr(AssignmentOnly?99:1);
        public bool HasValue { get { HasReads++; if (ForbidLayoutReads) throw new InvalidOperationException("Unsafe HasValue layout read"); return Has; } }
        public T Value { get { ValueReads++; if (ForbidLayoutReads) throw new InvalidOperationException("Unsafe Value layout read"); return Stored!; } }
        public override U? TryCast<U>() where U:class => (Boxed??this) as U;
    }
}
namespace Il2CppInterop.Runtime
{
    public static class Il2CppClassPointerStore<T> { public static IntPtr NativeClassPtr=new IntPtr(1); }
}
namespace Il2CppSystem.Collections { public interface IEnumerator { bool MoveNext(); } }
namespace Il2CppSystem.Collections.Generic
{
    public interface IEnumerable<T> { }
    public class List<T>:Il2CppSystem.Object { public void Add(T item) { } public int Count=>0; }
}
namespace Il2CppMonomiPark.SlimeRancher
{
    public class GameMetadata:Il2CppSystem.Object { public string? GameName; public int SlotIndex; }
    public class GameSaveIdentifier { public GameSaveIdentifier(string name,string save) { } }
    public class AutoSaveDirector
    {
        public Il2CppSystem.Nullable<GameMetadata>? Metadata; public bool Throws;
        public Il2CppSystem.Nullable<GameMetadata>? _currentGameMetadata => Throws?throw new NullReferenceException():Metadata;
        public Il2Cpp.StorageProvider? _storageProvider; public object? _profile; public Configuration? _configuration;
        public Translation? _saveReferenceTranslation;
        public void BeginLoad(GameSaveIdentifier id,object? callback) { }
    }
    public class Configuration { public int SaveSlotCount; public IdentifiableGroup? IdentifiableTypes; }
    public class Translation { public T? TryCast<T>() where T:class=>null; }
    public class SaveReferenceTranslation { public void OnIdentifiableTypesListChanged(Il2CppSystem.Collections.Generic.IEnumerable<Il2Cpp.IdentifiableType> all) { } }
    public class IdentifiableGroup { public MemberList GetAllMembers()=>new(); }
    public class MemberList { public Cursor GetEnumerator()=>new(); }
    public class Cursor:Il2CppSystem.Object,Il2CppSystem.Collections.IEnumerator { public bool MoveNext()=>false; public Il2Cpp.IdentifiableType Current=>new(); }
}
namespace Il2CppMonomiPark.SlimeRancher.Cutscene { public class CutsceneDirector { public static CutsceneDirector? Instance; } }
namespace Il2CppMonomiPark.SlimeRancher.Economy { public interface ICurrency { } }
namespace Il2Cpp
{
    public class StorageProvider { }
    public class IdentifiableType { }
    public class GameContext { public static GameContext? Instance; public Lookup? LookupDirector; }
    public class Lookup { public CurrencyList? CurrencyList; }
    public class CurrencyList { public Currency? GetDefaultCurrency()=>null; }
    public class Currency:Il2CppSystem.Object { }
    public class PlayerState { public int GetCurrency(Il2CppMonomiPark.SlimeRancher.Economy.ICurrency currency)=>0; public void SpendCurrency(Il2CppMonomiPark.SlimeRancher.Economy.ICurrency currency,int n,object? data) { } }
}
namespace Il2CppMonomiPark.SlimeRancher.Player
{
    public class AmmoSlot { public class AmmoMetadata { public AmmoMetadata(Il2Cpp.IdentifiableType type) { } } }
    public class AmmoSlotManager
    {
        public bool CouldAddToAnySlot(AmmoSlot.AmmoMetadata data)=>false;
        public bool MaybeAddToSpecificSlot(AmmoSlot.AmmoMetadata data,int slot,int count,bool flag)=>false;
    }
}
