using UnityEngine;

var scenarios = 0;
foreach (var fault in new[]{"shadow0","enabled0","player0","target","position","rotation","fov","near","active","release","destroy-rt","destroy-tex","", "shadow1"})
    foreach (var primary in new[]{false,true})
    {
        var state = new State(fault);
        var renderers = new[]{new Renderer(state,"0"),new Renderer(state,"1")};
        var modes = new[]{3,4};
        var enabled = new[]{true,false};
        var hiddenPlayerRenderers = new List<Renderer>{new(state,"player0"),new(state,"player1")};
        var cam = new Camera(state);
        var savedTarget = "old-target";
        var savedPosition = "old-position";
        var savedRotation = "old-rotation";
        var savedFov = 73f;
        var savedNear = .3f;
        var savedActive = "old-active";
        RenderTexture? rt = new(state);
        Texture2D? tex = new(state);
        RenderTexture.State = state;
        Exception? captureFailure = primary ? new ApplicationException("capture fault") : null;
        Exception? observed = null;
        try
        {
            try { if (captureFailure != null) throw captureFailure; }
            // PRODUCTION_FINALLY
        }
        catch(Exception error) { observed=error; }
        var expected = new[]{"shadow0","shadow1","enabled0","enabled1","player0","player1","target","position","rotation","fov","near","active","release","destroy-rt","destroy-tex"};
        foreach(var key in expected)
            if(!state.Attempts.Contains(key)) throw new Exception("cleanup stopped after " + fault + "; missing " + key);
        if(fault!="" && observed==null) throw new Exception("cleanup failure was reported as successful: " + fault);
        if(fault=="" && primary && observed is not ApplicationException) throw new Exception("original capture error was lost");
        if(fault=="" && !primary && observed!=null) throw new Exception("normal cleanup failed");
        if(primary && fault!="" && (observed is not AggregateException aggregate || !aggregate.InnerExceptions.Contains(captureFailure!)))
            throw new Exception("cleanup masked the primary capture error");
        scenarios++;
        Console.WriteLine("PASS fault="+fault+" primary="+primary);
    }
Console.WriteLine($"RESULT {scenarios} cleanup scenarios passed (production finally; managed boundary faults, NOT Unity proof)");

sealed class State(string fault)
{
    internal readonly HashSet<string> Attempts=new();
    internal void Apply(string key)
    {
        Attempts.Add(key);
        if(key==fault) throw new InvalidOperationException("native object fault: "+key);
    }
}
namespace UnityEngine
{
    sealed class Renderer(State state,string id)
    {
        public int shadowCastingMode {set{state.Apply("shadow"+id);}}
        public bool enabled {set{state.Apply(id.StartsWith("player")?id:"enabled"+id);}}
    }
    sealed class Transform(State state)
    {
        public string position {set{state.Apply("position");}}
        public string rotation {set{state.Apply("rotation");}}
    }
    sealed class Camera(State state)
    {
        public Transform transform=new(state);
        public string targetTexture {set{state.Apply("target");}}
        public float fieldOfView {set{state.Apply("fov");}}
        public float nearClipPlane {set{state.Apply("near");}}
    }
    sealed class RenderTexture(State state)
    {
        public static State State=null!;
        public static string active {set{State.Apply("active");}}
        public void Release()=>state.Apply("release");
        public void Destroy()=>state.Apply("destroy-rt");
    }
    sealed class Texture2D(State state) {public void Destroy()=>state.Apply("destroy-tex");}
    static class Object
    {
        public static void Destroy(RenderTexture value)=>value.Destroy();
        public static void Destroy(Texture2D value)=>value.Destroy();
    }
}
