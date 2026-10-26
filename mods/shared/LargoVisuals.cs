#nullable enable
using System;
using System.Collections.Generic;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// The presentation half of a user Largo: which renderer slots carry which
    /// role, where the anchors are, and how the family effects attach.
    /// </summary>
    /// <remarks>
    /// Kept separate from <see cref="LargoContentRuntime"/> so the contract can be
    /// unit-tested without a scene, and separate from any save, inventory, portal
    /// or counterpart code so a visual change can never alter gameplay state.
    /// Nothing here writes to a save file, a registry, or a native game object.
    /// </remarks>
    internal sealed class LargoVisuals
    {
        private readonly string _model;
        private readonly string _family;
        private readonly Dictionary<string, int> _slotByRole;

        internal LargoVisuals(string model, string family, IReadOnlyList<string> slotRoles)
        {
            _model = model ?? throw new ArgumentNullException(nameof(model));
            _family = family ?? string.Empty;
            _slotByRole = new Dictionary<string, int>(StringComparer.Ordinal);
            for (var i = 0; i < slotRoles.Count; i++)
            {
                // First occurrence wins: the pipeline orders slots so the primary
                // role comes first and later suffixed slots are its variants.
                if (!_slotByRole.ContainsKey(slotRoles[i])) _slotByRole[slotRoles[i]] = i;
            }
        }

        internal string Model => _model;
        internal string Family => _family;
        internal int SlotCount => _slotByRole.Count;

        /// <summary>
        /// Validate that a prefab can carry this Largo's presentation before any of
        /// it is applied. A prefab missing a required anchor is refused outright,
        /// because a missing anchor surfaces later as an effect playing at the world
        /// origin rather than as a clear error.
        /// </summary>
        internal bool TryValidate(IReadOnlyCollection<string> transformNames, out string reason)
        {
            if (transformNames == null || transformNames.Count == 0)
            {
                reason = "no transforms supplied";
                return false;
            }

            var present = new HashSet<string>(transformNames, StringComparer.Ordinal);
            var missing = new List<string>();
            var required = LargoContentRuntime.RequiredAnchors(_family);
            for (var i = 0; i < required.Count; i++)
            {
                if (!present.Contains(required[i])) missing.Add(required[i]);
            }

            if (missing.Count > 0)
            {
                reason = "missing anchors: " + string.Join(", ", missing);
                return false;
            }

            reason = string.Empty;
            return true;
        }

        /// <summary>Slot index for a role, or -1 when the Largo does not have it.</summary>
        internal int SlotFor(string role)
        {
            return _slotByRole.TryGetValue(role, out var index) ? index : -1;
        }

        /// <summary>
        /// The material name a role maps to, or null when the role is unknown. Using
        /// this instead of string concatenation keeps the model's naming in one place.
        /// </summary>
        internal string? MaterialNameFor(string role)
        {
            return LargoContentRuntime.MaterialNameFor(_model, role);
        }

        /// <summary>
        /// Which presentation a family needs. Returned as data so the runtime decides
        /// how to build it, and so a family without a documented mechanic gets an
        /// empty plan instead of an invented effect.
        /// </summary>
        internal List<LargoEffectPlan> PlanEffects(IReadOnlyList<LargoContentRuntime.ManifestEntry> manifest)
        {
            var effects = LargoContentRuntime.EffectsForFamily(manifest, _family);
            var plan = new List<LargoEffectPlan>(effects.Count);
            for (var i = 0; i < effects.Count; i++)
            {
                var entry = effects[i];
                plan.Add(new LargoEffectPlan(entry.Id, entry.Kind, entry.Anchor, entry.Looping, entry.DurationSeconds));
            }
            return plan;
        }
    }

    /// <summary>One effect to attach, described without any engine type.</summary>
    internal readonly struct LargoEffectPlan
    {
        internal readonly string Id, Kind, Anchor;
        internal readonly bool Looping;
        internal readonly float DurationSeconds;

        internal LargoEffectPlan(string id, string kind, string anchor, bool looping, float durationSeconds)
        {
            Id = id; Kind = kind; Anchor = anchor;
            Looping = looping; DurationSeconds = durationSeconds;
        }
    }
}
