using System.Security.Cryptography;
using System.Text.Json;
using SlimeBridge.SR2;
using SlimeBridge.Shared;
using UnityEngine;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
int passed=0;
void Check(bool condition,string label){ if(!condition)throw new Exception(label);passed++;Console.WriteLine("PASS " + label); }
var repo=Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,"../../../../../"));
Directory.CreateDirectory(Path.Combine(repo,"Lab/GeneratedContent/species-import"));
var directory=Path.Combine(repo,"Lab/RuntimeArtifacts/synthetic-lifecycle-harness");Directory.CreateDirectory(directory);
string model="""
{"schema":"bridge-native-mesh","schema_version":2,"source_game":"SR1","reference_id":"SlimeDefinition.Rad","skeleton":{"bones":[{"name":"root","parent":-1}]},
 "skinned_renderers":[{"role":"body","mesh_name":"synthetic-body","vertices":[[0,0,0],[1,0,0],[0,1,0]],"triangles":[[0,1,2]],
 "skin_bones":["root"],"root_bone":"root","bind_poses":[[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]],"bone_weights":[[1,0,0,0,0,0,0,0],[1,0,0,0,0,0,0,0],[1,0,0,0,0,0,0,0]],
 "materials":[{"name":"body","colors":{"_TopColor":[1,0,0,1],"_MiddleColor":[0,1,0,1],"_BottomColor":[0,0,1,1]},"floats":{"_Gloss":0.35},"textures":[{"property":"_Noise","file":"data.png"}]},
 {"name":"eyes","colors":{"_EyeRed":[1,1,1,1]},"textures":[{"property":"_FaceAtlas","file":"data.png"}]},
 {"name":"mouth","colors":{"_MouthMid":[1,0,0,1]},"textures":[{"property":"_FaceAtlas","file":"data.png"}]}]}],
 "static_renderers":[{"role":"source-effect","vertices":[[0,0,0],[1,0,0],[0,1,0]],"triangles":[[0,1,2]],"materials":[{"name":"aura"}]}],
 "source_animation":{"default_clip":"idle","clips":[{"name":"idle","duration":1,"sample_rate":30,"loop":true,"times":[0,0.5],"channels":[{"path":"root","property":"local_position","values":[[0,0,0],[0,1,0]]}]}]}}
""";
File.WriteAllText(Path.Combine(directory,"model.json"),model);File.WriteAllBytes(Path.Combine(directory,"data.png"),new byte[]{1,2,3});
string Hash(string name)=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Path.Combine(directory,name)))).ToLowerInvariant();
File.WriteAllText(Path.Combine(directory,"cache-manifest.json"),JsonSerializer.Serialize(new {schema="bridge-asset-cache-manifest",schema_version=1,canonical_id="slime.rad",files=new Dictionary<string,string>{{"model.json",Hash("model.json")},{"data.png",Hash("data.png")}}}));
Material MakeMaterial(string family,params string[] properties){var shader=new Shader{name="native/"+family};shader.Properties.UnionWith(properties);var mat=new Material(shader);return mat;}
(GameObject Actor,Renderer Body,Material BodyMaterial,LODGroup Lod) Actor(Il2Cpp.SlimeDefinition definition,bool faces=true)
{
 var actor=new GameObject("synthetic-actor");actor.AddComponent<Il2Cpp.Identifiable>().identType=definition;
 actor.AddComponent<Il2Cpp.SlimeAppearanceApplicator>().SlimeDefinition=definition;
 var body=actor.AddComponent<MeshRenderer>();
 var material=MakeMaterial("body","_TopColor","_MiddleColor","_BottomColor","_GlossRGB");
 var eye=MakeMaterial("eyes","_EyeRed","_FaceAtlas");var mouth=MakeMaterial("mouth","_MouthMid","_FaceAtlas");
 body.sharedMaterials=new Il2CppReferenceArray<Material>(faces ? new[]{material,eye,mouth}:new[]{material});
 return(actor,body,material,actor.AddComponent<LODGroup>());
}
var previousCache=Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_CACHE_DIR");var previousLab=Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE");
try
{
 Environment.SetEnvironmentVariable("SLIMEBRIDGE_IMPORT_CACHE_DIR",directory);Environment.SetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE",repo);
 Sr2ImportedSpawner.Configure(true);
 var definition=new Il2Cpp.SlimeDefinition();var entry=new ImportedSpeciesEntry{canonical_id="slime.rad"};
 var native=Actor(new Il2Cpp.SlimeDefinition());
 Check(!Sr2ImportedSpawner.TryApply(native.Actor,out _) && native.Body.enabled && native.Lod.enabled && native.Actor.transform.Find(Sr2ImportedVisualMarker.RootName)==null,"native species strict no-op");
 var actor=Actor(definition);Sr2ImportedSpawner.Register(definition,entry,actor.Actor,null);
 Check(actor.Body.enabled && actor.Actor.transform.Find(Sr2ImportedVisualMarker.RootName)==null,"registration does not serialize disabled prefab renderers");
 Check(!Sr2ImportedSpawner.HasAppliedVisual(actor.Actor),"bounds gate unchanged before visual success");
 Check(Sr2ImportedSpawner.TryApply(actor.Actor,out var problem),"source apply succeeds in managed doubles: "+problem);
 var owner=actor.Actor.transform.Find(Sr2ImportedVisualMarker.RootName)!.gameObject.GetComponent<Sr2ImportedVisualMarker>()!;
 var skin=owner.Renderers.Single().TryCast<SkinnedMeshRenderer>()!;
 Check(skin.bones.Length==1 && skin.rootBone.name=="root" && skin.sharedMesh.vertexCount==3 && skin.sharedMesh.boneWeights.Length==3,"real source loader binds skin palette and weights in doubles");
 Check(owner.IgnoredEffectParts==1 && owner.SkippedTextureProperties.SetEquals(new[]{"_Noise"}),"out-of-scope effects recorded and ignored");
 Check(skin.sharedMesh.subMeshCount==1 && skin.sharedMaterials.Length==3 && skin.sharedMaterials[1].shader.name=="native/eyes" && skin.sharedMaterials[2].shader.name=="native/mouth","one authored submesh retains all three native-family material passes");
 Check(skin.sharedMaterials[0].Colors["_TopColor"].r==1 && owner.PaletteVerified==1,"source palette applied with readback");
 Check(skin.sharedMaterials[0].Colors["_GlossRGB"].r==0.35f && actor.BodyMaterial.Colors.Count==0,"source gloss maps without writing native shared assets");
 Check(!actor.Body.enabled && !actor.Lod.enabled && Sr2ImportedSpawner.HasAppliedVisual(actor.Actor),"only committed imported visual suppresses native appearance");
 Check(owner.GetComponent<Animation>()!.isPlaying,"only explicit default source clip plays in doubles");
 var allocationCount=UnityEngine.Object.Allocations.Count;
 Check(Sr2ImportedSpawner.TryApply(actor.Actor,out _) && UnityEngine.Object.Allocations.Count==allocationCount,"reapplication is allocation-free/idempotent");
 var rebuilt=actor.Actor.AddComponent<MeshRenderer>(); rebuilt.sharedMaterials=actor.Body.sharedMaterials;
 Check(Sr2ImportedSpawner.TryApply(actor.Actor,out _) && !rebuilt.enabled,"native reconstruction renderers re-suppressed");
 using(var proof=JsonDocument.Parse(Sr2ImportedSpawner.Proof(actor.Actor)))
 {var row=proof.RootElement.GetProperty("renderers")[0];Check(row.GetProperty("component_type").GetString()=="UnityEngine.SkinnedMeshRenderer" && row.GetProperty("bound_bones").GetInt32()==1,"bounded proof reads source component/bone counts");}
 owner.ReleaseOwned();
 Check(actor.Body.enabled && rebuilt.enabled && actor.Lod.enabled && owner.Owned.Count==0,"owner restores native visibility and releases meshes/materials/clips");
 UnityEngine.Object.Destroy(owner.gameObject);
 var incomplete=Actor(definition,false);
 Check(!Sr2ImportedSpawner.TryApply(incomplete.Actor,out _) && incomplete.Body.enabled && incomplete.Lod.enabled && incomplete.Actor.transform.Find(Sr2ImportedVisualMarker.RootName)==null,"incompatible native face material fails closed and frees partial root");
 Animation.RejectPlayback=true;var rejected=Actor(definition);
 Check(!Sr2ImportedSpawner.TryApply(rejected.Actor,out _) && rejected.Body.enabled && rejected.Lod.enabled && !Sr2ImportedSpawner.HasAppliedVisual(rejected.Actor),"playback failure restores native visibility");
 Animation.RejectPlayback=false;
 // Production path: the registrar passes the native template definition; when the actor lacks the
 // native face families, the template's appearance structures and SlimeFace expressions complete them.
 var templateDefinition=new Il2Cpp.SlimeDefinition();
 var templateAppearance=new Il2Cpp.SlimeAppearance();
 var templateStructure=new Il2Cpp.SlimeAppearanceStructure();
 templateStructure.DefaultMaterials=new[]{MakeMaterial("body","_TopColor","_MiddleColor","_BottomColor")};
 var templateOverride=new Il2Cpp.SlimeAppearanceMaterials();templateOverride.Materials=new[]{MakeMaterial("eyes","_EyeRed","_FaceAtlas")};
 templateStructure.ElementMaterials=new[]{templateOverride};
 var templateElement=new Il2Cpp.SlimeAppearanceElement();
 var templateFace=new GameObject("template-face").AddComponent<Il2Cpp.SlimeAppearanceObject>();
 templateFace.gameObject.AddComponent<MeshRenderer>().sharedMaterials=new Il2CppReferenceArray<Material>(new[]{MakeMaterial("mouth","_MouthMid","_FaceAtlas")});
 templateElement.Prefabs=new[]{templateFace};templateStructure.Element=templateElement;
 templateAppearance.Structures=new[]{templateStructure};
 templateAppearance._face=new Il2Cpp.SlimeFace{ExpressionFaces=new[]{new Il2Cpp.SlimeExpressionFace{Eyes=MakeMaterial("eyes","_EyeRed","_FaceAtlas"),Mouth=MakeMaterial("mouth","_MouthMid","_FaceAtlas")}}};
 templateDefinition.AppearancesDefault=new[]{templateAppearance};
 var templatedDefinition=new Il2Cpp.SlimeDefinition();
 var templateEntry=new ImportedSpeciesEntry{canonical_id="slime.rad"};
 var templated=Actor(templatedDefinition,false);
 var templateStatus=Sr2ImportedSpawner.Register(templatedDefinition,templateEntry,templated.Actor,templateDefinition);
 Check(templateStatus.StartsWith("validated cache bound"),"templated registration validates: "+templateStatus);
 Check(Sr2ImportedSpawner.TryApply(templated.Actor,out var templateProblem),"template completes missing native families: "+templateProblem);
 var templateOwner=templated.Actor.transform.Find(Sr2ImportedVisualMarker.RootName)!.gameObject.GetComponent<Sr2ImportedVisualMarker>()!;
 var templateSkin=templateOwner.Renderers.Single().TryCast<SkinnedMeshRenderer>()!;
 Check(templateSkin.sharedMaterials.Length==3 && templateSkin.sharedMaterials[1].shader.name=="native/eyes" && templateSkin.sharedMaterials[2].shader.name=="native/mouth","template eye/mouth families bind as extra passes");
 templateOwner.ReleaseOwned();UnityEngine.Object.Destroy(templateOwner.gameObject);
 var emptyTemplate=new Il2Cpp.SlimeDefinition();
 var emptyDefinition=new Il2Cpp.SlimeDefinition();
 var emptyActor=Actor(emptyDefinition,false);
 Sr2ImportedSpawner.Register(emptyDefinition,new ImportedSpeciesEntry{canonical_id="slime.rad"},emptyActor.Actor,emptyTemplate);
 Check(!Sr2ImportedSpawner.TryApply(emptyActor.Actor,out _) && emptyActor.Body.enabled && emptyActor.Lod.enabled,"empty template still fails closed for missing families");
 // Multi-appearance template with null Structures on both appearances: only the second appearance's
 // SlimeFace expressions carry the families, pinning both the all-appearances walk and the
 // expressions-outside-the-structures-guard branch.
 var multiTemplate=new Il2Cpp.SlimeDefinition();
 var firstAppearance=new Il2Cpp.SlimeAppearance();firstAppearance.Structures=null;
 var secondAppearance=new Il2Cpp.SlimeAppearance();secondAppearance.Structures=null;
 secondAppearance._face=new Il2Cpp.SlimeFace{ExpressionFaces=new[]{new Il2Cpp.SlimeExpressionFace{Eyes=MakeMaterial("eyesExpr","_EyeRed","_FaceAtlas"),Mouth=MakeMaterial("mouthExpr","_MouthMid","_FaceAtlas")}}};
 multiTemplate.AppearancesDefault=new[]{firstAppearance,secondAppearance};
 var multiDefinition=new Il2Cpp.SlimeDefinition();
 var multi=Actor(multiDefinition,false);
 Sr2ImportedSpawner.Register(multiDefinition,new ImportedSpeciesEntry{canonical_id="slime.rad"},multi.Actor,multiTemplate);
 Check(Sr2ImportedSpawner.TryApply(multi.Actor,out var multiProblem),"multi-appearance template walk completes families: "+multiProblem);
 var multiOwner=multi.Actor.transform.Find(Sr2ImportedVisualMarker.RootName)!.gameObject.GetComponent<Sr2ImportedVisualMarker>()!;
 var multiSkin=multiOwner.Renderers.Single().TryCast<SkinnedMeshRenderer>()!;
 Check(multiSkin.sharedMaterials.Length==3 && multiSkin.sharedMaterials[1].shader.name=="native/eyesExpr" && multiSkin.sharedMaterials[2].shader.name=="native/mouthExpr","multi-appearance walk pins all-appearances and structures-null branches");
 multiOwner.ReleaseOwned();UnityEngine.Object.Destroy(multiOwner.gameObject);
 Sr2ImportedSpawner.Configure(false);var refused=Actor(definition);
 Check(!Sr2ImportedSpawner.TryApply(refused.Actor,out _) && refused.Body.enabled,"non-LAB executable refuses cache");
 Console.WriteLine($"RESULT {passed} passed; 0 failed (managed native doubles only; NOT game proof)");
}
finally
{
 Environment.SetEnvironmentVariable("SLIMEBRIDGE_IMPORT_CACHE_DIR",previousCache);Environment.SetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE",previousLab);Animation.RejectPlayback=false;
}
