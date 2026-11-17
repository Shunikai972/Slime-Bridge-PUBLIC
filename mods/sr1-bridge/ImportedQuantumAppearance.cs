using System;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Keep the native Quantum consumer's actor-local data alive while placeholder
    /// rendering is disabled. Never invokes generation, replaces native prefabs or enables actors.</summary>
    internal static class ImportedQuantumAppearance
    {
        internal static bool EnsureBound(GameObject actor, string canonical, out string problem)
        {
            problem = "";
            if (actor == null || string.IsNullOrEmpty(canonical) ||
                !canonical.StartsWith("largo.", StringComparison.Ordinal) ||
                !(canonical.Contains(".quantum.") || canonical.EndsWith(".quantum", StringComparison.Ordinal))) return true;
            var generator = actor.GetComponent<GenerateQuantumQubit>();
            if (generator == null) return true;
            var applicator = generator.AppearanceApplicator;
            if (applicator == null || !(applicator.transform == actor.transform || applicator.transform.IsChildOf(actor.transform)))
            { problem = "Quantum applicator missing or outside imported actor"; return false; }
            var identity = actor.GetComponent<Identifiable>();
            var definition = applicator.SlimeDefinition;
            if (identity == null || definition == null || definition.IdentifiableId != identity.id ||
                !string.Equals(definition.name, "SlimeBridge_" + canonical, StringComparison.Ordinal))
            { problem = "Quantum definition is not owned by this imported identity"; return false; }
            if (generator.QubitPrefab == null)
            { problem = "Quantum native qubit prefab missing"; return false; }
            // A valid selection (including a selected variant) remains authoritative.
            if (applicator.Appearance != null)
            {
                if (applicator.Appearance.QubitAppearance != null) return true;
                problem = "Quantum selected appearance lacks native qubit appearance";
                return false;
            }
            var defaults = definition.AppearancesDefault;
            if (defaults == null || defaults.Length != 1 || defaults[0] == null || defaults[0].QubitAppearance == null)
            { problem = "Quantum owned default lacks native qubit appearance"; return false; }
            // Direct data binding only: ApplyAppearance would rebuild placeholder rendering and
            // re-enter the guard. Repeated native rebuilds may clear this field; the shared guard
            // restores the same owned object on spawn, rethrow and appearance reapply.
            applicator.Appearance = defaults[0];
            return true;
        }
    }
}
