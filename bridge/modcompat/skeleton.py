"""Safe adapter skeletons for mods that cannot simply be deployed on the target realm.

``generate_adapter_skeleton(report, out_root=...)`` turns one ``bridge-mod-compat-report`` into a
small, deterministic folder of TEMPLATES for a human or a later local step:

- ``adapter.json``       metadata (source/target, verdict, capability map, LOCAL_QUERY list)
- ``<Class>Adapter.cs``  a C# class skeleton for the TARGET loader (SRML ``ModEntryPoint``-style for
                         SR1, ``MelonMod``-style for SR2) containing only loader lifecycle names that
                         repository evidence establishes and ``// TODO(LOCAL_QUERY Qxxx)`` comments
- ``CAPABILITIES.md``    source capability -> target capability -> status table
- ``TEST_CHECKLIST.md``  what local proof is needed before anyone may call the adapter working
- ``LOCAL_QUERIES.md``   the queries the local provider must answer first

What it never does: reconstruct or paraphrase the source mod's code, copy evidence strings (type,
member, Harmony target, file or config names) into output, call a game API, or invent a native
binding. Only the mod id, a sanitized display name and the numeric version reach the output.

Writes go through ``bridge.cache.fs.atomic_write`` under ``out_root``; ``subdir`` must be a safe
relative path; a symlinked root or a symlink anywhere on an output path refuses the whole write
before any file is touched. Output bytes depend only on the report (no clock, no environment).
"""

import hashlib
import re
from pathlib import PurePosixPath

from bridge.cache.fs import atomic_write, confined_root, refuse_links
from bridge.content.canonical import pretty_json
from bridge.core import diagnostics as D
from bridge.core.paths import safe_relative

from .analyzer import LOCAL_QUERIES, validate_report
from .manifest import deploy_key_id, diag, printable

SKELETON_SCHEMA = 'bridge-mod-adapter-skeleton'
RESULT_SCHEMA = 'bridge-mod-adapter-skeleton-result'
GENERATOR_VERSION = '1.0.0'
ALWAYS_QUERY = 'Q056'  # every MAPPED row still needs confirmation against the exact target lab build
# Loader templates: only names established by repository evidence (docs/RECON.md loader facts;
# mods/sr1-bridge/BridgeEntry.cs; mods/sr2-bridge/BridgeMod.cs and both .csproj files). No game API.
TEMPLATES = {
    'SR1': {'loader': 'SRML', 'framework': 'net472', 'namespace_import': 'SRML', 'base': 'ModEntryPoint',
            'methods': ('PreLoad', 'Load', 'PostLoad'), 'source': 'docs/RECON.md (SRML 0.2.1.0 ModEntryPoint)'},
    'SR2': {'loader': 'MELONLOADER', 'framework': 'net6.0', 'namespace_import': 'MelonLoader', 'base': 'MelonMod',
            'methods': ('OnInitializeMelon', 'OnUpdate', 'OnDeinitializeMelon'),
            'melon_game': ('MonomiPark', 'SlimeRancher2'), 'source': 'docs/RECON.md (MelonLoader 0.7.3 MelonMod)'},
}
_LABEL = re.compile(r'[^A-Za-z0-9 _-]+')


def _label(text, fallback):
    label = re.sub(r'\s+', ' ', _LABEL.sub(' ', text or '')).strip()[:64]
    return label or fallback


def _class_name(mod_id):
    pascal = ''.join(p[:1].upper() + p[1:] for p in re.split(r'[^a-z0-9]+', mod_id) if p)
    if not pascal[:1].isalpha():
        pascal = 'Mod' + pascal
    return pascal[:64]


def _folder(mod_id, realm):
    slug = deploy_key_id(mod_id)
    if len(slug) > 100:
        slug = f"{slug[:100]}-{hashlib.sha256(mod_id.encode('utf-8')).hexdigest()[:8]}"
    return f'{slug}.{realm.lower()}'


def _version(report):
    return '.'.join(map(str, report['mod']['version_parts'])) or 'unknown'


def _queries(report):
    return sorted(set(report['local_queries']) | {ALWAYS_QUERY})


def _todo_lines(report):
    rows = report['required_capabilities']
    lines = [f'// TODO(LOCAL_QUERY {ALWAYS_QUERY}): confirm every MAPPED capability row against the exact '
             f'{report["target_realm"]} lab build; MAPPED is a catalogue counterpart, not proof.']
    for row in rows:
        if row['status'] == 'MAPPED':
            lines.append(f"// MAPPED (NEEDS_LOCAL_RUNTIME_PROOF): '{row['source_capability']}' -> "
                         f"'{row['target_api'] or row['target_capability']}'.")
        else:
            lines.append(f"// TODO(LOCAL_QUERY {row['local_query'] or ALWAYS_QUERY}): '{row['source_capability']}' "
                         f"is {row['status']}; no native binding is known in Cloud.")
    harmony = report['harmony'] or {}
    if harmony.get('patch_count') or harmony.get('patch_all_calls'):
        lines.append(f"// TODO(LOCAL_QUERY Q055): the source mod applies {harmony.get('patch_count', 0)} Harmony "
                     'patch(es); targets are listed by index in the local evidence only. Re-implement each against '
                     'verified target members; source patch bodies are not reproduced.')
    native = report['native'] or {}
    if native.get('pinvoke_modules') or native.get('detour_indicators') or native.get('function_pointer_hooks'):
        lines.append('// TODO(LOCAL_QUERY Q057): the source mod uses native hooks; Bridge never generates or '
                     'enables them.')
    covered = {row['local_query'] for row in rows} | {ALWAYS_QUERY, 'Q055', 'Q057'}
    for query in _queries(report):
        if query not in covered:
            lines.append(f'// TODO(LOCAL_QUERY {query}): {LOCAL_QUERIES.get(query, "see docs/MOD_COMPATIBILITY.md")}.')
    return lines


