// These tests execute production InventoryAgent with SYNTHETIC native API fixtures.
using SlimeBridge.Shared;

internal static class MutationChecks
{
    internal static IEnumerable<(string Name, Action Test)> All()
    {
        yield return ("multi-slot add preplans matching stacks then empty native slots", () =>
        {
            using var w = new FixtureWorld((8, 10, true, true), (2, 4, true, true), (0, 8, true, true));
            var r = w.Run(w.Command(count: 10));
            Program.Require(r.Ok && r.Applied && r.Persisted && r.Verified == true && r.Count == 10 && r.Before == 10 && r.After == 20,
                "multi-slot add was not verified");
            Program.Require(w.Ammo.Mutations.SequenceEqual(new[] { (0, 2), (1, 2), (2, 6) }), "incorrect native add plan");
            Program.Require(new[] { 0, 1, 2 }.All(w.Ammo.AcceptanceChecks.Contains) && new[] { 0, 1, 2 }.All(w.Ammo.CapacityChecks.Contains),
                "per-index native acceptance/capacity APIs not used");
        });
        yield return ("multi-slot remove uses native decrement across every matching stack", () =>
        {
            using var w = new FixtureWorld((2, 10, true, true), (3, 10, true, true), (7, 10, true, true));
            var r = w.Run(w.Command("remove", 9));
            Program.Require(r.Ok && r.Applied && r.Persisted && r.Verified == true && r.Before == 12 && r.After == 3,
                "multi-slot removal was not verified");
            Program.Require(w.Ammo.Mutations.SequenceEqual(new[] { (0, 2), (1, 3), (2, 4) }), "incorrect native removal plan");
        });
        yield return ("insufficient total add capacity refuses before intent", () =>
        {
            using var w = new FixtureWorld((9, 10, true, true), (0, 2, true, true), (0, 100, true, false), (8, 100, false, true));
            CleanRefusal(w, w.Command(count: 4));
        });
        yield return ("insufficient total remove count refuses before intent", () =>
        {
            using var w = new FixtureWorld((2, 10, true, true), (9, 10, true, false), (8, 10, false, true));
            CleanRefusal(w, w.Command("remove", 3));
        });
        yield return ("native per-slot refusal prevents impossible planned add", () =>
        {
            using var w = new FixtureWorld((0, 20, true, true)); w.Ammo.Slots[0]!.Accepts = false;
            CleanRefusal(w, w.Command(count: 2));
        });
        yield return ("locked slot counts are excluded from live and save proof", () =>
        {
            using var w = new FixtureWorld((1, 10, true, true), (20, 30, true, false));
            var r = w.Run(w.Command(count: 2));
            Program.Require(r.Ok && r.Verified == true && r.Before == 1 && r.After == 3, "locked inventory counted");
        });
        yield return ("nonlocal provider refuses before mutation", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Director.StorageProvider = new Il2Cpp.StorageProvider();
            CleanRefusal(w, w.Command(count: 2));
        });
        yield return ("uninitialized provider refuses before mutation", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Provider.Initialized = false;
            CleanRefusal(w, w.Command(count: 2));
        });
        yield return ("journal absent refuses before mutation", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Agent = Program.Create(null);
            CleanRefusal(w, w.Command(count: 2));
        });
        yield return ("query echoes zero count and never persists", () =>
        {
            using var w = new FixtureWorld((3, 10, true, true), (7, 10, true, true));
            var r = w.Run(w.Command("query", 0));
            Program.Require(r.Ok && !r.Applied && !r.Persisted && r.Count == 0 && r.Before == 10 && r.After == 10 &&
                w.Ammo.Mutations.Count == 0 && w.Director.SaveCalls == 0 && w.JournalBytes().Length == 0, "query mutated or lost count");
        });
        yield return ("intent-only replay does not complete or reapply", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); var c = w.Command(count: 3);
            w.Journal.WriteIntent(c.OpId); var bytes = w.JournalBytes();
            var r = w.Run(c);
            Program.Require(!r.Ok && r.Uncertain && !r.Applied && r.Count == 3 && w.Ammo.Mutations.Count == 0 &&
                w.Director.SaveCalls == 0 && bytes.SequenceEqual(w.JournalBytes()), "intent-only replay appended or mutated");
        });
        yield return ("false native callback after intent is uncertain with unchanged Applied", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Ammo.RefuseMutationAt = 0;
            Uncertain(w, 3, false, 0);
        });
        yield return ("native throw before mutation after intent remains uncertain", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Ammo.ThrowBeforeMutationAt = 0;
            Uncertain(w, 3, false, 0);
        });
        yield return ("native throw after complete actual delta retains Applied", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Ammo.ThrowAfterMutationAt = 0;
            Uncertain(w, 3, true, 3);
        });
        yield return ("partial multi-slot refusal retains observed count but not Applied", () =>
        {
            using var w = new FixtureWorld((8, 10, true, true), (0, 10, true, true)); w.Ammo.RefuseMutationAt = 1;
            Uncertain(w, 4, false, 10);
        });
        yield return ("per-slot unexpected delta is uncertain", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Ammo.WrongSlotDelta = true;
            Uncertain(w, 3, false, 4);
        });
        yield return ("aggregate unexpected delta is uncertain", () =>
        {
            using var w = new FixtureWorld((1, 10, true, true), (1, 10, true, true)); w.Ammo.ChangeOtherSlot = true;
            Uncertain(w, 3, false, 6);
        });
        yield return ("negative live count fails before intent", () =>
        {
            using var w = new FixtureWorld((-1, 10, true, true)); CleanRefusal(w, w.Command(count: 3));
        });
        yield return ("positive live count without identifiable type fails before intent", () =>
        {
            using var w = new FixtureWorld((2, 10, true, true)); w.Ammo.Slots[0]!.Id = null; CleanRefusal(w, w.Command(count: 3));
        });
        yield return ("final journal write failure cannot report verified success", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            var save = w.Director.OnSave!;
            w.Director.OnSave = () => { var saved = save(); File.SetAttributes(w.JournalPath, FileAttributes.ReadOnly); return saved; };
            try
            {
                var r = w.Run(w.Command(count: 3));
                Program.Require(!r.Ok && r.Uncertain && r.Applied && r.Reason.Contains("journal", StringComparison.OrdinalIgnoreCase),
                    "final journal failure became ok success");
                Program.Require(w.Journal.HasIntentOnly("fixture-op"), "failed final journal did not preserve intent-only");
            }
            finally { File.SetAttributes(w.JournalPath, FileAttributes.Normal); }
        });
    }
    private static void CleanRefusal(FixtureWorld w, BridgeCommand c)
    {
        var r = w.Run(c);
        Program.Require(!r.Ok && !r.Applied && !r.Uncertain && r.Count == c.Count && w.Ammo.Mutations.Count == 0 &&
            w.Director.SaveCalls == 0 && !w.HasIntent, "unsafe preflight reached intent/native mutation");
    }
    private static void Uncertain(FixtureWorld w, int count, bool applied, int after)
    {
        var r = w.Run(w.Command(count: count));
        Program.Require(!r.Ok && r.Uncertain && r.Applied == applied && !r.Persisted && r.Verified != true && r.After == after &&
            r.Count == count && w.Director.SaveCalls == 0, "post-intent outcome flags/count incorrect");
    }
}
