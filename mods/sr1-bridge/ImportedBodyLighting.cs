using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Bounded adapter for compiled SR2 Body / SR1 Stripe lighting controls.
    /// It preserves source parameters, not HDRP lighting or multicolor rendering parity.</summary>
    internal sealed class ImportedBodyLighting
    {
        internal const string SourceShader = "SR/AMP/Slime/Body";
        internal const string DestinationShader = "SR/AMP/Slime/Body/Stripe";
        private readonly float uv;
        private readonly float body;
        private readonly float horizontal;
        internal float UVContribution => uv;
        internal float BodyContribution => body;
        internal float HorizontalAdjust => horizontal;

        private ImportedBodyLighting(float uv, float body, float horizontal)
        { this.uv = uv; this.body = body; this.horizontal = horizontal; }

        // Unity transports primitive fields on the marker, not this immutable managed object.
        // Revalidate after deserialization instead of trusting Inspector/serialized values.
        internal static ImportedBodyLighting? Restore(float uv, float body, float horizontal)
        {
            return IsControl(uv) && IsControl(body) && IsControl(horizontal)
                ? new ImportedBodyLighting(uv, body, horizontal) : null;
        }

        private static bool IsControl(float value)
        { return !float.IsNaN(value) && !float.IsInfinity(value) && value >= 0f && value <= 1f; }

        internal static bool TryRead(Dictionary<string, object> material, out ImportedBodyLighting? value, out string problem)
        {
            value = null;
            problem = "";
            // Old caches lack these properties. Unsupported source families have no adapter.
            if (!material.TryGetValue("shader", out var shader) || !(shader is string name) ||
                !string.Equals(name, SourceShader, StringComparison.Ordinal) || !material.TryGetValue("floats", out var raw))
                return true;
            if (!(raw is Dictionary<string, object> floats) ||
                !TryControl(floats, "_LightingUVContribution", out var uv) ||
                !TryControl(floats, "_BodyLightingContribution", out var body) ||
                !TryControl(floats, "_LightingUVHorizontalAdjust", out var horizontal))
            {
                problem = "source body lighting requires three finite numeric controls in [0, 1]";
                return false;
            }
            value = new ImportedBodyLighting(uv, body, horizontal);
            return true;
        }

        private static bool TryControl(Dictionary<string, object> properties, string name, out float value)
        {
            value = 0f;
            if (!properties.TryGetValue(name, out var raw)) return false;
            double number;
            switch (raw)
            {
                case long integer: number = integer; break;
                case int integer: number = integer; break;
                case double fraction: number = fraction; break;
                case float fraction: number = fraction; break;
                default: return false;
            }
            if (double.IsNaN(number) || double.IsInfinity(number) || number < 0d || number > 1d) return false;
            value = (float)number;
            return true;
        }

        private static bool Supports(Material material)
        {
            return material != null && material.shader != null &&
                string.Equals(material.shader.name, DestinationShader, StringComparison.Ordinal) &&
                material.HasProperty("_LightingUVContribution") && material.HasProperty("_BodyLightingContribution") &&
                material.HasProperty("_LightingUVHorizontalAdjust");
        }

        internal int Apply(Material material)
        {
            // Check the entire contract before any mutation; never write a face/unknown shader.
            if (!Supports(material)) return 0;
            material.SetFloat("_LightingUVContribution", uv);
            material.SetFloat("_BodyLightingContribution", body);
            material.SetFloat("_LightingUVHorizontalAdjust", horizontal);
            return 3;
        }

        internal bool Matches(Material material)
        {
            return Supports(material) && material.GetFloat("_LightingUVContribution") == uv &&
                material.GetFloat("_BodyLightingContribution") == body &&
                material.GetFloat("_LightingUVHorizontalAdjust") == horizontal;
        }
    }
}
