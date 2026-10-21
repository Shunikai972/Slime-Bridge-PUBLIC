// Only the unused transfer surfaces are doubled here. CounterpartCreator and InventoryAgent
// are the linked production sources, and all creation storage is the in-memory synthetic fixture.
namespace SlimeBridge.SR2
{
    internal static class BridgeMod { public const string PinkSlimeRef = "fixture.slime", PinkPlortRef = "fixture.plort"; }
}
namespace Il2Cpp
{
    public sealed class IdentifiableType : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase
    { public string ReferenceId = "fixture.identifiable"; }
    public sealed class LookupDirector
    { public bool TryFindIdentifiableTypeByReferenceId(string id, out IdentifiableType? type) { type = null; return false; } }
    public sealed class Ammo
    {
        public sealed class Slot { public IdentifiableType? Id; public int Count; public bool IsUnlocked; }
        public Slot[] Slots = Array.Empty<Slot>();
        public bool CouldAddToSlot(IdentifiableType type) => false;
        public int GetSlotCount(int index) => 0;
        public void Decrement(int index, int count) => throw new InvalidOperationException("transfer is outside this harness");
        public bool MaybeAddToSpecificSlot(IdentifiableType type, object? actor, int index, int count, bool force) => false;
    }
}
namespace SlimeBridge.SR2
{
    // Creation/readiness-only fixtures must never authorize transfer persistence.
    // Kept outside the removable BridgeMod fixture namespace for snapshot extraction.
    // Real disk proof is production-linked in Sr2InventoryHarness.
    internal sealed class Sr2InventoryPersistence
    {
        internal Sr2InventoryPersistence(Il2Cpp.GameContext game, Il2Cpp.SceneContext scene,
            string identity, Il2Cpp.IdentifiableType type)
        { throw new InvalidOperationException("transfer persistence outside creation-only fixture"); }
        internal readonly struct RefusedProof
        {
            internal bool Verified => false;
            internal string SaveFile => "";
            internal string Sha256 => "";
            internal string Reason => "transfer persistence outside creation-only fixture";
        }
        internal RefusedProof Save(int count) => throw new InvalidOperationException("transfer outside fixture");
        internal RefusedProof Recheck(SlimeBridge.Shared.CommandResult receipt) => throw new InvalidOperationException("transfer outside fixture");
    }
}
