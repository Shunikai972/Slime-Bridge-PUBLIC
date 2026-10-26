using System.Runtime.CompilerServices;
using UnityEngine;

public sealed class Identifiable : MonoBehaviour
{
    public enum Id { NONE = 0, Tabby = 3, ImportedRingtail = 900040 }
    public Id id;
}

public sealed class SlimeEat : MonoBehaviour
{
    public int EatAndProduceCalls;
    // Private in the real game; the production patch resolves it by name (fail-open when absent).
    private void EatAndProduce(GameObject target, int entry, bool immediateMode, bool skipDelays, bool skipProduction) { EatAndProduceCalls++; }
    public void TestEat() => EatAndProduce(gameObject, 0, false, false, false);
}

public sealed class Chomper : MonoBehaviour
{
    public delegate void OnChompStartDelegate();
    public delegate void OnChompCompleteDelegate(GameObject obj, Identifiable.Id id, bool held, bool launched);
    private bool _chomping;
    private OnChompCompleteDelegate? _onComplete;
    private GameObject? _other;
    private Identifiable.Id _otherId;
    private bool _held;
    public int NativeCompletionCalls;
    public bool DestroyActorOnCompletion;

    public bool IsChomping() => _chomping;
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void StartChomp(GameObject other, Identifiable.Id id, bool held, bool quick,
        OnChompStartDelegate onStart, OnChompCompleteDelegate onComplete)
    {
        onStart?.Invoke();
        _other = other;
        _otherId = id;
        _held = held;
        _onComplete = onComplete;
        _chomping = true;
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void BiteComplete()
    {
        NativeCompletionCalls++;
        var callback = _onComplete;
        _onComplete = null;
        _chomping = false;
        callback?.Invoke(_other!, _otherId, _held, false);
        if (DestroyActorOnCompletion) UnityEngine.Object.Destroy(gameObject);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    private void ForceCancelChomp() { _chomping = false; _onComplete = null; }
    public void Cancel() => ForceCancelChomp();
}

public class FindConsumable : MonoBehaviour
{
    [MethodImpl(MethodImplOptions.NoInlining)]
    protected void MoveTowards(Vector3 targetPos, bool shouldJump, ref float nextJumpAvail, float jumpStrength)
    {
        if (shouldJump) nextJumpAvail = jumpStrength;
    }
    public void InvokeMove(ref float nextJumpAvail, float newAvailability) =>
        MoveTowards(default, true, ref nextJumpAvail, newAvailability);
}

public sealed class StalkConsumable : FindConsumable
{
    private enum Mode { NONE, APPROACH, WAIT, PREP, POUNCE, FEINT, PIVOT }
    private Mode mode;
    public int NativeLeapCalls;
    [MethodImpl(MethodImplOptions.NoInlining)]
    private void LeapToward(float distanceSquared, Vector3 directionToJump, Vector3 directionToFace) { NativeLeapCalls++; }
    public void SetPounceMode(bool pounce) => mode = pounce ? Mode.POUNCE : Mode.FEINT;
    public void InvokeLeap() => LeapToward(1f, default, default);
}

public sealed class SlimeRandomMove : MonoBehaviour
{
    private float nextJumpTime;
    private bool _jumpNext;
    private float _nextValue;
    public float NextJump => nextJumpTime;
    public void QueueJump(float nextValue) { _jumpNext = true; _nextValue = nextValue; }
    [MethodImpl(MethodImplOptions.NoInlining)]
    public void Action() { if (_jumpNext) { nextJumpTime = _nextValue; _jumpNext = false; } }
}

public sealed class SlimeSubbehaviourPlexer : MonoBehaviour
{
    private bool floating;
    private bool captive;
    public int NativeGroundCallbacks;
    [MethodImpl(MethodImplOptions.NoInlining)]
    private void OnGroundedRaycastResultReceived(RaycastHit result) { NativeGroundCallbacks++; }
    public bool IsFloating() => floating;
    public bool IsCaptive() => captive;
    public void SetFloating(bool value) => floating = value;
    public void SetCaptive(bool value) => captive = value;
    public void Ground(Collider? collider) => OnGroundedRaycastResultReceived(new RaycastHit { collider = collider });
}
