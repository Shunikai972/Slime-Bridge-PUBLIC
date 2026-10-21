using System;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Names of items that travel with the player. Each game sends its own native names (SR1 <c>CARROT_VEGGIE</c>, SR2
    /// <c>IdentifiableType.Carrot</c>); the receiving game finds its own item with the same canonical key or leaves the item
    /// where it is. Only a move is ever done (taken from one vac, put in the other), never a copy.
    /// </summary>
    internal static class CarryNames
    {
        private static readonly string[] Suffixes = { "veggie", "fruit", "slime" };

        /// <summary><c>CARROT_VEGGIE</c> / <c>IdentifiableType.Carrot</c> -> <c>carrot</c>; <c>PINK_PLORT</c> / <c>IdentifiableType.PinkPlort</c> -> <c>pinkplort</c>.</summary>
        internal static string Canonical(string name)
        {
            var dot = name.LastIndexOf('.');
            var raw = dot >= 0 ? name.Substring(dot + 1) : name;
            var sb = new StringBuilder(raw.Length);
            foreach (var c in raw)
                if (char.IsLetterOrDigit(c)) sb.Append(char.ToLowerInvariant(c));
            var key = sb.ToString();
            foreach (var suffix in Suffixes)
                if (key.Length > suffix.Length + 1 && key.EndsWith(suffix, StringComparison.Ordinal)) return key.Substring(0, key.Length - suffix.Length);
            return key;
        }

        internal static bool IsSlimeOrPlort(string name)
        {
            var upper = name.ToUpperInvariant();
            return upper.EndsWith("PLORT", StringComparison.Ordinal) || upper.EndsWith("_SLIME", StringComparison.Ordinal) ||
                   upper.StartsWith("SLIMEDEFINITION.", StringComparison.Ordinal);
        }

        /// <summary>Whether an item may travel under the player's setting (<c>ALL</c>, <c>SLIMES_PLORTS</c>, <c>NONE</c>).</summary>
        internal static bool Allowed(string mode, string name)
        {
            if (name.Length == 0 || name.Length > 64) return false;
            if (mode == "ALL") return true;
            return mode == "SLIMES_PLORTS" && IsSlimeOrPlort(name);
        }
    }
}
