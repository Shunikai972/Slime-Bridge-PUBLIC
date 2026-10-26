// CPU boundary over ImportedPetrification: day/night window bounds, per-episode uniform timer,
// cave/solar-shield protection (CaveTrigger.Listener), exact freeze/restore and fail-open
// behavior, against minimal UnityEngine + game-type doubles. No Unity runtime, no GPU:
// a pass means the production state machine kept its documented v1 contract in this model.
using System.Reflection;
using System.Text.Json;
using SlimeBridge.SR1;
using UnityEngine;

var checks = 0;
var failures = 0;
void Check(bool condition, string issue)
{
    checks++;
    if (condition) return;
    failures++;
    Console.Error.WriteLine("FAIL: " + issue);
}
JsonElement Field(ImportedPetrification machine, string key) => JsonDocument.Parse(machine.ToJson()).RootElement.GetProperty(key);
string State(ImportedPetrification machine) => Field(machine, "state").GetString()!;
bool Near(double value, double expected, double tolerance = 0.005) => Math.Abs(value - expected) <= tolerance;

var root = Path.Combine(Path.GetTempPath(), "stone-petrif-harness-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(Path.Combine(root, "Lab", "GeneratedContent", "species-import"));
Environment.SetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE", Path.Combine(root, "anchor-missing.txt"));

// Minimal valid one-LOD stone form cache (mirrors the ringtail fixture in
// test_imported_stone_form.py; geometry is synthetic).
{
    var dir = Path.Combine(root, "Lab", "AssetCache", "sr2-to-sr1", "ringtail");
    Directory.CreateDirectory(dir);
    File.WriteAllBytes(Path.Combine(dir, "tex-alb.png"), new byte[] { 1, 2, 3, 4, 5, 6, 7, 8 });
    File.WriteAllBytes(Path.Combine(dir, "tex-nrm.png"), new byte[] { 9, 10, 11, 12, 13, 14, 15, 16 });
    var document =
        "{\"schema\": \"bridge-stone-form\", \"version\": 1, \"entry\": \"appearance\", " +
        "\"root\": {\"game_object_id\": 4430, \"name\": \"ringtailStatueSmall_v1\", \"transform_id\": 15633, " +
        "\"transform\": {\"local_position\": [0, 0, 0], \"local_rotation\": [0, 0, 0, 1], \"local_scale\": [1, 1, 1]}, \"components\": []}, " +
        "\"meshes\": [{\"game_object_id\": 100, \"game_object_name\": \"LF_ringtail_stone_LOD0\", \"index_count\": 6, " +
        "\"lod\": 0, \"mesh_filter_id\": 200, \"mesh_renderer_id\": 300, \"name\": \"LF_ringtail_stone_LOD0\", " +
        "\"normals\": [[0, 1, 0], [0, 1, 0], [0, 1, 0], [0, 1, 0], [0, 1, 0]], \"path_id\": 400, \"renderer_enabled\": true, " +
        "\"tangents\": [[1, 0, 0, 1], [1, 0, 0, 1], [1, 0, 0, 1], [1, 0, 0, 1], [1, 0, 0, 1]], " +
        "\"transform\": {\"local_position\": [0, -0.5, 0], \"local_rotation\": [0, 0, 0, 1], \"local_scale\": [1, 1, 1]}, " +
        "\"transform_id\": 500, \"triangle_count\": 2, \"triangles\": [[0, 1, 2, 2, 3, 4]], " +
        "\"uv\": [[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.5]], \"vertex_count\": 5, " +
        "\"vertices\": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0.5, 0.5, 0.5]]}], " +
        "\"material\": {\"colors\": {}, \"floats\": {}, \"name\": \"slimeRingtailStoneForm\", \"path_id\": 563, \"render_queue\": 2225, " +
        "\"shader_name\": \"SR/AMP/Paintlight/StaticUV/Base NormalMap\", \"shader_path_id\": 2009, " +
        "\"textures\": {\"_MainTex\": {\"path_id\": 1483, \"name\": \"alb_ringtail_statue\", \"width\": 4, \"height\": 4, \"file\": \"tex-alb.png\"}, " +
        "\"_Normal\": {\"path_id\": 1607, \"name\": \"nrm_ringtail_statue\", \"width\": 4, \"height\": 4, \"file\": \"tex-nrm.png\"}}}}";
    var documentBytes = new System.Text.UTF8Encoding(false).GetBytes(document);
    File.WriteAllBytes(Path.Combine(dir, "stone-form.json"), documentBytes);
    string Sha(byte[] bytes)
    {
        using var sha = System.Security.Cryptography.SHA256.Create();
        var digest = sha.ComputeHash(bytes);
        var sb = new System.Text.StringBuilder(64);
        foreach (var b in digest) sb.Append(b.ToString("x2"));
        return sb.ToString();
    }
    var albedo = File.ReadAllBytes(Path.Combine(dir, "tex-alb.png"));
    var normal = File.ReadAllBytes(Path.Combine(dir, "tex-nrm.png"));
    File.WriteAllText(Path.Combine(dir, "stone-form-receipt.json"),
        "{\"schema\": \"bridge-stone-form\", \"version\": 1, \"source_read_only\": true, \"files\": [" +
        "{\"name\": \"stone-form.json\", \"size\": " + documentBytes.Length + ", \"sha256\": \"" + Sha(documentBytes) + "\"}, " +
        "{\"name\": \"tex-alb.png\", \"size\": " + albedo.Length + ", \"sha256\": \"" + Sha(albedo) + "\"}, " +
        "{\"name\": \"tex-nrm.png\", \"size\": " + normal.Length + ", \"sha256\": \"" + Sha(normal) + "\"}], " +
        "\"counts\": {\"meshes\": 1, \"vertices\": 5, \"triangles\": 2}}");
}

var stoneLoaded = ImportedStoneForm.TryGet("ringtail", out var stoneForm, out var stoneProblem);
Check(stoneLoaded, "stone form fixture must load: " + stoneProblem);

SceneContext.Instance = new SceneContext();
var director = new TimeDirector { CurrHourValue = 12f };
SceneContext.Instance.TimeDirector = director;
UnityEngine.Random.Forced = 1f; // every episode draws the 1.0-minute source minimum

void InvokeUpdate(ImportedPetrification machine) =>
    typeof(ImportedPetrification).GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(machine, null);
void InvokeOnDestroy(ImportedPetrification machine) =>
    typeof(ImportedPetrification).GetMethod("OnDestroy", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(machine, null);

GameObject NewActor(bool withLiveVisual = true, bool withFeral = true)
{
    var actor = new GameObject("actor_ringtail");
    actor.AddComponent<SlimeRandomMove>();
    actor.AddComponent<SlimeEat>();
    actor.AddComponent<GotoConsumable>();
    actor.AddComponent<FleeThreats>();
    if (withFeral) actor.AddComponent<SlimeFeral>();
    if (withLiveVisual)
    {
        var visual = new GameObject("SlimeBridgeImportedVisual");
        visual.transform.parent = actor.transform;
        visual.AddComponent<ImportedVisualMarker>();
        var body = new GameObject("body");
        body.transform.parent = visual.transform;
        var renderer = body.AddComponent<MeshRenderer>();
        renderer.sharedMaterials = new Material[] { new Material("SlimeBridgeNativeBody(Clone)") };
    }
    return actor;
}
ImportedPetrification Attach(GameObject actor, out string problem)
{
    Check(ImportedPetrification.EnsureAttached(actor, "ringtail", stoneForm!, out problem), "attach: " + problem);
    var machine = actor.GetComponent<ImportedPetrification>();
    machine.Step(0f);
    return machine;
}
void Advance(ImportedPetrification machine, float gameSeconds)
{
    machine.Step(0f);
    director.WorldTimeValue += gameSeconds;
    machine.Step(gameSeconds);
}
List<Renderer> LiveRenderers(GameObject actor)
{
    var results = new List<Renderer>();
    foreach (var renderer in actor.GetComponentsInChildren<Renderer>(true))
    {
        var current = renderer.transform;
        var inLive = false;
        while (current != null)
        {
            if (current.gameObject.GetComponent<ImportedVisualMarker>() != null) { inLive = true; break; }
            current = current.parent;
        }
        if (inLive) results.Add(renderer);
    }
    return results;
}
GameObject? StoneVisual(GameObject actor)
{
    var marker = actor.GetComponentInChildren<ImportedStoneFormMarker>(true);
    return marker == null ? null : marker.gameObject;
}
int StoneVisualCount(GameObject actor) => actor.GetComponentsInChildren<ImportedStoneFormMarker>(true).Length;

// ---- Source constants (docs/RINGTAIL_STONE_FORM.md section 2) ----
Check(ImportedPetrification.MinMinutesToTransform == 1f && ImportedPetrification.MaxMinutesToTransform == 10f,
    "timer constants must mirror RingtailMetadata 1.0/10.0");
Check(ImportedPetrification.DaylightStartHour == 6f && ImportedPetrification.DaylightEndHour == 18f,
    "daylight window must be [6, 18)");

// ---- Day/night boundaries: 5.99 / 6 / 17.99 / 18 ----
{
    director.CurrHourValue = 5.99f;
    var night1 = NewActor();
    var m1 = Attach(night1, out _);
    Advance(m1, 120f);
    Check(State(m1) == "alive", "5.99 must stay alive");
    Check(Field(m1, "exposure_minutes").GetDecimal() == 0m, "5.99 must not accumulate");

    director.CurrHourValue = 6f;
    var day1 = NewActor();
    var m2 = Attach(day1, out _);
    Advance(m2, 59f);
    Check(State(m2) == "alive", "6.0 just before its threshold must stay alive");
    Check(Near((double)Field(m2, "exposure_minutes").GetDecimal(), 59.0 / 60.0), "6.0 exposure must accumulate");
    Advance(m2, 2f);
    Check(State(m2) == "stone", "6.0 must petrify once the 1-minute draw elapses");

    director.CurrHourValue = 17.99f;
    var day2 = NewActor();
    var m3 = Attach(day2, out _);
    Advance(m3, 61f);
    Check(State(m3) == "stone", "17.99 must petrify");

    director.CurrHourValue = 18f;
    var night2 = NewActor();
    var m4 = Attach(night2, out _);
    Advance(m4, 120f);
    Check(State(m4) == "alive", "18.0 must stay alive");
    Check(Field(m4, "exposure_minutes").GetDecimal() == 0m, "18.0 must not accumulate");
}

// ---- Uniform draw bounds through the production call site ----
{
    UnityEngine.Random.Forced = float.NaN; // seeded source: draw within [1, 10]
    director.CurrHourValue = 12f;
    var low = NewActor();
    var ml = Attach(low, out _);
    Advance(ml, 59.5f);
    Check(State(ml) == "alive", "no draw in [1,10] can fire before 59.5s");
    Advance(ml, 542f); // 601.5s total exceeds the 10-minute maximum
    Check(State(ml) == "stone", "every draw in [1,10] must have fired by 601.5s");

    var draws = new List<float>();
    UnityEngine.Random.Source = new System.Random(4242);
    for (var i = 0; i < 256; i++) draws.Add(UnityEngine.Random.Range(1f, 10f));
    Check(draws.All(d => d >= 1f && d <= 10f), "all draws must stay within [1,10]");
    Check(draws.Max() - draws.Min() > 1f, "draws must actually spread across the range");
    var second = new List<float>();
    UnityEngine.Random.Source = new System.Random(4242);
    for (var i = 0; i < 256; i++) second.Add(UnityEngine.Random.Range(1f, 10f));
    Check(draws.SequenceEqual(second), "seeded draws must be deterministic");
    UnityEngine.Random.Forced = 1f;
}

// ---- Protection: multi-volume counting, exit, purge, healing ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    var volumeA = new GameObject("caveA");
    var volumeB = new GameObject("solarShieldB");
    machine.OnCaveEnter(volumeA, true, AmbianceDirector.Zone.CAVE);
    machine.OnCaveEnter(volumeB, false, AmbianceDirector.Zone.CAVE);
    Advance(machine, 120f);
    Check(State(machine) == "alive" && Field(machine, "protected").GetBoolean(), "two volumes must protect");
    Check(Field(machine, "exposure_minutes").GetDecimal() == 0m, "protected actors must not accumulate");

    machine.OnCaveExit(volumeA, true, AmbianceDirector.Zone.CAVE);
    Advance(machine, 120f);
    Check(State(machine) == "alive" && Field(machine, "protected").GetBoolean(), "one remaining volume keeps protecting");

    machine.OnCaveExit(volumeB, false, AmbianceDirector.Zone.CAVE);
    Advance(machine, 59f);
    Check(State(machine) == "alive", "after full exit the actor resumes its episode");
    Check(Near((double)Field(machine, "exposure_minutes").GetDecimal(), 59.0 / 60.0), "exposure must resume after exit");
    Advance(machine, 2f);
    Check(State(machine) == "stone", "unprotected daytime actor must petrify");
    var visual = StoneVisual(actor);
    Check(visual != null && visual.activeSelf, "petrified actor must show the stone visual");
    Check(LiveRenderers(actor).All(r => !r.enabled), "petrified actor must hide the live visual");

    machine.OnCaveEnter(volumeA, true, AmbianceDirector.Zone.CAVE);
    Advance(machine, 0.5f);
    Check(State(machine) == "alive", "re-entering protection must revert the statue");
    Check(Field(machine, "exposure_minutes").GetDecimal() == 0m, "revert must reset the accumulator");
    Check(!StoneVisual(actor)!.activeSelf, "reverted actor must hide the stone visual");
    Check(LiveRenderers(actor).All(r => r.enabled), "reverted actor must re-enable the live visual");
    Advance(machine, 0.5f);
    Check(State(machine) == "alive", "double revert must be idempotent");
}
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    var volume = new GameObject("caveDestroyed");
    machine.OnCaveEnter(volume, true, AmbianceDirector.Zone.CAVE);
    UnityEngine.Object.Destroy(volume);
    Advance(machine, 120f);
    Check(!Field(machine, "protected").GetBoolean(), "destroyed volumes must be purged from protection");
    Check(State(machine) == "stone", "purged protection must let the actor petrify again");
}

