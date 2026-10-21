using SlimeBridge.Shared;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

internal static class RegistryCleanupChecks
{
    [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle OpenDirectory(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(SafeFileHandle handle, uint code, byte[] input, uint length,
        IntPtr output, uint outputLength, out uint returned, IntPtr overlapped);

    internal static void Run(Action<bool, string> check)
    {
        foreach (bool afterFlush in new[] { false, true })
        {
            var root = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "registry-cleanup-" + Guid.NewGuid().ToString("N"));
            var approved = Path.Combine(root, "approved");
            var outside = Path.Combine(root, "outside");
            Directory.CreateDirectory(approved);
            Directory.CreateDirectory(outside);
            var target = Path.Combine(approved, RegistrySnapshotFormat.SnapshotFileName);
            var sentinel = Path.Combine(outside, RegistrySnapshotFormat.SnapshotFileName);
            File.WriteAllText(sentinel, "outside sentinel must survive");
            using var mutator = OpenDirectory(approved, 0x100, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            if (mutator.IsInvalid) throw new IOException("scratch mutation handle unavailable");
            bool mutated = false, cleanupCalled = false, fileReleasedEarly = false, rejected = false;
            try
            {
                Action fail = () => throw new IOException("injected publication failure");
                if (afterFlush) RegistrySnapshotFormat.CaptureFlushedForTest = fail;
                else RegistrySnapshotFormat.CaptureFileCreatedForTest = fail;
                RegistrySnapshotFormat.BeforeFailedCaptureCleanupForTest = () =>
                {
                    cleanupCalled = true;
                    try { File.Delete(target); fileReleasedEarly = true; }
                    catch (IOException) { return; } // A live exclusive handle must reject this swap.
                    var buffer = RegistryBoundaryChecks.MountBuffer(outside);
                    if (!DeviceIoControl(mutator, 0x000900A4, buffer, (uint)buffer.Length, IntPtr.Zero, 0, out _, IntPtr.Zero))
                        throw new IOException("scratch cleanup conversion failed: " + Marshal.GetLastWin32Error());
                    mutated = true;
                };
                try { RegistrySnapshotFormat.PublishCaptureFile(approved, RegistrySnapshotFormat.SnapshotFileName, "partially published bytes"); }
                catch (IOException) { rejected = true; }
                finally
                {
                    RegistrySnapshotFormat.CaptureFileCreatedForTest = null;
                    RegistrySnapshotFormat.CaptureFlushedForTest = null;
                    RegistrySnapshotFormat.BeforeFailedCaptureCleanupForTest = null;
                    if (mutated) { RegistryBoundaryChecks.Unset(mutator); mutated = false; }
                }
                check(rejected && cleanupCalled, "injected " + (afterFlush ? "post-flush" : "post-create") + " failure reaches real cleanup");
                check(File.Exists(sentinel) && File.ReadAllText(sentinel) == "outside sentinel must survive",
                    "failure cleanup never deletes an outside replacement");
                check(!fileReleasedEarly && !File.Exists(target), "failed capture is deleted through its still-exclusive owned handle");
            }
            finally
            {
                RegistrySnapshotFormat.CaptureFileCreatedForTest = null;
                RegistrySnapshotFormat.CaptureFlushedForTest = null;
                RegistrySnapshotFormat.BeforeFailedCaptureCleanupForTest = null;
                if (mutated) RegistryBoundaryChecks.Unset(mutator);
                mutator.Dispose();
                Directory.Delete(root, true);
            }
        }
    }
}
