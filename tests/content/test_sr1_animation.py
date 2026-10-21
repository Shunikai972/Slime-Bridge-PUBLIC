"""Synthetic controller fixtures: no game assets or animation payloads."""
import copy
import importlib

import pytest


def controller():
    def state(slot):
        return {'data': {'m_BlendTreeConstantArray': [{'data': {
            'm_NodeArray': [{'data': {'m_ChildIndices': [], 'm_ClipID': slot}}]}}]}}
    return {'m_AnimationClips': [{'m_FileID': 0, 'm_PathID': i} for i in (20, 30, 20)],
            'm_Controller': {'m_LayerArray': [{'data': {'m_StateMachineIndex': 0}}],
                             'm_StateMachineArray': [{'data': {'m_DefaultState': 1,
                                 'm_StateConstantArray': [state(0), state(1), state(2)]}}]}}


def test_controller_selects_only_declared_refs_and_uses_serialized_default():
    try:
        module = importlib.import_module('bridge.content.sr1_animation')
    except ModuleNotFoundError:
        pytest.fail('SR1 data-driven controller selector is not implemented')
    tree = controller()
    before = copy.deepcopy(tree)
    selection = module.select_controller_clips(tree)
    assert selection['clip_pointers'] == tree['m_AnimationClips'][:2]
    assert selection['default_pointer'] == {'m_FileID': 0, 'm_PathID': 30}
    assert selection['policy'] == 'all-controller-referenced-clips; base-layer-default-state'
    assert tree == before


@pytest.mark.parametrize('mutate', [
    lambda t: t.update(m_AnimationClips=[]),
    lambda t: t['m_Controller']['m_LayerArray'][0]['data'].update(m_StateMachineIndex=-1),
    lambda t: t['m_Controller']['m_StateMachineArray'][0]['data'].update(m_DefaultState=99),
    lambda t: t['m_Controller']['m_StateMachineArray'][0]['data']['m_StateConstantArray'][1]['data']['m_BlendTreeConstantArray'][0]['data']['m_NodeArray'].append({'data': {'m_ChildIndices': [], 'm_ClipID': 0}}),
    lambda t: t['m_Controller']['m_StateMachineArray'][0]['data']['m_StateConstantArray'][1]['data']['m_BlendTreeConstantArray'][0]['data']['m_NodeArray'][0]['data'].update(m_ClipID=3),
    lambda t: t['m_AnimationClips'][0].update(m_PathID=True),
])
def test_ambiguous_or_malformed_controller_fails_closed(mutate):
    from bridge.content.sr1_animation import select_controller_clips
    tree = controller()
    mutate(tree)
    with pytest.raises(ValueError):
        select_controller_clips(tree)


def streamed_clip(epsilon, first_value=1):
    import struct
    raw = bytearray()
    for time, indices in [(-3.4028234663852886e38, range(3)), (epsilon, range(3)), (float('inf'), [])]:
        raw.extend(struct.pack('<fi', time, len(indices)))
        for i in indices:
            raw.extend(struct.pack('<iffff', i, 0, 0, 0, 1 if time < 0 else first_value))
    words = list(struct.unpack('<%dI' % (len(raw)//4), raw))
    return {'m_Name': 'Synthetic', 'm_SampleRate': 30,
        'm_MuscleClip': {'m_StartTime': 0, 'm_StopTime': 1, 'm_LoopTime': True,
            'm_Clip': {'data': {'m_StreamedClip': {'curveCount': 3, 'data': words},
                'm_DenseClip': {'m_CurveCount': 0, 'm_FrameCount': 0, 'm_BeginTime': 0,
                    'm_SampleRate': 0, 'm_SampleArray': []}, 'm_ConstantClip': {'data': []}}}},
        'm_ClipBindingConstant': {'genericBindings': [{'typeID': 4, 'customType': 0,
            'isPPtrCurve': 0, 'attribute': 1, 'path': 0}]}}


def test_sr1_float32_initial_key_uses_exact_matching_authored_startup_value():
    import bridge.content.sr1_animation as m
    tree = streamed_clip(4.76837158203125e-7)
    before = copy.deepcopy(tree)
    assert hasattr(m, 'decode_sr1_clip'), 'SR1 initial-key float32 adapter missing'
    clip = m.decode_sr1_clip(tree, {0: ''})
    assert clip['channels'][0]['values'][0] == [1, 1, 1]
    assert tree == before


@pytest.mark.parametrize('epsilon,value', [(1e-3, 1), (4.76837158203125e-7, 2)])
def test_sr1_missing_real_coverage_is_not_a_general_fallback(epsilon, value):
    import bridge.content.sr1_animation as m
    assert hasattr(m, 'decode_sr1_clip'), 'SR1 initial-key float32 adapter missing'
    with pytest.raises(ValueError):
        m.decode_sr1_clip(streamed_clip(epsilon, value), {0: ''})
