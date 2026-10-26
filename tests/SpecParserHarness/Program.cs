// Strict-parser harness: compiles the production MiniJson + ImportedSpeciesSpecLoader sources and
// checks them against the real Bridge-owned spec files (passed as argv) plus negative fixtures.
using System.Text;
using SlimeBridge.Shared;

var failures = new List<string>();
var checks = 0;

void Check(string name, bool condition)
{
    checks++;
    if (!condition) failures.Add(name);
}

void CheckThrows(string name, string json)
{
    checks++;
    try { MiniJson.Parse(json); failures.Add(name + " (no exception)"); }
    catch (FormatException) { }
    catch (Exception ex) { failures.Add(name + $" (wrong exception {ex.GetType().Name})"); }
}

// --- MiniJson negative coverage -------------------------------------------------------------
CheckThrows("duplicate key refused", "{\"a\": 1, \"a\": 2}");
CheckThrows("trailing characters refused", "{\"a\": 1} extra");
CheckThrows("unterminated string refused", "{\"a\": \"x}");
CheckThrows("bad escape refused", "{\"a\": \"\\q\"}");
CheckThrows("missing colon refused", "{\"a\" 1}");
CheckThrows("bare number suffix refused", "{\"a\": 1x}");

var tree = MiniJson.Parse("{\"s\": \"v\", \"n\": [1, -2, 3], \"b\": true, \"f\": 1.5, \"z\": null}") as Dictionary<string, object?>;
Check("object parses", tree != null);
Check("string value", tree?["s"] as string == "v");
Check("array length", tree?["n"] is List<object?> { Count: 3 });
Check("negative long", tree?["n"] is List<object?> n && n[1] is long l && l == -2);
Check("bool true", tree?["b"] is true);
Check("double", tree?["f"] is double d && Math.Abs(d - 1.5) < 1e-9);
Check("null", tree != null && tree.ContainsKey("z") && tree["z"] == null);

// --- Loader negative coverage ---------------------------------------------------------------
string spec(string entries) =>
    "{\"schema\": \"bridge-imported-species-spec\", \"schema_version\": 1, \"realm\": \"SR1\", " +
    "\"source_classification_sha256\": \"aaaa\", \"source_diet_sha256\": \"bbbb\", \"entries\": [" + entries + "]}";

string entry(string overrides = "") =>
    "{\"canonical_id\": \"slime.x\", \"display_name\": \"X\", \"status\": \"READY\", \"blockers\": [], " +
    "\"native_id\": 900000, \"native_ref_id\": \"\", " +
    "\"plort\": {\"canonical_id\": \"plort.x\", \"display_name\": \"X Plort\", \"native_id\": 900001, " +
    "\"native_ref_id\": \"\", \"template_native_ref\": \"PINK_PLORT\"}, " +
    "\"template\": {\"slime_native_ref\": \"TABBY_SLIME\", \"plort_native_ref\": \"PINK_PLORT\"}, " +
    "\"diet\": {\"major_food_groups\": [\"FRUIT\"], \"favorites\": [], \"additional_foods\": [], " +
    "\"favorite_production_count\": 2}, " +
    "\"flags\": {\"can_largofy\": false, \"visual_mode\": \"PLACEHOLDER_TEMPLATE\", " +
    "\"behavior_classification\": \"BLOCKED_ON_LOCAL_QUERY\"}, \"notes\": []" + overrides + "}";

List<string> loadProblems(string json, out ImportedSpeciesSpecDoc? doc)
{
    var problems = new List<string>();
    doc = ImportedSpeciesSpecLoader.Load(json, problems);
    return problems;
}

var problems = loadProblems(spec(entry()), out var okDoc);
Check("valid synthetic spec loads", okDoc != null && problems.Count == 0);
Check("synthetic entries", okDoc?.entries.Length == 1);

problems = loadProblems(spec(entry().Replace("\"diet\":", "\"dietX\":")), out _);
Check("unknown entry field refused", problems.Any(p => p.Contains("unknown field 'dietX'")));

problems = loadProblems(spec(entry().Replace("\"native_id\": 900000", "\"native_id\": \"900000\"")), out _);
Check("wrong native_id type refused", problems.Any(p => p.Contains("native_id must be an integer")));

problems = loadProblems(spec(entry().Replace("\"favorite_production_count\": 2", "\"favorite_production_count\": \"2\"")), out _);
Check("wrong diet count type refused", problems.Any(p => p.Contains("favorite_production_count must be an integer")));

