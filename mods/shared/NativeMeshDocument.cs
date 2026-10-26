using System.Collections.Generic;

namespace SlimeBridge.Shared
{
    // Loader-neutral bridge-native-mesh v2 data. No Unity or runtime dependencies.
    public sealed class NativeMeshDocument
    {
        public string SourceGame = "", ReferenceId = "", DefaultClip = "";
        public float PrefabScale = 1;
        public NativeMeshTransform VisualTransform = new NativeMeshTransform();
        public NativeMeshBone[] Bones = new NativeMeshBone[0];
        public NativeMeshPart[] Parts = new NativeMeshPart[0];
        public NativeMeshClip[] Clips = new NativeMeshClip[0];
    }
    public sealed class NativeMeshTransform
    {
        public float[] Position = new float[] { 0, 0, 0 };
        public float[] Rotation = new float[] { 0, 0, 0, 1 };
        public float[] Scale = new float[] { 1, 1, 1 };
    }
    public sealed class NativeMeshBone
    {
        public string Name = "", Path = "";
        public int Parent;
        public NativeMeshTransform Transform = new NativeMeshTransform();
    }
    public sealed class NativeMeshPart
    {
        public bool Skinned, Enabled = true;
        public string Role = "", Path = "", MeshName = "", RootBone = "";
        public NativeMeshTransform Transform = new NativeMeshTransform();
        public float[][] Vertices = new float[0][], Normals = new float[0][], Tangents = new float[0][],
            Uv = new float[0][], Uv2 = new float[0][], Colors = new float[0][],
            BindPoses = new float[0][], BoneWeights = new float[0][];
        public int[][] Triangles = new int[0][];
        public string[] SkinBones = new string[0];
        public NativeMeshMaterial[] Materials = new NativeMeshMaterial[0];
    }
    public sealed class NativeMeshMaterial
    {
        public string Name = "", Shader = "", FaceRole = "";
        public Dictionary<string, float[]> Colors = new Dictionary<string, float[]>();
        public Dictionary<string, float> Floats = new Dictionary<string, float>();
        public NativeMeshTexture[] Textures = new NativeMeshTexture[0];
        public int? RenderQueue;
    }
    public sealed class NativeMeshTexture
    {
        public string Property = "", File = "";
        public float[] Scale = new float[] { 1, 1 }, Offset = new float[] { 0, 0 };
        public bool Linear;
    }
    public sealed class NativeMeshClip
    {
        public string Name = "";
        public float Duration, SampleRate;
        public bool Loop;
        public float[] Times = new float[0];
        public NativeMeshChannel[] Channels = new NativeMeshChannel[0];
    }
    public sealed class NativeMeshChannel
    {
        public string Path = "", Property = "";
        public float[][] Values = new float[0][];
    }
}
