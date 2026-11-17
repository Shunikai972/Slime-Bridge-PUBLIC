"""Static LAB observer safety/wiring guards, not a native lifecycle execution test."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def test_vacuum_trace_is_explicit_lab_only_and_contains_no_actor_material_mutations():
    code=(ROOT/'mods/sr2-bridge/Sr2LabVacuumTrace.cs').read_text()
    assert 'SLIMEBRIDGE_VAC_TRACE_OUT' in code and '!exactLabExecutable' in code
    assert 'IsAllowedDirectory' in code and 'IsAllowedFile' in code
    assert 'private const int Limit = 512' in code
    assert 'renderer.sharedMaterials' in code
    for forbidden in ['.SetPropertyBlock(', '.SetColor(', '.SetTexture(', '.SetVector(', '.SetFloat(', '.SetInt(', '.Instantiate(', '.Destroy(']:
        assert forbidden not in code
    assert '"UpdateMaterialsHeldState", Type.EmptyTypes' in code
    assert '"ExpelHeld", new[] { typeof(float) }' in code
    assert 'writer.before' in code and 'writer.after' in code and 'writer.next_frame' in code
    assert 'native.expel_held.before' in code and 'native.expel_held.after' in code


def test_vacuum_trace_is_wired_to_real_main_thread_lifecycle_and_cleanup():
    code=(ROOT/'mods/sr2-bridge/BridgeMod.cs').read_text()
    assert 'Sr2LabVacuumTrace.Arm(OracleExecutableMatches())' in code
    late=code.split('public override void OnLateUpdate()',1)[1].split('public override void OnUpdate()',1)[0]
    assert 'Sr2LabVacuumTrace.LateTick()' in late
    unload=code.split('public override void OnDeinitializeMelon()',1)[1]
    assert 'Sr2LabVacuumTrace.Stop()' in unload
