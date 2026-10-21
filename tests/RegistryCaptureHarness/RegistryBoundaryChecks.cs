using SlimeBridge.Shared;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

internal static class RegistryBoundaryChecks
{
    [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle OpenDirectory(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint FinalPath(SafeFileHandle handle, StringBuilder path, uint capacity, uint flags);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(SafeFileHandle handle, uint code, byte[] input, uint length,
        IntPtr output, uint outputLength, out uint returned, IntPtr overlapped);

    internal static byte[] MountBuffer(string target)
    {
        var substitute = Encoding.Unicode.GetBytes("\\??\\" + target);
        var printable = Encoding.Unicode.GetBytes(target);
        var data = new byte[16 + substitute.Length + 2 + printable.Length + 2];
        void U16(int offset, int value) => BitConverter.GetBytes((ushort)value).CopyTo(data, offset);
        BitConverter.GetBytes(0xA0000003u).CopyTo(data, 0);
        U16(4, data.Length - 8); U16(8, 0); U16(10, substitute.Length);
        U16(12, substitute.Length + 2); U16(14, printable.Length);
        substitute.CopyTo(data, 16); printable.CopyTo(data, 16 + substitute.Length + 2);
        return data;
    }

    internal static void Unset(SafeFileHandle mutator)
    {
        var buffer = new byte[8];
        BitConverter.GetBytes(0xA0000003u).CopyTo(buffer, 0);
        if (!DeviceIoControl(mutator, 0x000900AC, buffer, (uint)buffer.Length, IntPtr.Zero, 0, out _, IntPtr.Zero))
            throw new IOException("scratch junction removal failed: " + Marshal.GetLastWin32Error());
    }

    internal static void Run(Action<bool, string> check)
    {
        var root = Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!, "registry-boundary-" + Guid.NewGuid().ToString("N"));
        var parent = Path.Combine(root, "approved");
        var leaf = Path.Combine(parent, "child");
        var outside = Path.Combine(root, "outside");
        var outsideLeaf = Path.Combine(outside, "child");
        Directory.CreateDirectory(leaf);
        Directory.CreateDirectory(outsideLeaf);
        using var mutator = OpenDirectory(parent, 0x100, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        if (mutator.IsInvalid) throw new IOException("scratch mutation handle unavailable");
        bool mutated = false, converted = false, rejected = false, outsideOpened = false;
        try
        {
            RegistrySnapshotFormat.DirectoryOpenedForTest = handle =>
            {
                var path = new StringBuilder(1024);
                if (FinalPath(handle, path, (uint)path.Capacity, 0) == 0) throw new IOException("scratch final path failed");
                if (path.ToString().Contains(outside, StringComparison.OrdinalIgnoreCase)) outsideOpened = true;
            };
            RegistrySnapshotFormat.DirectoryPinnedForTest = path =>
            {
                if (path == parent)
                {
                    if (Directory.Exists(leaf)) Directory.Delete(leaf);
                    var buffer = MountBuffer(outside);
                    converted = DeviceIoControl(mutator, 0x000900A4, buffer, (uint)buffer.Length, IntPtr.Zero, 0, out _, IntPtr.Zero);
                    if (!converted) throw new IOException("scratch ancestor conversion failed: " + Marshal.GetLastWin32Error());
                    mutated = true;
                }
                else if (path == leaf && mutated)
                {
                    // Restore the pathname after the next child handle has been acquired.
                    Unset(mutator);
                    mutated = false;
                }
            };
            try { RegistrySnapshotFormat.PublishCaptureFile(leaf, RegistrySnapshotFormat.SnapshotFileName, "synthetic acquisition race"); }
            catch (IOException ex) { Console.WriteLine("CAUGHT PublishCaptureFile IOException: " + ex.Message); rejected = true; }
            finally
            {
                RegistrySnapshotFormat.DirectoryPinnedForTest = null;
                if (mutated) { Unset(mutator); mutated = false; }
            }
            check(converted, "ancestor mutation actually executed at the validated-handle boundary");
            check(!outsideOpened, "no outside child directory handle is acquired through a mutated ancestor");
            check(rejected && !Directory.EnumerateFileSystemEntries(outsideLeaf).Any(),
                "temporary ancestor conversion cannot redirect subsequent acquisition/publication");
            Console.WriteLine("AcquisitionBoundary: converted=" + converted + " rejected=" + rejected +
                " outsideFiles=" + Directory.EnumerateFileSystemEntries(outsideLeaf).Count());
        }
        finally
        {
            RegistrySnapshotFormat.DirectoryPinnedForTest = null;
            RegistrySnapshotFormat.DirectoryOpenedForTest = null;
            if (mutated) Unset(mutator);
            mutator.Dispose();
            Directory.Delete(root, true);
        }
    }
}
