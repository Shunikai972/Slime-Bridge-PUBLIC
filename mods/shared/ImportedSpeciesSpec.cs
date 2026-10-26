using System;
using System.Collections.Generic;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Bridge-owned imported-species specification (bridge-imported-species-spec v1) consumed by the
    /// realm registration backends. Produced by tools/content/build_import_spec.py from the reviewed
    /// classification captures plus the additive diet captures; never edited by hand.
    /// Loader-neutral: plain public fields so UnityEngine.JsonUtility (SR1) and Newtonsoft (SR2)
    /// can both bind it.
    /// </summary>
    [Serializable]
    public sealed class ImportedSpeciesSpecDoc
    {
        public string schema = "";
        public int schema_version;
        public string realm = "";
        public string source_classification_sha256 = "";
        public string source_diet_sha256 = "";
        public ImportedSpeciesEntry[] entries = new ImportedSpeciesEntry[0];

        public List<string> Problems()
        {
            var problems = new List<string>();
            if (schema != "bridge-imported-species-spec") problems.Add("unexpected schema " + schema);
            if (schema_version != 1) problems.Add("unexpected schema_version " + schema_version);
            if (realm != "SR1" && realm != "SR2") problems.Add("invalid realm " + realm);
            if (string.IsNullOrEmpty(source_classification_sha256)) problems.Add("missing source classification hash");
            if (string.IsNullOrEmpty(source_diet_sha256)) problems.Add("missing source diet hash");
            if (entries == null) problems.Add("missing entries");
            return problems;
        }
    }

    [Serializable]
    public sealed class ImportedSpeciesEntry
    {
        public string canonical_id = "";
        public string display_name = "";
        public string status = "";
        public string[] blockers = new string[0];
        public int native_id;
        public string native_ref_id = "";
        public ImportedPlortSpec plort = new ImportedPlortSpec();
        public ImportedTemplateSpec template = new ImportedTemplateSpec();
        public ImportedDietSpec diet = new ImportedDietSpec();
        public ImportedFlagsSpec flags = new ImportedFlagsSpec();
        /// <summary>Optional canonical palette for placeholder tinting; null when the capture had none.</summary>
        public ImportedAppearanceSpec? appearance;
        /// <summary>Present on Largo entries: the two parent species (an imported species slug or a native SR1 slime name).</summary>
        public ImportedLargoSpec? largo;

        public List<string> Problems()
        {
            var problems = new List<string>();
            if (string.IsNullOrEmpty(canonical_id)) problems.Add("missing canonical_id");
            if (string.IsNullOrEmpty(display_name)) problems.Add("missing display_name");
            if (status != "READY" && status != "PENDING_DATA") problems.Add("invalid status " + status);
            if (status == "READY")
            {
                if (native_id < 900000 || native_id > 999999) problems.Add("native_id outside the reserved range");
                if (template == null || string.IsNullOrEmpty(template.slime_native_ref)) problems.Add("missing slime template");
                if (diet == null || diet.major_food_groups == null) problems.Add("missing diet");
                if (plort != null && !string.IsNullOrEmpty(plort.canonical_id))
                {
                    if (plort.native_id < 900000 || plort.native_id > 999999)
                        problems.Add("plort native_id outside the reserved range");
                    if (plort.native_id == native_id)
                        problems.Add("plort native_id collides with the slime native_id");
                }
                else if (plort != null && plort.native_id != 0)
                {
                    problems.Add("plort native_id present without a canonical plort id");
                }
            }
            if (plort == null) problems.Add("missing plort section");
            return problems;
        }
    }

    /// <summary>A Largo entry: made from two parents, produces both parents' plorts and has no plort of its own.</summary>
    [Serializable]
    public sealed class ImportedLargoSpec
    {
        public string[] parents = new string[0];
        public string sr2_name = "";
    }

    [Serializable]
    public sealed class ImportedPlortSpec
    {
        public string canonical_id = "";
        public string display_name = "";
        public int native_id;
        public string native_ref_id = "";
        public string template_native_ref = "";
    }

    [Serializable]
    public sealed class ImportedTemplateSpec
    {
        public string slime_native_ref = "";
        public string plort_native_ref = "";
    }

    [Serializable]
    public sealed class ImportedDietSpec
    {
        public string[] major_food_groups = new string[0];
        public string[] favorites = new string[0];
        public string[] additional_foods = new string[0];
        public int favorite_production_count;
    }

    [Serializable]
    public sealed class ImportedFlagsSpec
    {
        public bool can_largofy;
        public string visual_mode = "";
        public string behavior_classification = "";
    }

    /// <summary>Optional canonical palette captured from the source realm's default appearance.</summary>
    [Serializable]
    public sealed class ImportedAppearanceSpec
    {
        public float[] top = new float[0];
        public float[] middle = new float[0];
        public float[] bottom = new float[0];
        public float[] ammo = new float[0];
        public string save_set = "";
        public string source = "";

        public bool HasPalette => top.Length == 4 && middle.Length == 4 && bottom.Length == 4;
    }

    /// <summary>Strict, fail-closed loader for bridge-imported-species-spec v1 documents.</summary>
    public static class ImportedSpeciesSpecLoader
    {
        private static readonly string[] RootKeys = { "schema", "schema_version", "realm", "source_classification_sha256",
            "source_diet_sha256", "entries" };
        private static readonly string[] EntryKeys = { "canonical_id", "display_name", "status", "blockers", "native_id",
            "native_ref_id", "plort", "template", "diet", "flags", "notes", "appearance", "largo" };
        private static readonly string[] AppearanceKeys = { "palette", "ammo", "save_set", "source" };
        private static readonly string[] PaletteKeys = { "top", "middle", "bottom" };
        private static readonly string[] PlortKeys = { "canonical_id", "display_name", "native_id", "native_ref_id",
            "template_native_ref" };
        private static readonly string[] TemplateKeys = { "slime_native_ref", "plort_native_ref" };
        private static readonly string[] DietKeys = { "major_food_groups", "favorites", "additional_foods",
            "favorite_production_count" };
        private static readonly string[] LargoKeys = { "parents", "sr2_name" };
        private static readonly string[] FlagsKeys = { "can_largofy", "visual_mode", "behavior_classification" };

        public static ImportedSpeciesSpecDoc? Load(string json, List<string> problems)
        {
            object? tree;
            try { tree = MiniJson.Parse(json); }
            catch (Exception ex) { problems.Add("spec is not valid JSON: " + ex.Message); return null; }
            if (!(tree is Dictionary<string, object?> root)) { problems.Add("spec root must be an object"); return null; }
            RejectUnknown(root, RootKeys, "spec", problems);
            var doc = new ImportedSpeciesSpecDoc
            {
                schema = RequiredString(root, "schema", "spec", problems),
                schema_version = RequiredInt32(root, "schema_version", "spec", problems),
                realm = RequiredString(root, "realm", "spec", problems),
                source_classification_sha256 = RequiredString(root, "source_classification_sha256", "spec", problems),
                source_diet_sha256 = RequiredString(root, "source_diet_sha256", "spec", problems),
            };
            var entries = new List<ImportedSpeciesEntry>();
            if (!root.TryGetValue("entries", out var rawEntries) || !(rawEntries is List<object?> entryList))
            {
                problems.Add("spec.entries must be an array");
                return null;
            }
            for (var i = 0; i < entryList.Count; i++)
            {
                if (!(entryList[i] is Dictionary<string, object?> entry)) { problems.Add($"spec.entries[{i}] must be an object"); continue; }
                entries.Add(LoadEntry(entry, i, problems));
            }
            doc.entries = entries.ToArray();
            return doc;
        }

        private static ImportedSpeciesEntry LoadEntry(Dictionary<string, object?> entry, int index, List<string> problems)
        {
            var context = $"spec.entries[{index}]";
            RejectUnknown(entry, EntryKeys, context, problems);
            var result = new ImportedSpeciesEntry
            {
                canonical_id = RequiredString(entry, "canonical_id", context, problems),
                display_name = RequiredString(entry, "display_name", context, problems),
                status = RequiredString(entry, "status", context, problems),
                blockers = OptionalStringArray(entry, "blockers", context, problems),
                native_id = RequiredInt32(entry, "native_id", context, problems),
                native_ref_id = OptionalString(entry, "native_ref_id", context, problems),
            };
            if (entry.TryGetValue("plort", out var plortRaw))
            {
                if (plortRaw is Dictionary<string, object?> plort)
                {
                    var plortContext = context + ".plort";
                    RejectUnknown(plort, PlortKeys, plortContext, problems);
                    result.plort = new ImportedPlortSpec
                    {
                        canonical_id = RequiredString(plort, "canonical_id", plortContext, problems, allowEmpty: true),
                        display_name = RequiredString(plort, "display_name", plortContext, problems, allowEmpty: true),
                        native_id = RequiredInt32(plort, "native_id", plortContext, problems),
                        native_ref_id = OptionalString(plort, "native_ref_id", plortContext, problems),
                        template_native_ref = RequiredString(plort, "template_native_ref", plortContext, problems, allowEmpty: true),
                    };
                }
                else problems.Add(plortContext(entry) + " must be an object");
            }
            else problems.Add(context + ".plort is required");
            if (entry.TryGetValue("template", out var templateRaw))
            {
                if (templateRaw is Dictionary<string, object?> template)
                {
                    var templateContext = context + ".template";
                    RejectUnknown(template, TemplateKeys, templateContext, problems);
                    result.template = new ImportedTemplateSpec
                    {
                        slime_native_ref = RequiredString(template, "slime_native_ref", templateContext, problems),
                        plort_native_ref = RequiredString(template, "plort_native_ref", templateContext, problems, allowEmpty: true),
                    };
                }
                else problems.Add(context + ".template must be an object");
            }
            else problems.Add(context + ".template is required");
            if (entry.TryGetValue("diet", out var dietRaw))
            {
                if (dietRaw is Dictionary<string, object?> diet)
                {
                    var dietContext = context + ".diet";
                    RejectUnknown(diet, DietKeys, dietContext, problems);
                    result.diet = new ImportedDietSpec
                    {
                        major_food_groups = OptionalStringArray(diet, "major_food_groups", dietContext, problems, required: true),
                        favorites = OptionalStringArray(diet, "favorites", dietContext, problems, required: true),
                        additional_foods = OptionalStringArray(diet, "additional_foods", dietContext, problems, required: true),
                        favorite_production_count = RequiredInt32(diet, "favorite_production_count", dietContext, problems),
                    };
                }
                else problems.Add(context + ".diet must be an object");
            }
            else problems.Add(context + ".diet is required");
            if (entry.TryGetValue("flags", out var flagsRaw))
            {
                if (flagsRaw is Dictionary<string, object?> flags)
                {
                    var flagsContext = context + ".flags";
                    RejectUnknown(flags, FlagsKeys, flagsContext, problems);
                    result.flags = new ImportedFlagsSpec
                    {
                        can_largofy = RequiredBool(flags, "can_largofy", flagsContext, problems),
                        visual_mode = RequiredString(flags, "visual_mode", flagsContext, problems),
                        behavior_classification = RequiredString(flags, "behavior_classification", flagsContext, problems),
                    };
                }
                else problems.Add(context + ".flags must be an object");
            }
            else problems.Add(context + ".flags is required");
            if (entry.TryGetValue("largo", out var largoRaw))
            {
                if (largoRaw is Dictionary<string, object?> largoDict)
                {
                    var largoContext = context + ".largo";
                    RejectUnknown(largoDict, LargoKeys, largoContext, problems);
                    var parents = OptionalStringArray(largoDict, "parents", largoContext, problems, required: true);
                    if (parents.Length != 2) problems.Add(largoContext + ".parents must name exactly two parents");
                    result.largo = new ImportedLargoSpec { parents = parents, sr2_name = OptionalString(largoDict, "sr2_name", largoContext, problems) };
                }
                else problems.Add(context + ".largo must be an object");
            }
            if (entry.TryGetValue("appearance", out var appearanceRaw))
            {
                if (appearanceRaw is Dictionary<string, object?> appearanceDict)
                {
                    var appearanceContext = context + ".appearance";
                    RejectUnknown(appearanceDict, AppearanceKeys, appearanceContext, problems);
                    var specAppearance = new ImportedAppearanceSpec
                    {
                        save_set = OptionalString(appearanceDict, "save_set", appearanceContext, problems),
                        source = OptionalString(appearanceDict, "source", appearanceContext, problems),
                    };
                    if (appearanceDict.TryGetValue("palette", out var paletteRaw))
                    {
                        if (paletteRaw is Dictionary<string, object?> paletteDict)
                        {
                            var paletteContext = appearanceContext + ".palette";
                            RejectUnknown(paletteDict, PaletteKeys, paletteContext, problems);
                            specAppearance.top = RequiredColor(paletteDict, "top", paletteContext, problems);
                            specAppearance.middle = RequiredColor(paletteDict, "middle", paletteContext, problems);
                            specAppearance.bottom = RequiredColor(paletteDict, "bottom", paletteContext, problems);
                        }
                        else problems.Add(appearanceContext + ".palette must be an object");
                    }
                    else problems.Add(appearanceContext + ".palette is required");
                    if (appearanceDict.TryGetValue("ammo", out var ammoRaw))
                    {
                        if (ammoRaw is List<object?> ammoList)
                        {
                            specAppearance.ammo = ColorsFromList(ammoList, appearanceContext + ".ammo", problems, required: true);
                        }
                        else problems.Add(appearanceContext + ".ammo must be an array");
                    }
                    result.appearance = specAppearance;
                }
                else problems.Add(context + ".appearance must be an object");
            }
            return result;
        }

        private static string plortContext(Dictionary<string, object?> entry) => "spec.entries.plort";

        private static void RejectUnknown(Dictionary<string, object?> dict, string[] allowed, string context, List<string> problems)
        {
            foreach (var key in dict.Keys)
                if (Array.IndexOf(allowed, key) < 0) problems.Add($"{context}: unknown field '{key}'");
        }

        private static string RequiredString(Dictionary<string, object?> dict, string key, string context,
            List<string> problems, bool allowEmpty = false)
        {
            if (!dict.TryGetValue(key, out var value)) { problems.Add($"{context}.{key} is required"); return ""; }
            if (!(value is string text)) { problems.Add($"{context}.{key} must be a string"); return ""; }
            if (!allowEmpty && text.Length == 0) { problems.Add($"{context}.{key} must not be empty"); return ""; }
            return text;
        }

        private static long? RequiredLong(Dictionary<string, object?> dict, string key, string context, List<string> problems)
        {
            if (!dict.TryGetValue(key, out var value)) { problems.Add($"{context}.{key} is required"); return null; }
            if (!(value is long number)) { problems.Add($"{context}.{key} must be an integer"); return null; }
            return number;
        }

        /// <summary>Int64 JSON values must fit Int32 before any unchecked narrowing.</summary>
        private static int RequiredInt32(Dictionary<string, object?> dict, string key, string context, List<string> problems)
        {
            var value = RequiredLong(dict, key, context, problems);
            if (value == null) return 0;
            if (value.Value < int.MinValue || value.Value > int.MaxValue)
            {
                problems.Add($"{context}.{key} must fit in a 32-bit integer");
                return 0;
            }
            return (int)value.Value;
        }

        private static bool RequiredBool(Dictionary<string, object?> dict, string key, string context, List<string> problems)
        {
            if (!dict.TryGetValue(key, out var value)) { problems.Add($"{context}.{key} is required"); return false; }
            if (!(value is bool flag)) { problems.Add($"{context}.{key} must be a boolean"); return false; }
            return flag;
        }

        private static string OptionalString(Dictionary<string, object?> dict, string key, string context, List<string> problems)
        {
            if (!dict.TryGetValue(key, out var value)) return "";
            if (!(value is string text)) { problems.Add($"{context}.{key} must be a string"); return ""; }
            return text;
        }

        private static string[] OptionalStringArray(Dictionary<string, object?> dict, string key, string context,
            List<string> problems, bool required = false)
        {
            if (!dict.TryGetValue(key, out var value))
            {
                if (required) problems.Add($"{context}.{key} is required");
                return new string[0];
            }
            if (!(value is List<object?> list)) { problems.Add($"{context}.{key} must be an array"); return new string[0]; }
            var result = new string[list.Count];
            for (var i = 0; i < list.Count; i++)
            {
                if (!(list[i] is string text)) { problems.Add($"{context}.{key}[{i}] must be a string"); result[i] = ""; }
                else result[i] = text;
            }
            return result;
        }

        /// <summary>RGBA float array as written by build_import_spec; strictly 4 finite values in [0, 1].</summary>
        private static float[] RequiredColor(Dictionary<string, object?> dict, string key, string context, List<string> problems)
        {
            if (!dict.TryGetValue(key, out var value)) { problems.Add($"{context}.{key} is required"); return new float[0]; }
            if (!(value is List<object?> list)) { problems.Add($"{context}.{key} must be an array"); return new float[0]; }
            return ColorsFromList(list, context + "." + key, problems, required: true);
        }

        private static float[] ColorsFromList(List<object?> list, string context, List<string> problems, bool required)
        {
            if (list.Count != 4)
            {
                if (required || list.Count > 0) problems.Add(context + " must have exactly 4 components");
                return new float[0];
            }
            var result = new float[4];
            for (var i = 0; i < 4; i++)
            {
                double number;
                if (list[i] is long intValue) number = intValue;
                else if (list[i] is double doubleValue) number = doubleValue;
                else { problems.Add($"{context}[{i}] must be a number"); return new float[0]; }
                if (double.IsNaN(number) || double.IsInfinity(number) || number < 0.0 || number > 1.0)
                {
                    problems.Add($"{context}[{i}] must be finite and within [0, 1]");
                    return new float[0];
                }
                result[i] = (float)number;
            }
            return result;
        }
    }
}
