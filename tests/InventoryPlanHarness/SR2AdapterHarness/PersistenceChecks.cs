// Synthetic full payloads exercise the linked production adapter and real shared disk verifier.
internal static class PersistenceChecks
{
    internal static IEnumerable<(string Name, Action Test)> All()
    {
        yield return ("SaveAllNow false is not persistence proof", () => Bad(w => w.Director.OnSave = () => false));
        yield return ("SaveAllNow true with unchanged names is skipped, not proof", () => Bad(w => w.Director.OnSave = () => true));
        yield return ("ambiguous new revisions cannot prove persistence", () => Bad(w => w.Director.OnSave = () =>
        { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_1", w.Payload()); w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_2", w.Payload()); return true; }));
        yield return ("duplicate revision in entire before set refuses before mutation", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true)); w.Provider.Names.Add(FixtureWorld.Identity + "_0");
            var r = w.Run(w.Command(count: 3));
            Program.Require(!r.Ok && !r.Uncertain && !r.Applied && !w.HasIntent && w.Ammo.Mutations.Count == 0,
                "ambiguous initial revisions reached intent");
        });
        yield return ("duplicate revision in entire after set fails proof", () => Bad(w => w.Director.OnSave = () =>
        { w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_1", w.Payload()); w.Provider.Names.Add(FixtureWorld.Identity + "_0"); return true; }));
        yield return ("reused lower suffix is selected by exact raw difference", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.Provider.Names.Clear(); w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_9", w.Payload());
            w.Director.OnSave = () => { w.Provider.Names.Clear(); w.Provider.AddSyntheticRevision(FixtureWorld.Identity + "_0", w.Payload()); return true; };
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.SaveFile == FixtureWorld.Identity + "_0" &&
                w.Provider.PathRequests.Contains(r.SaveFile), "highest suffix selected instead of new revision");
        });
        yield return ("foreign new revision never substituted for exact bound save", () => Bad(w => w.Director.OnSave = () =>
        { w.Provider.AddSyntheticRevision("different_1", w.Payload()); return true; }));
        yield return ("saved full parsed count mismatch fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 2) }))));
        yield return ("saved GameName mismatch fails proof", () => Bad(w => w.SavePayload(() => w.Payload(name: "different"))));
        yield return ("managed provider bytes must hash-match exact physical file", () => Bad(w => w.SavePayload(() => w.Payload(), () => new byte[] { 1, 2, 3 })));
        yield return ("provider exact disk basename mismatch fails proof", () => Bad(w =>
        { w.Provider.PathOverride = Path.Combine(w.Root, "wrong.sav"); w.SavePayload(() => w.Payload()); }));
        yield return ("full detached load validates payload terminator", () => Bad(w => w.SavePayload(() => w.Payload(end: false))));
        yield return ("full detached load rejects trailing bytes at EOF", () => Bad(w => w.SavePayload(() => w.Payload(trailing: true))));
        yield return ("null saved Player fails proof", () => Bad(w => w.SavePayload(() => w.Payload(player: false))));
        yield return ("null saved ammo collection fails proof", () => Bad(w => w.SavePayload(() => w.Payload(ammo: false))));
        yield return ("null saved ammo entry fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { null }))));
        yield return ("negative saved count fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, -1) }))));
        yield return ("null saved index structure fails proof", () => Bad(w => w.SavePayload(() => w.Payload(table: false))));
        yield return ("null saved index table fails proof", () => Bad(w => w.SavePayload(() => w.Payload(indexTable: false))));
        yield return ("unresolved positive saved persistence id fails proof", () => Bad(w => w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (1234, 3) }))));
        yield return ("unresolved positive non-target saved entry fails proof", () => Bad(w =>
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (1234, 1) }))));
        yield return ("unresolved positive locked saved entry also fails proof", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true), (9, 20, true, false));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3), (1234, 9) })); AssertBad(w);
        });
        yield return ("missing save entry for unlocked live slot fails proof", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true), (0, 10, true, true));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (0, 3) })); AssertBad(w);
        });
        yield return ("persistence mapping comes from saved index table and live lookup cast", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            w.SavePayload(() => w.Payload(entries: new (int, int)?[] { (1, 3) }, index: new[] { w.Other.ReferenceId, w.Pink.ReferenceId }));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && FixtureTrace.Loads == 1 && FixtureTrace.LookupCasts > 0 &&
                FixtureTrace.SkipPayloadEnd == false && FixtureTrace.Disposals == 2, "detached complete native API route not used");
        });
        yield return ("native read stream EOF is copied into fresh zero-position payload", () =>
        {
            using var w = new FixtureWorld((0, 10, true, true));
            var r = w.Run(w.Command(count: 3));
            Program.Require(r.Ok && r.Verified == true && r.Sha256.Length == 64 && w.Provider.PayloadReads == 1,
                "did not parse provider bytes independently of its stream position");
        });
        yield return ("loaded identity change during save invalidates proof", () => Bad(w =>
        {
            var save = w.Director.OnSave!; w.Director.OnSave = () => { var ok = save(); w.Director._currentGameName = "different"; return ok; };
        }));
        yield return ("loaded saved-game identity change during readback invalidates proof", () => Bad(w =>
            w.Provider.OnRead = () => w.Director._savedGame!.Name = "different"));
        yield return ("live count change during readback invalidates proof", () => Bad(w =>
            w.Provider.OnRead = () => w.Ammo.Slots[0]!.Count++));
        yield return ("player disappears during proof invalidates loaded-world recheck", () => Bad(w =>
            w.Provider.OnRead = () => w.Scene.Player = null));
    }
    private static void Bad(Action<FixtureWorld> change)
    {
        using var w = new FixtureWorld((0, 10, true, true)); change(w); AssertBad(w);
    }
    private static void AssertBad(FixtureWorld w)
    {
        var r = w.Run(w.Command(count: 3));
        Program.Require(!r.Ok && r.Uncertain && r.Applied && !r.Persisted && r.Verified != true && r.Count == 3 &&
            w.Ammo.Mutations.Count == 1 && w.Director.SaveCalls == 1, "save boolean/header became persistence proof");
    }
}