def _csharp(report, names):
    template = TEMPLATES[report['target_realm']]
    namespace = f"SlimeBridge.Adapters.{names['class_base']}"
    cls = f"{names['class_base']}Adapter"
    head = [
        '// <auto-generated>',
        f'// SlimeBridge adapter SKELETON (generator {GENERATOR_VERSION}). TEMPLATE ONLY: not a port of the source',
        '// mod, does not reconstruct its code and calls no game API. Every native binding is a',
        '// TODO(LOCAL_QUERY) to answer from the exact lab build before any implementation.',
        f"// Source mod : {report['mod']['id']} \"{names['label']}\" {names['version']} "
        f"({report['source_realm']}, {report['mod']['loader']})",
        f"// Target     : {report['target_realm']} / {template['loader']} / {template['framework']} "
        f"({template['source']})",
        f"// Verdict    : {report['classification']} -> {report['portability']} (report {report['report_id']})",
        '// Evidence   : BLOCKED_ON_LOCAL_QUERY',
        '// </auto-generated>',
        '',
        f"using {template['namespace_import']};",
        '',
    ]
    if report['target_realm'] == 'SR2':
        developer, game = template['melon_game']
        head += [f'[assembly: MelonInfo(typeof({namespace}.{cls}), "{names["label"]} (SlimeBridge adapter)", '
                 '"0.0.0", "SlimeBridge adapter skeleton")]',
                 f'[assembly: MelonGame("{developer}", "{game}")]', '']
    body = [f'namespace {namespace}', '{',
            f"    /// <summary>Adapter skeleton for '{report['mod']['id']}'. Not functional.</summary>",
            f"    public sealed class {cls} : {template['base']}", '    {']
    for i, method in enumerate(template['methods']):
        body.append(f'        public override void {method}()')
        body.append('        {')
        if i == 0:
            body.extend('            ' + line for line in _todo_lines(report))
        body.append('        }')
        if i + 1 < len(template['methods']):
            body.append('')
    body += ['    }', '}', '']
    return '\n'.join(head + body)


def _md_cell(value):
    return '-' if value is None else str(value).replace('|', '/')


def _capabilities_md(report, names):
    lines = [f"# Capability mapping: {report['mod']['id']} ({report['source_realm']} -> {report['target_realm']})", '',
             f"Generated from report `{report['report_id']}`. Every row is NEEDS_LOCAL_RUNTIME_PROOF; MAPPED means a "
             'catalogue counterpart exists, not that it works.', '',
             '| Source capability | Target capability | Status | Target API | Local query |',
             '|---|---|---|---|---|']
    rows = report['required_capabilities']
    for row in rows:
        lines.append(f"| {_md_cell(row['source_capability'])} | {_md_cell(row['target_capability'])} | "
                     f"{row['status']} | {_md_cell(row['target_api'])} | {_md_cell(row['local_query'])} |")
    if not rows:
        lines.append(f"| (none: {report['classification']}) | - | - | - | {', '.join(_queries(report))} |")
    return '\n'.join(lines) + '\n'


def _checklist_md(report, names):
    target = report['target_realm']
    lines = [f"# Test checklist: {report['mod']['id']} -> {target}", '',
             'Evidence: BLOCKED_ON_LOCAL_QUERY. Nothing below has been run; a build alone is not in-game proof.', '',
             '- [ ] Answer every query in `LOCAL_QUERIES.md` from the exact lab build and record it.',
             f"- [ ] Build against the exact {target} lab assemblies ({TEMPLATES[target]['framework']}); never "
             'commit them.',
             '- [ ] Fingerprint the lab copy; check save/profile isolation and backups before launching.',
             '- [ ] Verify PID and executable path before controlling the game process.',
             '- [ ] Both plugin heartbeats reach one BridgeHost with the adapter installed.']
    for row in report['required_capabilities']:
        lines.append(f"- [ ] `{row['source_capability']}` ({row['status']}): one LAB observation on {target}.")
    if report['harmony'] and (report['harmony']['patch_count'] or report['harmony']['patch_all_calls']):
        lines.append('- [ ] Each re-implemented Harmony patch is verified against the target member (Q055).')
    lines += ['- [ ] Install and rollback through the journaled deploy executor leave no stray files.',
              '- [ ] Independent review of the adapter before it is enabled for anyone.']
    return '\n'.join(lines) + '\n'


