using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Source effects the SR2 shaders cannot carry over (the Hyper slime's energy aura: lights, glow sphere, plasma
    /// particles). The extractor describes them in model.json "effects"; the loader parks one inert node per effect on the
    /// template (serialized parameters survive Instantiate) and each spawned actor builds its own Light / ParticleSystem with
    /// Slime Rancher's own effect materials (<see cref="NativeFx"/>).
    /// </summary>
    internal sealed class ImportedEffect : MonoBehaviour
    {
        public string Kind = "";
        public Color Tint = Color.white;
        public Color Core = Color.white;
        public float Range = 1f;
        public float Size = 1f;
        public float Rate = 10f;
        public float Lifetime = 1f;
        private bool _built;
        private static readonly HashSet<string> Logged = new HashSet<string>(StringComparer.Ordinal);

        private void Start()
        {
            if (_built) return;
            _built = true;
            try
            {
                switch (Kind)
                {
                    case "light": BuildLight(); break;
                    case "particles": BuildParticles(); break;
                    case "glow_sphere": BuildGlow(); break;
                }
                if (Logged.Add(Kind)) Debug.Log("[SlimeBridge] imported effect built: " + Kind + " under " + (transform.parent != null ? transform.parent.name : "?"));
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] imported effect " + Kind + " skipped: " + ex.GetType().Name); }
        }

        private void BuildLight()
        {
            var light = gameObject.AddComponent<Light>();
            light.type = LightType.Point;
            light.color = NativeFx.Ldr(Tint);
            light.range = Mathf.Clamp(Range * 2.2f, 1.5f, 4f);
            light.intensity = 1.1f;
            light.shadows = LightShadows.None;
        }

        private void BuildParticles()
        {
            var system = gameObject.AddComponent<ParticleSystem>();
            system.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            var main = system.main;
            main.loop = true;
            main.startLifetime = Mathf.Clamp(Lifetime, 0.15f, 3f);
            main.startSize = new ParticleSystem.MinMaxCurve(Size * 0.35f, Size * 0.6f);
            main.startSpeed = new ParticleSystem.MinMaxCurve(0.2f, 0.7f);
            main.startColor = new ParticleSystem.MinMaxGradient(NativeFx.Ldr(Tint), NativeFx.Ldr(Core));
            main.maxParticles = 60;
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.scalingMode = ParticleSystemScalingMode.Hierarchy;
            var emission = system.emission;
            emission.rateOverTime = Mathf.Clamp(Rate * 1.5f, 4f, 40f);
            var shape = system.shape;
            shape.shapeType = ParticleSystemShapeType.Sphere;
            shape.radius = 0.55f;
            var fade = system.colorOverLifetime;
            fade.enabled = true;
            var gradient = new Gradient();
            gradient.SetKeys(new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(Color.white, 1f) },
                             new[] { new GradientAlphaKey(0f, 0f), new GradientAlphaKey(1f, 0.2f), new GradientAlphaKey(0f, 1f) });
            fade.color = gradient;
            var renderer = GetComponent<ParticleSystemRenderer>();
            renderer.material = NativeFx.Glow(NativeFx.Ldr(Tint));
            renderer.renderMode = ParticleSystemRenderMode.Billboard;
            system.Play(true);
        }

        private void BuildGlow()
        {
            // A soft additive halo around the slime (the source's unlit glow sphere): one long-lived billboard.
            var system = gameObject.AddComponent<ParticleSystem>();
            system.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            var main = system.main;
            main.loop = true;
            main.startLifetime = 1000f;
            main.startSpeed = 0f;
            main.startSize = 1.9f;
            main.maxParticles = 1;
            main.simulationSpace = ParticleSystemSimulationSpace.Local;
            main.scalingMode = ParticleSystemScalingMode.Hierarchy;
            var tint = NativeFx.Ldr(Tint);
            tint.a = 0.45f;
            main.startColor = tint;
            var emission = system.emission;
            emission.rateOverTime = 0f;
            emission.SetBursts(new[] { new ParticleSystem.Burst(0f, 1) });
            var shape = system.shape;
            shape.enabled = false;
            var renderer = GetComponent<ParticleSystemRenderer>();
            renderer.material = NativeFx.Glow(tint);
            system.Play(true);
        }

        // ---- loader side --------------------------------------------------------------------------------------------
        internal static int Attach(Transform root, Dictionary<string, object> document)
        {
            if (!document.TryGetValue("effects", out var value) || !(value is List<object> rows)) return 0;
            var built = 0;
            foreach (var rowValue in rows)
            {
                if (built >= 16 || !(rowValue is Dictionary<string, object> row)) break;
                var kind = row.TryGetValue("kind", out var k) ? k as string : null;
                var parentPath = row.TryGetValue("parent_path", out var p) ? p as string : null;
                var active = !row.TryGetValue("active", out var a) || !(a is bool flag) || flag;
                if (string.IsNullOrEmpty(kind) || string.IsNullOrEmpty(parentPath)) continue;
                if (!active && kind != "light") continue;          // the source keeps those for its charged state only
                var parent = root.Find(parentPath);
                if (parent == null) continue;
                var node = new GameObject("SlimeBridgeEffect_" + kind);
                node.transform.SetParent(parent, false);
                node.transform.localPosition = Vector(row, "local_position", Vector3.zero);
                node.transform.localScale = Vector(row, "local_scale", Vector3.one);
                var effect = node.AddComponent<ImportedEffect>();
                effect.Kind = kind;
                var colors = row.TryGetValue("colors", out var c) ? c as Dictionary<string, object> : null;
                effect.Tint = ColorOf(row, "color") ?? ColorOf(colors, "_ColorEdge") ?? ColorOf(colors, "_ColorBottom") ?? ColorOf(colors, "_Color") ?? Color.yellow;
                effect.Core = ColorOf(colors, "_ColorCore") ?? effect.Tint;
                effect.Range = Float(row, "range", 1f);
                effect.Size = Float(row, "size", 1f);
                effect.Rate = Float(row, "rate", 10f);
                effect.Lifetime = Float(row, "lifetime", 1f);
                built++;
            }
            return built;
        }

        private static float Float(Dictionary<string, object> row, string key, float fallback) =>
            row.TryGetValue(key, out var v) && v is double d && !double.IsNaN(d) && !double.IsInfinity(d) ? (float)d
            : row.TryGetValue(key, out v) && v is long l ? l : fallback;

        private static Vector3 Vector(Dictionary<string, object> row, string key, Vector3 fallback)
        {
            if (!row.TryGetValue(key, out var v) || !(v is List<object> list) || list.Count != 3) return fallback;
            var values = new float[3];
            for (var i = 0; i < 3; i++)
            {
                if (list[i] is double d) values[i] = (float)d;
                else if (list[i] is long l) values[i] = l;
                else return fallback;
            }
            return new Vector3(values[0], values[1], values[2]);
        }

        private static Color? ColorOf(Dictionary<string, object>? row, string key)
        {
            if (row == null || !row.TryGetValue(key, out var v) || !(v is List<object> list) || list.Count < 3) return null;
            var c = new float[4] { 0, 0, 0, 1 };
            for (var i = 0; i < Math.Min(4, list.Count); i++)
            {
                if (list[i] is double d) c[i] = (float)d;
                else if (list[i] is long l) c[i] = l;
            }
            return new Color(c[0], c[1], c[2], c[3]);
        }
    }

    /// <summary>A fire aura: flames rising from the whole body surface (the Hyper slime's energy aura), drawn with the game's
    /// own fire particle material, tinted with the source colour. World-space so they trail behind a moving slime.</summary>
    internal sealed class ImportedFlameAura : MonoBehaviour
    {
        public Color Tint = Color.yellow;
        private bool _built;

        private void Start()
        {
            if (_built) return;
            _built = true;
            try
            {
                var system = gameObject.AddComponent<ParticleSystem>();
                system.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
                var tint = NativeFx.Ldr(Tint);
                var main = system.main;
                main.loop = true;
                main.startLifetime = new ParticleSystem.MinMaxCurve(0.45f, 0.75f);
                main.startSpeed = new ParticleSystem.MinMaxCurve(0.05f, 0.25f);
                main.startSize = new ParticleSystem.MinMaxCurve(0.35f, 0.65f);
                main.startRotation = new ParticleSystem.MinMaxCurve(-0.4f, 0.4f);
                main.startColor = new ParticleSystem.MinMaxGradient(tint, Color.Lerp(tint, new Color(1f, 0.45f, 0.05f), 0.6f));
                main.maxParticles = 90;
                main.simulationSpace = ParticleSystemSimulationSpace.World;
                main.scalingMode = ParticleSystemScalingMode.Hierarchy;
                var emission = system.emission;
                emission.rateOverTime = 34f;
                var shape = system.shape;
                shape.shapeType = ParticleSystemShapeType.Sphere;
                shape.radius = 0.48f;
                shape.radiusThickness = 0.15f;              // from the body surface, not its inside
                var velocity = system.velocityOverLifetime;
                velocity.enabled = true;
                velocity.space = ParticleSystemSimulationSpace.World;
                velocity.x = new ParticleSystem.MinMaxCurve(0f, 0f);
                velocity.y = new ParticleSystem.MinMaxCurve(0.6f, 1.2f);
                velocity.z = new ParticleSystem.MinMaxCurve(0f, 0f);
                var size = system.sizeOverLifetime;
                size.enabled = true;
                size.size = new ParticleSystem.MinMaxCurve(1f, AnimationCurve.Linear(0f, 1f, 1f, 0.15f));
                var fade = system.colorOverLifetime;
                fade.enabled = true;
                var gradient = new Gradient();
                gradient.SetKeys(new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(new Color(1f, 0.55f, 0.2f), 1f) },
                                 new[] { new GradientAlphaKey(0f, 0f), new GradientAlphaKey(0.9f, 0.15f), new GradientAlphaKey(0f, 1f) });
                fade.color = gradient;
                var renderer = GetComponent<ParticleSystemRenderer>();
                renderer.material = NativeFx.Flame(tint);
                renderer.renderMode = ParticleSystemRenderMode.Billboard;
                system.Play(true);
                if (!_logged) { _logged = true; Debug.Log("[SlimeBridge] imported flame aura built (" + renderer.sharedMaterial.name + ")"); }
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] imported flame aura skipped: " + ex.GetType().Name); }
        }

        private static bool _logged;
    }

    /// <summary>Slime Rancher's own effect materials, found among the loaded assets and cloned (never edited in place).</summary>
    internal static class NativeFx
    {
        private static bool _fireLogged;
        private static readonly Dictionary<string, Material?> Found = new Dictionary<string, Material?>(StringComparer.Ordinal);

        /// <summary>HDR source colours (values above 1) folded into displayable LDR colours, hue kept.</summary>
        internal static Color Ldr(Color c)
        {
            var max = Mathf.Max(c.r, Mathf.Max(c.g, c.b));
            if (max > 1f) { c.r /= max; c.g /= max; c.b /= max; }
            c.a = 1f;
            return c;
        }

        private static Material? Native(string name, string shader)
        {
            var key = name + "|" + shader;
            if (Found.TryGetValue(key, out var cached) && cached != null) return cached;
            Material? byShader = null;
            foreach (var material in Resources.FindObjectsOfTypeAll<Material>())
            {
                if (material == null || material.shader == null) continue;
                if (material.name == name) { Found[key] = material; return material; }
                if (byShader == null && material.shader.name == shader) byShader = material;
            }
            Found[key] = byShader;
            return byShader;
        }

        /// <summary>The Fire slime's toon flame material tinted (falls back to SR's additive particle shader).</summary>
        internal static Material Fire(Color tint)
        {
            var source = Native("fxToonFire Slime", "SR/FX/Toon Fire");
            var clone = source != null ? new Material(source) : Additive();
            if (!_fireLogged) { _fireLogged = true; Debug.Log("[SlimeBridge] imported fire material from " + (source != null ? source.name + " (" + source.shader.name + ")" : "additive fallback")); }
            clone.name = "SlimeBridgeImportedFire";
            if (clone.HasProperty("_TintColor")) clone.SetColor("_TintColor", Ldr(tint));
            return clone;
        }

        /// <summary>An additive glowing shell (aura meshes): the game's soft glow texture, tinted and fairly opaque.</summary>
        internal static Material AuraShell(Color tint)
        {
            var glow = Ldr(tint);
            glow.a = 0.75f;
            var clone = Glow(glow);
            clone.name = "SlimeBridgeImportedAura";
            return clone;
        }

        /// <summary>The game's own fire particle material (Fire_01), tinted; soft glow as a fallback.</summary>
        internal static Material Flame(Color tint)
        {
            var source = Native("Fire_01", "SR/Particles/Additive");
            var clone = source != null && source.name == "Fire_01" ? new Material(source) : Glow(tint);
            clone.name = "SlimeBridgeImportedFlame";
            if (clone.HasProperty("_TintColor")) clone.SetColor("_TintColor", new Color(tint.r, tint.g, tint.b, 0.85f));
            return clone;
        }

        /// <summary>A soft additive glow (the game's own Glow_01 particle material when loaded).</summary>
        internal static Material Glow(Color tint)
        {
            var source = Native("Glow_01", "SR/Particles/Additive");
            var clone = source != null ? new Material(source) : Additive();
            clone.name = "SlimeBridgeImportedGlow";
            if (clone.HasProperty("_TintColor")) clone.SetColor("_TintColor", new Color(tint.r, tint.g, tint.b, Mathf.Clamp01(tint.a)));
            return clone;
        }

        private static Material Additive()
        {
            var shader = Shader.Find("SR/Particles/Additive") ?? Shader.Find("Particles/Additive") ?? Shader.Find("Sprites/Default");
            return new Material(shader);
        }
    }
}
