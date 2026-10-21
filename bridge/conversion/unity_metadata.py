"""Read-only Unity serialized metadata -> existing ManifestSource descriptors.

No YAML loader, asset extraction, neutral type/profile inference, or runtime claim.
Objects retain the actual serialized class tag/type as metadata; neutral kind stays
unknown until a separately verified mapper exists. All edges are generic references.
"""

import hashlib
import os
import re
from collections import deque
from pathlib import Path

from bridge.content.canonical import DataEvidence, diag, sha256_json, sort_diagnostics
from .manifest import SOURCE_SCHEMA, SOURCE_SCHEMA_VERSION

_GUID = re.compile(r'[0-9a-f]{32}')
_ANCHOR = re.compile(r'^--- !u!(\d+) &(-?\d+)( stripped)?\s*$', re.MULTILINE)
_FLOW = re.compile(r'\{[^{}]*\}')
_FILE_ID = re.compile(r'(?:0|[1-9][0-9]*|-[1-9][0-9]*)')
_RESERVED_GUIDS = {'0' * 32, '0000000000000000e000000000000000',
                   '0000000000000000f000000000000000'}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _ref(guid, file_id):
    return f'unity:{guid}:{file_id}'


def _mask_scalar_text(text):
    """Keep offsets, but hide strings/comments/block scalars from pointer recognition."""
    chars, quote, block_indent, position = list(text), None, None, 0
    for line in text.splitlines(keepends=True):
        indent = len(line) - len(line.lstrip(' '))
        if block_indent is not None and (not line.strip() or indent > block_indent):
            for index in range(position, position + len(line)):
                if chars[index] not in '\r\n':
                    chars[index] = ' '
            position += len(line)
            continue
        block_indent = None
        index, end = position, position + len(line)
        while index < end:
            ch = text[index]
            if quote:
                chars[index] = ' ' if ch not in '\r\n' else ch
                if quote == '"' and ch == '\\' and index + 1 < end:
                    index += 1
                    chars[index] = ' '
                elif ch == quote:
                    if quote == "'" and index + 1 < end and text[index + 1] == "'":
                        index += 1
                        chars[index] = ' '
                    else:
                        quote = None
            elif ch in "\"'" and (index == position or text[position:index].rstrip().endswith((':', '-', '[', '{', ','))):
                quote, chars[index] = ch, ' '
            elif ch == '#' and (index == position or text[index - 1].isspace()):
                for rest in range(index, end):
                    if chars[rest] not in '\r\n':
                        chars[rest] = ' '
                break
            index += 1
        masked = ''.join(chars[position:end]).rstrip()
        if re.search(r':\s*[|>][0-9+-]*\s*$', masked):
            block_indent = indent
        position = end
    return ''.join(chars)


