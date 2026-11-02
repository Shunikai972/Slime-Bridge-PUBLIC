using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Reduces a game to the minimum while the player is in the other one: few FPS, lowest quality, muted, no rendering, and for
    /// FREEZE the simulation is paused too. Everything it changes is remembered and put back by <see cref="Restore"/>.
    /// </summary>
    internal sealed class TravelThrottle
    {
        internal const int FrozenFps = 4, SlowFps = 10;
        private bool _active, _audioCaptured, _freeze;
        private int _fps, _vsync, _quality;
        private float _timeScale, _volume;
        private bool _audioPause;
        private bool _wasBackground;
        private float _focusedSince = -1f;
        private readonly List<Camera> _disabled = new List<Camera>();

        internal bool Active => _active || _audioCaptured;

        /// <summary>Silence a loading destination without pausing its world, rendering or startup.</summary>
        internal void MuteAudio()
        {
            if (!_audioCaptured)
            {
                _volume = AudioListener.volume; _audioPause = AudioListener.pause;
                _audioCaptured = true;
            }
            AudioListener.volume = 0f;
        }

        /// <summary>Native scene/settings writers must not make a parked game audible again.</summary>
        internal void Maintain()
        {
            if (!Active) return;
            AudioListener.volume = 0f;
            if (_active && _freeze) AudioListener.pause = true;
        }

        internal void Apply(bool freeze)
        {
            if (_active) { Restore(); }
            _wasBackground = false; _focusedSince = -1f;
            _fps = Application.targetFrameRate; _vsync = QualitySettings.vSyncCount; _quality = QualitySettings.GetQualityLevel();
            _timeScale = Time.timeScale;
            MuteAudio();
            _freeze = freeze;
            _active = true;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = freeze ? FrozenFps : SlowFps;
            QualitySettings.SetQualityLevel(0, false);
            Maintain();
            if (freeze) { AudioListener.pause = true; Time.timeScale = 0f; }
            _disabled.Clear();
            foreach (var camera in Camera.allCameras)
            {
                if (camera == null || !camera.enabled || camera.targetTexture != null) continue;
                camera.enabled = false;
                _disabled.Add(camera);
            }
        }

        /// <summary>A lingering source foreground is not a manual return: first observe a real departure.</summary>
        internal bool ReturnedByHand(bool focused, float now)
        {
            if (!_active) return false;
            if (!focused) { _wasBackground = true; _focusedSince = -1f; return false; }
            if (!_wasBackground) return false;
            if (_focusedSince < 0f) _focusedSince = now;
            return now - _focusedSince > 4f;
        }

        internal void Restore()
        {
            if (!Active) return;
            if (_active)
            {
                _active = false;
                foreach (var camera in _disabled) { try { if (camera != null) camera.enabled = true; } catch (Exception) { } }
                _disabled.Clear();
                // Restore independently: a quality failure must not retain mute or the FPS cap.
                try { Time.timeScale = _timeScale > 0f ? _timeScale : 1f; } catch (Exception) { }
                try { QualitySettings.SetQualityLevel(_quality, false); } catch (Exception) { }
                try { QualitySettings.vSyncCount = _vsync; } catch (Exception) { }
                try { Application.targetFrameRate = _fps; } catch (Exception) { }
            }
            if (_audioCaptured)
            {
                // Audio last: applying native quality settings may update the game's sound settings.
                AudioListener.pause = _audioPause; AudioListener.volume = _volume;
                _audioCaptured = false;
            }
        }
    }
}
