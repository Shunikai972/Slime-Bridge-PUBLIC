using SlimeBridge.SR1;
using SlimeBridge.Shared;
using UnityEngine;

const string sourceJson = """
{"shader":"SR/AMP/Slime/Body","floats":{"_LightingUVContribution":0,"_BodyLightingContribution":1,"_LightingUVHorizontalAdjust":0.123456789,"_Unknown":7}}
""";
Dictionary<string, object> Row(string json) => (Dictionary<string, object>)MiniJson.Parse(json)!;
var checks = 0;
void Check(bool condition, string issue) { if (!condition) throw new Exception(issue); checks++; }
Check(ImportedBodyLighting.TryRead(Row(sourceJson), out var source, out var problem) && source != null && problem == "", "source controls refused");
var body = new Material("SR/AMP/Slime/Body/Stripe");
body.values["_Gloss"] = .35f;
Check(source!.Apply(body) == 3 && body.GetFloat("_LightingUVContribution") == 0f &&
      body.GetFloat("_BodyLightingContribution") == 1f && body.GetFloat("_LightingUVHorizontalAdjust") == (float).123456789,
      "final clone kept template lighting or lost double precision");
Check(body.GetFloat("_Gloss") == .35f && !body.values.ContainsKey("_Unknown"), "lighting adapter touched unrelated properties");
Check(source.Matches(body), "material readback rejected assigned source controls");
body.values["_LightingUVContribution"] = 1f;
Check(!source.Matches(body), "readback accepted a template or later override");
foreach (var json in new[] { "{}", "{\"shader\":\"\"}", "{\"shader\":\"SR/AMP/Slime/Body\"}",
    "{\"shader\":\"SR/AMP/Slime/Face/Eyes Default\",\"floats\":{}}" })
    Check(ImportedBodyLighting.TryRead(Row(json), out var absent, out var absentProblem) && absent == null && absentProblem == "", "legacy/unsupported material changed");
foreach (var shader in new[] { "SR/AMP/Slime/Face/Eyes Default", "Standard", "SR/AMP/Slime/Body/Default" })
{
    var other = new Material(shader);
    Check(source.Apply(other) == 0 && other.GetFloat("_LightingUVContribution") == 1f && !source.Matches(other), "unsupported shader changed");
}
var missing = new Material("SR/AMP/Slime/Body/Stripe");
missing.values.Remove("_BodyLightingContribution");
Check(source.Apply(missing) == 0 && missing.GetFloat("_LightingUVContribution") == 1f && !source.Matches(missing), "partial shader mutation");
foreach (var bad in new object[] { true, "0", double.NaN, double.PositiveInfinity, double.NegativeInfinity, -0.01, 1.01, 1e40 })
{
    var row = Row(sourceJson);
    ((Dictionary<string, object>)row["floats"])["_LightingUVContribution"] = bad;
    Check(!ImportedBodyLighting.TryRead(row, out var refused, out var issue) && refused == null && issue.Length > 0, "malformed/out-of-range lighting accepted");
}
var incomplete = Row(sourceJson);
((Dictionary<string, object>)incomplete["floats"]).Remove("_BodyLightingContribution");
Check(!ImportedBodyLighting.TryRead(incomplete, out var partial, out var partialIssue) && partial == null && partialIssue.Length > 0, "partial source controls accepted");
var malformed = Row(sourceJson);
malformed["floats"] = new List<object>();
Check(!ImportedBodyLighting.TryRead(malformed, out var invalidMap, out var mapIssue) && invalidMap == null && mapIssue.Length > 0, "non-object floats accepted");
foreach (var value in new object[] { 0L, 0, 0.0, 0f, 1L, 1, 1.0, 1f })
{
    var row = Row(sourceJson);
    ((Dictionary<string, object>)row["floats"])["_LightingUVContribution"] = value;
    Check(ImportedBodyLighting.TryRead(row, out var numeric, out _) && numeric != null, "supported numeric storage refused");
}
// Model only Unity's documented scalar field transfer, not Object.Instantiate itself.
// This must not copy the custom immutable adapter reference: that was the P1 defect.
ImportedPartColors TransferScalarFields(ImportedPartColors template)
{
    var clone = new ImportedPartColors();
    foreach (var field in typeof(ImportedPartColors).GetFields(System.Reflection.BindingFlags.Instance |
                 System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic))
    {
        if (field.IsStatic || field.IsInitOnly || Attribute.IsDefined(field, typeof(NonSerializedAttribute))) continue;
        if (!field.IsPublic && !Attribute.IsDefined(field, typeof(SerializeField))) continue;
        if (field.FieldType != typeof(float) && field.FieldType != typeof(bool)) continue;
        field.SetValue(clone, field.GetValue(template));
    }
    return clone;
}
var templatePart = new ImportedPartColors { BodyLighting = source, SourceFace = false, Pattern = null, FaceTex = null, FaceMask = null };
var clonePart = TransferScalarFields(templatePart);
var recovered = clonePart.BodyLighting;
Check(recovered != null && clonePart.HasBodyLighting, "template-to-clone scalar field transfer lost body lighting");
var firstCloneMaterial = new Material("SR/AMP/Slime/Body/Stripe");
Check(firstCloneMaterial.GetFloat("_LightingUVContribution") == 1f, "fixture must start at template UV=1");
Check(recovered!.Apply(firstCloneMaterial) == 3 && recovered.Matches(firstCloneMaterial) &&
      firstCloneMaterial.GetFloat("_LightingUVContribution") == 0f, "cloned marker did not restore source UV=0");
