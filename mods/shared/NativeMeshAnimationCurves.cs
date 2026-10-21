using System;
namespace SlimeBridge.Shared
{
    public struct NativeMeshCurveKey
    {
        public float Time, Value, InTangent, OutTangent;
    }
    /// <summary>Linear authored samples, no Animator graph or guessed clip selection.</summary>
    public static class NativeMeshAnimationCurves
    {
        public static NativeMeshCurveKey[] Build(float[] times, float[][] values, int component,
            float duration, bool loop, bool rotation)
        {
            var count = times.Length;
            bool extend = times[count - 1] < duration;
            var result = new NativeMeshCurveKey[count + (extend ? 1 : 0)];
            float previousSign = 1;
            for (int i = 0; i < count; i++)
            {
                float sign = 1;
                if (rotation && i > 0)
                {
                    float dot = 0;
                    for (int j = 0; j < 4; j++) dot += values[i - 1][j] * previousSign * values[i][j];
                    sign = dot < 0 ? -1 : 1;
                }
                result[i] = new NativeMeshCurveKey {Time = times[i], Value = values[i][component] * sign};
                previousSign = sign;
            }
            float endpoint = result[count - 1].Value;
            if (loop)
            {
                float sign = 1;
                if (rotation)
                {
                    float dot = 0;
                    for (int j = 0; j < 4; j++) dot += values[count - 1][j] * previousSign * values[0][j];
                    sign = dot < 0 ? -1 : 1;
                }
                endpoint = values[0][component] * sign;
                // A looping legacy clip wraps to its start at duration. Close that
                // boundary on the existing key; never create duplicate-time keys.
                if (!extend) result[count - 1].Value = endpoint;
            }
            if (extend)
                result[count] = new NativeMeshCurveKey {Time = duration, Value = endpoint};
            for (int i = 0; i < result.Length; i++)
            {
                if (i > 0) result[i].InTangent = Slope(result[i-1], result[i]);
                if (i + 1 < result.Length) result[i].OutTangent = Slope(result[i], result[i+1]);
            }
            return result;
        }
        private static float Slope(NativeMeshCurveKey a, NativeMeshCurveKey b)
        {
            double slope = ((double)b.Value - a.Value) / ((double)b.Time - a.Time);
            if (double.IsNaN(slope) || double.IsInfinity(slope) || Math.Abs(slope) > float.MaxValue)
                throw new FormatException("source animation curve tangent overflow");
            return (float)slope;
        }
    }
}
