"""Native motion trace must observe an unpinned, normally driven actor.

This contract protects the instrumentation itself; it is not proof of immobility.
The RED/GREEN gameplay oracle consumes the trace produced by the real game separately.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / 'mods/sr1-bridge/LabActorMotionTrace.cs'
OPS = ROOT / 'mods/sr1-bridge/LabImportedOps.cs'


def test_motion_trace_is_read_only_and_not_a_pose_pin():
    source = TRACE.read_text(encoding='utf-8-sig')
    for field in ('position', 'rotation', 'velocity', 'angularVelocity', 'isKinematic', 'useGravity'):
        assert field in source
    assert 'GetComponent<Rigidbody>()' in source
    assert 'GetComponents<MonoBehaviour>()' in source
    assert 'currBehavior' in source  # actual native selection, not a guessed enabled-count
    assert 'OnCollisionStay' in source and 'external_contacts' in source
    assert 'GetCurrentAnimatorStateInfo' in source
    assert 'GetComponentInChildren<ImportedVisualMarker>' in source
    assert 'FileMode.CreateNew' in source and 'FileShare.Read' in source
    assert 'const int MaxSamples = 3000;' in source
    assert 'const float SamplePeriod = 0.1f;' in source
    assert 'LabRestPosePin' in source  # refuse pin presence explicitly
    assert not re.search(r'\b(?:velocity|angularVelocity|isKinematic|useGravity|position|rotation)\s*=', source)
    assert not re.search(r'\b(?:AddForce|AddTorque|SetPositionAndRotation|Sleep|Play|Sample)\s*\(', source)


def test_motion_trace_is_opt_in_on_the_gated_native_spawn():
    source = OPS.read_text(encoding='utf-8-sig')
    spawn = source[source.index('private bool AppendSpawn('):source.index('private static void RevertAdded(')]
    assert 'LabActorMotionTrace.TryAttach' in spawn
    assert 'LabRestPosePin.Attach' in spawn  # old framing remains available outside this proof
    trace = TRACE.read_text(encoding='utf-8-sig')
    assert 'SLIMEBRIDGE_LAB_MOTION_TRACE' in trace
    assert 'LabSpecPathGuard.IsAllowedDirectory' in trace
    assert 'Process.GetCurrentProcess()' in trace
    assert 'GetEnvironmentVariable("SLIMEBRIDGE_LAB_EXE")' in trace
    assert 'SLIMEBRIDGE_REST_POSE' in trace
    assert 'OnDestroy' in trace and '.Dispose()' in trace