Check(ReferenceEquals(recovered, clonePart.BodyLighting), "marker repeatedly allocates the immutable adapter");
Check(!ReferenceEquals(source, recovered), "field transfer borrowed the template managed payload");
var repeatedMaterial = new Material("SR/AMP/Slime/Body/Stripe");
Check(clonePart.BodyLighting!.Apply(repeatedMaterial) == 3 && clonePart.BodyLighting.Matches(repeatedMaterial),
      "repeated rebind lost source lighting");
Check(TransferScalarFields(new ImportedPartColors()).BodyLighting == null, "absent legacy lighting became zero-valued controls");
templatePart.BodyLighting = null;
Check(TransferScalarFields(templatePart).BodyLighting == null && recovered.Matches(firstCloneMaterial),
      "clearing template either retained a declaration or mutated an existing clone");
foreach (var fieldName in new[] { "lightingUVContribution", "bodyLightingContribution", "lightingUVHorizontalAdjust" })
{
    foreach (var invalid in new[] { float.NaN, float.PositiveInfinity, float.NegativeInfinity, -.01f, 1.01f })
    {
        var invalidPart = TransferScalarFields(clonePart);
        typeof(ImportedPartColors).GetField(fieldName, System.Reflection.BindingFlags.Instance |
            System.Reflection.BindingFlags.NonPublic)!.SetValue(invalidPart, invalid);
        Check(invalidPart.HasBodyLighting && invalidPart.BodyLighting == null,
            "serialized out-of-range control was accepted or lost declaration: " + fieldName);
    }
}
Console.WriteLine($"body lighting checks={checks} failures=0; scalar-field transfer model only; no Unity rendering exercised");

namespace UnityEngine
{
    [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute { }
    public class MonoBehaviour { }
    public sealed class Texture2D { }
    public struct Color { public static Color clear => default; }
    public sealed class Shader { public string name; public Shader(string name) { this.name = name; } }
    public sealed class Material
    {
        public Shader shader;
        public readonly Dictionary<string, float> values = new()
        {
            ["_LightingUVContribution"] = 1f, ["_BodyLightingContribution"] = 1f,
            ["_LightingUVHorizontalAdjust"] = 0f,
        };
        public Material(string shaderName) { shader = new Shader(shaderName); }
        public bool HasProperty(string name) => values.ContainsKey(name);
        public void SetFloat(string name, float value) { values[name] = value; }
        public float GetFloat(string name) => values[name];
    }
}
