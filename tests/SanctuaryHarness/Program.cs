using SlimeBridge.Shared;

// Synthetic-only checks of the sanctuary layout reader and the progress rules. Never in-game proof.
internal static class Program
{
    private static int _checks, _failures;

    private static void Check(bool ok, string label)
    {
        _checks++;
        if (!ok) _failures++;
        Console.WriteLine((ok ? "PASS " : "FAIL ") + label);
    }

    private static bool Throws<T>(Action action) where T : Exception
    {
        try { action(); return false; }
        catch (T) { return true; }
    }

    private static int Main(string[] args)
    {
        if (args.Length != 1) { Console.WriteLine("usage: SanctuaryHarness <layout.json>"); return 2; }
        var json = File.ReadAllText(args[0]);
        Layout(json);
        Progress();
        Travel();
        Console.WriteLine($"sanctuary checks={_checks} failures={_failures}");
        return _failures == 0 ? 0 : 1;
    }

    private sealed class FakeTransport : ITravelTransport
    {
        public TravelMessage? Head;
        public readonly List<(string Tid, string Event, string Ack, string Detail, Dictionary<string, int>? Carry)> Reports = new();
        public double LastContact { get; set; }
        public TravelMessage? Next(double now) => Head;
        public void Report(string transitionId, string eventName, string ackId, string detail, Dictionary<string, int>? carry)
        {
            Reports.Add((transitionId, eventName, ackId, detail, carry));
            if (Head != null && Head.MessageId == ackId) Head = null;
        }
    }

    private sealed class FakeRealm : ITravelRealm
    {
        public readonly List<string> Calls = new();
        public TravelDone? Pending;
        public Dictionary<string, int>? Carry;
        public bool Immediate = true, Ok = true, ThrowOnExecute;
        public bool Held { get; set; }
        public void Execute(TravelMessage message, TravelDone done)
        {
            Calls.Add(message.Kind);
            if (ThrowOnExecute) throw new InvalidOperationException("boom");
            if (message.Kind == "hold") Held = true;
            if (message.Kind == "resume" || message.Kind == "arrive") Held = false;
            if (Immediate) done(Ok, Ok ? "" : "nope", Carry); else Pending = done;
        }
        public void Cancel() => Calls.Add("cancel");
    }

    private static TravelMessage Msg(string kind, string id, string tid = "t1") => new TravelMessage { Kind = kind, MessageId = id, TransitionId = tid };

