using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using UnityEngine;

namespace SlimeBridge.Shared
{
    /// <summary>Read-only focus checks; unknown foreground ownership never authorizes automation.</summary>
    internal static class BackgroundGameFocus
    {
        private static readonly uint OwnPid = (uint)Process.GetCurrentProcess().Id;
        [DllImport("user32.dll", ExactSpelling = true)]
        private static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll", ExactSpelling = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

        internal static bool IsBackground()
        {
            try
            {
                if (Application.platform != RuntimePlatform.WindowsPlayer || Application.isFocused) return false;
                var window = GetForegroundWindow();
                if (window == IntPtr.Zero) return false;
                var thread = GetWindowThreadProcessId(window, out var foregroundPid);
                // A focus switch during the read, or stale Unity focus, must not authorize a skip.
                return window == GetForegroundWindow() && Allow(Application.isFocused, OwnPid, foregroundPid, thread != 0);
            }
            catch (Exception) { return false; }
        }

        internal static bool Allow(bool applicationFocused, uint ownPid, uint foregroundPid, bool validForeground)
        {
            return !applicationFocused && validForeground && ownPid != 0 && foregroundPid != 0 && ownPid != foregroundPid;
        }
    }
}
