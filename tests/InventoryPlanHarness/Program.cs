using SlimeBridge.Shared;

int checks = 0;
void Check(bool yes, string why) { checks++; if (!yes) throw new Exception(why); }
SlotSnapshot S(int count, int max, bool match = false, bool empty = false, bool usable = true, bool accepts = true)
    => new SlotSnapshot(usable, match, empty, accepts, count, max);

var remove = InventoryRules.PlanRemove(new[] { S(1, 30, match: true), S(1, 30, match: true) }, 2);
Check(remove != null && remove.Count == 2 && remove[0].Amount == 1 && remove[1].Amount == 1, "two one-item slots remove two");
Check(InventoryRules.PlanRemove(new[] { S(1, 30, match: true), S(20, 30, match: true, usable: false) }, 2) == null, "locked stock excluded");
Check(InventoryRules.PlanRemove(new[] { S(1, 30, match: true) }, 2) == null, "insufficient remove refuses before intent");
var add = InventoryRules.PlanAdd(new[] { S(30, 30, match: true), S(0, 30, empty: true) }, 2);
Check(add != null && add.Count == 1 && add[0].Index == 1 && add[0].Amount == 2, "full first stack uses free later slot");
add = InventoryRules.PlanAdd(new[] { S(29, 30, match: true), S(0, 30, empty: true) }, 2);
Check(add != null && add.Count == 2 && add[0].Amount == 1 && add[1].Amount == 1, "spread add two");
Check(InventoryRules.PlanAdd(new[] { S(29, 30, match: true), S(0, 30, empty: true, usable: false) }, 2) == null, "locked capacity excluded");
Check(InventoryRules.PlanAdd(new[] { S(30, 30, match: true), S(0, 30, empty: true, accepts: false) }, 1) == null, "native predicate blocks empty slot");
Check(InventoryRules.PlanAdd(new[] { S(29, 30, match: true) }, 2) == null, "full request refuses before intent");

var fakeSlots = new[] { 1, 1 };
var applied = InventoryRules.Apply(remove!, i => fakeSlots[i], (i, n) => { fakeSlots[i] -= n; return true; }, false);
Check(applied && fakeSlots[0] == 0 && fakeSlots[1] == 0, "planned remove callbacks complete");
fakeSlots = new[] { 1, 1 };
applied = InventoryRules.Apply(remove!, i => fakeSlots[i], (i, n) => { fakeSlots[i] -= n - 1; return true; }, false);
Check(!applied && fakeSlots[1] == 1, "partial callback stops before next slot");
fakeSlots = new[] { 1, 1 };
applied = InventoryRules.Apply(remove!, i => fakeSlots[i], (i, n) => throw new IOException("fixture"), false);
Check(!applied && fakeSlots[1] == 1, "throwing callback stops");

var bytes = new byte[] { 1, 2, 3 }; // synthetic fixture, not native evidence
var p = InventoryRules.VerifyPersistence(() => true, () => new SaveReadback("save_2", "save", 3, bytes), "save", 3);
Check(!p.Verified, "detached bytes without exact local disk proof cannot verify persistence");
Check(!InventoryRules.VerifyPersistence(() => false, () => new SaveReadback("save_2", "save", 3, bytes), "save", 3).Verified, "save false");
Check(!InventoryRules.VerifyPersistence(() => throw new IOException("fixture"), () => new SaveReadback("save_2", "save", 3, bytes), "save", 3).Verified, "save throws");
Check(!InventoryRules.VerifyPersistence(() => true, () => new SaveReadback("save_2", "save", 2, bytes), "save", 3).Verified, "mismatched count");
Check(!InventoryRules.VerifyPersistence(() => true, () => new SaveReadback("save_2", "other", 3, bytes), "save", 3).Verified, "mismatched identity");
Check(!InventoryRules.VerifyPersistence(() => true, () => throw new InvalidDataException("corrupt fixture"), "save", 3).Verified, "corrupt payload");
Check(InventoryRules.NewestRevision(new[] { "save_1", "save_2", "save_2_extra_9" }, "save") == null, "suffix order is not native save order; multiple revisions are ambiguous");
Check(InventoryRules.NewestRevision(new[] { "save_2", "save_2" }, "save") == null, "ambiguous revision refuses");
// Invoke the intended API through reflection for the RED missing-feature check.
string? Select(string[] before, string[] after) =>
    (string?)typeof(InventoryRules).GetMethod("SelectNewRevision")?.Invoke(null, new object[] { before, after, "save" });
