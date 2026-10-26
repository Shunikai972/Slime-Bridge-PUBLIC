using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Minimal strict JSON reader for Bridge-owned documents (imported-species spec, one-shot ops).
    /// Deliberately not UnityEngine.JsonUtility: that silently drops fields it cannot bind, which
    /// would turn a typo'd Bridge spec into a silently mis-registered species. This parser returns
    /// Dictionary&lt;string, object?&gt;/List&lt;object?&gt;/string/bool/long/double/null and the model
    /// loader validates every field fail-closed.
    /// </summary>
    public static class MiniJson
    {
        public static object? Parse(string text)
        {
            var reader = new Reader(text);
            reader.SkipWhitespace();
            var value = reader.ReadValue(0);
            reader.SkipWhitespace();
            if (reader.Position != reader.Length) throw new FormatException("trailing characters after JSON value");
            return value;
        }

        private sealed class Reader
        {
            private readonly string _text;
            public int Position;
            public int Length => _text.Length;

            public Reader(string text) { _text = text ?? throw new ArgumentNullException(nameof(text)); }

            public void SkipWhitespace()
            {
                while (Position < _text.Length && (_text[Position] == ' ' || _text[Position] == '\t' ||
                                                   _text[Position] == '\n' || _text[Position] == '\r')) Position++;
            }

            public object? ReadValue(int depth)
            {
                if (depth > 32) throw new FormatException("JSON nesting too deep");
                if (Position >= _text.Length) throw new FormatException("unexpected end of JSON");
                var c = _text[Position];
                switch (c)
                {
                    case '{': return ReadObject(depth);
                    case '[': return ReadArray(depth);
                    case '"': return ReadString();
                    case 't': Expect("true"); return true;
                    case 'f': Expect("false"); return false;
                    case 'n': Expect("null"); return null;
                    default: return ReadNumber();
                }
            }

            private Dictionary<string, object?> ReadObject(int depth)
            {
                var result = new Dictionary<string, object?>(StringComparer.Ordinal);
                Position++; // {
                SkipWhitespace();
                if (Position < _text.Length && _text[Position] == '}') { Position++; return result; }
                while (true)
                {
                    SkipWhitespace();
                    if (Position >= _text.Length || _text[Position] != '"')
                        throw new FormatException("object key must be a string");
                    var key = ReadString();
                    SkipWhitespace();
                    if (Position >= _text.Length || _text[Position] != ':') throw new FormatException("missing ':' after object key");
                    Position++;
                    SkipWhitespace();
                    if (result.ContainsKey(key)) throw new FormatException("duplicate object key " + key);
                    result[key] = ReadValue(depth + 1);
                    SkipWhitespace();
                    if (Position >= _text.Length) throw new FormatException("unterminated object");
                    var next = _text[Position];
                    if (next == ',') { Position++; continue; }
                    if (next == '}') { Position++; return result; }
                    throw new FormatException("expected ',' or '}' in object");
                }
            }

            private List<object?> ReadArray(int depth)
            {
                var result = new List<object?>();
                Position++; // [
                SkipWhitespace();
                if (Position < _text.Length && _text[Position] == ']') { Position++; return result; }
                while (true)
                {
                    SkipWhitespace();
                    result.Add(ReadValue(depth + 1));
                    SkipWhitespace();
                    if (Position >= _text.Length) throw new FormatException("unterminated array");
                    var next = _text[Position];
                    if (next == ',') { Position++; continue; }
                    if (next == ']') { Position++; return result; }
                    throw new FormatException("expected ',' or ']' in array");
                }
            }

            private string ReadString()
            {
                var sb = new StringBuilder();
                Position++; // opening quote
                while (true)
                {
                    if (Position >= _text.Length) throw new FormatException("unterminated string");
                    var c = _text[Position++];
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    if (Position >= _text.Length) throw new FormatException("unterminated escape");
                    var esc = _text[Position++];
                    switch (esc)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (Position + 4 > _text.Length) throw new FormatException("bad unicode escape");
                            var hex = _text.Substring(Position, 4);
                            if (!ushort.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var code))
                                throw new FormatException("bad unicode escape " + hex);
                            sb.Append((char)code);
                            Position += 4;
                            break;
                        default: throw new FormatException("bad escape character");
                    }
                }
            }

            private object ReadNumber()
            {
                var start = Position;
                if (Position < _text.Length && _text[Position] == '-') Position++;
                while (Position < _text.Length && _text[Position] >= '0' && _text[Position] <= '9') Position++;
                var isDouble = false;
                if (Position < _text.Length && _text[Position] == '.')
                {
                    isDouble = true;
                    Position++;
                    while (Position < _text.Length && _text[Position] >= '0' && _text[Position] <= '9') Position++;
                }
                if (Position < _text.Length && (_text[Position] == 'e' || _text[Position] == 'E'))
                {
                    isDouble = true;
                    Position++;
                    if (Position < _text.Length && (_text[Position] == '+' || _text[Position] == '-')) Position++;
                    while (Position < _text.Length && _text[Position] >= '0' && _text[Position] <= '9') Position++;
                }
                var token = _text.Substring(start, Position - start);
                if (token.Length == 0 || token == "-") throw new FormatException("invalid number");
                if (!isDouble && long.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l)) return l;
                if (double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) return d;
                throw new FormatException("invalid number " + token);
            }

            private void Expect(string literal)
            {
                if (Position + literal.Length > _text.Length ||
                    string.CompareOrdinal(_text, Position, literal, 0, literal.Length) != 0)
                    throw new FormatException("expected " + literal);
                Position += literal.Length;
            }
        }
    }
}
