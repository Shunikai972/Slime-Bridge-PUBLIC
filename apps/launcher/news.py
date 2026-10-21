"""Latest news and official artwork, loaded from Monomi Park's own sites at every launcher start.

* News: the news cards of https://www.slimerancher.com/ (title, date, summary, picture, link).
* Pictures: the official sites serve their art from the Storyblok CDN (``a.storyblok.com`` / ``img2.storyblok.com``).
  The launcher discovers the key art / screenshots / slime cut-outs on the two official pages and shows them at runtime.

Rules this module follows:
* Nothing is bundled or committed: pictures are fetched when the launcher runs and cached under the launcher's own
  (git-ignored) cache folder so the next start is instant and works offline.
* Only two page hosts and the two CDN hosts below are ever contacted (https only), with a clear User-Agent, a short
  timeout, a size cap, and a single refresh per start; page text is stripped of markup before it reaches the UI.
* Everything shown stays attributed to Monomi Park (the UI prints the Fan Content Policy notice next to it).
"""

import base64
import hashlib
import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

USER_AGENT = 'SlimeBridge-Launcher/0.3 (unofficial fan project; contact via GitHub)'
NEWS_PAGE = 'https://www.slimerancher.com/'
STUDIO_PAGE = 'https://monomipark.com/'
STUDIO_PAGES = (STUDIO_PAGE, 'https://monomipark.com/games/', 'https://monomipark.com/press')
PAGE_HOSTS = {'www.slimerancher.com', 'slimerancher.com', 'monomipark.com', 'www.monomipark.com'}
LINK_HOSTS = PAGE_HOSTS | {'store.steampowered.com', 'shop.monomipark.com', 'support.slimerancher.com', 'discord.com'}
IMAGE_HOSTS = {'a.storyblok.com', 'img2.storyblok.com'}
MAX_PAGE = 3_000_000
MAX_IMAGE = 3_000_000
FAN_POLICY_URL = 'https://www.slimerancher.com/fan-content-policy'

_STORYBLOK = re.compile(r'https://(?:a|img2)\.storyblok\.com/(?:fit-in/\d+x\d+/(?:filters:[^/]+/)?|\d+x\d+/(?:filters:[^/]+/)?)?'
                        r'f/(\d+)/(\d+)x(\d+)/([0-9a-f]{6,16})/([A-Za-z0-9_.-]+\.(?:png|jpe?g|webp))')
_SKIP_NAME = re.compile(r'logo|(^|[-_])bg|background|pattern|footer|shop|product|plush|deluxe|plortable|tile|layer|community|multi-block|'
                        r'features|hero-scene|itemview|image-20|about-intro|mobile|shot', re.I)
_OWN_LOGO = re.compile(r'slime-rancher(-2)?-keyart', re.I)   # key art that carries the game logo
_SCENE_NAME = re.compile(r'keyart|mainmenu|announce|screen|ranching|patchnotes|feature', re.I)
_SLIME_NAME = re.compile(r'pink|cotton|batty|flutter|tabby|honey|rock|boom|crystal|phosphor|hunter|angler|saber|rad|quantum|mosaic|'
                         r'dervish|tangle|ringtail|twin|hyper|slumber|sloomber|character', re.I)
# last resort when page discovery finds nothing (still fetched at runtime from the official CDN, never bundled)
FALLBACK_IMAGES = [
    ('scene', 'https://a.storyblok.com/f/336309/3840x2160/b0f7ad59cb/sr-keyart_beatrix_16x9-nologo.png'),
    ('scene', 'https://a.storyblok.com/f/164147/3840x2160/c7866a1884/mainmenu_withoutlogo-1.png'),
    ('scene', 'https://a.storyblok.com/f/164147/3840x2160/9a3b78d4d3/sr2_announce_feature_3.png'),
    ('slime', 'https://a.storyblok.com/f/336309/231x214/20cfe14b6e/newpinkslime.png'),
    ('slime', 'https://a.storyblok.com/f/336309/487x543/99d7ff1d33/newcottonslime.png'),
]