// ---- Exact freeze / restore, including an authored-disabled control ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var eat = actor.GetComponent<SlimeEat>();
    eat.enabled = false; // authored disabled before any petrification
    var machine = Attach(actor, out _);
    Advance(machine, 61f);
    Check(State(machine) == "stone", "freeze scenario must petrify");
    Check(!actor.GetComponent<SlimeRandomMove>().enabled && !actor.GetComponent<GotoConsumable>().enabled &&
          !actor.GetComponent<FleeThreats>().enabled && !actor.GetComponent<SlimeFeral>().enabled,
        "the four enabled controls must be frozen");
    Check(!eat.enabled, "authored-disabled control stays disabled");
    Check(Field(machine, "frozen_count").GetInt32() == 4, "frozen_count counts only the controls this machine disabled");

    var volume = new GameObject("cave");
    machine.OnCaveEnter(volume, true, AmbianceDirector.Zone.CAVE);
    Advance(machine, 0.5f);
    Check(State(machine) == "alive", "protection must revert");
    Check(actor.GetComponent<SlimeRandomMove>().enabled && !eat.enabled &&
          actor.GetComponent<GotoConsumable>().enabled && actor.GetComponent<FleeThreats>().enabled &&
          actor.GetComponent<SlimeFeral>().enabled, "restore must be exact (authored-disabled stays disabled)");
    Check(Field(machine, "frozen_count").GetInt32() == 0, "restored machine reports no frozen controls");
}
{
    director.CurrHourValue = 12f;
    var actor = NewActor(withFeral: false);
    var machine = Attach(actor, out _);
    Advance(machine, 61f);
    Check(State(machine) == "stone", "missing-component actor must still petrify");
    Check(Field(machine, "frozen_count").GetInt32() == 4, "missing controls are tolerated");
    var volume = new GameObject("cave");
    machine.OnCaveEnter(volume, true, AmbianceDirector.Zone.CAVE);
    Advance(machine, 0.5f);
    Check(State(machine) == "alive" && actor.GetComponent<SlimeRandomMove>().enabled, "missing-component revert must restore");
}

