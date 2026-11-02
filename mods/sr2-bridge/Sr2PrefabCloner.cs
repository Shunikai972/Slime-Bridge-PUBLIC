using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>Build reference prefabs without running native actor callbacks at the menu.</summary>
    internal static class Sr2PrefabCloner
    {
        // Registered definitions retain these reference prefabs across scene changes.
        // Keep their container inactive for the lifetime of the native registries.
        private static GameObject? _root;

        internal static GameObject Clone(GameObject template)
        {
            if (_root == null)
            {
                _root = new GameObject("SlimeBridge_ReferencePrefabs");
                _root.SetActive(false);
                UnityEngine.Object.DontDestroyOnLoad(_root);
            }
            // Parenting during Instantiate prevents Awake/OnEnable. Disabling a clone
            // afterwards is too late: native callbacks may already enter ActorRegistry.
            var clone = UnityEngine.Object.Instantiate(template, _root.transform, false);
            clone.SetActive(false);
            return clone;
        }
    }
}
