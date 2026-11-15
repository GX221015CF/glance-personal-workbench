"""Persistent, source-isolated reader for configured public WeChat RSS feeds."""
from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
import datetime as dt
import email.utils
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
USER_AGENT = 'PersonalWorkbench-WeChat/1.0'
FEED_HOST = 'wechat2rss.bestblogs.dev'
IMAGE_HOSTS = {'mmbiz.qpic.cn', 'mmbiz.qlogo.cn'}
XML_LIMIT = 8 * 1024 * 1024
IMAGE_LIMIT = 4 * 1024 * 1024


def analysis_progress(status, progress=''):
    """Milestone percentage, never inferred model/token completion or a timer."""
    if status == 'completed':
        return 100
    stages = {
        '等待 WorkBuddy 处理': 10,
        '正在读取正文与个人偏好': 15,
        '正在连接 WorkBuddy': 20,
        '正在提交 WorkBuddy 任务': 25,
        '已提交 WorkBuddy，等待执行': 30,
        'WorkBuddy 正在排队': 30,
        'WorkBuddy 正在分析正文': 45,
        'WorkBuddy 正在总结正文': 45,
        'WorkBuddy 已返回，正在检查结果': 80,
        '正在安全清理并排版': 90,
        '正在整理 Markdown': 90,
        '正在保存解读报告': 95,
        '正在保存总结文件': 95,
    }
    return stages.get(progress, 10 if status == 'queued' else 20 if status == 'running' else 0)


class WeChatError(Exception):
    pass


def safe_article_url(value):
    try:
        parsed = urllib.parse.urlsplit(unescape(value or '').strip())
        if parsed.scheme not in ('http', 'https') or parsed.hostname != 'mp.weixin.qq.com' or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return ''
        if parsed.path != '/s' and not re.fullmatch(r'/s/[A-Za-z0-9_-]+', parsed.path):
            return ''
        return urllib.parse.urlunsplit(('https', 'mp.weixin.qq.com', parsed.path, parsed.query, ''))
    except (TypeError, ValueError):
        return ''


def article_identity(value):
    url = safe_article_url(value)
    if not url:
        raise WeChatError('文章缺少可用的微信原文链接')
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    identity = [(key, value) for key, value in pairs if key in ('__biz', 'mid', 'idx', 'sn')]
    # The identity parameters identify individual articles. Never discard mid/idx/sn.
    if {'__biz', 'mid', 'idx'} <= {key for key, _ in identity}:
        pairs = identity
    return urllib.parse.urlunsplit(('https', 'mp.weixin.qq.com', parsed.path, urllib.parse.urlencode(sorted(pairs)), ''))


def safe_image_url(value):
    try:
        value = unescape(value or '').strip()
        if value.startswith('//'):
            value = 'https:' + value
        parsed = urllib.parse.urlsplit(value)
        if len(value) > 8192 or parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return ''
        if parsed.hostname in IMAGE_HOSTS:
            return value
        if parsed.hostname == FEED_HOST and parsed.path in ('/img-proxy', '/img-proxy/'):
            query = urllib.parse.parse_qs(parsed.query)
            targets = query.get('u', [])
            if len(targets) == 1 and urllib.parse.urlsplit(targets[0]).hostname in IMAGE_HOSTS and safe_image_url(targets[0]):
                return value
    except (TypeError, ValueError):
        pass
    return ''


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.images = []
        self.ignored = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'iframe', 'svg'):
            self.ignored.append(tag)
        if self.ignored:
            return
        if tag in ('p', 'div', 'section', 'br', 'li', 'h1', 'h2', 'h3'):
            self.parts.append(' ')
        if tag == 'img':
            attributes = dict(attrs)
            for field in ('data-src', 'src'):
                image = safe_image_url(attributes.get(field))
                if image and image not in self.images:
                    self.images.append(image)

    def handle_endtag(self, tag):
        if self.ignored and tag == self.ignored[-1]:
            self.ignored.pop()
        if not self.ignored and tag in ('p', 'div', 'section', 'li'):
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)

    def plain(self, limit):
        return re.sub(r'\s+', ' ', ''.join(self.parts)).strip()[:limit]


