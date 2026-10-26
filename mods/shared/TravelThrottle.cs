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
        private bool _active;
        private int _fps, _vsync, _quality;
        private float _timeScale, _volume;
        private bool _audioPause;
        private readonly List<Camera> _disabled = new List<Camera>();

        internal bool Active => _active;

        internal void Apply(bool freeze)
        {
            if (_active) { Restore(); }
            _fps = Application.targetFrameRate; _vsync = QualitySettings.vSyncCount; _quality = QualitySettings.GetQualityLevel();
            _timeScale = Time.timeScale; _volume = AudioListener.volume; _audioPause = AudioListener.pause;
            _active = true;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = freeze ? FrozenFps : SlowFps;
            QualitySettings.SetQualityLevel(0, false);
            AudioListener.volume = 0f;
            if (freeze) { AudioListener.pause = true; Time.timeScale = 0f; }
            _disabled.Clear();
            foreach (var camera in Camera.allCameras)
            {
                if (camera == null || !camera.enabled || camera.targetTexture != null) continue;
                camera.enabled = false;
                _disabled.Add(camera);
            }
        }

        internal void Restore()
        {
            if (!_active) return;
            _active = false;
            foreach (var camera in _disabled) { try { if (camera != null) camera.enabled = true; } catch (Exception) { } }
            _disabled.Clear();
            try
            {
                Time.timeScale = _timeScale > 0f ? _timeScale : 1f;
                AudioListener.pause = _audioPause; AudioListener.volume = _volume;
                QualitySettings.SetQualityLevel(_quality, false);
                QualitySettings.vSyncCount = _vsync;
                Application.targetFrameRate = _fps;
            }
            catch (Exception) { /* a failed restore of one knob must not leave the rest throttled */ }
        }
    }
}
