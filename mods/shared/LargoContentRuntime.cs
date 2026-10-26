#nullable enable
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Reads the art pipeline's manifests and turns them into the small contract a
    /// runtime needs: which material slots a Largo carries, where its anchors are,
    /// which VFX belong to its family, and which audio events it can raise.
    /// </summary>
    /// <remarks>
    /// This layer is deliberately inert with respect to gameplay. It never touches
    /// saves, BridgeHost, counterpart creation, inventory or portals; it only
    /// describes a Largo's presentation so a visual/audio adapter can act on it.
    /// Every lookup fails closed: an unreadable or malformed manifest yields no
    /// content rather than a partially applied effect set.
    /// </remarks>
    internal static class LargoContentRuntime
    {
        /// <summary>Material slot roles the pipeline guarantees, in slot order.</summary>
        internal static readonly string[] SlotRoles =
        {
            "BODY", "BODY_DETAIL", "CLOUD", "ACCESSORY", "EMISSIVE", "FX"
        };

        /// <summary>Anchors every exported Largo carries, whatever its family.</summary>
        internal static readonly string[] GenericAnchors =
        {
            "FX_CENTER", "FX_BODY", "FX_MOUTH", "FX_TRAIL", "FX_TOP",
            "SFX_IDLE", "SFX_EAT", "SFX_HAPPY", "SFX_LAND", "SFX_SPECIAL"
        };

        /// <summary>A parsed effect or sound entry, kept loose because the manifests evolve.</summary>
        internal readonly struct ManifestEntry
        {
            public readonly string Id, Family, Kind, Anchor;
            public readonly bool Looping;
            public readonly float DurationSeconds;
            public ManifestEntry(string id, string family, string kind, string anchor, bool looping, float durationSeconds)
            {
                Id = id; Family = family; Kind = kind; Anchor = anchor;
                Looping = looping; DurationSeconds = durationSeconds;
            }
        }

        /// <summary>
        /// Load the VFX manifest. Returns an empty list when the file is missing or
        /// malformed: a bad manifest must not half-populate a Largo's effects.
        /// </summary>
        internal static IReadOnlyList<ManifestEntry> LoadVfxManifest(string path)
        {
            var entries = new List<ManifestEntry>();
            var root = TryParseJson(path);
            if (root == null) return entries;

            var effects = MinimalJson.Get(root, "effects");
            foreach (var effect in MinimalJson.AsArray(effects))
            {
                var id = MinimalJson.AsString(MinimalJson.Get(effect, "id"));
                if (id.Length == 0) continue;
                entries.Add(new ManifestEntry(
                    id,
                    MinimalJson.AsString(MinimalJson.Get(effect, "family")),
                    MinimalJson.AsString(MinimalJson.Get(effect, "kind")),
                    MinimalJson.AsString(MinimalJson.Get(effect, "anchor")),
                    MinimalJson.AsBool(MinimalJson.Get(effect, "looping")),
                    MinimalJson.AsFloat(MinimalJson.Get(effect, "duration_s"))));
            }
            return entries;
        }

        /// <summary>Load the audio manifest as event id to unity-ready file path.</summary>
        internal static IReadOnlyDictionary<string, string> LoadAudioManifest(string path)
        {
            var map = new Dictionary<string, string>(StringComparer.Ordinal);
            var root = TryParseJson(path);
            if (root == null) return map;

            var sounds = MinimalJson.AsObject(MinimalJson.Get(root, "sounds"));
            foreach (var sound in sounds)
            {
                var unity = MinimalJson.AsString(MinimalJson.Get(sound.Value, "unity_ready"));
                if (unity.Length == 0) continue;
                map[sound.Key] = unity;
            }
            return map;
        }

        /// <summary>Effects belonging to one family, in manifest order.</summary>
        internal static List<ManifestEntry> EffectsForFamily(IReadOnlyList<ManifestEntry> all, string family)
        {
            var result = new List<ManifestEntry>();
            for (var i = 0; i < all.Count; i++)
            {
                if (string.Equals(all[i].Family, family, StringComparison.OrdinalIgnoreCase))
                {
                    result.Add(all[i]);
                }
            }
            return result;
        }

        /// <summary>
        /// Resolve a slot role to its material name for one model, e.g.
        /// ("rad_slumber", "CLOUD") -> "rad_slumber_CLOUD". Returns null when the role
        /// is not part of the convention, so callers cannot silently address a slot
        /// that the pipeline never produces.
        /// </summary>
        internal static string? MaterialNameFor(string model, string role)
        {
            if (string.IsNullOrEmpty(model) || string.IsNullOrEmpty(role)) return null;
            var known = false;
            for (var i = 0; i < SlotRoles.Length; i++)
            {
                // Slots beyond the first of a role are suffixed `_2`, `_3`, ...
                if (string.Equals(SlotRoles[i], role, StringComparison.Ordinal)) { known = true; break; }
                if (role.StartsWith(SlotRoles[i] + "_", StringComparison.Ordinal) &&
                    int.TryParse(role.Substring(SlotRoles[i].Length + 1), NumberStyles.Integer,
                                 CultureInfo.InvariantCulture, out _))
                {
                    known = true;
                    break;
                }
            }
            return known ? model + "_" + role : null;
        }

        /// <summary>
        /// True when a transform name is an anchor this pipeline exports. Used to
        /// validate a prefab before trusting it, rather than discovering a missing
        /// anchor while an effect is already playing.
        /// </summary>
        internal static bool IsAnchor(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            if (name.StartsWith("FX_", StringComparison.Ordinal) ||
                name.StartsWith("SFX_", StringComparison.Ordinal))
            {
                return true;
            }
            return false;
        }

        /// <summary>Anchors that the given model must provide; family extras are additive.</summary>
        internal static List<string> RequiredAnchors(string family)
        {
            var anchors = new List<string>(GenericAnchors);
            switch (family)
            {
                case "angler": anchors.Add("FX_LURE"); break;
                case "slumber": anchors.Add("FX_CLOUD"); break;
                case "mosaic": anchors.Add("FX_CRYSTAL"); break;
                case "twin":
                    anchors.Add("FX_ORBIT");
                    anchors.Add("FX_RIFT_ENTRY");
                    anchors.Add("FX_RIFT_EXIT");
                    break;
                case "quantum":
                    // Arcs are placed around the body; indices are fixed so the
                    // runtime never has to guess how many exist.
                    for (var i = 0; i < 6; i++) anchors.Add("FX_QUANTUM_ARC_" + i.ToString("00", CultureInfo.InvariantCulture));
                    break;
            }
            return anchors;
        }

        /// <summary>
        /// Parse a manifest, or return null. Callers treat null as "no content", so a
        /// read or parse failure can never produce a partially applied effect set.
        /// </summary>
        private static object? TryParseJson(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return null;
            try
            {
                return MinimalJson.Parse(File.ReadAllText(path));
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
