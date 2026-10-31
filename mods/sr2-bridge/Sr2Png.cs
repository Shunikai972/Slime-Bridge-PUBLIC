using System;
using System.IO;
using System.IO.Compression;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using UnityEngine;
namespace SlimeBridge.SR2
{
    /// <summary>
    /// PNG -> Texture2D without ImageConversion.LoadImage: on SR2 1.x (Unity 6) LoadImage goes through ReadOnlySpan and the
    /// game's interop layer lacks Il2CppSystem.ReadOnlySpan.GetPinnableReference (MissingMethodException), so no imported
    /// texture could load. Non-interlaced 8/16-bit grey, grey+alpha, RGB, RGBA and palette PNGs are decoded here and handed
    /// over with SetPixels32. Anything else falls back to LoadImage.
    /// </summary>
    internal static class Sr2Png
    {
        internal static Texture2D Load(byte[] data, string name, bool linear, bool mipmaps)
        {
            if (TryDecode(data, out var width, out var height, out var pixels))
            {
                var texture = new Texture2D(width, height, TextureFormat.RGBA32, mipmaps, linear) { name = name };
                var colors = new Il2CppStructArray<Color32>(pixels.Length);
                for (var i = 0; i < pixels.Length; i++) colors[i] = pixels[i];
                texture.SetPixels32(colors);
                texture.Apply(mipmaps, false);
                return texture;
            }
            var fallback = new Texture2D(2, 2, TextureFormat.RGBA32, mipmaps, linear) { name = name };
            if (!ImageConversion.LoadImage(fallback, new Il2CppStructArray<byte>(data), false))
                throw new FormatException("texture decode failed: " + name);
            return fallback;
        }

        /// <summary>Rows bottom-up (Unity's texture order).</summary>
        internal static bool TryDecode(byte[] data, out int width, out int height, out Color32[] pixels)
        {
            width = height = 0;
            pixels = Array.Empty<Color32>();
            if (data == null || data.Length < 33 || data[0] != 0x89 || data[1] != 0x50 || data[2] != 0x4E || data[3] != 0x47) return false;
            int depth = 0, colorType = 0, interlace = 0;
            byte[]? palette = null, paletteAlpha = null;
            var idat = new MemoryStream();
            var at = 8;
            while (at + 8 <= data.Length)
            {
                var length = (data[at] << 24) | (data[at + 1] << 16) | (data[at + 2] << 8) | data[at + 3];
                var type = System.Text.Encoding.ASCII.GetString(data, at + 4, 4);
                var body = at + 8;
                if (length < 0 || body + length > data.Length) return false;
                if (type == "IHDR")
                {
                    width = (data[body] << 24) | (data[body + 1] << 16) | (data[body + 2] << 8) | data[body + 3];
                    height = (data[body + 4] << 24) | (data[body + 5] << 16) | (data[body + 6] << 8) | data[body + 7];
                    depth = data[body + 8]; colorType = data[body + 9]; interlace = data[body + 12];
                }
                else if (type == "PLTE") { palette = new byte[length]; Buffer.BlockCopy(data, body, palette, 0, length); }
                else if (type == "tRNS") { paletteAlpha = new byte[length]; Buffer.BlockCopy(data, body, paletteAlpha, 0, length); }
                else if (type == "IDAT") idat.Write(data, body, length);
                else if (type == "IEND") break;
                at = body + length + 4;
            }
            if (width <= 0 || height <= 0 || width > 16384 || height > 16384 || interlace != 0 || (depth != 8 && depth != 16)) return false;
            int channels = colorType switch { 0 => 1, 2 => 3, 3 => 1, 4 => 2, 6 => 4, _ => 0 };
            if (channels == 0 || (colorType == 3 && (palette == null || depth != 8))) return false;
            var bytesPerPixel = channels * depth / 8;
            var stride = width * bytesPerPixel;
            var raw = new byte[(stride + 1) * height];
            idat.Position = 2;                                           // zlib header; the deflate stream follows
            using (var inflate = new DeflateStream(idat, CompressionMode.Decompress))
            {
                var read = 0;
                while (read < raw.Length)
                {
                    var n = inflate.Read(raw, read, raw.Length - read);
                    if (n <= 0) return false;
                    read += n;
                }
            }
            var current = new byte[stride];
            var previous = new byte[stride];
            pixels = new Color32[width * height];
            for (var y = 0; y < height; y++)
            {
                var filter = raw[y * (stride + 1)];
                Buffer.BlockCopy(raw, y * (stride + 1) + 1, current, 0, stride);
                for (var x = 0; x < stride; x++)
                {
                    int left = x >= bytesPerPixel ? current[x - bytesPerPixel] : 0, up = previous[x], upLeft = x >= bytesPerPixel ? previous[x - bytesPerPixel] : 0;
                    int value = current[x];
                    switch (filter)
                    {
                        case 1: value += left; break;
                        case 2: value += up; break;
                        case 3: value += (left + up) >> 1; break;
                        case 4:
                            int p = left + up - upLeft, pa = Math.Abs(p - left), pb = Math.Abs(p - up), pc = Math.Abs(p - upLeft);
                            value += pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
                            break;
                        case 0: break;
                        default: return false;
                    }
                    current[x] = (byte)value;
                }
                var row = (height - 1 - y) * width;                     // PNG rows are top-down, Unity's bottom-up
                var step = depth / 8;
                for (var x = 0; x < width; x++)
                {
                    var o = x * bytesPerPixel;
                    byte Sample(int c) => current[o + c * step];
                    Color32 color;
                    switch (colorType)
                    {
                        case 0: { var g = Sample(0); color = new Color32(g, g, g, 255); break; }
                        case 4: { var g = Sample(0); color = new Color32(g, g, g, Sample(1)); break; }
                        case 2: color = new Color32(Sample(0), Sample(1), Sample(2), 255); break;
                        case 3:
                        {
                            var index = current[o];
                            if (index * 3 + 2 >= palette!.Length) return false;
                            var alpha = paletteAlpha != null && index < paletteAlpha.Length ? paletteAlpha[index] : (byte)255;
                            color = new Color32(palette[index * 3], palette[index * 3 + 1], palette[index * 3 + 2], alpha);
                            break;
                        }
                        default: color = new Color32(Sample(0), Sample(1), Sample(2), Sample(3)); break;
                    }
                    pixels[row + x] = color;
                }
                var swap = previous; previous = current; current = swap;
            }
            return true;
        }
    }
}
