using System;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Catches a native plort thrown into a statue's trigger, with the same rules as the native <c>PuzzleSlot</c>
    /// (non-trigger collider, matching <c>Identifiable.Id</c>). Which plorts count and what a catch does is decided by
    /// the host through <see cref="OnPlort"/>; progress lives in the Bridge sidecar, never in the native save.
    /// </summary>
    internal sealed class SanctuaryPlortCatcher : MonoBehaviour
    {
        internal Identifiable.Id CatchId;
        internal string Key = "";
        /// <summary>Return true to consume the plort.</summary>
        internal Func<SanctuaryPlortCatcher, bool>? OnPlort;
        internal GameObject? Fx;
        private float _lastCatch = -10f;

        public void OnTriggerEnter(Collider other)
        {
            if (other == null || other.isTrigger || OnPlort == null) return;
            if (Time.time - _lastCatch < 0.25f) return;          // one plort per physics burst
            var identifiable = other.GetComponent<Identifiable>();
            if (identifiable == null || identifiable.id != CatchId) return;
            if (!OnPlort(this)) return;
            _lastCatch = Time.time;
            try
            {
                if (Fx != null) SRBehaviour.SpawnAndPlayFX(Fx, other.transform.position, other.transform.rotation);
            }
            catch (Exception ex) { Debug.Log("[SlimeBridge] sanctuary fx skipped: " + ex.GetType().Name); }
            Destroyer.DestroyActor(other.gameObject, "SlimeBridge.SanctuaryPlortCatcher");
        }
    }

    /// <summary>Tells the host when the player's main collider enters a trigger (the portal).</summary>
    internal sealed class SanctuaryPlayerTrigger : MonoBehaviour
    {
        internal Action? OnPlayerEnter;
        internal Action? OnPlayerExit;

        public void OnTriggerEnter(Collider other)
        {
            if (other != null && PhysicsUtil.IsPlayerMainCollider(other)) OnPlayerEnter?.Invoke();
        }

        public void OnTriggerExit(Collider other)
        {
            if (other != null && PhysicsUtil.IsPlayerMainCollider(other)) OnPlayerExit?.Invoke();
        }
    }

    /// <summary>One turning layer of the portal membrane (a front and a back quad share this parent).</summary>
    internal sealed class SanctuarySwirlLayer
    {
        internal Transform Spinner = null!;
        internal Material Material = null!;
        internal Color Tint;
        internal float DegreesPerSecond, PulseAmount, PulseSpeed, BaseScale;
    }

    /// <summary>
    /// The portal membrane. Dormant: the stone ring stands dark and the membrane does not exist (no light, no swirl). Opening:
    /// a wave of light runs around the ring while the membrane unfolds from its centre and starts turning, ending in a flash.
    /// A flash is also added when someone arrives.
    /// </summary>
    internal sealed class SanctuaryPortalGlow : MonoBehaviour
    {
        internal const float OpenSeconds = 3.4f;
        internal Renderer? Target;
        internal Light? Lamp;
        internal Transform? MembraneRoot;
        internal Transform[] RingBlocks = new Transform[0];
        internal Vector3[] RingScales = new Vector3[0];
        internal Action? OnBurst;
        internal bool Open;
        internal float FlashUntil;
        internal readonly System.Collections.Generic.List<SanctuarySwirlLayer> Layers = new System.Collections.Generic.List<SanctuarySwirlLayer>();
        private float _open;
        private bool _burst;

        /// <summary>Jump to a state without animation (a save that is already open, or a LAB shot).</summary>
        internal void Snap(bool open)
        {
            Open = open; _open = open ? 1f : 0f; _burst = open;
            Apply(0f);
        }

        internal float OpenAmount => _open;

        private static float EaseOutBack(float t)
        {
            const float c1 = 1.70158f, c3 = c1 + 1f;
            var x = t - 1f;
            return 1f + c3 * x * x * x + c1 * x * x;
        }

        public void Update()
        {
            var step = Time.unscaledDeltaTime / (Open ? OpenSeconds : 0.8f);
            _open = Mathf.Clamp01(_open + (Open ? step : -step));
            if (Open && _open >= 1f && !_burst)
            {
                _burst = true;
                FlashUntil = Time.realtimeSinceStartup + 2.2f;
                try { OnBurst?.Invoke(); } catch (Exception) { }
            }
            if (!Open && _open <= 0f) _burst = false;
            var flash = Mathf.Clamp01(FlashUntil - Time.realtimeSinceStartup);
            // A dormant portal (closed, no flash left) looks the same every frame: apply that state once, then do nothing until it wakes up.
            if (!Open && _open <= 0f && flash <= 0f)
            {
                if (_dormantApplied) return;
                _dormantApplied = true;
            }
            else _dormantApplied = false;
            Apply(flash);
        }

        private bool _dormantApplied;

        private void Apply(float flash)
        {
            var visible = _open > 0.001f;
            if (MembraneRoot != null)
            {
                if (MembraneRoot.gameObject.activeSelf != visible) MembraneRoot.gameObject.SetActive(visible);
                var grow = Mathf.Max(0.001f, EaseOutBack(Mathf.Clamp01(_open * 1.15f - 0.15f)));
                MembraneRoot.localScale = Vector3.one * grow;
            }
            if (Lamp != null)
            {
                Lamp.enabled = visible;
                Lamp.intensity = 3.0f * _open * (0.85f + 0.15f * Mathf.Sin(Time.time * 2.4f)) + 9f * flash;
            }
            // light running around the ring while the portal wakes up
            for (var i = 0; i < RingBlocks.Length; i++)
            {
                if (RingBlocks[i] == null) continue;
                var bump = 0f;
                if (_open > 0f && _open < 1f)
                {
                    var front = _open * 1.25f;
                    bump = Mathf.Max(0f, 1f - Mathf.Abs(i / (float)RingBlocks.Length - front) * 8f);
                }
                RingBlocks[i].localScale = RingScales[i] * (1f + 0.3f * bump + 0.12f * flash);
            }
            if (!visible) return;
            var level = Mathf.Clamp01(_open + flash);
            foreach (var layer in Layers)
            {
                var speed = layer.DegreesPerSecond * (0.4f + 0.6f * _open) * (1f + flash * 3f);
                layer.Spinner.Rotate(0f, 0f, speed * Time.unscaledDeltaTime, Space.Self);
                var scale = layer.BaseScale * (1f + layer.PulseAmount * Mathf.Sin(Time.time * layer.PulseSpeed));
                layer.Spinner.localScale = new Vector3(scale, scale, scale);
                var c = layer.Tint; c.a = Mathf.Clamp01(c.a * level);
                if (layer.Material.HasProperty("_TintColor")) layer.Material.SetColor("_TintColor", c);
                if (layer.Material.HasProperty("_Color")) layer.Material.SetColor("_Color", c);
            }
        }
    }

    /// <summary>One thing that appears during the island reveal.</summary>
    internal sealed class SanctuaryRevealItem
    {
        internal Transform T = null!;
        internal Vector3 Position, Scale;
        internal float Delay, Duration, Depth;
        internal bool Grow;
        internal Collider[] Colliders = new Collider[0];
        internal bool Started, Done;
    }

    /// <summary>
    /// The island appears with an animation when the gate is paid: the rock base rises out of the sea, the walkway and the
    /// court follow, then the garden grows, the statues rise and the portal ring assembles. Colliders only come back at the end
    /// of each item's animation; nothing is ever built in the player's way.
    /// </summary>
    internal sealed class SanctuaryReveal : MonoBehaviour
    {
        internal readonly System.Collections.Generic.List<SanctuaryRevealItem> Items = new System.Collections.Generic.List<SanctuaryRevealItem>();
        internal bool Playing { get; private set; }
        internal Action? Finished;
        private float _elapsed;

        internal void Add(Transform t, string phase, string id, float distance, int ordinal)
        {
            var hash = unchecked((uint)id.GetHashCode() * 2654435761u);
            var jitter = (hash % 1000u) / 1000f;
            var item = new SanctuaryRevealItem { T = t, Position = t.position, Scale = t.localScale };
            switch (phase)
            {
                case "rise": item.Delay = 0f; item.Duration = 3.4f; item.Depth = 38f; break;
                case "span": item.Delay = 0.9f + jitter * 0.3f; item.Duration = 1.8f; item.Depth = 10f; break;
                case "court": item.Delay = 2.0f + Mathf.Clamp01(distance / 34f) * 1.6f; item.Duration = 1.1f; item.Depth = 3.5f; break;
                case "flora": item.Delay = 2.6f + jitter * 2.4f; item.Duration = 1.0f; item.Grow = true; break;
                case "statue": item.Delay = 3.4f + ordinal * 0.15f; item.Duration = 1.3f; item.Depth = 4.5f; break;
                case "portal": item.Delay = 5.4f + ordinal * 0.07f; item.Duration = 0.8f; item.Grow = true; break;
                default: item.Delay = 2.0f; item.Duration = 1.0f; item.Depth = 3f; break;
            }
            item.Colliders = t.GetComponentsInChildren<Collider>(true);
            Items.Add(item);
        }

        internal void Play()
        {
            _elapsed = 0f;
            Playing = true;
            foreach (var item in Items)
            {
                item.Started = false; item.Done = false;
                foreach (var c in item.Colliders) if (c != null) c.enabled = false;
                if (item.T != null) item.T.gameObject.SetActive(false);
            }
            enabled = true;
        }

        private static float EaseOutCubic(float t) { var x = 1f - t; return 1f - x * x * x; }
        private static float EaseOutBack(float t) { const float c1 = 1.70158f, c3 = c1 + 1f; var x = t - 1f; return 1f + c3 * x * x * x + c1 * x * x; }

        public void Update()
        {
            if (!Playing) { enabled = false; return; }
            _elapsed += Time.unscaledDeltaTime;
            var remaining = 0;
            foreach (var item in Items)
            {
                if (item.Done || item.T == null) continue;
                var p = Mathf.Clamp01((_elapsed - item.Delay) / item.Duration);
                if (p <= 0f) { remaining++; continue; }
                if (!item.Started)
                {
                    item.Started = true;
                    item.T.gameObject.SetActive(true);
                }
                if (item.Grow) item.T.localScale = item.Scale * Mathf.Max(0.001f, EaseOutBack(p));
                else item.T.position = item.Position - Vector3.up * item.Depth * (1f - EaseOutCubic(p));
                if (p >= 1f)
                {
                    item.T.position = item.Position; item.T.localScale = item.Scale;
                    foreach (var c in item.Colliders) if (c != null) c.enabled = true;
                    item.Done = true;
                }
                else remaining++;
            }
            if (remaining == 0)
            {
                Playing = false;
                enabled = false;
                try { Finished?.Invoke(); } catch (Exception) { }
            }
        }

        /// <summary>Show everything at once (a save that is already open, or a LAB shot).</summary>
        internal void Skip()
        {
            Playing = false;
            enabled = false;
            foreach (var item in Items)
            {
                if (item.T == null) continue;
                item.T.gameObject.SetActive(true);
                item.T.position = item.Position; item.T.localScale = item.Scale;
                foreach (var c in item.Colliders) if (c != null) c.enabled = true;
                item.Done = true;
            }
        }
    }

    /// <summary>Procedural portal textures (no asset is shipped: they are drawn at load time).</summary>
    internal static class PortalTextures
    {
        /// <summary>A spiral of soft arms fading toward the rim. <paramref name="arms"/> arms, <paramref name="twist"/> turns of twist.</summary>
        internal static Texture2D Spiral(int size, int arms, float twist, Color inner, Color outer)
        {
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear, name = "SlimeBridge portal spiral" };
            var pixels = new Color32[size * size];
            for (var y = 0; y < size; y++)
                for (var x = 0; x < size; x++)
                {
                    var dx = (x + 0.5f) / size * 2f - 1f; var dy = (y + 0.5f) / size * 2f - 1f;
                    var r = Mathf.Sqrt(dx * dx + dy * dy);
                    var angle = Mathf.Atan2(dy, dx);
                    var wave = 0.5f + 0.5f * Mathf.Sin(arms * angle + twist * 6.2831853f * r * 1.4f);
                    wave = Mathf.Pow(wave, 1.6f);
                    var edge = Mathf.Clamp01((1f - r) / 0.18f);
                    var core = Mathf.Clamp01(1f - r * 1.6f);
                    var alpha = Mathf.Clamp01((wave * 0.85f + core * 0.55f) * edge);
                    var color = Color.Lerp(inner, outer, Mathf.Clamp01(r * 1.25f));
                    pixels[y * size + x] = new Color32((byte)(color.r * 255f), (byte)(color.g * 255f), (byte)(color.b * 255f), (byte)(alpha * 255f));
                }
            texture.SetPixels32(pixels); texture.Apply(false, false);
            return texture;
        }

        /// <summary>A soft round glow.</summary>
        internal static Texture2D Glow(int size, Color color)
        {
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear, name = "SlimeBridge portal glow" };
            var pixels = new Color32[size * size];
            for (var y = 0; y < size; y++)
                for (var x = 0; x < size; x++)
                {
                    var dx = (x + 0.5f) / size * 2f - 1f; var dy = (y + 0.5f) / size * 2f - 1f;
                    var a = Mathf.Pow(Mathf.Clamp01(1f - Mathf.Sqrt(dx * dx + dy * dy)), 2.2f);
                    pixels[y * size + x] = new Color32((byte)(color.r * 255f), (byte)(color.g * 255f), (byte)(color.b * 255f), (byte)(a * 255f));
                }
            texture.SetPixels32(pixels); texture.Apply(false, false);
            return texture;
        }
    }
}