// ---- Fail-open when a frozen control is destroyed mid-stone ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    Advance(machine, 61f);
    UnityEngine.Object.Destroy(actor.GetComponent<SlimeRandomMove>());
    var volume = new GameObject("cave");
    machine.OnCaveEnter(volume, true, AmbianceDirector.Zone.CAVE);
    Advance(machine, 0.5f);
    Check(State(machine) == "alive", "destroyed control must not block revert");
    Check(actor.GetComponent<SlimeEat>().enabled, "surviving controls must restore");
    Check(Field(machine, "frozen_count").GetInt32() == 0, "destroyed control is skipped, not counted");
}

// ---- TimeDirector absent: uncertain world time never petrifies ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    SceneContext.Instance.TimeDirector = null;
    Advance(machine, 600f);
    Check(State(machine) == "alive", "missing TimeDirector must never petrify");
    Check(Field(machine, "exposure_minutes").GetDecimal() == 0m, "missing TimeDirector must not accumulate");
    Check(Field(machine, "daylight").ValueKind == JsonValueKind.Null, "unknown daylight must be reported as null");

    SceneContext.Instance.TimeDirector = director;
    director.CurrHourValue = 12f;
    Advance(machine, 61f);
    Check(State(machine) == "stone", "restored TimeDirector resumes the machine");
    SceneContext.Instance.TimeDirector = null;
    Advance(machine, 0.5f);
    Check(State(machine) == "alive", "fail-open: lost clock reverts the statue");
    SceneContext.Instance.TimeDirector = director;
}

