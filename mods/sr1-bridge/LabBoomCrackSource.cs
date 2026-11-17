using System;
using System.IO;
using System.Security.Cryptography;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>LAB-only six-face BC7 mip-zero input from the owner's source game.
    /// Fixed format/dimensions, bounded bytes and SHA-256. Never a distributable texture.</summary>
    internal static class LabBoomCrackSource
    {
        internal static Cubemap? Load()
        {
            var path = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_CUBE_PATH");
            if (string.IsNullOrEmpty(path)) return null;
            Cubemap? owned = null;
            try
            {
                if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_CRACK_DETAIL") != "1") return null;
                var root = LabSpecPathGuard.FindRepoRoot();
                if (root == null || !LabSpecPathGuard.IsAllowedFile(Path.Combine(root, "Lab", "RuntimeArtifacts"), path!, out _)) return null;
                var expected = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_CUBE_SHA256");
                if (expected == null || expected.Length != 64) return null;
                var bytes = new byte[393216]; // 256x256 BC7 = 65536 bytes per face, six faces.
                using (var stream = new FileStream(path!, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (stream.Length != bytes.Length) return null;
                    var offset = 0;
                    while (offset < bytes.Length)
                    {
                        var read = stream.Read(bytes, offset, bytes.Length - offset);
                        if (read == 0) return null;
                        offset += read;
                    }
                }
                using (var digest = SHA256.Create())
                    if (!string.Equals(BitConverter.ToString(digest.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(), expected, StringComparison.Ordinal)) return null;
                if (!SystemInfo.SupportsTextureFormat(TextureFormat.BC7)) return null;
                owned = new Cubemap(256, TextureFormat.BC7, false) { name = "SlimeBridgeSourceBoomMipZero", filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
                for (var face = 0; face < 6; face++) owned.SetPixelData(bytes, 0, (CubemapFace)face, face * 65536);
                owned.Apply(false, true);
                Debug.Log("[SlimeBridge LAB] Boom sourcecube loaded graphicsFormat=" + owned.graphicsFormat + " memory=" + UnityEngine.Profiling.Profiler.GetRuntimeMemorySizeLong(owned));
                return owned;
            }
            catch (Exception error)
            {
                if (owned != null) UnityEngine.Object.Destroy(owned);
                Debug.LogWarning("[SlimeBridge LAB] Boom sourcecube refused: " + error.GetType().Name);
                return null;
            }
        }
    }
}
