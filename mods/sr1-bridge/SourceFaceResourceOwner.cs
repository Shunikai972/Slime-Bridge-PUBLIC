using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Template-only ownership. Nonserialized ledger is deliberately NOT
    /// copied by Unity Instantiate: actors borrow the immutable template passes.</summary>
    internal sealed class SourceFaceResourceOwner : MonoBehaviour
    {
        [NonSerialized] private List<UnityEngine.Object>? owned;
        internal int OwnedResourceCount => owned != null ? owned.Count : 0;

        internal void Retain(List<UnityEngine.Object> resources, Mesh mesh)
        {
            if (owned != null) throw new InvalidOperationException("source face owner initialized twice");
            owned = resources;
            owned.Add(mesh);
        }

        private void OnDestroy()
        {
            if (owned == null) return;
            foreach (var resource in owned) if (resource != null) UnityEngine.Object.Destroy(resource);
            owned.Clear();
            owned = null;
        }
    }
}
