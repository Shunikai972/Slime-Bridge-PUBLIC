// Managed doubles only: selection/material ownership execution, not Unity pose/shader proof.
using System.Numerics;
namespace UnityEngine
{
    public class Object
    {
        public string name = "";
        public bool Destroyed;
        public static void Destroy(Object value) { value.Destroyed = true; }
    }
    public class Component : Object
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public T? GetComponent<T>() where T:Component => gameObject.GetComponent<T>();
        public T[] GetComponentsInChildren<T>(bool inactive) where T:Component => gameObject.GetComponentsInChildren<T>(inactive);
    }
    public class MonoBehaviour : Component { }
    public class GameObject : Object
    {
        private readonly List<Component> components = new();
        public readonly Transform transform;
        public GameObject(string title = "") { name = title; transform = new Transform { gameObject = this }; components.Add(transform); }
        public T AddComponent<T>() where T:Component,new() { var result = new T { gameObject = this }; components.Add(result); return result; }
        public T? GetComponent<T>() where T:Component => components.OfType<T>().FirstOrDefault();
        public T[] GetComponentsInChildren<T>(bool inactive) where T:Component => components.OfType<T>().Concat(transform.Children.SelectMany(c => c.gameObject.GetComponentsInChildren<T>(inactive))).ToArray();
        public T? GetComponentInChildren<T>(bool inactive) where T:Component => GetComponentsInChildren<T>(inactive).FirstOrDefault();
    }
    public class Transform : Component
    {
        public Transform? parent;
        public readonly List<Transform> Children = new();
        public Vector3 localPosition;
        public Quaternion localRotation = Quaternion.identity;
        public Vector3 localScale = Vector3.one;
        public Vector3 position { get => parent == null ? localPosition : parent.TransformPoint(localPosition); set => localPosition = parent == null ? value : parent.worldToLocalMatrix.MultiplyPoint3x4(value); }
        public Quaternion rotation { get => localRotation; set => localRotation = value; }
        public void SetParent(Transform owner, bool worldPositionStays) { parent?.Children.Remove(this); parent = owner; owner.Children.Add(this); }
        public bool IsChildOf(Transform ancestor) { for (var p = parent; p != null; p = p.parent) if (p == ancestor) return true; return false; }
        public Matrix4x4 localToWorldMatrix => new Matrix4x4(System.Numerics.Matrix4x4.CreateScale(localScale.x,localScale.y,localScale.z) * System.Numerics.Matrix4x4.CreateTranslation(localPosition.x,localPosition.y,localPosition.z)) * (parent?.localToWorldMatrix ?? Matrix4x4.identity);
        public Matrix4x4 worldToLocalMatrix { get { System.Numerics.Matrix4x4.Invert(localToWorldMatrix.Value, out var result); return new Matrix4x4(result); } }
        public Vector3 TransformPoint(Vector3 point) => localToWorldMatrix.MultiplyPoint3x4(point);
    }
    public struct Matrix4x4
    {
        public System.Numerics.Matrix4x4 Value;
        public Matrix4x4(System.Numerics.Matrix4x4 value) { Value = value; }
        public static Matrix4x4 identity => new(System.Numerics.Matrix4x4.Identity);
        // Unity uses column vectors, System.Numerics row vectors: reverse the product.
        public static Matrix4x4 operator *(Matrix4x4 a,Matrix4x4 b) => new(b.Value*a.Value);
        public Vector3 MultiplyPoint3x4(Vector3 v) { var p=System.Numerics.Vector3.Transform(new(v.x,v.y,v.z),Value); return new(p.X,p.Y,p.Z); }
    }
    public struct Vector3
    {
        public float x,y,z;
        public Vector3(float a,float b,float c) { x=a;y=b;z=c; }
        public static Vector3 zero => new(0,0,0);
        public static Vector3 one => new(1,1,1);
        public static Vector3 up => new(0,1,0);
        public static Vector3 operator +(Vector3 a,Vector3 b) => new(a.x+b.x,a.y+b.y,a.z+b.z);
        public static Vector3 operator *(Vector3 a,float b) => new(a.x*b,a.y*b,a.z*b);
    }
    public struct Quaternion { public static Quaternion identity => new(); }
    public struct Color { }
    public struct Bounds
    {
        public Vector3 center,size;
        public Bounds(Vector3 c,Vector3 s) { center=c;size=s; }
        public Vector3 min => new(center.x-size.x/2,center.y-size.y/2,center.z-size.z/2);
        public Vector3 max => new(center.x+size.x/2,center.y+size.y/2,center.z+size.z/2);
        public void Encapsulate(Vector3 p) { var a=min;var b=max; a=new(Math.Min(a.x,p.x),Math.Min(a.y,p.y),Math.Min(a.z,p.z)); b=new(Math.Max(b.x,p.x),Math.Max(b.y,p.y),Math.Max(b.z,p.z));center=new((a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2);size=new(b.x-a.x,b.y-a.y,b.z-a.z); }
    }
    public static class Mathf { public static float Max(params float[] values) => values.Max(); }
    public class Mesh : Object { public Bounds bounds = new(Vector3.zero,Vector3.one); }
    public class MeshFilter : Component { public Mesh? sharedMesh; }
    public class Renderer : Component { public Material[] sharedMaterials = Array.Empty<Material>(); public Rendering.ShadowCastingMode shadowCastingMode; }
    public class MeshRenderer : Renderer { }
    public class SkinnedMeshRenderer : Renderer { public Mesh? sharedMesh; }
    public class Material : Object
    {
        public int renderQueue;
        public Material(string title,int queue=2000) { name=title;renderQueue=queue; }
        public Material(Material original) { name=original.name;renderQueue=original.renderQueue; }
        public bool HasProperty(string property) => false;
        public void SetColor(string property,Color value) { }
    }
    public class Camera : Component { public static Camera? main; }
    public class LODGroup : Component { }
}
namespace UnityEngine.Rendering { public enum ShadowCastingMode { Off } }
public class CameraFacingBillboard : UnityEngine.Component { }
public class SlimeAppearanceObject : UnityEngine.Component
{
    public bool IgnoreLODIndex;
    public int LODIndex;
    public SlimeAppearance.SlimeBone ParentBone;
}
public class SlimeAppearance
{
    public enum SlimeBone { None,Root }
    public SlimeAppearanceStructure[] Structures=Array.Empty<SlimeAppearanceStructure>();
    public SlimeFace? Face;
    public Palette ColorPalette=new();
    public class Palette { public UnityEngine.Color Top,Middle,Bottom; }
}
public class SlimeAppearanceElement { public SlimeAppearanceObject[] Prefabs=Array.Empty<SlimeAppearanceObject>(); }
public class SlimeAppearanceStructure
{
    public SlimeAppearanceElement Element=new();
    public bool SupportsFaces;
    public UnityEngine.Material[] DefaultMaterials=Array.Empty<UnityEngine.Material>();
    public SlimeAppearanceMaterials[] ElementMaterials=Array.Empty<SlimeAppearanceMaterials>();
    public SlimeFaceRules[] FaceRules=Array.Empty<SlimeFaceRules>();
}
public struct SlimeAppearanceMaterials { public bool OverrideDefaults; public UnityEngine.Material[] Materials; }
public struct SlimeFaceRules { public bool ShowEyes,ShowMouth; }
public struct SlimeExpressionFace { public UnityEngine.Material? Eyes,Mouth; }
public class SlimeFace { public enum SlimeExpression { Happy }; public SlimeExpressionFace Expression; public SlimeExpressionFace GetExpressionFace(SlimeExpression expression) => Expression; }
public class SlimeAppearanceApplicator : UnityEngine.Component
{
    public UnityEngine.LODGroup? LODGroup;
    public struct BoneMapping { public SlimeAppearance.SlimeBone Bone; public UnityEngine.GameObject BoneObject; }
    public BoneMapping[] Bones=Array.Empty<BoneMapping>();
    public UnityEngine.GameObject? RootAppearanceObject;
}
public class SlimeDefinition { public SlimeAppearance[] AppearancesDefault=Array.Empty<SlimeAppearance>(); }
public class Identifiable { public enum Id { GOLD_SLIME,PHOSPHOR_SLIME } }
public class GameContext
{
    public static GameContext Instance=new();
    public Definitions SlimeDefinitions=new();
    public Lookup LookupDirector=new();
    public class Definitions { public SlimeDefinition Definition=new(); public SlimeDefinition GetSlimeByIdentifiableId(Identifiable.Id id) => Definition; }
    public class Lookup { public UnityEngine.GameObject Prefab=new("template");public UnityEngine.GameObject GetPrefab(Identifiable.Id id) => Prefab; }
}
