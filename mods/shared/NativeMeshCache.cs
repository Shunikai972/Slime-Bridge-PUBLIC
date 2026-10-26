using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
namespace SlimeBridge.Shared
{
    /// <summary>Validated cache snapshot. Only model-selected textures are read.</summary>
    public sealed class NativeMeshCache
    {
        public string CanonicalId = "", ModelSha256 = "", Directory = "";
        public NativeMeshDocument Document = new NativeMeshDocument();
        public readonly Dictionary<string, byte[]> Textures = new Dictionary<string, byte[]>(StringComparer.Ordinal);
        public static NativeMeshCache Load(string repoRoot, string directory) => Load(repoRoot, directory, LabSpecPathGuard.OutRelativeRoot);

        /// <summary>Same load with another Bridge-owned root (the launcher's Lab/AssetCache folder): the guard is never skipped.</summary>
        public static NativeMeshCache Load(string repoRoot, string directory, string allowedRelativeRoot)
        {
            if (!Path.IsPathRooted(directory) || !LabSpecPathGuard.IsAllowedDirectory(
                Path.Combine(repoRoot, allowedRelativeRoot), directory, out var reason))
                throw new FormatException("cache outside allowed LAB directory");
            var raw = ReadGuarded(directory, "cache-manifest.json", 1024 * 1024);
            var manifest = MiniJson.Parse(Encoding.UTF8.GetString(raw).TrimStart((char)0xfeff)) as Dictionary<string, object?>;
            if (manifest == null || !manifest.TryGetValue("schema", out var schema) || !Equals(schema, "bridge-asset-cache-manifest") ||
                !manifest.TryGetValue("schema_version", out var version) || !((version is long integer && integer == 1) || (version is double number && number == 1)) ||
                !manifest.TryGetValue("canonical_id", out var canonical) || !(canonical is string id) ||
                !System.Text.RegularExpressions.Regex.IsMatch(id, @"^(slime|largo)\.[a-z0-9_.-]{1,80}$") ||
                !manifest.TryGetValue("files", out var filesRaw) || !(filesRaw is Dictionary<string, object?> files))
                throw new FormatException("invalid cache manifest v1");
            var result = new NativeMeshCache {Directory = directory, CanonicalId = id};
            var bytes = ReadVerified(directory, "model.json", 16 * 1024 * 1024, files);
            result.ModelSha256 = Digest(bytes);
            result.Document = NativeMeshParser.Parse(Encoding.UTF8.GetString(bytes).TrimStart((char)0xfeff));
            long totalBytes = 0;
            foreach (var part in result.Document.Parts)
                foreach (var material in part.Materials)
                    foreach (var texture in material.Textures)
                        if (!result.Textures.ContainsKey(texture.File))
                        {
                            var data = ReadVerified(directory, texture.File, 16 * 1024 * 1024, files);
                            totalBytes += data.Length;
                            if (totalBytes > 64 * 1024 * 1024) throw new FormatException("aggregate texture byte limit");
                            result.Textures.Add(texture.File, data);
                        }
            return result;
        }
        private static byte[] ReadGuarded(string directory, string file, int maxBytes)
        {
            var path = Path.Combine(directory, file);
            if (!LabSpecPathGuard.IsAllowedFile(directory, path, out var reason)) throw new FormatException(file + ": " + reason);
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length < 1 || stream.Length > maxBytes) throw new FormatException(file + ": byte limit");
                var bytes = new byte[(int)stream.Length];
                int read = 0;
                while (read < bytes.Length)
                {
                    int n = stream.Read(bytes, read, bytes.Length - read);
                    if (n == 0) throw new FormatException(file + ": unexpected EOF");
                    read += n;
                }
                if (stream.ReadByte() != -1) throw new FormatException(file + ": file changed while reading");
                return bytes;
            }
        }
        private static byte[] ReadVerified(string directory, string file, int maxBytes, Dictionary<string, object?> files)
        {
            if (!files.TryGetValue(file, out var value) || !(value is string hash) ||
                !System.Text.RegularExpressions.Regex.IsMatch(hash, @"^[a-fA-F0-9]{64}$"))
                throw new FormatException(file + ": missing manifest digest");
            var bytes = ReadGuarded(directory, file, maxBytes);
            if (!string.Equals(hash, Digest(bytes), StringComparison.OrdinalIgnoreCase)) throw new FormatException(file + ": digest mismatch");
            return bytes;
        }
        private static string Digest(byte[] bytes)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
    }
}
