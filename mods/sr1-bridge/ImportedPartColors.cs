using System;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Per-part source appearance data attached by the model loader to each imported renderer:
    /// the source material's three-way palette and the baked textures (pattern mask for the body
    /// shader's _StripeTexture slot, face art for the face shader's _FaceAtlas slot). The spawn
    /// rebind reads these to configure each renderer's own native-material instance.
    /// </summary>
    internal sealed class ImportedPartColors : MonoBehaviour
    {
        public Color Top = Color.clear;
        // Immutable static source passes bypass palette and all-eyes rebinding.
        public bool SourceFace;
        public Color Middle = Color.clear;
        public Color Bottom = Color.clear;
        // Source SR2 gloss (`_GlossRGB`/`_GlossPowerRGB` means) flow into the native
        // Slime/Body clone so the imported visual keeps the source's matte look instead
        // of the template species' much shinier values. NaN = source declares none.
        public float Gloss = float.NaN;
        public float GlossPower = float.NaN;
        // The presence flag preserves legacy null semantics; serializing a custom nullable
        // payload inline would lose both the readonly values and reliable absence in Unity.
        [SerializeField] private bool hasBodyLighting;
        [SerializeField] private float lightingUVContribution;
        [SerializeField] private float bodyLightingContribution;
        [SerializeField] private float lightingUVHorizontalAdjust;
        [System.NonSerialized] private ImportedBodyLighting? bodyLighting;
        internal bool HasBodyLighting => hasBodyLighting;
        public ImportedBodyLighting? BodyLighting
        {
            get
            {
                if (!hasBodyLighting) return null;
                if (bodyLighting == null)
                    bodyLighting = ImportedBodyLighting.Restore(lightingUVContribution,
                        bodyLightingContribution, lightingUVHorizontalAdjust);
                return bodyLighting;
            }
            set
            {
                bodyLighting = value;
                hasBodyLighting = value != null;
                lightingUVContribution = value?.UVContribution ?? 0f;
                bodyLightingContribution = value?.BodyContribution ?? 0f;
                lightingUVHorizontalAdjust = value?.HorizontalAdjust ?? 0f;
            }
        }
        public Texture2D? Pattern;
        public Texture2D? FaceTex;
        public Texture2D? FaceMask;
        // Source row provenance ([SerializeField]: both must survive the visual Instantiate
        // clone): the portable row's role (body/face/attachment) and the first-pass source
        // shader. The shader family decides whether the spawn may re-skin this part with the
        // native body material; an unknown shader (legacy caches) keeps the legacy rebind.
        [SerializeField] private string sourceRole = "";
        [SerializeField] private string sourceShader = "";
        internal string SourceRole { get => sourceRole ?? ""; set => sourceRole = value ?? ""; }
        internal string SourceShader { get => sourceShader ?? ""; set => sourceShader = value ?? ""; }
        internal bool SourceShaderKnown => !string.IsNullOrEmpty(sourceShader);
        internal bool SkipNativePaletteRebind => SourceShaderKnown && !IsNativeSlimeFamilyShader(sourceShader);
        internal const string NativeSlimeShaderFamily = "SR/AMP/Slime/";
        // Only the native slime families have a destination rebind contract. Anything else
        // (source FX smoke, Null Render, a future shader) must keep its converter materials:
        // a body-material clone would paint it as an opaque palette blob.
        internal static bool IsNativeSlimeFamilyShader(string shaderName)
        {
            return !string.IsNullOrEmpty(shaderName) &&
                shaderName.StartsWith(NativeSlimeShaderFamily, StringComparison.Ordinal);
        }
    }
}
