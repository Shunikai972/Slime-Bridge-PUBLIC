using System.Reflection;
using SlimeBridge.SR1;

// Synthetic engine boundary: real production agent/creator/skipper remain linked, no game DLLs loaded.
namespace UnityEngine
{
    public class Object
    {
        internal bool Dead; private static int next; private readonly int id = ++next;
        public string name = "synthetic";
        public int GetInstanceID() => id;
        public static bool operator ==(Object? a, Object? b) => ReferenceEquals(a,b) ||
            ((ReferenceEquals(a,null) || a.Dead) && (ReferenceEquals(b,null) || b.Dead));
        public static bool operator !=(Object? a, Object? b) => !(a == b);
        public override bool Equals(object? o) => ReferenceEquals(this,o);
        public override int GetHashCode() => id;
    }
    public class Component : Object
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public T? GetComponent<T>() where T : Component => gameObject.GetComponent<T>();
    }
    public class Behaviour : Component { public bool enabled = true; public bool isActiveAndEnabled => enabled && gameObject.activeInHierarchy; }
    public class MonoBehaviour : Behaviour { }
    public class CanvasGroup : Behaviour { }
    public class Transform : Component
    {
        public Transform? parent;
        public bool IsChildOf(Transform other) => this == other || (parent != null && parent.IsChildOf(other));
    }
    public class GameObject : Object
    {
        public bool activeInHierarchy = true;
        public SceneManagement.Scene scene = Fixture.Scene;
        public readonly Transform transform;
        private readonly List<Component> components = new();
        public readonly List<GameObject> Children = new();
        public GameObject() { transform = new Transform { gameObject = this }; }
        public T Add<T>() where T : Component,new() { var c = new T {gameObject=this}; components.Add(c); return c; }
        public T? GetComponent<T>() where T : Component => GetComponents<T>().FirstOrDefault();
        public T[] GetComponents<T>() where T : Component => components.OfType<T>().ToArray();
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : Component => GetComponents<T>().Concat(Children.SelectMany(c=>c.GetComponentsInChildren<T>(includeInactive))).ToArray();
        public GameObject Child() { var c = new GameObject(); c.transform.parent=transform; Children.Add(c); return c; }
        internal void DestroyDeferred()
        {
            Fixture.Deferred.Add(()=> {
                foreach(var i in GetComponents<IntroUI>()) i.OnDisable();
                foreach(var locker in GetComponents<UIInputLocker>()) locker.OnDisable();
                foreach(var mode in GetComponents<SetInputMode>()) mode.OnDisable();
                Dead=true; foreach(var c in components) c.Dead=true;
            });
        }
    }
    public static class Time { public static float realtimeSinceStartup; public static int frameCount; public static float timeScale = 1; }
    public enum RuntimePlatform { WindowsPlayer,WindowsEditor,LinuxPlayer }
    public static class Application { public static bool isFocused; public static RuntimePlatform platform = RuntimePlatform.WindowsPlayer; }
    public static class Debug { public static void Log(object? value) { Console.Error.WriteLine(value); } }
}
namespace UnityEngine.SceneManagement
{
    public struct Scene : IEquatable<Scene>
    {
        public int handle; public string name; public bool isLoaded;
        public bool IsValid() => handle > 0;
        public bool Equals(Scene o) => handle == o.handle;
        public override bool Equals(object? o) => o is Scene s && Equals(s);
        public override int GetHashCode() => handle;
        public static bool operator ==(Scene a,Scene b) => a.Equals(b);
        public static bool operator !=(Scene a,Scene b) => !a.Equals(b);
    }
    public static class SceneManager { public static Scene GetActiveScene() => Fixture.Scene; }
}
namespace DG.Tweening
{
    public delegate void TweenCallback();
    public class Tween { public TweenCallback? onComplete; public bool Active=true,Done; public bool Throw; public int Completes; }
    public sealed class Sequence : Tween { }
    public static class TweenExtensions
    {
        public static bool IsActive(this Tween t) => t.Active;
        public static bool IsComplete(this Tween t) => t.Done;
        public static void Complete(this Tween t,bool withCallbacks)
        {
            ++t.Completes; ++Fixture.TotalComplete;
            if(t.Throw) throw new InvalidOperationException("synthetic callback failure");
            t.Done=true; t.Active=false;
            // Exact pinned DOTween semantic: update-loop completion ignores original OnComplete.
            if(withCallbacks && !Core.TweenManager.isUpdateLoop) t.onComplete?.Invoke();
        }
    }
    public static class DOTween
    {
        public static readonly List<Tween> Playing = new(),Paused = new();
        public static Action? OnEnumerate;
        public static List<Tween>? PlayingTweens(List<Tween>? fillableList=null) { OnEnumerate?.Invoke(); return Playing.Count==0 ? null : new(Playing); }
        public static List<Tween>? PausedTweens(List<Tween>? fillableList=null) => Paused.Count==0 ? null : new(Paused);
    }
}
namespace DG.Tweening.Core { public static class TweenManager { public static bool isUpdateLoop; } }
namespace HarmonyLib
{
    public sealed class HarmonyMethod { public MethodInfo Method; public HarmonyMethod(Type t,string name) {Method=t.GetMethod(name,BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static)!;} }
    public sealed class Harmony
    {
        public Harmony(string id) { }
        public void Patch(MethodBase original,HarmonyMethod? prefix=null,HarmonyMethod? postfix=null)
        {
            if(prefix!=null) Fixture.NamePrefix=prefix.Method;
            if(postfix!=null) Fixture.MonitorPostfix=postfix.Method;
        }
    }
    public static class AccessTools
    {
        public static MethodInfo? Method(Type t,string n,Type[]? args=null) => t.GetMethod(n,BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance|BindingFlags.Static,null,args??Type.EmptyTypes,null);
    }
}
public static class SRSingleton<T> { public static T Instance = default!; }
public static class Levels { public static bool Menu = true; public static bool Special; public static bool isMainMenu()=>Menu; public static bool isSpecial()=>Special; }
public class GameContext { public AutoSaveDirector AutoSaveDirector = new(); }
public class SceneContext : UnityEngine.MonoBehaviour
{
    public UnityEngine.GameObject Player = new(); public PlayerState PlayerState = new();
    public GameModeConfig GameModeConfig = new(); public TutorialDirector TutorialDirector = new();
}
public class TutorialDirector { public int Suppressed; public void UnsuppressTutorials()=>--Suppressed; }
public class GameModeConfig { public GameModeSettings Settings = new(); public GameModeSettings GetModeSettings()=>Settings; }
public class GameModeSettings { public UnityEngine.GameObject newGamePrefab = new(); }
public class Identifiable { public enum Id { NONE,PINK_SLIME=3,PINK_PLORT=6 } }
public class PlayerState
{
    public enum AmmoMode { DEFAULT } public enum GameMode { CLASSIC }
    public Ammo GetAmmo(AmmoMode mode)=>new(); public int GetCurrHealth()=>100;
}
public class Ammo
{
    public int GetUsableSlotCount()=>4; public int GetSlotCount(int i)=>0;
    public Identifiable.Id GetSlotName(int i)=>Identifiable.Id.NONE;
    public int GetSlotMaxCount(Identifiable.Id id,int i)=>30;
    public bool CouldAddToSlot(Identifiable.Id id,int i,bool b)=>true;
    public void Decrement(int i,int amount) { }
    public bool MaybeAddToSpecificSlot(Identifiable.Id id,object? obj,int i,int amount,bool b)=>true;
}
namespace MonomiPark.SlimeRancher
{
    public class SavedGame { public string Name=""; public string GetName()=>Name; }
}
public class StorageProvider
{
    public readonly Dictionary<string,byte[]> Files=new();
    public bool IsInitialized()=>true;
    public IEnumerable<string> GetAvailableGames()=>Files.Keys;
    public void GetGameData(string file,Stream s) { var b=Files[file]; s.Write(b,0,b.Length); }
}
public class FileStorageProvider : StorageProvider
{
    // Production resolves the exact on-disk path through this private native API by reflection.
    private string GetFullFilePath(string name)=>System.IO.Path.Combine("native",name+".sav");
}
public class AutoSaveDirector : UnityEngine.MonoBehaviour
{
    public MonomiPark.SlimeRancher.SavedGame SavedGame = new();
    public StorageProvider StorageProvider = new();
    public bool Loading,Loaded; public Action? Error; public int Creates,Saves;
    public bool ThrowCreate,ErrorDuringCreate;
    public Action? BeforeCreate;
    public MonomiPark.SlimeRancher.Persist.Summary LoadSummary(string saveName) =>
        new() { name=SavedGame.Name, saveName=saveName, saveTimestamp=DateTimeOffset.UnixEpoch };
    public bool IsLoadingGame()=>Loading; public bool IsNewGame()=>!Loaded;
    private string GetGameSaveFileName(string displayName)=>"unowned";
    public void LoadNewGame(string displayName,Identifiable.Id icon,PlayerState.GameMode mode,Action onError)
    {
        BeforeCreate?.Invoke();
        ++Creates; Error=onError; Loading=true; Loaded=false;
        if(ThrowCreate) throw new InvalidOperationException("synthetic LoadNewGame uncertainty");
        if(ErrorDuringCreate) onError();
        var args=new object?[]{this,displayName,"unowned"};
        if(Fixture.NamePrefix!=null) Fixture.NamePrefix.Invoke(null,args);
        SavedGame.Name=(string)args[2]!;
    }
    public bool SaveAllNow()
    {
        ++Saves; if(Fixture.SaveFails) return false;
        StorageProvider.Files[SavedGame.Name+"_0"] = System.Text.Encoding.UTF8.GetBytes(SavedGame.Name);
        return true;
    }
    public Action<Destroyer.Metadata> NativeMonitorCallback(SceneContext ctx)
    {
        // Compiler-generated fixture closure faithfully owns ctx and this; no permissive name matching.
        Action other = () => _ = SavedGame;
        Action<Destroyer.Metadata> result = metadata => ctx.TutorialDirector.UnsuppressTutorials();
        GC.KeepAlive(other); return result;
    }
}
public class BaseUI : UnityEngine.MonoBehaviour { public virtual void Close() => Destroyer.Destroy(gameObject,"BaseUI.Close"); }
public class IntroUI : BaseUI
{
    public UnityEngine.GameObject background = null!;
    public UnityEngine.CanvasGroup introLine1=null!,introLine2=null!,introLine3=null!;
    private UnityEngine.CanvasGroup introCanvasGroup = null!;
    private bool endReached;
    public DG.Tweening.Sequence StartIntro()
    {
        introCanvasGroup=GetComponent<UnityEngine.CanvasGroup>()!;
        var ctx=SRSingleton<SceneContext>.Instance;
        ++ctx.TutorialDirector.Suppressed; ++Fixture.PopupSuppress; ++Fixture.AudioPause;
        var s=new DG.Tweening.Sequence();
        s.onComplete=()=> { endReached=true; Close(); };
        DG.Tweening.DOTween.Playing.Add(s); return s;
    }
    public void OnDisable()
    {
        ++Fixture.Disables; --Fixture.PopupSuppress; --Fixture.AudioPause;
        SRSingleton<SceneContext>.Instance.TutorialDirector.UnsuppressTutorials();
    }
    public bool EndReached=>endReached;
}
// Explicit synthetic prefab attachments, not a claim about the serialized LAB prefab.
public class UIInputLocker : UnityEngine.MonoBehaviour
{
    public void OnEnable() { ++Fixture.Pausers; UnityEngine.Time.timeScale=0; }
    public void OnDisable()
    {
        // Native TimeDirector.Unpause retains its own end-of-frame ownership decrement.
        Fixture.EndOfFrame.Add(()=> { --Fixture.Pausers; if(Fixture.Pausers==0) UnityEngine.Time.timeScale=1; });
    }
}
public class SetInputMode : UnityEngine.MonoBehaviour
{
    public void OnEnable() => Fixture.InputModes.Add(gameObject.GetInstanceID());
    public void OnDisable() => Fixture.InputModes.Remove(gameObject.GetInstanceID());
}
public static class Destroyer
{
    public class Metadata { }
    public static readonly Dictionary<int,List<Action<Metadata>>> Monitors = new();
    public static void Monitor(UnityEngine.Object instance,Action<Metadata> action)
    {
        if(!Monitors.TryGetValue(instance.GetInstanceID(),out var l)) Monitors.Add(instance.GetInstanceID(),l=new());
        l.Add(action);
        Fixture.MonitorPostfix?.Invoke(null,new object[]{instance,action});
    }
    public static void Destroy(UnityEngine.Object instance,string source)
    {
        ++Fixture.Closes;
        if(Monitors.Remove(instance.GetInstanceID(),out var l)) foreach(var cb in l) cb(new());
        ((UnityEngine.GameObject)instance).DestroyDeferred();
    }
}
namespace MonomiPark.SlimeRancher.Persist
{
    public class AmmoData { public Identifiable.Id id; public int count; }
    public class PlayerData { public Dictionary<PlayerState.AmmoMode,List<AmmoData>> ammo=new(); }
    public class Summary { public PlayerState.GameMode gameMode; public Identifiable.Id iconId=Identifiable.Id.PINK_SLIME;
        public bool isInvalid; public string name="",saveName=""; public DateTimeOffset saveTimestamp; }
    public class GameV12
    {
        public string gameName="",displayName=""; public Summary summary=new(); public PlayerData player=new();
        public void Load(Stream s) { using(var r=new StreamReader(s,leaveOpen:true)) gameName=r.ReadToEnd(); displayName="SlimeBridge "+gameName.Substring(4,8); }
    }
}
internal static class Fixture
{
    internal static UnityEngine.SceneManagement.Scene Scene=new(){handle=1,name="worldGenerated",isLoaded=true};
    internal static MethodInfo? NamePrefix,MonitorPostfix;
    internal static readonly List<Action> Deferred=new(),EndOfFrame=new();
    internal static readonly HashSet<int> InputModes=new();
    internal static DG.Tweening.Sequence EnableIntro(IntroUI owner)
    {
        owner.gameObject.GetComponent<UIInputLocker>()?.OnEnable();
        owner.gameObject.GetComponent<SetInputMode>()?.OnEnable();
        return owner.StartIntro();
    }
    internal static int TotalComplete,Closes,Disables,PopupSuppress,AudioPause,Pausers;
    internal static bool SaveFails;
    internal static void Frame()
    {
        ++UnityEngine.Time.frameCount; UnityEngine.Time.realtimeSinceStartup+=0.016f;
        var d=Deferred.ToArray(); Deferred.Clear(); foreach(var a in d)a();
        var e=EndOfFrame.ToArray(); EndOfFrame.Clear(); foreach(var a in e)a();
    }
}
