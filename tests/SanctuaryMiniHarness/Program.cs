using SlimeBridge.SR1;
using UnityEngine;

internal static class Program
{
    private static int checks,failures;
    private static void Check(bool value,string message) { checks++;if(!value){failures++;Console.WriteLine("FAIL "+message);} }
    private static SlimeAppearanceObject Part(string name,int lod)
    {
        var go=new GameObject(name);var part=go.AddComponent<SlimeAppearanceObject>();part.name=name;part.LODIndex=lod;
        go.AddComponent<MeshFilter>().sharedMesh=new Mesh();go.AddComponent<MeshRenderer>();return part;
    }
    public static int Main()
    {
        try
        {
            var body=new Material("slimeGoldBase");var shine=new Material("Default-Particle",3000);
            var parts=new[]{Part("body0",0),Part("body1",1),Part("body2",2),Part("body3",3),Part("gold_shine",0)};
            var overrides=new SlimeAppearanceMaterials[5];overrides[4]=new(){OverrideDefaults=true,Materials=new[]{shine}};
            var structure=new SlimeAppearanceStructure {Element=new(){Prefabs=parts},DefaultMaterials=new[]{body},ElementMaterials=overrides};
            GameContext.Instance.SlimeDefinitions.Definition=new(){AppearancesDefault=new[]{new SlimeAppearance {Structures=new[]{structure}}}};
            var owner=new GameObject("sanctuary");
            var gold=SanctuaryMiniVisual.Create("GOLD",owner.transform,Vector3.zero,Quaternion.identity,out var count)!;
            Check(gold!=null && count==2,"Gold selects body and shine, excludes higher LODs");
            var renderers=gold.GetComponentsInChildren<MeshRenderer>(true);
            var renderedShine=renderers.Single(r=>r.transform.parent!.gameObject.name=="gold_shine");
            Check(renderedShine.sharedMaterials[0].name.StartsWith("Default-Particle"),"Gold prefab4 LOD0 keeps its particle override");
            var marker=gold.GetComponent<SanctuaryMiniVisual>()!;
            Check(marker.SizeReference.Count()==1,"Gold shine is not a solid size reference");
            Check(body.name=="slimeGoldBase" && shine.name=="Default-Particle","native materials remain untouched");
            Check(!ReferenceEquals(renderedShine.sharedMaterials[0],shine),"copied material is actor-owned");
            var owned=renderers.SelectMany(r=>r.sharedMaterials).ToArray();marker.OnDestroy();
            Check(owned.All(m=>m.Destroyed) && !body.Destroyed && !shine.Destroyed,"cleanup destroys only owned copies");

            var skipped=Part("body-lod1",1);var visible=Part("body-lod0",0);
            var eyes=new Material("eyes");var mouth=new Material("mouth");
            var appearance=new SlimeAppearance {Face=new(){Expression=new(){Eyes=eyes,Mouth=mouth}},Structures=new[]{new SlimeAppearanceStructure {
                Element=new(){Prefabs=new[]{skipped,visible}},SupportsFaces=true,DefaultMaterials=new[]{body},
                FaceRules=new[]{new SlimeFaceRules{ShowEyes=false,ShowMouth=false},new SlimeFaceRules{ShowEyes=true,ShowMouth=true}}}}};
            GameContext.Instance.SlimeDefinitions.Definition.AppearancesDefault=new[]{appearance};
            var face=SanctuaryMiniVisual.Create("PHOSPHOR",owner.transform,Vector3.zero,Quaternion.identity,out count)!;
            Check(count==1,"prefab position and selected LOD differ in face fixture");
            Check(face.GetComponentsInChildren<MeshRenderer>(true).Single().sharedMaterials.Length==3,"face flags indexed by prefab1, not LOD0");

            var left=Part("wing-left",0);var right=Part("wing-right",0);var wing0=new Material("left-wing");var wing1=new Material("right-wing");
            var wingStructure = new SlimeAppearanceStructure {
                Element=new(){Prefabs=new[]{left,right}},DefaultMaterials=new[]{body},ElementMaterials=new[]{
                  new SlimeAppearanceMaterials{OverrideDefaults=true,Materials=new[]{wing0}},
                  new SlimeAppearanceMaterials{OverrideDefaults=true,Materials=new[]{wing1}}}};
            GameContext.Instance.SlimeDefinitions.Definition.AppearancesDefault=new[]{new SlimeAppearance {Structures=new[]{wingStructure}}};
            var wings=SanctuaryMiniVisual.Create("PHOSPHOR",owner.transform,Vector3.zero,Quaternion.identity,out count)!;
            Check(count==2,"two selected static LOD0 wings are retained");
            Check(wings.GetComponentsInChildren<MeshRenderer>(true).SelectMany(r=>r.sharedMaterials).Any(m=>m.name.StartsWith("right-wing")),"second LOD0 wing keeps its independent override");

            var native=GameContext.Instance.LookupDirector.Prefab;
            var applicator=native.AddComponent<SlimeAppearanceApplicator>();
            var visualSpace=new GameObject("appearance-space");visualSpace.transform.SetParent(native.transform,false);visualSpace.transform.localPosition=new Vector3(0,-.5f,0);
            applicator.LODGroup=visualSpace.AddComponent<LODGroup>();
            var bone=new GameObject("serialized-attachment");bone.transform.SetParent(visualSpace.transform,false);bone.transform.localPosition=new Vector3(.4f,.7f,0);
            applicator.Bones=new[]{new SlimeAppearanceApplicator.BoneMapping{Bone=SlimeAppearance.SlimeBone.Root,BoneObject=bone}};
            // RootAppearanceObject is runtime-generated by Awake, and is absent in a reference prefab.
            applicator.RootAppearanceObject=null;
            left.ParentBone=SlimeAppearance.SlimeBone.Root;right.ParentBone=SlimeAppearance.SlimeBone.Root;
            var attached=SanctuaryMiniVisual.Create("PHOSPHOR",owner.transform,Vector3.zero,Quaternion.identity,out count);
            Check(attached!=null && count==2,"serialized attachments work before native Awake creates RootAppearanceObject");
            if(attached!=null)
            {
                var attachedRenderer=attached.GetComponentsInChildren<MeshRenderer>(true).First();
                Check(Math.Abs(attachedRenderer.transform.position.x-.4f)<.001f && Math.Abs(attachedRenderer.transform.position.y-.7f)<.001f,"attachment chain retains native local offset");
            }
        }
        catch(Exception ex){failures++;Console.WriteLine("CAUGHT "+ex.GetType().Name+": "+ex.Message);}
        Console.WriteLine($"MINI_SELECTION_CHECKS={checks} FAILURES={failures}; managed doubles, not native Unity rendering");
        return failures==0?0:1;
    }
}