Check(Select(new[] { "save_1", "save_9" }, new[] { "save_1", "save_9", "save_0" }) == "save_0", "first-free reused lower suffix is the exact new revision");
// SR1 production multi-slot pattern (group with a freed slot): the save reuses the first free suffix.
Check(Select(new[] { "save_0", "save_1", "save_3", "save_4", "save_5" }, new[] { "save_0", "save_1", "save_2", "save_3", "save_4", "save_5" }) == "save_2", "production multi-slot group: first free suffix between traversals is the new revision");
Check(Select(new[] { "save_1", "save_9" }, new[] { "save_9", "save_0" }) == "save_0", "pruning an old revision does not change new identity");
Check(Select(new[] { "save_1" }, new[] { "save_1" }) == null, "unchanged revision set cannot prove a skipped save");
Check(Select(new[] { "save_1" }, new[] { "save_1", "save_0", "save_2" }) == null, "two new revisions are ambiguous");
Check(Select(new[] { "save_1", "save_1" }, new[] { "save_0" }) == null, "duplicate before revision refuses");
Check(Select(new[] { "save_1" }, new[] { "save_1", "save_0", "save_0" }) == null, "duplicate after revision refuses");
Check(Select(new[] { "save_1" }, new[] { "save_1", "other_0", "save_more_2" }) == null, "other save groups do not count as new revisions");
var scratch = Environment.GetEnvironmentVariable("TMPDIR") ?? throw new Exception("TMPDIR required");
var fixtureRoot = Path.Combine(scratch, "inventory-disk-fixture-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(fixtureRoot);
var fixturePath = Path.Combine(fixtureRoot, "save_2.sav");
try
{
    File.WriteAllBytes(fixturePath, new byte[] { 9, 9, 9 });
    p = InventoryRules.VerifyPersistence(() => true,
        () => new SaveReadback("save_2", "save", 3, bytes, fixturePath), "save", 3);
    Check(!p.Verified, "provider bytes must match exact physical native file");
    File.WriteAllBytes(fixturePath, bytes);
    p = InventoryRules.VerifyPersistence(() => true,
        () => new SaveReadback("save_2", "save", 3, bytes, fixturePath), "save", 3);
    Check(p.Verified && p.SaveFile == "save_2" && p.Sha256 == "039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81", "exact synthetic fixture matches real OS-flushed disk bytes");
    Check(File.ReadAllBytes(fixturePath).SequenceEqual(bytes), "verification never modifies file bytes");
    Check(!InventoryRules.VerifyPersistence(() => true,
        () => new SaveReadback("save_3", "save", 3, bytes, fixturePath), "save", 3).Verified, "provider path basename must match raw revision");
    Check(!InventoryRules.VerifyPersistence(() => true,
        () => new SaveReadback("save_2", "save", 3, bytes, fixturePath + ".missing"), "save", 3).Verified, "absent disk path cannot verify");
    using (var locked = new FileStream(fixturePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        Check(!InventoryRules.VerifyPersistence(() => true,
            () => new SaveReadback("save_2", "save", 3, bytes, fixturePath), "save", 3).Verified, "exclusive-writer/flush handle failure refuses proof");
    var unrelatedPath = Path.Combine(fixtureRoot, "other_0.sav");
    File.WriteAllBytes(unrelatedPath, bytes);
    try
    {
        Check(!InventoryRules.VerifyPersistence(() => true,
            () => new SaveReadback("other_0", "save", 3, bytes, unrelatedPath), "save", 3).Verified, "revision must belong to exact identity");
    }
    finally { File.Delete(unrelatedPath); }
}
finally { File.Delete(fixturePath); Directory.Delete(fixtureRoot); }
Console.WriteLine($"PASS: {checks} pure inventory fixture checks (not native proof)");
