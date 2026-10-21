using System;
using Il2CppInterop.Runtime;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using SlimeBridge.Shared;
using UnityEngine;
namespace SlimeBridge.SR2
{
    internal static class Sr2ImportedAnimation
    {
        internal static void Attach(GameObject root, NativeMeshDocument model, Sr2ImportedVisualMarker owner)
        {
            if (model.Clips.Length == 0) return;
            var playback = root.AddComponent<Animation>();
            playback.playAutomatically = false;
            playback.cullingType = AnimationCullingType.AlwaysAnimate;
            foreach (var source in model.Clips)
            {
                if (source.Name != model.DefaultClip) continue; // Only the model-selected clip, no guessed graph.
                var clip = owner.Own(new AnimationClip());
                clip.name = source.Name; clip.legacy = true; clip.frameRate = source.SampleRate;
                clip.wrapMode = source.Loop ? WrapMode.Loop : WrapMode.ClampForever;
                foreach (var channel in source.Channels)
                {
                    if (root.transform.Find(channel.Path) == null) throw new FormatException("unresolved source animation path " + channel.Path);
                    var property = channel.Property == "local_rotation" ? "localRotation" :
                        channel.Property == "local_position" ? "localPosition" : "localScale";
                    var width = channel.Property == "local_rotation" ? 4 : 3;
                    for (var component = 0; component < width; component++)
                    {
                        var samples = NativeMeshAnimationCurves.Build(source.Times, channel.Values, component,
                            source.Duration, source.Loop, width == 4);
                        var keys = new Il2CppStructArray<Keyframe>(samples.Length);
                        for (var i = 0; i < samples.Length; i++)
                            keys[i] = new Keyframe(samples[i].Time, samples[i].Value, samples[i].InTangent, samples[i].OutTangent);
                        clip.SetCurve(channel.Path, Il2CppType.Of<Transform>(), property + "." + "xyzw"[component], new AnimationCurve(keys));
                    }
                }
                clip.EnsureQuaternionContinuity();
                playback.AddClip(clip, clip.name);
                if (clip.name == model.DefaultClip) playback.clip = clip;
            }
            if (playback.clip == null) throw new FormatException("declared default clip unavailable");
            owner.DefaultClip = model.DefaultClip;
        }
    }
}