// ---- Re-affirmation heals a re-activated live visual (appearance-guard rebuild) ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    Advance(machine, 61f);
    foreach (var renderer in LiveRenderers(actor)) renderer.enabled = true; // guard rebuild re-enables the visual
    StoneVisual(actor)!.SetActive(false);
    Advance(machine, 0.5f);
    Check(LiveRenderers(actor).All(r => !r.enabled), "Update must re-hide a re-enabled live visual");
    Check(StoneVisual(actor)!.activeSelf, "Update must re-show the stone visual");
}

// ---- Update wiring: native WorldTime feeds the accumulator; stationary clock adds nothing ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    UnityEngine.Time.deltaTime = 0.5f;
    director.WorldTimeValue += 30d;
    InvokeUpdate(machine);
    Check(Near((double)Field(machine, "exposure_minutes").GetDecimal(), 0.5), "Update must accumulate native game seconds");
    UnityEngine.Time.deltaTime = float.NaN;
    InvokeUpdate(machine);
    Check(Near((double)Field(machine, "exposure_minutes").GetDecimal(), 0.5), "non-finite deltas must be ignored");
    UnityEngine.Time.deltaTime = 0f;
}

// ---- EnsureAttached: idempotent, refuses inactive roots and missing models ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    Check(actor.GetComponent<ImportedPetrification>() == null, "fixture starts without a machine");
    var first = ImportedPetrification.EnsureAttached(actor, "ringtail", stoneForm!, out var firstProblem);
    var second = ImportedPetrification.EnsureAttached(actor, "ringtail", stoneForm!, out var secondProblem);
    Check(first && second, "double EnsureAttached must be idempotent: " + firstProblem + " " + secondProblem);
    Check(actor.GetComponentsInChildren<ImportedPetrification>(true).Length == 1, "exactly one machine per actor");
    Check(StoneVisualCount(actor) == 1, "exactly one stone visual per actor");
    Check(StoneVisual(actor)!.activeSelf == false, "stone visual starts inactive");
    Check(actor.GetComponent<ImportedPetrification>() is CaveTrigger.Listener, "machine must implement CaveTrigger.Listener");

    var inactive = NewActor();
    inactive.SetActive(false);
    Check(!ImportedPetrification.EnsureAttached(inactive, "ringtail", stoneForm!, out var inactiveProblem) &&
          inactiveProblem.Contains("inactive", StringComparison.OrdinalIgnoreCase),
        "inactive roots must be refused: " + inactiveProblem);

    Check(!ImportedPetrification.EnsureAttached(NewActor(), "ringtail", null!, out var missingProblem) && missingProblem.Length > 0,
        "missing stone form model must be refused");
}

