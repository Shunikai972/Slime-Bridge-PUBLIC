using System; using System.Collections.Generic; using UnityEngine;
namespace SlimeBridge.SR1 { internal static class AmbientUnderTest {
private static readonly int[] ShIds =
        {
            Shader.PropertyToID("unity_SHAr"), Shader.PropertyToID("unity_SHAg"), Shader.PropertyToID("unity_SHAb"),
            Shader.PropertyToID("unity_SHBr"), Shader.PropertyToID("unity_SHBg"), Shader.PropertyToID("unity_SHBb"), Shader.PropertyToID("unity_SHC")
        };
private static void ApplyNeutralAmbient(GameObject visual)
        {
            if (visual == null) return;
            try
            {
                var renderers = new List<Renderer>();
                CollectRenderersBounded(visual, renderers);
                // a cool ambient balances the games' warm sunlight, which pulled every hue toward orange (measured)
                var a = ImportedModelLoader.LookAmbient; var cool = ImportedModelLoader.LookCool;
                float ar = a * (1f - 0.25f * cool), ag = a, ab = a * (1f + 0.35f * cool);
                foreach (var renderer in renderers)
                {
                    if (renderer == null) continue;
                    var ours = false;
                    foreach (var material in renderer.sharedMaterials)
                        if (material != null && material.name == "SlimeBridgeUserLargo") { ours = true; break; }
                    if (!ours) continue;
                    renderer.lightProbeUsage = UnityEngine.Rendering.LightProbeUsage.CustomProvided;
                    var block = new MaterialPropertyBlock();
                    renderer.GetPropertyBlock(block);
                    block.SetVector(ShIds[0], new Vector4(0f, 0f, 0f, ar));
                    block.SetVector(ShIds[1], new Vector4(0f, 0f, 0f, ag));
                    block.SetVector(ShIds[2], new Vector4(0f, 0f, 0f, ab));
                    for (var i = 3; i < ShIds.Length; i++) block.SetVector(ShIds[i], Vector4.zero);
                    renderer.SetPropertyBlock(block);
                    // SR1's Vacuumable preserves an indexed block with _HeldInVac=0 after release.
                    // Indexed blocks take precedence over the renderer-wide block, so each owned
                    // baked-material slot must carry its custom ambient too. Preserve native held
                    // state and all unrelated indexed properties; never touch a native material slot.
                    var materials = renderer.sharedMaterials;
                    for (var slot = 0; slot < materials.Length; slot++)
                    {
                        var material = materials[slot];
                        if (material == null || material.name != "SlimeBridgeUserLargo") continue;
                        var indexed = new MaterialPropertyBlock();
                        renderer.GetPropertyBlock(indexed, slot);
                        indexed.SetVector(ShIds[0], new Vector4(0f, 0f, 0f, ar));
                        indexed.SetVector(ShIds[1], new Vector4(0f, 0f, 0f, ag));
                        indexed.SetVector(ShIds[2], new Vector4(0f, 0f, 0f, ab));
                        for (var i = 3; i < ShIds.Length; i++) indexed.SetVector(ShIds[i], Vector4.zero);
                        renderer.SetPropertyBlock(indexed, slot);
                    }
                }
            }
            catch (Exception) { /* fail-open: the scene's ambient stays */ }
        }
internal static void Apply(GameObject root)=>ApplyNeutralAmbient(root);
private static void CollectRenderersBounded(GameObject root,List<Renderer> rows){rows.AddRange(root.Renderers);}
} }
