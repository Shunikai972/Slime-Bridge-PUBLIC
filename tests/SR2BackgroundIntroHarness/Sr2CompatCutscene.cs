// Intro harness part of the Sr2Compat test double: 1.x reads the cutscene director singleton, the fixture keeps it on SceneContext.
namespace SlimeBridge.SR2
{
    internal static partial class Sr2Compat
    {
        internal static Il2CppMonomiPark.SlimeRancher.Cutscene.CutsceneDirector? Cutscene() => Il2Cpp.SceneContext.Instance?.CutsceneDirector;
    }
}
