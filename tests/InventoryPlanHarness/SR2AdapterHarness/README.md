# SR2 adapter executable regression harness

Links the **actual production** SR2 `InventoryAgent.cs`, `InventoryRules.cs` and
`BridgeCommands.cs`. `NativeApiFixtures.cs` supplies explicitly synthetic IL2CPP
API doubles. Its binary serialization is a deliberately separate fixture format,
**not native SR2 serialization or proof of in-game persistence**. No game DLL is
loaded or executed. Scratch-only fixture `.sav` files exercise the real shared
independent disk hash and OS-flush verification; never user or game save files.

Run from the assigned worktree:

```bash
dotnet run --project tests/InventoryPlanHarness/SR2AdapterHarness/SR2AdapterHarness.csproj
```

The parent `InventoryPlanHarness.csproj` must exclude `SR2AdapterHarness/**/*.cs`
from its implicit recursive compile glob; this child is a separate executable.
