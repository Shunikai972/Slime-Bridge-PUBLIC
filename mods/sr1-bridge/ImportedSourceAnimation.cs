using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Native legacy playback on the isolated imported visual, never on the actor.
    /// Integration: pass document["source_animation"] to TryAttach before publishing a template.
    /// A declared descriptor failure MUST refuse the visual; absence is the loader's decision.
    /// Suppress SlimeBridgeJiggle when attached. Instantiate the inactive visual under the actor,
    /// then activate it: OnEnable starts the default clip with an independent native clock.
    /// Gameplay hooks may call TryPlay/TryCrossFade with exact decoded idle/pounce/bite names.
    /// This class deliberately makes no unverified actor-state/Animator assumptions.</summary>
    internal sealed class ImportedSourceAnimation : MonoBehaviour
    {
        [SerializeField] private Animation? playback;
        [SerializeField] private AnimationClip[] clips = new AnimationClip[0];
        [SerializeField] private string defaultClip = "";
        // Unity Instantiate copies serialized clip references, but MUST NOT copy ownership.
        // No initializer: clone ownership starts empty; only the template owns native clips.
        [NonSerialized] private AnimationClip[]? ownedClips;

        /// <summary>The exact default clip declared by the source cache. Gameplay routing must not
        /// substitute a species alias when this value differs.</summary>
        internal string DefaultClipName => defaultClip;

        /// <summary>Loader gate: false means refuse the visual, never fall back to an alias.
        /// authored is true for ANY declaration (including malformed values). With no declaration
        /// this is a no-op so the existing probe/jiggle path can remain unchanged.</summary>
        internal static bool TryAttachDeclared(GameObject root, Dictionary<string, object> document,
            out bool authored, out string problem)
        {
            problem = "";
            authored = document.TryGetValue("source_animation", out var value);
            if (!authored) return true;
            if (!(value is Dictionary<string, object> descriptor))
            {
                problem = "source animation refused: descriptor object required";
                return false;
            }
            return TryAttach(root, descriptor, out problem);
        }

        internal static bool TryAttach(GameObject root, Dictionary<string, object> descriptor, out string problem)
        {
            problem = "";
            var built = new List<AnimationClip>();
            Animation? native = null;
            ImportedSourceAnimation? player = null;
            try
            {
                if (root == null || descriptor == null || root.activeSelf ||
                    root.GetComponent<Animation>() != null || root.GetComponent<ImportedSourceAnimation>() != null)
                    throw new FormatException("inactive isolated visual without animation components required");
                var selected = descriptor["default_clip"] as string;
                if (selected == null || selected.Length == 0 || selected.Length > 256)
                    throw new FormatException("default source clip name required");
                var sources = ImportedAnimationProbe.Array(descriptor["clips"], 16);
                var names = new HashSet<string>(StringComparer.Ordinal);
                var componentBudget = 0;
                foreach (var raw in sources)
                {
                    var source = ImportedAnimationProbe.Map(raw);
                    var name = source["name"] as string;
                    if (name == null || !names.Add(name)) throw new FormatException("duplicate source clip name");
                    foreach (var track in ImportedAnimationProbe.Array(source["channels"], 256))
                    {
                        var channel = ImportedAnimationProbe.Map(track);
                        var count = ImportedAnimationProbe.Array(channel["values"], 2048).Count;
                        componentBudget += count * (channel["property"] as string == "local_rotation" ? 4 : 3);
                        if (componentBudget > 1048576) throw new FormatException("source animation component budget exceeded");
                    }
                    var clip = ImportedAnimationProbe.BuildClip(root, source, null, true, out _, out var loop);
                    built.Add(clip);
                    // Non-loop clips hold their authored endpoint until the verified gameplay hook
                    // requests another action. No invented return-to-idle transition or root motion.
                    clip.wrapMode = loop ? WrapMode.Loop : WrapMode.ClampForever;
                }
                if (!names.Contains(selected)) throw new FormatException("default source clip is not declared");
                native = root.AddComponent<Animation>();
                native.playAutomatically = false;
                native.animatePhysics = false;
                native.cullingType = AnimationCullingType.AlwaysAnimate;
                foreach (var clip in built) native.AddClip(clip, clip.name);
                player = root.AddComponent<ImportedSourceAnimation>();
                player.playback = native;
                player.clips = built.ToArray();
                player.defaultClip = selected;
                native.clip = FindClip(player.clips, selected);
                player.ownedClips = player.clips;
                return true;
            }
            catch (Exception ex)
            {
                // No ownership is transferred until setup completes. Partial native clips and
                // components are disposed even when Unity AddClip/AddComponent throws.
                if (player != null)
                {
                    player.ownedClips = null;
                    UnityEngine.Object.Destroy(player);
                }
                if (native != null) UnityEngine.Object.Destroy(native);
                foreach (var clip in built) UnityEngine.Object.Destroy(clip);
                problem = "source animation refused: " + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        internal bool TryPlay(string clipName, out string problem)
        {
            if (!TryState(clipName, out var state, out problem)) return false;
            state!.time = 0f;
            state.speed = 1f;
            if (!playback!.Play(clipName, PlayMode.StopAll))
            {
                problem = "native source clip Play refused";
                return false;
            }
            return true;
        }

        /// <summary>Prepare every declared state while attaching the imported visual, so the
        /// first native gameplay hook never has to lazily register an Animation state.</summary>
        internal bool PrepareForGameplay(out string problem)
        {
            problem = "";
            if (playback == null || clips == null || clips.Length == 0)
            {
                problem = "source animation playback is unavailable";
                return false;
            }
            foreach (var clip in clips)
            {
                if (clip == null || !TryState(clip.name, out _, out problem))
                {
                    if (problem.Length == 0) problem = "source animation contains an unavailable clip";
                    return false;
                }
            }
            return true;
        }

        /// <summary>Duration lookup for bounded visual state transitions. This never changes the
        /// native clock and allocates nothing.</summary>
        internal bool TryGetClipLength(string clipName, out float length)
        {
            length = 0f;
            var clip = clipName == null ? null : FindClip(clips, clipName);
            if (clip == null || float.IsNaN(clip.length) || float.IsInfinity(clip.length) || clip.length < 0f)
                return false;
            length = clip.length;
            return true;
        }

        internal bool TryCrossFade(string clipName, float seconds, out string problem)
        {
            if (float.IsNaN(seconds) || float.IsInfinity(seconds) || seconds < 0f || seconds > 10f)
            {
                problem = "source fade duration outside bounds";
                return false;
            }
            if (seconds == 0f) return TryPlay(clipName, out problem);
            if (!TryState(clipName, out var state, out problem)) return false;
            state!.time = 0f;
            state.speed = 1f;
            playback!.CrossFade(clipName, seconds, PlayMode.StopAll);
            return true;
        }

        private bool TryState(string clipName, out AnimationState? state, out string problem)
        {
            state = null;
            problem = "";
            var clip = clipName == null ? null : FindClip(clips, clipName);
            if (playback == null || clip == null)
            {
                problem = "unknown or unavailable source clip";
                return false;
            }
            state = playback[clip.name];
            if (state == null)
            {
                // Unity does not necessarily carry runtime AddClip states through Instantiate. The
                // serialized clip reference is shared and immutable, so register it on this
                // actor's own Animation instead of rebuilding or copying any clip.
                playback.AddClip(clip, clip.name);
                state = playback[clip.name];
            }
            if (state == null)
            {
                problem = "unknown or unavailable source clip";
                return false;
            }
            return true;
        }

        /// <summary>LAB proof read-back: the currently enabled source state and its unwrapped time.</summary>
        internal bool TryReadPlayback(out string clipName, out float time, out float length)
        {
            clipName = "";
            time = 0f;
            length = 0f;
            if (playback == null) return false;
            foreach (var clip in clips)
            {
                if (clip == null) continue;
                var state = playback[clip.name];
                if (state == null || !state.enabled) continue;
                clipName = clip.name;
                time = state.time;
                length = clip.length;
                return true;
            }
            return false;
        }

        private static AnimationClip? FindClip(AnimationClip[] available, string name)
        {
            foreach (var clip in available)
                if (clip != null && string.Equals(clip.name, name, StringComparison.Ordinal)) return clip;
            return null;
        }

        private void OnEnable()
        {
            PrepareForGameplay(out _);
            TryPlay(defaultClip, out _);
        }
        private void OnDisable() { if (playback != null) playback.Stop(); }
        // Call before destroying/replacing a template, AFTER all its actors are destroyed.
        // Unity can omit OnDestroy on a GameObject which has never been active; templates are
        // intentionally inactive. Actors have no ownership, so this is safely a no-op on clones.
        internal void ReleaseOwnedClips()
        {
            var owned = ownedClips;
            ownedClips = null;
            if (owned != null)
                foreach (var clip in owned) if (clip != null) UnityEngine.Object.Destroy(clip);
        }

        private void OnDestroy() { ReleaseOwnedClips(); }
    }
}
