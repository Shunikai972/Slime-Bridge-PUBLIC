"""Extraction planning and receipt validation (planning is pure; extraction itself is local).

A plan lists the binary-bearing nodes a species needs (meshes, textures, clips, audio...),
named by content hash so a texture shared by many species is extracted once. Materials,
prefab hierarchy, renderers and shaders are not extracted: they are rebuilt on the target
from IR metadata and semantic material plans. Receipts returned by a local
AssetExtractionProvider are checked against the plan so a stale or foreign file can never
be packaged (source hash binding, location confinement, digest format).
"""

from pathlib import PurePosixPath

from bridge.content.canonical import Evidence, diag, is_sha256, sha256_json, sort_diagnostics
from bridge.content.graph import NodeKind, Resolution

PLAN_SCHEMA = 'bridge-extraction-plan'
EXTRACT_AS = {NodeKind.MESH: 'mesh', NodeKind.TEXTURE: 'texture', NodeKind.ANIMATION_CLIP: 'animation_clip',
              NodeKind.AUDIO: 'audio', NodeKind.ANIMATOR: 'animator_controller', NodeKind.VFX: 'vfx',
              NodeKind.ICON: 'sprite'}
RECEIPT_STATUSES = ('EXTRACTED', 'FAILED', 'NOT_AVAILABLE')


def plan_extraction(graph, root, analysis=None) -> dict:
    """Deterministic extraction plan for one root; unresolved critical inputs block the plan."""
    analysis = analysis or graph.analyze(root)
    critical = set(analysis['critical'])
    items, diags = [], []
    for nid in analysis['closure']:
        node = graph.nodes[nid]
        kind = node['kind']
        if kind not in EXTRACT_AS:
            continue
        required = nid in critical
        if node['resolution'] != Resolution.RESOLVED or node['hash'] is None:
            diags.append(diag('EXTRACTION_INPUT_UNRESOLVED', 'error' if required else 'warning', nid,
                              str(node['resolution'])))
            continue
        items.append({'node': nid, 'kind': str(kind), 'extract_as': EXTRACT_AS[kind], 'source_hash': node['hash'],
                      'required': required, 'artifact': f"{EXTRACT_AS[kind]}/{node['hash']}",
                      'format': None})
    plan = {'schema': PLAN_SCHEMA, 'schema_version': 1, 'root': root, 'realm': root.split(':', 1)[0],
            'closure_hash': analysis['closure_hash'], 'items': items, 'diagnostics': sort_diagnostics(diags),
            'evidence': str(Evidence.BLOCKED_ON_LOCAL_QUERY), 'local_queries': ['Q016']}
    plan['plan_hash'] = sha256_json({k: v for k, v in plan.items() if k != 'diagnostics'})
    return plan


def validate_receipt(plan, receipt, *, synthetic_run) -> list:
    """Check one provider receipt against its plan item. Returns diagnostics (empty = acceptable)."""
    items = {item['node']: item for item in plan['items']}
    subject = receipt.get('node') if isinstance(receipt, dict) else None
    if not isinstance(receipt, dict) or subject not in items:
        return [diag('RECEIPT_NOT_IN_PLAN', 'error', str(subject), 'receipt does not match a planned node')]
    item, out = items[subject], []
    if receipt.get('status') not in RECEIPT_STATUSES:
        out.append(diag('RECEIPT_INVALID', 'error', subject, 'unknown status'))
    if receipt.get('source_hash') != item['source_hash']:
        out.append(diag('RECEIPT_SOURCE_MISMATCH', 'error', subject, 'extracted from a different source object'))
    if receipt.get('synthetic') is not synthetic_run:
        out.append(diag('RECEIPT_EVIDENCE_MISMATCH', 'error', subject, 'synthetic flag differs from the run'))
    if receipt.get('status') == 'EXTRACTED':
        location = receipt.get('location')
        path = PurePosixPath(location) if isinstance(location, str) and location else None
        if path is None or path.is_absolute() or '..' in path.parts or '\\' in location or ':' in location:
            out.append(diag('RECEIPT_LOCATION_UNSAFE', 'error', subject, repr(location)))
        if not is_sha256(receipt.get('sha256')):
            out.append(diag('RECEIPT_INVALID', 'error', subject, 'extracted file digest missing'))
    return sort_diagnostics(out)


class SyntheticExtractionProvider:
    """Cloud test double: returns metadata receipts only, always flagged synthetic. Writes nothing."""

    def __init__(self, realm, fail=()):
        self.realm = realm
        self.fail = set(fail)

    def extract(self, request):
        node = request['node']
        if node in self.fail:
            return {'node': node, 'status': 'FAILED', 'source_hash': request['source_hash'], 'synthetic': True,
                    'detail': 'injected synthetic failure'}
        return {'node': node, 'status': 'EXTRACTED', 'source_hash': request['source_hash'], 'synthetic': True,
                'location': request['artifact'], 'sha256': sha256_json({'synthetic-extract': request['source_hash']}),
                'evidence': 'SYNTHETIC'}
