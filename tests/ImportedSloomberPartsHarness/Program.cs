using System.Reflection;
using SlimeBridge.SR1;
using UnityEngine;

// Production-linked CPU checks for the Sloomber parts contract: the shader-family palette-rebind
// skip and the [SerializeField] role/shader provenance transport. No Unity rendering and no
// Object.Instantiate: the transfer model below only mirrors Unity's serialized-field rules.
var checks = 0;
void Check(bool condition, string issue) { if (!condition) throw new Exception(issue); checks++; }

// 1. Rebinding policy: only source shaders inside the native slime families may be re-skinned
// onto the destination body/eyes material clone. The Sloomber cloud (SR/FX/Smoke Sloomber) and
// Null Render keep their converter materials; existing species keep the legacy rebind.
foreach (var shader in new[] { "SR/AMP/Slime/Body", "SR/AMP/Slime/Body/Stripe", "SR/AMP/Slime/Body/Default",
    "SR/AMP/Slime/Face/Eyes Default", "SR/AMP/Slime/TwinOrbitals" })
    Check(ImportedPartColors.IsNativeSlimeFamilyShader(shader), "native slime family refused: " + shader);
foreach (var foreign in new[] { "SR/FX/Smoke Sloomber", "SR/Null Render", "Standard", "SR/AMP/Slime",
    "sr/amp/slime/body", "SR/AMP/SlimeTwin", "HDRP/Lit" })
    Check(!ImportedPartColors.IsNativeSlimeFamilyShader(foreign), "foreign shader accepted: " + foreign);
Check(!ImportedPartColors.IsNativeSlimeFamilyShader("") && !ImportedPartColors.IsNativeSlimeFamilyShader(null!),
    "absent shader is not a family member");

// 2. Marker policy semantics: unknown shader = legacy rebind (default), declared foreign shader =
// skip, declared slime shader = rebind; null/empty provenance normalizes to absent.
var marker = new ImportedPartColors { Pattern = null, FaceTex = null, FaceMask = null };
Check(!marker.SourceShaderKnown && !marker.SkipNativePaletteRebind && marker.SourceRole == "",
    "fresh marker must keep the legacy rebind");
marker = new ImportedPartColors { SourceShader = "SR/FX/Smoke Sloomber", SourceRole = "attachment" };
Check(marker.SourceShaderKnown && marker.SkipNativePaletteRebind && marker.SourceRole == "attachment",
    "declared foreign shader must skip the native palette rebind");
marker = new ImportedPartColors { SourceShader = "SR/AMP/Slime/Body", SourceRole = "body" };
Check(marker.SourceShaderKnown && !marker.SkipNativePaletteRebind, "declared slime shader must keep the rebind");
marker = new ImportedPartColors { SourceShader = null!, SourceRole = null! };
Check(!marker.SourceShaderKnown && !marker.SkipNativePaletteRebind &&
      marker.SourceShader == "" && marker.SourceRole == "",
    "null provenance must normalize to absent");

// 3. Serialized-field contract: both provenance strings must be private [SerializeField] fields
// (the lighting-slice lesson: anything else is lost when the visual is Instantiated), and the
// transfer model below must preserve them together with the existing serialized payload.
foreach (var name in new[] { "sourceRole", "sourceShader" })
{
    var field = typeof(ImportedPartColors).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
    Check(field != null && field.IsPrivate && Attribute.IsDefined(field, typeof(SerializeField)),
        "provenance field is not a serialized private field: " + name);
}
ImportedPartColors TransferScalarFields(ImportedPartColors template)
{
    var clone = new ImportedPartColors();
    foreach (var field in typeof(ImportedPartColors).GetFields(BindingFlags.Instance |
                 BindingFlags.Public | BindingFlags.NonPublic))
    {
        if (field.IsStatic || field.IsInitOnly || Attribute.IsDefined(field, typeof(NonSerializedAttribute))) continue;
        if (!field.IsPublic && !Attribute.IsDefined(field, typeof(SerializeField))) continue;
        if (field.FieldType != typeof(string) && field.FieldType != typeof(float) &&
            field.FieldType != typeof(bool) && field.FieldType != typeof(Color) &&
            field.FieldType != typeof(Texture2D)) continue;
        field.SetValue(clone, field.GetValue(template));
    }
    return clone;
}
var cloud = new ImportedPartColors { SourceFace = false, SourceRole = "attachment", SourceShader = "SR/FX/Smoke Sloomber" };
var cloudClone = TransferScalarFields(cloud);
Check(cloudClone.SourceRole == "attachment" && cloudClone.SourceShader == "SR/FX/Smoke Sloomber" &&
      cloudClone.SkipNativePaletteRebind, "clone lost the source provenance or the skip decision");
var body = new ImportedPartColors { SourceRole = "body", SourceShader = "SR/AMP/Slime/Body" };
var bodyClone = TransferScalarFields(body);
Check(bodyClone.SourceShaderKnown && !bodyClone.SkipNativePaletteRebind,
    "clone broke the slime-family rebind");
Check(!TransferScalarFields(new ImportedPartColors()).SkipNativePaletteRebind,
    "legacy clone must keep the legacy rebind");
Check(!TransferScalarFields(cloud).SourceFace && TransferScalarFields(cloud).SkipNativePaletteRebind,
    "source-face flag transfer changed the skip decision");

Console.WriteLine($"sloomber parts checks={checks} failures=0; shader-family policy and serialized provenance model only; no Unity rendering exercised");

namespace UnityEngine
{
    [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute { }
    public class MonoBehaviour { }
    public sealed class Texture2D { }
    public struct Color { public float r, g, b, a; public static Color clear => default; }
    public sealed class Shader { public string name; public Shader(string name) { this.name = name; } }
    public sealed class Material
    {
        public Shader shader;
        public readonly Dictionary<string, float> values = new();
        public Material(string shaderName) { shader = new Shader(shaderName); }
        public bool HasProperty(string name) => values.ContainsKey(name);
        public void SetFloat(string name, float value) { values[name] = value; }
        public float GetFloat(string name) => values[name];
    }
}
