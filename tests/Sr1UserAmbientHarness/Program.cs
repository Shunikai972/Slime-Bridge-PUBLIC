using UnityEngine;
using SlimeBridge.SR1;
internal static class Program
{
 static int checks,failures;
 static void Check(string name,Action test){checks++;try{test();Console.WriteLine("PASS "+name);}catch(Exception e){failures++;Console.WriteLine("FAIL "+name+": "+e.Message);}}
 static void Equal(float x,float y){if(Math.Abs(x-y)>.00001f)throw new InvalidOperationException(x+" != "+y);}
 static int Held=>Shader.PropertyToID("_HeldInVac"); static int Ambient=>Shader.PropertyToID("unity_SHAr");
 // Same indexed Get/Set and held=0 release contract observed in the real SR1 writer.
 static void NativeHeld(Renderer r,bool held){for(int i=0;i<r.sharedMaterials.Length;i++){var b=new MaterialPropertyBlock();r.GetPropertyBlock(b,i);b.SetInt(Held,held?1:0);r.SetPropertyBlock(b,i);}}
 static (GameObject root,Renderer renderer) User(){var r=new Renderer(new Material("SlimeBridgeUserLargo"));return(new GameObject{Renderers={r}},r);}
 static int Main()
 {
  Check("spawn ambient",()=>{var(r,m)=User();AmbientUnderTest.Apply(r);Equal(m.Effective(Ambient).w,1.05f);});
  Check("ambient survives native hold",()=>{var(r,m)=User();AmbientUnderTest.Apply(r);NativeHeld(m,true);Equal(m.Effective(Ambient).w,1.05f);Equal(m.Slots[0].GetInt(Held),1);});
  Check("ambient survives persistent native release block",()=>{var(r,m)=User();AmbientUnderTest.Apply(r);NativeHeld(m,true);NativeHeld(m,false);Equal(m.Effective(Ambient).w,1.05f);Equal(m.Slots[0].GetInt(Held),0);});
  Check("reapply preserves held and unrelated indexed keys",()=>{var(r,m)=User();int k=Shader.PropertyToID("unrelated");var b=new MaterialPropertyBlock();b.SetInt(k,42);b.SetInt(Held,1);m.SetPropertyBlock(b,0);AmbientUnderTest.Apply(r);Equal(m.Slots[0].GetInt(k),42);Equal(m.Slots[0].GetInt(Held),1);Equal(m.Effective(Ambient).w,1.05f);});
  Check("wide unrelated keys preserved",()=>{var(r,m)=User();int k=Shader.PropertyToID("wide");var b=new MaterialPropertyBlock();b.SetInt(k,7);m.SetPropertyBlock(b);AmbientUnderTest.Apply(r);Equal(m.Wide.GetInt(k),7);});
  Check("native renderer untouched",()=>{var m=new Renderer(new Material("Native"));var root=new GameObject{Renderers={m}};AmbientUnderTest.Apply(root);if(!m.Wide.isEmpty||m.Slots.Count!=0||m.lightProbeUsage!=UnityEngine.Rendering.LightProbeUsage.BlendProbes)throw new Exception("native modified");});
  Check("only user slot receives custom indexed ambient",()=>{var(r,m)=User();var own=m.sharedMaterials[0];var native=new Material("Native");m.sharedMaterials=new[]{own,native};var b=new MaterialPropertyBlock();b.SetInt(Shader.PropertyToID("native_key"),3);m.SetPropertyBlock(b,1);AmbientUnderTest.Apply(r);Equal(m.Slots[1].GetVector(Ambient).w,0);Equal(m.Slots[1].GetInt(Shader.PropertyToID("native_key")),3);if(!ReferenceEquals(own,m.sharedMaterials[0])||!ReferenceEquals(native,m.sharedMaterials[1]))throw new Exception("material reference changed");});
  Check("idempotent across repeated cycles",()=>{var(r,m)=User();for(int n=0;n<5;n++){AmbientUnderTest.Apply(r);NativeHeld(m,true);NativeHeld(m,false);Equal(m.Effective(Ambient).w,1.05f);} });
  Console.WriteLine("SR1_AMBIENT_CHECKS="+checks+" FAILURES="+failures+"; exact extracted production method with synthetic MPB precedence, not Unity rendering.");return failures==0?0:1;
 }
}
