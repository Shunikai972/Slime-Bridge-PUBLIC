"""Per-realm counterpart conventions, taken only from code already in this repository.

SR1: marker ``sb1_<creation_id>`` and native file ``sb1_<id>_<n>`` come from
``bridge/profile/native_proof.py`` and the runtime-proven SR1 creation oracle (local LAB proof,
see docs/native-contracts/sr1-runtime.md). ``sr1-creation:<id>`` is the identity returned by
``verify_sr1_creation``. ``sr2-creation:<id>`` is a Bridge record, not a native fact, pending Q020.

SR2: the ``sb2_<creation_id>`` marker and canonical revision suffix of at most ten digits
come from ``CounterpartCreator.GameNameFor`` and ``Verify`` in
``mods/sr2-bridge/CounterpartCreator.cs``. This is a local static acceptance contract, NOT
runtime proof of the storage format, parser or marker hook. Q019/Q020 remain blocked.
"""

from bridge.content.canonical import Evidence
from bridge.core import diagnostics as D
from bridge.profile.native_proof import creation_save_id, verify_creation
from bridge.verification.receipts import make_receipt
from .adapters import RealmConvention, WireCounterpartAdapter

SR1 = RealmConvention('SR1', marker_prefix='sb1_', identity_prefix='sr1-creation:', filename_pattern=r'{save_id}_(0|[1-9][0-9]{0,5})',
                      slot_semantics='SR1 save groups (no fixed slot count assumed)',
                      evidence=Evidence.NEEDS_LOCAL_RUNTIME_PROOF,
                      sources=('bridge/profile/native_proof.py', 'tools/lab/creation_oracle.py',
                               'docs/native-contracts/sr1-runtime.md'),
                      features=('create', 'reconcile', 'full_readback', 'save_hash'))
SR2 = RealmConvention('SR2', marker_prefix='sb2_', identity_prefix='sr2-creation:', filename_pattern=r'{save_id}_(0|[1-9][0-9]{0,9})',
                      slot_semantics='native SaveSlotIndex chosen and revalidated by the plugin',
                      evidence=Evidence.BLOCKED_ON_LOCAL_QUERY,
                      sources=('mods/sr2-bridge/CounterpartCreator.cs', 'docs/native-contracts/sr2-runtime.md'),
                      local_queries=('Q019', 'Q020'),
                      features=('create', 'reconcile', 'slot_probe', 'full_readback', 'save_hash'))
CONVENTIONS = {'SR1': SR1, 'SR2': SR2}


def wire_adapter(realm):
    return WireCounterpartAdapter(CONVENTIONS[realm], provider_id=f'bridge.wire-counterpart.{realm.lower()}')


def _disk_readback_receipt(result, *, realm, save_root, provider, observed_at, build_fingerprint):
    if not isinstance(result, dict):
        raise ValueError('invalid normalized counterpart disk-readback result')
    creation_id = result.get('creation_id')
    creation_save_id(creation_id, realm=realm)  # Never fabricate an unbound receipt subject.
    native_save_id = result.get('native_save_id')
    try:
        if 'target_realm' in result and result['target_realm'] != realm:
            raise ValueError('disk readback target_realm does not match the requested realm')
        operation_kind = result.get('operation_kind', 'create')
        if operation_kind not in ('create', 'reconcile'):
            raise ValueError('invalid counterpart disk-readback operation kind')
        kind = 'create_save' if operation_kind == 'create' else 'reconcile_save'
        session = result.get('session')
        if not isinstance(session, dict):
            raise ValueError('counterpart disk readback requires a process session')
        pid, op_id = session.get('pid'), result.get('operation_id')
        wire = {'realm': realm, 'kind': kind, 'op_id': op_id, 'creation_id': creation_id,
                'save_id': native_save_id, 'pid': pid, 'ok': True, 'verified': True,
                'save_file': result.get('native_filename'), 'sha256': result.get('save_hash')}
        proof = verify_creation(wire, creation_id, op_id, pid, save_root, realm=realm, expected_kind=kind)
    except (OSError, ValueError) as exc:
        return make_receipt('counterpart.native_save_exists', 'counterpart', creation_id, 'FAILED',
                            'DISK_READBACK', realm=realm, build_fingerprint=build_fingerprint, provider=provider,
                            observed_at=observed_at,
                            details={'native_save_id': native_save_id if isinstance(native_save_id, str) else None},
                            diagnostics=[D.make(f'{realm}_DISK_READBACK_FAILED', 'error', 'counterpart', str(exc)[:200],
                                                realm=realm)])
    return make_receipt('counterpart.native_save_exists', 'counterpart', result['creation_id'], 'PASSED',
                        'DISK_READBACK', realm=realm, build_fingerprint=build_fingerprint, provider=provider,
                        observed_at=observed_at, artifact_hashes={'save': proof['sha256']},
                        details={'native_save_id': proof['save_id'], 'identity': proof['identity'],
                                 'os_flush_completed': proof['os_flush_completed']})


def sr1_disk_readback_receipt(result, *, save_root, provider, observed_at, build_fingerprint):
    """SR1 existence/disk receipt, preserving the historical normalized-result API.

    Callers already bind/validate the plugin's native result. This independent check matches
    bytes and requests an OS flush; it is not a native parser or full-readback attestation.
    Local tests use synthetic files only.
    """
    return _disk_readback_receipt(result, realm='SR1', save_root=save_root, provider=provider,
                                  observed_at=observed_at, build_fingerprint=build_fingerprint)


def sr2_disk_readback_receipt(result, *, save_root, provider, observed_at, build_fingerprint):
    """SR2 existence/disk receipt only; native parser/slot/provenance proofs remain separate."""
    return _disk_readback_receipt(result, realm='SR2', save_root=save_root, provider=provider,
                                  observed_at=observed_at, build_fingerprint=build_fingerprint)
