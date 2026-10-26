"""Push the current branch and publish GitHub release v<VERSION> with the zips in dist/ (no gh CLI needed).

    py -3.12 -m tools.release.publish_github [--draft]

Run after tools.release.make_release and tools.release.build_exe. The repository comes from the ``origin`` remote; the token
is the one git already stores for github.com (``git credential fill``), never printed or written anywhere. Refuses to
overwrite an existing release of the same tag: bump VERSION first.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def die(message):
    print('ERROR: ' + message)
    sys.exit(1)


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def token():
    fill = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                          capture_output=True, text=True, cwd=ROOT).stdout
    value = next((line.split('=', 1)[1] for line in fill.splitlines() if line.startswith('password=')), None)
    if not value:
        die('no GitHub credential stored for git (push once from this machine to store it)')
    return value


def api(secret, method, url, data=None, content_type='application/json', allow_404=False):
    body = json.dumps(data).encode() if isinstance(data, dict) else data
    request = urllib.request.Request(url, data=body, method=method, headers={
        'Authorization': 'Bearer ' + secret, 'Accept': 'application/vnd.github+json', 'Content-Type': content_type,
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'SlimeBridge-release'})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        die(f'{method} {url}: HTTP {exc.code}: {exc.read()[:300]!r}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--draft', action='store_true')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    tag = 'v' + version
    match = re.search(r'github\.com[/:]([^/]+/[^/.]+?)(?:\.git)?$', git('remote', 'get-url', 'origin'))
    if not match:
        die('origin is not a GitHub repository')
    repo = match.group(1)
    if git('status', '--porcelain'):
        die('uncommitted changes in the public tree')
    assets = [ROOT / 'dist' / f'SlimeBridge-v{version}-win64-standalone.zip', ROOT / 'dist' / f'SlimeBridge-v{version}-win64.zip']
    for path in assets:
        if not path.is_file():
            die(f'{path} missing: run make_release and build_exe first')
    secret = token()
    if api(secret, 'GET', f'https://api.github.com/repos/{repo}/releases/tags/{tag}', allow_404=True):
        die(f'release {tag} already exists: bump VERSION (and mods/sr1-bridge/modinfo.json) first')
    print(git('push', 'origin', 'HEAD') or 'pushed')
    sums = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in assets)
    (ROOT / 'dist' / 'SHA256SUMS.txt').write_text(sums, encoding='utf-8')
    assets.append(ROOT / 'dist' / 'SHA256SUMS.txt')
    notes_file = ROOT / 'dist' / f'release-notes-v{version}.md'
    notes = notes_file.read_text(encoding='utf-8') if notes_file.is_file() else f'SlimeBridge {tag}'
    release = api(secret, 'POST', f'https://api.github.com/repos/{repo}/releases', {
        'tag_name': tag, 'target_commitish': git('rev-parse', 'HEAD'), 'name': f'SlimeBridge {tag}', 'body': notes,
        'draft': bool(args.draft), 'prerelease': False})
    upload = release['upload_url'].split('{')[0]
    for path in assets:
        asset = api(secret, 'POST', f'{upload}?name={path.name}', path.read_bytes(),
                    'application/zip' if path.suffix == '.zip' else 'text/plain')
        print('uploaded', asset.get('name'), asset.get('size'))
    print('release', release['html_url'])


if __name__ == '__main__':
    main()
