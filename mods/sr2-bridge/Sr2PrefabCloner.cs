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
            // Parenting during Instantiate prevents Awake/OnEnable because _root is inactive.
            // Keep prefab-asset semantics (activeSelf=true under the inactive container) so every
            // native caller (WeaponVacuum shooting, plort production, InstantiationHelpers.InstantiateActor,
            // and GameModel save reload) produces an ACTIVE live actor whose Awake/OnEnable run normally.
            var clone = UnityEngine.Object.Instantiate(template, _root.transform, false);
            if (!clone.activeSelf) clone.SetActive(true);
            return clone;
        }
    }
}
