using System;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Shared, reference-counted authored shader. The native SR1 crack texture is borrowed,
    /// never extracted or shipped. Only the caller's owned eligible materials are changed.</summary>
    internal sealed class ImportedBoomShader : MonoBehaviour
    {
        internal const string ResourceName = "SlimeBridge.SR1.ImportedBoom.Windows";
        private static AssetBundle? _bundle;
        private static Shader? _shader;
        private static int _users;
        private static bool _unavailable;
        private Shader? _owned;
        private bool _attempted;

        internal bool Apply(Material material, bool ear)
        {
            if (material == null || !(material.GetTexture("_Cracks") is Cubemap)) return false;
            if (!_attempted)
            {
                _attempted = true;
                _owned = Acquire();
            }
            if (_owned == null) return false;
            var top = material.GetColor("_TopColor");
            var middle = material.GetColor("_MiddleColor");
            var bottom = material.GetColor("_BottomColor");
            var gloss = material.GetFloat("_Gloss");
            var power = material.GetFloat("_GlossPower");
            var cracks = material.GetTexture("_Cracks");
            material.shader = _owned;
            material.SetColor("_TopColor", top); material.SetColor("_MiddleColor", middle); material.SetColor("_BottomColor", bottom);
            material.SetFloat("_Gloss", gloss); material.SetFloat("_GlossPower", power); material.SetTexture("_Cracks", cracks);
            material.SetColor("_CrackColor", new Color(2f, 1f, .25f, 1f));
            material.SetFloat("_AttachmentCracks", ear ? 1f : 0f);
            return true;
        }

        private static Shader? Acquire()
        {
            if (_shader != null) { _users++; return _shader; }
            if (_unavailable) return null;
            // Bundles are target-specific. Other targets keep the real native material.
            if (Application.platform != RuntimePlatform.WindowsPlayer) { _unavailable = true; return null; }
            AssetBundle? candidate = null;
            try
            {
                using (var stream = typeof(ImportedBoomShader).Assembly.GetManifestResourceStream(ResourceName))
                {
                    if (stream == null) throw new InvalidOperationException("Own shader resource not embedded");
                    if (stream.Length < 1 || stream.Length > 4194304) throw new InvalidOperationException("Own shader resource size invalid");
                    var bytes = new byte[(int)stream.Length];
                    var offset = 0;
                    while (offset < bytes.Length)
                    {
                        var read = stream.Read(bytes, offset, bytes.Length - offset);
                        if (read == 0) throw new InvalidOperationException("Own shader resource truncated");
                        offset += read;
                    }
                    candidate = AssetBundle.LoadFromMemory(bytes);
                }
                if (candidate == null) throw new InvalidOperationException("Own shader bundle could not load");
                var names = candidate.GetAllAssetNames();
                if (names.Length != 1) throw new InvalidOperationException("Own shader bundle asset count invalid");
                var shader = candidate.LoadAsset<Shader>(names[0]);
                if (shader == null || shader.name != "SlimeBridge/ImportedBoom" || !shader.isSupported || shader.passCount != 2)
                    throw new InvalidOperationException("Own shader unsupported");
                _bundle = candidate; _shader = shader; _users = 1;
                candidate = null;
                Debug.Log("[SlimeBridge] Boom embedded shader loaded name=" + shader.name + " passes=" + shader.passCount);
                return shader;
            }
            catch (Exception error)
            {
                _unavailable = true;
                Debug.LogWarning("[SlimeBridge] Boom embedded shader unavailable; preserving native shader: " + error.GetType().Name);
                return null;
            }
            finally { if (candidate != null) candidate.Unload(true); }
        }

        private void OnDestroy()
        {
            if (_owned == null || !ReferenceEquals(_owned, _shader)) return;
            _owned = null;
            if (--_users > 0) return;
            if (_bundle != null) _bundle.Unload(true);
            _bundle = null; _shader = null; _users = 0;
        }
    }
}
