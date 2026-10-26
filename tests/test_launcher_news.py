"""Official news + artwork feed: parsing, allowlists, caching, offline behaviour and the API surface around it."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.launcher import news
from apps.launcher.desktop import LauncherAPI

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
WEBP = b'RIFF\x10\x00\x00\x00WEBPVP8 ' + b'\x00' * 16

CARD = '''<div class="news-card-wrapper" data-v-b0b76ba2><a href="/news/{slug}" class="news-card"><!----> <div class="news-card-image"><img src="https://img2.storyblok.com/fit-in/750x500/f/164147/3840x2160/73f1763556/{img}" width="750" alt="" class="image">
<div class="character"><img alt="c" src="/images/pink.png" class="graphic"></div></div> <div class="news-card-content pt-7"><h1 class="title text-xl">{title}</h1>
<p class="news-card-date text-lg">{date}</p> <div class="text-content font-secondary"><p>{summary}</p></div></div></a></div>'''
HOME = ('<html><body><div class="latest-news">'
        + CARD.format(slug='patch-1-2-2-notes', img='patchnotes_1-2-2.jpg', title='Slime Rancher 2 - Patch <b>1.2.2</b> Notes', date='5/11/2026', summary='Some important&nbsp;fixes!')
        + CARD.format(slug='shop-launch', img='monomi-shop-news.png', title='The Shop is Live!', date='9/17/2026', summary='Apparel &amp; decor')
        + CARD.format(slug='shop-launch', img='dup.png', title='Duplicate', date='1/1/2026', summary='x')
        + '<script>evil()</script><img src="https://a.storyblok.com/f/336309/3840x2160/b0f7ad59cb/sr-keyart_beatrix_16x9-nologo.png">'
        + '<img src="https://a.storyblok.com/f/164147/3840x2160/c7866a1884/mainmenu_withoutlogo-1.png">'
        + '<img src="https://a.storyblok.com/f/164147/3840x2160/9a3b78d4d3/sr2_announce_feature_3.png">'
        + '<img src="https://a.storyblok.com/f/164147/3000x1736/68df665c7a/sr2_v1-2keyart.png">'
        + '<img src="https://a.storyblok.com/f/336309/2146x1204/1d0f89ca4f/slime-rancher-2-keyart.png">'       # carries the game logo
        + '<img src="https://a.storyblok.com/f/336309/1200x339/d2b659ff1c/slime-rancher-logo.png">'              # a logo: never used
        + '<img src="https://a.storyblok.com/f/336309/231x214/20cfe14b6e/newpinkslime.png">'
        + '<img src="https://a.storyblok.com/f/336309/487x543/99d7ff1d33/newcottonslime.png">'
        + '<img src="https://evil.example/f/1/100x100/abcdef/pink.png">'
        + '</div></body></html>')


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """Opener that records every URL requested and serves canned bodies."""

    def __init__(self, fail=False):
        self.requested, self.fail = [], fail
        self.pages = {news.NEWS_PAGE: HOME.encode(), news.STUDIO_PAGE: b'<html></html>'}
        self.big = False

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requested.append(url)
        if self.fail:
            raise OSError('network down')
        if url in self.pages:
            return FakeResponse(self.pages[url])
        if 'storyblok.com' in url:
            return FakeResponse(b'x' * (news.MAX_IMAGE + 5) if self.big else (WEBP if 'webp' in url else PNG))
        raise OSError('404')


class ParsingTests(unittest.TestCase):
    def test_cards_are_parsed_cleaned_and_deduplicated(self):
        items = news.parse_news(HOME)
        self.assertEqual([i['url'] for i in items], ['https://www.slimerancher.com/news/patch-1-2-2-notes', 'https://www.slimerancher.com/news/shop-launch'])
        self.assertEqual(items[0]['title'], 'Slime Rancher 2 - Patch 1.2.2 Notes')       # markup stripped
        self.assertEqual((items[0]['date'], items[0]['summary']), ('5/11/2026', 'Some important fixes!'))
        self.assertEqual(items[1]['summary'], 'Apparel & decor')
        self.assertTrue(items[0]['image'].startswith('https://img2.storyblok.com/'))

    def test_unknown_markup_yields_nothing_instead_of_crashing(self):
        self.assertEqual(news.parse_news('<html><body>redesigned</body></html>'), [])
        self.assertEqual(news.parse_news(''), [])

    def test_image_discovery_classifies_and_skips_logos_and_foreign_hosts(self):
        found = news.discover_images(HOME)
        kinds = {u.rsplit('/', 1)[-1]: k for k, u in found}
        self.assertEqual(kinds['sr-keyart_beatrix_16x9-nologo.png'], 'scene')
        self.assertEqual(kinds['mainmenu_withoutlogo-1.png'], 'scene')
        self.assertEqual(kinds['newpinkslime.png'], 'slime')
        self.assertNotIn('slime-rancher-logo.png', kinds)
        self.assertNotIn('slime-rancher-2-keyart.png', kinds)           # has the game logo baked in
        self.assertFalse([u for _, u in found if 'evil.example' in u])

    def test_transform_only_for_official_cdn_pictures(self):
        url = 'https://a.storyblok.com/f/336309/231x214/20cfe14b6e/newpinkslime.png'
        self.assertEqual(news.transform_url(url, 200, 200), 'https://img2.storyblok.com/fit-in/200x200/filters:format(webp):quality(82)/f/336309/231x214/20cfe14b6e/newpinkslime.png')
        for bad in ('https://evil.example/f/1/10x10/abcdef/x.png', 'http://a.storyblok.com/f/1/10x10/abcdef/x.png', 'javascript:alert(1)', '', None):
            self.assertIsNone(news.transform_url(bad, 10, 10), bad)

    def test_links_open_only_official_https_pages(self):
        for good in ('https://www.slimerancher.com/news/x', 'https://monomipark.com/', 'https://store.steampowered.com/app/433340/'):
            self.assertTrue(news.link_allowed(good), good)
        for bad in ('http://www.slimerancher.com/', 'https://evil.example/', 'https://www.slimerancher.com.evil.example/', 'file:///c:/x', 'https://user@www.slimerancher.com/', ''):
            self.assertFalse(news.link_allowed(bad), bad)


class FeedTests(unittest.TestCase):
    def feed(self, net, tmp):
        return news.NewsFeed(Path(tmp) / 'cache', opener=net, clock=lambda: 1234.0)

    def test_refresh_fetches_official_pages_only_and_orders_clean_art_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = FakeNet()
            doc = self.feed(net, tmp).refresh()
            self.assertFalse(doc['offline'])
            self.assertEqual(len(doc['items']), 2)
            self.assertTrue(set(net.requested) <= {news.NEWS_PAGE, *news.STUDIO_PAGES})
            self.assertRegex(doc['scenes'][0], r'keyart_beatrix')                  # clean key art first
            self.assertRegex(doc['backdrop'], r'mainmenu')
            self.assertEqual(len(doc['slimes']), 2)
            self.assertEqual(doc['policy'], news.FAN_POLICY_URL)
            cached = json.loads((Path(tmp) / 'cache' / 'news.json').read_text(encoding='utf-8'))
            self.assertEqual(len(cached['items']), 2)

    def test_offline_start_falls_back_to_the_last_good_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.feed(FakeNet(), tmp).refresh()
            offline = self.feed(FakeNet(fail=True), tmp)
            instant = offline.cached()
            self.assertEqual(len(instant['items']), 2)
            doc = offline.refresh()
            self.assertTrue(doc['offline'])
            self.assertEqual(len(doc['items']), 2)
            self.assertIn('OSError', doc['error'])

    def test_first_ever_start_without_network_is_an_empty_feed_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = self.feed(FakeNet(fail=True), tmp).refresh()
            self.assertEqual((doc['items'], doc['scenes'], doc['offline']), ([], [], True))

    def test_redesigned_site_still_gets_pictures_from_the_fallback_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = FakeNet()
            net.pages[news.NEWS_PAGE] = b'<html>new design</html>'
            doc = self.feed(net, tmp).refresh()
            self.assertEqual(doc['items'], [])
            self.assertTrue(doc['scenes'] and doc['slimes'])

    def test_page_larger_than_the_cap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = FakeNet()
            net.pages[news.NEWS_PAGE] = b'x' * (news.MAX_PAGE + 10)
            doc = self.feed(net, tmp).refresh()
            self.assertTrue(doc['offline'])
            self.assertIn('too large', doc['error'])

    def test_images_are_cached_on_disk_and_returned_as_data_uris(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = FakeNet()
            feed = self.feed(net, tmp)
            url = 'https://a.storyblok.com/f/336309/231x214/20cfe14b6e/newpinkslime.png'
            first = feed.image_data(url, 200, 200)
            self.assertTrue(first.startswith('data:image/webp;base64,'))
            self.assertEqual(base64.b64decode(first.split(',', 1)[1]), WEBP)
            count = len(net.requested)
            self.assertEqual(feed.image_data(url, 200, 200), first)
            self.assertEqual(len(net.requested), count)                          # second read came from disk
            offline = self.feed(FakeNet(fail=True), tmp)
            self.assertEqual(offline.image_data(url, 200, 200), first)           # and works offline

    def test_bad_pictures_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = FakeNet()
            feed = self.feed(net, tmp)
            self.assertIsNone(feed.image_data('https://evil.example/f/1/10x10/abcdef/x.png'))
            self.assertEqual(net.requested, [])                                   # never even contacted
            net.big = True
            self.assertIsNone(feed.image_data('https://a.storyblok.com/f/1/100x100/abcdef/a.png', 100, 100))   # over the size cap
            feed2 = news.NewsFeed(Path(tmp) / 'c2', opener=lambda r, timeout=None: FakeResponse(b'<html>not an image</html>'))
            self.assertIsNone(feed2.image_data('https://a.storyblok.com/f/1/100x100/abcdef/a.png', 100, 100))
            self.assertFalse(list((Path(tmp) / 'c2' / 'img').glob('*.bin')) if (Path(tmp) / 'c2' / 'img').exists() else [])

    def test_batch_requests_keep_their_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = self.feed(FakeNet(), tmp)
            urls = [f'https://a.storyblok.com/f/1/100x100/abcdef/{n}.png' for n in 'abc']
            result = feed.images_data([(u, 64, 64, True) for u in urls] + [('https://evil.example/x.png', 64, 64, True)])
            self.assertEqual([bool(r) for r in result], [True, True, True, False])


class ApiNewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.net = FakeNet()
        patcher = patch('apps.launcher.main.discover_stores', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = LauncherAPI(config_path=Path(self.tmp.name) / 'Launcher' / 'installations.json', known_paths=(), lab_games=Path(self.tmp.name) / 'x',
                               news_opener=self.net)

    def test_news_and_images_through_the_api(self):
        self.assertEqual(self.api.news_cached()['items'], [])
        doc = self.api.news_refresh()
        self.assertEqual(len(doc['items']), 2)
        self.assertEqual(self.api.news_cached()['items'][0]['title'], 'Slime Rancher 2 - Patch 1.2.2 Notes')
        self.assertTrue(self.api.image_data(doc['slimes'][0], 120, 120, True).startswith('data:image/'))
        self.assertIsNone(self.api.image_data('https://evil.example/f/1/10x10/abcdef/x.png', 100, 100, True))
        self.assertIsNone(self.api.image_data(None, 'x', 'y'))
        result = self.api.images_data([[doc['slimes'][0], 100, 100, True], ['bad'], 'oops'])
        self.assertTrue(result[0] and result[1] is None and result[2] is None)

    def test_only_official_links_are_opened(self):
        with patch('apps.launcher.desktop.webbrowser.open') as opened:
            self.assertTrue(self.api.open_url('https://www.slimerancher.com/news/x')['ok'])
            self.assertEqual(self.api.open_url('https://evil.example/')['error'], 'URL_NOT_ALLOWED')
            self.assertEqual(self.api.open_url('file:///c:/windows/system32/calc.exe')['error'], 'URL_NOT_ALLOWED')
            opened.assert_called_once_with('https://www.slimerancher.com/news/x')

    def test_window_controls_are_harmless_without_a_window_and_drive_it_with_one(self):
        self.assertFalse(self.api.window_minimize()['ok'])
        self.assertFalse(self.api.window_toggle_maximize()['ok'])
        self.assertFalse(self.api.window_close()['ok'])

        class Win:
            calls = []
            minimize = lambda self: self.calls.append('min')        # noqa: E731
            maximize = lambda self: self.calls.append('max')        # noqa: E731
            restore = lambda self: self.calls.append('restore')     # noqa: E731
            destroy = lambda self: self.calls.append('close')       # noqa: E731
        self.api._window = Win()
        self.api.window_minimize()
        self.assertTrue(self.api.window_toggle_maximize()['maximized'])
        self.assertFalse(self.api.window_toggle_maximize()['maximized'])
        self.api.window_close()
        self.assertEqual(Win.calls, ['min', 'max', 'restore', 'close'])

    def test_play_target_and_frame_preferences_are_validated(self):
        self.assertTrue(self.api.set_preference('play_target', 'SR2')['ok'])
        self.assertTrue(self.api.set_preference('native_frame', True)['ok'])
        with self.assertRaises(ValueError):
            self.api.set_preference('play_target', 'ALL')
        with self.assertRaises(ValueError):
            self.api.set_preference('native_frame', 'yes')
        prefs = self.api.get_state()['preferences']
        self.assertEqual((prefs['play_target'], prefs['native_frame']), ('SR2', True))


class PolicyNoticeTests(unittest.TestCase):
    def test_the_required_fan_content_notice_is_always_shown_with_the_artwork(self):
        ui = Path(__file__).resolve().parents[1] / 'apps' / 'launcher' / 'ui'
        text = (ui / 'js' / 'i18n.js').read_text(encoding='utf-8')
        for fragment in ('is unofficial Fan Content permitted under the ', 'Fan Content Policy', 'Not approved/endorsed by Monomi Park.',
                         'Portions of the materials used are property of Monomi Park, LLC.', '©Monomi Park LLC'):
            self.assertGreaterEqual(text.count(fragment), 2, fragment)          # English and French dictionaries both carry the exact notice
        app = (ui / 'js' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("$('#ribbon').innerHTML", app)
        self.assertIn('fan-content-policy', app)
        self.assertIn('<footer id="ribbon">', (ui / 'index.html').read_text(encoding='utf-8'))


class LoadingScreenTests(unittest.TestCase):
    """The start-up and game-launch screens are one design that carries the Monomi Park notice (it shows their art)."""

    def setUp(self):
        self.ui = Path(__file__).resolve().parents[1] / 'apps' / 'launcher' / 'ui'

    def test_old_splash_is_gone_and_loading_screen_is_wired(self):
        index = (self.ui / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="loading-root"', index)
        self.assertIn('js/loading.js', index)
        self.assertNotIn('id="splash"><div class="splash-inner">', index)
        app = (self.ui / 'js' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('SB.loading.open', app)
        self.assertNotIn('splash-scene', app)
        for css in ('base.css', 'pages.css'):
            self.assertNotIn('.splash-', (self.ui / 'css' / css).read_text(encoding='utf-8'))

    def test_loading_screen_shows_the_required_notice_and_both_languages_have_the_texts(self):
        loading = (self.ui / 'js' / 'loading.js').read_text(encoding='utf-8')
        for key in ("t('ribbon.a')", "t('ribbon.link')", "t('ribbon.b')"):
            self.assertIn(key, loading)
        i18n = (self.ui / 'js' / 'i18n.js').read_text(encoding='utf-8')
        for key in ("'boot.1'", "'boot.5'", "'launch.t'", "'launch.s1'", "'launch.s2'", "'launch.s3'", "'launch.ok'", "'launch.fail'", "'splash.loading'"):
            self.assertEqual(i18n.count(key), 2, key)


if __name__ == '__main__':
    unittest.main()