problems = loadProblems("{\"schema\": \"bridge-imported-species-spec\", \"schema_version\": 1, \"realm\": \"SR1\", " +
                        "\"source_classification_sha256\": \"a\", \"source_diet_sha256\": \"b\"}", out _);
Check("missing entries refused", problems.Any(p => p.Contains("entries")));

problems = loadProblems(spec(entry(", \"surprise\": 1")), out _);
Check("unknown field inside entry refused", problems.Any(p => p.Contains("unknown field 'surprise'")));

// SR1-008/SR1-009: Int64 narrowing and reserved-range validation.
problems = loadProblems(spec(entry().Replace("\"native_id\": 900000", "\"native_id\": 4295867296")), out _);
Check("native_id beyond Int32 refused", problems.Any(p => p.Contains("must fit in a 32-bit integer")));
problems = loadProblems(spec(entry().Replace("\"native_id\": 900001", "\"native_id\": 4295867296")), out _);
Check("plort native_id beyond Int32 refused", problems.Any(p => p.Contains("must fit in a 32-bit integer")));
problems = loadProblems(spec(entry().Replace("\"native_id\": 900001", "\"native_id\": 1000000")), out var rangeDoc);
Check("plort native_id outside reserved range refused by entry validation",
    problems.Count == 0 && rangeDoc?.entries[0].Problems().Any(p => p.Contains("plort native_id outside the reserved range")) == true);
problems = loadProblems(spec(entry().Replace("\"native_id\": 900001", "\"native_id\": 900000")), out var dupDoc);
Check("plort id equal to slime id refused by entry validation",
    problems.Count == 0 && dupDoc?.entries[0].Problems().Any(p => p.Contains("collides with the slime native_id")) == true);

// --- Appearance block (optional canonical palette) -----------------------------------------
string color(double a = 0.1, double b = 0.2, double c = 0.3, double d = 1) =>
    $"[{a.ToString(System.Globalization.CultureInfo.InvariantCulture)}, {b.ToString(System.Globalization.CultureInfo.InvariantCulture)}, {c.ToString(System.Globalization.CultureInfo.InvariantCulture)}, {d.ToString(System.Globalization.CultureInfo.InvariantCulture)}]";
string paletteBlock(string top = null!, string middle = null!, string bottom = null!) =>
    "{\"top\": " + (top ?? color()) + ", \"middle\": " + (middle ?? color()) + ", \"bottom\": " + (bottom ?? color()) + "}";
string appearanceBlock(string palette) =>
    ", \"appearance\": {\"palette\": " + palette + ", \"ammo\": " + color() + ", \"save_set\": \"CLASSIC\", \"source\": \"new:classic\"}";

problems = loadProblems(spec(entry(appearanceBlock(paletteBlock()))), out var palDoc);
Check("appearance palette loads", problems.Count == 0 && palDoc?.entries[0].appearance?.HasPalette == true);
Check("appearance palette values", palDoc != null && palDoc.entries[0].appearance != null &&
    Math.Abs(palDoc.entries[0].appearance!.top[0] - 0.1) < 1e-6 &&
    Math.Abs(palDoc.entries[0].appearance!.bottom[2] - 0.3) < 1e-6);

problems = loadProblems(spec(entry(appearanceBlock(paletteBlock("[0.1, 0.2, 0.3, 1, 0.9]")))), out _);
Check("appearance 5-component color refused", problems.Any(p => p.Contains("exactly 4 components")));

problems = loadProblems(spec(entry(appearanceBlock(paletteBlock("[1.5, 0.2, 0.3, 1]")))), out _);
Check("appearance out-of-range color refused", problems.Any(p => p.Contains("within [0, 1]")));

problems = loadProblems(spec(entry(appearanceBlock(paletteBlock("[0.1, \"x\", 0.3, 1]")))), out _);
Check("appearance non-number color refused", problems.Any(p => p.Contains("must be a number")));

problems = loadProblems(spec(entry(", \"appearance\": {\"ammo\": " + color() + "}")), out _);
Check("appearance missing palette refused", problems.Any(p => p.Contains("palette is required")));

problems = loadProblems(spec(entry(appearanceBlock(paletteBlock()).Replace("\"ammo\":", "\"ammoX\":"))), out _);
Check("appearance unknown key refused", problems.Any(p => p.Contains("unknown field 'ammoX'")));

problems = loadProblems(spec(entry(appearanceBlock(paletteBlock()).Replace(color(), "[1, 2]"))), out _);
Check("appearance short ammo refused", problems.Any(p => p.Contains("exactly 4 components")));

