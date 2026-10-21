// TEST DOUBLES ONLY: managed C# ownership/transaction evidence, not Unity or IL2CPP proof.
using System.Collections;
using System.Reflection;
namespace Il2CppInterop.Runtime.Attributes { [AttributeUsage(AttributeTargets.Method)] public class HideFromIl2CppAttribute : Attribute { } }
namespace Il2CppInterop.Runtime.Injection { public static class ClassInjector { public static bool IsTypeRegisteredInIl2Cpp<T>() => true; public static void RegisterTypeInIl2Cpp<T>() { } } }
namespace Il2CppInterop.Runtime { public static class Il2CppType { public static Type Of<T>() => typeof(T); } }
namespace Il2CppInterop.Runtime.InteropTypes.Arrays
{
    public class Il2CppStructArray<T> : IEnumerable<T>
    {
        private readonly T[] values;
        public Il2CppStructArray(int count) { values = new T[count]; }
        public Il2CppStructArray(T[] data) { values = data.ToArray(); }
        public int Length => values.Length;
        public T this[int i] {get => values[i]; set => values[i] = value;}
        public IEnumerator<T> GetEnumerator() => ((IEnumerable<T>)values).GetEnumerator();
        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
    public class Il2CppReferenceArray<T> : Il2CppStructArray<T> { public Il2CppReferenceArray(int count) : base(count) { } public Il2CppReferenceArray(T[] data) : base(data) { } }
}
namespace UnityEngine
{
    using Il2CppInterop.Runtime.InteropTypes.Arrays;
    public class Object
    {
        private static int next;
        public IntPtr Pointer = (IntPtr)Interlocked.Increment(ref next);
        public virtual string name { get; set; } = "";
        public bool Destroyed;
        public T? TryCast<T>() where T : Object => this as T;
        public static readonly List<Object> Allocations = new();
        public Object() { Allocations.Add(this); }
        public static void Destroy(Object obj)
        {
            if (obj.Destroyed) return;
            obj.Destroyed = true;
            if (obj is GameObject go)
            {
                foreach (var child in go.transform.Children.ToArray()) Destroy(child.gameObject);
                foreach (var c in go.Components.ToArray()) { c.GetType().GetMethod("OnDestroy")?.Invoke(c,null); c.Destroyed = true; }
                go.transform.SetParent(null,false);
            }
        }
    }
    public class Component : Object
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public override string name {get => gameObject?.name ?? ""; set {if(gameObject!=null)gameObject.name=value;} }
        public T? GetComponent<T>() where T : Component => gameObject.GetComponent<T>();
    }
    public class MonoBehaviour : Component { public MonoBehaviour() { } public MonoBehaviour(IntPtr pointer) {Pointer=pointer;} }
    public class GameObject : Object
    {
        public readonly List<Component> Components = new();
        public readonly Transform transform;
        public bool activeSelf = true;
        public bool activeInHierarchy => activeSelf && (transform.parent == null || transform.parent.gameObject.activeInHierarchy);
        public GameObject(string name) { this.name=name; transform=new Transform {gameObject=this}; Components.Add(transform); }
        public void SetActive(bool value) { activeSelf=value; if(value && activeInHierarchy) foreach(var c in Components) c.GetType().GetMethod("OnEnable")?.Invoke(c,null); }
        public T AddComponent<T>() where T : Component
        {
            var ctor=typeof(T).GetConstructor(new[]{typeof(IntPtr)});
            T c=ctor==null ? (T)Activator.CreateInstance(typeof(T))! : (T)ctor.Invoke(new object[]{(IntPtr)0});
            c.gameObject=this; c.Pointer=(IntPtr)(100000+Object.Allocations.Count); Components.Add(c); return c;
        }
        public T? GetComponent<T>() where T : Component => Components.OfType<T>().FirstOrDefault(c=>!c.Destroyed);
        public T? GetComponentInParent<T>(bool inactive) where T : Component => GetComponent<T>() ?? transform.parent?.gameObject.GetComponentInParent<T>(inactive);
        public T? GetComponentInChildren<T>(bool inactive) where T : Component => GetComponentsInChildren<T>(inactive).FirstOrDefault();
        public T[] GetComponentsInChildren<T>(bool inactive) where T : Component => Components.OfType<T>().Where(c=>!c.Destroyed).Concat(transform.Children.SelectMany(t=>t.gameObject.GetComponentsInChildren<T>(inactive))).ToArray();
    }
    public class Transform : Component
    {
        public Transform? parent;
        public readonly List<Transform> Children=new();
        public Vector3 localPosition,localScale=new(1,1,1);
        public Quaternion localRotation=new(0,0,0,1);
        public void SetParent(Transform? value,bool world) { parent?.Children.Remove(this);parent=value;parent?.Children.Add(this); }
        public Transform? Find(string path)
        {
            var pieces=path.Split('/'); Transform? result=this;
            foreach(var piece in pieces)result=result?.Children.FirstOrDefault(t=>t.name==piece && !t.Destroyed); return result;
        }
    }
    public struct Vector2 { public float x,y; public Vector2(float x,float y){this.x=x;this.y=y;} }
    public struct Vector3 { public float x,y,z; public Vector3(float x,float y,float z){this.x=x;this.y=y;this.z=z;} }
    public struct Vector4 { public float x,y,z,w; public Vector4(float x,float y,float z,float w){this.x=x;this.y=y;this.z=z;this.w=w;} }
    public struct Quaternion { public float x,y,z,w; public Quaternion(float x,float y,float z,float w){this.x=x;this.y=y;this.z=z;this.w=w;} }
    public struct Color { public float r,g,b,a; public Color(float r,float g,float b,float a){this.r=r;this.g=g;this.b=b;this.a=a;} }
    public struct Matrix4x4 { private float[] values; public float this[int r,int c] {get => values[r*4+c];set{values??=new float[16];values[r*4+c]=value;} } }
    public struct BoneWeight { public float weight0,weight1,weight2,weight3;public int boneIndex0,boneIndex1,boneIndex2,boneIndex3; }
    public class Mesh : Object
    {
        public Rendering.IndexFormat indexFormat;
        public int subMeshCount;
        public int vertexCount;
        public Il2CppStructArray<Vector4> tangents;
        public Il2CppStructArray<Color> colors;
        public Il2CppStructArray<Matrix4x4> bindposes = new(0);
        public Il2CppStructArray<BoneWeight> boneWeights = new(0);
        public void SetVertices(Il2CppStructArray<Vector3> data) { vertexCount=data.Length; }
        public void SetNormals(Il2CppStructArray<Vector3> data) { }
        public void SetUVs(int channel,Il2CppStructArray<Vector2> data) { }
        public void SetTriangles(Il2CppStructArray<int> data,int submesh,bool bounds) { }
        public void RecalculateNormals() { }
        public void RecalculateBounds() { }
    }
    public class Renderer : Component
    {
        public bool enabled=true;
        public Il2CppReferenceArray<Material> sharedMaterials=new(0);
        private readonly Dictionary<int,MaterialPropertyBlock> blocks=new();
        public void SetPropertyBlock(MaterialPropertyBlock block,int slot) { blocks[slot]=block; }
        public void GetPropertyBlock(MaterialPropertyBlock block,int slot) { if(blocks.TryGetValue(slot,out var source))foreach(var p in source.Colors)block.SetColor(p.Key,p.Value); }
    }
    public class MeshRenderer : Renderer { }
    public class MeshFilter : Component { public Mesh sharedMesh; }
    public class SkinnedMeshRenderer : Renderer { public Il2CppReferenceArray<Transform> bones=new(0); public Transform rootBone; public Mesh sharedMesh; public bool updateWhenOffscreen; }
    public class LODGroup : Component { public bool enabled=true; }
    public class Shader : Object { public readonly HashSet<string> Properties=new(); }
    public class Material : Object
    {
        public Shader shader;
        public int renderQueue;
        public readonly Dictionary<string,Color> Colors=new();
        public readonly Dictionary<string,float> Floats=new();
        public readonly Dictionary<string,Texture2D> Textures=new();
        public Material(Shader shader) { this.shader=shader; }
        public Material(Material source) { shader=source.shader; foreach(var p in source.Colors)Colors[p.Key]=p.Value;foreach(var p in source.Floats)Floats[p.Key]=p.Value;foreach(var p in source.Textures)Textures[p.Key]=p.Value; }
        public bool HasProperty(string key) => shader.Properties.Contains(key);
        public void SetColor(string key,Color value) {Colors[key]=value;}
        public Color GetColor(string key) => Colors.TryGetValue(key,out var value) ? value : default;
        public void SetFloat(string key,float value){Floats[key]=value;}
        public void SetTexture(string key,Texture2D value){Textures[key]=value;}
        public void SetTextureScale(string key,Vector2 value) { }
        public void SetTextureOffset(string key,Vector2 value) { }
    }
    public class MaterialPropertyBlock { public readonly Dictionary<string,Color> Colors=new(); public void SetColor(string key,Color value){Colors[key]=value;} public Color GetColor(string key)=>Colors[key]; }
    public enum TextureFormat { RGBA32 }
    public enum FilterMode { Bilinear }
    public enum TextureWrapMode { Clamp }
    public class Texture2D : Object { public FilterMode filterMode;public TextureWrapMode wrapMode;public Texture2D(int w,int h,TextureFormat format,bool mip,bool linear){} }
    public static class ImageConversion { public static bool LoadImage(Texture2D target,Il2CppStructArray<byte> bytes,bool unreadable) => true; }
    public static class Debug { public static void Log(string text) { } }
    public enum WrapMode { Loop,ClampForever }
    public enum PlayMode { StopAll }
    public enum AnimationCullingType { AlwaysAnimate }
    public class Animation : Component
    {
        public static bool RejectPlayback;
        public bool playAutomatically,isPlaying;
        public AnimationCullingType cullingType;
        public AnimationClip clip;
        public readonly Dictionary<string,AnimationClip> Clips=new();
        public void AddClip(AnimationClip clip,string name) {Clips[name]=clip;}
        public bool Play(string name,PlayMode mode) {isPlaying=!RejectPlayback && Clips.ContainsKey(name);return isPlaying;}
    }
    public class AnimationClip : Object
    {
        public bool legacy;public float frameRate;public WrapMode wrapMode;
        public readonly List<(string Path,string Property)> Curves=new();
        public void SetCurve(string path,Type type,string property,AnimationCurve curve) { Curves.Add((path,property)); }
        public void EnsureQuaternionContinuity() { }
    }
    public class AnimationCurve {public AnimationCurve(Il2CppStructArray<Keyframe> keys){} }
    public struct Keyframe {public Keyframe(float time,float value,float inSlope,float outSlope){} }
}
namespace UnityEngine.Rendering { public enum IndexFormat { UInt32 } }
namespace Il2Cpp
{
    public class IdentifiableType : UnityEngine.Object { }
    public class SlimeDefinition : IdentifiableType { public Il2Cpp.SlimeAppearance[] AppearancesDefault = System.Array.Empty<Il2Cpp.SlimeAppearance>(); }
    public class SlimeAppearance { public Il2Cpp.SlimeAppearanceStructure[] Structures = System.Array.Empty<Il2Cpp.SlimeAppearanceStructure>(); public Il2Cpp.SlimeFace? _face; }
    public class SlimeAppearanceStructure { public UnityEngine.Material[] DefaultMaterials = System.Array.Empty<UnityEngine.Material>(); public Il2Cpp.SlimeAppearanceMaterials[] ElementMaterials = System.Array.Empty<Il2Cpp.SlimeAppearanceMaterials>(); public Il2Cpp.SlimeAppearanceElement? Element; }
    public class SlimeAppearanceMaterials { public UnityEngine.Material[] Materials = System.Array.Empty<UnityEngine.Material>(); }
    public class SlimeAppearanceElement { public Il2Cpp.SlimeAppearanceObject[] Prefabs = System.Array.Empty<Il2Cpp.SlimeAppearanceObject>(); }
    public class SlimeAppearanceObject : UnityEngine.Component { }
    public class SlimeFace { public Il2Cpp.SlimeExpressionFace[] ExpressionFaces = System.Array.Empty<Il2Cpp.SlimeExpressionFace>(); }
    public struct SlimeExpressionFace { public UnityEngine.Material? Eyes; public UnityEngine.Material? Mouth; }
    public class Identifiable : UnityEngine.Component { public IdentifiableType identType; }
    public class SlimeAppearanceApplicator : UnityEngine.Component { public SlimeDefinition SlimeDefinition; }
}
namespace SlimeBridge.SR2 { internal static class Sr2ImportedAppearancePatch {internal static void Install() { }} }
namespace SlimeBridge.Shared { public static class RegistrySnapshotFormat { public static string Quote(string value) => System.Text.Json.JsonSerializer.Serialize(value); } }
