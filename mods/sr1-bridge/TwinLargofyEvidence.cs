namespace SlimeBridge.SR1
{
    // Evidence policy only: an actor nearby is not sufficient proof of a consumed plort.
    internal static class TwinLargofyEvidence
    {
        internal static bool Accept(bool mapHasTarget, bool pinkPlortEaten, bool foodGone,
            bool originalGone, int newlyObservedTargets)
        {
            return mapHasTarget && pinkPlortEaten && foodGone && originalGone && newlyObservedTargets == 1;
        }
    }
}
