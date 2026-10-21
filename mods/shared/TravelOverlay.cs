using System;
using UnityEngine;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// The portal transition drawn over the game (IMGUI, procedural textures only): an iris of portal light closes on the screen,
    /// a waiting card with bouncing slime blobs holds it, and the iris opens again where the player emerges.
    /// The host calls <see cref="Draw"/> from its OnGUI; everything else may be called from the main thread at any time.
    /// </summary>
    internal sealed class TravelOverlay
    {
        internal enum Phase { Clear, Closing, Covered, Opening }

        private static readonly Color Deep = new Color(0.10f, 0.04f, 0.22f, 1f);
        private static readonly Color Glow = new Color(0.62f, 0.32f, 0.98f, 1f);
        private static readonly Color Rim = new Color(1f, 0.62f, 0.93f, 1f);
        private static readonly Color[] BlobColors =
        {
            new Color(1f, 0.55f, 0.78f), new Color(0.55f, 0.85f, 1f), new Color(1f, 0.85f, 0.4f),
            new Color(0.6f, 1f, 0.7f), new Color(0.85f, 0.65f, 1f), new Color(1f, 0.65f, 0.4f),
        };

        private Texture2D? _hole, _blob;
        private Phase _phase = Phase.Clear;
        private float _t, _duration = 1f;
        private Action? _onDone;
        private string _title = "", _subtitle = "";
        private GUIStyle? _titleStyle, _subStyle;
        private float _lastTime;

        private string _hint = "";
        private float _hintUntil;
        private GUIStyle? _hintStyle;

        /// <summary>A short message at the top of the screen (fades after <paramref name="seconds"/>).</summary>
        internal void ShowHint(string text, float seconds)
        {
            _hint = text; _hintUntil = Time.realtimeSinceStartup + seconds;
        }

        private void DrawHint()
        {
            if (_hint.Length == 0 || Time.realtimeSinceStartup > _hintUntil) return;
            if (Event.current != null && Event.current.type != EventType.Repaint) return;
            if (_hintStyle == null) _hintStyle = new GUIStyle(GUI.skin.label) { alignment = TextAnchor.MiddleCenter, fontStyle = FontStyle.Bold, wordWrap = true };
            var w = (float)Screen.width; var h = (float)Screen.height;
            _hintStyle.fontSize = Mathf.Max(15, (int)(h * 0.028f));
            var alpha = Mathf.Clamp01((_hintUntil - Time.realtimeSinceStartup) / 0.6f);
            var previousColor = GUI.color; var previousDepth = GUI.depth;
            GUI.depth = -9000;
            var box = new Rect(w * 0.2f, h * 0.07f, w * 0.6f, h * 0.075f);
            GUI.color = new Color(0.1f, 0.04f, 0.22f, 0.72f * alpha);
            GUI.DrawTexture(box, Texture2D.whiteTexture);
            GUI.color = new Color(1f, 1f, 1f, alpha);
            GUI.Label(box, _hint, _hintStyle);
            GUI.color = previousColor; GUI.depth = previousDepth;
        }

        internal Phase Current => _phase;
        internal bool IsCovering => _phase != Phase.Clear;
        internal bool IsCovered => _phase == Phase.Covered;

        /// <summary>The iris closes (the player enters the portal); <paramref name="onCovered"/> runs when the screen is fully covered.</summary>
        internal void Close(string title, string subtitle, float seconds, Action? onCovered)
        {
            _title = title; _subtitle = subtitle;
            if (_phase == Phase.Covered) { onCovered?.Invoke(); return; }
            Begin(Phase.Closing, seconds, onCovered);
        }

        /// <summary>Cover instantly (a freshly started destination game waits behind the card until the player arrives).</summary>
        internal void CoverNow(string title, string subtitle)
        {
            _title = title; _subtitle = subtitle; _onDone = null;
            _phase = Phase.Covered; _t = 0f; _lastTime = Time.realtimeSinceStartup;
        }

        /// <summary>The iris opens (the player emerges from the portal); <paramref name="onClear"/> runs when the screen is clear.</summary>
        internal void Open(float seconds, Action? onClear)
        {
            if (_phase == Phase.Clear) { onClear?.Invoke(); return; }
            Begin(Phase.Opening, seconds, onClear);
        }

        internal void ClearNow()
        {
            _phase = Phase.Clear; _onDone = null;
        }

        private void Begin(Phase phase, float seconds, Action? done)
        {
            _phase = phase; _t = 0f; _duration = Mathf.Max(0.05f, seconds); _onDone = done; _lastTime = Time.realtimeSinceStartup;
        }

        private static float Ease(float x) => x * x * (3f - 2f * x);

        private void Advance()
        {
            var now = Time.realtimeSinceStartup;
            var dt = Mathf.Clamp(now - _lastTime, 0f, 0.25f);
            _lastTime = now;
            if (_phase == Phase.Closing || _phase == Phase.Opening)
            {
                _t += dt / _duration;
                if (_t >= 1f)
                {
                    var done = _onDone; _onDone = null;
                    _phase = _phase == Phase.Closing ? Phase.Covered : Phase.Clear;
                    _t = 0f;
                    done?.Invoke();
                }
            }
            else if (_phase == Phase.Covered) _t += dt;
        }

        private void EnsureTextures()
        {
            if (_hole == null)
            {
                const int n = 256;
                _hole = new Texture2D(n, n, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear };
                var px = new Color32[n * n];
                for (var y = 0; y < n; y++)
                    for (var x = 0; x < n; x++)
                    {
                        // 0 at the centre, 1 on the edge of the texture: transparent hole up to 0.36, glowing rim, opaque from 0.5.
                        var d = Mathf.Sqrt((x - n / 2f) * (x - n / 2f) + (y - n / 2f) * (y - n / 2f)) / (n / 2f);
                        var a = Mathf.Clamp01((d - 0.34f) / 0.12f);
                        var rim = Mathf.Clamp01(1f - Mathf.Abs(d - 0.40f) / 0.07f);
                        var c = Color.Lerp(Deep, Rim, rim * 0.85f);
                        px[y * n + x] = new Color32((byte)(c.r * 255f), (byte)(c.g * 255f), (byte)(c.b * 255f), (byte)(Mathf.Max(a, rim * 0.9f) * 255f));
                    }
                _hole.SetPixels32(px); _hole.Apply(false, false);
            }
            if (_blob == null)
            {
                const int n = 64;
                _blob = new Texture2D(n, n, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear };
                var px = new Color32[n * n];
                for (var y = 0; y < n; y++)
                    for (var x = 0; x < n; x++)
                    {
                        var dx = (x - n / 2f) / (n / 2f); var dy = (y - n * 0.45f) / (n / 2f);
                        var d = Mathf.Sqrt(dx * dx + dy * dy * 1.15f);
                        var a = Mathf.Clamp01((1f - d) / 0.12f);
                        var shade = 0.78f + 0.22f * Mathf.Clamp01(1f - d) + (dy < -0.25f && Mathf.Abs(dx + 0.25f) < 0.25f ? 0.2f : 0f);
                        px[y * n + x] = new Color32((byte)(Mathf.Min(1f, shade) * 255f), (byte)(Mathf.Min(1f, shade) * 255f), (byte)(Mathf.Min(1f, shade) * 255f), (byte)(a * 255f));
                    }
                _blob.SetPixels32(px); _blob.Apply(false, false);
            }
        }

        /// <summary>Call from OnGUI (Repaint events only draw).</summary>
        internal void Draw()
        {
            if (_phase == Phase.Clear) { DrawHint(); return; }
            if (Event.current != null && Event.current.type != EventType.Repaint) return;
            Advance();
            if (_phase == Phase.Clear) return;
            EnsureTextures();
            var w = (float)Screen.width; var h = (float)Screen.height;
            var diag = Mathf.Sqrt(w * w + h * h) * 0.5f;
            var previousDepth = GUI.depth; var previousColor = GUI.color;
            GUI.depth = -10000;
            float open;                                                    // 0 = covered, 1 = clear
            if (_phase == Phase.Closing) open = 1f - Ease(Mathf.Clamp01(_t));
            else if (_phase == Phase.Opening) open = Ease(Mathf.Clamp01(_t));
            else open = 0f;
            var radius = open * (diag * 1.15f);
            GUI.color = Color.white;
            if (open >= 0.995f) { GUI.color = previousColor; GUI.depth = previousDepth; return; }
            if (radius < 2f)
            {
                GUI.color = Deep;
                GUI.DrawTexture(new Rect(0f, 0f, w, h), Texture2D.whiteTexture);
            }
            else
            {
                // The textured square has its hole at 0.34..0.5 of its half-width; scale it so the hole matches the radius.
                var half = radius / 0.40f;
                var rect = new Rect(w / 2f - half, h / 2f - half, half * 2f, half * 2f);
                GUI.color = Color.white;
                GUI.DrawTexture(rect, _hole);
                GUI.color = Deep;
                if (rect.yMin > 0f) GUI.DrawTexture(new Rect(0f, 0f, w, rect.yMin), Texture2D.whiteTexture);
                if (rect.yMax < h) GUI.DrawTexture(new Rect(0f, rect.yMax, w, h - rect.yMax), Texture2D.whiteTexture);
                if (rect.xMin > 0f) GUI.DrawTexture(new Rect(0f, Mathf.Max(0f, rect.yMin), rect.xMin, Mathf.Min(h, rect.yMax) - Mathf.Max(0f, rect.yMin)), Texture2D.whiteTexture);
                if (rect.xMax < w) GUI.DrawTexture(new Rect(rect.xMax, Mathf.Max(0f, rect.yMin), w - rect.xMax, Mathf.Min(h, rect.yMax) - Mathf.Max(0f, rect.yMin)), Texture2D.whiteTexture);
            }
            var cover = 1f - open;
            if (cover > 0.55f) DrawCard(w, h, Mathf.Clamp01((cover - 0.55f) / 0.45f));
            GUI.color = previousColor; GUI.depth = previousDepth;
        }

        private void DrawCard(float w, float h, float alpha)
        {
            var time = Time.realtimeSinceStartup;
            if (_titleStyle == null)
            {
                _titleStyle = new GUIStyle(GUI.skin.label) { alignment = TextAnchor.MiddleCenter, fontStyle = FontStyle.Bold, wordWrap = true };
                _subStyle = new GUIStyle(GUI.skin.label) { alignment = TextAnchor.MiddleCenter, wordWrap = true };
            }
            // Swirl of slime blobs orbiting and bouncing around the centre.
            var size = Mathf.Min(w, h) * 0.07f;
            for (var i = 0; i < BlobColors.Length; i++)
            {
                var angle = time * 1.15f + i * (Mathf.PI * 2f / BlobColors.Length);
                var orbit = Mathf.Min(w, h) * (0.14f + 0.012f * Mathf.Sin(time * 2f + i));
                var bounce = Mathf.Abs(Mathf.Sin(time * 3.2f + i * 0.9f)) * size * 0.9f;
                var cx = w / 2f + Mathf.Cos(angle) * orbit;
                var cy = h * 0.40f + Mathf.Sin(angle) * orbit * 0.55f - bounce;
                var squash = 1f + 0.18f * Mathf.Sin(time * 6.4f + i);
                var c = BlobColors[i]; c.a = alpha;
                GUI.color = c;
                GUI.DrawTexture(new Rect(cx - size * squash / 2f, cy - size / squash / 2f, size * squash, size / squash), _blob);
            }
            var dots = new string('.', 1 + (int)(time * 2.5f) % 3);
            _titleStyle.fontSize = Mathf.Max(18, (int)(h * 0.045f));
            _subStyle!.fontSize = Mathf.Max(13, (int)(h * 0.026f));
            GUI.color = new Color(1f, 1f, 1f, alpha);
            GUI.Label(new Rect(w * 0.1f, h * 0.55f, w * 0.8f, h * 0.08f), _title + dots, _titleStyle);
            GUI.color = new Color(0.86f, 0.8f, 1f, alpha * 0.9f);
            GUI.Label(new Rect(w * 0.15f, h * 0.63f, w * 0.7f, h * 0.06f), _subtitle, _subStyle);
        }
    }

    /// <summary>The few strings the transition shows, in the game's language (English fallback).</summary>
    internal static class TravelText
    {
        internal static string Game(string realm) => realm == "SR2" ? "Slime Rancher 2" : "Slime Rancher";

        internal static string Departing(string destinationRealm)
        {
            var french = Application.systemLanguage == SystemLanguage.French;
            return (french ? "Voyage vers " : "Travelling to ") + Game(destinationRealm);
        }

        internal static string Waiting(string destinationRealm)
        {
            var french = Application.systemLanguage == SystemLanguage.French;
            return french ? "Le portail s’ouvre sur un autre ranch. Ta sauvegarde t’y suit." : "The portal opens on another ranch. Your save follows you there.";
        }

        internal static string Parked(string homeRealm)
        {
            var french = Application.systemLanguage == SystemLanguage.French;
            return (french ? "Tu es dans " : "You are in ") + Game(homeRealm == "SR1" ? "SR2" : "SR1");
        }

        internal static string Arriving()
        {
            return Application.systemLanguage == SystemLanguage.French ? "Arrivée en cours" : "Arriving";
        }
    }
}
