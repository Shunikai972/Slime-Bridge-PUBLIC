using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SbRuntime
{
    /// <summary>Small strict JSON parser (RFC 8259: no NaN/Infinity, no trailing commas). Objects become
    /// Dictionary&lt;string,object&gt;, arrays List&lt;object&gt;, numbers double (long when integral), plus string/bool/null.</summary>
    public static class Json
    {
        public static object Parse(string text)
        {
            if (text == null) throw new FormatException("JSON is null");
            if (text.Length > 0 && text[0] == '﻿') text = text.Substring(1);
            int i = 0;
            var v = Value(text, ref i, 0);
            Ws(text, ref i);
            if (i != text.Length) throw new FormatException("trailing characters at " + i);
            return v;
        }

        static void Ws(string s, ref int i) { while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\r' || s[i] == '\n')) i++; }

        static object Value(string s, ref int i, int depth)
        {
            if (depth > 64) throw new FormatException("nesting limit 64 exceeded");
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end");
            char c = s[i];
            if (c == '{') return Obj(s, ref i, depth);
            if (c == '[') return Arr(s, ref i, depth);
            if (c == '"') return Str(s, ref i);
            if (c == 't') return Lit(s, ref i, "true", true);
            if (c == 'f') return Lit(s, ref i, "false", false);
            if (c == 'n') return Lit(s, ref i, "null", null);
            return Num(s, ref i);
        }

        static object Lit(string s, ref int i, string word, object value)
        {
            if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0) throw new FormatException("invalid literal at " + i);
            i += word.Length;
            return value;
        }

        static object Num(string s, ref int i)
        {
            int st = i;
            if (i < s.Length && s[i] == '-') i++;
            if (i >= s.Length || !char.IsDigit(s[i])) throw new FormatException("invalid number at " + st);
            if (s[i] == '0') i++; else while (i < s.Length && char.IsDigit(s[i])) i++;
            bool integral = true;
            if (i < s.Length && s[i] == '.') { integral = false; i++; if (i >= s.Length || !char.IsDigit(s[i])) throw new FormatException("invalid fraction"); while (i < s.Length && char.IsDigit(s[i])) i++; }
            if (i < s.Length && (s[i] == 'e' || s[i] == 'E'))
            {
                integral = false; i++;
                if (i < s.Length && (s[i] == '+' || s[i] == '-')) i++;
                if (i >= s.Length || !char.IsDigit(s[i])) throw new FormatException("invalid exponent");
                while (i < s.Length && char.IsDigit(s[i])) i++;
            }
            string t = s.Substring(st, i - st);
            if (integral && long.TryParse(t, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out long l)) return l;
            double d = double.Parse(t, NumberStyles.Float, CultureInfo.InvariantCulture);
            if (double.IsInfinity(d) || double.IsNaN(d)) throw new FormatException("number out of range at " + st);
            return d;
        }

        static string Str(string s, ref int i)
        {
            i++;
            var sb = new StringBuilder();
            while (true)
            {
                if (i >= s.Length) throw new FormatException("unterminated string");
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c < 32) throw new FormatException("control character in string");
                if (c != '\\') { sb.Append(c); continue; }
                if (i >= s.Length) throw new FormatException("unterminated escape");
                char e = s[i++];
                switch (e)
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
                        if (i + 4 > s.Length) throw new FormatException("bad unicode escape");
                        sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16)); i += 4; break;
                    default: throw new FormatException("invalid escape");
                }
            }
        }

        static List<object> Arr(string s, ref int i, int depth)
        {
            i++;
            var list = new List<object>();
            Ws(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return list; }
            while (true)
            {
                list.Add(Value(s, ref i, depth + 1));
                Ws(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated array");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return list; }
                throw new FormatException("expected , or ] at " + i);
            }
        }

        static Dictionary<string, object> Obj(string s, ref int i, int depth)
        {
            i++;
            var d = new Dictionary<string, object>(StringComparer.Ordinal);
            Ws(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }
            while (true)
            {
                Ws(s, ref i);
                if (i >= s.Length || s[i] != '"') throw new FormatException("expected property name at " + i);
                string k = Str(s, ref i);
                Ws(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected : at " + i);
                i++;
                d[k] = Value(s, ref i, depth + 1);
                Ws(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated object");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return d; }
                throw new FormatException("expected , or } at " + i);
            }
        }

        // ---- typed accessors -------------------------------------------------------------------------
        public static Dictionary<string, object> O(object o, string at) { var d = o as Dictionary<string, object>; if (d == null) throw new FormatException(at + ": object expected"); return d; }
        public static List<object> A(object o, string at) { var l = o as List<object>; if (l == null) throw new FormatException(at + ": array expected"); return l; }
        public static object Get(Dictionary<string, object> d, string k) { if (!d.TryGetValue(k, out var v)) throw new FormatException("missing property " + k); return v; }
        public static string S(Dictionary<string, object> d, string k, string def = null) { if (!d.TryGetValue(k, out var v) || v == null) { if (def != null) return def; throw new FormatException("missing string " + k); } return (string)v; }
        public static double N(object o) { if (o is long l) return l; if (o is double dd) return dd; throw new FormatException("number expected"); }
        public static double N(Dictionary<string, object> d, string k, double? def = null) { if (!d.TryGetValue(k, out var v) || v == null) { if (def.HasValue) return def.Value; throw new FormatException("missing number " + k); } return N(v); }
        public static int I(Dictionary<string, object> d, string k, int? def = null) { return (int)N(d, k, def); }
        public static bool B(Dictionary<string, object> d, string k, bool def = false) { return d.TryGetValue(k, out var v) && v is bool b ? b : def; }
        public static float[] F(object o, int n, string at) { var l = A(o, at); if (n >= 0 && l.Count != n) throw new FormatException(at + ": expected " + n + " numbers"); var r = new float[l.Count]; for (int i = 0; i < r.Length; i++) r[i] = (float)N(l[i]); return r; }
    }
}
