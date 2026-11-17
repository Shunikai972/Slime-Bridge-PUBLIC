using System;
using System.IO;
using System.Security.Cryptography;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>LAB-only authored shader comparison; never replaces a native/shared material.</summary>
    internal sealed class LabBoomShader : MonoBehaviour
    {
        private Shader? _owned;
        private bool _attempted;
        internal bool Apply(Material material, bool ear)
        {
            if (!_attempted) { _attempted = true; _owned = Load(); }
            if (_owned == null || material == null) return false;
            var top = material.GetColor("_TopColor"); var middle = material.GetColor("_MiddleColor"); var bottom = material.GetColor("_BottomColor");
            var gloss = material.GetFloat("_Gloss"); var power = material.GetFloat("_GlossPower"); var cracks = material.GetTexture("_Cracks");
            material.shader = _owned;
            material.SetColor("_TopColor", top); material.SetColor("_MiddleColor", middle); material.SetColor("_BottomColor", bottom);
            material.SetFloat("_Gloss", gloss); material.SetFloat("_GlossPower", power); material.SetTexture("_Cracks", cracks);
            material.SetColor("_CrackColor", new Color(2f, 1f, .25f, 1f));
            material.SetFloat("_AttachmentCracks", ear ? 1f : 0f);
            Debug.Log("[SlimeBridge LAB] Boom authored material cracks=" + (cracks != null ? cracks.name : "null") + " gloss=" + gloss + " power=" + power);
            return true;
        }
        private static Shader? Load()
        {
            AssetBundle? bundle = null;
            Shader? shader = null;
            var accepted = false;
            try
            {
                if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_CRACK_DETAIL") != "1") return null;
                var path = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_SHADER_PATH");
                var expected = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_SHADER_SHA256");
                var root = LabSpecPathGuard.FindRepoRoot();
                if (root == null || string.IsNullOrEmpty(path) || expected == null || expected.Length != 64 ||
                    !LabSpecPathGuard.IsAllowedFile(Path.Combine(root, "Lab", "RuntimeArtifacts"), path!, out _)) return null;
                byte[] bytes;
                using (var stream = new FileStream(path!, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (stream.Length < 1 || stream.Length > 4194304) return null;
                    bytes = new byte[(int)stream.Length];
                    var offset = 0;
                    while (offset < bytes.Length) { var read = stream.Read(bytes, offset, bytes.Length - offset); if (read == 0) return null; offset += read; }
                }
                using (var digest = SHA256.Create())
                    if (!string.Equals(BitConverter.ToString(digest.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(), expected, StringComparison.Ordinal)) return null;
                bundle = AssetBundle.LoadFromMemory(bytes);
                if (bundle == null) return null;
                var names = bundle.GetAllAssetNames();
                if (names.Length != 1) return null;
                shader = bundle.LoadAsset<Shader>(names[0]);
                if (shader == null || shader.name != "SlimeBridge/ImportedBoom" || !shader.isSupported) return null;
                Debug.Log("[SlimeBridge LAB] Boom authored shader loaded name=" + shader.name + " passes=" + shader.passCount);
                accepted = true;
                return shader;
            }
            catch (Exception error) { Debug.LogWarning("[SlimeBridge LAB] Boom shader refused: " + error.GetType().Name); return null; }
            finally { if (bundle != null) bundle.Unload(!accepted); }
        }
        private void OnDestroy() { if (_owned != null) Destroy(_owned); _owned = null; }
    }
}
