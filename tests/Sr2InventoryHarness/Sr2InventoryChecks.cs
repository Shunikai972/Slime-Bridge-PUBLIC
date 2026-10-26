// Linked production InventoryAgent + authored fixtures. No native game code executes.
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using SlimeBridge.Shared;
using SlimeBridge.SR2;
internal static class Program
{
    private static readonly List<object> Wire = new();
    private static int Main(string[] args)
    {
        var tests = new List<(string Name, Action Test)>();
        foreach (var kind in new[] { "add", "remove", "lab_give" })
            foreach (var item in new[] { "slime.pink", "plort.pink", "slime.ringtail" })
                tests.Add(($"{kind}/{item} requires full disk proof", () => Success(kind, item)));
        tests.AddRange(PersistenceChecks.All());
        foreach (var change in new[] { "none", "live-count", "payload", "disk", "world", "provider", "root", "latest", "tie", "missing-file", "latest-during-read" })
            tests.Add(("restart replay " + change, () => Replay(change)));
        tests.Add(("Ringtail current-PID replay", () => Replay("none", "slime.ringtail")));
        tests.Add(("Ringtail changed-count replay fails closed", () => Replay("live-count", "slime.ringtail")));
        foreach (var item in new[] { "slime.unknown", "slime.rad", "plort.ringtail" })
            tests.Add(("unsupported type has no fallback: " + item, () => {
                using var w = new FixtureWorld((0,20,true,true));
                var r = w.Run(w.Command(item:item));
                Require(!r.Ok && !r.Applied && !r.Uncertain && !w.HasIntent && w.Ammo.Mutations.Count==0,
                    "unsupported canonical item mutated a Pink native slot");
            }));
        foreach (var missing in new[] { false, true })
            tests.Add(("Ringtail unresolved or wrong native reference fails closed: " + missing, () => {
                using var w = new FixtureWorld((0,20,true,true));
                if (missing) w.Lookup._identifiableTypeByRefId!.Items.Remove(w.Ringtail.ReferenceId);
                else w.Lookup._identifiableTypeByRefId![w.Ringtail.ReferenceId] = w.Pink;
                var r = w.Run(w.Command(item:"slime.ringtail"));
                Require(!r.Ok && !r.Applied && !w.HasIntent && w.Ammo.Mutations.Count==0,
                    "missing or mismatched Ringtail resolution reached a native mutation");
            }));
        tests.Add(("old success missing disk proof replay fails closed", () => LegacyReplay()));
        tests.Add(("receipt command-count mismatch never mutates", () => ReplayBinding()));
        tests.Add(("intent-only replay never repeats native effect", () => IntentOnly()));
        tests.Add(("query remains nonmutating", () => { using var w=new FixtureWorld((4,20,true,true)); var r=w.Run(w.Command("query",0)); Require(r.Ok&&!r.Applied&&w.Director.SaveCalls==0&&w.Ammo.Mutations.Count==0,"query touched native effects"); }));
        tests.Add(("missing provider refuses before native mutation", () => { using var w=new FixtureWorld((0,20,true,true)); w.Director.StorageProvider=null; var r=w.Run(w.Command()); Require(!r.Ok&&!r.Applied&&!r.Uncertain&&!w.HasIntent&&w.Ammo.Mutations.Count==0,"no storage reached native mutation"); }));
        tests.Add(("non-file provider refuses before native mutation", () => { using var w=new FixtureWorld((0,20,true,true)); w.Director.StorageProvider=new Il2Cpp.StorageProvider(); var r=w.Run(w.Command()); Require(!r.Ok&&!r.Applied&&!w.HasIntent&&w.Ammo.Mutations.Count==0,"foreign provider reached mutation"); }));
        tests.Add(("new revision during readback invalidates proof", () => Bad(w=>w.Provider.OnRead=()=>w.Provider.AddSyntheticRevision(FixtureWorld.Identity+"_2",w.Payload()))));
        tests.Add(("provider replacement during readback invalidates proof", () => Bad(w=>w.Provider.OnRead=()=>w.Director.StorageProvider=new Il2Cpp.FileStorageProvider(w.Root))));
        tests.Add(("root change during readback invalidates proof", () => Bad(w=>w.Provider.OnRead=()=>w.Provider.savePath=w.Root+"-changed")));
        tests.Add(("director replacement during readback invalidates proof", () => Bad(w=>w.Provider.OnRead=()=>Il2Cpp.GameContext.Instance!.AutoSaveDirector=new Il2Cpp.AutoSaveDirector())));
        tests.Add(("saved pointer replacement during readback invalidates proof", () => Bad(w=>w.Provider.OnRead=()=>w.Director._savedGame=new Il2Cpp.SavedGame())));
        var failures=0; var skips=0;
        Console.WriteLine("SYNTHETIC SR2 inventory proof/replay harness; not native runtime or save-format evidence.");
        foreach(var (name,test) in tests) { try { test(); Console.WriteLine("PASS "+name); } catch(PersistenceChecks.UnavailableFixtureException ex) { skips++; Console.WriteLine("SKIP "+name+": "+ex.Message); } catch(Exception ex) { failures++; Console.WriteLine("FAIL "+name+": "+ex.Message); } }
        if(args.Length>0) File.WriteAllText(args[0],JsonSerializer.Serialize(Wire,new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine($"{tests.Count-failures-skips}/{tests.Count} checks passed; failures={failures}; skipped={skips}");
        return failures==0?0:1;
    }
    private static void Success(string kind,string item)
    {
        using var w=new FixtureWorld((kind=="remove"?8:0,20,true,true));
        if(kind=="remove") w.Ammo.Slots[0]!.Id=item=="plort.pink" ? w.Plort : item=="slime.ringtail" ? w.Ringtail : w.Pink;
        if(kind=="lab_give") w.Agent=Create(w.Journal,true);
        var c=w.Command(kind,3,item:item); var r=w.Run(c); var json=r.ToJson(Environment.ProcessId);
        Wire.Add(new { scenario="success",kind,item,wire=JsonSerializer.Deserialize<JsonElement>(json) });
        Require(r.Ok&&r.Applied&&!r.Uncertain&&r.Persisted&&r.Verified==true&&r.SaveFile==FixtureWorld.Identity+"_1"&&r.Sha256.Length==64,"native boolean was not replaced with verified full payload/disk proof");
        Require(w.Ammo.Mutations.Count==1&&w.Director.SaveCalls==1,"duplicate native/save call");
        var expectedType = item=="slime.ringtail" ? w.Ringtail : item=="plort.pink" ? w.Plort : w.Pink;
        Require(w.Ammo.Slots[0]?.Id?.Pointer==expectedType.Pointer && w.Ammo.Slots[0]!.Count==(kind=="remove"?5:3),
            "canonical identity was silently replaced by another native inventory type");
    }
    private static void Bad(Action<FixtureWorld> change)
    {
        using var w=new FixtureWorld((0,20,true,true)); change(w); var r=w.Run(w.Command(count:3));
        Require(!r.Ok&&r.Applied&&r.Uncertain&&!r.Persisted&&r.Verified!=true,"stale world/storage passed persistence");
    }
    private static void Replay(string change, string item = "slime.pink")
    {
        using var w=new FixtureWorld((0,20,true,true)); var c=w.Command(count:3, item:item); var first=w.Run(c);
        // Reopen the real durable journal with an explicit historical process ID.
        var historical=first.ToJson(424242);
        File.WriteAllText(w.JournalPath,"I\t"+c.OpId+"\nD\t"+c.OpId+"\t"+historical+"\n");
        var reopened=OpJournal.Open(w.JournalPath,out var error)??throw new InvalidOperationException(error);
        if(change=="live-count") w.Ammo.Slots[0]!.Count++;
        if(change=="payload") w.Provider.Payloads[first.SaveFile.Length>0?first.SaveFile:FixtureWorld.Identity+"_1"]=w.Payload(entries:new (int,int)?[]{(0,99)});
        if(change=="disk") File.WriteAllBytes(Path.Combine(w.Root,FixtureWorld.Identity+"_1.sav"),new byte[]{1});
        if(change=="world") w.Director._currentGameName="other";
        if(change=="provider") w.Director.StorageProvider=new Il2Cpp.StorageProvider();
        if(change=="root") w.Provider.savePath=w.Root+"-changed";
        if(change=="latest-during-read") w.Provider.OnRead=()=>w.Provider.AddSyntheticRevision(FixtureWorld.Identity+"_2",w.Payload());
        if(change=="latest") w.Provider.AddSyntheticRevision(FixtureWorld.Identity+"_2",w.Payload());
        if(change=="tie") w.Provider.Summaries[FixtureWorld.Identity+"_0"].SaveTimestamp=w.Provider.Summaries[FixtureWorld.Identity+"_1"].SaveTimestamp;
        if(change=="missing-file") w.Provider.Names.Remove(FixtureWorld.Identity+"_1");
        var before=w.JournalBytes(); var mutations=w.Ammo.Mutations.Count; var saves=w.Director.SaveCalls;
        var json=TickWire(reopened,c); var r=JsonSerializer.Deserialize<JsonElement>(json);
        Wire.Add(new { scenario="replay",change,wire=r,historical_pid=424242 });
        Require(r.GetProperty("pid").GetInt32()==Environment.ProcessId,"Tick reposted historical PID");
        Require(w.Ammo.Mutations.Count==mutations&&w.Director.SaveCalls==saves&&before.SequenceEqual(w.JournalBytes()),"replay repeated native effects or rewrote terminal journal");
        Require(change=="none" ? r.GetProperty("ok").GetBoolean()&&r.GetProperty("verified").GetBoolean()&&!r.GetProperty("uncertain").GetBoolean() : !r.GetProperty("ok").GetBoolean()&&r.GetProperty("uncertain").GetBoolean()&&!r.GetProperty("persisted").GetBoolean()&&!r.GetProperty("verified").GetBoolean(),"replay after-state was not freshly fail-closed reverified");
    }
    private static void LegacyReplay()
    {
        using var w=new FixtureWorld((3,20,true,true)); var c=w.Command(count:3);
        w.Journal.WriteIntent(c.OpId); w.Journal.WriteDone(c.OpId,new CommandResult{Realm="SR2",OpId=c.OpId,Kind=c.Kind,Item=c.Item,Count=3,SaveId=FixtureWorld.Identity,Before=0,After=3,Ok=true,Applied=true,Persisted=true}.ToJson(424242));
        var json=TickWire(w.Journal,c); var r=JsonSerializer.Deserialize<JsonElement>(json);
        Require(!r.GetProperty("ok").GetBoolean()&&r.GetProperty("uncertain").GetBoolean()&&r.GetProperty("pid").GetInt32()==Environment.ProcessId&&w.Ammo.Mutations.Count==0&&w.Director.SaveCalls==0,"legacy boolean-only receipt was promoted or redispatched");
    }
    private static void ReplayBinding()
    {
        using var w=new FixtureWorld((0,20,true,true)); var c=w.Command(count:3); w.Run(c); c.Count=2;
        var r=JsonSerializer.Deserialize<JsonElement>(TickWire(w.Journal,c));
        Require(!r.GetProperty("ok").GetBoolean()&&r.GetProperty("uncertain").GetBoolean()&&w.Ammo.Mutations.Count==1&&w.Director.SaveCalls==1,"mismatched journal command accepted or repeated");
    }
    private static void IntentOnly()
    {
        using var w=new FixtureWorld((3,20,true,true)); var c=w.Command(count:3); w.Journal.WriteIntent(c.OpId);
        var r=JsonSerializer.Deserialize<JsonElement>(TickWire(w.Journal,c));
        Require(!r.GetProperty("ok").GetBoolean()&&r.GetProperty("uncertain").GetBoolean()&&w.Ammo.Mutations.Count==0&&w.Director.SaveCalls==0,"intent-only redispatched");
    }
    internal static InventoryAgent Create(OpJournal? journal,bool lab=false,string url="http://127.0.0.1:1") => (InventoryAgent)Activator.CreateInstance(typeof(InventoryAgent),BindingFlags.Instance|BindingFlags.NonPublic,null,new object?[]{url,"synthetic-token",lab,journal,(Action<string>)(_=>{})},null)!;
    internal static CommandResult Invoke(InventoryAgent agent,BridgeCommand command)
    {
        try { return (CommandResult)typeof(InventoryAgent).GetMethod("Execute",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(agent,new object[]{command})!; }
        catch(TargetInvocationException ex) { throw ex.InnerException!; }
    }
    private static string TickWire(OpJournal journal,BridgeCommand command)
    {
        using var reservation=new TcpListener(IPAddress.Loopback,0); reservation.Start(); var port=((IPEndPoint)reservation.LocalEndpoint).Port; reservation.Stop();
        using var listener=new HttpListener(); listener.Prefixes.Add($"http://127.0.0.1:{port}/"); listener.Start();
        var agent=Create(journal,url:$"http://127.0.0.1:{port}");
        typeof(InventoryAgent).GetField("_incoming",BindingFlags.Instance|BindingFlags.NonPublic)!.SetValue(agent,command);
        var received=listener.GetContextAsync(); agent.Tick();
        if(!received.Wait(TimeSpan.FromSeconds(5))) throw new InvalidOperationException("production POST did not arrive");
        var context=received.Result; using var reader=new StreamReader(context.Request.InputStream,Encoding.UTF8); var json=reader.ReadToEnd(); context.Response.StatusCode=200; context.Response.Close();
        var posting=typeof(InventoryAgent).GetField("_posting",BindingFlags.Instance|BindingFlags.NonPublic)!;
        if(!SpinWait.SpinUntil(()=> (int)posting.GetValue(agent)! == 0,TimeSpan.FromSeconds(5))) throw new InvalidOperationException("POST did not finish");
        agent.Shutdown(); return json;
    }
    internal static void Require(bool condition,string message) { if(!condition) throw new InvalidOperationException(message); }
}
