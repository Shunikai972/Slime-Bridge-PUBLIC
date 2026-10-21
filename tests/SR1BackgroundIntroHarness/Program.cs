using System.Reflection;
using SlimeBridge.SR1;
using SlimeBridge.Shared;
using DG.Tweening;
using UnityEngine;

internal static class Program
{
    internal const BindingFlags All = BindingFlags.Instance|BindingFlags.Static|BindingFlags.NonPublic|BindingFlags.Public;
    private const string Id="1234567890abcdef1234567890abcdef";
    internal static void Check(bool ok,string why) { if(!ok) throw new Exception("ASSERT: "+why); }
    internal static void Set(object instance,string field,object? value) => instance.GetType().GetField(field,All)!.SetValue(instance,value);
    internal static T Get<T>(object instance,string field) => (T)instance.GetType().GetField(field,All)!.GetValue(instance)!;
    internal static bool Background=true;
    internal static IntroUI Owner=new();
    internal static Sequence Seq=new();
    internal static InventoryAgent Agent=null!;

    private static void Prepare()
    {
        var journal=OpJournal.Open(Path.Combine(Environment.GetEnvironmentVariable("TMPDIR")!,Guid.NewGuid().ToString("N")),out var error);
        Check(journal!=null,"synthetic journal opened: "+error);
        SRSingleton<GameContext>.Instance=new();
        var ctx=new SceneContext {gameObject=new GameObject()}; SRSingleton<SceneContext>.Instance=ctx;
        ctx.GameModeConfig.Settings.newGamePrefab=Root(out var template);
        var constructor=typeof(InventoryAgent).GetConstructors(All).Single(c=>!c.IsStatic);
        Agent=(InventoryAgent)constructor.Invoke(new object?[]{"http://127.0.0.1:1","synthetic",false,true,journal});
        // Inject only the missing engine metadata/focus boundary. No production guard bypass or #if.
        var skipper=typeof(InventoryAgent).Assembly.GetType("SlimeBridge.SR1.BackgroundIntroSkipper");
        if(skipper!=null)
        {
            var intro=Get<object>(Agent,"_intro");
            var contractType=skipper.GetNestedType("NativeContract",BindingFlags.NonPublic)!;
            var contract=Activator.CreateInstance(contractType,nonPublic:true)!;
            var cb=SRSingleton<GameContext>.Instance.AutoSaveDirector.NativeMonitorCallback(ctx);
            Set(contract,"Monitor",typeof(Destroyer).GetMethod(nameof(Destroyer.Monitor)));
            Set(contract,"MonitorCallback",cb.Method);
            Set(contract,"ClosureContext",cb.Target!.GetType().GetField("ctx",All));
            Set(contract,"ClosureDirector",cb.Target.GetType().GetField("<>4__this",All));
            var s=template.StartIntro();
            Set(contract,"IntroCallback",s.onComplete!.Method);
            Set(contract,"IntroCanvas",typeof(IntroUI).GetField("introCanvasGroup",All));
            Set(contract,"EndReached",typeof(IntroUI).GetField("endReached",All));
            Set(contract,"UpdateLoop",typeof(DG.Tweening.Core.TweenManager).GetField("isUpdateLoop"));
            Set(intro,"_contract",contract);
            Set(intro,"_background",(Func<bool>)(()=>Background && BackgroundGameFocus.Allow(Application.isFocused,10,20,true)));
            DOTween.Playing.Clear(); Fixture.PopupSuppress=Fixture.AudioPause=Fixture.Pausers=ctx.TutorialDirector.Suppressed=0;
        }
    }
    private static GameObject Root(out IntroUI owner)
    {
        var root=new GameObject(); root.Add<CanvasGroup>(); root.Add<UIInputLocker>(); root.Add<SetInputMode>(); owner=root.Add<IntroUI>();
        owner.background=root.Child(); owner.introLine1=root.Child().Add<CanvasGroup>();
        owner.introLine2=root.Child().Add<CanvasGroup>(); owner.introLine3=root.Child().Add<CanvasGroup>();
        return root;
    }
    private static void Start(string kind="create_save")
    {
        Set(Agent,"_incoming",new BridgeCommand {OpId="create:test",Kind=kind,CreationId=Id});
        Agent.Tick();
        Check(SRSingleton<GameContext>.Instance.AutoSaveDirector.Creates==1,"fresh production Begin dispatched one native creation: "+Get<string?>(Agent,"_outgoing"));
        Check(Get<CounterpartCreator?>(Agent,"_creation")!=null,"Begin returned actual creator");
    }
    private static void Monitor(string mode="valid")
    {
        Levels.Menu=false;
        var ctx=SRSingleton<SceneContext>.Instance;
        ++ctx.TutorialDirector.Suppressed; // native outer OnGameLoadedCoroutine suppression
        var root=Root(out Owner); Seq=Fixture.EnableIntro(Owner);
        var d=SRSingleton<GameContext>.Instance.AutoSaveDirector;
        var cb=d.NativeMonitorCallback(ctx);
        if(mode=="callback") cb=_=>ctx.TutorialDirector.UnsuppressTutorials();
        if(mode=="multicast-monitor") cb+=d.NativeMonitorCallback(ctx);
        if(mode=="monitor-context") cb=d.NativeMonitorCallback(new SceneContext {gameObject=new GameObject()});
        if(mode=="monitor-director") cb=new AutoSaveDirector().NativeMonitorCallback(ctx);
        if(mode=="child-owner")
        {
            root= new GameObject(); root.Children.Add(Owner.gameObject); Owner.transform.parent=root.transform;
        }
        if(mode=="bad-template") ctx.GameModeConfig.Settings.newGamePrefab=new GameObject();
        Destroyer.Monitor(root,cb);
        if(mode=="double-monitor") Destroyer.Monitor(root,d.NativeMonitorCallback(ctx));
        Check(Fixture.TotalComplete==0 && Fixture.Closes==0,"monitor capture must not complete during native coroutine");
        d.Loading=false;
    }
    private static void Happy()
    {
        Prepare(); Start(); Monitor();
        Set(Agent,"_posting",1); // no network; prove persistent skip precedes delivery gate
        Fixture.Frame(); Agent.Tick();
        Check(Fixture.TotalComplete==1,"background NEWGAME must complete native original sequence despite posting gate");
        Check(Owner.EndReached && Fixture.Closes==1,"original OnComplete sets endReached and closes native root");
        Check(Fixture.InputModes.Contains(Owner.gameObject.GetInstanceID()),"native input-mode owner stays installed until deferred OnDisable");
        Check(Fixture.Disables==0 && Fixture.Pausers==1,"destruction/unpause must remain deferred, not bridge shortcuts");
        Check(SRSingleton<SceneContext>.Instance.TutorialDirector.Suppressed==1,"native Destroyer monitor owns first unsuppression");
        Fixture.Frame(); Agent.Tick();
        Check(!Fixture.InputModes.Contains(Owner.gameObject.GetInstanceID()),"exact native input-mode entry removed by attached OnDisable");
        Check(Fixture.Disables==1 && Fixture.Pausers==0,"native OnDisable and end-of-frame unpause complete");
        Check(Fixture.AudioPause==0 && Fixture.PopupSuppress==0 && SRSingleton<SceneContext>.Instance.TutorialDirector.Suppressed==0,"native suppression ownership balanced");
        for(var i=0;i<10;++i) { Fixture.Frame(); Agent.Tick(); }
        Check(Fixture.TotalComplete==1,"one-shot ownership never retries");
    }
    private static void Gate(string mode)
    {
        Prepare();
        if(mode=="unload-before-arm") Agent.CancelBackgroundIntro();
        if(mode=="unknown-contract") Set(Get<object>(Agent,"_intro"),"_contract",null);
        Start(); Monitor(mode);
        var d=SRSingleton<GameContext>.Instance.AutoSaveDirector;
        var scene=SRSingleton<SceneContext>.Instance;
        var name=d.SavedGame.Name;
        Set(Agent,"_posting",1);
        var terminal=true;
        switch(mode)
        {
            case "foreground": Application.isFocused=true; terminal=false; break;
            case "unknown-focus": Background=false; terminal=false; break;
            case "loading": d.Loading=true; terminal=false; break;
            case "tween-stack": DG.Tweening.Core.TweenManager.isUpdateLoop=true; terminal=false; break;
            case "name": d.SavedGame.Name="other"; break;
            case "name-foreground": d.SavedGame.Name="other"; Background=false; break;
            case "director": SRSingleton<GameContext>.Instance.AutoSaveDirector=new(); break;
            case "save-object": d.SavedGame=new(){Name=name}; break;
            case "context": SRSingleton<SceneContext>.Instance=new(){gameObject=new GameObject()}; break;
            case "root-scene": Owner.gameObject.scene=new(){handle=99,name="worldGenerated",isLoaded=true}; break;
            case "unloaded-scene": Fixture.Scene.isLoaded=false; break;
            case "menu": Levels.Menu=true; break;
            case "special": Levels.Special=true; break;
            case "prefab": scene.GameModeConfig.Settings.newGamePrefab=new(); break;
            case "canvas": Owner.introLine1=null!; break;
            case "disabled": Owner.enabled=false; break;
            case "inactive": Owner.gameObject.activeInHierarchy=false; break;
            case "duplicate-sequence": DOTween.Playing.Add(new Sequence {onComplete=Seq.onComplete}); break;
            case "multicast-sequence": Seq.onComplete+=()=>{}; break;
            case "wrong-sequence-method": Seq.onComplete=Owner.OnDisable; break;
            case "tweener-not-sequence": DOTween.Playing.Clear(); DOTween.Playing.Add(new Tween {onComplete=Seq.onComplete}); break;
            case "null-lists": DOTween.Playing.Clear(); terminal=false; break;
            case "loaded-save": d.Loaded=true; break;
            case "callback-error": d.Error!(); break;
            case "timeout": Time.realtimeSinceStartup=241; break;
            case "tick-budget": Set(Get<object>(Agent,"_intro"),"_ticks",30000); break;
            case "unload": Agent.CancelBackgroundIntro(); break;
            case "focus-race": DOTween.OnEnumerate=()=>Application.isFocused=true; terminal=false; break;
            case "identity-race": DOTween.OnEnumerate=()=>d.SavedGame.Name="other"; break;
        }
        Fixture.Frame(); Agent.Tick();
        Check(Fixture.TotalComplete==0 && Fixture.Closes==0,mode+" must not invoke completion or cleanup");
        if(mode=="name") Check(Get<bool>(Get<object>(Agent,"_intro"),"_terminal"),"unexpected name change must be terminal even before focus gate");
        if(terminal)
        {
            Check(Get<bool>(Get<object>(Agent,"_intro"),"_terminal") || mode is "callback" or "multicast-monitor","identity/cancellation failure must be terminal: "+mode);
            DOTween.OnEnumerate=null; Application.isFocused=false; Background=true; d.Loading=false;
            d.SavedGame.Name=name; SRSingleton<GameContext>.Instance.AutoSaveDirector=d; SRSingleton<SceneContext>.Instance=scene;
            DG.Tweening.Core.TweenManager.isUpdateLoop=false;
            Fixture.Frame(); Agent.Tick();
            Check(Fixture.TotalComplete==0,"failure must not re-arm after repair: "+mode);
        }
    }
    private static void ResultOrder(bool uncertain)
    {
        Prepare(); Start(); Background=false; Monitor();
        Fixture.SaveFails=uncertain;
        var result=Get<CounterpartCreator>(Agent,"_creation").Step();
        Check(result!=null && (uncertain ? result.Uncertain : result.Ok && result.Verified==true),"actual creator result established");
        typeof(InventoryAgent).GetMethod("FinishCreation",All)!.Invoke(Agent,new object[]{result!});
        Set(Agent,"_creation",null); Set(Agent,"_posting",1); Background=true;
        Fixture.Frame(); Agent.Tick();
        Check(Fixture.TotalComplete==(uncertain?0:1),"only success preserves persistent owner after result before ACK");
    }
    private static void Provenance(string mode)
    {
        Prepare();
        var journal=Get<OpJournal>(Agent,"_journal");
        var d=SRSingleton<GameContext>.Instance.AutoSaveDirector;
        if(mode=="unauthorized") Set(Agent,"_labCreationAllowed",false);
        if(mode=="no-journal") Set(Agent,"_journal",null);
        if(mode=="prior-intent") journal.WriteIntent("create:"+Id);
        if(mode=="replay") journal.WriteDone("create:test",new CommandResult {Realm="SR1",OpId="create:test",Kind="create_save",CreationId=Id,SaveId="sb1_"+Id,Ok=true,Persisted=true,Verified=true}.ToJson(1));
        if(mode=="existing") d.StorageProvider.Files["sb1_"+Id+"_0"]=System.Text.Encoding.UTF8.GetBytes("sb1_"+Id);
        if(mode=="begin-throw") d.ThrowCreate=true;
        if(mode=="begin-error") d.ErrorDuringCreate=true;
        var kind=mode=="reconcile"?"reconcile_save":"create_save";
        Set(Agent,"_incoming",new BridgeCommand {OpId="create:test",Kind=kind,CreationId=Id});
        Agent.Tick(); Set(Agent,"_posting",1); Monitor(); Fixture.Frame(); Agent.Tick();
        Check(Fixture.TotalComplete==0 && Fixture.Closes==0,"no fresh successful Begin means no ownership: "+mode);
        Check(d.Creates==(mode is "begin-throw" or "begin-error" ? 1:0),"no synthetic provenance can initiate native creation: "+mode);
    }
    private static void Additional(string mode)
    {
        Prepare();
        var d=SRSingleton<GameContext>.Instance.AutoSaveDirector;
        if(mode=="intent-order")
        {
            d.BeforeCreate=()=> {
                var journal=Get<OpJournal>(Agent,"_journal");
                Check(journal.HasAny("create:"+Id) && journal.HasAny("create:test"),"both durable intents precede native dispatch");
                Check(!Get<bool>(Get<object>(Agent,"_intro"),"_used"),"never arm before real Begin returned");
            };
        }
        Start();
        if(mode=="pending-name")
        {
            var expected=d.SavedGame.Name; d.SavedGame.Name="previous"; Set(Agent,"_posting",1);
            Fixture.Frame(); Agent.Tick(); Check(!Get<bool>(Get<object>(Agent,"_intro"),"_terminal"),"old save identity while native coroutine pending is not capture");
            d.SavedGame.Name=expected;
        }
        Monitor(); Set(Agent,"_posting",1);
        if(mode=="paused") { DOTween.Playing.Clear(); DOTween.Paused.Add(Seq); }
        if(mode=="other-pause") { ++Fixture.Pausers; Time.timeScale=0; }
        if(mode=="callback-throw") Seq.Throw=true;
        if(mode=="retired") { Seq.Done=true; Seq.Active=false; }
        if(mode=="natural-finish") Seq.Complete(true);
        if(mode=="unknown-update") Set(Get<object>(Get<object>(Agent,"_intro"),"_contract"),"UpdateLoop",null);
        Agent.Tick(); Check(Fixture.TotalComplete==(mode=="natural-finish"?1:0),"must defer past capture frame");
        Fixture.Frame(); Agent.Tick();
        var completed=mode is "unknown-update" or "retired" ? 0:1;
        Check(Fixture.TotalComplete==completed,"completion contract: "+mode);
        Fixture.Frame(); Agent.Tick(); Fixture.Frame(); Agent.Tick();
        Check(Fixture.TotalComplete==completed,"never retry retired/throwing/disappeared sequence: "+mode);
        if(mode=="other-pause") Check(Fixture.Pausers==1 && Time.timeScale==0,"legitimate other native pause preserved");
        if(mode=="callback-throw") Check(Fixture.Closes==0 && Get<bool>(Get<object>(Agent,"_intro"),"_terminal"),"unknown callback result never triggers fallback");
    }
    private static void FocusContract()
    {
        foreach(var focused in new[]{true,false}) foreach(var own in new uint[]{0,10}) foreach(var foreground in new uint[]{0,10,20}) foreach(var valid in new[]{true,false})
            Check(BackgroundGameFocus.Allow(focused,own,foreground,valid)==(!focused && own!=0 && foreground!=0 && own!=foreground && valid),"read-only focus fail-closed predicate");
        Application.platform=RuntimePlatform.WindowsEditor; Check(!BackgroundGameFocus.IsBackground(),"editor denied before Win32 reads");
        Application.platform=RuntimePlatform.LinuxPlayer; Check(!BackgroundGameFocus.IsBackground(),"non-Windows denied before Win32 reads");
        Application.platform=RuntimePlatform.WindowsPlayer; Application.isFocused=true; Check(!BackgroundGameFocus.IsBackground(),"foreground denied before Win32 reads");
    }
    private static int Main(string[] args)
    {
        try
        {
            var mode=args.Single();
            if(mode=="ringtail-inventory") RingtailInventoryChecks.Run();
            else if(mode=="happy") Happy();
            else if(mode=="result-success") ResultOrder(false);
            else if(mode=="result-uncertain") ResultOrder(true);
            else if(mode=="focus-contract") FocusContract();
            else if(mode is "unauthorized" or "no-journal" or "prior-intent" or "replay" or "existing" or "begin-throw" or "begin-error" or "reconcile") Provenance(mode);
            else if(mode is "intent-order" or "pending-name" or "paused" or "other-pause" or "callback-throw" or "retired" or "natural-finish" or "unknown-update") Additional(mode);
            else Gate(mode);
            Console.WriteLine("PASS "+mode); return 0;
        }
        catch(Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
