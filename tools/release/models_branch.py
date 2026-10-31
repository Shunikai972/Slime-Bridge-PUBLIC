"""The MODELS branch: only the finished largo models, on the private and the public repository, plus the "largos" release
the launcher's "Download largos" button installs from.

    py -3.12 -m tools.release.models_branch --final <folder of <id>.glb> [--push] [--release]

* Branch MODELS (orphan, no code): models/<id>/<id>.glb (the lightened finals, every animation replayed), an icon per model,
  MODELS.json (ids, parents, SHA-256, sizes), README.md, LICENSE. Built in a git worktree next to each repository
  (../SlimeBridge-MODELS for the private one, ../SlimeBridge-Public-MODELS for the public one); same files, separate commits.
  glTF metadata (extras, generator) is scrubbed of local paths and tool names before anything is committed; geometry,
  rig, animations and textures are untouched. GitHub refuses files over 100 MB: checked first.
* Release "largos" (never marked latest, so the launcher's self-update keeps reading the app releases): one zip per model
  made by tools.largo_models.build --package (cache both games load + largo.json) and largo-models.json.
"""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT.parent / 'SlimeBridge-Public'
DIST = ROOT / 'Lab' / 'LargoModels' / 'dist'
CACHES = ROOT / 'Lab' / 'LargoModels' / 'caches'
BRANCH, TAG = 'MODELS', 'largos'
LIMIT = 99 * 1024 * 1024
TRAILER = os.environ.get('SLIMEBRIDGE_PRIVATE_TRAILER', '')       # optional trailer, private repository commits only

sys.path.insert(0, str(ROOT))
from tools.release.sync_public import FORBIDDEN_TEXT  # noqa: E402
from tools.largo_models.build import describe  # noqa: E402


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---- glTF metadata scrub ---------------------------------------------------------------------------------------------
def _scrub(node):
    """Drop every extras/generator string that names a local path or a tool; returns how many values were dropped."""
    dropped = 0
    if isinstance(node, dict):
        for key in list(node):
            value = node[key]
            if key in ('extras', 'generator', 'copyright') and FORBIDDEN_TEXT.search(json.dumps(value)):
                node[key] = 'SlimeBridge' if key == 'generator' else ({} if key == 'extras' else '')
                dropped += 1
            else:
                dropped += _scrub(value)
    elif isinstance(node, list):
        for value in node:
            dropped += _scrub(value)
    return dropped


def clean_glb(src: Path, dst: Path) -> int:
    data = src.read_bytes()
    magic, version, _ = struct.unpack_from('<III', data, 0)
    if magic != 0x46546C67:
        raise ValueError(f'{src.name}: not a GLB')
    length, kind = struct.unpack_from('<II', data, 12)
    doc = json.loads(data[20:20 + length].decode('utf-8'))
    dropped = _scrub(doc)
    if FORBIDDEN_TEXT.search(json.dumps(doc)):
        raise ValueError(f'{src.name}: names a local path or a tool outside extras: fix the source first')
    if not dropped:
        shutil.copy2(src, dst)
        return 0
    text = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    text += b' ' * ((4 - len(text) % 4) % 4)
    rest = data[20 + length:]
    out = struct.pack('<III', 0x46546C67, version, 12 + 8 + len(text) + len(rest)) + struct.pack('<II', len(text), kind) + text + rest
    dst.write_bytes(out)
    return dropped


# ---- branch content --------------------------------------------------------------------------------------------------
README = """# SlimeBridge — largo models

This branch holds only the finished 3D models of the 24 cross-game Largos (the three Slime Rancher families Rad, Quantum
and Mosaic crossed with the eight Slime Rancher 2 species). There is no code here: the launcher lives on `main`.

**To play with them** you do not need this branch: in the SlimeBridge launcher, open **Largos** and press
**Download all**. The launcher takes the ready-to-play versions from the `largos` release of this repository; both
games then have them (diet, favourite foods and plorts of both parents, each game's own Largo rules).

| Folder | Content |
|---|---|
| `models/<id>/<id>.glb` | the model (rig, every animation, textures), lightened without visible change |
| `models/<id>/icon.png` | a picture of it |
| `MODELS.json` | ids, parents, display names, SHA-256 and size of every file |

{table}

These models are part of SlimeBridge and under the same license (see `LICENSE`): no use, copy, modification or
redistribution without the author's permission. Slime Rancher is a trademark of Monomi Park; SlimeBridge is unofficial
fan content, not approved or endorsed by Monomi Park.
"""