def _reference_tokens(body):
    """Recognize flat flow/block PPtrs; preserve raw tokens but never load YAML."""
    masked = _mask_scalar_text(body)
    found, spans = [], []
    value_prefix = re.compile(r'^\s*(?:-\s*)?(?:[^:{}\[\],]+:\s*)?$')
    brackets, scan_pos = [], 0
    for match in _FLOW.finditer(masked):
        for marker in re.finditer(r'[\[\]]', masked[scan_pos:match.start()]):
            if marker[0] == '[':
                brackets.append(scan_pos + marker.start())
            elif brackets:
                brackets.pop()
        scan_pos = match.end()
        spans.append((match.start(), match.end()))
        if not re.search(r'\bfileID\s*:', match[0]):
            continue
        prefix = masked[masked.rfind('\n', 0, match.start()) + 1:match.start()]
        if brackets:
            start = brackets[0]
            before = masked[masked.rfind('\n', 0, start) + 1:start]
            after = masked[start + 1:match.start()]
            at_value = bool(value_prefix.fullmatch(before)) and not re.sub(r'[\s,\[\]]', '', _FLOW.sub('', after))
        elif prefix.strip():
            at_value = bool(value_prefix.fullmatch(prefix))
        else:
            previous = masked[:match.start()].rstrip().splitlines()
            at_value = bool(previous and previous[-1].rstrip().endswith(':'))
        if not at_value:  # braces inside a plain scalar are not PPtr mappings
            continue
        raw = body[match.start():match.end()]
        tokens, valid = {}, True
        cuts = [0] + [m.start() for m in re.finditer(',', match[0][1:-1])] + [len(raw) - 2]
        # Commas masked inside quoted scalars are deliberately not separators.
        interior = raw[1:-1]
        begin = 0
        for end in cuts[1:]:
            part = interior[begin:end]
            key, sep, value = part.strip().partition(':')
            if not sep or key in tokens or key not in ('fileID', 'guid', 'type'):
                valid = False
            tokens.setdefault(key, value.strip())
            begin = end + 1
        found.append((match.start(), tokens, valid, _sha(raw.encode())))
    group, offset, depth, position, valid, span_index = {}, 0, None, 0, True, 0
    block = re.compile(r'^( *)(- )?([^:\s]+):[ \t]*(.*?)\s*$')
    for line in masked.splitlines(keepends=True) + ['END']:
        while span_index < len(spans) and spans[span_index][1] <= position:
            span_index += 1
        in_flow = span_index < len(spans) and spans[span_index][0] <= position < spans[span_index][1]
        if not line.strip():  # blank/comment lines do not end a mapping
            position += len(line)
            continue
        match = block.fullmatch(line.rstrip('\r\n')) if not in_flow else None
        indent = len(match[1]) + (2 if match[2] else 0) if match else None
        if not match or indent != depth or match[2]:
            if 'fileID' in group:
                found.append((offset, group, valid, _sha(body[offset:position].encode())))
            group, offset, depth, valid = {}, position, indent, True
        if match:
            key = match[3]
            raw_line = body[position:position + len(line)].rstrip('\r\n')
            value = raw_line[match.start(4):].strip()
            if key in group or key not in ('fileID', 'guid', 'type'):
                valid = False
            group.setdefault(key, value)
        position += len(line)
    return sorted(found, key=lambda item: item[0])


def _objects(raw, entry, source_sha, max_nodes):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return {}
    if not text.startswith(('%YAML ', '--- !u!')):
        return {}
    anchors = []
    for anchor in _ANCHOR.finditer(text):
        anchors.append(anchor)
        if len(anchors) > max_nodes:
            raise ValueError('Unity metadata node limit exceeded (object anchors)')
    result = {}
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
        body = text[anchor.end():end]
        type_match = re.match(r'\s*([A-Za-z_][A-Za-z_0-9]*):', body)
        guid, file_id = entry['guid'], anchor[2]
        identity = {'guid': guid, 'file_id': file_id, 'class_id': anchor[1],
                    'serialized_type': type_match[1] if type_match else None,
                    'stripped': bool(anchor[3]), 'asset_path': entry['asset_path'],
                    'meta_path': entry['meta_path'], 'meta_sha256': entry['meta_sha256'],
                    'source_sha256': source_sha}
        provenance = f"unity-metadata:PARTIAL:UNVERIFIED:{entry['asset_path']}#{file_id}"
        references, records = [], []
        for offset, tokens, valid, token_sha in _reference_tokens(body):
            redacted = sorted(k for k in ('fileID', 'guid', 'type') if len(tokens.get(k, '')) > 256)
            valid = valid and not redacted and bool(_FILE_ID.fullmatch(tokens.get('fileID', ''))) and \
                ('guid' not in tokens or bool(_GUID.fullmatch(tokens['guid']))) and \
                ('type' not in tokens or bool(re.fullmatch(r'[0-9]+', tokens['type'])))
            status = 'NULL' if valid and tokens['fileID'] == '0' else 'PENDING'
            target = None if status == 'NULL' else _ref(tokens.get('guid', guid), tokens.get('fileID'))
            if not valid:
                status = 'UNSUPPORTED_REFERENCE'
                target = 'unity-unsupported:' + sha256_json([guid, file_id, offset, token_sha])
            slot = f'offset:{anchor.end() + offset}'
            records.append({'file_id': tokens.get('fileID') if 'fileID' not in redacted else None,
                            'guid': tokens.get('guid') if 'guid' not in redacted else None,
                            'type': tokens.get('type') if 'type' not in redacted else None,
                            'target': target, 'slot': slot, 'status': status,
                            'token_sha256': token_sha, 'redacted_fields': redacted})
            if target:
                references.append({'relation': 'references', 'target': target, 'slot': slot,
                                   'critical': False, 'target_kind': 'unknown', 'candidates': None,
                                   'provenance': provenance})
        ref = _ref(guid, file_id)
        if ref in result:
            raise ValueError(f'Duplicate Unity fileID {ref}')
        result[ref] = {'ref': ref, 'kind': 'unknown', 'hash': _sha(text[anchor.start():end].encode()),
                       'attributes': {'unity': identity, 'unity_references': records}, 'references': references,
                       'provenance': provenance}
    return result