class FeedError(RuntimeError):
    pass


def strip_tags(text):
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text))).strip()


def parse_news(page, base=NEWS_PAGE):
    """News cards of the official page, newest first (the page's own order). Unknown markup yields an empty list."""
    items, seen = [], set()
    for chunk in re.split(r'<div class="news-card-wrapper', page)[1:]:
        link = re.search(r'<a href="(/news/[a-z0-9-]+)"', chunk)
        title = re.search(r'<h1[^>]*class="title[^"]*"[^>]*>(.*?)</h1>', chunk, re.S)
        if not link or not title or link.group(1) in seen:
            continue
        seen.add(link.group(1))
        date = re.search(r'news-card-date[^>]*>(.*?)</p>', chunk, re.S)
        summary = re.search(r'class="text-content[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        image = re.search(r'class="news-card-image"[^>]*>\s*<img src="([^"]+)"', chunk)
        items.append({'title': strip_tags(title.group(1))[:140], 'date': strip_tags(date.group(1))[:24] if date else '',
                      'summary': strip_tags(summary.group(1))[:240] if summary else '',
                      'url': base.rstrip('/') + link.group(1), 'image': image.group(1) if image and _STORYBLOK.match(image.group(1)) else None})
    return items


def discover_images(page):
    """Official pictures on a page, classified by file name: ``scene`` (16:9 art), ``slime`` (transparent cut-outs)."""
    found, seen = [], set()
    for match in _STORYBLOK.finditer(page):
        space, w, h, digest, name = match.group(1), int(match.group(2)), int(match.group(3)), match.group(4), match.group(5)
        key = (space, digest)
        plain = re.sub(r'(no|without)logo', '', name, flags=re.I)   # "...-nologo" art is exactly what we want
        if key in seen or _SKIP_NAME.search(plain) or _OWN_LOGO.search(name):
            continue
        seen.add(key)
        canonical = f'https://a.storyblok.com/f/{space}/{w}x{h}/{digest}/{name}'
        aspect = w / h if h else 0
        if _SCENE_NAME.search(name) and 1.6 <= aspect <= 1.9 and w >= 1600:
            found.append(('scene', canonical))
        elif _SLIME_NAME.search(name) and name.lower().endswith('.png') and w <= 1200:
            found.append(('slime', canonical))
    return found


def transform_url(url, width, height, fit=True, webp=True):
    """Resized (and WebP-compressed) rendition of an official Storyblok picture, or None if it is not one."""
    match = _STORYBLOK.match(url or '')
    if not match:
        return None
    space, w, h, digest, name = match.groups()
    size = f'{int(width)}x{int(height)}'
    filters = 'filters:format(webp):quality(82)/' if webp else ''
    return f'https://img2.storyblok.com/{"fit-in/" if fit else ""}{size}/{filters}f/{space}/{w}x{h}/{digest}/{name}'


