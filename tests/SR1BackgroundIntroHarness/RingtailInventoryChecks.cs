// Authored synthetic registration/model boundaries; never real cache or game proof.
using UnityEngine;
using SlimeBridge.SR1;

internal static class RingtailInventoryChecks
{
    internal static void Run()
    {
        var checks = 0;
        void Check(bool condition, string why) { Program.Check(condition, why); checks++; }
        try
        {
            SpeciesRegistrar.Current = null;
            ImportedModelLoader.Available = true;
            Check(InventoryAgent.TryResolveItem("slime.pink", out var pink, out _) && pink == Identifiable.Id.PINK_SLIME,
                "Pink slime native identity remains unchanged");
            Check(InventoryAgent.TryResolveItem("plort.pink", out var plort, out _) && plort == Identifiable.Id.PINK_PLORT,
                "Pink plort native identity remains unchanged");
            foreach (var unknown in new[] { "", "slime.rad", "slime.ringtail.extra", "plort.ringtail", "SLIME.RINGTAIL" })
                Check(!InventoryAgent.TryResolveItem(unknown, out var invalid, out _) && invalid == Identifiable.Id.NONE,
                    "unsupported canonical identity cannot fall back to a Pink plort");
            Check(!InventoryAgent.TryResolveItem("slime.ringtail", out _, out _), "unregistered Ringtail fails closed");
            SpeciesRegistrar.Current = new SpeciesRegistrar();
            ImportedModelLoader.Available = false;
            Check(!InventoryAgent.TryResolveItem("slime.ringtail", out _, out _), "placeholder-only Ringtail cannot receive a transfer");
            ImportedModelLoader.Available = true;
            Check(InventoryAgent.TryResolveItem("slime.ringtail", out var ringtail, out _) && (int)ringtail == 900010,
                "Ringtail uses only the active registrar identity and verified model");
            SpeciesRegistrar.Current.NativeId = 0;
            Check(!InventoryAgent.TryResolveItem("slime.ringtail", out _, out _), "zero imported identity is refused");
            Console.WriteLine($"{checks} Ringtail inventory resolver checks passed (synthetic)");
        }
        finally { SpeciesRegistrar.Current = null; ImportedModelLoader.Available = true; }
    }
}

namespace SlimeBridge.SR1
{
    internal sealed class SpeciesRegistrar
    {
        internal static SpeciesRegistrar? Current;
        internal int NativeId = 900010;
        internal bool TryResolveNativeId(string canonical, out int nativeId)
        { nativeId = NativeId; return canonical == "slime.ringtail"; }
    }
    internal static class ImportedModelLoader
    {
        internal struct ImportedModel { public GameObject Root; public int VertexTotal, RendererCount; }
        internal static bool Available = true;
        internal static bool TryGet(string canonical, out ImportedModel model, out string reason)
        {
            model = new ImportedModel { Root = new GameObject(), VertexTotal = 1, RendererCount = 1 };
            reason = Available ? "" : "synthetic model unavailable";
            return Available && canonical == "slime.ringtail";
        }
    }
}