// ---- OnDestroy restores a petrified actor's controls, idempotently ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var machine = Attach(actor, out _);
    Advance(machine, 61f);
    InvokeOnDestroy(machine);
    Check(actor.GetComponent<SlimeRandomMove>().enabled && actor.GetComponent<SlimeEat>().enabled,
        "OnDestroy must restore frozen controls");
    InvokeOnDestroy(machine);
    Check(State(machine) == "stone" || State(machine) == "alive", "double OnDestroy must not throw");
}

// ---- Regression: metadata says IN-GAME minutes, not wall-clock minutes ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var clockMachine = Attach(actor, out _);
    clockMachine.Step(0f); // seed the native world clock
    director.WorldTimeValue += 60d; // one game minute: 1 real second at the native 60x rate
    clockMachine.Step(1f);
    Check(State(clockMachine) == "stone", "one GAME minute must petrify at the source minimum (not 60 real seconds)");
}
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var clockMachine = Attach(actor, out _);
    clockMachine.Step(0f);
    clockMachine.Step(600f); // real time passes but the native world clock is paused
    Check(State(clockMachine) == "alive", "a paused world clock must not accumulate solar exposure");
}
// ---- Regression: an appearance rebuild can disable the stone renderer, not just its GO ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var clockMachine = Attach(actor, out _);
    clockMachine.Step(0f);
    director.WorldTimeValue += 61d;
    clockMachine.Step(61f);
    foreach (var renderer in StoneVisual(actor)!.GetComponentsInChildren<Renderer>(true)) renderer.enabled = false;
    clockMachine.Step(0f);
    Check(StoneVisual(actor)!.GetComponentsInChildren<Renderer>(true).All(r => r.enabled),
        "stone re-affirmation must restore renderers disabled by an appearance rebuild");
    Check(Field(clockMachine, "stone_visible").GetBoolean() && !Field(clockMachine, "live_visible").GetBoolean(),
        "stone diagnostic must confirm enabled stone mesh and hidden living renderers");
    clockMachine.OnCaveEnter(new GameObject("native_shelter"), false, AmbianceDirector.Zone.CAVE);
    clockMachine.Step(0f);
    Check(!Field(clockMachine, "stone_visible").GetBoolean() && Field(clockMachine, "live_visible").GetBoolean(),
        "shelter diagnostic must confirm visible living renderers and hidden stone mesh");
}
// ---- Native movement regression: missing StalkConsumable + retained scheduler action ----
{
    director.CurrHourValue = 12f;
    var actor = NewActor();
    var stalk = actor.AddComponent<StalkConsumable>();
    var scheduler = actor.AddComponent<SlimeSubbehaviourPlexer>();
    scheduler.Current = stalk;
    var keeper = actor.AddComponent<KeepUpright>();
    var chomp = actor.AddComponent<Chomper>();
    var gameplay = actor.AddComponent<SlimeBridge.SR1.ImportedGameplayAnimation>();
    var visual = actor.GetComponentInChildren<ImportedVisualMarker>(true)!.gameObject;
    var sourcePlayer = visual.AddComponent<SlimeBridge.SR1.ImportedSourceAnimation>();
    var paletteMetadata = visual.AddComponent<SlimeBridge.SR1.ImportedPartColors>();
    var faceOwnership = visual.AddComponent<SlimeBridge.SR1.SourceFaceResourceOwner>();
    var animator = actor.AddComponent<Animator>();
    var rb = actor.AddComponent<Rigidbody>();
    rb.velocity = new Vector3(5f, 0f, 0f);
    rb.angularVelocity = new Vector3(0f, 2f, 0f);
    var machine = Attach(actor, out _);
    Advance(machine, 61f);
    scheduler.RegistryFixedUpdate();
    Check(stalk.Calls == 0, "stone must not execute StalkConsumable.Action through the native scheduler");
    Check(scheduler.Current == null && scheduler.Blockers == 1, "entry must force-deselect then hold exactly one native blocker");
    Check(scheduler.enabled, "grounding/floating scheduler bookkeeping must remain available");
    Check(!stalk.enabled && !keeper.enabled && !chomp.enabled, "unlisted autonomous behaviours and chomp must be stopped");
    Check(!gameplay.enabled && !sourcePlayer.enabled && !animator.enabled && paletteMetadata.enabled && faceOwnership.enabled,
        "living animation must pause without disabling palette metadata or face-resource ownership");
    Check(rb.velocity.x == 0f && rb.angularVelocity.y == 0f, "entry clears residual momentum once");
    Check(!rb.isKinematic && rb.useGravity, "statue must remain dynamic with gravity, not frozen transform");
    rb.velocity = new Vector3(1f, 0f, 0f); // an external impulse after entry
    Advance(machine, 0f);
    Check(rb.velocity.x == 1f, "stone re-affirmation must not erase legitimate external momentum");
    director.CurrHourValue = 18f;
    Advance(machine, 0f);
    Check(scheduler.Blockers == 0 && stalk.enabled && keeper.enabled && chomp.enabled, "night releases owned blocker and restores native controls");
    Check(gameplay.enabled && sourcePlayer.enabled && animator.enabled, "night restores native and source animations exactly");
    Check(!rb.isKinematic && rb.useGravity, "return living does not leave physical locks");
    director.CurrHourValue = 12f;
    Advance(machine, 61f);
    Check(scheduler.Blockers == 1, "second episode registers only one blocker");
    InvokeOnDestroy(machine);
    Check(scheduler.Blockers == 0, "destroy must release the owned scheduler blocker");
}
Console.WriteLine($"petrification checks={checks} failures={failures}; production-linked harness; no Unity runtime exercised");
return failures == 0 ? 0 : 1;

