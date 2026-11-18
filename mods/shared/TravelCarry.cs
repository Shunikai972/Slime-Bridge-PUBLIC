using System;
using System.Collections.Generic;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Names of items that travel with the player. Each game sends its own native names (SR1 <c>CARROT_VEGGIE</c>, SR2
    /// <c>IdentifiableType.Carrot</c>) or, for a species it only knows as imported content, the bridge id
    /// (<c>slime.cotton</c>, <c>plort.cotton</c>, <c>largo.angler.batty</c>). The receiving game finds its own item with the same
    /// canonical key or leaves the item where it is. Only a move is ever done (taken from one vac, put in the other), never a copy.
    /// </summary>
    internal static class CarryNames
    {
        private static readonly string[] Suffixes = { "veggie", "fruit", "slime" };

        // Species words a Largo name is made of (both games; SR1/bridge "slumber" is SR2's "sloomber").
        private static readonly HashSet<string> Species = new HashSet<string>(StringComparer.Ordinal)
        {
            "pink", "rock", "tabby", "phosphor", "honey", "boom", "puddle", "fire", "crystal", "hunter", "quantum", "dervish",
            "tangle", "mosaic", "saber", "rad", "gold", "lucky", "tarr", "glitch", "quicksilver", "cotton", "batty", "flutter",
            "angler", "ringtail", "yolky", "twin", "hyper", "sloomber", "shadow",
        };

        /// <summary>
        /// <c>CARROT_VEGGIE</c> / <c>IdentifiableType.Carrot</c> -> <c>carrot</c>; <c>PINK_PLORT</c> / <c>IdentifiableType.PinkPlort</c> /
        /// <c>plort.pink</c> -> <c>pinkplort</c>; <c>slime.cotton</c> / <c>SlimeDefinition.Cotton</c> -> <c>cotton</c>; a Largo
        /// (<c>PINK_TABBY_LARGO</c>, <c>SlimeDefinition.PinkTabby</c>, <c>largo.pink.tabby</c>, <c>SlimeBridge.SlimeDefinition.largo.angler.rad</c>)
        /// -> <c>largo:a+b</c> with the two species in alphabetical order, so the games' different orders meet.
        /// </summary>
        internal static string Canonical(string name)
        {
            if (string.IsNullOrEmpty(name)) return "";
            var largo = BridgeLargo(name);
            if (largo != null) return largo;
            if (name.StartsWith("slime.", StringComparison.Ordinal)) return Plain(name.Substring(6));
            if (name.StartsWith("plort.", StringComparison.Ordinal)) return Plain(name.Substring(6)) + "plort";

            var dot = name.LastIndexOf('.');
            var raw = dot >= 0 ? name.Substring(dot + 1) : name;
            var words = Words(raw);
            if (words.Count == 3 && words[2] == "largo" && IsSpecies(words[0]) && IsSpecies(words[1])) return LargoKey(words[0], words[1]);
            if (words.Count == 2 && name.StartsWith("SlimeDefinition.", StringComparison.Ordinal) && IsSpecies(words[0]) && IsSpecies(words[1]))
                return LargoKey(words[0], words[1]);
            return Plain(raw);
        }

        private static string? BridgeLargo(string name)
        {
            int at;
            if (name.StartsWith("largo.", StringComparison.Ordinal)) at = 6;
            else
            {
                var i = name.IndexOf(".largo.", StringComparison.Ordinal);
                if (i < 0) return null;
                at = i + 7;
            }
            var parts = name.Substring(at).Split('.');
            if (parts.Length != 2) return null;
            var a = Plain(parts[0]);
            var b = Plain(parts[1]);
            return a.Length == 0 || b.Length == 0 ? null : LargoKey(a, b);
        }

        private static string Sloomber(string word) => word == "slumber" ? "sloomber" : word;

        private static string LargoKey(string a, string b)
        {
            a = Sloomber(a);
            b = Sloomber(b);
            return string.CompareOrdinal(a, b) <= 0 ? "largo:" + a + "+" + b : "largo:" + b + "+" + a;
        }

        private static bool IsSpecies(string word) => Species.Contains(Sloomber(word));

        /// <summary>Lower-case words of a native name: <c>PINK_TABBY_LARGO</c> -> pink, tabby, largo; <c>PinkTabby</c> -> pink, tabby.</summary>
        private static List<string> Words(string raw)
        {
            var words = new List<string>();
            var sb = new StringBuilder();
            for (var i = 0; i < raw.Length; i++)
            {
                var c = raw[i];
                if (!char.IsLetterOrDigit(c)) { Flush(words, sb); continue; }
                if (char.IsUpper(c) && sb.Length > 0 && char.IsLower(raw[i - 1])) Flush(words, sb);
                sb.Append(char.ToLowerInvariant(c));
            }
            Flush(words, sb);
            return words;
        }

        private static void Flush(List<string> words, StringBuilder sb)
        {
            if (sb.Length > 0) { words.Add(sb.ToString()); sb.Length = 0; }
        }

        private static string Plain(string raw)
        {
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
                   upper.EndsWith("_LARGO", StringComparison.Ordinal) || upper.StartsWith("SLIMEDEFINITION.", StringComparison.Ordinal) ||
                   upper.StartsWith("SLIMEBRIDGE.SLIMEDEFINITION.", StringComparison.Ordinal) || upper.StartsWith("SLIME.", StringComparison.Ordinal) ||
                   upper.StartsWith("PLORT.", StringComparison.Ordinal) || upper.StartsWith("LARGO.", StringComparison.Ordinal);
        }

        /// <summary>Whether an item may travel under the player's setting (<c>ALL</c>, <c>SLIMES_PLORTS</c>, <c>NONE</c>).</summary>
        internal static bool Allowed(string mode, string name)
        {
            if (name.Length == 0 || name.Length > 64) return false;
            if (mode == "ALL") return true;
            return mode == "SLIMES_PLORTS" && IsSlimeOrPlort(name);
        }

        /// <summary>What was offered but not taken ("Cotton x3"), for the player's notice; empty when everything moved.</summary>
        internal static string Left(Dictionary<string, int> offered, Dictionary<string, int> accepted)
        {
            var parts = new List<string>();
            foreach (var item in offered)
            {
                var took = accepted.TryGetValue(item.Key, out var n) ? n : 0;
                if (item.Value > took) parts.Add(Display(item.Key) + " x" + (item.Value - took));
            }
            return string.Join(", ", parts.ToArray());
        }

        /// <summary>A readable item name: <c>SlimeDefinition.Cotton</c> -> Cotton, <c>PINK_PLORT</c> -> Pink Plort, <c>largo.angler.rad</c> -> Angler Rad Largo.</summary>
        internal static string Display(string name)
        {
            var key = Canonical(name);
            if (key.StartsWith("largo:", StringComparison.Ordinal))
            {
                var pair = key.Substring(6).Split('+');
                return Title(pair[0]) + " " + Title(pair.Length > 1 ? pair[1] : "") + " Largo";
            }
            if (key.EndsWith("plort", StringComparison.Ordinal) && key.Length > 5) return Title(key.Substring(0, key.Length - 5)) + " Plort";
            return Title(key);
        }

        private static string Title(string word) => word.Length == 0 ? "" : char.ToUpperInvariant(word[0]) + word.Substring(1);
    }
}
