using SlimeBridge.SR2;
using UnityEngine;

// Managed lifecycle contract only. Real Unity/IL2CPP verification is a separate gate.
var passed = 0;
void Check(bool value, string label)
{
    if (!value) throw new InvalidOperationException("FAIL " + label);
    passed++;
    Console.WriteLine("PASS " + label);
}
var template = new GameObject("native-slime-template") { HasActorScript = true };
var legacy = UnityEngine.Object.Instantiate(template);
legacy.SetActive(false);
Check(UnityEngine.Object.AwakeCalls == 1, "control: disabling after Instantiate cannot undo Awake");
UnityEngine.Object.AwakeCalls = 0;
var clone = Sr2PrefabCloner.Clone(template);
Check(UnityEngine.Object.AwakeCalls == 0, "reference cloning must not invoke native actor Awake at the menu");
Check(!clone.activeSelf && !clone.activeInHierarchy, "registered reference prefab is inactive itself and in hierarchy");
Check(clone.transform.parent != null && !clone.transform.parent.gameObject.activeSelf,
    "parent is already inactive when the native clone is created");
Check(template.activeSelf && template.transform.parent == null && !ReferenceEquals(clone, template),
    "original native template is not hidden, moved or returned as the clone");
var second = Sr2PrefabCloner.Clone(template);
Check(ReferenceEquals(clone.transform.parent, second.transform.parent) && UnityEngine.Object.PersistentCalls == 1,
    "reference clones share one persistent inactive root");
Check(UnityEngine.Object.AwakeCalls == 0, "second reference clone still does not register actor callbacks");
var live = UnityEngine.Object.Instantiate(clone);
Check(UnityEngine.Object.AwakeCalls == 0 && !live.activeSelf,
    "spawning the inactive reference still defers Awake until deliberate activation");
live.SetActive(true);
Check(UnityEngine.Object.AwakeCalls == 1 && live.activeInHierarchy,
    "world activation invokes actor Awake exactly once");
var plort = new GameObject("native-plort-template") { HasActorScript = true };
var before = UnityEngine.Object.AwakeCalls;
var plortReference = Sr2PrefabCloner.Clone(plort);
Check(UnityEngine.Object.AwakeCalls == before && !plortReference.activeSelf && plort.activeSelf,
    "the same contract applies to plort reference prefabs");
Console.WriteLine($"RESULT {passed} passed; 0 failed (managed lifecycle contracts, not Unity proof)");

namespace UnityEngine
{
    public class Object
    {
        public static int AwakeCalls, PersistentCalls;
        public static GameObject Instantiate(GameObject original) => Copy(original, null);
        public static GameObject Instantiate(GameObject original, Transform parent, bool worldPositionStays)
        {
            if (worldPositionStays) throw new InvalidOperationException("Expected preserved local prefab pose");
            return Copy(original, parent);
        }
        private static GameObject Copy(GameObject original, Transform? parent)
        {
            var clone = new GameObject(original.name) { HasActorScript = original.HasActorScript, activeSelf = original.activeSelf };
            clone.transform.parent = parent;
            clone.InvokeAwakeIfActive();
            return clone;
        }
        public static void DontDestroyOnLoad(GameObject value)
        {
            if (value.transform.parent != null) throw new InvalidOperationException("Only root objects can be persistent");
            PersistentCalls++;
        }
    }
    public class Transform
    {
        public readonly GameObject gameObject;
        public Transform? parent;
        public Transform(GameObject owner) { gameObject = owner; }
    }
    public class GameObject : Object
    {
        public string name;
        public readonly Transform transform;
        public bool activeSelf = true, HasActorScript;
        private bool _awakened;
        public bool activeInHierarchy => activeSelf && (transform.parent == null || transform.parent.gameObject.activeInHierarchy);
        public GameObject(string value) { name = value; transform = new Transform(this); }
        public void SetActive(bool value) { activeSelf = value; InvokeAwakeIfActive(); }
        internal void InvokeAwakeIfActive()
        {
            if (HasActorScript && activeInHierarchy && !_awakened) { _awakened = true; AwakeCalls++; }
        }
    }
}