def fill(tree: Path, final: Path) -> dict:
    for item in tree.iterdir():
        if item.name != '.git':
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    rows, too_big = [], []
    for glb in sorted(final.glob('*.glb')):
        info = describe(glb.stem)
        folder = tree / 'models' / info['id']
        folder.mkdir(parents=True)
        target = folder / glb.name
        dropped = clean_glb(glb, target)
        icon = CACHES / info['canonical'] / 'icon.png'
        if icon.is_file():
            shutil.copy2(icon, folder / 'icon.png')
        if target.stat().st_size > LIMIT:
            too_big.append(f'{glb.name} ({target.stat().st_size // 2**20} MB)')
        rows.append({**info, 'file': f'models/{info["id"]}/{glb.name}', 'size': target.stat().st_size, 'sha256': sha256(target),
                     'metadata_scrubbed': dropped})
    if too_big:
        raise SystemExit('GitHub refuses files over 100 MB: ' + ', '.join(too_big))
    (tree / 'MODELS.json').write_text(json.dumps({'schema': 'slimebridge-largo-sources', 'schema_version': 1, 'models': rows}, indent=1),
                                      encoding='utf-8')
    table = '| Largo | File | Size |\n|---|---|---|\n' + '\n'.join(
        f"| {r['display_name']} | `{r['file']}` | {r['size'] / 1e6:.1f} MB |" for r in rows)
    (tree / 'README.md').write_text(README.replace('{table}', table), encoding='utf-8')
    shutil.copy2(ROOT / 'LICENSE', tree / 'LICENSE')
    (tree / '.gitattributes').write_text('*.glb binary\n*.png binary\n', encoding='utf-8')
    for text in (tree / 'README.md', tree / 'MODELS.json'):
        if FORBIDDEN_TEXT.search(text.read_text(encoding='utf-8')):
            raise SystemExit(f'{text.name} names a local path or a tool')
    return {'models': len(rows), 'bytes': sum(r['size'] for r in rows)}


def worktree(repo: Path, path: Path) -> Path:
    if path.exists() and (path / '.git').exists():
        return path
    branches = git('branch', '--list', BRANCH, cwd=repo)
    remote = subprocess.run(['git', 'ls-remote', '--heads', 'origin', BRANCH], cwd=repo, capture_output=True, text=True).stdout.strip()
    if branches:
        git('worktree', 'add', str(path), BRANCH, cwd=repo)
    elif remote:
        git('fetch', 'origin', f'{BRANCH}:{BRANCH}', cwd=repo)
        git('worktree', 'add', str(path), BRANCH, cwd=repo)
    else:
        git('worktree', 'add', '--orphan', '-b', BRANCH, str(path), cwd=repo)
    return path


def commit(tree: Path, message: str) -> bool:
    git('add', '-A', cwd=tree)
    if not git('status', '--porcelain', cwd=tree):
        return False
    git('commit', '-q', '-m', message, cwd=tree)
    return True


# ---- release ---------------------------------------------------------------------------------------------------------
def release(repo: Path):
    from tools.release import publish_github as gh
    match = re.search(r'github\.com[/:]([^/]+/[^/.]+?)(?:\.git)?$', git('remote', 'get-url', 'origin', cwd=repo))
    slug = match.group(1)
    secret = gh.token()
    assets = sorted(DIST.glob('largo-*.zip')) + [DIST / 'largo-models.json']
    rel = gh.api(secret, 'GET', f'https://api.github.com/repos/{slug}/releases/tags/{TAG}', allow_404=True)
    head = git('rev-parse', BRANCH, cwd=repo)
    body = ('The finished largo models, ready for both games. Install them from the SlimeBridge launcher: '
            'Largos > Download all. Source models: branch MODELS.')
    if rel is None:
        rel = gh.api(secret, 'POST', f'https://api.github.com/repos/{slug}/releases', {
            'tag_name': TAG, 'target_commitish': head, 'name': 'SlimeBridge largos', 'body': body, 'draft': False,
            'prerelease': False, 'make_latest': 'false'})
    else:
        gh.api(secret, 'PATCH', f'https://api.github.com/repos/{slug}/releases/{rel["id"]}', {'body': body, 'make_latest': 'false'})
    existing = {a['name']: a['id'] for a in gh.api(secret, 'GET', f'https://api.github.com/repos/{slug}/releases/{rel["id"]}/assets?per_page=100') or []}
    upload = rel['upload_url'].split('{')[0]
    for path in assets:
        if path.name in existing:
            gh.api(secret, 'DELETE', f'https://api.github.com/repos/{slug}/releases/assets/{existing[path.name]}')
        gh.api(secret, 'POST', f'{upload}?name={path.name}', path.read_bytes(),
               'application/zip' if path.suffix == '.zip' else 'application/json')
        print(f'{slug}: uploaded {path.name}', flush=True)
    print(f'{slug}: release {rel.get("html_url")}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--final', required=True, help='folder holding the 24 <id>.glb to publish')
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--release', action='store_true')
    ap.add_argument('--private-only', action='store_true')
    args = ap.parse_args(argv)
    final = Path(args.final)
    targets = [(ROOT, ROOT.parent / 'SlimeBridge-MODELS', True)]
    if not args.private_only and PUBLIC.is_dir():
        targets.append((PUBLIC, ROOT.parent / 'SlimeBridge-Public-MODELS', False))
    for repo, path, private in targets:
        tree = worktree(repo, path)
        stats = fill(tree, final)
        message = f'Largo models: {stats["models"]} finished models ({stats["bytes"] / 1e6:.0f} MB)'
        if private and TRAILER:
            message += '\n\n' + TRAILER
        changed = commit(tree, message)
        print(f'{repo.name}: branch {BRANCH} {"committed" if changed else "unchanged"} ({stats["models"]} models)', flush=True)
        if args.push:
            print(git('push', '-u', 'origin', BRANCH, cwd=tree) or f'{repo.name}: pushed {BRANCH}', flush=True)
        if args.release:
            release(repo)
    return 0


if __name__ == '__main__':
    sys.exit(main())
