"""Deterministic, resumable build graph over the Phase 1 pipeline.

Generic executor: nodes declare dependencies, a tool/schema version and JSON params. A node's key is
the hash of (id, version, params, dependency output hashes); a stored record with that key is reused
(REUSED), otherwise the action runs (RUN). Failures mark the node FAILED and its dependents BLOCKED;
unrelated nodes continue. Records are verified on read, so a corrupt record is rebuilt, never trusted.
``volatile`` nodes (external sources such as provider reads) always run; their output hash then
drives every downstream key, which is what makes rebuilds incremental and partial.

Content build: registry snapshots → classification → discovery (shared) → per species
identity → inputs (Phase 1 cache inputs + dependency hashes) → conversion → generated package.
Phase 1 stage outputs are exposed as derived ``stage:`` nodes for per-stage status and hashes.
"""

import json
from collections import deque

from bridge.cache.fs import atomic_write, confined_root, refuse_links
from bridge.content.canonical import canonical_json, sha256_json
from bridge.content.discovery import DEFAULT_MAX_NODES, discover
from bridge.conversion.engine import cache_inputs, convert_species, root_for
from bridge.core import diagnostics as D
from bridge.registry.reconcile import classify_species
from .package import build_generated_package

BUILD_SCHEMA = 'bridge-build-report'
NODE_VERSION = {'registry': '1', 'classification': '1', 'discovery': '1', 'identity': '1', 'inputs': '1',
                'conversion': '1', 'package': '1'}


class MemoryBuildStore:
    def __init__(self):
        self.records = {}

    def get(self, key):
        return self.records.get(key)

    def put(self, key, record):
        self.records[key] = json.loads(canonical_json(record))


class DirectoryBuildStore:
    """Records under a confined root (atomic, link-refusing)."""

    def __init__(self, root):
        self.root = confined_root(root)

    def _path(self, key):
        return self.root / 'nodes' / key[:2] / f'{key}.json'

    def get(self, key):
        path = self._path(key)
        refuse_links(self.root, path)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, RecursionError):
            return {'corrupt': True}

    def put(self, key, record):
        atomic_write(self.root, self._path(key), canonical_json(record).encode('utf-8'))


class Node:
    def __init__(self, node_id, kind, action, deps=(), params=None, version='1', volatile=False):
        self.id, self.kind, self.action = node_id, kind, action
        self.deps, self.params, self.version, self.volatile = sorted(deps), params or {}, version, volatile


def topological(nodes):
    """Deterministic Kahn order; raises on unknown dependencies or cycles."""
    by_id = {n.id: n for n in nodes}
    indegree = {n.id: 0 for n in nodes}
    users = {n.id: [] for n in nodes}
    for node in nodes:
        for dep in node.deps:
            if dep not in by_id:
                raise ValueError(f'{node.id} depends on unknown node {dep}')
            indegree[node.id] += 1
            users[dep].append(node.id)
    ready = sorted(i for i, d in indegree.items() if d == 0)
    order = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for user in sorted(users[current]):
            indegree[user] -= 1
            if indegree[user] == 0:
                ready.append(user)
                ready.sort()
    if len(order) != len(nodes):
        raise ValueError('build graph has a cycle')
    return order


def _ancestors(nodes, targets):
    by_id = {n.id: n for n in nodes}
    keep, queue = set(), deque(t for t in targets if t in by_id)
    while queue:
        current = queue.popleft()
        if current not in keep:
            keep.add(current)
            queue.extend(by_id[current].deps)
    return keep


