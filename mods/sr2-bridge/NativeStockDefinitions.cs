using System;
using System.Collections.Generic;
using Il2CppInterop.Runtime.InteropTypes;
using Il2CppMonomiPark.SlimeRancher.Options;

namespace SlimeBridge.SR2
{
    /// <summary>Main-thread, read-only snapshot of the supported native stock definition list.
    /// Avoids casting a returned native value-type enumerator to IEnumerator; the local
    /// IL2CPP wrappers throw on that first MoveNext even when the stock list is unchanged.</summary>
    internal static class NativeStockDefinitions
    {
        internal static List<OptionsItemDefinition> Capture(Il2CppObjectBase source)
        {
            var native = source.TryCast<Il2CppSystem.Collections.Generic.List<OptionsItemDefinition>>();
            if (native == null) throw new InvalidOperationException("unsupported native stock definition collection");
            var count = native.Count;
            var version = native._version;
            if (count <= 0 || count > 4096)
                throw new InvalidOperationException("empty or excessive native stock definitions");
            var snapshot = new List<OptionsItemDefinition>(count);
            for (var index = 0; index < count; index++) snapshot.Add(native[index]);
            if (native.Count != count || native._version != version)
                throw new InvalidOperationException("native stock definitions changed during snapshot");
            return snapshot;
        }
    }
}
