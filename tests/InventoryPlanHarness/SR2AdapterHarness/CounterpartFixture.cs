// This transfer-only harness never authorizes native creation. The real creator is exercised
// with production InventoryAgent dispatch in tests/CounterpartCreatorHarness, not these doubles.
using SlimeBridge.Shared;
namespace SlimeBridge.SR2
{
    internal sealed class CounterpartCreator
    {
        public static CounterpartCreator? Begin(BridgeCommand c, OpJournal j, int pid, out CommandResult? result)
        { result = Reconcile(c); return null; }
        public static CommandResult Reconcile(BridgeCommand c) => new()
        { OpId = c.OpId, Kind = c.Kind, CanCreate = false, Verified = false, Reason = "counterpart outside transfer-only fixture" };
        public CommandResult? Step() => throw new System.InvalidOperationException("counterpart outside transfer-only fixture");
    }
}
