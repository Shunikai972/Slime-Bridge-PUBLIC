using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Owned imported body slots borrow SR1's actual Boom shader/cubemap and follow the
    /// actor's native readiness/recovery. Never draws cracks on faces, user bakes or native actors.</summary>
    internal sealed class ImportedBoomVisual : MonoBehaviour
    {
        internal const string BodyShader = "SR/AMP/Slime/Body/BoomCracks";
        private static readonly FieldInfo? ExplosionFx = typeof(BoomSlimeExplode).GetField("explodeFX", BindingFlags.Instance | BindingFlags.NonPublic);
        private BoomSlimeExplode? _boom;
        private GameObject? _fallbackFx;
        private bool _fxChecked;
        private readonly List<Slot> _slots = new List<Slot>();
        private MaterialPropertyBlock? _block;
        private float _lastCrack = -1f, _lastChar = -1f;
        internal struct Slot { internal Renderer Renderer; internal int Index; }

        internal static bool Eligible(string canonical, bool face, string sourceShader)
        {
            return !face && sourceShader == ImportedBodyLighting.SourceShader &&
                canonical.StartsWith("largo.", StringComparison.Ordinal) &&
                (canonical.Contains(".boom.") || canonical.EndsWith(".boom", StringComparison.Ordinal));
        }

        internal static Material? NativeTemplate()
        {
            var appearance = NativeAppearance();
            if (appearance?.Structures == null) return null;
            foreach (var structure in appearance.Structures)
                if (structure?.DefaultMaterials != null)
                    foreach (var material in structure.DefaultMaterials)
                        if (material != null && material.shader != null && material.shader.name == BodyShader &&
                            material.HasProperty("_CrackAmount") && material.HasProperty("_Char") &&
                            material.GetTexture("_Cracks") is Cubemap) return material;
            return null;
        }

        private static SlimeAppearance? NativeAppearance()
        {
            var definition = GameContext.Instance?.SlimeDefinitions?.GetSlimeByIdentifiableId(Identifiable.Id.BOOM_SLIME);
            return definition?.GetAppearanceForSet(SlimeAppearance.AppearanceSaveSet.CLASSIC) ??
                (definition?.AppearancesDefault != null && definition.AppearancesDefault.Length > 0 ? definition.AppearancesDefault[0] : null);
        }

        internal static void Bind(GameObject visual, GameObject actor, List<Slot> slots)
        {
            if (slots.Count == 0 || slots.Count > 32) return;
            var boom = actor.GetComponent<BoomSlimeExplode>();
            if (boom == null) return;
            var driver = visual.GetComponent<ImportedBoomVisual>() ?? visual.AddComponent<ImportedBoomVisual>();
            driver._boom = boom;
            driver._fallbackFx = NativeAppearance()?.ExplosionAppearance?.explodeFx;
            driver._fxChecked = false;
            driver._slots.Clear(); driver._slots.AddRange(slots);
            driver._block = driver._block ?? new MaterialPropertyBlock();
            driver._lastCrack = -1f; driver._lastChar = -1f;
            if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_BOOM_CRACK_DETAIL") == "1")
            {
                var detail = visual.GetComponent<ImportedBoomCrackMap>() ?? visual.AddComponent<ImportedBoomCrackMap>();
                var shader = visual.GetComponent<LabBoomShader>() ?? visual.AddComponent<LabBoomShader>();
                var applied = 0;
                foreach (var slot in slots)
                {
                    if (slot.Renderer == null) continue;
                    var materials = slot.Renderer.sharedMaterials;
                    if (slot.Index >= 0 && slot.Index < materials.Length && materials[slot.Index] != null)
                    {
                        if (detail.Apply(materials[slot.Index])) applied++;
                        shader.Apply(materials[slot.Index], string.Equals(slot.Renderer.GetComponent<ImportedPartColors>()?.SourceRole, "cottonears", StringComparison.Ordinal));
                    }
                }
                Debug.Log("[SlimeBridge LAB] Boom mip-zero slots=" + applied + "/" + slots.Count);
            }
            else
            {
                var shader = visual.GetComponent<ImportedBoomShader>() ?? visual.AddComponent<ImportedBoomShader>();
                var applied = 0;
                foreach (var slot in slots)
                {
                    if (slot.Renderer == null) continue;
                    var materials = slot.Renderer.sharedMaterials;
                    if (slot.Index >= 0 && slot.Index < materials.Length && materials[slot.Index] != null &&
                        shader.Apply(materials[slot.Index], string.Equals(slot.Renderer.GetComponent<ImportedPartColors>()?.SourceRole, "cottonears", StringComparison.Ordinal))) applied++;
                }
                Debug.Log("[SlimeBridge] Boom embedded shader slots=" + applied + "/" + slots.Count);
            }
        }

        internal static bool Evaluate(float readiness, float recovery, out float cracks, out float charAmount)
        {
            cracks = charAmount = 0f;
            if (float.IsNaN(readiness) || float.IsInfinity(readiness) || float.IsNaN(recovery) || float.IsInfinity(recovery)) return false;
            // Exact native BoomMaterialAnimator response, with defensive input clamping.
            charAmount = Mathf.Clamp01(recovery * 2.5f);
            cracks = (1f - charAmount) * Mathf.Lerp(.1f, 1f, Mathf.Clamp01(readiness));
            return true;
        }

        private void LateUpdate()
        {
            if (_boom == null || _block == null) return;
            // Imported prefab/applicator initialization can leave the native cached FX empty.
            // Borrow the real Boom asset only when absent, after Awake; never replace a valid FX.
            if (!_fxChecked && ExplosionFx != null && _fallbackFx != null)
            {
                if (ExplosionFx.GetValue(_boom) as GameObject == null)
                    ExplosionFx.SetValue(_boom, _fallbackFx);
                _fxChecked = true;
            }
            if (!Evaluate(_boom.GetReadiness(), _boom.GetRecoveriness(), out var cracks, out var charAmount)) return;
            if (Mathf.Abs(cracks - _lastCrack) < .001f && Mathf.Abs(charAmount - _lastChar) < .001f) return;
            foreach (var slot in _slots)
            {
                if (slot.Renderer == null) continue;
                _block.Clear();
                slot.Renderer.GetPropertyBlock(_block, slot.Index);
                _block.SetFloat("_CrackAmount", cracks); _block.SetFloat("_Char", charAmount);
                slot.Renderer.SetPropertyBlock(_block, slot.Index);
            }
            _lastCrack = cracks; _lastChar = charAmount;
        }
    }
}
