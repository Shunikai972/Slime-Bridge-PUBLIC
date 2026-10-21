using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// The species' own plort look. Imported plorts are clones of the Pink plort prefab, so without this they
    /// are pink with the Pink icon. SR2 plorts share one mesh and differ by material colours (and sometimes a
    /// stripe texture) plus an icon; the converter writes those to <c>plort.json</c> in the species cache
    /// (manifest-hashed like every other cache file). SR1's plort shader uses the same colour properties.
    /// Fail-open: no/invalid data leaves the template plort untouched.
    /// </summary>
    internal static class ImportedPlortAppearance
    {
        internal sealed class Info
        {
            internal Color Top, Middle, Bottom, Outline;
            internal Texture2D? Stripe;
            internal Sprite? Icon;
            /// <summary>The plort's own SR2 sale price and market saturation; 0 when the cache has none.</summary>
            internal float MarketValue, MarketFullSaturation, MarketPinkValue;
        }

        private static readonly Dictionary<string, Info?> Loaded = new Dictionary<string, Info?>(StringComparer.Ordinal);

        internal static bool TryGet(string slimeCanonicalId, out Info info)
        {
            info = null!;
            if (string.IsNullOrEmpty(slimeCanonicalId)) return false;
            if (Loaded.TryGetValue(slimeCanonicalId, out var cached))
            {
                if (cached == null) return false;
                info = cached;
                return true;
            }
            Info? loaded = null;
            try { loaded = Load(slimeCanonicalId, out var problem); if (loaded == null && !string.IsNullOrEmpty(problem)) UnityEngine.Debug.Log("[SlimeBridge] plort look for " + slimeCanonicalId + " unavailable: " + problem); }
            catch (Exception ex) { UnityEngine.Debug.LogWarning("[SlimeBridge] plort look load threw: " + ex.GetType().Name); }
            Loaded[slimeCanonicalId] = loaded;
            if (loaded == null) return false;
            info = loaded;
            return true;
        }

        private static Info? Load(string canonical, out string problem)
        {
            problem = "";
            var repoRoot = LabSpecPathGuard.FindRepoRoot();
            if (repoRoot == null) { problem = "repository root not found"; return null; }
            var cacheRoot = Path.Combine(repoRoot, "Lab", "AssetCache", "sr2-to-sr1");
            var directory = Path.Combine(cacheRoot, canonical);
            if (!LabSpecPathGuard.IsAllowedDirectory(cacheRoot, directory, out var reason)) { problem = "cache directory refused: " + reason; return null; }
            var manifestPath = Path.Combine(directory, "cache-manifest.json");
            var plortPath = Path.Combine(directory, "plort.json");
            if (!LabSpecPathGuard.IsAllowedFile(directory, manifestPath, out reason) || !LabSpecPathGuard.IsAllowedFile(directory, plortPath, out reason))
            { problem = "cache file refused: " + reason; return null; }
            if (!File.Exists(manifestPath) || !File.Exists(plortPath)) { problem = "no plort data in the cache"; return null; }
            var manifest = MiniJson.Parse(File.ReadAllText(manifestPath)) as Dictionary<string, object>;
            var files = manifest != null && manifest.TryGetValue("files", out var f) ? f as Dictionary<string, object> : null;
            if (files == null || !string.Equals(manifest!.TryGetValue("canonical_id", out var c) ? c as string : null, canonical, StringComparison.Ordinal))
            { problem = "cache manifest invalid or foreign"; return null; }
            if (!Verified(directory, files, "plort.json")) { problem = "plort.json is not covered by a matching manifest hash"; return null; }
            var document = MiniJson.Parse(File.ReadAllText(plortPath)) as Dictionary<string, object>;
            if (document == null || !string.Equals(document.TryGetValue("schema", out var schema) ? schema as string : null, "bridge-plort", StringComparison.Ordinal))
            { problem = "plort schema mismatch"; return null; }
            if (!(document.TryGetValue("palette", out var paletteValue) && paletteValue is Dictionary<string, object> palette) ||
                !TryColor(palette, "top", out var top) || !TryColor(palette, "middle", out var middle) || !TryColor(palette, "bottom", out var bottom))
            { problem = "plort palette missing"; return null; }
            var info = new Info { Top = top, Middle = middle, Bottom = bottom, Outline = Color.Lerp(top, Color.white, 0.25f) };
            if (document.TryGetValue("market", out var marketValue) && marketValue is Dictionary<string, object> market)
            {
                try
                {
                    var price = Convert.ToSingle(market["value"], System.Globalization.CultureInfo.InvariantCulture);
                    var saturation = Convert.ToSingle(market["full_saturation"], System.Globalization.CultureInfo.InvariantCulture);
                    if (price > 0f && price < 100000f && saturation > 0f && saturation < 1e7f) { info.MarketValue = price; info.MarketFullSaturation = saturation; }
                    if (market.TryGetValue("pink_value", out var pinkRaw) && pinkRaw != null)
                    {
                        var pink = Convert.ToSingle(pinkRaw, System.Globalization.CultureInfo.InvariantCulture);
                        if (pink > 0f && pink < 100000f) info.MarketPinkValue = pink;
                    }
                }
                catch (Exception) { /* keep the template price */ }
            }
            info.Stripe = LoadTexture(directory, files, document, "stripe_texture", true);
            var iconTexture = LoadTexture(directory, files, document, "icon", false);
            if (iconTexture != null)
            {
                iconTexture.wrapMode = TextureWrapMode.Clamp;
                info.Icon = Sprite.Create(iconTexture, new Rect(0, 0, iconTexture.width, iconTexture.height), new Vector2(0.5f, 0.5f), 100f);
                info.Icon.name = "SlimeBridgePlortIcon_" + canonical;
                UnityEngine.Object.DontDestroyOnLoad(info.Icon);
            }
            return info;
        }

        private static Texture2D? LoadTexture(string directory, Dictionary<string, object> files, Dictionary<string, object> document, string key, bool repeat)
        {
            if (!document.TryGetValue(key, out var raw) || !(raw is string name) || name.Length == 0) return null;
            if (name.IndexOfAny(new[] { '/', '\\', ':' }) >= 0 || name != Path.GetFileName(name)) return null;
            if (!Verified(directory, files, name)) return null;
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, true);
            if (!texture.LoadImage(File.ReadAllBytes(Path.Combine(directory, name)))) { UnityEngine.Object.Destroy(texture); return null; }
            texture.wrapMode = repeat ? TextureWrapMode.Repeat : TextureWrapMode.Clamp;
            texture.name = Path.GetFileNameWithoutExtension(name);
            UnityEngine.Object.DontDestroyOnLoad(texture);
            return texture;
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

        private static bool TryColor(Dictionary<string, object> map, string key, out Color color)
        {
            color = Color.white;
            if (!map.TryGetValue(key, out var raw) || !(raw is List<object> row) || row.Count < 3) return false;
            var values = new float[4] { 0f, 0f, 0f, 1f };
            for (var i = 0; i < 4 && i < row.Count; i++)
            {
                try { values[i] = Convert.ToSingle(row[i], System.Globalization.CultureInfo.InvariantCulture); } catch (Exception) { return false; }
                if (float.IsNaN(values[i]) || float.IsInfinity(values[i])) return false;
            }
            color = new Color(values[0], values[1], values[2], values[3]);
            return true;
        }

        /// <summary>Give the cloned plort prefab its own material instances carrying the species palette.</summary>
        internal static int ApplyToPlort(GameObject plort, Info info)
        {
            var changed = 0;
            foreach (var renderer in plort.GetComponentsInChildren<Renderer>(true))
            {
                if (renderer == null) continue;
                var shared = renderer.sharedMaterials;
                if (shared == null || shared.Length == 0) continue;
                var replaced = new Material[shared.Length];
                for (var i = 0; i < shared.Length; i++)
                {
                    var source = shared[i];
                    if (source == null || source.shader == null || !(source.shader.name ?? "").Contains("Plort")) { replaced[i] = source!; continue; }
                    var copy = new Material(source) { name = source.name + "_SlimeBridgeImported" };
                    if (copy.HasProperty("_TopColor")) copy.SetColor("_TopColor", info.Top);
                    if (copy.HasProperty("_MiddleColor")) copy.SetColor("_MiddleColor", info.Middle);
                    if (copy.HasProperty("_BottomColor")) copy.SetColor("_BottomColor", info.Bottom);
                    if (copy.HasProperty("_OutlineColor")) copy.SetColor("_OutlineColor", info.Outline);
                    if (info.Stripe != null && copy.HasProperty("_StripeTexture")) copy.SetTexture("_StripeTexture", info.Stripe);
                    UnityEngine.Object.DontDestroyOnLoad(copy);
                    replaced[i] = copy;
                    changed++;
                }
                renderer.sharedMaterials = replaced;
            }
            return changed;
        }
    }
}
