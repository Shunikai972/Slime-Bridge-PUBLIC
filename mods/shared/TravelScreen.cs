using System;
using System.Globalization;
using UnityEngine;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// A game started in the background runs small and windowed. When the player arrives in it and they had fullscreen
    /// (the launcher says so), it goes back to their saved resolution and fullscreen mode (or borderless fullscreen at
    /// the monitor's native resolution), once.
    /// </summary>
    internal static class TravelScreen
    {
        private static bool _wanted = Environment.GetEnvironmentVariable("SLIMEBRIDGE_FULLSCREEN_ON_ARRIVAL") == "1";

        internal static void FullscreenAgainOnArrival()
        {
            if (!_wanted) return;
            _wanted = false;
            try
            {
                var width = ParsePositive("SLIMEBRIDGE_RESTORE_WIDTH");
                var height = ParsePositive("SLIMEBRIDGE_RESTORE_HEIGHT");
                if (width <= 0 || height <= 0)
                {
                    var main = Display.main;
                    if (main != null && main.systemWidth > 0 && main.systemHeight > 0)
                    {
                        width = main.systemWidth;
                        height = main.systemHeight;
                    }
                    else
                    {
                        var monitor = Screen.currentResolution;
                        width = monitor.width;
                        height = monitor.height;
                    }
                }
                var mode = FullScreenMode.FullScreenWindow;
                var rawMode = Environment.GetEnvironmentVariable("SLIMEBRIDGE_RESTORE_FSMODE");
                if (int.TryParse(rawMode, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed) &&
                    parsed >= 0 && parsed <= 2)
                {
                    mode = (FullScreenMode)parsed;
                }
                if (width > 0 && height > 0)
                    Screen.SetResolution(width, height, mode);
            }
            catch (Exception) { /* the window simply stays as it is */ }
        }

        private static int ParsePositive(string key) =>
            int.TryParse(Environment.GetEnvironmentVariable(key), NumberStyles.Integer, CultureInfo.InvariantCulture, out var v) && v > 0 ? v : 0;
    }
}
