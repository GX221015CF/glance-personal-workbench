import http.client
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import bridge
from wechat_bridge import (
    WeChatError, WeChatService, article_identity, download,
    parse_feed, safe_article_url, safe_image_url, RestrictedRedirect, ReaderHTML, IMAGE_LIMIT,
)

ARTICLE = 'https://mp.weixin.qq.com/s?__biz=account%3D%3D&amp;mid=123&amp;idx=1&amp;sn=abc'
COVER = 'https://mmbiz.qpic.cn/mmbiz_jpg/test/0?wx_fmt=jpeg'
JPEG = b'\xff\xd8\xff' + b'image-test'


def rss(mid='123', idx='1', title='新文章', summary='短摘要', image=COVER):
    return ('''<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><title>来源</title><item><title>%s</title>
      <link>https://mp.weixin.qq.com/s?__biz=account%%3D%%3D&amp;mid=%s&amp;idx=%s&amp;sn=abc</link>
      <description><![CDATA[%s]]></description><pubDate>Thu, 05 Nov 2026 18:22:00 +0800</pubDate>
      <content:encoded><![CDATA[<script>alert('bad')</script><p>正文文本</p><img src="%s">]]></content:encoded>
      </item></channel></rss>''' % (title, mid, idx, summary, image)).encode()


class WeChatFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp.name) / 'sources.json'
        config = json.loads((Path(__file__).parent / 'config' / 'wechat-sources.json').read_text(encoding='utf-8'))
        # This fixture models four independent sources, regardless of the user's
        # live subscription list. Adding real subscriptions must not alter tests.
        fixture_ids = {'tencent-cloud', 'kazike', 'daishudi', 'appso'}
        config['sources'] = [source for source in config['sources'] if source['id'] in fixture_ids]
        self.config_path.write_text(json.dumps(config), encoding='utf-8')
        self.service = WeChatService(self.config_path, Path(self.temp.name) / 'data')
        self.source = self.service.sources[0]

    def tearDown(self):
        self.temp.cleanup()

    def populate(self):
        with patch('wechat_bridge.download', return_value=(rss(), {'etag': 'version-one'})):
            for source in self.service.sources:
                self.service.refresh_source(source)


