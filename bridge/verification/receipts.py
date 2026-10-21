"""``bridge-verification-receipt`` v1: one attested outcome for one check on one subject.

Receipts are untrusted input. ``validate_receipt`` rejects impossible combinations (a PASSED
without evidence, a NOT_RUN carrying artifacts, runtime evidence without a process/session or
build binding, BLOCKED/NOT_APPLICABLE without a reason). ``receipt_id`` is a content hash for
integrity and de-duplication only; it is not a signature and proves nothing about who wrote it.
Trust is decided by the aggregator from an explicit provider→realm trust map.
"""

import copy
import re

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D
from bridge.core import schema as S

RECEIPT_SCHEMA = 'bridge-verification-receipt'
STATUSES = ('PASSED', 'FAILED', 'BLOCKED', 'NOT_APPLICABLE', 'NOT_RUN')
EVIDENCE_TYPES = ('RUNTIME_OBSERVATION', 'NATIVE_READBACK', 'DISK_READBACK', 'STATIC_ANALYSIS', 'SYNTHETIC', 'NONE')
RUNTIME_EVIDENCE = ('RUNTIME_OBSERVATION', 'NATIVE_READBACK', 'DISK_READBACK')
SUBJECT_KINDS = ('package', 'counterpart', 'transition', 'provider', 'largo', 'multiplayer')
_TIME = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$')

_SPEC = S.obj({
    'schema': S.enum(RECEIPT_SCHEMA), 'schema_version': S.enum(1), 'receipt_id': S.sha256(),
    'check_id': S.string(r'[a-z0-9_.:-]+', max_length=160), 'subject_kind': S.enum(*SUBJECT_KINDS),
    'subject_id': S.string(max_length=256), 'realm': S.optional(S.enum('SR1', 'SR2')),
    'build_fingerprint': S.optional(S.string()), 'status': S.enum(*STATUSES), 'evidence_type': S.enum(*EVIDENCE_TYPES),
    'provider': S.optional(S.obj({'provider_id': S.string(), 'provider_version': S.string()})),
    'observed_at': S.optional(S.string(_TIME.pattern)),
    'session': S.optional(S.obj({'pid': S.integer(minimum=1, maximum=2 ** 32 - 1), 'session_id': S.string(),
                                 'process_image_sha256': S.optional(S.sha256())})),
    'artifact_hashes': S.map_of(S.string(max_length=256), S.sha256()),
    'details': S.any_json(), 'diagnostics': S.list_of(S.any_json()),
})


def make_receipt(check_id, subject_kind, subject_id, status, evidence_type, *, realm=None, build_fingerprint=None,
                 provider=None, observed_at=None, session=None, artifact_hashes=None, details=None, diagnostics=()):
    if session is not None:
        session = {'process_image_sha256': None, **session}
    receipt = {'schema': RECEIPT_SCHEMA, 'schema_version': 1, 'check_id': check_id, 'subject_kind': subject_kind,
               'subject_id': subject_id, 'realm': realm, 'build_fingerprint': build_fingerprint, 'status': status,
               'evidence_type': evidence_type, 'provider': provider, 'observed_at': observed_at, 'session': session,
               'artifact_hashes': dict(sorted((artifact_hashes or {}).items())), 'details': details or {},
               'diagnostics': D.sort(list(diagnostics))}
    return seal(receipt)


def seal(receipt):
    receipt = copy.deepcopy(receipt)
    receipt['receipt_id'] = sha256_json({k: v for k, v in receipt.items() if k != 'receipt_id'})
    return receipt


def validate_receipt(receipt):
    """Problems (unified diagnostics) with a receipt; empty means internally consistent."""
    problems = S.check(_SPEC, receipt)
    if problems:
        return [D.make('RECEIPT_SCHEMA_INVALID', 'error', 'verification', 'receipt schema violation',
                       details={'problems': problems[:20]})]
    out = []

    def bad(code, message):
        out.append(D.make(code, 'error', 'verification', message, subject=receipt['check_id']))
    if seal(receipt)['receipt_id'] != receipt['receipt_id']:
        bad('RECEIPT_ID_MISMATCH', 'receipt_id is not the content hash')
    status, evidence = receipt['status'], receipt['evidence_type']
    if status == 'PASSED' and evidence == 'NONE':
        bad('RECEIPT_IMPOSSIBLE', 'PASSED without evidence')
    if status in ('NOT_RUN',) and (evidence != 'NONE' or receipt['artifact_hashes'] or receipt['session']):
        bad('RECEIPT_IMPOSSIBLE', 'NOT_RUN cannot carry evidence, artifacts or a session')
    if status in ('BLOCKED', 'NOT_APPLICABLE') and not receipt['diagnostics']:
        bad('RECEIPT_REASON_MISSING', f'{status} requires a diagnostic reason')
    if status in ('PASSED', 'FAILED', 'BLOCKED', 'NOT_APPLICABLE') and receipt['provider'] is None:
        # A waiver (NOT_APPLICABLE/BLOCKED) changes a verdict as much as an outcome does: always attributed.
        bad('RECEIPT_PROVIDER_MISSING', 'outcomes and waivers need the attesting provider identity')
    if evidence in RUNTIME_EVIDENCE:
        if receipt['realm'] is None or receipt['build_fingerprint'] is None:
            bad('RECEIPT_UNBOUND', 'runtime evidence must name realm and build')
        if receipt['observed_at'] is None:
            bad('RECEIPT_UNTIMED', 'runtime evidence must carry observed_at')
        if evidence == 'RUNTIME_OBSERVATION' and receipt['session'] is None:
            bad('RECEIPT_SESSION_MISSING', 'runtime observation must name the process/session')
    return D.sort(out)
