// Caught, method-extracted production policy fixture; not real IL2CPP diet/feeding execution.
using UnityEngine;
internal static partial class IdentityHarness
{
    private static int checks,failures;
    private static void Check(bool value,string message) { checks++;if(!value){failures++;Console.WriteLine("FAIL "+message);} }
    public static int Main()
    {
        try
        {
            var template = new Il2Cpp.SlimeDefinition();
            var imported = new Il2Cpp.SlimeDefinition();
            var clone = new GameObject();
            var ident = new Il2Cpp.Identifiable { identType = template };
            var eater = new Il2Cpp.SlimeEat { SlimeDefinition = template };
            var nestedEater = new Il2Cpp.SlimeEat { SlimeDefinition = template };
            clone.Items.AddRange(new object[]{ident,eater,nestedEater});
            Check(RetargetIdentifiables(clone,imported),"valid inactive slime binds");
            Check(ident.identType==imported,"imported identifiable identity binds");
            Check(eater.SlimeDefinition==imported && nestedEater.SlimeDefinition==imported,"root and nested eaters receive imported diet definition before Awake");
            Check(template.Pointer!=imported.Pointer,"native template definition is not reused or mutated");
            var noEater=new GameObject();noEater.Items.Add(new Il2Cpp.Identifiable{identType=template});
            Check(!RetargetIdentifiables(noEater,imported),"missing eater refuses registration rather than template diet fallback");
            var active=new GameObject {activeInHierarchy=true};
            var activeIdent=new Il2Cpp.Identifiable{identType=template};var activeEat=new Il2Cpp.SlimeEat{SlimeDefinition=template};
            active.Items.AddRange(new object[]{activeIdent,activeEat});
            Check(!RetargetIdentifiables(active,imported) && activeIdent.identType==template && activeEat.SlimeDefinition==template,"active actor refuses retarget before any identity mutation");
            var plort=new Il2Cpp.IdentifiableType();var plortClone=new GameObject();var plortIdent=new Il2Cpp.Identifiable{identType=template};plortClone.Items.Add(plortIdent);
            Check(RetargetIdentifiables(plortClone,plort) && plortIdent.identType==plort,"plort has no eater and keeps supported identity path");
        }
        catch(Exception ex){failures++;Console.WriteLine("CAUGHT "+ex.GetType().Name+": "+ex.Message);}
        Console.WriteLine($"SR2_EATER_IDENTITY_CHECKS={checks} FAILURES={failures}");
        return failures==0?0:1;
    }
}
namespace UnityEngine
{
    public class GameObject
    {
        public bool activeInHierarchy;
        public readonly List<object> Items=new();
        public T[] GetComponentsInChildren<T>(bool includeInactive) => Items.OfType<T>().ToArray();
    }
}
namespace Il2Cpp
{
    public class IdentifiableType { private static int next;public readonly IntPtr Pointer=new(++next); }
    public class SlimeDefinition:IdentifiableType { }
    public class Identifiable { public IdentifiableType? identType; }
    public class SlimeEat { public SlimeDefinition? SlimeDefinition; }
}
