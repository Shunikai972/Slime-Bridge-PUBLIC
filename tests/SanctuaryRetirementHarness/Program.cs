using SlimeBridge.Shared;

internal static class Program
{
    private static int checks, failures;
    private static void Check(bool condition, string name)
    {
        checks++;
        if (!condition) { failures++; Console.WriteLine("FAIL " + name); }
    }

    public static int Main()
    {
        // Catch every failure: this positive in-memory fixture never creates a crash dialog,
        // opens a game/native library, or writes a save/registry/profile.
        try
        {
            var progress = new SanctuaryProgress { SaveKey = "fixture", KitSha256 = "original-kit", GoldDeposited = 7, GatePaid = true };
            progress.RequiredManifest.AddRange(new[] { "PINK", "SABER", "HUNTER" });
            progress.FilledStatues.UnionWith(new[] { "PINK", "SABER", "HUNTER" });
            progress.EnsureManifest(new[] { "PINK", "ROCK" }, "new-kit", false);
            Check(progress.RequiredManifest.SequenceEqual(new[] { "PINK", "HUNTER" }), "retire only Saber; keep other frozen requirements");
            Check(progress.FilledStatues.SetEquals(new[] { "PINK", "SABER", "HUNTER" }), "historical filled statues preserved");
            Check(progress.KitSha256 == "original-kit" && progress.GoldDeposited == 7 && progress.GatePaid, "payment and frozen kit preserved");
            Check(progress.TryUnlockPortal(), "retirement can complete the old puzzle without another deposit");
            progress.PortalUsed = true;
            var before = progress.ToJson();
            progress.EnsureManifest(new[] { "PINK", "ROCK" }, "new-kit", false);
            Check(progress.ToJson() == before, "retirement idempotent");
            var loaded = SanctuaryProgress.FromJson(progress.ToJson());
            Check(loaded.PortalUnlocked && loaded.PortalUsed && loaded.FilledStatues.Contains("SABER"), "JSON roundtrip retains quest history");
            Check(!loaded.TryUnlockPortal(), "already open portal is not unlocked twice");

            var incomplete = new SanctuaryProgress();
            incomplete.RequiredManifest.AddRange(new[] { "PINK", "SABER", "HUNTER" });
            incomplete.FilledStatues.Add("PINK");
            incomplete.EnsureManifest(new[] { "PINK" }, "new", false);
            Check(incomplete.RequiredManifest.SequenceEqual(new[] { "PINK", "HUNTER" }) && !incomplete.TryUnlockPortal(), "missing non-retired statue still blocks");

            var olderLayout = new SanctuaryProgress();
            olderLayout.RequiredManifest.AddRange(new[] { "PINK", "SABER" });
            olderLayout.EnsureManifest(new[] { "PINK", "SABER" }, "old", false);
            Check(olderLayout.RequiredManifest.Contains("SABER"), "a still-present old layout is not silently called retired");

            var fresh = new SanctuaryProgress();
            fresh.EnsureManifest(new[] { "PINK", "HUNTER" }, "fresh", false);
            Check(fresh.RequiredManifest.SequenceEqual(new[] { "HUNTER", "PINK" }) && fresh.KitSha256 == "fresh", "new manifest initialization unchanged");
            fresh.EnsureManifest(new[] { "ROCK" }, "dynamic", true);
            Check(fresh.RequiredManifest.SequenceEqual(new[] { "ROCK" }) && fresh.KitSha256 == "dynamic", "explicit dynamic rules unchanged");

            var onlySaber = new SanctuaryProgress { SaveKey = "singleton", KitSha256 = "frozen-kit", GoldDeposited = 5, GatePaid = true };
            onlySaber.RequiredManifest.Add("SABER");
            onlySaber.FilledStatues.Add("SABER");
            onlySaber.EnsureManifest(new[] { "PINK", "ROCK" }, "replacement-kit", false);
            Check(onlySaber.RequiredManifest.Count == 0 && onlySaber.KitSha256 == "frozen-kit", "singleton retirement never imports new requirements or kit");
            Check(onlySaber.TryUnlockPortal(), "initialized empty retired puzzle is complete");
            var emptyBefore = onlySaber.ToJson();
            onlySaber.EnsureManifest(new[] { "PINK", "ROCK" }, "replacement-kit", false);
            Check(onlySaber.ToJson() == emptyBefore, "singleton repeated migration remains frozen empty");
            var emptyLoaded = SanctuaryProgress.FromJson(onlySaber.ToJson());
            emptyLoaded.EnsureManifest(new[] { "PINK", "ROCK" }, "replacement-kit", false);
            Check(emptyLoaded.RequiredManifest.Count == 0 && emptyLoaded.KitSha256 == "frozen-kit" && emptyLoaded.PortalUnlocked,
                "initialized empty survives persistence and replay");
            Check(emptyLoaded.GoldDeposited == 5 && emptyLoaded.GatePaid && emptyLoaded.FilledStatues.SetEquals(new[] { "SABER" }),
                "singleton history and payments survive");
            var trulyNew = new SanctuaryProgress();
            Check(!trulyNew.AllRequiredFilled() && !trulyNew.TryUnlockPortal(), "never-initialized empty is not complete");
            var explicitEmpty = new SanctuaryProgress();
            explicitEmpty.EnsureManifest(Array.Empty<string>(), "empty-kit", false);
            Check(explicitEmpty.TryUnlockPortal(), "explicitly initialized empty puzzle has defined completion");
            var legacyJson = "{\"schema\":\"bridge-sanctuary-progress\",\"schema_version\":1,\"save_key\":\"legacy\",\"kit_sha256\":\"old-kit\",\"gold_deposited\":3,\"gate_paid\":true,\"portal_unlocked\":false,\"portal_used\":false,\"required\":[\"SABER\"],\"filled\":[\"SABER\"],\"updated_utc\":\"\"}";
            var legacy = SanctuaryProgress.FromJson(legacyJson);
            legacy.EnsureManifest(new[] { "PINK", "ROCK" }, "new", false);
            Check(legacy.RequiredManifest.Count == 0 && legacy.KitSha256 == "old-kit" && legacy.TryUnlockPortal(), "legacy JSON without new marker migrates singleton");
            var reloadedLegacy = SanctuaryProgress.FromJson(legacy.ToJson());
            reloadedLegacy.EnsureManifest(new[] { "PINK" }, "newer", false);
            Check(reloadedLegacy.RequiredManifest.Count == 0 && reloadedLegacy.KitSha256 == "old-kit", "legacy migrated marker prevents later thaw");
        }
        catch (Exception ex)
        {
            failures++;
            Console.WriteLine("CAUGHT " + ex.GetType().Name + ": " + ex.Message);
        }
        Console.WriteLine($"SANCTUARY_RETIREMENT_CHECKS={checks} FAILURES={failures}");
        return failures == 0 ? 0 : 1;
    }
}