    private static void Travel()
    {
        var parsed = TravelMessage.Parse("{\"protocol_version\":1,\"message\":{\"kind\":\"hold\",\"transition_id\":\"t9\",\"message_id\":\"m9\",\"policy\":\"FREEZE\"}}");
        Check(parsed != null && parsed.Kind == "hold" && parsed.Policy == "FREEZE" && parsed.TransitionId == "t9", "mailbox answer parses");
        Check(TravelMessage.Parse("{\"protocol_version\":1,\"message\":null}") == null, "an empty mailbox parses to nothing");
        Check(TravelMessage.Parse("{\"message\":{\"kind\":\"hold\"}}") == null, "an instruction without ids is ignored");

        var t = new FakeTransport(); var r = new FakeRealm(); var logs = new List<string>();
        var agent = new TravelAgent(t, r, logs.Add);
        agent.Tick(1);
        Check(r.Calls.Count == 0 && t.Reports.Count == 0, "idle without instructions");
        t.Head = Msg("prepare_departure", "m1");
        agent.Tick(2);
        Check(r.Calls.SequenceEqual(new[] { "prepare_departure" }), "instruction executed once");
        agent.Tick(3);
        Check(t.Reports.Count == 1 && t.Reports[0].Event == "departure_ready" && t.Reports[0].Ack == "m1", "completion reported with the acknowledgement");
        t.Head = Msg("prepare_departure", "m1");
        agent.Tick(3.5);
        Check(r.Calls.Count == 1 && t.Reports.Count == 1, "a stale copy right after the ack is not re-executed or re-reported yet");
        agent.Tick(6);
        Check(r.Calls.Count == 1 && t.Reports.Count == 2 && t.Reports[1].Event == "departure_ready", "a redelivered instruction re-sends its event, never re-executes");

        r.Immediate = false; t.Head = Msg("hold", "m2");
        agent.Tick(10); agent.Tick(10.1);
        Check(r.Calls.Count(c => c == "hold") == 1 && t.Reports.Count == 2, "a slow instruction is executed once and reports when done");
        r.Pending!(true, "frozen", null); agent.Tick(11);
        Check(t.Reports.Last().Event == "held" && t.Reports.Last().Detail == "frozen", "late completion reported next tick");

        r.Immediate = false; t.Head = Msg("prepare_departure", "m3", "t2");
        agent.Tick(20);
        t.Head = Msg("resume", "m4", "t2");
        agent.Tick(21);
        Check(r.Calls.TakeLast(2).SequenceEqual(new[] { "cancel", "resume" }), "a newer instruction cancels the one in progress");
        r.Pending!(true, "", null); agent.Tick(22);
        Check(t.Reports.Last().Event == "resumed" && t.Reports.Last().Ack == "m4", "the replacement reports; the cancelled one never does");

        r.Immediate = true; r.Ok = false; t.Head = Msg("arrive", "m5");
        agent.Tick(30); agent.Tick(31);
        Check(t.Reports.Last().Event == "failed" && t.Reports.Last().Detail == "nope", "a failed instruction reports failed");
        r.Ok = true; r.ThrowOnExecute = true; t.Head = Msg("arrive", "m6");
        agent.Tick(40); agent.Tick(41);
        Check(t.Reports.Last().Event == "failed" && t.Reports.Last().Detail == "InvalidOperationException", "an exception becomes a failed report");

        // items that travel: reported with the completion, re-sent with a redelivered instruction
        r.Immediate = true; r.Ok = true; r.ThrowOnExecute = false; r.Carry = new Dictionary<string, int> { ["CARROT_VEGGIE"] = 3 };
        t.Head = Msg("arrive", "m7");
        agent.Tick(50); agent.Tick(51);
        Check(t.Reports.Last().Event == "arrived" && t.Reports.Last().Carry != null && t.Reports.Last().Carry!["CARROT_VEGGIE"] == 3, "accepted items are reported with the event");
        var withCarry = TravelMessage.Parse("{\"protocol_version\":1,\"message\":{\"kind\":\"arrive\",\"transition_id\":\"t9\",\"message_id\":\"m9\",\"carry_mode\":\"ALL\",\"carry\":{\"CARROT_VEGGIE\":3,\"IdentifiableType.PinkPlort\":2,\"BAD\":0}}}");
        Check(withCarry != null && withCarry.Carry.Count == 2 && withCarry.Carry["CARROT_VEGGIE"] == 3 && withCarry.CarryMode == "ALL", "carried items parse (zero counts dropped)");
        Check(CarryNames.Canonical("CARROT_VEGGIE") == CarryNames.Canonical("IdentifiableType.Carrot") && CarryNames.Canonical("PINK_PLORT") == CarryNames.Canonical("IdentifiableType.PinkPlort")
              && CarryNames.Canonical("PINK_SLIME") == CarryNames.Canonical("SlimeDefinition.Pink") && CarryNames.Canonical("PEAR_FRUIT") == "pear", "the two games' names meet on one canonical key");
        Check(CarryNames.Allowed("ALL", "HEN") && !CarryNames.Allowed("NONE", "PINK_PLORT") && CarryNames.Allowed("SLIMES_PLORTS", "PINK_PLORT") && CarryNames.Allowed("SLIMES_PLORTS", "IdentifiableType.PinkPlort")
              && CarryNames.Allowed("SLIMES_PLORTS", "SlimeDefinition.Pink") && !CarryNames.Allowed("SLIMES_PLORTS", "CARROT_VEGGIE") && !CarryNames.Allowed("ALL", ""), "carry modes filter what may travel");

        var t2 = new FakeTransport(); var r2 = new FakeRealm { Held = true };
        var agent2 = new TravelAgent(t2, r2, logs.Add);
        t2.LastContact = 100;
        agent2.Tick(100 + TravelAgent.HeldWithoutHostSeconds - 1);
        Check(r2.Calls.Count == 0, "a held game waits while BridgeHost was heard recently");
        agent2.Tick(100 + TravelAgent.HeldWithoutHostSeconds + 1);
        Check(r2.Calls.SequenceEqual(new[] { "resume" }) && t2.Reports.Count == 0, "a held game resumes on its own when BridgeHost stays silent, without reporting");
        Check(TravelAgent.EventFor("prepare_departure") == "departure_ready" && TravelAgent.EventFor("hold") == "held" && TravelAgent.EventFor("resume") == "resumed"
              && TravelAgent.EventFor("arrive") == "arrived" && TravelAgent.EventFor("abort") == "aborted" && TravelAgent.EventFor("zzz") == "failed", "event names");
    }

