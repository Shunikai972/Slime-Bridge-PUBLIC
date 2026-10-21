using SlimeBridge.Shared;

// The raw RootDirectory IntPtr embedded in OBJECT_ATTRIBUTES must stay referenced by the
// SafeHandle until NtCreateFile returns, even if the owning SafeHandle is disposed concurrently.
internal static class RegistryLifetimeChecks
{
    internal static void Run(Action<bool, string> check)
    {
        var root = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "registry-lifetime-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string? failure = null;
            bool hookRan = false;
            using (var lease = RegistrySnapshotFormat.PinCaptureDirectory(root))
            {
                RegistrySnapshotFormat.RelativeRootCapturedForTest = handle => { hookRan = true; handle.Dispose(); };
                try
                {
                    using (RegistrySnapshotFormat.OpenRelative(lease.LeafHandle, "lifetime.json", false)) { }
                }
                catch (Exception ex) { failure = ex.Message; }
                finally { RegistrySnapshotFormat.RelativeRootCapturedForTest = null; }
            }
            check(hookRan, "root handle disposal injected between capture and NtCreateFile");
            check(failure == null && File.Exists(Path.Combine(root, "lifetime.json")),
                "RootDirectory raw handle stays referenced through NtCreateFile (" + (failure ?? "ok") + ")");
            bool refused = false;
            var lease2 = RegistrySnapshotFormat.PinCaptureDirectory(root);
            var leaf = lease2.LeafHandle;
            lease2.Dispose();
            try { using (RegistrySnapshotFormat.OpenRelative(leaf, "after-dispose.json", false)) { } }
            catch (ObjectDisposedException) { refused = true; }
            catch (IOException) { refused = true; }
            check(refused && !File.Exists(Path.Combine(root, "after-dispose.json")), "disposed RootDirectory handle is never used");
        }
        finally { Directory.Delete(root, true); }
    }
}
