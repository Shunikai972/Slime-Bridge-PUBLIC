using SlimeBridge.Shared;

// Compile and exercise the actual shared C# source, without Unity or a game process.
var scratch = Environment.GetEnvironmentVariable("TMPDIR") ?? throw new Exception("TMPDIR required");
var root = Path.Combine(scratch, "bridge-journal-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
var path = Path.Combine(root, "ops.journal");
int checks = 0;
void Check(bool condition, string description)
{
    checks++;
    if (!condition) throw new Exception("FAIL: " + description);
}
void CheckRefusedJournal(string contents, string description)
{
    File.WriteAllText(path, contents);
    var bytes = File.ReadAllBytes(path);
    Check(OpJournal.Open(path, out var error) == null && error.StartsWith("InvalidDataException:", StringComparison.Ordinal) &&
        File.ReadAllBytes(path).SequenceEqual(bytes), description + " (no bytes changed)");
}
var result = new CommandResult { Realm = "SR1", OpId = "test:op", Kind = "remove", Item = "slime.pink",
    SaveId = "test-save", Count = 1, Before = 2, After = 1, Applied = true, Ok = true, Persisted = true,
    Verified = true, SaveFile = "test-save_2", Sha256 = new string('a', 64), Uncertain = false, CanAdd = true };
var json = result.ToJson(1234);
var ringtailJson = json.Replace("slime.pink", "slime.ringtail");
var ringtailCommand = new BridgeCommand { OpId = "test:op", Kind = "remove", Item = "slime.ringtail", Count = 1, SaveId = "test-save" };
Check(BridgeCommand.Parse(ringtailJson)?.Item == "slime.ringtail", "Ringtail inventory command parses without remapping");
Check(CommandResult.ReplayInventory(ringtailCommand, "SR1", ringtailJson, r => r.Item == "slime.ringtail" && r.After == 1).Ok,
    "Ringtail replay retains canonical identity and disk/live proof");
Check(CommandResult.ReplayInventory(ringtailCommand, "SR1", ringtailJson, _ => false).Uncertain,
    "Ringtail stale native proof never becomes replay success");
Check(json.Contains("\"count\":1"), "requested count serialized");
var replayCommand = new BridgeCommand { OpId = "test:op", Kind = "remove", Item = "slime.pink", Count = 1, SaveId = "test-save" };
var replay = CommandResult.ReplayInventory(replayCommand, "SR1", json, r => r.After == 1);
Check(replay.Ok && replay.Persisted && replay.Verified == true && replay.ToJson(4321).Contains("\"pid\":4321"), "bound verified replay uses current pid");
Check(CommandResult.ReplayInventory(new BridgeCommand { OpId = "test:op", Kind = "remove", Item = "slime.pink", Count = 2, SaveId = "test-save" }, "SR1", json, _ => true).Uncertain, "count mismatch never replays");
Check(CommandResult.ReplayInventory(replayCommand, "SR1", json, _ => false).Uncertain, "failed disk/live recheck uncertain");
Check(CommandResult.ReplayInventory(replayCommand, "SR1", json.Replace("\"count\":1", "\"count\":\"1\""), _ => true).Uncertain, "mistyped count refuses replay");
Check(CommandResult.ReplayInventory(replayCommand, "SR1", json.Replace("\"count\":1,", ""), _ => true).Uncertain, "old receipt without count uncertain");
Check(CommandResult.ReplayInventory(replayCommand, "SR1", json.Replace("\"persisted\":true", "\"persisted\":false"), _ => true).Uncertain, "prior save failure stays uncertain");
void CheckUncertainReceipt(string receipt, string description, BridgeCommand? command = null, string realm = "SR1")
{
    var rechecks = 0;
    var denied = CommandResult.ReplayInventory(command ?? replayCommand, realm, receipt, _ => { rechecks++; return true; });
    Check(denied.Uncertain && !denied.Ok && !denied.Persisted && denied.Verified != true && rechecks == 0,
        description + " (no proof callback)");
}
foreach (var field in new[] { (Name: "protocol_version", Value: "1", Bad: "2"),
                              (Name: "message_type", Value: "\"command_result\"", Bad: "\"heartbeat\""),
                              (Name: "pid", Value: "1234", Bad: "0") })
{
    var token = "\"" + field.Name + "\":" + field.Value;
    CheckUncertainReceipt(json.Replace(token + ",", ""), "missing replay provenance: " + field.Name);
    CheckUncertainReceipt(json.Replace(token, "\"" + field.Name + "\":" + field.Bad), "invalid replay provenance: " + field.Name);
    CheckUncertainReceipt(json.Replace(token, "\"" + field.Name + "\":false"), "mistyped replay provenance: " + field.Name);
}
CheckUncertainReceipt(json.Replace("\"pid\":1234", "\"pid\":-1"), "negative execution PID rejected");
foreach (var binding in new[] { (Name: "realm", Value: "SR1", Bad: "SR2"), (Name: "op_id", Value: "test:op", Bad: "test:other"),
                                (Name: "kind", Value: "remove", Bad: "add"), (Name: "item", Value: "slime.pink", Bad: "plort.pink"),
                                (Name: "save_id", Value: "test-save", Bad: "other-save") })
{
    var token = "\"" + binding.Name + "\":\"" + binding.Value + "\"";
    CheckUncertainReceipt(json.Replace(token, "\"" + binding.Name + "\":\"" + binding.Bad + "\""),
        "replay binding mismatch: " + binding.Name);
}
var refusalJson = json.Replace("\"ok\":true", "\"ok\":false").Replace("\"applied\":true", "\"applied\":false")
    .Replace("\"persisted\":true", "\"persisted\":false").Replace("\"verified\":true", "\"verified\":false")
    .Replace("\"after\":1", "\"after\":2");
var cleanRefusal = CommandResult.ReplayInventory(replayCommand, "SR1", refusalJson, _ => throw new Exception("refusal must not need disk proof"));
Check(!cleanRefusal.Ok && !cleanRefusal.Applied && !cleanRefusal.Uncertain && !cleanRefusal.Persisted,
    "explicitly bound false flags retain a clean preflight refusal");
foreach (var receipt in new[] { refusalJson, json })
    foreach (var flag in new[] { "uncertain", "persisted", "ok", "applied" })
    {
        var token = "\"" + flag + "\":" + (flag == "uncertain" || receipt == refusalJson ? "false" : "true");
        CheckUncertainReceipt(receipt.Replace(token + ",", ""), "missing outcome flag is uncertain: " + flag);
        CheckUncertainReceipt(receipt.Replace(token, "\"" + flag + "\":\"false\""), "mistyped outcome flag is uncertain: " + flag);
    }
CheckUncertainReceipt(refusalJson.Replace("\"persisted\":false", "\"persisted\":true"), "persisted refusal is contradictory");
CheckUncertainReceipt(refusalJson.Replace("\"verified\":false", "\"verified\":true"), "verified refusal is contradictory");
foreach (var invalidDelta in new[] { (Name: "before", Value: "2", Bad: "-1"), (Name: "after", Value: "1", Bad: "-1"),
                                    (Name: "before", Value: "2", Bad: "1"), (Name: "after", Value: "1", Bad: "2") })
{
    var token = "\"" + invalidDelta.Name + "\":" + invalidDelta.Value;
    CheckUncertainReceipt(json.Replace(token, "\"" + invalidDelta.Name + "\":" + invalidDelta.Bad),
        "impossible successful removal delta: " + invalidDelta.Name + "=" + invalidDelta.Bad);
}
foreach (var receipt in new[] { json, refusalJson })
    foreach (var endpoint in new[] { "before", "after" })
    {
        var token = "\"" + endpoint + "\":" + (endpoint == "before" || receipt == refusalJson ? "2" : "1");
        CheckUncertainReceipt(receipt.Replace(token + ",", ""), "missing inventory delta endpoint: " + endpoint);
        CheckUncertainReceipt(receipt.Replace(token, "\"" + endpoint + "\":\"2\""), "mistyped inventory delta endpoint: " + endpoint);
    }
CheckUncertainReceipt(refusalJson.Replace("\"after\":2", "\"after\":1"), "changed count cannot be a clean refusal");
CheckUncertainReceipt(refusalJson.Replace("\"before\":2", "\"before\":-2").Replace("\"after\":2", "\"after\":-2"),
    "invalid unknown-count sentinel cannot be a clean refusal");
var addCommand = new BridgeCommand { OpId = "test:op", Kind = "add", Item = "slime.pink", Count = 1, SaveId = "test-save" };
var addJson = json.Replace("\"kind\":\"remove\"", "\"kind\":\"add\"").Replace("\"after\":1", "\"after\":3");
Check(CommandResult.ReplayInventory(addCommand, "SR1", addJson, _ => true).Ok, "matching add delta replays");
CheckUncertainReceipt(addJson.Replace("\"after\":3", "\"after\":2"), "successful add requires the full positive delta", addCommand);
CheckUncertainReceipt(addJson.Replace("\"before\":2", "\"before\":2147483647").Replace("\"after\":3", "\"after\":-2147483648"),
    "overflow cannot turn impossible add delta into success", addCommand);
var labCommand = new BridgeCommand { OpId = "test:op", Kind = "lab_give", Item = "slime.pink", Count = 1, SaveId = "test-save" };
var labJson = addJson.Replace("\"kind\":\"add\"", "\"kind\":\"lab_give\"");
Check(CommandResult.ReplayInventory(labCommand, "SR1", labJson, _ => true).Ok, "matching lab-give delta replays");
CheckUncertainReceipt(labJson.Replace("\"after\":3", "\"after\":2"), "lab-give success requires the full positive delta", labCommand);
foreach (var retired in new[] { "07afb0b92e1b4461be1560b7da6b995d", "2e2a689bf0f7452eb63db918ffd4036c", "3209eadc2fd14b35982ac256f733091b", "56a077126cb24847b4e46f1216fb0ec4", "9f1fea2d47ef4594a540a5eb83805802", "ae635bb84dde47e192ae0f2e8cfaa278", "b767b95d44d546cfa11bf567e46fdfb3", "c5c48aa00cda4e378dc3a0375571f9e3", "ef9415e10fdf4b509039141770bd03d5" })
{
    foreach (var retiredKind in new[] { "create_save", "reconcile_save" })
        Check(BridgeCommand.Parse("{\"command\":{\"op_id\":\"" + retired + ":x\",\"kind\":\"" + retiredKind + "\",\"creation_id\":\"" + retired + "\"}}") == null,
            "retired legacy creation id never parses into a native command: " + retiredKind);
    Check(BridgeCommand.IsRetiredCreationId(retired) && !BridgeCommand.IsRetiredCreationId(retired.ToUpperInvariant()) &&
        !BridgeCommand.IsRetiredCreationId(retired.Substring(1)), "retired id match is exact");
}
var freshId = new string('d', 32);
Check(BridgeCommand.Parse("{\"command\":{\"op_id\":\"" + freshId + ":create\",\"kind\":\"create_save\",\"creation_id\":\"" + freshId + "\"}}")?.CreationId == freshId,
    "fresh creation id still parses");
foreach (var invalidKind in new[] { "query", "create_save", "reconcile_save" })
{
    var command = new BridgeCommand { OpId = "test:op", Kind = invalidKind, Item = "slime.pink", Count = 1, SaveId = "test-save" };
    CheckUncertainReceipt(addJson.Replace("\"kind\":\"add\"", "\"kind\":\"" + invalidKind + "\""),
        "non-mutation kind cannot replay an inventory success: " + invalidKind, command);
}
foreach (var invalidItem in new[] { "", "slime.unknown" })
{
    var command = new BridgeCommand { OpId = "test:op", Kind = "remove", Item = invalidItem, Count = 1, SaveId = "test-save" };
    CheckUncertainReceipt(json.Replace("slime.pink", invalidItem), "self-bound unsupported item is not native proof", command);
}
foreach (var invalidSave in new[] { "", "../test-save" })
{
    var command = new BridgeCommand { OpId = "test:op", Kind = "remove", Item = "slime.pink", Count = 1, SaveId = invalidSave };
    CheckUncertainReceipt(json.Replace("test-save", invalidSave), "self-bound empty or invalid save is not native proof", command);
}
foreach (var count in new[] { 0, 31 })
{
    var command = new BridgeCommand { OpId = "test:op", Kind = "remove", Item = "slime.pink", Count = count, SaveId = "test-save" };
    CheckUncertainReceipt(json.Replace("\"count\":1", "\"count\":" + count).Replace("\"before\":2", "\"before\":" + (count + 1)),
        "self-bound count outside transfer range is not native proof", command);
}
foreach (var invalidFile in new[] { "other-save_2", "../test-save_2", "test-save", "test-save_01", "test-save_2_extra" })
    CheckUncertainReceipt(json.Replace("test-save_2", invalidFile), "native proof filename must belong to the bound save: " + invalidFile);
foreach (var missingProof in new[] { "\"verified\":true,", "\"save_file\":\"test-save_2\",", "\"sha256\":\"" + new string('a', 64) + "\"," })
    CheckUncertainReceipt(json.Replace(missingProof, ""), "missing native proof is uncertain: " + missingProof);
CheckUncertainReceipt(json.Replace(new string('a', 64), new string('A', 64)), "noncanonical digest is not native proof");
Check(CommandResult.ReplayInventory(replayCommand, "SR1", json, _ => throw new IOException("fixture disk read failure")).Uncertain,
    "throwing disk/live proof callback fails closed");
CheckUncertainReceipt(json.Replace("\"uncertain\":false", "\"uncertain\":true"), "a prior uncertain outcome is never upgraded");
CheckUncertainReceipt(json.Replace("\"verified\":true", "\"verified\":\"true\""), "mistyped verification proof stays uncertain");
CheckUncertainReceipt(json.Replace("\"pid\":1234", "\"pid\":1.5"), "fractional PID is not execution provenance");
CheckUncertainReceipt(json.Replace("\"pid\":1234", "\"pid\":\"1234\""), "string PID is not execution provenance");
CheckUncertainReceipt(json.Replace("\"pid\":1234", "\"pid\":1234,\"pid\":4321"), "duplicate PID is not execution provenance");
foreach (var binding in new[] { "\"realm\":\"SR1\",", "\"op_id\":\"test:op\",", "\"kind\":\"remove\",",
                                "\"item\":\"slime.pink\",", "\"count\":1,", "\"save_id\":\"test-save\"," })
    CheckUncertainReceipt(json.Replace(binding, ""), "missing command binding is uncertain: " + binding);
CheckUncertainReceipt(json.Replace("\"count\":1", "\"count\":2"), "count binding rejected before disk/live proof");
var unknownRefusal = refusalJson.Replace("\"before\":2", "\"before\":-1").Replace("\"after\":2", "\"after\":-1");
Check(!CommandResult.ReplayInventory(replayCommand, "SR1", unknownRefusal, _ => throw new Exception("no mutation")).Uncertain,
    "explicit unknown-count pair retains a fully proven preflight refusal");
CheckUncertainReceipt(unknownRefusal.Replace("\"uncertain\":false,", ""), "unknown-count legacy refusal still needs explicit false uncertainty");
var sr2Command = new BridgeCommand { OpId = "test:op", Kind = "remove", Item = "plort.pink", Count = 2, SaveId = "20250619134130_1" };
var sr2Json = json.Replace("SR1", "SR2").Replace("slime.pink", "plort.pink").Replace("test-save", sr2Command.SaveId)
    .Replace("\"count\":1", "\"count\":2").Replace("\"before\":2", "\"before\":3");
Check(CommandResult.ReplayInventory(sr2Command, "SR2", sr2Json, r => r.Before == 3 && r.After == 1 && r.Count == 2 &&
    r.SaveId == sr2Command.SaveId && r.SaveFile == sr2Command.SaveId + "_2" && r.Sha256 == new string('a', 64)).Ok,
    "SR2 multi-item replay passes the exact identity, count and disk proof to recheck");
try
{
    var journal = OpJournal.Open(path, out var error);
    Check(journal != null && error.Length == 0, "new journal opens");
    journal!.WriteIntent("test:op");
    var intentBytes = File.ReadAllBytes(path);
    var repeatedIntentRejected = false;
    try { journal.WriteIntent("test:op"); }
    catch (InvalidDataException) { repeatedIntentRejected = true; }
    Check(repeatedIntentRejected && File.ReadAllBytes(path).SequenceEqual(intentBytes) &&
        journal.HasIntentOnly("test:op") && !journal.TryGetDone("test:op", out _),
        "repeated INTENT write refuses before appending or changing the pending cache");
    journal.WriteDone("test:op", json);
    var originalBytes = File.ReadAllBytes(path);
    journal.WriteDone("test:done-only", json.Replace("test:op", "test:done-only"));
    var completedBytes = File.ReadAllBytes(path);
    var completedIntentRejected = false;
    try { journal.WriteIntent("test:done-only"); }
    catch (InvalidDataException) { completedIntentRejected = true; }
    Check(completedIntentRejected && File.ReadAllBytes(path).SequenceEqual(completedBytes) &&
        !journal.HasIntentOnly("test:done-only") && journal.TryGetDone("test:done-only", out var completedReceipt) &&
        completedReceipt == json.Replace("test:op", "test:done-only"),
        "INTENT write after DONE-only refuses before appending or changing the completed cache");
    originalBytes = completedBytes;
    var conflictRejected = false;
    try { journal.WriteDone("test:op", json.Replace("\"reason\":\"\"", "\"reason\":\"different\"")); }
    catch (InvalidDataException) { conflictRejected = true; }
    Check(conflictRejected && File.ReadAllBytes(path).SequenceEqual(originalBytes) &&
        journal.TryGetDone("test:op", out var originalReceipt) && originalReceipt == json,
        "conflicting write rejects before changing bytes or in-memory DONE");
    journal.WriteDone("test:op", json);
    Check(File.ReadAllBytes(path).SequenceEqual(originalBytes) &&
        journal.TryGetDone("test:op", out var repeatedReceipt) && repeatedReceipt == json,
        "identical DONE write is a byte-preserving idempotent no-op");
    var replayed = CommandResult.ReplayInventory(replayCommand, "SR1", json, _ => true);
    var transportJson = replayed.ToJson(4321);
    Check(transportJson.Contains("\"pid\":4321") && !transportJson.Contains("\"pid\":1234") &&
        journal.TryGetDone("test:op", out var executedJson) && executedJson == json &&
        File.ReadAllBytes(path).SequenceEqual(originalBytes),
        "transport PID changes without rewriting the original execution receipt or journal PID");
    var transportOverwriteRejected = false;
    try { journal.WriteDone("test:op", transportJson); }
    catch (InvalidDataException) { transportOverwriteRejected = true; }
    Check(transportOverwriteRejected && File.ReadAllBytes(path).SequenceEqual(originalBytes) &&
        journal.TryGetDone("test:op", out var stillExecuted) && stillExecuted == json,
        "a current-transport receipt cannot overwrite execution provenance");
    journal = OpJournal.Open(path, out error);
    Check(journal != null && journal.TryGetDone("test:op", out var saved) && saved == json,
        "valid native-result transport journal round trips");
    foreach (var invalidOp in new[] { "", "bad id", "test:injected\nI\ttest:other", new string('x', 129) })
    {
        var rejectedDone = false;
        var rejectedIntent = false;
        try { journal!.WriteDone(invalidOp, json.Replace("test:op", CommandResult.Esc(invalidOp))); }
        catch (InvalidDataException) { rejectedDone = true; }
        try { journal!.WriteIntent(invalidOp); }
        catch (InvalidDataException) { rejectedIntent = true; }
        Check(rejectedDone && rejectedIntent && File.ReadAllBytes(path).SequenceEqual(originalBytes) &&
            !journal!.HasAny(invalidOp), "invalid op id cannot enter either writer: " + invalidOp);
    }
    foreach (var delimiter in new[] { "\n", "\t", ((char)13).ToString() })
    {
        var rejected = false;
        try { journal!.WriteDone("test:multiline", delimiter + json.Replace("test:op", "test:multiline")); }
        catch (InvalidDataException) { rejected = true; }
        Check(rejected && File.ReadAllBytes(path).SequenceEqual(originalBytes) && !journal!.HasAny("test:multiline"),
            "raw journal delimiters are rejected without normalizing the original receipt");
    }
    File.WriteAllText(path, "D\ttest:op\t" + json.Replace("\"count\":1,", "") + "\n");
    Check(OpJournal.Open(path, out error) != null, "old typed mutation receipt opens compatibly");
    File.WriteAllText(path, "I\ttest:pending\n");
    journal = OpJournal.Open(path, out error);
    Check(journal != null && journal.HasIntentOnly("test:pending"), "intact unresolved intent survives");
    CheckRefusedJournal("I\ttest:pending\nI\ttest:pending\n", "identical physical INTENT records refuse");
    foreach (var truncated in new[] { "I\ttest:pending", "D\ttest:op\t" + json,
                                      "I\ttest:pending\nI\ttest:truncated" })
    {
        File.WriteAllText(path, truncated);
        Check(OpJournal.Open(path, out error) == null && error.Length > 0,
            "unterminated final journal record refuses: " + truncated);
    }
    CheckRefusedJournal("D\ttest:op\t" + json + "\nI\ttest:op\n", "physical INTENT after DONE refuses");
    File.WriteAllText(path, "I\ttest:pending" + (char)13 + "\n");
    Check(OpJournal.Open(path, out error)?.HasIntentOnly("test:pending") == true,
        "complete CRLF journal remains compatible");
    // Blank records were already fail-closed; cover each placement without weakening that behavior.
    var intentLine = "I\ttest:op\n";
    var doneLine = "D\ttest:op\t" + json + "\n";
    foreach (var blank in new[] { "\n", "" + (char)13 + "\n", " \n", "\t\n" })
    {
        CheckRefusedJournal(blank, "blank-only physical journal refuses");
        CheckRefusedJournal(blank + intentLine + doneLine, "leading blank record refuses");
        CheckRefusedJournal(intentLine + blank + doneLine, "interior blank record refuses");
        CheckRefusedJournal(intentLine + doneLine + blank, "trailing blank record refuses");
    }
    File.WriteAllText(path, "");
    Check(OpJournal.Open(path, out error) != null && error.Length == 0, "zero-byte journal is not a blank physical record");
    foreach (var record in new[] { "unknown", "", "I\t", "I\tbad id", "I\ttest:op\textra", "D\ttest:op",
                                  "D\ttest:op\t{}", "D\ttest:op\t{bad json}",
                                  "D\tother:op\t" + json, "D\ttest:op\t" + json + " trailing" })
    {
        File.WriteAllText(path, "I\ttest:pending\n" + record + "\n");
        journal = OpJournal.Open(path, out error);
        Check(journal == null && error.Length > 0, "malformed record refuses entire journal: " + record);
    }
    // Escapes, negative counts and every optional field must round trip through the strict reader.
    var rich = new CommandResult { Realm = "SR1", OpId = "test:rich", Kind = "create_save", Before = -1, After = -1,
        Reason = "quote \" backslash \\ tab \t newline-free", CreationId = new string('a', 32),
        SaveFile = "sb1_" + new string('a', 32) + "_0", Sha256 = new string('b', 64), Verified = true, CanCreate = false };
    File.WriteAllText(path, "D\ttest:rich\t" + rich.ToJson(42) + "\n");
    journal = OpJournal.Open(path, out error);
    Check(journal != null && error.Length == 0, "escaped strings, negative ints and optional fields parse: " + error);
    File.WriteAllText(path, "");
    journal = OpJournal.Open(path, out error)!;
    var richJson = rich.ToJson(42);
    journal.WriteDone("test:rich", richJson);
    var creationBytes = File.ReadAllBytes(path);
    journal.WriteDone("test:rich", richJson);
    Check(File.ReadAllBytes(path).SequenceEqual(creationBytes) &&
        OpJournal.Open(path, out error)!.TryGetDone("test:rich", out var richSaved) && richSaved == richJson,
        "creation receipts without inventory count retain writer and idempotent-read compatibility");
    var head = "{\"protocol_version\":1,\"message_type\":\"command_result\",\"realm\":\"SR1\",\"op_id\":\"test:op\",";
    foreach (var bad in new[] {
        head + "\"kind\":\"remove\",\"pid\":1,\"ok\":true,\"applied\":true,\"ok\":false}",        // duplicate key
        head + "\"kind\":\"remove\",\"pid\":1.5,\"ok\":true,\"applied\":true}",                   // non-integer pid
        head + "\"kind\":\"remove\",\"pid\":1,\"ok\":\"true\",\"applied\":true}",                 // string bool
        head + "\"kind\":\"remove\",\"pid\":1,\"ok\":true,\"applied\":true,\"x\":{}}",            // nested value
        head + "\"kind\":\"remove\",\"pid\":1,\"ok\":true,\"applied\":true,\"count\":\"1\"}", // count wrong type
        head + "\"kind\":\"remove\",\"pid\":01,\"ok\":true,\"applied\":true}",                    // leading zero
        head + "\"kind\":\"remove\",\"pid\":0,\"ok\":true,\"applied\":true}",                     // non-positive pid
        head + "\"kind\":\"\",\"pid\":1,\"ok\":true,\"applied\":true}",                           // empty kind
        head.Replace("\"protocol_version\":1", "\"protocol_version\":2") + "\"kind\":\"remove\",\"pid\":1,\"ok\":true,\"applied\":true}",
        head + "\"kind\":\"remove\",\"pid\":1,\"ok\":true,\"applied\":true,}",                    // trailing comma
        head + "\"kind\":\"re\\qmove\",\"pid\":1,\"ok\":true,\"applied\":true}",                  // invalid escape
    })
    {
        File.WriteAllText(path, "");
        journal = OpJournal.Open(path, out error)!;
        var rejected = false;
        try { journal.WriteDone("test:op", bad); }
        catch (InvalidDataException) { rejected = true; }
        Check(rejected && File.ReadAllBytes(path).Length == 0 && !journal.HasAny("test:op"),
            "writer validates payload before appending or caching: " + bad);
        File.WriteAllText(path, "D\ttest:op\t" + bad + "\n");
        Check(OpJournal.Open(path, out error) == null && error.Length > 0, "strict reader refuses: " + bad);
    }
    CheckRefusedJournal("D\ttest:op\t" + json + "\nD\ttest:op\t" + json + "\n", "identical physical DONE records refuse");
    File.WriteAllText(path, "D\ttest:op\t" + json + "\nD\ttest:op\t" + json.Replace("\"after\":1", "\"after\":2") + "\n");
    Check(OpJournal.Open(path, out error) == null, "contradictory DONE records refuse");
    File.WriteAllBytes(path, new byte[] { 0x49, 0x09, 0xff, 0x0a });
    Check(OpJournal.Open(path, out error) == null, "invalid UTF8 refuses");
    foreach (var archived in args)
    {
        var parsed = OpJournal.Open(archived, out error);
        Check(parsed != null && error.Length == 0, "archived native-generated journal parses");
    }
    Console.WriteLine($"PASS: {checks} actual shared C# journal checks (.NET harness, not native game proof)");
}
finally
{
    File.Delete(path);
    Directory.Delete(root);
}