def execute(nodes, store, *, targets=None, values=None):
    """Run the graph. ``values`` carries in-process outputs (e.g. objects) between nodes this run."""
    order = topological(nodes)
    by_id = {n.id: n for n in nodes}
    wanted = _ancestors(nodes, targets) if targets is not None else set(by_id)
    values = values if values is not None else {}
    report = {}
    for node_id in order:
        node = by_id[node_id]
        if node_id not in wanted:
            report[node_id] = {'id': node_id, 'kind': node.kind, 'status': 'SKIPPED', 'deps': node.deps}
            continue
        blocked = [d for d in node.deps if report[d]['status'] in ('FAILED', 'BLOCKED', 'SKIPPED')]
        input_hashes = {d: report[d].get('output_hash') for d in node.deps}
        entry = {'id': node_id, 'kind': node.kind, 'deps': node.deps, 'version': node.version,
                 'input_hashes': input_hashes, 'diagnostics': []}
        if blocked:
            report[node_id] = {**entry, 'status': 'BLOCKED', 'key': None, 'output_hash': None,
                               'diagnostics': [D.make('BUILD_BLOCKED', 'error', 'build', 'dependency unavailable',
                                                      subject=node_id, details={'deps': blocked})]}
            continue
        key = sha256_json({'id': node_id, 'version': node.version, 'params': node.params, 'deps': input_hashes})
        entry['key'] = key
        record = None if node.volatile else store.get(key)
        if record is not None:
            if (not isinstance(record, dict) or record.get('key') != key or
                    sha256_json(record.get('output')) != record.get('output_hash')):
                entry['diagnostics'].append(D.make('BUILD_RECORD_CORRUPT', 'warning', 'build',
                                                   'stored record rejected; rebuilding', subject=node_id))
                record = None
        if record is not None:
            values[node_id] = record['output']
            report[node_id] = {**entry, 'status': 'REUSED', 'output_hash': record['output_hash']}
            continue
        try:
            value, output = node.action({d: values[d] for d in node.deps})
            output_hash = sha256_json(output)
        except Exception as exc:  # isolate: one node never stops unrelated work
            report[node_id] = {**entry, 'status': 'FAILED', 'output_hash': None,
                               'diagnostics': entry['diagnostics'] + [D.make(
                                   'BUILD_NODE_FAILED', 'error', 'build', f'{type(exc).__name__}: {str(exc)[:200]}',
                                   subject=node_id)]}
            continue
        values[node_id] = value
        if not node.volatile:
            store.put(key, {'key': key, 'node': node_id, 'output': output, 'output_hash': output_hash})
        report[node_id] = {**entry, 'status': 'RUN', 'output_hash': output_hash}
    return report


def _json(value):
    return value, value


def _given(node_id, kind, value, output=None):
    """Inject an output computed earlier in this run (its hash is what dependents key on)."""
    return Node(node_id, kind, lambda _: (value, value if output is None else output), volatile=True)


def content_build(*, source, destination_registry, target_profile, store, aliases=None, only=None,
                  max_nodes=DEFAULT_MAX_NODES):
    """Incremental build of generated packages for every source-exclusive species."""
    src, tgt = source.realm, target_profile['realm']
    values = {}
    alias_params = sha256_json(sorted((repr(k), sorted(v['evidence']), v['canonical_id'])
                                      for k, v in (aliases or {}).items()))
    report = execute([
        Node(f'registry:{src}', 'registry', lambda _: _json(source.provider.read_registry()), volatile=True,
             version=NODE_VERSION['registry']),
        Node(f'registry:{tgt}', 'registry', lambda _: _json(destination_registry.read_registry()), volatile=True,
             version=NODE_VERSION['registry']),
        Node('classification', 'classification',
             lambda d: _json(classify_species(d['registry:SR1'], d['registry:SR2'], aliases=aliases)),
             deps=['registry:SR1', 'registry:SR2'], version=NODE_VERSION['classification'],
             params={'aliases': alias_params})], store, values=values)
    classification = values.get('classification')
    rows = [] if classification is None else [
        r for r in classification['species'] if r['classification'] == f'{src}_ONLY'
        and (only is None or r['canonical_id'] in only)]
    if classification is not None:
        try:
            roots = sorted(root_for(source, row).split(':', 1)[1] for row in rows)
            found = discover(source.provider, roots, max_nodes=max_nodes)
            values['discovery'] = found
            summary = {'graph_hash': found['graph'].graph_hash(), 'inspected': found['inspected'],
                       'truncated': found['truncated']}
            nodes = [_given('classification', 'classification', classification),
                     _given('discovery', 'discovery', found, summary)]
        except Exception as exc:  # the shared walk failing blocks species, never the report
            nodes = [_given('classification', 'classification', classification),
                     Node('discovery', 'discovery', (lambda e: lambda _: (_ for _ in ()).throw(e))(exc),
                          deps=['classification'], volatile=True)]
        for row in rows:
            nodes.extend(_species_nodes(row, values, source, target_profile))
        targets = None if only is None else [f'package:{cid}' for cid in sorted(only)]
        report.update({k: v for k, v in execute(nodes, store, values=values, targets=targets).items()
                       if k not in ('classification',)})
    for row in rows:
        _stage_entries(report, values, row['canonical_id'])
    packages = {row['canonical_id']: values[f'package:{row["canonical_id"]}']['package_id']
                for row in rows if f'package:{row["canonical_id"]}' in values}
    return {'schema': BUILD_SCHEMA, 'schema_version': 1, 'nodes': _sorted(report), 'packages': packages,
            'package_documents': {cid: values[f'package:{cid}'] for cid in packages}, 'counts': _counts(report)}