#nullable disable
namespace UnityEngine
{
    public class Object
    {
        public string name = "";
        internal bool Destroyed;
        public static bool operator ==(Object left, Object right)
        {
            var leftNull = left is null || left.Destroyed;
            var rightNull = right is null || right.Destroyed;
            return leftNull ? rightNull : !rightNull && ReferenceEquals(left, right);
        }
        public static bool operator !=(Object left, Object right) => !(left == right);
        public override bool Equals(object other) => other is Object o && this == o;
        public override int GetHashCode() => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(this);
        public static void Destroy(Object target) { if (target != null) target.Destroyed = true; }
        public static void DestroyImmediate(Object target) { if (target != null) target.Destroyed = true; }
        public static void DontDestroyOnLoad(Object target) { }
        public static T Instantiate<T>(T original, Transform parent, bool worldPositionStays) where T : Object
        {
            var clone = original switch
            {
                GameObject gameObject => (Object)gameObject.CloneForHarness(),
                _ => throw new NotSupportedException("harness Instantiate supports GameObjects only"),
            };
            if (clone is GameObject cloned && parent != null) cloned.transform.parent = parent;
            return (T)clone;
        }
    }

    public class Component : Object
    {
        public GameObject gameObject;
        public Transform transform => gameObject.transform;
        public T GetComponent<T>() where T : class => gameObject.GetComponent<T>();
        public T[] GetComponents<T>() where T : class => gameObject.Components.OfType<T>().ToArray();
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : class => gameObject.GetComponentsInChildren<T>(includeInactive);
        public T GetComponentInChildren<T>(bool includeInactive) where T : class => gameObject.GetComponentInChildren<T>(includeInactive);
        public T GetComponentInParent<T>() where T : class => gameObject.GetComponentInParent<T>();
    }

    public class Rigidbody : Component
    {
        public Vector3 velocity;
        public Vector3 angularVelocity;
        public bool isKinematic;
        public bool useGravity = true;
    }
    public class Animator : Behaviour { }

    public class Behaviour : Component
    {
        public bool enabled = true;
    }

    public class MonoBehaviour : Behaviour { }

    public sealed class GameObject : Object
    {
        internal readonly List<Component> Components = new();
        internal readonly List<GameObject> Children = new();
        private readonly Transform _transform;
        public bool activeSelf = true;
        public int layer;

        public GameObject(string name = "")
        {
            this.name = name;
            _transform = new Transform();
            _transform.gameObject = this;
        }

        public Transform transform => _transform;
        public bool activeInHierarchy
        {
            get
            {
                var current = this;
                while (current != null)
                {
                    if (!current.activeSelf) return false;
                    current = current.transform.parent == null ? null : current.transform.parent.gameObject;
                }
                return true;
            }
        }
        public void SetActive(bool value) => activeSelf = value;
        public T AddComponent<T>() where T : Component
        {
            var component = (T)Activator.CreateInstance(typeof(T), nonPublic: true);
            component.gameObject = this;
            Components.Add(component);
            if (component is Transform transform) _transform.SetParent(transform, false);
            return component;
        }
        public T GetComponent<T>() where T : class
        {
            foreach (var component in Components)
                if (component is T match) return match;
            return null;
        }
        public bool TryGetComponent<T>(out T component) where T : class
        {
            component = GetComponent<T>();
            return component != null;
        }
        public T GetComponentInChildren<T>(bool includeInactive) where T : class
        {
            foreach (var component in Components)
                if (component is T match) return match;
            for (var i = 0; i < transform.childCount; i++)
            {
                var found = transform.GetChild(i).gameObject.GetComponentInChildren<T>(includeInactive);
                if (found != null) return found;
            }
            return null;
        }
        public T GetComponentInParent<T>() where T : class
        {
            var current = this;
            while (current != null)
            {
                var found = current.GetComponent<T>();
                if (found != null) return found;
                current = current.transform.parent == null ? null : current.transform.parent.gameObject;
            }
            return null;
        }
        public T[] GetComponentsInChildren<T>(bool includeInactive) where T : class
        {
            var result = new List<T>();
            if (!includeInactive && !activeSelf) return result.ToArray();
            foreach (var component in Components)
                if (component is T match) result.Add(match);
            for (var i = 0; i < transform.childCount; i++)
                result.AddRange(transform.GetChild(i).gameObject.GetComponentsInChildren<T>(includeInactive));
            return result.ToArray();
        }
        internal GameObject CloneForHarness()
        {
            var copy = new GameObject(name + "(Clone)") { layer = layer, activeSelf = activeSelf };
            foreach (var component in Components)
            {
                switch (component)
                {
                    case Transform:
                        break;
                    case MeshFilter filter:
                        copy.AddComponent<MeshFilter>().sharedMesh = filter.sharedMesh;
                        break;
                    case MeshRenderer renderer:
                        copy.AddComponent<MeshRenderer>().sharedMaterials = renderer.sharedMaterials;
                        break;
                    case ImportedStoneFormMarker:
                        copy.AddComponent<ImportedStoneFormMarker>();
                        break;
                }
            }
            for (var i = 0; i < transform.childCount; i++)
                ((GameObject)transform.GetChild(i).gameObject.CloneForHarness()).transform.parent = copy.transform;
            return copy;
        }
    }

