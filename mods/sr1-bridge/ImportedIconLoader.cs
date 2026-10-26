using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Read-only source Sprite import. No shared native appearance is ever modified.</summary>
    internal static class ImportedIconLoader
    {
        // Directors/UI may retain an appearance after registration rollback. Once published,
        // keep all three objects alive for the definition lifetime (process lifetime here).
        // Never destroy a published bundle merely because a registrar/scene went away.
        private static readonly Dictionary<SlimeDefinition, OwnedAppearance> Registered =
            new Dictionary<SlimeDefinition, OwnedAppearance>();

        internal sealed class OwnedAppearance : IDisposable
        {
            internal readonly SlimeAppearance Appearance;
            internal readonly Texture2D? Texture;
            internal readonly Sprite? Sprite;
            internal bool Retained;
            private bool _disposed;
            internal OwnedAppearance(SlimeAppearance appearance, Texture2D? texture, Sprite? sprite)
            { Appearance = appearance; Texture = texture; Sprite = sprite; }
            public void Dispose()
            {
                if (_disposed || Retained) return;
                _disposed = true;
                UnityEngine.Object.Destroy(Appearance);
                if (Sprite != null) UnityEngine.Object.Destroy(Sprite);
                if (Texture != null) UnityEngine.Object.Destroy(Texture);
            }
        }

        internal static bool WasPublished(int nativeId)
        {
            foreach (var definition in Registered.Keys)
                if ((int)definition.IdentifiableId == nativeId) return true;
            return false;
        }

        internal static void Retain(SlimeDefinition definition, OwnedAppearance owner)
        {
            if (Registered.TryGetValue(definition, out var existing))
            {
                if (!ReferenceEquals(existing, owner)) throw new InvalidOperationException("definition already owns another icon");
                return;
            }
            Registered.Add(definition, owner);
            owner.Retained = true;
            UnityEngine.Object.DontDestroyOnLoad(owner.Appearance);
            if (owner.Texture != null) UnityEngine.Object.DontDestroyOnLoad(owner.Texture);
            if (owner.Sprite != null) UnityEngine.Object.DontDestroyOnLoad(owner.Sprite);
        }

        internal static bool TryPrepare(string canonicalId, SlimeDefinition template,
            out OwnedAppearance? owner, out string problem)
        {
            var root = LabSpecPathGuard.FindRepoRoot();
            if (root == null) { owner = null; problem = "source icon repository root unavailable"; return false; }
            var cacheRoot = Path.Combine(root, "Lab", "AssetCache", "sr2-to-sr1");
            return TryPrepareFromDirectory(cacheRoot, Path.Combine(cacheRoot, canonicalId), canonicalId, template, out owner, out problem);
        }

        internal static bool TryPrepareFromDirectory(string cacheRoot, string directory, string canonicalId,
            SlimeDefinition template, out OwnedAppearance? owner, out string problem)
        {
            owner = null; problem = "";
            Texture2D? texture = null; Sprite? sprite = null; SlimeAppearance? copy = null;
            try
            {
                if (string.IsNullOrEmpty(canonicalId) || canonicalId.Contains("..")) throw new InvalidDataException("unsafe icon canonical id");
                foreach (var c in canonicalId)
                    if (!(c >= 'a' && c <= 'z') && !(c >= '0' && c <= '9') && c != '.' && c != '_' && c != '-')
                        throw new InvalidDataException("unsafe icon canonical id");
                var classic = template.GetAppearanceForSet(SlimeAppearance.AppearanceSaveSet.CLASSIC) ??
                    (template.AppearancesDefault != null && template.AppearancesDefault.Length > 0 ? template.AppearancesDefault[0] : null);   // Largos keep their default look under another save set
                if (classic == null) throw new InvalidDataException("template has no CLASSIC appearance");
                var mandatory = canonicalId == "slime.ringtail";
                var manifestPath = Path.Combine(directory, "cache-manifest.json");
                // Guard before all existence probes or reads, including optional cache entries.
                if (!LabSpecPathGuard.IsUnderRoot(cacheRoot, directory, out var reason) ||
                    LabSpecPathGuard.HasReparsePoint(directory, out reason) ||
                    LabSpecPathGuard.HasReparsePoint(manifestPath, out reason))
                    throw new InvalidDataException("source icon cache refused: " + reason);
                Dictionary<string, object>? manifest = null;
                if (File.Exists(manifestPath))
                {
                    if (!LabSpecPathGuard.IsAllowedFile(directory, manifestPath, out reason)) throw new InvalidDataException(reason);
                    manifest = MiniJson.Parse(System.Text.Encoding.UTF8.GetString(ReadBounded(manifestPath, 2 * 1024 * 1024))) as Dictionary<string, object>;
                    if (manifest == null || Text(manifest, "canonical_id") != canonicalId) throw new InvalidDataException("source icon manifest invalid/foreign");
                }
                if (manifest != null && manifest.TryGetValue("icon", out var value))
                {
                    if (!(value is Dictionary<string, object> icon)) throw new InvalidDataException("source icon descriptor malformed");
                    if (Text(icon, "schema") != "bridge-source-sprite" || Number(icon, "schema_version") != 1 ||
                        !icon.TryGetValue("mandatory", out var required) || !(required is bool) ||
                        (mandatory && !(bool)required)) throw new InvalidDataException("source icon schema/mandatory flag invalid");
                    var file = Text(icon, "file") ?? "";
                    if (string.IsNullOrEmpty(file) || file.Contains("..") || file.IndexOfAny(new[] { '/', '\\', ':' }) >= 0 ||
                        Path.IsPathRooted(file) || Path.GetFileName(file) != file) throw new InvalidDataException("unsafe source icon file");
                    if (!manifest.TryGetValue("files", out var covered) || !(covered is Dictionary<string, object> files) ||
                        !files.TryGetValue(file, out var hashValue) || !(hashValue is string expectedHash) || !IsHash(expectedHash))
                        throw new InvalidDataException("source icon file not manifest-covered");
                    var rect = Array(icon, "rect", 4); var pivot = Array(icon, "pivot", 2); var size = Array(icon, "texture_size", 2);
                    var ppu = Number(icon, "pixels_per_unit");
                    // This narrow source slice is explicitly unpacked/unrotated. Do not silently
                    // interpret an atlas crop or rounded rect as a different full logical Sprite.
                    if (rect[0] != 0 || rect[1] != 0 || rect[2] != 1024 || rect[3] != 1024 ||
                        size[0] != 1024 || size[1] != 1024 || pivot[0] != .5f || pivot[1] != .5f || ppu != 100 ||
                        Number(icon, "source_settings_raw") != 64 || Number(icon, "packing_rotation") != 0 ||
                        (Text(icon, "pixel_encoding") != "source-texture-rgba-unresampled" &&
                         Text(icon, "pixel_encoding") != "composed-square-icon")) throw new InvalidDataException("source icon framing/packing unsupported");
                    if (mandatory && (Text(icon, "source_sprite_name") != "iconSlimeRingtail" ||
                        Number(icon, "source_sprite_path_id") != 3052 || Number(icon, "source_appearance_path_id") != 31303 ||
                        Number(icon, "source_texture_path_id") != 1324)) throw new InvalidDataException("Ringtail source icon identity invalid");
                    var crop = Array(icon, "source_texture_rect", 4); var offset = Array(icon, "source_texture_rect_offset", 2);
                    if (crop[0] < 0 || crop[1] < 0 || crop[2] <= 0 || crop[3] <= 0 ||
                        crop[0] + crop[2] > 1024.01f || crop[1] + crop[3] > 1024.01f ||
                        offset[0] != crop[0] || offset[1] != crop[1]) throw new InvalidDataException("source icon tight rect invalid");
                    if (!icon.TryGetValue("vertices", out var vs) || !(vs is List<object> vertices) || vertices.Count < 3 || vertices.Count > 4096 ||
                        !icon.TryGetValue("triangles", out var ts) || !(ts is List<object> indices) || indices.Count < 3 || indices.Count > 24576 || indices.Count % 3 != 0)
                        throw new InvalidDataException("source icon geometry shape/budget invalid");
                    var points = new Vector2[vertices.Count];
                    for (var i = 0; i < points.Length; i++)
                    {
                        var v = ArrayValue(vertices[i], 2);
                        if (Math.Abs(v[0]) > 5.13f || Math.Abs(v[1]) > 5.13f) throw new InvalidDataException("source icon vertex outside logical rect");
                        // The descriptor stores vertices in sprite units around the pivot; Sprite.OverrideGeometry wants pixels
                        // inside the sprite rect (origin bottom-left): "Some vertices are outside of the Sprite rectangle".
                        points[i] = new Vector2(v[0] * ppu + pivot[0] * rect[2], v[1] * ppu + pivot[1] * rect[3]);
                    }
                    var triangles = new ushort[indices.Count];
                    for (var i = 0; i < triangles.Length; i++)
                    {
                        var n = Numeric(indices[i]);
                        if (n != Math.Floor(n) || n < 0 || n >= points.Length) throw new InvalidDataException("source icon triangle out of bounds");
                        triangles[i] = (ushort)n;
                    }
                    var path = Path.Combine(directory, file);
                    if (!LabSpecPathGuard.IsAllowedFile(directory, path, out reason)) throw new InvalidDataException("source icon file refused: " + reason);
                    var bytes = ReadBounded(path, 16 * 1024 * 1024);
                    using (var sha = SHA256.Create())
                    {
                        var actual = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
                        if (!string.Equals(actual, expectedHash, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("source icon hash mismatch");
                    }
                    // Reject unexpected PNG dimensions before Unity allocates decode buffers.
                    if (bytes.Length < 33 || bytes[0] != 137 || bytes[1] != 80 || bytes[2] != 78 || bytes[3] != 71 ||
                        bytes[4] != 13 || bytes[5] != 10 || bytes[6] != 26 || bytes[7] != 10 ||
                        BigEndian(bytes, 16) != 1024 || BigEndian(bytes, 20) != 1024)
                        throw new InvalidDataException("source icon PNG header/dimensions invalid");
                    texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    texture.name = "SlimeBridgeIconTexture_" + canonicalId;
                    texture.wrapMode = TextureWrapMode.Clamp; texture.filterMode = FilterMode.Bilinear;
                    if (!ImageConversion.LoadImage(texture, bytes, false) || texture.width != 1024 || texture.height != 1024)
                        throw new InvalidDataException("source icon native decode failed");
                    sprite = UnityEngine.Sprite.Create(texture, new Rect(rect[0], rect[1], rect[2], rect[3]),
                        new Vector2(pivot[0], pivot[1]), ppu, 0, SpriteMeshType.Tight);
                    if (sprite == null) throw new InvalidDataException("source icon native Sprite creation failed");
                    sprite.name = "SlimeBridgeIcon_" + canonicalId;
                    // Unity refuses OverrideGeometry on a FullRect sprite ("Not allowed to override geometry on sprite")
                    // and silently keeps the plain quad; a Tight sprite takes the source's authored outline.
                    sprite.OverrideGeometry(points, triangles);
                }
                else if (mandatory) throw new InvalidDataException("mandatory Ringtail source icon descriptor missing (native substitute refused)");
                copy = UnityEngine.Object.Instantiate(classic);
                copy.name = "SlimeBridgeClassic_" + canonicalId;
                if (sprite != null) copy.Icon = sprite;
                owner = new OwnedAppearance(copy, texture, sprite);
                return true;
            }
            catch (Exception ex)
            {
                if (copy != null) UnityEngine.Object.Destroy(copy);
                if (sprite != null) UnityEngine.Object.Destroy(sprite);
                if (texture != null) UnityEngine.Object.Destroy(texture);
                problem = "source icon refused: " + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static byte[] ReadBounded(string path, int maximum)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length <= 0 || stream.Length > maximum) throw new InvalidDataException("source icon file exceeds budget");
                var bytes = new byte[(int)stream.Length]; var offset = 0;
                while (offset < bytes.Length) { var read = stream.Read(bytes, offset, bytes.Length - offset); if (read == 0) throw new EndOfStreamException(); offset += read; }
                if (stream.ReadByte() != -1) throw new InvalidDataException("source icon file changed size");
                return bytes;
            }
        }
        private static int BigEndian(byte[] bytes, int at) => (bytes[at] << 24) | (bytes[at + 1] << 16) | (bytes[at + 2] << 8) | bytes[at + 3];
        private static bool IsHash(string value)
        { if (value.Length != 64) return false; foreach (var c in value) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f') && !(c >= 'A' && c <= 'F')) return false; return true; }
        private static string? Text(Dictionary<string, object> d, string key) => d.TryGetValue(key, out var v) ? v as string : null;
        private static float Number(Dictionary<string, object> d, string key)
        { if (!d.TryGetValue(key, out var v)) throw new InvalidDataException("missing icon number " + key); return Numeric(v); }
        private static float Numeric(object value)
        {
            double number;
            if (value is long i) number = i; else if (value is double f) number = f;
            else throw new InvalidDataException("icon numeric type invalid");
            if (double.IsNaN(number) || double.IsInfinity(number) || Math.Abs(number) > 100000) throw new InvalidDataException("icon number out of bounds");
            return (float)number;
        }
        private static float[] Array(Dictionary<string, object> d, string key, int count)
        { if (!d.TryGetValue(key, out var v)) throw new InvalidDataException("missing icon array " + key); return ArrayValue(v, count); }
        private static float[] ArrayValue(object value, int count)
        { if (!(value is List<object> list) || list.Count != count) throw new InvalidDataException("icon array shape invalid"); var result = new float[count]; for (var i = 0; i < count; i++) result[i] = Numeric(list[i]); return result; }
    }
}
