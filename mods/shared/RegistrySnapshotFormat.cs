using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>One native slime definition observed by a runtime registry traversal.</summary>
    public sealed class RegistryRow
    {
        public string ObjectRef = "";
        public long? NativeId;
        public string ReferenceId = "";
        public string Name = "";
        public bool IsLargo;
        public bool CanLargofy;
        public List<string> BaseSlimes = new List<string>();
        public List<string> ProducedPlorts = new List<string>();
        public List<string> PrefabRefs = new List<string>();
    }

    /// <summary>
    /// Pure serializer for the runtime snapshot consumed by bridge.content.scan.read_runtime_snapshot
    /// and for the separate traversal receipt. The canonical registry hash must stay byte-identical to
    /// bridge.content.registry_capture.canonical_registry_sha256.
    /// </summary>
    public static class RegistrySnapshotFormat
    {
        public const string CollectorVersion = "2";
        public const string SnapshotFileName = "registry-snapshot.json";
        public const string ReceiptFileName = "traversal-receipt.json";
        public const string DietSnapshotFileName = "diet-snapshot.json";
        public const string AppearanceColorsFileName = "appearance-colors.json";
        public const string ImportedSpeciesReceiptFileName = "imported-species-receipt.json";
        public const string ImportedOpsReceiptFileName = "imported-ops-receipt.json";

        public static string ValidateCaptureLocation(string realm, string actual, string expected, string output, string runId)
        {
            if (realm != "SR1" && realm != "SR2") throw new IOException("invalid realm");
            if (string.IsNullOrWhiteSpace(actual) || string.IsNullOrWhiteSpace(expected) ||
                !Path.IsPathRooted(output) || runId.Length < 8 || runId.Length > 64 ||
                runId.Any(c => !(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '-'))
                throw new IOException("invalid capture request");
            var exe = new FileInfo(Path.GetFullPath(actual));
            var game = exe.Directory;
            if (!string.Equals(exe.FullName, Path.GetFullPath(expected), StringComparison.OrdinalIgnoreCase) ||
                exe.Name != (realm == "SR1" ? "SlimeRancher.exe" : "SlimeRancher2.exe") ||
                game == null || game.Name != realm || game.Parent?.Name != "Games" || game.Parent.Parent?.Name != "Lab")
                throw new IOException("not the exact LAB executable");
            var marker = Path.Combine(game.FullName, "slimebridge-lab.json");
            if (!File.Exists(marker)) throw new IOException("LAB marker missing");
            RefuseReparse(exe.FullName);
            RefuseReparse(marker);
            var captureRoot = Path.Combine(game.Parent.Parent.FullName, "RuntimeArtifacts", runId);
            var destination = Path.GetFullPath(output).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (!Directory.Exists(destination) || (!string.Equals(destination, captureRoot, StringComparison.OrdinalIgnoreCase) &&
                !destination.StartsWith(captureRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)))
                throw new IOException("capture output outside run-scoped RuntimeArtifacts");
            RefuseReparse(destination);
            return destination;
        }

        public static void RefuseReparse(string path)
        {
            for (var cursor = Path.GetFullPath(path); !string.IsNullOrEmpty(cursor); cursor = Path.GetDirectoryName(cursor))
                if ((File.Exists(cursor) || Directory.Exists(cursor)) && (File.GetAttributes(cursor) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("reparse point refused");
        }

        // Pin one local-drive root, then open each descendant relative to its preceding handle.
        // Read-only sharing denies rename/delete, not FILE_WRITE_ATTRIBUTES or junction conversion.
        // Native single-component opens and handle metadata enforce containment despite mutation.
#if REGISTRY_CAPTURE_HARNESS
        // Deterministic race injection; absent from both production plugin builds.
        internal static Action<string>? DirectoryPinnedForTest;
        internal static Action<SafeFileHandle>? DirectoryOpenedForTest;
        internal static Action? CaptureFileCreatedForTest;
        internal static Action? CaptureFlushedForTest;
        internal static Action? BeforeFailedCaptureCleanupForTest;
        internal static Action<SafeFileHandle>? RelativeRootCapturedForTest;
#endif
        internal static DirectoryLease PinCaptureDirectory(string directory) => new DirectoryLease(directory);

        internal sealed class DirectoryLease : IDisposable
        {
            private readonly List<SafeFileHandle> _handles = new List<SafeFileHandle>();

            internal SafeFileHandle LeafHandle => _handles.Count > 0 ? _handles[_handles.Count - 1] : throw new InvalidOperationException("no directory handles pinned");

            [StructLayout(LayoutKind.Sequential)]
            internal struct AttributeTag { public uint Attributes; public uint ReparseTag; }

            [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
            private static extern SafeFileHandle OpenDirectory(string path, uint access, uint sharing,
                IntPtr security, uint creation, uint flags, IntPtr template);

            [DllImport("kernel32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool GetFileInformationByHandleEx(SafeFileHandle handle, int infoClass,
                out AttributeTag info, uint size);

            internal static bool GetAttributeTag(SafeFileHandle handle, out AttributeTag info)
            {
                return GetFileInformationByHandleEx(handle, 9, out info, 8);
            }

            internal DirectoryLease(string directory)
            {
                if (Environment.OSVersion.Platform != PlatformID.Win32NT)
                    throw new PlatformNotSupportedException("Windows directory pinning required for registry output");
                var full = Path.GetFullPath(directory);
                var root = Path.GetPathRoot(full);
                if (root == null || root.Length != 3 || root[1] != ':' || full.Substring(2).Contains(':'))
                    throw new IOException("only normal local drive paths are supported");
                var ancestors = new List<string>();
                for (var cursor = full; !string.IsNullOrEmpty(cursor); cursor = Path.GetDirectoryName(cursor)) ancestors.Add(cursor);
                ancestors.Reverse();
                try
                {
                    string? previousPath = null;
                    foreach (var ancestor in ancestors)
                    {
                        // Only the local-drive anchor is opened by absolute path.
                        var handle = _handles.Count == 0
                            ? OpenDirectory(root, 0x80000000, 1, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero)
                            : OpenRelative(LeafHandle, Path.GetFileName(ancestor), true);
                        if (handle.IsInvalid)
                        {
                            var error = Marshal.GetLastWin32Error();
                            handle.Dispose();
                            throw new IOException("capture directory lease refused: " + error);
                        }
                        _handles.Add(handle);
#if REGISTRY_CAPTURE_HARNESS
                        DirectoryOpenedForTest?.Invoke(handle);
#endif
                        if (!GetFileInformationByHandleEx(handle, 9, out var info, 8) ||
                            (info.Attributes & 0x10) == 0 || (info.Attributes & 0x400) != 0 || info.ReparseTag != 0)
                            throw new IOException("capture directory handle is not a plain directory");

                        var currentPath = GetFinalPath(handle);
                        if (previousPath != null)
                        {
                            var expectedParent = Path.GetDirectoryName(currentPath);
                            if (!string.Equals(expectedParent, previousPath, StringComparison.OrdinalIgnoreCase))
                                throw new IOException("ancestor hierarchy divergence detected: " + currentPath + " is not under " + previousPath);
                        }
                        else
                        {
                            if (!string.Equals(currentPath.TrimEnd('\\'), ancestor.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                                throw new IOException("root path divergence detected: " + currentPath + " != " + ancestor);
                        }
                        previousPath = currentPath;

#if REGISTRY_CAPTURE_HARNESS
                        DirectoryPinnedForTest?.Invoke(ancestor);
#endif
                    }
                }
                catch { Dispose(); throw; }
            }

            public void Dispose()
            {
                for (var i = _handles.Count - 1; i >= 0; i--) _handles[i].Dispose();
                _handles.Clear();
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_STATUS_BLOCK
        {
            public IntPtr Status;
            public IntPtr Information;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct UNICODE_STRING
        {
            public ushort Length;
            public ushort MaximumLength;
            public IntPtr Buffer;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct OBJECT_ATTRIBUTES
        {
            public int Length;
            public IntPtr RootDirectory;
            public IntPtr ObjectName;
            public uint Attributes;
            public IntPtr SecurityDescriptor;
            public IntPtr SecurityQualityOfService;
        }

        [DllImport("ntdll.dll", ExactSpelling = true)]
        private static extern int NtCreateFile(
            out SafeFileHandle FileHandle,
            uint DesiredAccess,
            ref OBJECT_ATTRIBUTES ObjectAttributes,
            out IO_STATUS_BLOCK IoStatusBlock,
            IntPtr AllocationSize,
            uint FileAttributes,
            uint ShareAccess,
            uint CreateDisposition,
            uint CreateOptions,
            IntPtr EaBuffer,
            uint EaLength);

        [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(SafeFileHandle hFile, [Out] StringBuilder lpszFilePath, uint cchFilePath, uint dwFlags);

        private static string GetFinalPath(SafeFileHandle handle)
        {
            var sb = new StringBuilder(1024);
            var len = GetFinalPathNameByHandle(handle, sb, (uint)sb.Capacity, 0);
            if (len == 0 || len > sb.Capacity)
                throw new IOException("failed to retrieve final path by handle: " + Marshal.GetLastWin32Error());
            var path = sb.ToString();
            if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
                return @"\\" + path.Substring(8);
            if (path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase))
                return path.Substring(4);
            return path;
        }

        private static SafeFileHandle CreateCaptureFile(SafeFileHandle dirHandle, string name) => OpenRelative(dirHandle, name, false);

        internal static SafeFileHandle OpenRelative(SafeFileHandle dirHandle, string name, bool directory)
        {
            if (string.IsNullOrEmpty(name) || name == "." || name == ".." ||
                name.IndexOfAny(new[] { '\\', '/', ':', (char)0 }) >= 0)
                throw new IOException("invalid relative capture component");
            var nameBytes = Encoding.Unicode.GetBytes(name);
            if (nameBytes.Length > ushort.MaxValue - 2) throw new IOException("capture component too long");
            // Pin the SafeHandle reference count for the whole native call: the raw IntPtr embedded
            // in OBJECT_ATTRIBUTES is not protected by the marshaller (Dispose would otherwise close it).
            var rootReferenced = false;
            dirHandle.DangerousAddRef(ref rootReferenced);
            var bufferPtr = IntPtr.Zero;
            var usPtr = IntPtr.Zero;
            try
            {
                bufferPtr = Marshal.AllocHGlobal(nameBytes.Length + 2);
                usPtr = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UNICODE_STRING)));
                Marshal.Copy(nameBytes, 0, bufferPtr, nameBytes.Length);
                Marshal.WriteInt16(bufferPtr, nameBytes.Length, 0);

                var us = new UNICODE_STRING
                {
                    Length = (ushort)nameBytes.Length,
                    MaximumLength = (ushort)(nameBytes.Length + 2),
                    Buffer = bufferPtr
                };
                Marshal.StructureToPtr(us, usPtr, false);

                var objAttr = new OBJECT_ATTRIBUTES
                {
                    Length = Marshal.SizeOf(typeof(OBJECT_ATTRIBUTES)),
                    RootDirectory = dirHandle.DangerousGetHandle(),
                    ObjectName = usPtr,
                    Attributes = 0x00000040 | 0x00001000, // OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE
                    SecurityDescriptor = IntPtr.Zero,
                    SecurityQualityOfService = IntPtr.Zero
                };

#if REGISTRY_CAPTURE_HARNESS
                RelativeRootCapturedForTest?.Invoke(dirHandle);
#endif
                var status = NtCreateFile(
                    out var fileHandle,
                    directory ? 0x001200A9u : 0x40110080u, // Publication also needs DELETE for owned-handle rollback.
                    ref objAttr,
                    out var ioStatus,
                    IntPtr.Zero,
                    0x80, // FILE_ATTRIBUTE_NORMAL
                    directory ? 1u : 0u, // SHARE_READ for directories; exclusive publication.
                    directory ? 1u : 2u, // FILE_OPEN / FILE_CREATE.
                    directory ? 0x00200021u : 0x00200062u, // OPEN_REPARSE_POINT | synchronous directory/file options.
                    IntPtr.Zero,
                    0);

                if (status != 0 || fileHandle.IsInvalid)
                {
                    fileHandle?.Dispose();
                    throw new IOException(string.Format(CultureInfo.InvariantCulture, "capture file creation refused: NTSTATUS 0x{0:X8}", status));
                }
                return fileHandle;
            }
            finally
            {
                if (bufferPtr != IntPtr.Zero) Marshal.FreeHGlobal(bufferPtr);
                if (usPtr != IntPtr.Zero) Marshal.FreeHGlobal(usPtr);
                if (rootReferenced) dirHandle.DangerousRelease();
            }
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetFileInformationByHandle(SafeFileHandle handle, int infoClass,
            ref byte deleteFile, uint size); // FILE_DISPOSITION_INFO contains one BOOLEAN.

        public static void PublishCaptureFile(string directory, string name, string text)
        {
            if (name != SnapshotFileName && name != ReceiptFileName && name != DietSnapshotFileName &&
                name != AppearanceColorsFileName &&
                name != ImportedSpeciesReceiptFileName && name != ImportedOpsReceiptFileName &&
                !name.StartsWith("visual-diagnostics-", StringComparison.Ordinal))
                throw new IOException("invalid capture filename");
            using (var lease = PinCaptureDirectory(directory))
            {
                var dirHandle = lease.LeafHandle;

                if (!DirectoryLease.GetAttributeTag(dirHandle, out var preDirTag) ||
                    (preDirTag.Attributes & 0x10) == 0 || (preDirTag.Attributes & 0x400) != 0 || preDirTag.ReparseTag != 0)
                    throw new IOException("capture directory handle is not a plain directory");

                var expectedDir = GetFinalPath(dirHandle);
                using (var fileHandle = CreateCaptureFile(dirHandle, name))
                {
                try
                {
                    var actualFile = GetFinalPath(fileHandle);
#if REGISTRY_CAPTURE_HARNESS
                    CaptureFileCreatedForTest?.Invoke();
#endif
                    var actualDir = Path.GetDirectoryName(actualFile);
                    if (!string.Equals(actualDir, expectedDir, StringComparison.OrdinalIgnoreCase) ||
                        !string.Equals(Path.GetFileName(actualFile), name, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new IOException("capture destination escaped pinned directory");
                    }

                    if (!DirectoryLease.GetAttributeTag(dirHandle, out var postDirTag) ||
                        (postDirTag.Attributes & 0x10) == 0 || (postDirTag.Attributes & 0x400) != 0 || postDirTag.ReparseTag != 0)
                    {
                        throw new IOException("capture directory mutated into reparse point");
                    }

                    if (!DirectoryLease.GetAttributeTag(fileHandle, out var fileTag) ||
                        (fileTag.Attributes & 0x400) != 0 || fileTag.ReparseTag != 0)
                    {
                        throw new IOException("capture file is a reparse point");
                    }

                    // FileStream owns only a non-owning view. Keep the actual owned handle live
                    // through stream disposal so every failure can mark precisely that file for deletion.
                    var retained = false;
                    try
                    {
                        fileHandle.DangerousAddRef(ref retained);
                        using (var view = new SafeFileHandle(fileHandle.DangerousGetHandle(), false))
                        using (var stream = new FileStream(view, FileAccess.Write, 4096))
                        {
                            var bytes = new UTF8Encoding(false).GetBytes(text);
                            stream.Write(bytes, 0, bytes.Length);
                            stream.Flush(true);
#if REGISTRY_CAPTURE_HARNESS
                            CaptureFlushedForTest?.Invoke();
#endif
                        }
                    }
                    finally { if (retained) fileHandle.DangerousRelease(); }
                }
                catch (Exception failure)
                {
#if REGISTRY_CAPTURE_HARNESS
                    BeforeFailedCaptureCleanupForTest?.Invoke();
#endif
                    byte deleteFile = 1;
                    if (!SetFileInformationByHandle(fileHandle, 4, ref deleteFile, 1))
                        throw new IOException("owned capture cleanup refused: " + Marshal.GetLastWin32Error(), failure);
                    throw;
                }
                }
            }
        }

        public static string Sha256Hex(byte[] data)
        {
            using (var sha = SHA256.Create())
            {
                var hash = sha.ComputeHash(data);
                var sb = new StringBuilder(hash.Length * 2);
                foreach (var b in hash) sb.Append(b.ToString("x2", CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        /// <summary>Matches Python json.dumps(ensure_ascii=True) string escaping.</summary>
        public static string Quote(string value)
        {
            var sb = new StringBuilder(value.Length + 2);
            sb.Append('"');
            foreach (var c in value)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c == (char)13) sb.Append("\\r");
                        else if (c < 0x20 || c > 0x7e) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        private static string Bool(bool value) => value ? "true" : "false";

        private static string List(IEnumerable<string> values) => "[" + string.Join(",", values.Select(Quote)) + "]";

        private static string Native(long? id) => id.HasValue ? id.Value.ToString(CultureInfo.InvariantCulture) : "null";

        /// <summary>Python: json.dumps([object_ref, native_id, reference_id, name, is_largo, can_largofy,
        /// base_slimes, produced_plorts, prefab_refs], separators=(',', ':'), ensure_ascii=True)</summary>
        public static string CanonicalLine(RegistryRow r) =>
            "[" + Quote(r.ObjectRef) + "," + Native(r.NativeId) + "," + Quote(r.ReferenceId) + "," + Quote(r.Name) + "," +
            Bool(r.IsLargo) + "," + Bool(r.CanLargofy) + "," + List(r.BaseSlimes) + "," + List(r.ProducedPlorts) + "," +
            List(r.PrefabRefs) + "]";

        public static List<RegistryRow> Ordered(IEnumerable<RegistryRow> rows) =>
            rows.OrderBy(r => r.ObjectRef, StringComparer.Ordinal).ToList();

        public static string RegistrySha256(IEnumerable<RegistryRow> rows)
        {
            var text = string.Concat(Ordered(rows).Select(r => CanonicalLine(r) + "\n"));
            return Sha256Hex(Encoding.UTF8.GetBytes(text));
        }

        public static string Completeness(int expected, IList<RegistryRow> rows, IList<string> errors, bool countMismatch)
        {
            if (countMismatch) return "FAILED";
            var references = new HashSet<string>(rows.Select(r => r.ReferenceId), StringComparer.Ordinal);
            var baseReferences = new HashSet<string>(rows.Where(r => !r.IsLargo).Select(r => r.ReferenceId), StringComparer.Ordinal);
            var nativeIds = rows.Where(r => r.NativeId.HasValue).Select(r => r.NativeId!.Value).ToList();
            var invalid = rows.Any(r => string.IsNullOrEmpty(r.ObjectRef) || string.IsNullOrEmpty(r.ReferenceId) ||
                r.BaseSlimes.Concat(r.ProducedPlorts).Concat(r.PrefabRefs).Any(string.IsNullOrEmpty) ||
                (r.IsLargo && (r.BaseSlimes.Count != 2 || r.BaseSlimes.Distinct().Count() != 2 ||
                    r.BaseSlimes.Any(b => !baseReferences.Contains(b))))) ||
                references.Count != rows.Count || rows.Select(r => r.ObjectRef).Distinct().Count() != rows.Count ||
                nativeIds.Distinct().Count() != nativeIds.Count;
            return expected > 0 && !invalid && errors.Count == 0 && expected == rows.Count ? "COMPLETE" : "PARTIAL";
        }

        private static string Counts(IDictionary<string, long> counts) =>
            "{" + string.Join(",", counts.OrderBy(k => k.Key, StringComparer.Ordinal)
                .Select(k => Quote(k.Key) + ":" + k.Value.ToString(CultureInfo.InvariantCulture))) + "}";

        public static string SnapshotJson(string realm, string runId, string activeSha256, string scannedSha256,
            string activePath, string provenance, string registryIdentity, int expected, IList<RegistryRow> rows,
            IList<string> errors, IDictionary<string, long> counts, string completeness)
        {
            var ordered = Ordered(rows);
            var sb = new StringBuilder();
            sb.Append("{\"schema_version\":1,\"realm\":").Append(Quote(realm));
            sb.Append(",\"game_build\":").Append(Quote(activeSha256));
            sb.Append(",\"build_match\":\"VERIFIED\",\"registry_traversed\":true");
            sb.Append(",\"completeness\":").Append(Quote(completeness));
            sb.Append(",\"collector\":{\"version\":").Append(Quote(CollectorVersion)).Append(",\"run_id\":").Append(Quote(runId))
                .Append(",\"counts\":").Append(Counts(counts)).Append("}");
            sb.Append(",\"build_proof\":{\"active_sha256\":").Append(Quote(activeSha256))
                .Append(",\"scanned_sha256\":").Append(Quote(scannedSha256))
                .Append(",\"active_path\":").Append(Quote(activePath))
                .Append(",\"provenance\":").Append(Quote(provenance)).Append("}");
            sb.Append(",\"traversal_proof\":{\"registry_identity\":").Append(Quote(registryIdentity))
                .Append(",\"registry_sha256\":").Append(Quote(RegistrySha256(ordered)))
                .Append(",\"expected_count\":").Append(expected.ToString(CultureInfo.InvariantCulture))
                .Append(",\"observed_count\":").Append(ordered.Count.ToString(CultureInfo.InvariantCulture)).Append("}");
            sb.Append(",\"definitions\":[");
            for (var i = 0; i < ordered.Count; i++)
            {
                var r = ordered[i];
                if (i > 0) sb.Append(",");
                sb.Append("\n{\"object_ref\":").Append(Quote(r.ObjectRef))
                    .Append(",\"native_id\":").Append(Native(r.NativeId))
                    .Append(",\"reference_id\":").Append(Quote(r.ReferenceId))
                    .Append(",\"name\":").Append(Quote(r.Name))
                    .Append(",\"is_largo\":").Append(Bool(r.IsLargo))
                    .Append(",\"can_largofy\":").Append(Bool(r.CanLargofy))
                    .Append(",\"base_slimes\":").Append(List(r.BaseSlimes))
                    .Append(",\"produced_plorts\":").Append(List(r.ProducedPlorts))
                    .Append(",\"prefab_refs\":").Append(List(r.PrefabRefs))
                    .Append(",\"evidence\":[\"runtime registry traversal\"]}");
            }
            sb.Append("],\"errors\":").Append(List(errors)).Append("}\n");
            return sb.ToString();
        }

        public static string ReceiptJson(string realm, string runId, string activeSha256, string registryIdentity,
            int expected, IList<RegistryRow> rows, IDictionary<string, long> counts)
        {
            var ordered = Ordered(rows);
            return "{\"schema\":\"bridge-traversal-receipt\",\"schema_version\":1,\"realm\":" + Quote(realm) +
                ",\"collector_version\":" + Quote(CollectorVersion) + ",\"run_id\":" + Quote(runId) +
                ",\"active_sha256\":" + Quote(activeSha256) + ",\"registry_identity\":" + Quote(registryIdentity) +
                ",\"registry_sha256\":" + Quote(RegistrySha256(ordered)) +
                ",\"expected_count\":" + expected.ToString(CultureInfo.InvariantCulture) +
                ",\"observed_count\":" + ordered.Count.ToString(CultureInfo.InvariantCulture) +
                ",\"counts\":" + Counts(counts) +
                ",\"object_refs\":" + List(ordered.Select(r => r.ObjectRef)) + "}\n";
        }
    }
}