    public sealed class Transform : Component
    {
        private readonly List<Transform> _children = new();
        private Transform _parent;
        public Vector3 localPosition;
        public Vector3 localScale = Vector3.one;
        public Quaternion localRotation = Quaternion.identity;

        public Transform parent
        {
            get => _parent;
            set
            {
                if (_parent != null) _parent._children.Remove(this);
                _parent = value;
                if (value != null) value._children.Add(this);
            }
        }
        public int childCount => _children.Count;
        public Transform GetChild(int index) => _children[index];
        public void SetParent(Transform value, bool worldPositionStays) => parent = value;
    }

    public struct Vector2
    {
        public float x, y;
        public Vector2(float x, float y) { this.x = x; this.y = y; }
    }

    public struct Vector3
    {
        public float x, y, z;
        public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
        public static Vector3 zero => new Vector3(0, 0, 0);
        public static Vector3 one => new Vector3(1, 1, 1);
        public static Vector3 operator +(Vector3 a, Vector3 b) => new Vector3(a.x + b.x, a.y + b.y, a.z + b.z);
        public static Vector3 operator -(Vector3 a, Vector3 b) => new Vector3(a.x - b.x, a.y - b.y, a.z - b.z);
        public static Vector3 operator *(Vector3 a, float s) => new Vector3(a.x * s, a.y * s, a.z * s);
    }

    public struct Vector4
    {
        public float x, y, z, w;
        public Vector4(float x, float y, float z, float w) { this.x = x; this.y = y; this.z = z; this.w = w; }
    }

    public struct Quaternion
    {
        public float x, y, z, w;
        public Quaternion(float x, float y, float z, float w) { this.x = x; this.y = y; this.z = z; this.w = w; }
        public static Quaternion identity => new Quaternion(0, 0, 0, 1);
        public float sqrMagnitude => x * x + y * y + z * z + w * w;
    }

    public struct Color
    {
        public float r, g, b, a;
        public Color(float r, float g, float b, float a) { this.r = r; this.g = g; this.b = b; this.a = a; }
        public static Color white => new Color(1, 1, 1, 1);
        public static Color clear => default;
        public static bool operator ==(Color left, Color right) => left.r == right.r && left.g == right.g && left.b == right.b && left.a == right.a;
        public static bool operator !=(Color left, Color right) => !(left == right);
        public override bool Equals(object other) => other is Color c && this == c;
        public override int GetHashCode() => (r, g, b, a).GetHashCode();
    }

    public sealed class Shader : Object
    {
        private static readonly Dictionary<string, Shader> Known = new(StringComparer.Ordinal);
        internal Shader(string shaderName) { name = shaderName; }
        public static Shader Find(string shaderName)
        {
            if (!Known.TryGetValue(shaderName, out var shader)) { shader = new Shader(shaderName); Known[shaderName] = shader; }
            return shader;
        }
    }

    public class Texture : Object
    {
        public TextureWrapMode wrapMode;
    }

    public enum TextureFormat { RGBA32 = 4 }
    public enum TextureWrapMode { Repeat = 0, Clamp = 1 }

    public sealed class Texture2D : Texture
    {
        public static bool FailLoad;
        public bool linear;
        public Texture2D(int width, int height, TextureFormat format, bool mipChain) : this(width, height, format, mipChain, false) { }
        public Texture2D(int width, int height, TextureFormat format, bool mipChain, bool linear) { this.linear = linear; }
        public bool LoadImage(byte[] data) => !FailLoad && data.Length > 0;
    }