def _is_link(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def _metadata_paths(root, maximum, diagnostics):
    paths = []
    def read_error(exc):
        raise ValueError('Cannot traverse Unity metadata index') from exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=read_error):
        for name in list(dirs) + list(files):
            path = Path(directory) / name
            if _is_link(path):
                diagnostics.append(diag('UNITY_METADATA_LINK_SKIPPED', 'warning',
                                        path.relative_to(root).as_posix(), 'Not followed or read'))
                if name in dirs:
                    dirs.remove(name)
                continue
            if name in files and name.endswith('.meta'):
                paths.append(path)
                if len(paths) > maximum:
                    raise ValueError('Unity metadata index limit exceeded')
    return sorted(paths)


def _bounded_read(path, maximum, root):
    try:
        if _is_link(path) or not path.resolve().is_relative_to(root):
            return None, 'OUTSIDE_ROOT_OR_LINK'
        if path.is_dir():
            return None, 'OPAQUE_DIRECTORY'
        if path.stat().st_size > maximum:
            return None, 'FILE_SIZE_LIMIT'
        with path.open('rb') as handle:
            raw = handle.read(maximum + 1)
        return (None, 'FILE_SIZE_LIMIT') if len(raw) > maximum else (raw, 'READ')
    except FileNotFoundError:
        return None, 'MISSING_ASSET'
    except OSError:
        return None, 'READ_ERROR'


def _target_status(ref, by_guid, loaded, observations):
    guid = ref.split(':')[1]
    if guid in _RESERVED_GUIDS:
        return 'BUILTIN_OR_RESERVED_UNVERIFIED'
    if guid not in by_guid:
        return 'MISSING_GUID'
    status = observations.get(guid, {}).get('status')
    if status and status != 'SERIALIZED_METADATA':
        return status
    return 'RESOLVED' if ref in loaded.get(guid, {}) else 'MISSING_FILEID'


