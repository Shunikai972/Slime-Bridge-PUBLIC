using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Resolves native SR1 meshes, materials and textures by name from the running game (nothing is copied or shipped).
    /// The whole world scene is loaded, so every asset the sanctuary kit names is already in memory.
    /// </summary>
    internal sealed class SanctuaryNative
    {
        private readonly Dictionary<string, Mesh> _meshes = new Dictionary<string, Mesh>(StringComparer.Ordinal);
        private readonly Dictionary<string, Material> _materials = new Dictionary<string, Material>(StringComparer.Ordinal);
        private readonly Dictionary<string, Texture> _textures = new Dictionary<string, Texture>(StringComparer.Ordinal);
        private bool _indexed;
        public readonly List<string> Missing = new List<string>();

        public int MeshCount => _meshes.Count;
        public int MaterialCount => _materials.Count;

        public void EnsureIndexed()
        {
            if (_indexed) return;
            Index(_meshes, Resources.FindObjectsOfTypeAll<Mesh>());
            Index(_materials, Resources.FindObjectsOfTypeAll<Material>());
            Index(_textures, Resources.FindObjectsOfTypeAll<Texture>());
            _indexed = true;
        }

        private static void Index<T>(Dictionary<string, T> into, T[] all) where T : UnityEngine.Object
        {
            foreach (var item in all)
            {
                if (item == null || string.IsNullOrEmpty(item.name) || (item.hideFlags & HideFlags.HideAndDontSave) == HideFlags.HideAndDontSave) continue;
                if (!into.ContainsKey(item.name)) into[item.name] = item;
            }
        }

        public Mesh? Mesh(string name)
        {
            EnsureIndexed();
            if (_meshes.TryGetValue(name, out var mesh)) return mesh;
            Missing.Add("mesh:" + name);
            return null;
        }

        public Material? Material(string name)
        {
            EnsureIndexed();
            if (_materials.TryGetValue(name, out var material)) return material;
            Missing.Add("material:" + name);
            return null;
        }

        public Texture? Texture(string name)
        {
            EnsureIndexed();
            return _textures.TryGetValue(name, out var texture) ? texture : null;
        }

        /// <summary>First native PuzzleSlot in the world: source of the fill FX and cue every statue shares.</summary>
        public PuzzleSlot? ReferenceSlot()
        {
            foreach (var slot in Resources.FindObjectsOfTypeAll<PuzzleSlot>())
                if (slot != null && slot.changeFX != null) return slot;
            return null;
        }

        /// <summary>A private copy of a native material (never edits the shared asset).</summary>
        public Material? Clone(string name)
        {
            var source = Material(name);
            return source == null ? null : new Material(source) { name = source.name + " (SlimeBridge)" };
        }

        public static Color ToColor(float[]? v) => v == null || v.Length < 4 ? Color.white : new Color(v[0], v[1], v[2], v[3]);
    }
}
