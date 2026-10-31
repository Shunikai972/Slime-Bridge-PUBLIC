using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Gives an imported actor's visual the native squash/stretch spring simulation.
    /// SR1 slimes deform (hop squash, vacpack stretch, held-by-vac damping) through a
    /// <c>RubberBoneEffect</c> that lives on the appearance root and springs the
    /// <c>bone_jiggle_*</c> bones of THAT root's rig. The imported visual brings its own rig with the
    /// same bone names, but the placeholder's effect only moves the hidden placeholder bones, so the
    /// visible model stayed rigid. This copies the placeholder effect's settings and bone intensities
    /// onto a new effect on the imported visual root, bound to the imported bones and skinned body.
    /// Idempotent and fail-open: no placeholder effect or no matching bone is a clean no-op.
    /// </summary>
    internal static class ImportedRubberBone
    {
        private const int MaxBones = 64;
        private static bool _templateLogged;

        public static bool EnsureAttached(GameObject instance, GameObject visual, out string problem)
        {
            problem = "";
            if (instance == null || visual == null) { problem = "no instance/visual"; return false; }
            if (visual.GetComponent<RubberBoneEffect>() != null) return true;

            RubberBoneEffect template = null;
            foreach (var effect in instance.GetComponentsInChildren<RubberBoneEffect>(true))
            {
                if (effect == null || effect.transform.IsChildOf(visual.transform)) continue;
                if (effect.bones != null && effect.bones.Length > 0) { template = effect; break; }
            }
            if (template == null) { problem = "no native RubberBoneEffect template on the actor"; return false; }

            var entries = new List<RubberBoneEffect.BoneEntry>();
            foreach (var source in template.bones)
            {
                if (source == null || source.trans == null || entries.Count >= MaxBones) continue;
                var target = FindByName(visual.transform, source.trans.name);
                if (target == null) continue;
                entries.Add(new RubberBoneEffect.BoneEntry { trans = target, intensity = source.intensity });
            }
            if (entries.Count == 0) { problem = "imported rig has none of the native jiggle bones"; return false; }

            SkinnedMeshRenderer body = null;
            foreach (var skinned in visual.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                if (skinned == null) continue;
                if (body == null) body = skinned;
                if (skinned.gameObject.name.StartsWith("body", StringComparison.OrdinalIgnoreCase)) { body = skinned; break; }
            }

            if (!_templateLogged)
            {
                _templateLogged = true;
                var rows = new List<string>();
                foreach (var source in template.bones) if (source != null && source.trans != null) rows.Add(source.trans.name + ":" + source.intensity.ToString("R", System.Globalization.CultureInfo.InvariantCulture));
                UnityEngine.Debug.Log("[SlimeBridge] rubber template " + template.Presets + " intensity=" + template.effectIntensity + " gravity=" + template.gravity +
                    " damping=" + template.damping + " mass=" + template.mass + " stiffness=" + template.stiffness + " bones=" + string.Join(",", rows));
            }
            var added = visual.AddComponent<RubberBoneEffect>();
            added.Presets = template.Presets;
            added.effectIntensity = template.effectIntensity;
            added.gravity = template.gravity;
            added.damping = template.damping;
            added.mass = template.mass;
            added.stiffness = template.stiffness;
            added.unscaledTime = template.unscaledTime;
            added.bones = entries.ToArray();
            added.skinRenderer = body;
            return true;
        }

        private static Transform FindByName(Transform root, string name)
        {
            if (root == null || string.IsNullOrEmpty(name)) return null;
            var stack = new Stack<Transform>();
            stack.Push(root);
            var visited = 0;
            while (stack.Count > 0 && visited++ < 2048)
            {
                var current = stack.Pop();
                if (current == null) continue;
                if (current != root && string.Equals(current.name, name, StringComparison.Ordinal)) return current;
                for (var i = 0; i < current.childCount; i++) stack.Push(current.GetChild(i));
            }
            return null;
        }
    }
}