def read_unity_metadata(root, *, realm, roots, synthetic=False,
                        max_meta_files=10000, max_file_bytes=8 * 1024 * 1024, max_nodes=20000):
    """Return a metadata-only source manifest for explicitly supplied Unity object roots.

    ``root`` is the caller-authorized export Assets directory, never auto-discovered.
    ``roots`` maps caller native refs to ``unity:<lowercase GUID>:<fileID>``.
    This is an export graph slice, not a registry traversal or verified game build.
    """
    if realm not in ('SR1', 'SR2') or type(synthetic) is not bool:
        raise ValueError('Unity metadata requires SR1/SR2 realm and boolean synthetic flag')
    if not isinstance(roots, dict) or not roots:
        raise ValueError('Explicit nonempty Unity object roots are required')
    for native, ref in roots.items():
        if not isinstance(native, str) or not native or not isinstance(ref, str) or \
                not re.fullmatch(r'unity:[0-9a-f]{32}:(?:[1-9][0-9]*|-[1-9][0-9]*)', ref):
            raise ValueError('Root must be unity:<lowercase GUID>:<canonical nonzero fileID>')
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Explicit Unity metadata root must be a directory')
    if any(type(n) is not int or n < 1 for n in (max_meta_files, max_file_bytes, max_nodes)):
        raise ValueError('Unity metadata bounds must be positive integers')
    index, diagnostics = [], []
    by_guid = {}
    for path in _metadata_paths(root, max_meta_files, diagnostics):
        raw, status = _bounded_read(path, max_file_bytes, root)
        if raw is None:
            raise ValueError(f'Cannot index Unity metadata: {status}: {path.name}')
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')
        guid_lines = re.findall(r'^guid:[ \t]*(.*?)[ \t]*$', text, re.MULTILINE)
        if len(guid_lines) > 1:
            raise ValueError('Ambiguous Unity GUID lines in one metadata sidecar')
        if not guid_lines or not _GUID.fullmatch(guid_lines[0]):
            diagnostics.append(diag('UNITY_METADATA_GUID_UNSUPPORTED', 'warning',
                                    path.relative_to(root).as_posix(), _sha(raw)))
            continue
        entry = {'guid': guid_lines[0], 'meta_path': path.relative_to(root).as_posix(),
                 'asset_path': path.with_suffix('').relative_to(root).as_posix(),
                 'meta_sha256': _sha(raw)}
        if entry['guid'] in by_guid:
            raise ValueError(f"Duplicate Unity GUID {entry['guid']}")
        index.append(entry)
        by_guid[entry['guid']] = entry
    pending, seen, loaded, objects = deque(sorted(set(roots.values()))), set(), {}, []
    observations = {}
    while pending:
        ref = pending.popleft()
        if ref in seen:
            continue
        if len(seen) >= max_nodes:
            raise ValueError('Unity metadata node limit exceeded')
        seen.add(ref)
        if ref.startswith('unity-unsupported:'):
            continue
        guid = ref.split(':')[1]
        if guid in _RESERVED_GUIDS:
            continue
        if guid not in loaded:
            entry = by_guid.get(guid)
            loaded[guid] = {}
            if entry:
                raw, status = _bounded_read(root / entry['asset_path'], max_file_bytes, root)
                source_sha = _sha(raw) if raw is not None else None
                if raw is not None:
                    loaded[guid] = _objects(raw, entry, source_sha, max_nodes)
                    status = 'SERIALIZED_METADATA' if loaded[guid] else 'OPAQUE_ASSET'
                observations[guid] = {'asset_path': entry['asset_path'],
                                      'source_sha256': source_sha,
                                      'status': status}
        found = loaded[guid].get(ref)
        if found:
            objects.append(found)
            pending.extend(item['target'] for item in found['references'])
    unresolved = []
    for obj in objects:
        for record in obj['attributes']['unity_references']:
            if record['status'] == 'NULL':
                continue
            if record['status'] == 'UNSUPPORTED_REFERENCE':
                unresolved.append({'source': obj['ref'], **record, 'provenance': obj['provenance']})
                continue
            record['status'] = _target_status(record['target'], by_guid, loaded, observations)
            if record['status'] != 'RESOLVED':
                unresolved.append({'source': obj['ref'], **record, 'provenance': obj['provenance']})
    evidence = str(DataEvidence.SYNTHETIC if synthetic else DataEvidence.STATIC)
    build = {'fingerprint': 'UNVERIFIED:unity-metadata-index:' + sha256_json(index),
             'label': 'PARTIAL export; game build UNVERIFIED'}
    registry = {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': realm,
                'build': dict(build), 'data_evidence': evidence,
                'traversal': {'completeness': 'PARTIAL', 'registry_identity': 'unverified-export-metadata',
                              'expected_count': None, 'observed_count': 0},
                'entries': [], 'diagnostics': []}
    return {'schema': SOURCE_SCHEMA, 'schema_version': SOURCE_SCHEMA_VERSION,
            'realm': realm, 'synthetic': synthetic, 'data_evidence': evidence,
            'build': build, 'registry': registry, 'roots': dict(roots),
            'objects': sorted(objects, key=lambda item: item['ref']),
            'extensions': {'unity_metadata': {'source_root': str(root), 'read_only': True,
                           'export_completeness': 'PARTIAL', 'build_verification': 'UNVERIFIED',
                           'active_runtime_equivalence': 'UNVERIFIED', 'guid_index': index,
                           'root_observations': {native: {'ref': ref, 'status': _target_status(
                               ref, by_guid, loaded, observations)} for native, ref in sorted(roots.items())},
                           'asset_observations': observations,
                           'unresolved_references': unresolved, 'diagnostics': sort_diagnostics(diagnostics)}}}