    private static void Layout(string json)
    {
        var layout = SanctuaryLayout.Parse(json);
        Check(layout.Pieces.Count > 0 && layout.Statues.Count > 0, "layout parses with pieces and statues");
        Check(layout.RootName == "SlimeBridge_SR1_WorldRoot", "dedicated world root name");
Check(layout.Pieces.Any(x => x.Phase == "rise") && layout.Pieces.Any(x => x.Phase == "flora") && layout.Pieces.All(x => x.Phase.Length > 0), "pieces carry a reveal phase");
Check(layout.Pieces.Where(x => x.Group == "flora").All(x => !x.CastShadows), "flora does not cast shadows");
Check(layout.Gate.Tablet != null && layout.Gate.Tablet.Title == "H" && layout.Gate.Tablet.Lines.Length >= 3 && layout.Gate.Tablet.Signature.EndsWith("H"), "the H tablet parses");
Check(layout.Gate.Figure != null && layout.Gate.Figure.Kind == "native_slime" && layout.Gate.Figure.Slime == "GOLD", "the gold figure parses");
        Check(layout.GoldCost == 10 && layout.AccessMode == "ENDGAME", "default gate settings");
        Check(layout.Gate.Plort == "GOLD_PLORT" && layout.Gate.Enabled, "gold plort gate enabled in ENDGAME mode");
        Check(layout.Gate.Statue.PlortId == layout.Gate.PlortId && layout.Gate.FrameMesh.Length > 0 && layout.Gate.DoorMaterials.Length > 0, "gate depositor statue and barrier pieces are described");
        Check(layout.Portal.RingCount >= 3 && layout.Portal.RingRadius > 1 && layout.Portal.MembraneMaterial.Length > 0, "portal ring and membrane are described");
        var required = layout.Statues.Count(s => s.Required);
        Check(required == layout.RequiredSlimes.Count, "requirement manifest equals the required statues");
        Check(layout.Statues.Any(s => s.Hidden && s.Placement == "alcove"), "hidden statues sit in alcoves");
        Check(layout.Statues.All(s => s.Template.Nodes.Any(n => n.Name == "Statue") && s.Template.Nodes.Any(n => n.Name == "Eyes")), "every statue template has Statue and Eyes parts");
        Check(layout.Statues.Where(s => s.Status == "synthesized").All(s => s.TintTop != null || s.LightColor != null), "synthesized statues carry colours");
        Check(layout.Statues.Select(s => s.PlortId).Distinct().Count() == layout.Statues.Count, "one plort id per statue");
        Check(layout.Pieces.All(p => p.Materials.Length > 0 && p.Scale.All(v => v > 0)), "pieces have materials and positive scale");
        Check(layout.Shots.Count >= 4, "camera shots are present");
        var pink = layout.FindStatue("PINK");
        Check(pink != null && pink.Template.Nodes.Any(n => n.Components.Any(c => c.Type == "SphereCollider" && c.Trigger)), "statue trigger sphere is described");

        string Replace(string from, string to) => json.Replace(from, to);
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse(Replace("\"bridge-sanctuary-layout\"", "\"other\""))), "wrong schema refused");
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse(Replace("\"sr1_gold_plort_gate_cost\": 10", "\"sr1_gold_plort_gate_cost\": 0"))), "zero gold cost refused");
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse(Replace("\"ENDGAME\"", "\"SOMETIMES\""))), "unknown access mode refused");
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse(Replace("\"collider\": \"mesh\"", "\"collider\": \"sphere\""))), "unknown collider kind refused");
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse("{}")), "empty document refused");
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse(Replace("\"realm\": \"SR1\"", "\"realm\": \"SR2\""))), "SR2 layout refused");
        Check(Throws<FormatException>(() => SanctuaryLayout.Parse(json.Substring(0, json.Length / 2))), "truncated document refused");
    }

    private static void Progress()
    {
        var p = new SanctuaryProgress { SaveKey = "sb1_abc" };
        p.EnsureManifest(new[] { "ROCK", "PINK", "BOOM" }, "kit1", false);
        Check(p.RequiredManifest.SequenceEqual(new[] { "BOOM", "PINK", "ROCK" }), "manifest is sorted");
        p.EnsureManifest(new[] { "PINK" }, "kit2", false);
        Check(p.RequiredManifest.Count == 3 && p.KitSha256 == "kit1", "manifest stays frozen when not dynamic");
        p.EnsureManifest(new[] { "PINK", "ROCK" }, "kit3", true);
        Check(p.RequiredManifest.Count == 2 && p.KitSha256 == "kit3", "dynamic mode refreshes the manifest");
        p.EnsureManifest(new[] { "ROCK", "PINK", "BOOM" }, "kit1", true);

        Check(!p.DepositGold(0, 10) && p.GoldDeposited == 0, "zero deposit ignored");
        Check(!p.DepositGold(4, 10) && !p.GatePaid && p.GoldDeposited == 4, "partial deposit keeps the gate closed");
        Check(p.DepositGold(7, 10) && p.GatePaid && p.GoldDeposited == 10, "overpay completes and clamps at the cost");
        Check(!p.DepositGold(1, 10) && p.GoldDeposited == 10, "no deposits after payment");

        Check(!p.TryUnlockPortal(), "portal stays locked with no statues");
        Check(p.FillStatue("PINK", true) && !p.FillStatue("PINK", true), "a statue fills once");
        Check(!p.FillStatue("GLITCH", true), "a slime outside the frozen manifest cannot count");
        Check(p.FillStatue("GOLD", false), "optional statues can be filled without counting");
        Check(!p.TryUnlockPortal() && p.FilledRequiredCount() == 1, "portal still locked with one of three");
        p.FillStatue("ROCK", true); p.FillStatue("BOOM", true);
        Check(p.AllRequiredFilled() && p.TryUnlockPortal() && p.PortalUnlocked, "portal unlocks when the last required statue fills");
        Check(!p.TryUnlockPortal(), "unlock is reported once");

        var round = SanctuaryProgress.FromJson(p.ToJson());
        Check(round.SaveKey == "sb1_abc" && round.GatePaid && round.PortalUnlocked && round.FilledStatues.Count == 4 && round.RequiredManifest.Count == 3, "json round trip");
        Check(Throws<FormatException>(() => SanctuaryProgress.FromJson(p.ToJson().Replace("\"filled\":[\"BOOM\",\"GOLD\",\"PINK\",\"ROCK\"]", "\"filled\":[]"))), "unlocked portal without statues is refused");
        Check(Throws<FormatException>(() => SanctuaryProgress.FromJson("{\"schema\":\"x\"}")), "foreign document refused");

        var dir = Path.Combine(Path.GetTempPath(), "sanctuary-harness-" + Guid.NewGuid().ToString("N"));
        try
        {
            p.Save(dir);
            var loaded = SanctuaryProgress.LoadOrCreate(dir, "sb1_abc", out var problem);
            Check(problem == null && loaded.PortalUnlocked && loaded.GoldDeposited == 10, "save then load restores everything");
            Check(Directory.GetFiles(dir, "*.tmp").Length == 0, "no temp files left behind");
            p.GoldDeposited = 3; p.Save(dir);
            Check(SanctuaryProgress.LoadOrCreate(dir, "sb1_abc", out _).GoldDeposited == 3, "second save replaces the first");

            File.WriteAllText(SanctuaryProgress.PathFor(dir, "sb1_abc"), "{not json");
            var fresh = SanctuaryProgress.LoadOrCreate(dir, "sb1_abc", out var why);
            Check(why != null && !fresh.GatePaid && fresh.SaveKey == "sb1_abc", "corrupt file yields fresh progress and a reason");
            Check(Directory.GetFiles(dir, "*.corrupt-*").Length == 1, "corrupt file is kept aside, never deleted");

            p.Save(dir);
            var other = SanctuaryProgress.LoadOrCreate(dir, "sb1_other", out _);
            Check(!other.GatePaid && other.SaveKey == "sb1_other", "another save never sees this progress");
            var tampered = File.ReadAllText(SanctuaryProgress.PathFor(dir, "sb1_abc")).Replace("sb1_abc", "sb1_zzz");
            File.WriteAllText(SanctuaryProgress.PathFor(dir, "sb1_abc"), tampered);
            var mismatch = SanctuaryProgress.LoadOrCreate(dir, "sb1_abc", out var mismatchWhy);
            Check(mismatchWhy != null && !mismatch.GatePaid, "progress of another save under this name is moved aside");
        }
        finally
        {
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
        }

        Check(SanctuaryProgress.SanitizeSaveKey("../..\\evil name") == "evilname", "save key cannot escape the folder");
        Check(SanctuaryProgress.SanitizeSaveKey(null) == "unknown" && SanctuaryProgress.SanitizeSaveKey("   ") == "unknown", "empty save key becomes unknown");

        var dev = new SanctuaryProgress { SaveKey = "k" };
        dev.EnsureManifest(new[] { "A", "B" }, "k", false);
        dev.DevUnlockAll();
        Check(dev.GatePaid && dev.PortalUnlocked && dev.FilledStatues.Count == 2, "dev unlock-all opens everything");
        dev.DevReset();
        Check(!dev.GatePaid && !dev.PortalUnlocked && dev.FilledStatues.Count == 0 && dev.RequiredManifest.Count == 2, "dev reset keeps the manifest");
    }
}
