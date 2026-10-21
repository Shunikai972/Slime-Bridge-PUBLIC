"""Analyse one game installation (read-only) into a JSON-serialisable ``InstallInfo`` dict.

Nothing about the Unity version, backend or layout is hard-coded: every field is derived from the
files on disk. ``recognition`` compares the result against ``known_versions.json`` and, for unknown
combinations, reports a diagnostic instead of claiming compatibility.
"""

import json
import os
import re
import struct
from pathlib import Path

from ..safefs import sha256_file
from . import unity_serialized as us
from .discovery import GAMES

KNOWN_VERSIONS = Path(__file__).resolve().parent.parent / 'registry' / 'known_versions.json'

MACHINES = {0x14c: 'x86', 0x8664: 'x64', 0xaa64: 'arm64'}


def pe_machine(exe):
    with open(exe, 'rb') as f:
        head = f.read(4096)
    off, = struct.unpack('<I', head[0x3c:0x40])
    if head[off:off + 4] != b'PE\0\0':
        return 'unknown'
    return MACHINES.get(struct.unpack('<H', head[off + 4:off + 6])[0], 'unknown')


def exe_version(exe):
    """FileVersion / ProductVersion strings via the Windows version API (None elsewhere)."""
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL('version', use_last_error=True)
        size = ver.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(exe), 0, size, buf):
            return {}
        out = {}
        trans = ctypes.c_void_p()
        n = wintypes.UINT()
        if ver.VerQueryValueW(buf, '\\VarFileInfo\\Translation', ctypes.byref(trans), ctypes.byref(n)) and n.value >= 4:
            lang, cp = struct.unpack('<HH', ctypes.string_at(trans, 4))
            for key in ('FileVersion', 'ProductVersion', 'ProductName', 'CompanyName'):
                val = ctypes.c_void_p()
                if ver.VerQueryValueW(buf, f'\\StringFileInfo\\{lang:04x}{cp:04x}\\{key}', ctypes.byref(val), ctypes.byref(n)) and n.value:
                    out[key] = ctypes.wstring_at(val.value, n.value).rstrip('\0')
        return out
    except Exception:
        return {}


def _serialized_files(data_dir):
    """Top-level serialized containers (globalgamemanagers, levelN, sharedassetsN.assets, ...)."""
    rows = []
    for p in sorted(Path(data_dir).iterdir(), key=lambda q: q.name):
        if not p.is_file() or p.suffix in ('.resS', '.resource', '.json', '.dat', '.txt', '.info', '.config'):
            continue
        if p.name in ('app.info', 'boot.config'):
            continue
        try:
            h = us.read_header(p)
        except (us.NotSerializedFile, OSError, ValueError, struct.error):
            continue
        rows.append({'name': p.name, 'size': p.stat().st_size, **h.as_dict()})
    return rows


def _bundles(data_dir, sample=4):
    aa = Path(data_dir) / 'StreamingAssets' / 'aa'
    has_catalog = aa.is_dir() and any((aa / n).is_file() for n in ('catalog.json', 'catalog.bin'))
    info = {'addressables': bool(has_catalog), 'directory': str(aa) if aa.is_dir() else None, 'count': 0,
            'total_bytes': 0, 'headers': [], 'engine_versions': []}
    files = []
    for ext in ('*.bundle', '*.unity3d', '*.assetbundle'):
        files += list(Path(data_dir).rglob(ext))
    info['count'] = len(files)
    info['total_bytes'] = sum(f.stat().st_size for f in files)
    engines = set()
    for f in sorted(files)[:max(sample, 0)] if sample else []:
        try:
            hdr = us.read_bundle_header(f)
        except (ValueError, OSError, struct.error):
            continue
        info['headers'].append({'file': f.name, **hdr})
    # engine revision across *all* bundles is cheap (header only): needed to spot mixed versions
    for f in files:
        try:
            engines.add(us.read_bundle_header(f)['engine_version'])
        except (ValueError, OSError, struct.error):
            pass
    info['engine_versions'] = sorted(engines)
    info['catalog'] = None
    cat = aa / 'catalog.json'
    if cat.is_file():
        try:
            data = json.loads(cat.read_text(encoding='utf-8'))
            info['catalog'] = {'file': 'catalog.json', 'internal_ids': len(data.get('m_InternalIds', [])),
                               'provider_ids': sorted(set(data.get('m_ProviderIds', [])))}
        except (OSError, ValueError):
            info['catalog'] = {'file': 'catalog.json', 'error': 'unreadable'}
    return info