def _species_nodes(row, values, source, target_profile):
    cid = row['canonical_id']

    def inputs(d):
        graph = d['discovery']['graph']
        root = root_for(source, row)
        deps = graph.dependency_hashes(root) if root in graph.nodes else []
        record = {'cache_inputs': cache_inputs(cid, values['classification'], source, target_profile, graph),
                  'dependency_hashes': [{**x, 'kind': str(x['kind']), 'resolution': str(x['resolution'])}
                                        for x in deps]}
        return record, record

    def conversion(_):
        # Keyed only by inputs:<cid> (= Phase 1 cache inputs): unrelated changes never rebuild it.
        result = convert_species(cid, classification=values['classification'], source=source,
                                 target_profile=target_profile, discovery=values['discovery'])
        return result, result

    def package(d):
        pkg = build_generated_package(d[f'conversion:{cid}'],
                                      dependency_hashes=d[f'inputs:{cid}']['dependency_hashes'])
        return pkg, pkg
    return [Node(f'identity:{cid}', 'identity', lambda _: _json(row), deps=['classification'],
                 version=NODE_VERSION['identity'], volatile=True),
            Node(f'inputs:{cid}', 'inputs', inputs, deps=['discovery', f'identity:{cid}'],
                 version=NODE_VERSION['inputs'], volatile=True),
            Node(f'conversion:{cid}', 'conversion', conversion, deps=[f'inputs:{cid}'],
                 version=NODE_VERSION['conversion']),
            Node(f'package:{cid}', 'package', package, deps=[f'conversion:{cid}', f'inputs:{cid}'],
                 version=NODE_VERSION['package'])]


def _stage_entries(report, values, cid):
    """Expose Phase 1 stages (IR, extraction, target plan...) as derived build nodes."""
    conv = report.get(f'conversion:{cid}')
    result = values.get(f'conversion:{cid}')
    if not conv or conv['status'] not in ('RUN', 'REUSED') or not isinstance(result, dict):
        return
    for stage in result.get('stages', []):
        node_id = f'stage:{cid}:{stage["stage"]}'
        report[node_id] = {'id': node_id, 'kind': 'stage', 'status': stage['status'], 'deps': [conv['id']],
                           'output_hash': stage['output_hash'], 'key': conv['key'],
                           'version': NODE_VERSION['conversion'], 'input_hashes': {},
                           'diagnostics': [D.from_phase1(x, 'conversion', content_id=cid)
                                           for x in stage['diagnostics'] if x['severity'] == 'error']}


def _sorted(report):
    return [report[k] for k in sorted(report)]


def _counts(report):
    counts = {}
    for entry in report.values():
        key = f"{entry['kind']}:{entry['status']}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