class WeChatTests(WeChatFixture, unittest.TestCase):
    def test_reader_sanitizes_and_persists_body_without_overwriting_on_empty_feed(self):
        raw = rss().replace(b'<p>\xe6\xad\xa3\xe6\x96\x87\xe6\x96\x87\xe6\x9c\xac</p>', b'<p onclick="alert(1)">safe <strong>bold</strong><a href="javascript:evil()">link</a></p><form>secret</form><iframe>evil</iframe><img src="https://evil.test/a">')
        with patch('wechat_bridge.download', return_value=(raw, {})):
            self.service.refresh_source(self.source)
        article_id = self.service.snapshot()['articles'][0]['id']
        article = self.service.article(article_id)
        rendered = json.dumps(article['nodes'])
        self.assertIn('safe', rendered)
        self.assertIn('bold', rendered)
        for unsafe in ('onclick', 'javascript:', 'secret', 'evil.test', 'alert'):
            self.assertNotIn(unsafe, rendered)
        with patch('wechat_bridge.download', return_value=(rss().replace(b'<content:encoded><![CDATA[', b'<content:encoded><![CDATA[').replace(b'<script>alert(\'bad\')</script><p>\xe6\xad\xa3\xe6\x96\x87\xe6\x96\x87\xe6\x9c\xac</p><img src="' + COVER.encode() + b'">', b''), {})):
            self.service.refresh_source(self.source)
        self.assertEqual(self.service.article(article_id)['nodes'], article['nodes'])
        restarted = WeChatService(self.config_path, self.service.data_dir)
        self.assertEqual(restarted.article(article_id)['nodes'], article['nodes'])

    def test_old_database_migrates_and_body_is_backfilled(self):
        self.populate()
        article_id = self.service.snapshot()['articles'][0]['id']
        with self.service.connect() as db:
            db.execute('ALTER TABLE articles DROP COLUMN body')
            db.execute("UPDATE sources SET etag='old', modified='old'")
            db.execute('INSERT INTO covers VALUES(?,?,?)', (COVER, 'image/jpeg', JPEG))
        migrated = WeChatService(self.config_path, self.service.data_dir)
        self.assertIsNone(migrated.article(article_id)['nodes'])
        with migrated.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM articles').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT count(*) FROM covers').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT etag FROM sources LIMIT 1').fetchone()[0], '')
        with patch('wechat_bridge.download', return_value=(rss(), {})):
            for source in migrated.sources:
                migrated.refresh_source(source)
        self.assertTrue(migrated.article(article_id)['nodes'])

    def test_body_image_is_indexed_by_article_and_cached(self):
        self.populate()
        article_id = self.service.snapshot()['articles'][0]['id']
        with patch('wechat_bridge.download', return_value=(JPEG, {})) as fetch:
            self.assertEqual(self.service.body_image(article_id, 0), (JPEG, 'image/jpeg'))
            self.assertEqual(self.service.body_image(article_id, 0), (JPEG, 'image/jpeg'))
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(WeChatService(self.config_path, self.service.data_dir).body_image(article_id, 0), (JPEG, 'image/jpeg'))
        for bad_id, index in [(article_id, 1), ('a' * 64, 0), ('../bad', 0)]:
            with self.assertRaises(WeChatError):
                self.service.body_image(bad_id, index)
        with self.assertRaises(WeChatError):
            self.service.article('f' * 64)

    def test_image_size_limit(self):
        class Response(io.BytesIO):
            headers = {}
        class Opener:
            def open(self, request, timeout):
                return Response(b'x' * (IMAGE_LIMIT + 1))
        with patch('wechat_bridge.urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(WeChatError):
                download(COVER, image=True)

    def test_parse_plain_summary_dates_and_cover_without_aihot_filter(self):
        rows = parse_feed(rss(title='OpenAI论文与其他全部内容', summary='<p>摘要 <a href="javascript:bad">内容</a></p><script>evil()</script>'), self.source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['summary'], '摘要 内容')
        self.assertEqual(rows[0]['publishedAt'], '2026-11-05T18:22:00+08:00')
        self.assertEqual(rows[0]['cover'], COVER)
        self.assertNotIn('amp;', rows[0]['url'])

    def test_identity_ignores_tracking_but_preserves_article_parameters(self):
        original = article_identity(ARTICLE)
        self.assertEqual(original, article_identity(ARTICLE + '&amp;scene=21#wechat_redirect'))
        self.assertNotEqual(original, article_identity(ARTICLE.replace('mid=123', 'mid=124')))
        self.assertNotEqual(original, article_identity(ARTICLE.replace('idx=1', 'idx=2')))
        self.assertNotEqual(original, article_identity(ARTICLE.replace('sn=abc', 'sn=def')))
        self.assertNotEqual(article_identity('https://mp.weixin.qq.com/s/first'), article_identity('https://mp.weixin.qq.com/s/second'))

    def test_article_and_image_addresses_are_restricted(self):
        for url in ('javascript:alert(1)', 'https://mp.weixin.qq.com.evil.test/s', 'http://127.0.0.1/s', 'https://user:pass@mp.weixin.qq.com/s', 'https://mp.weixin.qq.com:9999/s'):
            self.assertEqual(safe_article_url(url), '')
        for url in ('https://evil.test/image', 'http://mmbiz.qpic.cn/image', 'https://mmbiz.qpic.cn:9999/a', 'https://wechat2rss.bestblogs.dev/img-proxy/?u=http%3A%2F%2F127.0.0.1%2Fprivate'):
            self.assertEqual(safe_image_url(url), '')
        self.assertEqual(safe_image_url(COVER), COVER)
        self.assertTrue(safe_image_url('https://wechat2rss.bestblogs.dev/img-proxy/?u=https%3A%2F%2Fmmbiz.qpic.cn%2Fa&amp;k=test'))
        self.assertTrue(safe_image_url('https://wechat2rss.bestblogs.dev/img-proxy?u=https%3A%2F%2Fmmbiz.qpic.cn%2Fa&k=test'))
        with self.assertRaises(WeChatError):
            download('http://127.0.0.1/private')
        with self.assertRaises(WeChatError):
            RestrictedRedirect(safe_image_url).redirect_request(None, None, 302, '', {}, 'https://evil.test/image')

    def test_invalid_xml_or_links_do_not_clear_previous_articles(self):
        self.populate()
        before = self.service.snapshot()
        for raw in (b'<rss>', b'<!DOCTYPE rss [<!ENTITY a "boom">]><rss/>', rss().replace(b'https://mp.weixin.qq.com', b'https://evil.test')):
            with patch('wechat_bridge.download', return_value=(raw, {})):
                self.service.refresh_source(self.source)
            current = self.service.snapshot()
            self.assertEqual(current['articles'], before['articles'])
            self.assertEqual(current['sources'][0]['lastSuccess'], before['sources'][0]['lastSuccess'])
            self.assertTrue(current['sources'][0]['error'])

    def test_repeat_fetch_restart_and_new_articles_keep_archive_consistent(self):
        self.populate()
        before = self.service.snapshot()
        with patch('wechat_bridge.download', return_value=(rss(), {})):
            self.service.refresh_source(self.source)
        self.assertEqual(self.service.snapshot()['articles'], before['articles'])
        restarted = WeChatService(self.config_path, self.service.data_dir)
        self.assertEqual(restarted.snapshot()['articles'], before['articles'])
        with patch('wechat_bridge.download', return_value=(rss(mid='456', idx='2'), {})):
            restarted.refresh_source(self.source)
        self.assertEqual(len(restarted.snapshot()['articles']), 5)
        self.assertEqual(restarted.snapshot()['sources'][0]['count'], 2)
        with patch('wechat_bridge.download', return_value=(None, {})) as fetch:
            restarted.refresh_source(self.source)
        self.assertEqual(fetch.call_args.kwargs['etag'], 'version-one')
        self.assertEqual(len(restarted.snapshot()['articles']), 5)

    def test_source_failure_isolated_from_other_sources_and_persisted(self):
        self.populate()
        old_success = self.service.snapshot()['sources'][0]['lastSuccess']
        def fetch(url, **kwargs):
            if url == self.source['url']:
                raise WeChatError('上游 HTTP 503')
            return rss(mid='789'), {}
        with patch('wechat_bridge.download', side_effect=fetch):
            for source in self.service.sources:
                self.service.refresh_source(source)
        state = self.service.snapshot()
        self.assertEqual(len(state['articles']), 7)
        self.assertEqual(state['sources'][0]['lastSuccess'], old_success)
        self.assertEqual(state['sources'][0]['error'], '上游 HTTP 503')
        self.assertTrue(state['sources'][0]['stale'])
        self.assertTrue(all(not source['error'] for source in state['sources'][1:]))
        self.assertEqual(WeChatService(self.config_path, self.service.data_dir).snapshot()['sources'][0]['error'], '上游 HTTP 503')

    def test_cooldown_survives_restart_and_overlapping_refresh_joins_running_job(self):
        self.populate()
        restarted = WeChatService(self.config_path, self.service.data_dir)
        with patch('wechat_bridge.download') as fetch:
            result = restarted.request_refresh(manual=True)
            self.assertFalse(result['accepted'])
            self.assertGreater(result['retryAfterSeconds'], 0)
            restarted.request_refresh()
            fetch.assert_not_called()
        gate = threading.Event()
        def delayed(url, **kwargs):
            gate.wait(3)
            return rss(), {}
        with restarted.connect() as db:
            db.execute('UPDATE sources SET last_attempt=0')
        with patch('wechat_bridge.download', side_effect=delayed):
            self.assertTrue(restarted.request_refresh(manual=True)['accepted'])
            self.assertTrue(restarted.request_refresh(manual=True)['running'])
            gate.set()
            for _ in range(100):
                if not restarted.snapshot()['refreshing']:
                    break
                time.sleep(0.02)
        self.assertFalse(restarted.snapshot()['refreshing'])
        self.assertEqual(len(restarted.snapshot()['articles']), 4)

    def test_first_success_is_visible_while_other_sources_are_still_loading(self):
        first_done = threading.Event()
        release = threading.Event()
        original = self.service.refresh_source
        def fetch(url, **kwargs):
            if url != self.source['url']:
                release.wait(3)
            return rss(), {}
        def refresh(source):
            original(source)
            if source['id'] == self.source['id']:
                first_done.set()
        with patch('wechat_bridge.download', side_effect=fetch), patch.object(self.service, 'refresh_source', side_effect=refresh):
            self.assertTrue(self.service.request_refresh()['accepted'])
            self.assertTrue(first_done.wait(2))
            partial = self.service.snapshot()
            self.assertTrue(partial['refreshing'])
            self.assertEqual(len(partial['articles']), 1)
            release.set()
            for _ in range(100):
                if not self.service.snapshot()['refreshing']:
                    break
                time.sleep(0.02)
        self.assertEqual(len(self.service.snapshot()['articles']), 4)

    def test_cover_cache_type_checks_and_failure_placeholder_path(self):
        self.populate()
        article_id = self.service.snapshot()['articles'][0]['id']
        with patch('wechat_bridge.download', return_value=(JPEG, {})) as fetch:
            self.assertEqual(self.service.cover(article_id), (JPEG, 'image/jpeg'))
            self.assertEqual(self.service.cover(article_id), (JPEG, 'image/jpeg'))
            self.assertEqual(fetch.call_count, 1)
        restarted = WeChatService(self.config_path, self.service.data_dir)
        with patch('wechat_bridge.download') as fetch:
            self.assertEqual(restarted.cover(article_id), (JPEG, 'image/jpeg'))
            fetch.assert_not_called()
        with self.service.connect() as db:
            db.execute('DELETE FROM covers')
        with patch('wechat_bridge.download', return_value=(b'<html>verification</html>', {})) as fetch:
            for _ in range(2):
                with self.assertRaises(WeChatError):
                    self.service.cover(article_id)
            self.assertEqual(fetch.call_count, 1)
        with self.assertRaises(WeChatError):
            self.service.cover('../outside')


class WeChatHttpTests(WeChatFixture, unittest.TestCase):
    def test_normal_api_origin_refresh_throttle_and_cover_response(self):
        self.populate()
        server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, token=bridge.DESKTOP_TOKEN, origin='http://127.0.0.1:8180'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            headers = {'Host': '127.0.0.1:8182', 'X-Desktop-Token': token, 'Origin': origin}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        try:
            with patch('bridge.WECHAT', self.service):
                self.assertEqual(request('GET', '/api/wechat')[0], 200)
                self.assertEqual(request('OPTIONS', '/api/wechat/refresh')[0], 204)
                self.assertEqual(request('POST', '/api/wechat/refresh', token='wrong')[0], 403)
                self.assertEqual(request('POST', '/api/wechat/refresh', origin='https://evil.test')[0], 403)
                self.assertEqual(request('GET', '/api/wechat/refresh')[0], 404)
                self.assertEqual(request('GET', '/api/wechat?url=http://evil.test')[0], 400)
                self.assertEqual(request('POST', '/api/wechat/refresh')[0], 429)
                article_id = self.service.snapshot()['articles'][0]['id']
                status, _, raw = request('GET', '/api/wechat/article/' + article_id)
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(raw)['nodes'])
                self.assertEqual(request('GET', '/api/wechat/article/' + 'f' * 64)[0], 404)
                self.assertEqual(request('GET', '/api/wechat/article/' + article_id + '?url=https://evil.test')[0], 400)
                with patch('wechat_bridge.download', return_value=(JPEG, {})):
                    status, headers, raw = request('GET', '/api/wechat/cover/' + article_id)
                    self.assertEqual(request('GET', '/api/wechat/image/' + article_id + '/0')[0], 200)
                    self.assertEqual(request('GET', '/api/wechat/image/' + article_id + '/1')[0], 404)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], 'image/jpeg')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(raw, JPEG)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
