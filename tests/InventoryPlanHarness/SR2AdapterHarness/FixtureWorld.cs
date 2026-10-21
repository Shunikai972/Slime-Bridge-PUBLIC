// Authored SYNTHETIC fixtures, never native serialization or user saves.
using System.Text;
using Il2Cpp;
using SlimeBridge.Shared;
using SlimeBridge.SR2;

internal sealed class FixtureWorld : IDisposable
{
    public const string Identity = "fixture_save";
    public readonly string Root, JournalPath;
    public readonly IdentifiableType Pink = new(BridgeMod.PinkSlimeRef), Plort = new(BridgeMod.PinkPlortRef), Other = new("fixture.other");
    public readonly Ammo Ammo = new();
    public readonly LookupDirector Lookup = new();
    public readonly AutoSaveDirector Director = new();
    public readonly SceneContext Scene = new();
    public readonly FileStorageProvider Provider;
    public readonly OpJournal Journal;
    public InventoryAgent Agent;
    public FixtureWorld(params (int Count, int Capacity, bool Matching, bool Unlocked)[] slots)
    {
        var scratch = Environment.GetEnvironmentVariable("TMPDIR") ?? throw new InvalidOperationException("TMPDIR scratch is required");
        Root = Path.Combine(scratch, "synthetic-sr2-adapter-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Root);
        JournalPath = Path.Combine(Root, "synthetic-op-journal.log");
        Journal = OpJournal.Open(JournalPath, out var error) ?? throw new InvalidOperationException(error);
        Provider = new(Root);
        Ammo.Slots = new(slots.Select(s => new Ammo.Slot { Id = s.Count == 0 ? null : s.Matching ? Pink : Other,
            Count = s.Count, Capacity = s.Capacity, IsUnlocked = s.Unlocked }).ToArray());
        Lookup._identifiableTypeByRefId![Pink.ReferenceId] = Pink;
        Lookup._identifiableTypeByRefId[Plort.ReferenceId] = Plort;
        Lookup._identifiableTypeByRefId[Other.ReferenceId] = Other;
        Director.StorageProvider = Provider;
        Scene.PlayerState = new() { Ammo = Ammo };
        GameContext.Instance = new() { AutoSaveDirector = Director, LookupDirector = Lookup };
        SceneContext.Instance = Scene;
        Provider.AddSyntheticRevision(Identity + "_0", Payload());
        Director.OnSave = () => { Provider.AddSyntheticRevision(Identity + "_1", Payload()); return true; };
        Agent = Program.Create(Journal);
        FixtureTrace.Reset();
    }
    public BridgeCommand Command(string kind = "add", int count = 1, string op = "fixture-op", string item = "slime.pink") =>
        new() { Kind = kind, Count = count, OpId = op, Item = item, SaveId = Identity };
    public CommandResult Run(BridgeCommand command) => Program.Invoke(Agent, command);
    public int LiveCount() => Ammo.Slots.Items.Where(s => s != null && s.IsUnlocked && s.Id?.Pointer == Pink.Pointer).Sum(s => s!.Count);
    public byte[] JournalBytes() => File.Exists(JournalPath) ? File.ReadAllBytes(JournalPath) : Array.Empty<byte>();
    public bool HasIntent => File.Exists(JournalPath) && File.ReadAllText(JournalPath).StartsWith("I\t", StringComparison.Ordinal);
    public byte[] Payload(string? name = null, (int ID, int Count)?[]? entries = null, string[]? index = null,
        bool player = true, bool ammo = true, bool table = true, bool indexTable = true, bool end = true, bool trailing = false)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.UTF8, true);
        writer.Write("SYNTHETIC_SR2_ADAPTER_FIXTURE"); writer.Write(name ?? Identity);
        writer.Write(table);
        if (table)
        {
            writer.Write(indexTable);
            if (indexTable)
            {
                var refs = index ?? new[] { Pink.ReferenceId, Plort.ReferenceId, Other.ReferenceId };
                writer.Write(refs.Length); foreach (var reference in refs) writer.Write(reference);
            }
        }
        writer.Write(player);
        if (player)
        {
            writer.Write(ammo);
            if (ammo)
            {
                var records = entries ?? Ammo.Slots.Items.Select(s => s == null ? ((int, int)?)null :
                    (s.Id == null ? -1 : s.Id.Pointer == Pink.Pointer ? 0 : s.Id.Pointer == Plort.Pointer ? 1 : 2, s.Count)).ToArray();
                writer.Write(records.Length);
                foreach (var record in records)
                {
                    writer.Write(record.HasValue);
                    if (record.HasValue) { writer.Write(record.Value.ID); writer.Write(record.Value.Count); }
                }
            }
        }
        if (end) writer.Write("SYNTHETIC_END");
        if (trailing) writer.Write((byte)42);
        writer.Flush(); return stream.ToArray();
    }
    public void SavePayload(Func<byte[]> bytes, Func<byte[]>? physical = null)
    { Director.OnSave = () => { Provider.AddSyntheticRevision(Identity + "_1", bytes(), physical?.Invoke()); return true; }; }
    public void Dispose()
    {
        GameContext.Instance = null; SceneContext.Instance = null;
        Directory.Delete(Root, true);
    }
}
