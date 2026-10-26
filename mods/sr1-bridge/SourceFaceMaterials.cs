using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Immutable static source face passes. Material cardinality is independent
    /// of submesh count: Unity layers surplus materials onto the final submesh.</summary>
    internal static class SourceFaceMaterials
    {
        internal static bool TryBuild(List<object> rows, Func<string, Texture2D?> load,
            out Material[] materials, out List<UnityEngine.Object> owned, out string problem)
        {
            materials = new Material[0];
            owned = new List<UnityEngine.Object>();
            problem = "";
            var complete = false;
            try
            {
                if (rows == null || rows.Count < 2 || rows.Count > 3) throw new FormatException("two or three static source face passes required");
                var roles = new[] { "base-null", "eyes", "mouth" };                   // two passes: a face without a mouth (Yolky)
                var shaders = new[] { "SR/Null Render", "SR/AMP/Slime/Face/Eyes Default", "SR/AMP/Slime/Face/Mouth Default" };
                var prepared = new List<Material>();
                for (var i = 0; i < rows.Count; i++)
                {
                    var row = Map(rows[i]);
                    if (Text(row["face_role"]) != roles[i])
                        throw new FormatException("unsupported or reordered source face shader role");
                    var eyesRole = string.Equals(roles[i], "eyes", StringComparison.Ordinal);
                    var declared = Text(row["shader"]);
                    // SR1 provides the glow eye family natively; accept the declared eyes variant.
                    var eyesVariant = eyesRole && (declared == shaders[1] || declared == "SR/AMP/Slime/Face/Eyes Glow" || declared == "SR/AMP/Slime/Face/Eyes Shadow");
                    var mouthVariant = string.Equals(roles[i], "mouth", StringComparison.Ordinal) && declared == "SR/AMP/Slime/Face/Mouth Shadow";
                    if (!eyesVariant && !mouthVariant && declared != shaders[i])
                        throw new FormatException("unsupported or reordered source face shader role");
                    var shader = Shader.Find(declared);
                    if (shader == null && declared.EndsWith(" Shadow", StringComparison.Ordinal)) shader = Shader.Find(shaders[i]);   // SR2-only shadow variants: the plain face shader
                    if (shader == null || !shader.isSupported) throw new FormatException("required native face shader unavailable: " + declared);
                    var material = new Material(shader) { name = "SlimeBridgeSourceFace_" + roles[i] + "_" + Text(row["name"]) };
                    owned.Add(material);
                    prepared.Add(material);
                    foreach (var color in Map(row["colors"]))
                    {
                        var key = Text(color.Key);
                        var values = Array(color.Value, 4);
                        if (values.Count != 4) throw new FormatException("source color must have four components");
                        var value = new Color(Number(values[0]), Number(values[1]), Number(values[2]), Number(values[3]));
                        if (material.HasProperty(key)) material.SetColor(key, value);
                    }
                    foreach (var scalar in Map(row["floats"]))
                    {
                        var key = Text(scalar.Key);
                        var value = Number(scalar.Value);
                        if (material.HasProperty(key)) material.SetFloat(key, value);
                    }
                    if (!(row["render_queue"] is long queue) || queue < -1 || queue > 5000)
                        throw new FormatException("invalid source face render queue");
                    material.renderQueue = (int)queue;
                    var keywordRows = Array(row["keywords"], 64);
                    var keywords = new List<string>();
                    foreach (var keyword in keywordRows) keywords.Add(Text(keyword));
                    material.shaderKeywords = keywords.ToArray();
                    var textures = Array(row["textures"], eyesRole ? 2 : 1);
                    var minimum = string.Equals(roles[i], "base-null", StringComparison.Ordinal) ? 0 : 1;
                    if (textures.Count < minimum || textures.Count > minimum + (eyesRole ? 1 : 0))
                        throw new FormatException("source face atlas count mismatch");
                    var atlasSeen = false;
                    var noiseSeen = false;
                    foreach (var textureRow in textures)
                    {
                        var texture = Map(textureRow);
                        var property = Text(texture["property"]);
                        // The glow eye family carries an optional glow-noise map next to the raw atlas.
                        var isAtlas = property == "_FaceAtlas";
                        var isGlowNoise = eyesRole && property == "_GlowNoise";
                        if ((!isAtlas && !isGlowNoise) || (isAtlas && atlasSeen) || (isGlowNoise && noiseSeen))
                            throw new FormatException("required raw source face atlas property unavailable");
                        if (isAtlas) atlasSeen = true;
                        if (isGlowNoise) noiseSeen = true;
                        // The optional glow map is dropped when the destination shader lacks the
                        // slot; the required atlas still fails closed.
                        if (!material.HasProperty(property))
                        {
                            if (isGlowNoise) continue;
                            throw new FormatException("required raw source face atlas property unavailable");
                        }
                        var file = Text(texture["file"]);
                        if (file.Contains("..") || file.IndexOf('/') >= 0 || file.IndexOf('\\') >= 0 || file.IndexOf(':') >= 0)
                            throw new FormatException("unsafe source face texture file");
                        var scale = Vector(texture["scale"]);
                        var offset = Vector(texture["offset"]);
                        var loaded = load(file);
                        if (loaded == null) throw new FormatException("verified source face texture unavailable: " + file);
                        owned.Add(loaded);
                        material.SetTexture(property, loaded);
                        material.SetTextureScale(property, scale);
                        material.SetTextureOffset(property, offset);
                    }
                    if (minimum > 0 && !atlasSeen)
                        throw new FormatException("required raw source face atlas property unavailable");
                }
                materials = prepared.ToArray();
                complete = true;
                return true;
            }
            catch (Exception error)
            {
                problem = "source face refused: " + error.GetType().Name + ": " + error.Message;
                return false;
            }
            finally
            {
                if (!complete)
                {
                    foreach (var resource in owned) UnityEngine.Object.Destroy(resource);
                    owned.Clear();
                }
            }
        }

        private static string Text(object value)
        {
            var text = value as string;
            if (text == null || text.Length == 0 || text.Length > 256) throw new FormatException("invalid source face text");
            foreach (var c in text) if (char.IsControl(c)) throw new FormatException("invalid source face text");
            return text;
        }

        private static Dictionary<string, object> Map(object value)
        {
            var result = value as Dictionary<string, object>;
            if (result == null || result.Count > 1024) throw new FormatException("invalid source face object");
            return result;
        }

        private static List<object> Array(object value, int maximum)
        {
            var result = value as List<object>;
            if (result == null || result.Count > maximum) throw new FormatException("invalid source face array");
            return result;
        }

        private static float Number(object value)
        {
            var result = value is long integer ? (float)integer : value is double real ? (float)real : float.NaN;
            // Saved shader colors/floats can carry packed identifiers and HDR data.
            // Preserve any finite native float; a spatial-coordinate cap corrupts that contract.
            if (float.IsNaN(result) || float.IsInfinity(result))
                throw new FormatException("invalid finite source face number");
            return result;
        }

        private static Vector2 Vector(object value)
        {
            var values = Array(value, 2);
            if (values.Count != 2) throw new FormatException("source texture transform must have two components");
            var x = Number(values[0]);
            var y = Number(values[1]);
            if (Math.Abs(x) > 1000000f || Math.Abs(y) > 1000000f)
                throw new FormatException("unsupported source atlas transform range");
            return new Vector2(x, y);
        }
    }
}
