using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace SbRuntime
{
    /// <summary>One mesh from a .sbmesh file (Unity space, little-endian):  'SBM1' | u32 headerLen | header JSON | blob.</summary>
    public sealed class SbMeshData
    {
        public string Name;
        public int VertexCount;
        public float[] Positions;                 // 3N
        public float[] Normals, Tangents;         // 3N, 4N (may be null)
        public float[][] Uv = new float[8][];     // 2N each (null when absent)
        public float[] Colors;                    // 4N
        public int[] BoneIndices;                 // 4N
        public float[] BoneWeights;               // 4N
        public float[] BindPoses;                 // 16 * boneCount, row-major (Unity Matrix4x4 e00,e01,...)
        public List<int[]> Triangles = new List<int[]>();   // per triangle-topology submesh
        public float[] BoundsCenter, BoundsExtent;
        public bool Skinned { get { return BoneIndices != null && BoneWeights != null && BindPoses != null; } }

        public static SbMeshData Load(string path)
        {
            byte[] raw = File.ReadAllBytes(path);
            if (raw.Length < 8 || raw[0] != 'S' || raw[1] != 'B' || raw[2] != 'M' || raw[3] != '1') throw new FormatException(path + ": not an sbmesh file");
            int headerLen = BitConverter.ToInt32(raw, 4);
            if (headerLen <= 0 || 8 + headerLen > raw.Length) throw new FormatException(path + ": bad header length");
            var header = Json.O(Json.Parse(Encoding.UTF8.GetString(raw, 8, headerLen)), "sbmesh header");
            int blobStart = 8 + headerLen;
            var arrays = new Dictionary<string, Dictionary<string, object>>();
            foreach (var o in Json.A(Json.Get(header, "arrays"), "arrays")) { var e = Json.O(o, "array"); arrays[Json.S(e, "name")] = e; }

            var m = new SbMeshData { Name = Json.S(header, "name"), VertexCount = Json.I(header, "vertex_count") };
            m.Positions = Floats(raw, blobStart, arrays, "positions", m.VertexCount * 3);
            m.Normals = Floats(raw, blobStart, arrays, "normals", m.VertexCount * 3, false);
            m.Tangents = Floats(raw, blobStart, arrays, "tangents", m.VertexCount * 4, false);
            for (int c = 0; c < 8; c++) m.Uv[c] = Floats(raw, blobStart, arrays, "uv" + c, m.VertexCount * 2, false);
            m.Colors = Floats(raw, blobStart, arrays, "colors", m.VertexCount * 4, false);
            if (arrays.ContainsKey("bone_indices"))
            {
                var e = arrays["bone_indices"];
                int n = m.VertexCount * 4;
                m.BoneIndices = new int[n];
                int off = blobStart + Json.I(e, "offset");
                for (int i = 0; i < n; i++) m.BoneIndices[i] = BitConverter.ToUInt16(raw, off + i * 2);
                m.BoneWeights = Floats(raw, blobStart, arrays, "bone_weights", n);
                m.BindPoses = Floats(raw, blobStart, arrays, "bind_poses", -1, false);
            }
            foreach (var o in Json.A(Json.Get(header, "submeshes"), "submeshes"))
            {
                var sm = Json.O(o, "submesh");
                if (Json.I(sm, "topology") != 0) continue;        // triangles only
                var e = arrays[Json.S(sm, "index_array")];
                int count = ShapeLen(e);
                var idx = new int[count];
                int off = blobStart + Json.I(e, "offset");
                for (int i = 0; i < count; i++)
                {
                    uint v = BitConverter.ToUInt32(raw, off + i * 4);
                    if (v >= (uint)m.VertexCount) throw new FormatException(path + ": index out of range");
                    idx[i] = (int)v;
                }
                if (count % 3 != 0) throw new FormatException(path + ": index count not a multiple of 3");
                m.Triangles.Add(idx);
            }
            m.BoundsCenter = Json.F(Json.Get(header, "bounds_center"), 3, "bounds_center");
            m.BoundsExtent = Json.F(Json.Get(header, "bounds_extent"), 3, "bounds_extent");
            return m;
        }

        static int ShapeLen(Dictionary<string, object> e)
        {
            int n = 1;
            foreach (var d in Json.A(Json.Get(e, "shape"), "shape")) n *= (int)Json.N(d);
            return n;
        }

        static float[] Floats(byte[] raw, int blobStart, Dictionary<string, Dictionary<string, object>> arrays, string name, int expected, bool required = true)
        {
            if (!arrays.TryGetValue(name, out var e)) { if (required) throw new FormatException("missing array " + name); return null; }
            int n = ShapeLen(e);
            if (expected >= 0 && n != expected) throw new FormatException(name + ": expected " + expected + " floats, found " + n);
            int off = blobStart + Json.I(e, "offset");
            if (off + n * 4 > raw.Length) throw new FormatException(name + ": truncated");
            var r = new float[n];
            Buffer.BlockCopy(raw, off, r, 0, n * 4);
            for (int i = 0; i < n; i++) if (float.IsNaN(r[i]) || float.IsInfinity(r[i])) throw new FormatException(name + ": non-finite value");
            return r;
        }
    }
}