class ReaderHTML(HTMLParser):
    """Convert untrusted markup to an inert tree without retaining attributes."""
    allowed = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
               'blockquote', 'pre', 'code', 'strong', 'b', 'em', 'i', 'br'}
    blocked = {'script', 'style', 'noscript', 'iframe', 'svg', 'math', 'form',
               'button', 'textarea', 'select', 'object', 'embed', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = [(None, self.nodes)]
        self.ignored = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        if self.ignored:
            if tag in self.blocked:
                self.ignored.append(tag)
            return
        if tag in self.blocked:
            self.ignored.append(tag)
            return
        target = self.stack[-1][1]
        if tag == 'img':
            attrs = dict(attrs)
            image = safe_image_url(attrs.get('data-src')) or safe_image_url(attrs.get('src'))
            if image:
                index = len(self.images)
                self.images.append(image)
                target.append({'type': 'image', 'index': index})
        elif tag in self.allowed:
            node = {'type': tag, 'children': []}
            target.append(node)
            if tag != 'br':
                self.stack.append((tag, node['children']))
        elif tag in ('div', 'section', 'article'):
            target.append({'type': 'br', 'children': []})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ('img', 'br'):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.ignored and data:
            self.stack[-1][1].append({'type': 'text', 'text': data})


def plain_text(value, limit):
    parser = ArticleHTML()
    parser.feed(value or '')
    return parser.plain(limit)


def parse_feed(raw, source):
    if len(raw) > XML_LIMIT or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
        raise WeChatError('RSS 大小超限或含不支持的 XML 声明')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise WeChatError('RSS XML 格式无效') from None
    channel = root.find('channel')
    if channel is None:
        raise WeChatError('返回内容不是 RSS 频道')
    entries = channel.findall('item')
    rows = {}
    for entry in entries:
        url = safe_article_url(entry.findtext('link'))
        title = plain_text(entry.findtext('title'), 500)
        if not url or not title:
            continue
        try:
            published = email.utils.parsedate_to_datetime(entry.findtext('pubDate', ''))
            if published.tzinfo is None:
                continue
            timestamp = published.timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
        description = entry.findtext('description', '')
        body = entry.findtext('{http://purl.org/rss/1.0/modules/content/}encoded', '')
        parser = ArticleHTML()
        parser.feed(body or description)
        reader = ReaderHTML()
        if body and body.strip():
            reader.feed(body)
        summary = plain_text(description, 220) or parser.plain(220)
        identity = article_identity(url)
        article_id = hashlib.sha256((source['id'] + '\n' + identity).encode()).hexdigest()
        rows[article_id] = {'id': article_id, 'sourceId': source['id'], 'title': title,
                            'url': url, 'summary': summary, 'publishedAt': published.astimezone(TZ).isoformat(timespec='seconds'),
                            'timestamp': timestamp, 'cover': parser.images[0] if parser.images else '',
                            'body': json.dumps({'nodes': reader.nodes, 'images': reader.images}, ensure_ascii=False) if reader.nodes else ''}
    if entries and not rows:
        raise WeChatError('RSS 含文章但未解析到有效标题、发布时间和原文链接')
    return list(rows.values())


def feed_url_allowed(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme == 'https' and parsed.hostname == FEED_HOST and not parsed.username and not parsed.password and parsed.port in (None, 443) and not parsed.query and bool(re.fullmatch(r'/feed/[a-f0-9]{40}\.xml', parsed.path))
    except (TypeError, ValueError):
        return False


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, request, response, code, message, headers, new_url):
        if not self.allowed(new_url):
            raise WeChatError('远端重定向超出允许的访问范围')
        return super().redirect_request(request, response, code, message, headers, new_url)


def download(url, *, image=False, etag='', modified=''):
    allowed = safe_image_url if image else feed_url_allowed
    if not allowed(url):
        raise WeChatError('请求地址不在允许范围内')
    headers = {'User-Agent': USER_AGENT, 'Accept': 'image/jpeg,image/png,image/webp,image/gif' if image else 'application/rss+xml,application/xml'}
    if etag:
        headers['If-None-Match'] = etag
    if modified:
        headers['If-Modified-Since'] = modified
    # Use the current network/TUN route without an application-specific proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RestrictedRedirect(allowed))
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
            limit = IMAGE_LIMIT if image else XML_LIMIT
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise WeChatError('返回内容超过大小限制')
            return raw, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, {key.lower(): value for key, value in error.headers.items()}
        raise WeChatError('上游 HTTP %s' % error.code) from None
    except (OSError, ValueError):
        raise WeChatError('网络连接失败或超时') from None


def image_type(raw):
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        return 'image/webp'
    raise WeChatError('封面返回的内容不是支持的图片')


class WeChatService:
    def __init__(self, config_path=None, data_dir=None):
        config_path = Path(config_path or ROOT / 'config' / 'wechat-sources.json')
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        self.interval = max(1800, int(config.get('refreshIntervalSeconds', 1800)))
        self.cooldown = max(60, int(config.get('manualCooldownSeconds', 300)))
        self.sources = config['sources']
        ids = set()
        for source in self.sources:
            if not re.fullmatch(r'[a-z0-9-]{1,60}', source['id']) or source['id'] in ids or not source['name'] or not feed_url_allowed(source['url']):
                raise WeChatError('公众号来源配置无效或重复')
            ids.add(source['id'])
        from workbench_config import DATA_DIR
        self.data_dir = Path(data_dir or DATA_DIR / 'wechat')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.data_dir / 'articles.sqlite3'
        self.lock = threading.RLock()
        self.image_locks = [threading.Lock() for _ in range(4)]
        self.cover_failures = {}
        self.active = set()
        self.refreshing = False
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, last_attempt REAL DEFAULT 0,
                    last_success REAL DEFAULT 0, error TEXT DEFAULT '', etag TEXT DEFAULT '', modified TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
                    summary TEXT NOT NULL, published_at TEXT NOT NULL, timestamp REAL NOT NULL,
                    cover TEXT NOT NULL, first_seen REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS articles_source_date ON articles(source_id, timestamp DESC);
                CREATE TABLE IF NOT EXISTS covers (url TEXT PRIMARY KEY, content_type TEXT NOT NULL, data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY, article_id TEXT NOT NULL, status TEXT NOT NULL,
                    progress TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    workbuddy_job_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS analysis_runs_article_date ON analysis_runs(article_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS summary_runs (
                    run_id TEXT PRIMARY KEY, article_id TEXT NOT NULL, status TEXT NOT NULL,
                    progress TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    workbuddy_job_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS summary_runs_article_date ON summary_runs(article_id, created_at DESC);
            ''')
            if 'body' not in {row['name'] for row in db.execute('PRAGMA table_info(articles)')}:
                db.execute("ALTER TABLE articles ADD COLUMN body TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE sources SET etag='', modified='', last_attempt=0")
            if 'workbuddy_job_id' not in {row['name'] for row in db.execute('PRAGMA table_info(analysis_runs)')}:
                db.execute("ALTER TABLE analysis_runs ADD COLUMN workbuddy_job_id TEXT NOT NULL DEFAULT ''")
            for source in self.sources:
                db.execute('INSERT INTO sources(id,url) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET etag=CASE WHEN sources.url=excluded.url THEN etag ELSE \'\' END, modified=CASE WHEN sources.url=excluded.url THEN modified ELSE \'\' END, last_attempt=CASE WHEN sources.url=excluded.url THEN last_attempt ELSE 0 END, url=excluded.url', (source['id'], source['url']))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self):
        with self.lock, self.connect() as db:
            metadata = {row['id']: dict(row) for row in db.execute('SELECT * FROM sources')}
            names = {source['id']: source['name'] for source in self.sources}
            articles = []
            analysis = {}
            summaries = {}
            for run in db.execute('SELECT article_id,run_id,status,error,progress FROM analysis_runs ORDER BY created_at DESC,run_id DESC'):
                analysis.setdefault(run['article_id'], run)
            for run in db.execute('SELECT article_id,run_id,status,error,progress FROM summary_runs ORDER BY created_at DESC,run_id DESC'):
                summaries.setdefault(run['article_id'], run)
            for row in db.execute('SELECT * FROM articles ORDER BY timestamp DESC,id'):
                if row['source_id'] not in names:
                    continue
                latest_analysis = analysis.get(row['id'])
                latest_summary = summaries.get(row['id'])
                articles.append({'id': row['id'], 'sourceId': row['source_id'], 'source': names[row['source_id']],
                                 'title': row['title'], 'url': row['url'], 'summary': row['summary'],
                                 'timestamp': row['timestamp'], 'publishedAt': row['published_at'],
                                 'hasCover': bool(row['cover']),
                                 'analysisStatus': latest_analysis['status'] if latest_analysis else 'none',
                                 'analysisRunId': latest_analysis['run_id'] if latest_analysis else '',
                                 'analysisError': latest_analysis['error'] if latest_analysis else '',
                                 'analysisPercent': analysis_progress(latest_analysis['status'], latest_analysis['progress']) if latest_analysis else 0,
                                 'analysisProgress': latest_analysis['progress'] if latest_analysis else '',
                                 'summaryStatus': latest_summary['status'] if latest_summary else 'none',
                                 'summaryRunId': latest_summary['run_id'] if latest_summary else '',
                                 'summaryError': latest_summary['error'] if latest_summary else '',
                                 'summaryPercent': analysis_progress(latest_summary['status'], latest_summary['progress']) if latest_summary else 0,
                                 'summaryProgress': latest_summary['progress'] if latest_summary else ''})
            counts = {key: 0 for key in names}
            for row in articles:
                counts[row['sourceId']] += 1
            sources = []
            for source in self.sources:
                row = metadata[source['id']]
                success = row['last_success']
                sources.append({'id': source['id'], 'name': source['name'], 'count': counts[source['id']],
                                'status': '正在同步' if source['id'] in self.active else '更新失败' if row['error'] else '已同步' if success else '等待首次同步',
                                'error': row['error'], 'stale': bool(row['error']),
                                'lastSuccess': dt.datetime.fromtimestamp(success, TZ).isoformat(timespec='seconds') if success else '',
                                'lastAttempt': row['last_attempt']})
            last_attempt = max((metadata[source['id']]['last_attempt'] for source in self.sources), default=0)
            return {'sources': sources, 'articles': articles, 'refreshing': self.refreshing,
                    'intervalSeconds': self.interval, 'cooldownSeconds': self.cooldown,
                    'retryAfterSeconds': max(0, int(last_attempt + self.cooldown - time.time() + 0.999))}

    def refresh_source(self, source):
        identity = source['id']
        with self.lock:
            self.active.add(identity)
        try:
            with self.connect() as db:
                previous = dict(db.execute('SELECT * FROM sources WHERE id=?', (identity,)).fetchone())
                db.execute('UPDATE sources SET last_attempt=? WHERE id=?', (time.time(), identity))
            raw, headers = download(source['url'], etag=previous['etag'], modified=previous['modified'])
            rows = parse_feed(raw, source) if raw is not None else []
            now = time.time()
            with self.connect() as db:
                for row in rows:
                    db.execute('''INSERT INTO articles(id,source_id,title,url,summary,published_at,timestamp,cover,first_seen,body) VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,summary=excluded.summary,
                        published_at=excluded.published_at,timestamp=excluded.timestamp,
                        cover=CASE WHEN excluded.cover='' THEN articles.cover ELSE excluded.cover END,
                        body=CASE WHEN excluded.body='' THEN articles.body ELSE excluded.body END''',
                               (row['id'], identity, row['title'], row['url'], row['summary'], row['publishedAt'], row['timestamp'], row['cover'], now, row['body']))
                db.execute('UPDATE sources SET last_success=?,error=\'\',etag=?,modified=? WHERE id=?',
                           (now, headers.get('etag', previous['etag']), headers.get('last-modified', previous['modified']), identity))
        except Exception as error:
            message = str(error) if isinstance(error, WeChatError) else 'RSS 解析或本地保存失败（%s）' % type(error).__name__
            with self.connect() as db:
                db.execute('UPDATE sources SET error=? WHERE id=?', (message[:300], identity))
        finally:
            with self.lock:
                self.active.discard(identity)

    def request_refresh(self, manual=False):
        with self.lock, self.connect() as db:
            if self.refreshing:
                return {'accepted': False, 'running': True, 'retryAfterSeconds': 0}
            states = {row['id']: dict(row) for row in db.execute('SELECT id,last_attempt FROM sources')}
            last = max((states[source['id']]['last_attempt'] for source in self.sources), default=0)
            retry = max(0, int(last + self.cooldown - time.time() + 0.999))
            if manual and retry:
                return {'accepted': False, 'running': False, 'retryAfterSeconds': retry}
            due = [source for source in self.sources if manual or time.time() - states[source['id']]['last_attempt'] >= self.interval]
            if not due:
                return {'accepted': False, 'running': False, 'retryAfterSeconds': retry}
            self.refreshing = True
        def job():
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(self.refresh_source, due))
            finally:
                with self.lock:
                    self.refreshing = False
        threading.Thread(target=job, daemon=True).start()
        return {'accepted': True, 'running': True, 'retryAfterSeconds': 0}

    def cover(self, article_id):
        if not re.fullmatch(r'[a-f0-9]{64}', article_id):
            raise WeChatError('封面标识无效')
        with self.connect() as db:
            row = db.execute('SELECT source_id,cover FROM articles WHERE id=?', (article_id,)).fetchone()
        if not row or row['source_id'] not in {source['id'] for source in self.sources} or not safe_image_url(row['cover']):
            raise WeChatError('此文章没有可用封面')
        return self.cached_image(row['cover'])

    def article(self, article_id):
        if not re.fullmatch(r'[a-f0-9]{64}', article_id):
            raise WeChatError('文章标识无效')
        with self.connect() as db:
            row = db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
        names = {source['id']: source['name'] for source in self.sources}
        if not row or row['source_id'] not in names or not safe_article_url(row['url']):
            raise WeChatError('文章不存在')
        body = json.loads(row['body']) if row['body'] else None
        return {'id': row['id'], 'source': names[row['source_id']], 'title': row['title'],
                'url': safe_article_url(row['url']), 'summary': row['summary'], 'timestamp': row['timestamp'],
                'publishedAt': row['published_at'], 'nodes': body['nodes'] if body else None}

    def body_image(self, article_id, index):
        if not re.fullmatch(r'[a-f0-9]{64}', article_id):
            raise WeChatError('文章标识无效')
        with self.connect() as db:
            row = db.execute('SELECT source_id,body FROM articles WHERE id=?', (article_id,)).fetchone()
        if not row or row['source_id'] not in {source['id'] for source in self.sources} or not row['body']:
            raise WeChatError('图片不存在')
        images = json.loads(row['body'])['images']
        if index < 0 or index >= len(images) or not safe_image_url(images[index]):
            raise WeChatError('图片不存在')
        return self.cached_image(images[index])

    def cached_image(self, url):
        # Limit concurrent cover downloads and coalesce duplicate requests.
        bucket = int(hashlib.sha256(url.encode()).hexdigest()[:2], 16) % len(self.image_locks)
        with self.image_locks[bucket]:
            with self.connect() as db:
                cached = db.execute('SELECT * FROM covers WHERE url=?', (url,)).fetchone()
            if cached:
                return bytes(cached['data']), cached['content_type']
            if time.monotonic() < self.cover_failures.get(url, 0):
                raise WeChatError('封面暂不可用，稍后重试')
            try:
                raw, _ = download(url, image=True)
                if not raw:
                    raise WeChatError('封面返回内容为空')
                content_type = image_type(raw)
                with self.connect() as db:
                    db.execute('INSERT OR REPLACE INTO covers VALUES(?,?,?)', (url, content_type, raw))
                return raw, content_type
            except WeChatError:
                self.cover_failures[url] = time.monotonic() + 300
                raise

    def run(self):
        while True:
            self.request_refresh()
            time.sleep(30)


def handle_request(handler, service, desktop_token):
    path = urllib.parse.urlsplit(handler.path).path
    if path != '/api/wechat' and not path.startswith('/api/wechat/'):
        return False
    if not handler.local_request():
        handler.send_error(403)
        return True
    image_match = re.fullmatch(r'/api/wechat/image/([a-f0-9]{64})/(0|[1-9][0-9]{0,5})', path)
    cover_match = re.fullmatch(r'/api/wechat/cover/([a-f0-9]{64})', path)
    if handler.command == 'GET' and (cover_match or image_match) and handler.path == path:
        try:
            raw, content_type = service.cover(cover_match.group(1)) if cover_match else service.body_image(image_match.group(1), int(image_match.group(2)))
            handler.send_response(200)
            handler.send_header('Content-Type', content_type)
            handler.send_header('Content-Length', str(len(raw)))
            handler.send_header('Cache-Control', 'private, max-age=86400')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            # The dashboard and bridge use different loopback ports. These are
            # public article images, addressed only by a stored article ID.
            handler.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
            handler.end_headers()
            handler.wfile.write(raw)
        except WeChatError:
            handler.send_error(404, 'Cover unavailable')
        return True
    if handler.path != path or handler.headers.get('Transfer-Encoding') or handler.headers.get('Content-Length', '0') != '0':
        handler.json_response(400, {'message': '公众号接口不接受额外参数'})
        return True
    article_match = re.fullmatch(r'/api/wechat/article/([a-f0-9]{64})', path)
    if article_match and handler.command == 'GET':
        try:
            handler.json_response(200, service.article(article_match.group(1)))
        except WeChatError:
            handler.send_error(404)
    elif path == '/api/wechat' and handler.command == 'GET':
        handler.json_response(200, service.snapshot())
    elif path == '/api/wechat/refresh' and handler.command == 'POST':
        if not handler.desktop_request() or not secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), desktop_token):
            handler.send_error(403)
        else:
            result = service.request_refresh(manual=True)
            if not result['accepted'] and not result['running']:
                handler.json_response(429, {**result, 'message': '请在 %s 秒后再刷新' % result['retryAfterSeconds']}, {'Retry-After': str(result['retryAfterSeconds'])})
            else:
                handler.json_response(202, {**result, 'message': '正在检查公众号新文章'})
    else:
        handler.send_error(404)
    return True
