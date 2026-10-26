#nullable enable
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// A tiny read-only JSON reader: just enough to read the art pipeline's own
    /// manifests.
    /// </summary>
    /// <remarks>
    /// Deliberately dependency-free. Unity does not ship System.Text.Json, and
    /// pulling a serializer into the game assembly for two small documents would be
    /// the wrong trade; the project's other modules hand-write JSON for the same
    /// reason. This is a real recursive-descent parser (objects, arrays, strings
    /// with escapes, numbers, booleans, null) rather than a regular-expression
    /// approximation, because a manifest that parses wrongly must fail, not produce
    /// plausible garbage.
    /// </remarks>
    internal static class MinimalJson
    {
        internal static object? Parse(string text)
        {
            var index = 0;
            var value = ParseValue(text, ref index);
            SkipWhitespace(text, ref index);
            if (index != text.Length)
            {
                throw new FormatException("trailing content at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
            return value;
        }

        internal static Dictionary<string, object?> AsObject(object? value)
        {
            return value as Dictionary<string, object?> ?? new Dictionary<string, object?>(StringComparer.Ordinal);
        }

        internal static List<object?> AsArray(object? value)
        {
            return value as List<object?> ?? new List<object?>();
        }

        internal static string AsString(object? value)
        {
            return value as string ?? string.Empty;
        }

        internal static bool AsBool(object? value)
        {
            return value is bool flag && flag;
        }

        internal static float AsFloat(object? value)
        {
            if (value is double number) return (float)number;
            if (value is long integer) return integer;
            return 0f;
        }

        internal static object? Get(object? node, string key)
        {
            return AsObject(node).TryGetValue(key, out var value) ? value : null;
        }

        private static object? ParseValue(string text, ref int index)
        {
            SkipWhitespace(text, ref index);
            if (index >= text.Length) throw new FormatException("unexpected end of input");

            var current = text[index];
            switch (current)
            {
                case '{': return ParseObject(text, ref index);
                case '[': return ParseArray(text, ref index);
                case '"': return ParseString(text, ref index);
                case 't': Expect(text, ref index, "true"); return true;
                case 'f': Expect(text, ref index, "false"); return false;
                case 'n': Expect(text, ref index, "null"); return null;
                default: return ParseNumber(text, ref index);
            }
        }

        private static Dictionary<string, object?> ParseObject(string text, ref int index)
        {
            var result = new Dictionary<string, object?>(StringComparer.Ordinal);
            index++; // '{'
            SkipWhitespace(text, ref index);
            if (index < text.Length && text[index] == '}') { index++; return result; }

            while (true)
            {
                SkipWhitespace(text, ref index);
                if (index >= text.Length || text[index] != '"')
                {
                    throw new FormatException("expected an object key at offset " + index.ToString(CultureInfo.InvariantCulture));
                }
                var key = ParseString(text, ref index);
                SkipWhitespace(text, ref index);
                if (index >= text.Length || text[index] != ':')
                {
                    throw new FormatException("expected ':' at offset " + index.ToString(CultureInfo.InvariantCulture));
                }
                index++;
                result[key] = ParseValue(text, ref index);
                SkipWhitespace(text, ref index);
                if (index >= text.Length) throw new FormatException("unterminated object");
                if (text[index] == ',') { index++; continue; }
                if (text[index] == '}') { index++; return result; }
                throw new FormatException("expected ',' or '}' at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static List<object?> ParseArray(string text, ref int index)
        {
            var result = new List<object?>();
            index++; // '['
            SkipWhitespace(text, ref index);
            if (index < text.Length && text[index] == ']') { index++; return result; }

            while (true)
            {
                result.Add(ParseValue(text, ref index));
                SkipWhitespace(text, ref index);
                if (index >= text.Length) throw new FormatException("unterminated array");
                if (text[index] == ',') { index++; continue; }
                if (text[index] == ']') { index++; return result; }
                throw new FormatException("expected ',' or ']' at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static string ParseString(string text, ref int index)
        {
            var builder = new StringBuilder();
            index++; // opening quote
            while (true)
            {
                if (index >= text.Length) throw new FormatException("unterminated string");
                var current = text[index++];
                if (current == '"') return builder.ToString();
                if (current != '\\') { builder.Append(current); continue; }

                if (index >= text.Length) throw new FormatException("unterminated escape");
                var escape = text[index++];
                switch (escape)
                {
                    case '"': builder.Append('"'); break;
                    case '\\': builder.Append('\\'); break;
                    case '/': builder.Append('/'); break;
                    case 'b': builder.Append('\b'); break;
                    case 'f': builder.Append('\f'); break;
                    case 'n': builder.Append('\n'); break;
                    case 'r': builder.Append('\r'); break;
                    case 't': builder.Append('\t'); break;
                    case 'u':
                        if (index + 4 > text.Length) throw new FormatException("truncated \\u escape");
                        var code = int.Parse(text.Substring(index, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                        builder.Append((char)code);
                        index += 4;
                        break;
                    default: throw new FormatException("unknown escape \\" + escape);
                }
            }
        }

        private static object ParseNumber(string text, ref int index)
        {
            var start = index;
            while (index < text.Length && (char.IsDigit(text[index]) || text[index] == '-' || text[index] == '+' ||
                                           text[index] == '.' || text[index] == 'e' || text[index] == 'E'))
            {
                index++;
            }
            var slice = text.Substring(start, index - start);
            if (slice.Length == 0) throw new FormatException("expected a value at offset " + start.ToString(CultureInfo.InvariantCulture));
            if (slice.IndexOf('.') < 0 && slice.IndexOf('e') < 0 && slice.IndexOf('E') < 0 &&
                long.TryParse(slice, NumberStyles.Integer, CultureInfo.InvariantCulture, out var integer))
            {
                return integer;
            }
            if (double.TryParse(slice, NumberStyles.Float, CultureInfo.InvariantCulture, out var number)) return number;
            throw new FormatException("unreadable number '" + slice + "'");
        }

        private static void Expect(string text, ref int index, string literal)
        {
            if (index + literal.Length > text.Length || string.CompareOrdinal(text, index, literal, 0, literal.Length) != 0)
            {
                throw new FormatException("expected '" + literal + "' at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
            index += literal.Length;
        }

        private static void SkipWhitespace(string text, ref int index)
        {
            while (index < text.Length && (text[index] == ' ' || text[index] == '\t' || text[index] == '\n' || text[index] == '\r'))
            {
                index++;
            }
        }
    }
}
