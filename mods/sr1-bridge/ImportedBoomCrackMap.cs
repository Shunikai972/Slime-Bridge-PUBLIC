using System;
using UnityEngine;
using UnityEngine.Rendering;

namespace SlimeBridge.SR1
{
    /// <summary>LAB comparison: use native crack pixels at mip zero, as the SR2 shader does.
    /// Owns a GPU-only copy; never changes the shared native cubemap or reads it back to the CPU.</summary>
    internal sealed class ImportedBoomCrackMap : MonoBehaviour
    {
        private Cubemap? _source;
        private Cubemap? _owned;
        private bool _attempted;

        internal bool Apply(Material material)
        {
            var source = material.GetTexture("_Cracks") as Cubemap;
            if (source == null) return false;
            if (source.mipmapCount == 1) return true;
            if (!_attempted)
            {
                _attempted = true;
                _source = source;
                _owned = LabBoomCrackSource.Load() ?? CopyDetail(source);
                if (_owned != null) Debug.Log("[SlimeBridge LAB] Boom cube nativeFormat=" + source.graphicsFormat + " ownedFormat=" + _owned.graphicsFormat);
            }
            if (source != _source || _owned == null) return false;
            material.SetTexture("_Cracks", _owned);
            return true;
        }

        internal static Cubemap? CopyDetail(Cubemap source)
        {
            if (source == null || (SystemInfo.copyTextureSupport & CopyTextureSupport.Basic) == 0) return null;
            Cubemap? owned = null;
            try
            {
                owned = new Cubemap(source.width, source.format, false)
                {
                    name = "SlimeBridgeBoomCracksMipZero",
                    filterMode = FilterMode.Bilinear,
                    wrapMode = source.wrapMode
                };
                for (var face = 0; face < 6; face++)
                    Graphics.CopyTexture(source, face, 0, owned, face, 0);
                return owned;
            }
            catch (Exception)
            {
                if (owned != null) Destroy(owned);
                return null; // Preserve the working native material on unsupported GPUs.
            }
        }

        private void OnDestroy()
        {
            if (_owned != null) Destroy(_owned);
            _owned = null;
            _source = null;
        }
    }
}