def _managed_info(data_dir):
    managed = Path(data_dir) / 'Managed'
    if not managed.is_dir():
        return None
    return {'assemblies': len(list(managed.glob('*.dll'))),
            'assembly_csharp': (managed / 'Assembly-CSharp.dll').is_file(),
            'mscorlib': (managed / 'mscorlib.dll').is_file()}


def _player_settings(data_dir):
    """PlayerSettings (productName/bundleVersion) and GraphicsSettings via UnityPy, when installed."""
    out = {'product_name': None, 'company_name': None, 'bundle_version': None,
           'color_space': None, 'graphics': {'custom_render_pipeline': None, 'always_included_shaders': None,
                                              'srp_detected': None, 'pipeline': None}}
    try:
        import UnityPy
        env = UnityPy.load(str(Path(data_dir) / 'globalgamemanagers'))
    except Exception as e:
        out['error'] = f'UnityPy unavailable or failed: {type(e).__name__}: {e}'
        return out
    try:
        for obj in env.objects:
            if obj.type.name == 'PlayerSettings':
                tree = obj.read_typetree()
                out['product_name'] = tree.get('productName')
                out['company_name'] = tree.get('companyName')
                out['bundle_version'] = tree.get('bundleVersion')
                cs = tree.get('m_ActiveColorSpace')
                out['color_space'] = {0: 'Gamma', 1: 'Linear'}.get(cs, cs)
            elif obj.type.name == 'GraphicsSettings':
                tree = obj.read_typetree()
                crp = tree.get('m_CustomRenderPipeline')
                pid = crp.get('m_PathID') if isinstance(crp, dict) else None
                defaults = tree.get('m_SRPDefaultSettings') or []
                names = [d[0] for d in defaults if isinstance(d, (list, tuple)) and d]
                out['graphics']['custom_render_pipeline'] = bool(pid)
                out['graphics']['srp_detected'] = bool(pid) or bool(names)
                out['graphics']['srp_types'] = names
                out['graphics']['pipeline'] = ('HDRP' if any('HighDefinition' in n for n in names) else
                                               'URP' if any('Universal' in n for n in names) else
                                               'SRP' if (pid or names) else 'BuiltIn')
                out['graphics']['lights_use_linear_intensity'] = tree.get('m_LightsUseLinearIntensity')
                out['graphics']['always_included_shaders'] = len(tree.get('m_AlwaysIncludedShaders', []))
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
    return out


def _gog_info(root):
    for p in Path(root).glob('goggame-*.info'):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            return {'store': 'gog', 'game_id': d.get('gameId'), 'name': d.get('name'), 'build_id': d.get('buildId')}
        except (OSError, ValueError):
            pass
    return None


def _fingerprint(root, game, full=True):
    spec = GAMES[game]
    data = Path(root) / spec['data']
    files = [Path(root) / spec['exe'], Path(root) / 'UnityPlayer.dll', data / 'globalgamemanagers']
    if game == 'SR2':
        files += [Path(root) / 'GameAssembly.dll', data / 'il2cpp_data' / 'Metadata' / 'global-metadata.dat']
    else:
        files += [data / 'Managed' / 'Assembly-CSharp.dll']
    out = {}
    for f in files:
        if f.is_file():
            rel = f.relative_to(root).as_posix()
            out[rel] = sha256_file(f) if full else None
    return out


def _version_key(unity_version):
    return unity_version


def load_known_versions():
    try:
        return json.loads(KNOWN_VERSIONS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'schema': 1, 'entries': []}


def recognise(info, known=None):
    """Compare a scan to the validated table; never invent compatibility."""
    known = known or load_known_versions()
    diags = []
    unity = info['unity']['version']
    status = 'UNRECOGNIZED'
    matched = None
    for e in known.get('entries', []):
        if e['game'] != info['game']:
            continue
        if e['unity_version'] == unity and e['backend'] == info['backend']:
            matched = e
            fp = e.get('fingerprint') or {}
            if not fp:
                status = 'KNOWN_ENGINE_UNPINNED_BUILD'
                diags.append({'level': 'info', 'code': 'BUILD_NOT_PINNED', 'message': 'Unity version known; this exact build is not fingerprinted yet.'})
                break
            mismatch = [k for k, v in fp.items() if info['fingerprint'].get(k) not in (None, v)]
            if mismatch:
                status = 'KNOWN_ENGINE_NEW_BUILD'
                diags.append({'level': 'warning', 'code': 'BUILD_CHANGED',
                              'message': f'Unity {unity} is known but these files differ from the validated build: {mismatch}. '
                                         'Conversion is attempted but results stay unverified until re-validated.'})
            else:
                status = 'VALIDATED_BUILD'
            break
    if matched is None:
        same_game = [e for e in known.get('entries', []) if e['game'] == info['game']]
        if same_game:
            status = 'UNKNOWN_ENGINE_VERSION'
            diags.append({'level': 'warning', 'code': 'UNKNOWN_UNITY_VERSION',
                          'message': f'{info["game"]} reports Unity {unity}/{info["backend"]}; validated: '
                                     f'{[e["unity_version"] for e in same_game]}. Original files are untouched; '
                                     'run `sbconv scan --json` and attach it to a compatibility report.'})
        else:
            diags.append({'level': 'info', 'code': 'NO_KNOWN_ENTRIES', 'message': 'No validated build table entry for this game.'})
    return {'status': status, 'matched': matched and {k: matched[k] for k in ('unity_version', 'game_version', 'validated') if k in matched}}, diags