// --- LabSpecPathGuard (runtime containment for Bridge-owned import paths) --------------------
var guardTmp = Path.Combine(Path.GetTempPath(), "spec-guard-" + Guid.NewGuid().ToString("N"));
var guardRoot = Path.Combine(guardTmp, "root");
var guardSub = Path.Combine(guardRoot, "sub");
Directory.CreateDirectory(guardSub);
var guardSpec = Path.Combine(guardSub, "spec.json");
File.WriteAllText(guardSpec, "{}");
Check("guard accepts file under root", LabSpecPathGuard.IsAllowedFile(guardRoot, guardSpec, out _));
Check("guard refuses file outside root",
    !LabSpecPathGuard.IsAllowedFile(guardRoot, Path.Combine(guardTmp, "outside.json"), out _));
Check("guard refuses the root path itself", !LabSpecPathGuard.IsAllowedFile(guardRoot, guardRoot, out _));
Check("guard refuses missing file", !LabSpecPathGuard.IsAllowedFile(guardRoot, Path.Combine(guardSub, "nope.json"), out _));
Check("guard accepts directory under root", LabSpecPathGuard.IsAllowedDirectory(guardRoot, guardSub, out _));
Check("guard refuses directory outside root",
    !LabSpecPathGuard.IsAllowedDirectory(guardRoot, guardTmp, out _));
var guardLink = Path.Combine(guardRoot, "link");
var mklink = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
{
    FileName = "cmd", Arguments = "/c mklink /J \"" + guardLink + "\" \"" + guardSub + "\"",
    UseShellExecute = false, CreateNoWindow = true,
})!;
mklink.WaitForExit();
if (mklink.ExitCode == 0 && (File.GetAttributes(guardLink) & FileAttributes.ReparsePoint) != 0)
{
    Check("guard refuses junction inside root",
        !LabSpecPathGuard.IsAllowedDirectory(guardRoot, guardLink, out _));
    Check("guard refuses file through junction",
        !LabSpecPathGuard.IsAllowedFile(guardRoot, Path.Combine(guardLink, "spec.json"), out _));
}
else
{
    Console.WriteLine("NOTE: junction tests skipped (mklink unavailable)");
}
try { Directory.Delete(guardTmp, true); } catch { }

// --- Real Bridge-owned spec files (argv) ----------------------------------------------------
foreach (var path in args)
{
    var name = Path.GetFileName(path);
    var json = File.ReadAllText(path, Encoding.UTF8);
    problems = loadProblems(json, out var doc);
    Check($"{name}: loads clean", doc != null && problems.Count == 0);
    if (doc == null || problems.Count > 0)
    {
        foreach (var problem in problems) Console.WriteLine($"  [{name}] {problem}");
        continue;
    }
    if (doc.realm == "SR1")
    {
        Check($"{name}: 9 entries", doc.entries.Length == 9);
        var ringtail = doc.entries.FirstOrDefault(e => e.canonical_id == "slime.ringtail");
        Check($"{name}: ringtail present", ringtail != null);
        Check($"{name}: ringtail READY", ringtail?.status == "READY");
        Check($"{name}: ringtail diet groups", ringtail != null &&
            ringtail.diet.major_food_groups.SequenceEqual(new[] { "FRUIT", "MEAT", "VEGGIES" }));
        Check($"{name}: ringtail plort id", ringtail?.plort.native_id == ringtail?.native_id + 1);
        Check($"{name}: ringtail template", ringtail?.template.slime_native_ref == "TABBY_SLIME");
        Check($"{name}: ringtail palette present", ringtail?.appearance?.HasPalette == true);
        Check($"{name}: ringtail palette is brown", ringtail?.appearance != null &&
            Math.Abs(ringtail.appearance!.top[0] - 0.48235297f) < 1e-5 &&
            Math.Abs(ringtail.appearance!.bottom[0] - 0.16078432f) < 1e-5);
    }
    else
    {
        Check($"{name}: 6 entries", doc.entries.Length == 6);
        var rad = doc.entries.FirstOrDefault(e => e.canonical_id == "slime.rad");
        Check($"{name}: rad present", rad != null);
        Check($"{name}: rad READY", rad?.status == "READY");
        Check($"{name}: rad plort id", rad?.plort.native_id == rad?.native_id + 1);
    }
}

Console.WriteLine($"{checks} checks, {failures.Count} failures");
foreach (var failure in failures) Console.WriteLine("FAILED: " + failure);
return failures.Count == 0 ? 0 : 1;
