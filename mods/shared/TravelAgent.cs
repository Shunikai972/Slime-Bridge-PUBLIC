using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace SlimeBridge.Shared
{
    /// <summary>Completion callback of one instruction: success, a short note, and optionally the items that were offered/accepted/removed.</summary>
    internal delegate void TravelDone(bool ok, string detail, Dictionary<string, int>? carry = null);

    /// <summary>One instruction the launcher relayed through BridgeHost for this game.</summary>
    internal sealed class TravelMessage
    {
        public string Kind = "", TransitionId = "", MessageId = "", Destination = "", Source = "", PortalId = "", Reason = "", Policy = "", CarryMode = "NONE";
        public Dictionary<string, int> Carry = new Dictionary<string, int>(StringComparer.Ordinal);

        internal static TravelMessage? Parse(string json)
        {
            var root = MiniJson.Parse(json) as Dictionary<string, object?>;
            if (root == null || !root.TryGetValue("message", out var raw) || !(raw is Dictionary<string, object?> d)) return null;
            string S(string key) => d.TryGetValue(key, out var v) && v is string s ? s : "";
            var m = new TravelMessage
            {
                Kind = S("kind"), TransitionId = S("transition_id"), MessageId = S("message_id"), Destination = S("destination"),
                Source = S("source"), PortalId = S("portal_id"), Reason = S("reason"), Policy = S("policy"),
                CarryMode = d.TryGetValue("carry_mode", out var cm) && cm is string cms && cms.Length > 0 ? cms : "NONE",
            };
            if (d.TryGetValue("carry", out var carry) && carry is Dictionary<string, object?> items)
                foreach (var item in items)
                    if (item.Value is long n && n > 0 && n < 100000) m.Carry[item.Key] = (int)n;
            return m.Kind.Length > 0 && m.TransitionId.Length > 0 && m.MessageId.Length > 0 ? m : null;
        }
    }

    internal interface ITravelTransport
    {
        /// <summary>Never blocks: the newest instruction at the head of the mailbox, or null.</summary>
        TravelMessage? Next(double now);
        void Report(string transitionId, string eventName, string ackId, string detail, Dictionary<string, int>? carry);
        /// <summary>Seconds (same clock as <c>now</c>) of the last successful contact with BridgeHost.</summary>
        double LastContact { get; }
    }

    /// <summary>Game-specific side of a crossing. Each call reports exactly once through <c>done</c>, possibly on a later frame.</summary>
    internal interface ITravelRealm
    {
        void Execute(TravelMessage message, TravelDone done);
        /// <summary>A newer instruction replaced the one being executed.</summary>
        void Cancel();
        /// <summary>True while this game is parked in the background waiting for its player to come back.</summary>
        bool Held { get; }
    }

    /// <summary>
    /// Takes the launcher's instructions (prepare_departure, hold, resume, arrive, abort) at-least-once and reports the matching
    /// event. An instruction stays at the head of the host mailbox until acknowledged, so a lost report is re-sent when it comes
    /// back. If this game stays held while BridgeHost disappears, it resumes by itself: the player is never stranded.
    /// </summary>
    internal sealed class TravelAgent
    {
        internal const double HeldWithoutHostSeconds = 45.0;
        private readonly ITravelTransport _transport;
        private readonly ITravelRealm _realm;
        private readonly Action<string> _log;
        private readonly Dictionary<string, (string Event, string Detail, Dictionary<string, int>? Carry)> _completed = new Dictionary<string, (string, string, Dictionary<string, int>?)>();
        private readonly Queue<string> _completedOrder = new Queue<string>();
        private TravelMessage? _current;
        private bool _hasResult, _resultOk;
        private string _resultDetail = "";
        private Dictionary<string, int>? _resultCarry;
        private double _lastResend = -100;
        private bool _autoResumeStarted;

        internal TravelAgent(ITravelTransport transport, ITravelRealm realm, Action<string> log)
        {
            _transport = transport; _realm = realm; _log = log;
        }

        internal TravelMessage? Current => _current;

        internal static string EventFor(string kind) => kind switch
        {
            "prepare_departure" => "departure_ready",
            "hold" => "held",
            "resume" => "resumed",
            "arrive" => "arrived",
            "abort" => "aborted",
            _ => "failed",
        };

        internal void Tick(double now)
        {
            if (_hasResult && _current != null) Finish(now);
            var message = _transport.Next(now);
            if (message != null) Handle(message, now);
            else if (_current == null && _realm.Held && now - _transport.LastContact > HeldWithoutHostSeconds) AutoResume(now);
        }

        private void Handle(TravelMessage message, double now)
        {
            if (_completed.TryGetValue(message.MessageId, out var done))
            {
                if (now - _lastResend >= 2.0)
                {
                    _lastResend = now;
                    _transport.Report(message.TransitionId, done.Event, message.MessageId, done.Detail, done.Carry);
                }
                return;
            }
            if (_current != null)
            {
                if (_current.MessageId == message.MessageId) return;      // still executing it
                _log("[SlimeBridge] travel: '" + _current.Kind + "' superseded by '" + message.Kind + "'");
                try { _realm.Cancel(); } catch (Exception) { /* a failing cancel must not block the newer instruction */ }
                _hasResult = false;
            }
            _current = message;
            _autoResumeStarted = false;
            _log("[SlimeBridge] travel: " + message.Kind + " (" + message.TransitionId + ")");
            try { _realm.Execute(message, Complete(message)); }
            catch (Exception ex)
            {
                _log("[SlimeBridge] travel: " + message.Kind + " threw " + ex.GetType().Name + ": " + ex.Message);
                Complete(message)(false, ex.GetType().Name);
            }
        }

        private TravelDone Complete(TravelMessage message)
        {
            return (ok, detail, carry) =>
            {
                if (_current == null || _current.MessageId != message.MessageId || _hasResult) return;
                _resultOk = ok; _resultDetail = detail ?? ""; _resultCarry = carry; _hasResult = true;
            };
        }

        private void Finish(double now)
        {
            var message = _current!;
            var eventName = _resultOk ? EventFor(message.Kind) : "failed";
            Remember(message.MessageId, eventName, _resultDetail, _resultCarry);
            _transport.Report(message.TransitionId, eventName, message.MessageId, _resultDetail, _resultCarry);
            _log("[SlimeBridge] travel: " + message.Kind + " -> " + eventName + (_resultDetail.Length > 0 ? " (" + _resultDetail + ")" : ""));
            _current = null; _hasResult = false; _lastResend = now;
        }

        private void Remember(string id, string eventName, string detail, Dictionary<string, int>? carry)
        {
            _completed[id] = (eventName, detail, carry);
            _completedOrder.Enqueue(id);
            while (_completedOrder.Count > 32) _completed.Remove(_completedOrder.Dequeue());
        }

        private void AutoResume(double now)
        {
            if (_autoResumeStarted) return;
            _autoResumeStarted = true;
            _log("[SlimeBridge] travel: BridgeHost has been silent while this game is held; resuming on our own");
            var local = new TravelMessage { Kind = "resume", TransitionId = "local", MessageId = "local-" + now.ToString("F0", CultureInfo.InvariantCulture), Reason = "host_lost" };
            _current = local;
            try { _realm.Execute(local, (ok, detail, carry) => { _current = null; _autoResumeStarted = false; }); }
            catch (Exception) { _current = null; _autoResumeStarted = false; }
        }
    }

    /// <summary>BridgeHost mailbox over plain HTTP (works under Mono/net472 and Il2Cpp/net6 alike). Polling never touches the main thread.</summary>
    internal sealed class HttpTravelTransport : ITravelTransport
    {
        private readonly string _base, _token, _realm;
        private int _inFlight;
        private double _nextPoll;
        private TravelMessage? _latest;
        private double _lastContact;
        private readonly Func<double> _clock;
        internal double PollSeconds = 0.5, SlowPollSeconds = 2.0;
        /// <summary>Called (from a worker thread) when a crossing request could not reach BridgeHost.</summary>
        internal Action? CrossingFailed = null;
        internal bool Slow { get; set; }

        internal HttpTravelTransport(string baseUrl, string token, string realm, Func<double> clock)
        {
            _base = baseUrl.TrimEnd('/'); _token = token; _realm = realm; _clock = clock; _lastContact = clock();
        }

        public double LastContact => Volatile.Read(ref _lastContact);

        public TravelMessage? Next(double now)
        {
            if (now >= _nextPoll && Interlocked.Exchange(ref _inFlight, 1) == 0)
            {
                _nextPoll = now + (Slow ? SlowPollSeconds : PollSeconds);
                Task.Run(Poll);
            }
            return Volatile.Read(ref _latest);
        }

        private void Poll()
        {
            try
            {
                var request = Create("/v1/travel/next?realm=" + _realm, "GET", 2000);
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    Volatile.Write(ref _latest, TravelMessage.Parse(reader.ReadToEnd()));
                    Volatile.Write(ref _lastContact, _clock());
                }
            }
            catch (Exception) { /* BridgeHost away: keep the last answer, LastContact ages */ }
            finally { Interlocked.Exchange(ref _inFlight, 0); }
        }

        /// <summary>A portal in this game asks the launcher to take the player to the other game (the launcher decides and drives it).</summary>
        internal void RequestCrossing(string portalId, string saveId)
        {
            var body = "{\"protocol_version\":1,\"message_type\":\"portal_cross\",\"realm\":\"" + _realm + "\",\"portal_id\":\"" + Esc(portalId) +
                       "\",\"save_id\":\"" + Esc(saveId) + "\",\"request_id\":\"" + Guid.NewGuid().ToString("N") + "\"}";
            Task.Run(() =>
            {
                try
                {
                    var request = Create("/v1/portal/cross", "POST", 3000);
                    request.ContentType = "application/json";
                    var payload = Encoding.UTF8.GetBytes(body);
                    request.ContentLength = payload.Length;
                    using (var stream = request.GetRequestStream()) stream.Write(payload, 0, payload.Length);
                    using (var response = (HttpWebResponse)request.GetResponse()) { }
                }
                catch (Exception) { CrossingFailed?.Invoke(); }       // the realm tells the player (BridgeHost unreachable)
            });
        }

        public void Report(string transitionId, string eventName, string ackId, string detail, Dictionary<string, int>? carry)
        {
            var body = "{\"protocol_version\":1,\"message_type\":\"travel_event\",\"realm\":\"" + _realm + "\",\"transition_id\":\"" + Esc(transitionId) +
                       "\",\"event\":\"" + Esc(eventName) + "\"" + (ackId.Length > 0 && !ackId.StartsWith("local", StringComparison.Ordinal) ? ",\"ack_id\":\"" + Esc(ackId) + "\"" : "") +
                       (detail.Length > 0 ? ",\"detail\":\"" + Esc(detail.Length > 190 ? detail.Substring(0, 190) : detail) + "\"" : "") + CarryJson(carry) + "}";
            Task.Run(() =>
            {
                for (var attempt = 0; attempt < 3; attempt++)
                {
                    try
                    {
                        var request = Create("/v1/travel/event", "POST", 3000);
                        request.ContentType = "application/json";
                        var payload = Encoding.UTF8.GetBytes(body);
                        request.ContentLength = payload.Length;
                        using (var stream = request.GetRequestStream()) stream.Write(payload, 0, payload.Length);
                        using (var response = (HttpWebResponse)request.GetResponse()) { }
                        Volatile.Write(ref _lastContact, _clock());
                        // The acknowledged head is gone from the mailbox; do not keep re-serving the stale copy.
                        var latest = Volatile.Read(ref _latest);
                        if (latest != null && latest.MessageId == ackId) Interlocked.CompareExchange(ref _latest, null, latest);
                        return;
                    }
                    catch (Exception) { Thread.Sleep(250); }
                }
            });
        }

        private HttpWebRequest Create(string path, string method, int timeoutMs)
        {
#pragma warning disable SYSLIB0014
            var request = (HttpWebRequest)WebRequest.Create(_base + path);
#pragma warning restore SYSLIB0014
            request.Method = method;
            request.Headers[HttpRequestHeader.Authorization] = "Bearer " + _token;
            request.Proxy = null; request.Timeout = timeoutMs; request.ReadWriteTimeout = timeoutMs;
            return request;
        }

        private static string CarryJson(Dictionary<string, int>? carry)
        {
            if (carry == null || carry.Count == 0) return "";
            var sb = new StringBuilder(",\"carry\":{");
            var first = true;
            foreach (var item in carry)
            {
                if (item.Value < 1 || item.Value > 9999) continue;
                if (!first) sb.Append(',');
                first = false;
                sb.Append('"').Append(Esc(item.Key)).Append("\":").Append(item.Value.ToString(CultureInfo.InvariantCulture));
            }
            return first ? "" : sb.Append('}').ToString();
        }

        private static string Esc(string value)
        {
            var sb = new StringBuilder(value.Length + 4);
            foreach (var c in value)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append(' ');
                else sb.Append(c);
            }
            return sb.ToString();
        }
    }
}