def _queries_md(report):
    lines = [f"# LOCAL_QUERY list: {report['mod']['id']} -> {report['target_realm']}", '',
             'Answer in the docs/LOCAL_QUERIES.md format (Status / local answer). None of these is answered.', '']
    lines += [f"- {q}: {LOCAL_QUERIES.get(q, 'see docs/MOD_COMPATIBILITY.md')}" for q in _queries(report)]
    return '\n'.join(lines) + '\n'


def render_adapter_skeleton(report):
    """``({file name: text} | None, diagnostics)``: pure and deterministic; nothing is written."""
    problems = validate_report(report)
    if problems:
        return None, problems
    if report['mod']['id'] is None or report['source_realm'] is None or report['target_realm'] is None:
        return None, [diag('SKELETON_INPUT_UNUSABLE', 'error', 'report has no valid mod id or realm')]
    if report['portability'] == 'PORTABLE' and not report['cross_realm']:
        return {}, [diag('SKELETON_NOT_NEEDED', 'info', 'same-realm PORTABLE mod needs no adapter',
                         subject=report['mod']['id'])]
    mod_id = report['mod']['id']
    base = _class_name(mod_id)
    names = {'class_base': base, 'label': _label(report['mod']['name'], mod_id), 'version': _version(report)}
    template = TEMPLATES[report['target_realm']]
    metadata = {
        'schema': SKELETON_SCHEMA, 'schema_version': 1, 'generator_version': GENERATOR_VERSION,
        'template_only': True, 'evidence': 'BLOCKED_ON_LOCAL_QUERY',
        'source': {'mod_id': mod_id, 'name': names['label'], 'version': names['version'],
                   'realm': report['source_realm'], 'loader': report['mod']['loader'],
                   'manifest_id': report['mod']['manifest_id'], 'report_id': report['report_id']},
        'target': {'realm': report['target_realm'], 'loader': template['loader'], 'framework': template['framework'],
                   'class': f'SlimeBridge.Adapters.{base}.{base}Adapter'},
        'classification': report['classification'], 'portability': report['portability'],
        'capability_map': report['required_capabilities'],
        'harmony_patch_count': (report['harmony'] or {}).get('patch_count', 0),
        'native_hooks': bool(report['native'] and (report['native']['pinvoke_modules'] or
                                                   report['native']['detour_indicators'] or
                                                   report['native']['function_pointer_hooks'])),
        'local_queries': [{'id': q, 'title': LOCAL_QUERIES.get(q, 'see docs/MOD_COMPATIBILITY.md')}
                          for q in _queries(report)],
    }
    files = {f'{base}Adapter.cs': _csharp(report, names), 'CAPABILITIES.md': _capabilities_md(report, names),
             'TEST_CHECKLIST.md': _checklist_md(report, names), 'LOCAL_QUERIES.md': _queries_md(report)}
    metadata['files'] = sorted([*files, 'adapter.json'])
    files['adapter.json'] = pretty_json(metadata)
    return dict(sorted(files.items())), []


def _result(status, diagnostics, directory=None, files=()):
    return {'schema': RESULT_SCHEMA, 'schema_version': 1, 'status': status, 'directory': directory,
            'files': list(files), 'diagnostics': D.sort(list(diagnostics))}


def generate_adapter_skeleton(report, *, out_root, subdir='adapters'):
    """Write the skeleton under ``out_root/subdir/<mod>.<realm>/``. Returns a result dict; never raises
    for unsafe input (status REFUSED, nothing written), SKIPPED when no adapter is needed."""
    files, problems = render_adapter_skeleton(report)
    if files is None:
        return _result('REFUSED', problems)
    if not files:
        return _result('SKIPPED', problems)
    if not safe_relative(subdir):
        return _result('REFUSED', [diag('SKELETON_PATH_UNSAFE', 'error', 'subdir must be a safe relative path',
                                        details={'subdir': printable(subdir, 200)})])
    directory = f"{subdir}/{_folder(report['mod']['id'], report['target_realm'])}"
    rels = [f'{directory}/{name}' for name in files]
    if not all(safe_relative(rel) for rel in rels):
        return _result('REFUSED', [diag('SKELETON_PATH_UNSAFE', 'error', 'generated path is unsafe')])
    try:
        root = confined_root(out_root)
        if root.exists() and not root.is_dir():
            raise ValueError('output root is not a directory')
        targets = [root / PurePosixPath(rel) for rel in rels]
        for path in targets:  # preflight: refuse before anything is written
            refuse_links(root, path)
            if path.exists() and (path.is_dir() or path.stat().st_nlink > 1):
                raise ValueError(f'refusing to replace {path.name}')
        written = []
        for rel, path, text in zip(rels, targets, files.values()):
            data = text.encode('utf-8')
            atomic_write(root, path, data)
            written.append({'path': rel, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    except (ValueError, OSError) as exc:
        return _result('REFUSED', [diag('SKELETON_WRITE_REFUSED', 'error', 'output location refused',
                                        details={'reason': type(exc).__name__, 'detail': printable(str(exc), 200)})])
    return _result('WRITTEN', problems, directory, written)
