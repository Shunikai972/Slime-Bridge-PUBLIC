using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Marker on the attached imported visual root ("SlimeBridgeImportedVisual"). Lookup by
    /// component rather than by name so the visual survives template copies, renames and
    /// diagnostics; also lets the suppression walk skip the visual subtree.
    /// </summary>
    internal sealed class ImportedVisualMarker : MonoBehaviour
    {
    }
}
