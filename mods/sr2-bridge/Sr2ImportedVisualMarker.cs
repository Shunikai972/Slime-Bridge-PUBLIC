using System;
using System.Collections.Generic;
using Il2CppInterop.Runtime.Attributes;
using Il2CppInterop.Runtime.Injection;
using UnityEngine;
namespace SlimeBridge.SR2
{
    /// <summary>Per-instance owner: native Instantiate must never copy managed ownership.</summary>
    internal sealed class Sr2ImportedVisualMarker : MonoBehaviour
    {
        internal const string RootName = "SlimeBridgeImportedVisual";
        internal string CanonicalId = "", ModelSha256 = "", DefaultClip = "";
        internal bool Initialized, Disposed;
        internal int PaletteVerified, IgnoredEffectParts;
        internal readonly HashSet<string> SkippedTextureProperties = new HashSet<string>(StringComparer.Ordinal);
        internal readonly List<UnityEngine.Object> Owned = new List<UnityEngine.Object>();
        internal Renderer[] Renderers = Array.Empty<Renderer>();
        private readonly Dictionary<IntPtr, (Renderer Renderer, bool Enabled)> _nativeRenderers = new Dictionary<IntPtr, (Renderer, bool)>();
        private readonly Dictionary<IntPtr, (LODGroup Group, bool Enabled)> _nativeLods = new Dictionary<IntPtr, (LODGroup, bool)>();
        [HideFromIl2Cpp]
        internal void Hide(Renderer renderer)
        {
            if (!_nativeRenderers.ContainsKey(renderer.Pointer)) _nativeRenderers.Add(renderer.Pointer, (renderer, renderer.enabled));
            renderer.enabled = false;
        }
        [HideFromIl2Cpp]
        internal void Hide(LODGroup group)
        {
            if (!_nativeLods.ContainsKey(group.Pointer)) _nativeLods.Add(group.Pointer, (group, group.enabled));
            group.enabled = false;
        }
        [HideFromIl2Cpp]
        private void RestoreNative()
        {
            foreach (var pair in _nativeRenderers.Values)
                try { if (pair.Renderer != null) pair.Renderer.enabled = pair.Enabled; } catch (Exception) { }
            foreach (var pair in _nativeLods.Values)
                try { if (pair.Group != null) pair.Group.enabled = pair.Enabled; } catch (Exception) { }
            _nativeRenderers.Clear(); _nativeLods.Clear();
        }
        public Sr2ImportedVisualMarker(IntPtr pointer) : base(pointer) { }
        [HideFromIl2Cpp]
        internal static void Register()
        {
            if (!ClassInjector.IsTypeRegisteredInIl2Cpp<Sr2ImportedVisualMarker>())
                ClassInjector.RegisterTypeInIl2Cpp<Sr2ImportedVisualMarker>();
        }
        [HideFromIl2Cpp]
        internal T Own<T>(T value) where T : UnityEngine.Object { Owned.Add(value); return value; }
        public void OnEnable()
        {
            if (!Initialized || Disposed || DefaultClip.Length == 0) return;
            try { var playback = GetComponent<Animation>(); if (playback != null) playback.Play(DefaultClip, PlayMode.StopAll); }
            catch (Exception ex) { Debug.Log("[SlimeBridge] source playback: " + ex.GetType().Name); }
        }
        public void OnDestroy() { ReleaseOwned(); }
        [HideFromIl2Cpp]
        internal void ReleaseOwned()
        {
            if (Disposed) return;
            Disposed = true; Initialized = false;
            RestoreNative();
            foreach (var asset in Owned)
                try { if (asset != null) UnityEngine.Object.Destroy(asset); } catch (Exception) { }
            Owned.Clear();
        }
    }
}
