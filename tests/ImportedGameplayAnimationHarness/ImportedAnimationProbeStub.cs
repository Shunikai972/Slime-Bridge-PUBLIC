using UnityEngine;

namespace SlimeBridge.SR1
{
    internal static class ImportedAnimationProbe
    {
        internal static List<object> Array(object value, int maximum) => value as List<object> ?? throw new FormatException("list required");
        internal static Dictionary<string, object> Map(object value) => value as Dictionary<string, object> ?? throw new FormatException("object required");
        internal static AnimationClip BuildClip(GameObject root, Dictionary<string, object> source, float? sampleTime,
            bool playback, out float duration, out bool loop)
        {
            duration = Convert.ToSingle(source["duration"], System.Globalization.CultureInfo.InvariantCulture);
            loop = Convert.ToBoolean(source["loop"], System.Globalization.CultureInfo.InvariantCulture);
            return new AnimationClip { name = (string)source["name"], legacy = true, length = duration };
        }
    }
}
