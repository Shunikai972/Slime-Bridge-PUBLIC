using System;
using UnityEngine;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// A game started in the background runs small and windowed. When the player arrives in it and they had fullscreen
    /// (the launcher says so), it goes back to borderless fullscreen at the monitor's resolution, once.
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
                var monitor = Screen.currentResolution;
                Screen.SetResolution(monitor.width, monitor.height, FullScreenMode.FullScreenWindow);
            }
            catch (Exception) { /* the window simply stays as it is */ }
        }
    }
}
