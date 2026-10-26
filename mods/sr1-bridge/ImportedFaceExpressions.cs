using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// The species' own facial expressions (scared, hungry, alarmed, awed, happy, chewing ...).
    ///
    /// SR1 drives a slime's face with <c>SlimeFaceAnimator</c> -> <c>SlimeAppearanceApplicator.SetExpression</c>, which swaps the
    /// eyes and mouth materials of the *placeholder's* face renderers; the imported visual has its own face renderer, so the
    /// expressions never reached it. SR2's <c>SlimeExpression</c> starts with the same 21 values in the same order as SR1's,
    /// and each species' SR2 face lists, per expression, the eyes and mouth atlases. The converter writes them to
    /// <c>face-expressions.json</c> (+ the atlas PNGs, manifest-hashed); this swaps the face atlases whenever the game sets an
    /// expression. Fail-open: no data for a species (or an expression) leaves the face as it is.
    /// </summary>
    internal static class ImportedFaceExpressions
    {
        private const string AtlasProperty = "_FaceAtlas";

        internal sealed class Part
        {
            internal Texture2D? Atlas;
            internal Vector2 Scale = Vector2.one, Offset = Vector2.zero;
        }

        internal sealed class Set
        {
            internal readonly Dictionary<int, Part?[]> ByExpression = new Dictionary<int, Part?[]>();   // [0] eyes, [1] mouth
        }

        private static readonly Dictionary<string, Set?> Loaded = new Dictionary<string, Set?>(StringComparer.Ordinal);
        private static Harmony? _harmony;

        internal static void EnsurePatched()
        {
            if (_harmony != null) return;
            try
            {
                var harmony = new Harmony("slimebridge.sr1.imported-face");
                harmony.Patch(AccessTools.Method(typeof(SlimeAppearanceApplicator), "SetExpression"),
                    prefix: new HarmonyMethod(typeof(ImportedFaceExpressions), nameof(SetExpressionPrefix)),
                    postfix: new HarmonyMethod(typeof(ImportedFaceExpressions), nameof(SetExpressionPostfix)));
                _harmony = harmony;
                UnityEngine.Debug.Log("[SlimeBridge] imported face expressions patched");
            }
            catch (Exception ex) { UnityEngine.Debug.LogError("[SlimeBridge] face expression patch failed: " + ex.GetType().Name); }
        }

        /// <summary>The native SetExpression swaps the face materials of the placeholder's renderers. An imported actor's placeholder is
        /// suppressed and its applicator disabled, so its Appearance/Face/renderers can be missing: the native body then threw a
        /// NullReferenceException on every frame the face animator ran (tens of thousands per session on spawned Twin Largos). The
        /// imported face is driven by the postfix, so the native body is skipped for imported actors and for an applicator with no
        /// appearance or face to read.</summary>
        private static bool SetExpressionPrefix(SlimeAppearanceApplicator __instance)
        {
            try
            {
                if (__instance == null) return false;
                if (__instance.Appearance == null || __instance.Appearance.Face == null) return false;
                return string.IsNullOrEmpty(ImportedSpawner.ResolveImportedCanonical(__instance));
            }
            catch (Exception) { return true; }
        }

        private static void SetExpressionPostfix(SlimeAppearanceApplicator __instance, SlimeFace.SlimeExpression slimeExpression)
        {
            try
            {
                var canonical = ImportedSpawner.ResolveImportedCanonical(__instance);
                if (string.IsNullOrEmpty(canonical)) return;
                var identifiable = __instance.GetComponentInParent<Identifiable>();
                var visual = identifiable != null ? ImportedSpawner.FindImportedVisual(identifiable.gameObject) : null;
                if (visual == null) return;
                var state = visual.GetComponent<ImportedFaceState>() ?? visual.AddComponent<ImportedFaceState>();
                state.Apply(canonical, (int)slimeExpression);
            }
            catch (Exception) { /* a face that does not change is better than an exception per frame */ }
        }

        internal static Set? Get(string canonical)
        {
            if (Loaded.TryGetValue(canonical, out var cached)) return cached;
            Set? set = null;
            try { set = Load(canonical); }
            catch (Exception ex) { UnityEngine.Debug.Log("[SlimeBridge] face expressions load failed: " + ex.GetType().Name); }
            Loaded[canonical] = set;
            return set;
        }

        private static Set? Load(string canonical)
        {
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (repoRoot == null) return null;
            var cacheRoot = Path.Combine(repoRoot, "Lab", "AssetCache", "sr2-to-sr1");
            var directory = Path.Combine(cacheRoot, canonical);
            if (!LabSpecPathGuard.IsAllowedDirectory(cacheRoot, directory, out _)) return null;
            var manifestPath = Path.Combine(directory, "cache-manifest.json");
            var documentPath = Path.Combine(directory, "face-expressions.json");
            if (!File.Exists(manifestPath) || !File.Exists(documentPath)) return null;
            if (!LabSpecPathGuard.IsAllowedFile(directory, manifestPath, out _) || !LabSpecPathGuard.IsAllowedFile(directory, documentPath, out _)) return null;
            var manifest = MiniJson.Parse(File.ReadAllText(manifestPath)) as Dictionary<string, object>;
            var files = manifest != null && manifest.TryGetValue("files", out var f) ? f as Dictionary<string, object> : null;
            if (files == null || !string.Equals(manifest!.TryGetValue("canonical_id", out var c) ? c as string : null, canonical, StringComparison.Ordinal)) return null;
            if (!Verified(directory, files, "face-expressions.json")) return null;
            var document = MiniJson.Parse(File.ReadAllText(documentPath)) as Dictionary<string, object>;
            if (document == null || !string.Equals(document.TryGetValue("schema", out var schema) ? schema as string : null, "bridge-face-expressions", StringComparison.Ordinal)) return null;
            if (!(document.TryGetValue("expressions", out var rows) && rows is Dictionary<string, object> expressions)) return null;
            var textures = new Dictionary<string, Texture2D?>(StringComparer.Ordinal);
            var set = new Set();
            foreach (var entry in expressions)
            {
                if (!int.TryParse(entry.Key, out var id) || id < 0 || id > 64 || !(entry.Value is Dictionary<string, object> row)) continue;
                var parts = new Part?[2];
                parts[0] = ReadPart(directory, files, row, "eyes", textures);
                parts[1] = ReadPart(directory, files, row, "mouth", textures);
                set.ByExpression[id] = parts;
            }
            return set.ByExpression.Count > 0 ? set : null;
        }

        private static Part? ReadPart(string directory, Dictionary<string, object> files, Dictionary<string, object> row, string role, Dictionary<string, Texture2D?> textures)
        {
            if (!row.TryGetValue(role, out var raw) || !(raw is Dictionary<string, object> map)) return null;
            if (!map.TryGetValue("atlas", out var atlasName) || !(atlasName is string name) || name.Length == 0) return null;
            if (name.IndexOfAny(new[] { '/', '\\', ':' }) >= 0 || name != Path.GetFileName(name)) return null;
            if (!textures.TryGetValue(name, out var texture))
            {
                texture = null;
                if (Verified(directory, files, name))
                {
                    var loaded = new Texture2D(2, 2, TextureFormat.RGBA32, true);
                    if (loaded.LoadImage(File.ReadAllBytes(Path.Combine(directory, name))))
                    {
                        loaded.wrapMode = TextureWrapMode.Clamp;
                        loaded.name = Path.GetFileNameWithoutExtension(name);
                        UnityEngine.Object.DontDestroyOnLoad(loaded);
                        texture = loaded;
                    }
                    else UnityEngine.Object.Destroy(loaded);
                }
                textures[name] = texture;
            }
            if (texture == null) return null;
            var part = new Part { Atlas = texture };
            if (map.TryGetValue("scale", out var scale) && scale is List<object> s && s.Count >= 2) part.Scale = new Vector2(ToFloat(s[0], 1f), ToFloat(s[1], 1f));
            if (map.TryGetValue("offset", out var offset) && offset is List<object> o && o.Count >= 2) part.Offset = new Vector2(ToFloat(o[0], 0f), ToFloat(o[1], 0f));
            return part;
        }

        private static float ToFloat(object value, float fallback)
        {
            try { var x = Convert.ToSingle(value, System.Globalization.CultureInfo.InvariantCulture); return float.IsNaN(x) || float.IsInfinity(x) ? fallback : x; }
            catch (Exception) { return fallback; }
        }

        private static bool Verified(string directory, Dictionary<string, object> files, string name)
        {
            if (!files.TryGetValue(name, out var expected) || !(expected is string hash)) return false;
            var path = Path.Combine(directory, name);
            if (!LabSpecPathGuard.IsAllowedFile(directory, path, out _) || !File.Exists(path)) return false;
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
            {
                var sb = new StringBuilder(64);
                foreach (var b in sha.ComputeHash(stream)) sb.Append(b.ToString("x2"));
                return string.Equals(sb.ToString(), hash, StringComparison.OrdinalIgnoreCase);
            }
        }

        internal static void SetAtlas(Material material, Part? part)
        {
            if (material == null || !material.HasProperty(AtlasProperty)) return;
            // An expression without this role (a blink has no mouth) leaves it as it is, exactly like SlimeAppearanceApplicator.SetExpression
            // (`if (expressionFace.Mouth != null) ...`). Writing an empty atlas here made the mouth shader paint its whole area: the
            // enormous dark-red mouth that flashed on every blink and chew.
            if (part == null || part.Atlas == null) return;
            material.SetTexture(AtlasProperty, part.Atlas);
            material.SetTextureScale(AtlasProperty, part.Scale);
            material.SetTextureOffset(AtlasProperty, part.Offset);
        }
    }

    /// <summary>Per imported visual: remembers its face renderer and the last expression applied to it.</summary>
    internal sealed class ImportedFaceState : MonoBehaviour
    {
        private static int LogBudget = 200;
        private Renderer? _face;
        private int _last = -1;
        private bool _searched, _cloned;
        private readonly List<Material> _owned = new List<Material>();

        private void OnDestroy()
        {
            foreach (var material in _owned) if (material != null) UnityEngine.Object.Destroy(material);
            _owned.Clear();
        }

        internal void Apply(string canonical, int expression)
        {
            if (expression == _last) return;
            if (LogBudget > 0 && Environment.GetEnvironmentVariable("SLIMEBRIDGE_FACELOG") == "1") { LogBudget--; UnityEngine.Debug.Log("[SlimeBridge] face expression " + canonical + " -> " + expression + " t=" + UnityEngine.Time.time.ToString("F2")); }
            var set = ImportedFaceExpressions.Get(canonical);
            if (set == null || !set.ByExpression.TryGetValue(expression, out var parts)) return;
            if (!_searched)
            {
                _searched = true;
                foreach (var colors in GetComponentsInChildren<ImportedPartColors>(true))
                    if (colors != null && colors.SourceFace) { _face = colors.GetComponent<Renderer>(); break; }
            }
            if (_face == null) return;
            var materials = _face.sharedMaterials;
            if (!_cloned)
            {
                // the face materials are built once per species model and shared by every actor: give this actor its own
                // copies, or one slime's fear would be every slime's fear
                for (var i = 0; i < materials.Length; i++)
                {
                    var source = materials[i];
                    if (source == null || source.name == null || !source.name.StartsWith("SlimeBridgeSourceFace_", StringComparison.Ordinal)) continue;
                    if (!source.name.StartsWith("SlimeBridgeSourceFace_eyes", StringComparison.Ordinal) && !source.name.StartsWith("SlimeBridgeSourceFace_mouth", StringComparison.Ordinal)) continue;
                    var copy = new Material(source) { name = source.name };
                    materials[i] = copy;
                    _owned.Add(copy);
                }
                _face.sharedMaterials = materials;
                _cloned = true;
            }
            var changed = false;
            foreach (var material in materials)
            {
                if (material == null || material.name == null) continue;
                if (material.name.StartsWith("SlimeBridgeSourceFace_eyes", StringComparison.Ordinal)) { ImportedFaceExpressions.SetAtlas(material, parts[0]); changed = true; }
                else if (material.name.StartsWith("SlimeBridgeSourceFace_mouth", StringComparison.Ordinal)) { ImportedFaceExpressions.SetAtlas(material, parts[1]); changed = true; }
            }
            if (changed) _last = expression;
        }
    }
}
