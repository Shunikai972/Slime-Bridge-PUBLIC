// Synthetic readiness boundary, not native CREATE/readiness proof.
using SlimeBridge.Shared;
namespace SlimeBridge.SR2
{
 internal sealed class CounterpartCreator
 {
  internal sealed class WorldReading
  {
   internal readonly Il2Cpp.GameContext Game; internal readonly Il2Cpp.SceneContext Scene;
   internal readonly Il2Cpp.AutoSaveDirector Director; internal readonly string Name;
   internal WorldReading(Il2Cpp.GameContext game, Il2Cpp.SceneContext scene, string name) { Game=game; Scene=scene; Director=game.AutoSaveDirector!; Name=name; }
  }
  internal static object? CurrentWorldWitness => null;
  internal static void ClearWorldReadiness() { }
  internal static bool TryReadWorld(out WorldReading? world)
  {
   world=null; var game=Il2Cpp.GameContext.Instance; var scene=Il2Cpp.SceneContext.Instance; var director=game?.AutoSaveDirector;
   if (game==null || scene==null || director?.SavedGame==null || !director.loadedGame || director._currentGameName!=director.SavedGame.GetName() || scene.Player?.activeInHierarchy!=true || scene.PlayerState?.GetCurrHealth()<=0 || scene.PlayerState==null) return false;
   world=new(game,scene,director._currentGameName!); return true;
  }
  public static string GameNameFor(string id)=>"sb2_"+id;
  public static CounterpartCreator? Begin(BridgeCommand c,OpJournal j,int pid,out CommandResult? result,Func<Il2Cpp.AutoSaveDirector,bool>? binding=null) { result=Reconcile(c,binding); return null; }
  public static CommandResult Reconcile(BridgeCommand c,Func<Il2Cpp.AutoSaveDirector,bool>? binding=null)=>new(){OpId=c.OpId,Kind=c.Kind,CanCreate=false,Verified=false};
  public CommandResult? Step()=>throw new InvalidOperationException("CREATE is out of scope");
 }
 internal sealed class BackgroundIntroSkipper
 {
  internal bool Stopped => true;
  internal BackgroundIntroSkipper(string name,IntPtr director,Action<string> log,Func<bool>? background) { throw new InvalidOperationException("intro is out of scope"); }
  internal void Tick() { } internal void Cancel() { }
 }
}
