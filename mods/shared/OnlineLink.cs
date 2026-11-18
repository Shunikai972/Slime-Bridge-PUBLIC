using System;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Where this game's online mod stands (SRMP in Slime Rancher, Ranching Together in Slime Rancher 2), sent with every
    /// heartbeat so the launcher shows the same code the game shows. Built on the main thread, read by the heartbeat thread.
    /// </summary>
    internal sealed class OnlineState
    {
        public bool mod { get; set; }          // the online mod is loaded and speaks to SlimeBridge
        public bool signed_in { get; set; }    // ready to host or join (SRMP: Epic sign-in done)
        public bool hosting { get; set; }
        public bool connected { get; set; }    // in someone else's game
        // Sanitised on write: BridgeHost refuses a heartbeat whose code is not alphanumeric or whose error exceeds 80 characters.
        private string _code = "", _error = "";
        public string code { get => _code; set => _code = OnlineCodes.Clean(value); }
        public string error { get => _error; set => _error = Safe(value); }

        internal string Json()
        {
            return "{\"mod\":" + B(mod) + ",\"signed_in\":" + B(signed_in) + ",\"hosting\":" + B(hosting) + ",\"connected\":" + B(connected) +
                   ",\"code\":\"" + OnlineCodes.Clean(code) + "\",\"error\":\"" + Safe(error) + "\"}";
        }

        private static string B(bool value) => value ? "true" : "false";

        private static string Safe(string text)
        {
            var sb = new StringBuilder();
            foreach (var c in text ?? "")
            {
                if (sb.Length >= 80) break;
                if (char.IsLetterOrDigit(c) || c == '_' || c == ':' || c == ' ' || c == '.' || c == '-') sb.Append(c);
            }
            return sb.ToString();
        }
    }

    internal static class OnlineStatus
    {
        private static OnlineState? _current;
        internal static OnlineState? Current => System.Threading.Volatile.Read(ref _current);
        internal static void Set(OnlineState state) => System.Threading.Volatile.Write(ref _current, state);
    }

    /// <summary>
    /// The one code players share: the 9-character Base58 session code (Ranching Together's join code; SRMP opens its lobby
    /// under the same code), or SRMP's own 7-character code when only Slime Rancher is online.
    /// </summary>
    internal static class OnlineCodes
    {
        private const string Base58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

        internal static string Clean(string? code)
        {
            var sb = new StringBuilder();
            foreach (var c in code ?? "")
                if (c < 128 && char.IsLetterOrDigit(c) && sb.Length < 16) sb.Append(c);
            return sb.ToString();
        }

        /// <summary>A player name as SRMP accepts it (letters, digits, underscore; not starting with a digit), else "".</summary>
        internal static string CleanName(string? name)
        {
            if (string.IsNullOrEmpty(name) || name!.Length > 24) return "";
            if (!(char.IsLetter(name[0]) && name[0] < 128) && name[0] != '_') return "";
            foreach (var c in name) if (!(c < 128 && (char.IsLetterOrDigit(c) || c == '_'))) return "";
            return name;
        }

        internal static bool IsSession(string? code)
        {
            if (code == null || code.Length != 9) return false;
            foreach (var c in code) if (Base58.IndexOf(c) < 0) return false;
            return true;
        }

        internal static bool IsSr1(string? code)
        {
            if (code == null || code.Length != 7) return false;
            foreach (var c in code) if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) return false;
            return true;
        }
    }
}