def _sniff(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    return None


def _scene_rank(url):
    name = url.rsplit('/', 1)[-1]
    if re.search(r'mainmenu', name, re.I):
        return 9                      # used as the window backdrop, so it comes last among the clean ones
    if re.search(r'nologo|withoutlogo|keyart_', name, re.I):
        return 0
    if re.search(r'announce|screen_', name, re.I):
        return 1
    return 5                          # patch-note covers etc. carry their own lettering


def link_allowed(url):
    parts = urlparse(url or '')
    return parts.scheme == 'https' and parts.hostname in LINK_HOSTS and not parts.username


class NewsFeed:
    def __init__(self, cache_dir, opener=urlopen, clock=time.time):
        self.cache_dir = Path(cache_dir)
        self._open, self._clock = opener, clock
        self._lock = threading.Lock()
        self.state = None

    # ---- network ---------------------------------------------------------------------------------------------------
    def _get(self, url, limit, timeout=12):
        parts = urlparse(url)
        if parts.scheme != 'https' or parts.hostname not in (PAGE_HOSTS | IMAGE_HOSTS):
            raise FeedError(f'host not allowed: {parts.hostname}')
        request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
        with self._open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise FeedError('response too large')
        return data

    # ---- cache -----------------------------------------------------------------------------------------------------
    @property
    def _cache_file(self):
        return self.cache_dir / 'news.json'

    def cached(self):
        """Last successful refresh, instantly (or an empty feed)."""
        with self._lock:
            if self.state is not None:
                return self.state
        try:
            doc = json.loads(self._cache_file.read_text(encoding='utf-8'))
            if isinstance(doc, dict) and isinstance(doc.get('items'), list):
                return {**doc, 'offline': True, 'from_cache': True}
        except (OSError, ValueError):
            pass
        return self.empty()

    @staticmethod
    def empty(error=None):
        return {'items': [], 'scenes': [], 'slimes': [], 'backdrop': None, 'fetched_at': None, 'offline': True,
                'from_cache': False, 'error': error, 'source': NEWS_PAGE, 'policy': FAN_POLICY_URL}

    def _store(self, doc):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temp = self._cache_file.with_suffix('.tmp')
        temp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temp, self._cache_file)

    # ---- refresh ---------------------------------------------------------------------------------------------------
    def refresh(self):
        """Fetch the official pages now. On any failure the last cached feed is returned, flagged ``offline``."""
        try:
            page = self._get(NEWS_PAGE, MAX_PAGE).decode('utf-8', 'replace')
            items = parse_news(page)
            images = discover_images(page)
            for studio in STUDIO_PAGES:
                try:
                    images += discover_images(self._get(studio, MAX_PAGE).decode('utf-8', 'replace'))
                except (OSError, FeedError, ValueError):
                    continue  # the studio pages only add pictures
            if not images:
                images = list(FALLBACK_IMAGES)
            scenes = [u for kind, u in images if kind == 'scene']
            backdrop = next((u for u in scenes if re.search(r'mainmenu', u, re.I)), None)
            scenes.sort(key=_scene_rank)       # clean key art first (no text baked in), art with embedded titles last
            slimes = [u for kind, u in images if kind == 'slime']
            backdrop = backdrop or (scenes[0] if scenes else None)
            doc = {'items': items, 'scenes': scenes, 'slimes': slimes, 'backdrop': backdrop, 'fetched_at': self._clock(),
                   'offline': False, 'from_cache': False, 'error': None if items else 'no news found on the page',
                   'source': NEWS_PAGE, 'policy': FAN_POLICY_URL}
            if items or scenes:
                self._store({k: v for k, v in doc.items() if k not in ('offline', 'from_cache')})
        except (OSError, FeedError, ValueError) as exc:
            doc = {**self.cached(), 'offline': True, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'}
        with self._lock:
            self.state = doc
        return doc

    # ---- pictures --------------------------------------------------------------------------------------------------
    def image_data(self, url, width=640, height=360, fit=True):
        """``data:`` URI of an official picture at the requested size (cached on disk), or None."""
        target = transform_url(url, max(16, min(int(width), 1920)), max(16, min(int(height), 1080)), fit=fit)
        if target is None:
            return None
        path = self.cache_dir / 'img' / (hashlib.sha1(target.encode()).hexdigest() + '.bin')
        data = None
        try:
            data = path.read_bytes()
        except OSError:
            try:
                data = self._get(target, MAX_IMAGE, timeout=20)
            except (OSError, FeedError, ValueError):
                return None
            if _sniff(data) is None:
                return None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.tmp')
                temp.write_bytes(data)
                os.replace(temp, path)
            except OSError:
                pass
        mime = _sniff(data)
        if mime is None:
            return None
        return f'data:{mime};base64,{base64.b64encode(data).decode("ascii")}'

    def images_data(self, requests, workers=4):
        """Several pictures in parallel: ``[(url, w, h, fit)]`` -> list of data URIs (None where unavailable)."""
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda r: self.image_data(*r), requests))
