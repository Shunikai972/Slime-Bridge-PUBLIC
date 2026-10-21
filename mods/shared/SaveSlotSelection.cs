using System;
using System.Collections.Generic;

namespace SlimeBridge.Shared
{
    /// <summary>Read-only selection from a complete native summary traversal, not a reservation.</summary>
    public static class SaveSlotSelection
    {
        public static int SelectFree(int slotCount, string[] rawNames, string[] gameNames,
            int[] slotIndices, bool[] invalid)
        {
            if (slotCount <= 0 || slotCount > 100_000 || rawNames == null || gameNames == null ||
                slotIndices == null || invalid == null || rawNames.Length != gameNames.Length ||
                rawNames.Length != slotIndices.Length || rawNames.Length != invalid.Length)
                throw new InvalidOperationException("Incomplete native save traversal or unknown slot range");
            var raw = new HashSet<string>(StringComparer.Ordinal);
            var byGame = new Dictionary<string, int>(StringComparer.Ordinal);
            var bySlot = new Dictionary<int, string>();
            var occupied = new HashSet<int>();
            for (var index = 0; index < rawNames.Length; index++)
            {
                var game = gameNames[index];
                var slot = slotIndices[index];
                if (string.IsNullOrWhiteSpace(rawNames[index]) || string.IsNullOrWhiteSpace(game) ||
                    invalid[index] || slot < 0 || slot >= slotCount || !raw.Add(rawNames[index]))
                    throw new InvalidOperationException("Invalid or repeated native save summary");
                if ((byGame.TryGetValue(game, out var gameSlot) && gameSlot != slot) ||
                    (bySlot.TryGetValue(slot, out var slotGame) && !string.Equals(slotGame, game, StringComparison.Ordinal)))
                    throw new InvalidOperationException("Conflicting native save slot identities");
                byGame[game] = slot;
                bySlot[slot] = game;
                occupied.Add(slot);
            }
            for (var index = 0; index < slotCount; index++)
                if (!occupied.Contains(index)) return index;
            throw new InvalidOperationException("No free native save slot");
        }
    }
}
