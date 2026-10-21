using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Destroys the per-actor material copies (native-shader clones) with the imported visual that owns them.</summary>
    internal sealed class ImportedMaterialOwner : MonoBehaviour
    {
        private readonly System.Collections.Generic.List<Material> _owned = new System.Collections.Generic.List<Material>();

        internal void Own(Material material) { if (material != null) _owned.Add(material); }

        private void OnDestroy()
        {
            foreach (var material in _owned) if (material != null) Destroy(material);
            _owned.Clear();
        }
    }
}