def analyse(root, game, full_hash=True, sample_bundles=4):
    spec = GAMES[game]
    root = Path(root)
    data_dir = root / spec['data']
    exe = root / spec['exe']
    diags = []
    ggm = data_dir / 'globalgamemanagers'
    try:
        hdr = us.read_header(ggm)
    except (us.NotSerializedFile, OSError) as e:
        raise RuntimeError(f'{ggm}: cannot read Unity header: {e}')
    headers = _serialized_files(data_dir)
    versions = sorted({h['unity_version'] for h in headers})
    if len(versions) > 1:
        diags.append({'level': 'warning', 'code': 'MIXED_UNITY_VERSIONS', 'message': f'serialized files report {versions}'})
    il2cpp = (root / 'GameAssembly.dll').is_file() or (data_dir / 'il2cpp_data').is_dir()
    mono = (data_dir / 'Managed' / 'mscorlib.dll').is_file()
    backend = 'il2cpp' if il2cpp else ('mono' if mono else 'unknown')
    if backend == 'unknown':
        diags.append({'level': 'error', 'code': 'UNKNOWN_BACKEND', 'message': 'neither GameAssembly.dll nor Managed/mscorlib.dll found'})
    bundles = _bundles(data_dir, sample_bundles)
    if bundles['engine_versions']:
        bad = [v for v in bundles['engine_versions'] if not v.startswith(hdr.unity_version.split('f')[0].rsplit('.', 1)[0])]
        if bad:
            diags.append({'level': 'warning', 'code': 'BUNDLE_ENGINE_MISMATCH', 'message': f'bundles built with {bad}'})
    ps = _player_settings(data_dir)
    ev = exe_version(exe)
    game_version = ps['bundle_version'] or ev.get('ProductVersion') or ev.get('FileVersion')
    version_source = 'PlayerSettings.bundleVersion' if ps['bundle_version'] else ('exe version resource' if game_version else None)
    shader_hint = dict(ps['graphics'])
    shader_hint['built_in_pipeline'] = (ps['graphics']['pipeline'] == 'BuiltIn') if ps['graphics'].get('pipeline') else None
    notable = [d for d in ('GI', 'Managed', 'Plugins', 'Resources', 'StreamingAssets', 'il2cpp_data', 'SRMP Libs') if (data_dir / d).is_dir()]
    resource_files = [h['name'] for h in headers if h['name'].startswith(('resources', 'sharedassets'))]
    info = {
        'game': game,
        'root': str(root),
        'data_dir': str(data_dir),
        'store': _gog_info(root) or ({'store': 'unknown'}),
        'game_version': {'value': game_version, 'source': version_source},
        'unity': {'version': hdr.unity_version, 'serialized_format': hdr.format_version,
                  'exe_file_version': ev.get('FileVersion'), 'exe_product_version': ev.get('ProductVersion')},
        'platform': hdr.platform_name,
        'architecture': pe_machine(exe),
        'backend': backend,
        'color_space': ps['color_space'],
        'graphics': shader_hint,
        'type_tree': {'globalgamemanagers': hdr.has_type_tree,
                      'files_with_typetree': sum(1 for h in headers if h['has_type_tree']),
                      'files_without_typetree': sum(1 for h in headers if h['has_type_tree'] is False)},
        'serialized_files': headers,
        'managed': _managed_info(data_dir),
        'il2cpp_metadata': (data_dir / 'il2cpp_data' / 'Metadata' / 'global-metadata.dat').is_file(),
        'bundles': bundles,
        'asset_directories': {'data': notable, 'resource_containers': resource_files},
        'fingerprint': _fingerprint(root, game, full_hash),
        'diagnostics': diags,
    }
    info['recognition'], rd = recognise(info)
    info['diagnostics'] += rd
    if ps.get('error'):
        info['diagnostics'].append({'level': 'warning', 'code': 'PLAYER_SETTINGS', 'message': ps['error']})
    return info