    public sealed class Material : Object
    {
        private readonly Dictionary<string, Texture> textures = new(StringComparer.Ordinal);
        private readonly Dictionary<string, float> floats = new(StringComparer.Ordinal);
        private readonly HashSet<string> keywords = new(StringComparer.Ordinal);
        public Shader shader;
        public Color color = Color.white;
        public int renderQueue = -1; // Unity convention: -1 resolves to the shader's own queue
        public Material(Shader shader) { this.shader = shader; }
        public Material(string shaderName) { shader = Shader.Find(shaderName); }
        public Material(Material source) { shader = source.shader; color = source.color; foreach (var pair in source.textures) textures[pair.Key] = pair.Value; }
        public Texture mainTexture
        {
            get => textures.TryGetValue("_MainTex", out var texture) ? texture : null;
            set { if (value != null) textures["_MainTex"] = value; }
        }
        public bool HasProperty(string property) =>
            property == "_MainTex" || property == "_Color" || property == "_Normal" || property == "_BumpMap" || textures.ContainsKey(property) || floats.ContainsKey(property);
        public void SetTexture(string property, Texture texture) { if (texture != null) textures[property] = texture; }
        public Texture GetTexture(string property) => textures.TryGetValue(property, out var texture) ? texture : null;
        public void SetFloat(string property, float value) => floats[property] = value;
        public float GetFloat(string property) => floats.TryGetValue(property, out var value) ? value : 0f;
        public void EnableKeyword(string keyword) => keywords.Add(keyword);
        public bool IsKeywordEnabled(string keyword) => keywords.Contains(keyword);
    }

    public sealed class Mesh : Object
    {
        public int vertexCount => Vertices.Count;
        public int subMeshCount { get; internal set; }
        public List<Vector3> Vertices { get; } = new();
        public List<Vector3> Normals { get; } = new();
        public List<Vector2> UVs { get; } = new();
        public List<Vector4> Tangents { get; } = new();
        public List<int> TriangleList { get; } = new();
        public int[] triangles => TriangleList.ToArray();
        public void SetVertices(List<Vector3> values) { Vertices.Clear(); Vertices.AddRange(values); }
        public void SetNormals(List<Vector3> values) { Normals.Clear(); Normals.AddRange(values); }
        public void SetUVs(int channel, List<Vector2> values) { UVs.Clear(); UVs.AddRange(values); }
        public void SetTangents(List<Vector4> values) { Tangents.Clear(); Tangents.AddRange(values); }
        public void SetTriangles(List<int> values, int submesh) { TriangleList.Clear(); TriangleList.AddRange(values); subMeshCount = 1; }
        public void RecalculateBounds() { }
    }

    public sealed class MeshFilter : Component
    {
        public Mesh sharedMesh;
    }

    public class Renderer : Component
    {
        public bool enabled = true;
        public Material[] sharedMaterials = Array.Empty<Material>();
        public bool HasPropertyBlock() => false;
    }

    public sealed class MeshRenderer : Renderer { }

    public static class Time
    {
        public static float deltaTime;
    }

    public static class Random
    {
        public static System.Random Source = new System.Random(20260709);
        public static float Forced = float.NaN;
        public static float Range(float min, float max) =>
            !float.IsNaN(Forced) ? Forced : (float)(min + Source.NextDouble() * (max - min));
    }

    public static class Debug
    {
        public static void Log(object message) { }
        public static void LogWarning(object message) { Console.Error.WriteLine("WARN: " + message); }
        public static void LogError(object message) { Console.Error.WriteLine("ERROR: " + message); }
    }
}

public class AmbianceDirector
{
    public enum Zone { UNKNOWN = 0, CAVE = 10000 }
}

public class CaveTrigger
{
    public interface Listener
    {
        void OnCaveEnter(GameObject gameObject, bool affectLighting, AmbianceDirector.Zone caveZone);
        void OnCaveExit(GameObject gameObject, bool affectLighting, AmbianceDirector.Zone caveZone);
    }
}

public class TimeDirector
{
    public float CurrHourValue = 12f;
    public double WorldTimeValue;
    public float CurrHour() => CurrHourValue;
    public double WorldTime() => WorldTimeValue;
}

public class SceneContext
{
    public static SceneContext Instance;
    public TimeDirector TimeDirector;
}

public class SlimeSubbehaviour : MonoBehaviour
{
    public virtual void Action() { }
    public virtual bool CanRethink() => false;
}
public class StalkConsumable : SlimeSubbehaviour
{
    public int Calls;
    public override void Action() { Calls++; }
}
public class SlimeSubbehaviourPlexer : MonoBehaviour
{
    public SlimeSubbehaviour Current;
    public int Blockers;
    public void ForceRethink() { Current = null; }
    public void RegisterBehaviorBlocker() { Blockers++; }
    public void UnregisterBehaviorBlocker() { Blockers--; }
    // Mirror the important native boundary: a selected action is an explicit call,
    // not a Unity message suppressed by Behaviour.enabled.
    public void RegistryFixedUpdate()
    {
        if (Blockers > 0 && Current != null && Current.CanRethink()) { Current = null; return; }
        Current?.Action(); // a non-rethinkable action continues under a blocker until ForceRethink
    }
}
public class KeepUpright : MonoBehaviour { }
public class Chomper : MonoBehaviour { }
public class SlimeRandomMove : SlimeSubbehaviour { }
public class SlimeEat : MonoBehaviour { }
public class GotoConsumable : MonoBehaviour { }
public class FleeThreats : MonoBehaviour { }
public class SlimeFeral : MonoBehaviour { }
