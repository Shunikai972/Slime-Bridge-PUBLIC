using SlimeBridge.Shared;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

internal static class RegistryRaceChecks
{
    [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle OpenDirectory(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(SafeFileHandle h, uint code, byte[] input, uint len, IntPtr output, uint olen, out uint returned, IntPtr overlapped);

    private static byte[] MountBuffer(string outside)
    {
        var sub = Encoding.Unicode.GetBytes("\\??\\" + outside);
        var print = Encoding.Unicode.GetBytes(outside);
        var data = new byte[16 + sub.Length + 2 + print.Length + 2];
        void U16(int offset, int n) => BitConverter.GetBytes((ushort)n).CopyTo(data, offset);
        BitConverter.GetBytes(0xA0000003u).CopyTo(data, 0);
        U16(4, data.Length - 8); U16(8, 0); U16(10, sub.Length); U16(12, sub.Length + 2); U16(14, print.Length);
        sub.CopyTo(data, 16); print.CopyTo(data, 16 + sub.Length + 2);
        return data;
    }
    internal static void Run(Action<bool, string> check)
    {
        var root = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "registry-race-" + Guid.NewGuid().ToString("N"));
        var slot = Path.Combine(root, "Lab", "RuntimeArtifacts", "capture-test", "slot");
        var parkedNormal = Path.Combine(root, "normal-park");
        var parkedLink = Path.Combine(root, "junction-park");
        var outside = Path.Combine(root, "outside");
        Directory.CreateDirectory(slot);
        Directory.CreateDirectory(outside);
        var arguments = new ProcessStartInfo("cmd.exe") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "/d", "/c", "mklink", "/J", parkedLink, outside }) arguments.ArgumentList.Add(arg);
        using (var command = Process.Start(arguments)!)
        {
            var stdout = command.StandardOutput.ReadToEnd();
            var stderr = command.StandardError.ReadToEnd();
            command.WaitForExit();
            if (command.ExitCode != 0) throw new Exception("Junction fixture unavailable: " + stdout + stderr);
        }
        check((File.GetAttributes(parkedLink) & FileAttributes.ReparsePoint) != 0, "real NTFS junction fixture");
        try
        {
            var escaped = new ConcurrentQueue<string>();
            using (var watcher = new FileSystemWatcher(outside) { NotifyFilter = NotifyFilters.FileName, EnableRaisingEvents = true })
            {
                watcher.Created += (_, e) => escaped.Enqueue(e.Name ?? "unknown");
                int phase = 0, running = 1;
                var swapping = new Thread(() => {
                    while (Volatile.Read(ref running) == 1 || phase != 0)
                    {
                        try
                        {
                            switch (phase)
                            {
                                case 0: Directory.Move(slot, parkedNormal); phase = 1; break;
                                case 1: Directory.Move(parkedLink, slot); phase = 2; break;
                                case 2: Thread.SpinWait(100); Directory.Move(slot, parkedLink); phase = 3; break;
                                case 3: Directory.Move(parkedNormal, slot); phase = 0; break;
                            }
                        }
                        catch (IOException) { Thread.Yield(); }
                    }
                });
                swapping.Start();
                int attempts = 0, published = 0;
                var clock = Stopwatch.StartNew();
                try
                {
                    while (clock.Elapsed < TimeSpan.FromSeconds(2) && escaped.IsEmpty)
                    {
                        attempts++;
                        try { RegistrySnapshotFormat.PublishCaptureFile(slot, RegistrySnapshotFormat.SnapshotFileName, "synthetic"); published++; }
                        catch (IOException) { }
                    }
                }
                finally
                {
                    Volatile.Write(ref running, 0);
                    if (!swapping.Join(10000)) throw new Exception("scratch swap thread did not stop");
                }
                Thread.Sleep(100); // Drain filesystem notification events after the writer stopped.
                Console.WriteLine("Race: attempts=" + attempts + " published=" + published + " escaped=" + escaped.Count);
                check(escaped.IsEmpty && !Directory.EnumerateFileSystemEntries(outside).Any(), "no escaped files during junction swaps");
            }

            var mutOutside = Path.Combine(root, "mut-outside");
            Directory.CreateDirectory(mutOutside);
            var outsideSnapshot = Path.Combine(mutOutside, RegistrySnapshotFormat.SnapshotFileName);
            var buffer = MountBuffer(mutOutside);
            int mutEscapes = 0, mutPublished = 0, mutAttempts = 200;
            for (int i = 0; i < mutAttempts; i++)
            {
                var mutSlot = Path.Combine(root, "slot-" + i);
                Directory.CreateDirectory(mutSlot);
                using var mut = OpenDirectory(mutSlot, 0x100, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
                var ready = new ManualResetEventSlim(false);
                var go = new ManualResetEventSlim(false);
                var thread = new Thread(() =>
                {
                    ready.Set();
                    go.Wait();
                    for (int m = 0; m < 50; m++)
                        DeviceIoControl(mut, 0x000900A4, buffer, (uint)buffer.Length, IntPtr.Zero, 0, out _, IntPtr.Zero);
                });
                thread.Start();
                ready.Wait();
                go.Set();
                try
                {
                    RegistrySnapshotFormat.PublishCaptureFile(mutSlot, RegistrySnapshotFormat.SnapshotFileName, "mut-" + i);
                    mutPublished++;
                }
                catch (IOException) { }
                thread.Join();
                if (File.Exists(outsideSnapshot))
                {
                    mutEscapes++;
                    File.Delete(outsideSnapshot);
                }
                try
                {
                    if ((File.GetAttributes(mutSlot) & FileAttributes.ReparsePoint) != 0) Directory.Delete(mutSlot);
                    else Directory.Delete(mutSlot, true);
                }
                catch { }
            }
            Console.WriteLine("MutationRace: attempts=" + mutAttempts + " published=" + mutPublished + " escaped=" + mutEscapes);
            check(mutEscapes == 0 && !Directory.EnumerateFileSystemEntries(mutOutside).Any(), "no escaped files during in-place reparse mutation races");
        }
        finally
        {
            // The swapper restores phase zero before exiting. Remove only the junction, never its target.
            if ((File.GetAttributes(parkedLink) & FileAttributes.ReparsePoint) != 0) Directory.Delete(parkedLink);
            Directory.Delete(root, true);
        }
    }
}
