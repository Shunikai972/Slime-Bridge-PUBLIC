using System;
using System.Collections.Generic;
using System.Net;
using System.Reflection;
using SlimeBridge.Shared;

static class Program
{
    static int checks, failures;
    static void Require(bool ok, string name) { checks++; if (!ok) { failures++; Console.WriteLine("FAIL " + name); } }
    // Catch all failures so the negative control never raises a Windows unhandled-.NET crash dialog.
    static int Main()
    {
        try
        {
            var transport = new Transport(); var realm = new Realm();
            var agent = new TravelAgent(transport, realm, _ => { });
            transport.Message = new TravelMessage { Kind = "hold", TransitionId = "t1", MessageId = "m1" };
            agent.Tick(0);
            Require(transport.Reports == 1, "synchronous completion reports in same tick, not next throttled frame");
            agent.Tick(.1);
            Require(realm.Executions == 1 && transport.Reports == 1, "replayed completed head does not execute again");
            agent.Tick(2.1);
            Require(transport.Reports == 2, "lost acknowledgement still resends");
            transport.Message = new TravelMessage { Kind = "arrive", TransitionId = "t2", MessageId = "m2" };
            realm.Async = true; agent.Tick(3);
            Require(transport.Reports == 2, "asynchronous arrival is not acknowledged early");
            realm.Pending!(true, "ready"); agent.Tick(3.1);
            Require(transport.Reports == 3, "asynchronous completion acknowledged on next main-thread tick");
            var http = new HttpTravelTransport("http://127.0.0.1:45821", "synthetic-only", "SR1", () => 0);
            Require(http.PollSeconds <= .1, "mailbox scheduling budget at most100ms");
            var create = typeof(HttpTravelTransport).GetMethod("Create", BindingFlags.Instance | BindingFlags.NonPublic)!;
            var request = (HttpWebRequest)create.Invoke(http, new object[] { "/v1/travel/event", "POST", 2000 })!;
            Require(!request.ServicePoint.Expect100Continue, "loopback POST must not wait for100Continue");
            Require(!request.ServicePoint.UseNagleAlgorithm, "tiny loopback commands must not wait for Nagle");
            Require(request.Proxy == null, "loopback request still bypasses proxy");
            Require(request.Timeout == 2000 && request.ReadWriteTimeout == 2000, "timeouts preserved");
        }
        catch (Exception error) { failures++; Console.WriteLine("HARNESS_ERROR " + error); }
        Console.WriteLine("CHECKS=" + checks + " FAILURES=" + failures);
        return failures == 0 ? 0 : 1;
    }
    sealed class Realm : ITravelRealm
    {
        public int Executions; public bool Async; public TravelDone? Pending;
        public bool Held => false;
        public void Cancel() { Pending = null; }
        public void Execute(TravelMessage message, TravelDone done)
        { Executions++; if (Async) Pending = done; else done(true, "ready"); }
    }
    sealed class Transport : ITravelTransport
    {
        public TravelMessage? Message; public int Reports;
        public double LastContact => 0;
        public TravelMessage? Next(double now) => Message;
        public void Report(string transitionId, string eventName, string ackId, string detail, Dictionary<string,int>? carry) { Reports++; }
    }
}
