using System.Collections.Generic;
namespace UnityEngine.Rendering { public enum LightProbeUsage { BlendProbes, CustomProvided } }
namespace UnityEngine
{
 public struct Vector4 { public float x,y,z,w; public Vector4(float a,float b,float c,float d){x=a;y=b;z=c;w=d;} public static Vector4 zero=>new(0,0,0,0); }
 public static class Shader { static readonly Dictionary<string,int> Ids=new(); public static int PropertyToID(string name){if(!Ids.TryGetValue(name,out var n))Ids[name]=n=Ids.Count+1;return n;} }
 public class Material { public string name; public Material(string n){name=n;} }
 public class MaterialPropertyBlock
 {
  internal Dictionary<int,Vector4> Values=new();
  public void SetVector(int k,Vector4 v)=>Values[k]=v;
  public Vector4 GetVector(int k)=>Values.TryGetValue(k,out var v)?v:Vector4.zero;
  public void SetInt(int k,int n)=>Values[k]=new Vector4(0,0,0,n);
  public int GetInt(int k)=>(int)GetVector(k).w;
  public bool isEmpty=>Values.Count==0;
 }
 public class Renderer
 {
  public Material[] sharedMaterials; public Rendering.LightProbeUsage lightProbeUsage;
  public MaterialPropertyBlock Wide=new(); public Dictionary<int,MaterialPropertyBlock> Slots=new();
  public Renderer(params Material[] m){sharedMaterials=m;}
  public void GetPropertyBlock(MaterialPropertyBlock b){b.Values=new(Wide.Values);}
  public void GetPropertyBlock(MaterialPropertyBlock b,int slot){b.Values=Slots.TryGetValue(slot,out var s)?new(s.Values):new();}
  public void SetPropertyBlock(MaterialPropertyBlock b){Wide=new(){Values=new(b.Values)};}
  public void SetPropertyBlock(MaterialPropertyBlock b,int slot){Slots[slot]=new(){Values=new(b.Values)};}
  public Vector4 Effective(int key,int slot=0)=>Slots.TryGetValue(slot,out var b)&&!b.isEmpty?b.GetVector(key):Wide.GetVector(key);
 }
 public class GameObject { public List<Renderer> Renderers=new(); }
}
namespace SlimeBridge.SR1 { internal static class ImportedModelLoader { public static float LookAmbient=1.2f, LookCool=.5f; } }
