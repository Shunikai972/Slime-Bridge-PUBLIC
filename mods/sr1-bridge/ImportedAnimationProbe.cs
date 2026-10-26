using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>LAB-only, single source-lattice pose sampled by Unity's legacy clip engine.
    /// This is not an Animator graph, continuous playback or gameplay animation adapter.</summary>
    internal static class ImportedAnimationProbe
    {
        internal static bool TrySample(GameObject root, Dictionary<string, object> document, out string problem)
        {
            problem = "";
            if (!BridgeEntry.LabRestPoseRequested || !document.TryGetValue("lab_source_clip", out var value)) return true;
            AnimationClip? temporary = null;
            try
            {
                var request = Map(value);
                var sampleTime = Number(request["sample_time"]);
                temporary = BuildClip(root, Map(request["clip"]), sampleTime, false, out _, out _);
                temporary.SampleAnimation(root, sampleTime);
                return true;
            }
            catch (Exception ex)
            {
                problem = "LAB source clip refused: " + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
            finally
            {
                if (temporary != null) UnityEngine.Object.Destroy(temporary);
            }
        }

        /// <summary>Playback uses linear component tangents between decoded source samples,
        /// not original between-key interpolation. A missing duration endpoint holds the last
        /// source pose, preserving the declared loop period without synthesizing motion.</summary>
        internal static AnimationClip BuildClip(GameObject root, Dictionary<string, object> source,
            float? sampleTime, bool playback, out float duration, out bool loop)
        {
            AnimationClip? temporary = null;
            try
            {
                duration = playback ? PlaybackNumber(source["duration"], 0.0, 120.0) : Number(source["duration"]);
                var sourceDuration = playback ? JsonNumber(source["duration"]) : duration;
                loop = false;
                if (playback)
                {
                    if (!(source["loop"] is bool sourceLoop)) throw new FormatException("source loop boolean required");
                    loop = sourceLoop;
                }
                var rate = playback ? PlaybackNumber(source["sample_rate"], 0.0, 240.0) : Number(source["sample_rate"]);
                var name = source["name"] as string;
                if (root == null || name == null || name.Length == 0 || name.Length > 256 || sampleTime < 0f ||
                    duration < 0f || duration > 120f || rate <= 0f || rate > 240f || (playback && duration == 0f))
                    throw new FormatException("invalid source clip metadata");
                var rawTimes = Array(source["times"], 2048);
                var times = new float[rawTimes.Count];
                var onLattice = false;
                for (var i = 0; i < times.Length; i++)
                {
                    times[i] = playback ? PlaybackNumber(rawTimes[i], 0.0, sourceDuration) : Number(rawTimes[i]);
                    if (times[i] < 0f || times[i] > duration + (playback ? 0f : 0.00001f) ||
                        (i == 0 && times[i] != 0f) || (i > 0 && times[i] - times[i - 1] < 0.000001f))
                        throw new FormatException("invalid source clip times");
                    if (sampleTime.HasValue && Math.Abs(times[i] - sampleTime.Value) <= 0.000001f) onLattice = true;
                }
                if (sampleTime.HasValue && (!onLattice || sampleTime > times[times.Length - 1])) throw new FormatException("sample must be on the source lattice");
                var channels = Array(source["channels"], 256);
                var seen = new HashSet<string>(StringComparer.Ordinal);
                temporary = new AnimationClip { name = name, legacy = true, frameRate = rate };
                foreach (var raw in channels)
                {
                    var channel = Map(raw);
                    var path = channel["path"] as string;
                    var property = channel["property"] as string;
                    if (path == null || path.Length == 0 || path.Length > 1024 || (playback && !IsRelativePath(path)) || root.transform.Find(path) == null ||
                        !seen.Add(path + "|" + property)) throw new FormatException("unresolved or duplicate source channel");
                    var nativeProperty = property == "local_position" ? "localPosition" :
                        property == "local_rotation" ? "localRotation" : property == "local_scale" ? "localScale" : null;
                    if (nativeProperty == null) throw new FormatException("unsupported source channel");
                    var width = property == "local_rotation" ? 4 : 3;
                    var rows = Array(channel["values"], 2048);
                    if (rows.Count != times.Length) throw new FormatException("source channel sample count mismatch");
                    var vectors = new float[rows.Count][];
                    for (var frame = 0; frame < rows.Count; frame++)
                    {
                        var row = Array(rows[frame], 4);
                        if (row.Count != width) throw new FormatException("source channel width mismatch");
                        vectors[frame] = new float[width];
                        var norm = 0.0;
                        for (var axis = 0; axis < width; axis++)
                        {
                            var component = playback ? PlaybackNumber(row[axis], -100000.0, 100000.0) : Number(row[axis]);
                            if (Math.Abs(component) > 100000f) throw new FormatException("source component exceeds limit");
                            vectors[frame][axis] = component;
                            norm += (double)component * component;
                        }
                        if (width == 4 && Math.Abs(norm - 1.0) > 0.001)
                            throw new FormatException("source quaternion is not normalized");
                        if (playback && width == 4 && frame > 0)
                        {
                            var dot = 0.0;
                            for (var axis = 0; axis < width; axis++) dot += (double)vectors[frame - 1][axis] * vectors[frame][axis];
                            if (dot < 0.0)
                                for (var axis = 0; axis < width; axis++) vectors[frame][axis] = -vectors[frame][axis];
                        }
                    }
                    var tail = playback && duration > times[times.Length - 1];
                    for (var axis = 0; axis < width; axis++)
                    {
                        var keys = new Keyframe[times.Length + (tail ? 1 : 0)];
                        for (var i = 0; i < times.Length; i++)
                        {
                            keys[i] = new Keyframe(times[i], vectors[i][axis]);
                            if (i > 0) keys[i].inTangent = (vectors[i][axis] - vectors[i - 1][axis]) / (times[i] - times[i - 1]);
                            if (i + 1 < times.Length) keys[i].outTangent = (vectors[i + 1][axis] - vectors[i][axis]) / (times[i + 1] - times[i]);
                        }
                        if (tail) keys[keys.Length - 1] = new Keyframe(duration, vectors[vectors.Length - 1][axis]);
                        temporary.SetCurve(path, typeof(Transform), nativeProperty + "." + "xyzw"[axis], new AnimationCurve(keys));
                    }
                }
                temporary.EnsureQuaternionContinuity();
                return temporary;
            }
            catch
            {
                if (temporary != null) UnityEngine.Object.Destroy(temporary);
                throw;
            }
        }

        private static bool IsRelativePath(string path)
        {
            if (path.IndexOf('\\') >= 0 || path.IndexOf(':') >= 0 || path.IndexOf('\0') >= 0) return false;
            foreach (var part in path.Split('/'))
                if (part.Length == 0 || part == "." || part == "..") return false;
            return true;
        }

        internal static Dictionary<string, object> Map(object value) => value as Dictionary<string, object> ??
            throw new FormatException("source clip object required");

        internal static List<object> Array(object value, int maximum)
        {
            var list = value as List<object>;
            if (list == null || list.Count == 0 || list.Count > maximum) throw new FormatException("source clip array outside bounds");
            return list;
        }

        private static double JsonNumber(object value)
        {
            var number = value is long integer ? (double)integer : value is double real ? real : double.NaN;
            if (double.IsNaN(number) || double.IsInfinity(number)) throw new FormatException("finite JSON number required");
            return number;
        }

        private static float PlaybackNumber(object value, double minimum, double maximum)
        {
            var raw = JsonNumber(value);
            var number = (float)raw;
            // Validate BEFORE narrowing: a float cast can hide tiny negatives/out-of-budget values.
            if (raw < minimum || raw > maximum || float.IsInfinity(number) || (raw != 0.0 && number == 0f))
                throw new FormatException("source number outside representable bounds");
            return number;
        }

        internal static float Number(object value)
        {
            // MiniJson returns integer tokens as long and fractional tokens as double.
            var number = value is long integer ? (float)integer : value is double real ? (float)real : float.NaN;
            if (float.IsNaN(number) || float.IsInfinity(number)) throw new FormatException("finite JSON number required");
            return number;
        }
    }
}
