using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace SlimeBridge.Shared
{
    /// <summary>One host -> plugin inventory command (protocol v1, /v1/commands/next).</summary>
    internal sealed class BridgeCommand
    {
        public string OpId = "";
        public string Kind = "";
        public string Item = "";
        public int Count;
        public string? SaveId;
        public string? CreationId;

        public bool Mutates => Kind == "remove" || Kind == "add" || Kind == "lab_give" || Kind == "create_save";
        public bool IsCreation => Kind == "create_save" || Kind == "reconcile_save";

        // Exact retired legacy creation ids (UNRESOLVED_LEGACY_INTENT_QUARANTINED; mirrors
        // bridge/counterpart/legacy_quarantine.py). A retired id never becomes a native command.
        private static readonly HashSet<string> RetiredCreationIds = new HashSet<string>(StringComparer.Ordinal)
        {
            "07afb0b92e1b4461be1560b7da6b995d",
            "2e2a689bf0f7452eb63db918ffd4036c",
            "3209eadc2fd14b35982ac256f733091b",
            "56a077126cb24847b4e46f1216fb0ec4",
            "9f1fea2d47ef4594a540a5eb83805802",
            "ae635bb84dde47e192ae0f2e8cfaa278",
            "b767b95d44d546cfa11bf567e46fdfb3",
            "c5c48aa00cda4e378dc3a0375571f9e3",
            "ef9415e10fdf4b509039141770bd03d5"
        };

        public static bool IsRetiredCreationId(string? creationId) =>
            creationId != null && RetiredCreationIds.Contains(creationId);

        private static readonly Regex OpIdRe = new Regex("\"op_id\"\\s*:\\s*\"([A-Za-z0-9:_-]{1,128})\"", RegexOptions.CultureInvariant);
        private static readonly Regex KindRe = new Regex("\"kind\"\\s*:\\s*\"(query|remove|add|lab_give|create_save|reconcile_save)\"", RegexOptions.CultureInvariant);
        private static readonly Regex CreationRe = new Regex("\"creation_id\"\\s*:\\s*\"([0-9a-f]{32})\"", RegexOptions.CultureInvariant);
        private static readonly Regex ItemRe = new Regex("\"item\"\\s*:\\s*\"(slime\\.pink|plort\\.pink|slime\\.ringtail)\"", RegexOptions.CultureInvariant);
        private static readonly Regex CountRe = new Regex("\"count\"\\s*:\\s*([0-9]{1,2})\\b", RegexOptions.CultureInvariant);
        private static readonly Regex SaveRe = new Regex("\"save_id\"\\s*:\\s*\"([A-Za-z0-9_-]{1,128})\"", RegexOptions.CultureInvariant);

        /// <summary>Parse the host reply; null when there is no command or it is malformed.</summary>
        public static BridgeCommand? Parse(string? json)
        {
            if (string.IsNullOrEmpty(json) || json!.Contains("\"command\": null") || json.Contains("\"command\":null"))
                return null;
            var op = OpIdRe.Match(json);
            var kind = KindRe.Match(json);
            if (!op.Success || !kind.Success) return null;
            if (kind.Groups[1].Value == "create_save" || kind.Groups[1].Value == "reconcile_save")
            {
                var creation = CreationRe.Match(json);
                if (!creation.Success || IsRetiredCreationId(creation.Groups[1].Value)) return null;
                return new BridgeCommand { OpId = op.Groups[1].Value, Kind = kind.Groups[1].Value, CreationId = creation.Groups[1].Value };
            }
            var item = ItemRe.Match(json);
            var count = CountRe.Match(json);
            if (!item.Success || !count.Success) return null;
            var n = int.Parse(count.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture);
            if (n < 0 || n > 30) return null;
            var save = SaveRe.Match(json);
            return new BridgeCommand
            {
                OpId = op.Groups[1].Value, Kind = kind.Groups[1].Value, Item = item.Groups[1].Value, Count = n,
                SaveId = save.Success ? save.Groups[1].Value : null,
            };
        }
    }

    /// <summary>Result of executing (or refusing) one command; serialized by hand (no JSON library dependency).</summary>
    internal sealed class CommandResult
    {
        public string Realm = "";
        public string OpId = "";
        public string Kind = "";
        public string Item = "";
        public bool Ok;
        public bool Applied;
        public bool Uncertain;
        public bool Persisted;
        public int Before = -1;
        public int After = -1;
        public int Count;
        public bool? CanAdd;
        public string SaveId = "";
        public string Reason = "";
        public string CreationId = "";
        public string SaveFile = "";
        public string Sha256 = "";
        public bool? Verified = null;
        public bool? CanCreate = null;

        public string ToJson(int pid)
        {
            var sb = new StringBuilder(256);
            sb.Append("{\"protocol_version\":1,\"message_type\":\"command_result\",\"realm\":\"").Append(Realm)
              .Append("\",\"op_id\":\"").Append(Esc(OpId))
              .Append("\",\"kind\":\"").Append(Esc(Kind))
              .Append("\",\"item\":\"").Append(Esc(Item))
              .Append("\",\"pid\":").Append(pid)
              .Append(",\"ok\":").Append(Ok ? "true" : "false")
              .Append(",\"applied\":").Append(Applied ? "true" : "false")
              .Append(",\"uncertain\":").Append(Uncertain ? "true" : "false")
              .Append(",\"persisted\":").Append(Persisted ? "true" : "false")
              .Append(",\"before\":").Append(Before)
              .Append(",\"after\":").Append(After)
              .Append(",\"count\":").Append(Count);
            if (CanAdd.HasValue) sb.Append(",\"can_add\":").Append(CanAdd.Value ? "true" : "false");
            if (Verified.HasValue) sb.Append(",\"verified\":").Append(Verified.Value ? "true" : "false");
            if (CanCreate.HasValue) sb.Append(",\"can_create\":").Append(CanCreate.Value ? "true" : "false");
            if (CreationId.Length > 0) sb.Append(",\"creation_id\":\"").Append(Esc(CreationId)).Append('"');
            if (SaveFile.Length > 0) sb.Append(",\"save_file\":\"").Append(Esc(Trim(SaveFile, 128))).Append('"');
            if (Sha256.Length > 0) sb.Append(",\"sha256\":\"").Append(Esc(Sha256)).Append('"');
            sb.Append(",\"save_id\":\"").Append(Esc(Trim(SaveId, 128)))
              .Append("\",\"reason\":\"").Append(Esc(Trim(Reason, 256))).Append("\"}");
            return sb.ToString();
        }

        private static string Trim(string s, int n) => s.Length <= n ? s : s.Substring(0, n);

        internal static string Esc(string value)
        {
            var sb = new StringBuilder(value.Length + 8);
            foreach (var c in value)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            return sb.ToString();
        }

        // A DONE is bound to the original command. An old receipt missing count or disk proof
        // can be opened for compatibility, but can never be promoted to successful replay.
        public static CommandResult ReplayInventory(BridgeCommand command, string realm, string stored,
            Func<CommandResult, bool> recheck)
        {
            var response = new CommandResult { Realm = realm, OpId = command.OpId, Kind = command.Kind,
                Item = command.Item, Count = command.Count, SaveId = command.SaveId ?? "", Verified = false };
            try
            {
                var data = OpJournal.ValidateReceipt(command.OpId, stored);
                if ((command.Item != "slime.pink" && command.Item != "plort.pink" && command.Item != "slime.ringtail") ||
                    !Regex.IsMatch(command.SaveId ?? "", "\\A[A-Za-z0-9_-]{1,128}\\z") ||
                    !(data.TryGetValue("realm", out var r) && r is string sr && sr == realm) ||
                    !(data.TryGetValue("op_id", out var o) && o is string so && so == command.OpId) ||
                    !(data.TryGetValue("kind", out var k) && k is string sk && sk == command.Kind) ||
                    !(data.TryGetValue("item", out var i) && i is string si && si == command.Item) ||
                    !(data.TryGetValue("count", out var c) && c is int sc && sc == command.Count) ||
                    !(data.TryGetValue("save_id", out var s) && s is string ss && ss == command.SaveId))
                {
                    response.Uncertain = true;
                    response.Reason = "op id receipt binding missing or mismatched";
                    return response;
                }
                response.SaveId = (string)data["save_id"];
                if (!(data.TryGetValue("before", out var b) && b is int bi) ||
                    !(data.TryGetValue("after", out var a) && a is int ai))
                {
                    response.Uncertain = true;
                    response.Reason = "journaled inventory delta is incomplete";
                    return response;
                }
                response.Before = bi;
                response.After = ai;
                response.Applied = data.TryGetValue("applied", out var ap) && ap is bool ab && ab;
                response.CanAdd = data.TryGetValue("can_add", out var ca) && ca is bool cab ? cab : (bool?)null;
                response.Reason = data.TryGetValue("reason", out var reason) && reason is string why ? why : "";
                response.SaveFile = data.TryGetValue("save_file", out var f) && f is string sf ? sf : "";
                response.Sha256 = data.TryGetValue("sha256", out var h) && h is string sh ? sh : "";
                var oldOk = (bool)data["ok"];
                if (!(data.TryGetValue("persisted", out var p) && p is bool) ||
                    !(data.TryGetValue("uncertain", out var u) && u is bool))
                {
                    response.Uncertain = true;
                    response.Reason = "journaled outcome flags are incomplete";
                    return response;
                }
                var oldPersisted = (bool)p;
                var oldVerified = data.TryGetValue("verified", out var v) && v is bool vb && vb;
                var oldUncertain = (bool)u;
                if (oldUncertain || (response.Applied && (!oldPersisted || !oldVerified)))
                {
                    response.Uncertain = true;
                    response.Reason = "journaled mutation has no durable verified outcome";
                    return response;
                }
                // A clean refusal cannot carry evidence of a changed native count. -1/-1 is the
                // writer's explicit unknown-count pair for a refusal before inventory inspection.
                if (!oldOk && !response.Applied && !oldPersisted && !oldVerified &&
                    response.Before >= -1 && response.Before == response.After) return response;
                var expectedAfter = (long)response.Before + (command.Kind == "remove" ? -command.Count : command.Count);
                if (!oldOk || !response.Applied || !oldPersisted || !oldVerified ||
                    command.Count < 1 || command.Count > 30 ||
                    (command.Kind != "remove" && command.Kind != "add" && command.Kind != "lab_give") ||
                    response.Before < 0 || response.After < 0 || response.After != expectedAfter ||
                    !Regex.IsMatch(response.SaveFile, "\\A" + Regex.Escape(response.SaveId) + "_(0|[1-9][0-9]{0,9})\\z") ||
                    !Regex.IsMatch(response.Sha256, "\\A[0-9a-f]{64}\\z") ||
                    !recheck(response))
                {
                    response.Uncertain = true;
                    response.Reason = "journaled after-state cannot be reverified";
                    return response;
                }
                response.Ok = response.Persisted = true;
                response.Verified = true;
                return response;
            }
            catch (Exception ex)
            {
                response.Uncertain = true;
                response.Reason = "receipt replay failed: " + ex.GetType().Name;
                return response;
            }
        }
    }

    /// <summary>
    /// Append-only, fsynced per-op journal. A mutating op writes INTENT before touching the native
    /// inventory and DONE (with its result) after. After a crash, INTENT without DONE is reported as
    /// uncertain and is NEVER re-applied, so a redelivered command cannot duplicate or double-remove.
    /// </summary>
    internal sealed class OpJournal
    {
        private readonly string _path;
        private readonly HashSet<string> _intents = new HashSet<string>(StringComparer.Ordinal);
        private readonly Dictionary<string, string> _done = new Dictionary<string, string>(StringComparer.Ordinal);

        private OpJournal(string path) { _path = path; }

        public static OpJournal? Open(string? path, out string error)
        {
            error = "";
            if (string.IsNullOrWhiteSpace(path)) { error = "no journal configured"; return null; }
            try
            {
                var full = Path.GetFullPath(path);
                Directory.CreateDirectory(Path.GetDirectoryName(full)!);
                var journal = new OpJournal(full);
                if (File.Exists(full))
                {
                    // An unreadable intent cannot be treated as proof that no dispatch happened.
                    var contents = File.ReadAllText(full, new UTF8Encoding(false, true));
                    if (contents.Length > 0 && !contents.EndsWith("\n", StringComparison.Ordinal))
                        throw new InvalidDataException("unterminated operation journal record");
                    // Read the same complete snapshot whose final delimiter was checked.
                    using (var reader = new StringReader(contents))
                    {
                        string? line;
                        while ((line = reader.ReadLine()) != null)
                        {
                            var parts = line.Split('\t');
                            if (parts.Length < 2 || !Regex.IsMatch(parts[1], "\\A[A-Za-z0-9:_-]{1,128}\\z"))
                                throw new InvalidDataException("malformed operation journal record");
                            if (parts.Length == 2 && parts[0] == "I")
                            {
                                if (journal._done.ContainsKey(parts[1]) || !journal._intents.Add(parts[1]))
                                    throw new InvalidDataException("duplicate or completed operation journal intent");
                            }
                            else if (parts.Length == 3 && parts[0] == "D")
                            {
                                ValidateReceipt(parts[1], parts[2]);
                                if (journal._done.ContainsKey(parts[1]))
                                    throw new InvalidDataException("duplicate operation journal result");
                                journal._done[parts[1]] = parts[2];
                            }
                            else throw new InvalidDataException("unknown or incomplete operation journal record");
                        }
                    }
                }
                return journal;
            }
            catch (Exception ex) { error = ex.GetType().Name + ": " + ex.Message; return null; }
        }

        // Exactly the fields CommandResult.ToJson can emit, each with its one permitted JSON type.
        private static readonly HashSet<string> StringFields = new HashSet<string>(StringComparer.Ordinal)
            { "message_type", "realm", "op_id", "kind", "item", "creation_id", "save_file", "sha256", "save_id", "reason" };
        private static readonly HashSet<string> IntFields = new HashSet<string>(StringComparer.Ordinal)
            { "protocol_version", "pid", "before", "after", "count" };
        private static readonly HashSet<string> BoolFields = new HashSet<string>(StringComparer.Ordinal)
            { "ok", "applied", "uncertain", "persisted", "can_add", "verified", "can_create" };
        private static readonly HashSet<string> Kinds = new HashSet<string>(StringComparer.Ordinal)
            { "query", "remove", "add", "lab_give", "create_save", "reconcile_save" };

        // Dependency-free on purpose: SR1's Unity Mono has no SMDiagnostics, so DataContractJsonSerializer
        // threw TypeLoadException there and left the journal unavailable (observed in a real lab run).
        internal static Dictionary<string, object> ValidateReceipt(string opId, string json)
        {
            ValidateOpId(opId);
            var values = FlatJson.ParseObject(json);
            foreach (var pair in values)
            {
                var typed = pair.Value is string ? StringFields.Contains(pair.Key)
                          : pair.Value is int ? IntFields.Contains(pair.Key)
                          : pair.Value is bool && BoolFields.Contains(pair.Key);
                if (!typed) throw new InvalidDataException("unexpected or mistyped journal result field: " + pair.Key);
            }
            if (!(values.TryGetValue("protocol_version", out var version) && version is int v && v == 1) ||
                !(values.TryGetValue("message_type", out var type) && type is string t && t == "command_result") ||
                !(values.TryGetValue("realm", out var realm) && realm is string r && (r == "SR1" || r == "SR2")) ||
                !(values.TryGetValue("op_id", out var op) && op is string o && o == opId) ||
                !(values.TryGetValue("kind", out var kind) && kind is string k && Kinds.Contains(k)) ||
                !(values.TryGetValue("pid", out var pid) && pid is int p && p > 0) ||
                !(values.TryGetValue("ok", out var ok) && ok is bool) ||
                !(values.TryGetValue("applied", out var applied) && applied is bool))
                throw new InvalidDataException("invalid operation journal result provenance");
            return values;
        }

        public bool TryGetDone(string opId, out string resultJson)
        {
            if (_done.TryGetValue(opId, out var stored)) { resultJson = stored; return true; }
            resultJson = "";
            return false;
        }
        public bool HasIntentOnly(string opId) => _intents.Contains(opId) && !_done.ContainsKey(opId);
        public bool HasAny(string opId) => _intents.Contains(opId) || _done.ContainsKey(opId);

        public void WriteIntent(string opId)
        {
            ValidateOpId(opId);
            if (HasAny(opId))
                throw new InvalidDataException("duplicate or completed operation journal intent");
            Append("I\t" + opId);
            _intents.Add(opId);
        }

        public void WriteDone(string opId, string resultJson)
        {
            ValidateOpId(opId);
            if (resultJson.IndexOfAny(new[] { '\t', '\n', (char)13 }) >= 0)
                throw new InvalidDataException("journal result must be one tab-free line");
            ValidateReceipt(opId, resultJson);
            if (_done.TryGetValue(opId, out var previous))
            {
                if (previous != resultJson) throw new InvalidDataException("conflicting operation journal results");
                return;
            }
            Append("D\t" + opId + "\t" + resultJson);
            _done[opId] = resultJson;
        }

        private static void ValidateOpId(string opId)
        {
            if (!Regex.IsMatch(opId, "\\A[A-Za-z0-9:_-]{1,128}\\z"))
                throw new InvalidDataException("malformed operation journal id");
        }

        private void Append(string line)
        {
            var bytes = Encoding.UTF8.GetBytes(line + "\n");
            using (var stream = new FileStream(_path, FileMode.Append, FileAccess.Write, FileShare.Read, 4096, FileOptions.WriteThrough))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
        }
    }

    /// <summary>
    /// Strict reader for one flat JSON object of strings, integers and booleans, the only shapes
    /// CommandResult.ToJson writes. Anything else (nesting, null, fractions, exponents, leading zeros,
    /// duplicate keys, invalid escapes, raw control characters, trailing data) is refused.
    /// </summary>
    internal static class FlatJson
    {
        public static Dictionary<string, object> ParseObject(string json)
        {
            var values = new Dictionary<string, object>(StringComparer.Ordinal);
            var i = 0;
            Expect(json, ref i, '{');
            SkipSpace(json, ref i);
            if (i < json.Length && json[i] == '}') i++;
            else
            {
                while (true)
                {
                    SkipSpace(json, ref i);
                    var key = ReadString(json, ref i);
                    Expect(json, ref i, ':');
                    SkipSpace(json, ref i);
                    var value = ReadValue(json, ref i);
                    if (values.ContainsKey(key)) throw new InvalidDataException("duplicate key: " + key);
                    values.Add(key, value);
                    SkipSpace(json, ref i);
                    if (i >= json.Length) throw new InvalidDataException("unterminated object");
                    var separator = json[i++];
                    if (separator == '}') break;
                    if (separator != ',') throw new InvalidDataException("expected ',' or '}'");
                }
            }
            SkipSpace(json, ref i);
            if (i != json.Length) throw new InvalidDataException("trailing data after object");
            return values;
        }

        private static void SkipSpace(string s, ref int i)
        {
            while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == (char)13 || s[i] == '\n')) i++;
        }

        private static void Expect(string s, ref int i, char expected)
        {
            SkipSpace(s, ref i);
            if (i >= s.Length || s[i] != expected) throw new InvalidDataException("expected '" + expected + "'");
            i++;
        }

        private static object ReadValue(string s, ref int i)
        {
            if (i >= s.Length) throw new InvalidDataException("missing value");
            var c = s[i];
            if (c == '"') return ReadString(s, ref i);
            if (string.CompareOrdinal(s, i, "true", 0, 4) == 0) { i += 4; return true; }
            if (string.CompareOrdinal(s, i, "false", 0, 5) == 0) { i += 5; return false; }
            if (c == '-' || (c >= '0' && c <= '9')) return ReadInt(s, ref i);
            throw new InvalidDataException("unsupported value type");
        }

        private static int ReadInt(string s, ref int i)
        {
            var negative = s[i] == '-';
            if (negative) i++;
            if (i >= s.Length || s[i] < '0' || s[i] > '9') throw new InvalidDataException("invalid integer");
            long value = 0;
            if (s[i] == '0') i++;
            else
                while (i < s.Length && s[i] >= '0' && s[i] <= '9')
                {
                    value = value * 10 + (s[i++] - '0');
                    if (value > 2147483648L) throw new InvalidDataException("integer out of range");
                }
            if (i < s.Length && ((s[i] >= '0' && s[i] <= '9') || s[i] == '.' || s[i] == 'e' || s[i] == 'E'))
                throw new InvalidDataException("integer has a leading zero, fraction or exponent");
            value = negative ? -value : value;
            if (value < int.MinValue || value > int.MaxValue) throw new InvalidDataException("integer out of range");
            return (int)value;
        }

        private static string ReadString(string s, ref int i)
        {
            if (i >= s.Length || s[i] != '"') throw new InvalidDataException("expected string");
            i++;
            var sb = new StringBuilder();
            while (true)
            {
                if (i >= s.Length) throw new InvalidDataException("unterminated string");
                var c = s[i++];
                if (c == '"') return sb.ToString();
                if (c < 0x20) throw new InvalidDataException("raw control character in string");
                if (c != '\\') { sb.Append(c); continue; }
                if (i >= s.Length) throw new InvalidDataException("unterminated escape");
                var e = s[i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append((char)13); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length) throw new InvalidDataException("short unicode escape");
                        var code = 0;
                        for (var n = 0; n < 4; n++)
                        {
                            var h = s[i++];
                            var digit = h >= '0' && h <= '9' ? h - '0' : h >= 'a' && h <= 'f' ? h - 'a' + 10
                                      : h >= 'A' && h <= 'F' ? h - 'A' + 10 : -1;
                            if (digit < 0) throw new InvalidDataException("invalid unicode escape");
                            code = code * 16 + digit;
                        }
                        sb.Append((char)code);
                        break;
                    default: throw new InvalidDataException("invalid escape");
                }
            }
        }
    }
}
