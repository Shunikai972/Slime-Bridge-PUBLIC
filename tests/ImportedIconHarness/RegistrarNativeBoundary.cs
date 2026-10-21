using UnityEngine;

// Only the native boundary is synthetic; the registrar, spec parser, path/hash
// guards, ownership code and selection ordering run from production source.
public class Identifiable:Component {
    public enum Id {NONE,TABBY_SLIME,TABBY_PLORT}
    public Id id; public static HashSet<Id> SLIME_CLASS=new(),EATERS_CLASS=new(),PLORT_CLASS=new(),NON_SLIMES_CLASS=new();
    public static bool IsSlime(Id i)=>SLIME_CLASS.Contains(i); public static bool IsPlort(Id i)=>PLORT_CLASS.Contains(i);
}
public class SlimeEat {public enum FoodGroup {MEAT,FRUIT,VEGGIES}}
public class SlimeDiet {
    public SlimeEat.FoodGroup[] MajorFoodGroups=Array.Empty<SlimeEat.FoodGroup>();
    public Identifiable.Id[] Favorites=Array.Empty<Identifiable.Id>(),AdditionalFoods=Array.Empty<Identifiable.Id>(),Produces=Array.Empty<Identifiable.Id>();
    public int FavoriteProductionCount; public List<object> EatMap=new(){new()};
}
public class SlimeDefinitions {
    public Dictionary<Identifiable.Id,SlimeDefinition> slimeDefinitionsByIdentifiable=new();
    public SlimeDefinition[] Slimes=Array.Empty<SlimeDefinition>();
    public SlimeDefinition? GetSlimeByIdentifiableId(Identifiable.Id id)=>slimeDefinitionsByIdentifiable.GetValueOrDefault(id);
    public void RefreshDefinitions(){}
}
public class VacItemDefinition {
    public Identifiable.Id Id; public Color Color;public Sprite? Icon;
    public static VacItemDefinition CreateVacItemDefinition(Identifiable.Id i,Color c,Sprite? s)=>new(){Id=i,Color=c,Icon=s};
}
public class SlimeAppearanceDirector {
    public Dictionary<SlimeDefinition,SlimeAppearance> chosen=new(); public List<string> Calls=new();
    public bool ThrowOnSelection;
    public void UnlockAppearance(SlimeDefinition d,SlimeAppearance a){if(a.Icon==null)throw new Exception("unlock before icon initialized");Calls.Add("unlock");}
    public void UpdateChosenSlimeAppearance(SlimeDefinition d,SlimeAppearance a){if(a.Icon==null)throw new Exception("select before icon initialized");Calls.Add("select");chosen[d]=a;if(ThrowOnSelection)throw new Exception("selection failure");}
    public SlimeAppearance? GetChosenSlimeAppearance(SlimeDefinition d)=>chosen.GetValueOrDefault(d);
}
public class LookupDirector {
    public Dictionary<Identifiable.Id,GameObject> identifiablePrefabDict=new();
    public Dictionary<Identifiable.Id,VacItemDefinition> vacItemDict=new();public List<VacItemDefinition> vacItemDefinitionsDynamic=new();
    public SlimeAppearanceDirector slimeAppearanceDirector=new();
    public GameObject? GetPrefab(Identifiable.Id id)=>identifiablePrefabDict.GetValueOrDefault(id);
}
public class GameContext {
    public static GameContext? Instance; public SlimeDefinitions SlimeDefinitions=new(); public LookupDirector LookupDirector=new();
}
public class SlimeAppearanceApplicator:Component { public SlimeAppearanceDirector? SlimeAppearanceDirector; }
namespace SlimeBridge.SR1 {internal static class ImportedIdentityPatch {internal static void Register(int i,string n){} internal static void Unregister(int i){} }}
namespace SlimeBridge.SR1 {internal static class ImportedPlortAppearance {internal sealed class Info {internal UnityEngine.Color Middle = default; internal UnityEngine.Sprite? Icon = null;} internal static bool TryGet(string id,out Info info){info=null!;return false;} internal static int ApplyToPlort(UnityEngine.GameObject plort,Info info)=>0;}}
namespace SRML {public static class SRModLoader {public enum LoadingStep{FINISHED} public static LoadingStep CurrentLoadingStep;}}
namespace UnityEngine {
    public class Component:Object {}
    public class Transform {public void SetParent(Transform t,bool worldPositionStays){} }
    public class GameObject:Object {
        public Transform transform=new();public GameObject(string n=""){name=n;}
        public void SetActive(bool active){}
        public T[] GetComponentsInChildren<T>(bool inactive)=>System.Array.Empty<T>();
    }
    public struct Color {public static Color magenta=>new();}
    public static class Time {public static float realtimeSinceStartup;}
    public static class Debug {public static void Log(string text){} }
}
