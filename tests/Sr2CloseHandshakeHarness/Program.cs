using System;

internal sealed class CloseHarness
{
    private const int OwnPid = 222;
    private TravelMessage? _quitRequest;
    private string? _quitCommitId;
    private bool _verifiedQuit, _held;
    private float _quitUntil;
    private readonly Throttle _throttle = new();
    private readonly Overlay _overlay = new();
    private readonly Transport _transport = new();
    private static string? _save = "ranch";
    private bool _proof = true;
    private int _writes, _resumes;
    internal bool Quiescing => _quitRequest != null || _verifiedQuit;

    // PRODUCTION_METHODS

    private static string? CurrentSave() => _save;
    private bool VerifyCloseSave(TravelMessage _)
    {
        Require(Quiescing && _held && _throttle.Frozen, "save proof taken before writers and gameplay were quiesced");
        _writes++;
        return _proof;
    }
    private void Resume(TravelDone? _) { _held = _throttle.Frozen = false; _resumes++; }
    private static void Require(bool value, string reason)
    { if (!value) throw new InvalidOperationException(reason); }

    public static void Main()
    {
        var prepare = new TravelMessage("pair", "prep", OwnPid, "ranch");
        var commit = new TravelMessage("pair", "commit", OwnPid, "ranch");
        var late = new CloseHarness { _proof = false };
        late.PrepareClose(prepare, (ok, detail) => Require(!ok && detail == "save_not_confirmed", "failed proof accepted"));
        late.CommitClose(commit, (ok, _) => Require(!ok, "commit accepted after failed proof"));
        Require(late._writes == 1 && late._resumes == 1 && !late.TickClose() && Application.Quits == 0, "failed proof closed or retained the freeze");

        late = new CloseHarness();
        late.PrepareClose(prepare, (ok, _) => Require(ok, "valid proof refused"));
        late._transport.Ack = prepare.MessageId;
        Require(late.TickClose() && late.Quiescing && Application.Quits == 0, "prepare receipt alone caused exit");
        Time.realtimeSinceStartup = 21f;
        late.CommitClose(commit, (ok, _) => Require(!ok, "commit after the local deadline accepted"));
        late._transport.Ack = commit.MessageId;
        Require(!late.Quiescing && !late.TickClose() && Application.Quits == 0, "late receipt revived an aborted close");

        var valid = new CloseHarness();
        Time.realtimeSinceStartup = 30;
        valid.PrepareClose(prepare, (ok, _) => Require(ok, "second valid proof refused"));
        valid.CommitClose(new TravelMessage("other_pair", "old", OwnPid, "ranch"), (ok, _) => Require(!ok, "foreign commit accepted"));
        valid.CommitClose(commit, (ok, _) => Require(ok, "commit refused after proof"));
        Require(valid.TickClose() && valid.Quiescing && Application.Quits == 0, "quit before commit receipt");
        valid._transport.Ack = commit.MessageId;
        valid.TickClose(); valid.TickClose();
        Require(valid._writes == 1 && valid.Quiescing && Application.Quits == 1, "verified close should quit exactly once and leave writes disabled");

        var busy = new CloseHarness();
        Sr2Online.Safe = false;
        busy.PrepareClose(prepare, (ok, _) => Require(!ok, "accepted close during native multiplayer"));
        Require(busy._writes == 0 && !busy.Quiescing, "multiplayer refusal wrote to the ranch");
        Console.WriteLine("SR2 close handshake OK");
    }
}

internal sealed record TravelMessage(string TransitionId, string MessageId, int TargetPid, string TargetSave)
{
    public bool Targets(int pid, string? save) => pid == TargetPid && save == TargetSave;
    public string SaveForClose(int pid, Func<string?> read, Func<bool> save) =>
        !Targets(pid, read()) ? "wrong_save_or_process" : save() && Targets(pid, read()) ? "" : "save_not_confirmed";
}
internal delegate void TravelDone(bool ok, string detail);
internal sealed class Throttle { public bool Frozen; public void Apply(bool freeze) => Frozen = freeze; }
internal sealed class Overlay { public void CoverNow(string _, string __) { } }
internal sealed class Transport { public string? Ack; public bool SavedAcknowledged(string id) => id == Ack; }
internal static class TravelText { public static string SavingAndClosing(string other) => other; }
internal static class Sr2Online { public static bool Safe = true; public static bool CanQuiesce() => Safe; }
internal static class Time { public static float realtimeSinceStartup; }
internal static class Application { public static int Quits; public static void Quit() => Quits++; }
