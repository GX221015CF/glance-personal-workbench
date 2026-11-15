"""Read-only YouTube subscriptions, with a separate OAuth grant from Gmail."""
from __future__ import annotations

import base64
import concurrent.futures
import copy
import datetime as dt
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from gmail_bridge import CredentialStore, GmailError, TZ

SCOPE = 'https://www.googleapis.com/auth/youtube.readonly'
LOGIN_SCOPE = SCOPE + ' openid email'
CALLBACK = 'http://127.0.0.1:8182/oauth/youtube/callback'
API = 'https://www.googleapis.com/youtube/v3/'
CHANNEL = re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO = re.compile(r'[A-Za-z0-9_-]{11}')
INTERVAL = 3600


class YouTubeError(GmailError):
    pass


def youtube_json(resource, *, bearer=None, params=None, form=None):
    if resource not in ('token', 'userinfo', 'channels', 'subscriptions', 'playlistItems'):
        raise YouTubeError('不支持此 YouTube 请求')
    url = 'https://oauth2.googleapis.com/token' if resource == 'token' else API + resource
    if resource == 'userinfo':
        url = 'https://openidconnect.googleapis.com/v1/userinfo'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    headers = {'Accept': 'application/json', 'User-Agent': 'PersonalWorkbench-YouTube/1.0'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    body = urllib.parse.urlencode(form).encode() if form else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        reasons = set()
        try:
            payload = json.loads(error.read(65536))
            detail = payload.get('error') if isinstance(payload, dict) else None
            if isinstance(detail, dict):
                for field in ('details', 'errors'):
                    rows = detail.get(field)
                    if isinstance(rows, list):
                        reasons.update(row['reason'] for row in rows if isinstance(row, dict) and isinstance(row.get('reason'), str))
        except (OSError, ValueError):
            pass
        finally:
            error.close()
        if error.code in (400, 401) and (resource == 'token' or error.code == 401):
            raise YouTubeError('YouTube 授权已失效，请重新连接此账号', 401) from None
        if reasons & {'SERVICE_DISABLED', 'accessNotConfigured', 'serviceDisabled'}:
            raise YouTubeError('YouTube Data API v3 尚未启用，请在当前 Google 客户端所属项目启用，等待几分钟后重新连接。', 403) from None
        if reasons & {'quotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded', 'userRateLimitExceeded'} or error.code == 429:
            raise YouTubeError('YouTube API 请求配额或频率已达上限，请稍后重试；已有视频会保留。', 429) from None
        if reasons & {'insufficientPermissions', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'}:
            raise YouTubeError('未获得 YouTube 只读权限，请重新连接并允许查看 YouTube 账号。', 401) from None
        if reasons & {'youtubeSignupRequired', 'NoLinkedYouTubeAccount'}:
            raise YouTubeError('Google 暂未向此账号提供 YouTube 订阅 API 访问。普通观看者无需发布视频或成为创作者；请确认所选账号能在 YouTube 网页查看订阅。', 409) from None
        if error.code == 404:
            raise YouTubeError('此频道的上传列表暂不可用', 404) from None
        raise YouTubeError('YouTube 请求被拒绝（HTTP %s），请检查 API 设置和账号权限。' % error.code, 503) from None
    except (OSError, ValueError):
        raise YouTubeError('暂时无法连接 YouTube，请稍后重试', 503) from None


def video_rows(payload, channel):
    rows = []
    for item in payload.get('items', []):
        snippet, detail = item.get('snippet', {}), item.get('contentDetails', {})
        video = detail.get('videoId', '')
        published = detail.get('videoPublishedAt')
        if not VIDEO.fullmatch(video) or not published:
            continue
        try:
            when = dt.datetime.fromisoformat(published.replace('Z', '+00:00'))
            if when.tzinfo is None:
                continue
        except (ValueError, TypeError):
            continue
        if when > dt.datetime.now(dt.timezone.utc):
            continue
        rows.append({'id': video, 'title': snippet.get('title') or '（无标题）',
                     'channelId': channel['id'], 'channel': channel['title'],
                     'timestamp': when.timestamp(), 'publishedAt': published,
                     'timeLabel': when.astimezone(TZ).strftime('%m-%d %H:%M'),
                     'url': 'https://www.youtube.com/watch?v=' + video,
                     'thumbnail': 'https://i.ytimg.com/vi/' + video + '/mqdefault.jpg'})
    return rows


class YouTubeService:
    def __init__(self, store=None):
        self.store = store or CredentialStore()
        self.lock = threading.RLock()
        self.job_lock = threading.Lock()
        self.pending = {}
        self.views = {}
        self.cache = {}
        self.last_request = None

    def settings(self):
        try:
            return self.store.read('client', None), self.store.read('youtube-accounts', {})
        except (GmailError, OSError, ValueError):
            raise YouTubeError('无法读取本机 YouTube 加密配置，请检查 Windows 用户', 503) from None

    def snapshot(self):
        with self.lock:
            try:
                client, saved = self.settings()
                error = ''
            except YouTubeError as problem:
                client, saved, error = None, {}, str(problem)
            accounts, channels, videos = [], {}, {}
            for identity, credential in saved.items():
                view = self.views.get(identity, {})
                accounts.append({'id': identity, 'title': credential['title'],
                                 'available': view.get('available', False), 'status': view.get('status', '等待同步'),
                                 'checkedAt': view.get('checkedAt', ''), 'error': view.get('error', ''),
                                 'stale': view.get('stale', False), 'subscriptionCount': len(view.get('channels', []))})
                for row in view.get('channels', []):
                    entry = channels.setdefault(row['id'], {**row, 'accountIds': []})
                    entry['accountIds'].append(identity)
                for row in view.get('videos', []):
                    entry = videos.setdefault(row['id'], {**row, 'accountIds': []})
                    entry['accountIds'].append(identity)
            return {'configured': bool(client), 'accounts': accounts,
                    'channels': sorted(channels.values(), key=lambda row: row['title'].casefold()),
                    'videos': sorted(videos.values(), key=lambda row: -row['timestamp']),
                    'error': error, 'refreshing': self.job_lock.locked(), 'intervalSeconds': INTERVAL}

    def begin_connect(self):
        with self.lock:
            client, _ = self.settings()
            if not client:
                raise YouTubeError('请先导入 Google 桌面客户端配置', 409)
            now = time.monotonic()
            self.pending = {key: value for key, value in self.pending.items() if value['expires'] > now}
            if len(self.pending) >= 5:
                raise YouTubeError('请先完成已打开的 YouTube 登录窗口，或十分钟后重试', 429)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            self.pending[state] = {'verifier': verifier, 'expires': now + 600}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            query = urllib.parse.urlencode({'client_id': client['client_id'], 'redirect_uri': CALLBACK,
                                           'response_type': 'code', 'scope': LOGIN_SCOPE, 'access_type': 'offline',
                                           'prompt': 'select_account consent', 'state': state,
                                           'code_challenge': challenge, 'code_challenge_method': 'S256'})
            return 'https://accounts.google.com/o/oauth2/v2/auth?' + query

    def complete_connect(self, query):
        states, codes = query.get('state', []), query.get('code', [])
        if len(states) != 1:
            raise YouTubeError('登录请求无效，请从工作台重新连接 YouTube')
        with self.lock:
            pending = self.pending.pop(states[0], None)
            client, _ = self.settings()
        if not pending or pending['expires'] < time.monotonic() or not client:
            raise YouTubeError('YouTube 登录请求已过期，请重新连接')
        if query.get('error') or len(codes) != 1 or len(codes[0]) > 4096:
            raise YouTubeError('此次 YouTube 登录未完成，未添加账号')
        token = youtube_json('token', form={**client, 'grant_type': 'authorization_code', 'code': codes[0],
                                           'redirect_uri': CALLBACK, 'code_verifier': pending['verifier']})
        if SCOPE not in token.get('scope', '').split() or not token.get('access_token') or not token.get('refresh_token'):
            raise YouTubeError('未取得持续读取 YouTube 的只读授权，请重新连接')
        # An owned channel identifies a creator/brand account, but viewers can
        # subscribe without creating one. Never make channels.mine a prerequisite.
        try:
            profile = youtube_json('channels', bearer=token['access_token'], params={'part': 'snippet', 'mine': 'true'})
            items = profile.get('items', [])
        except YouTubeError as error:
            if error.status != 409:
                raise
            items = []
        if items and CHANNEL.fullmatch(items[0].get('id', '')):
            identity = items[0]['id']
            title = items[0].get('snippet', {}).get('title') or 'YouTube 账号'
        else:
            if 'openid' not in token.get('scope', '').split():
                raise YouTubeError('此登录链接来自旧版工作台，请返回 YouTube 页面重新点击连接')
            profile = youtube_json('userinfo', bearer=token['access_token'])
            subject, email = profile.get('sub', ''), profile.get('email', '')
            if not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', subject):
                raise YouTubeError('无法确认 Google 登录身份，未保存账号授权')
            if profile.get('email_verified') is not True or not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                raise YouTubeError('未取得已验证的 Google 账号邮箱，请重新连接并允许基本身份授权')
            identity = 'google-' + hashlib.sha256(subject.encode()).hexdigest()[:24]
            title = email
        with self.lock:
            _, accounts = self.settings()
            accounts[identity] = {'title': title, 'refresh_token': token['refresh_token']}
            self.store.write('youtube-accounts', accounts)
        self.request_refresh(force=True)

    def request_refresh(self, force=False):
        with self.lock:
            if not force and self.last_request is not None and time.monotonic() - self.last_request < 300:
                raise YouTubeError('为节省 YouTube API 配额，请在上次同步开始 5 分钟后再刷新', 429)
            if not self.job_lock.acquire(blocking=False):
                return
            self.last_request = time.monotonic()
        def job():
            try:
                self.refresh_all()
            finally:
                self.job_lock.release()
        threading.Thread(target=job, daemon=True).start()

    def subscriptions(self, bearer):
        channels, page, seen = {}, None, set()
        for _ in range(100):
            params = {'part': 'snippet', 'mine': 'true', 'maxResults': 50}
            if page:
                params['pageToken'] = page
            result = youtube_json('subscriptions', bearer=bearer, params=params)
            for item in result.get('items', []):
                snippet = item.get('snippet', {})
                identity = snippet.get('resourceId', {}).get('channelId', '')
                if CHANNEL.fullmatch(identity):
                    channels[identity] = {'id': identity, 'title': snippet.get('title') or identity,
                                          'url': 'https://www.youtube.com/channel/' + identity}
            page = result.get('nextPageToken')
            if not page:
                return list(channels.values())
            if page in seen:
                break
            seen.add(page)
        raise YouTubeError('订阅列表分页异常或超过 5000 个频道，未用不完整列表覆盖已有数据', 503)

    def fetch_videos(self, channel, bearer):
        cached = self.cache.get(channel['id'])
        if cached and time.monotonic() - cached['at'] < 300:
            return cached['videos']
        result = youtube_json('playlistItems', bearer=bearer, params={
            'part': 'snippet,contentDetails', 'playlistId': channel['playlist'], 'maxResults': 5,
            'fields': 'items(snippet(title),contentDetails(videoId,videoPublishedAt))'})
        videos = video_rows(result, channel)
        self.cache[channel['id']] = {'at': time.monotonic(), 'videos': videos}
        return videos

    def refresh_all(self):
        try:
            with self.lock:
                client, accounts = self.settings()
            if not client:
                return
            active_channels = set()
            for identity, credential in accounts.items():
                try:
                    token = youtube_json('token', form={**client, 'grant_type': 'refresh_token',
                                                       'refresh_token': credential['refresh_token']})
                    bearer = token['access_token']
                    channels = self.subscriptions(bearer)
                    active_channels.update(channel['id'] for channel in channels)
                    playlists = {}
                    for offset in range(0, len(channels), 50):
                        result = youtube_json('channels', bearer=bearer, params={
                            'part': 'contentDetails', 'id': ','.join(row['id'] for row in channels[offset:offset + 50])})
                        for row in result.get('items', []):
                            playlist = row.get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads', '')
                            if re.fullmatch(r'[A-Za-z0-9_-]{1,100}', playlist):
                                playlists[row['id']] = playlist
                    videos, failures, problems = [], 0, []
                    fetchable = []
                    for channel in channels:
                        if channel['id'] in playlists:
                            fetchable.append({**channel, 'playlist': playlists[channel['id']]})
                        else:
                            failures += 1
                            videos.extend(self.cache.get(channel['id'], {}).get('videos', []))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                        futures = {pool.submit(self.fetch_videos, channel, bearer): channel for channel in fetchable}
                        for future, channel in futures.items():
                            try:
                                videos.extend(future.result())
                            except (YouTubeError, KeyError, TypeError, ValueError, OSError):
                                failures += 1
                                videos.extend(self.cache.get(channel['id'], {}).get('videos', []))
                                error = future.exception()
                                if isinstance(error, YouTubeError) and str(error) not in problems:
                                    problems.append(str(error))
                    error = ('%s 个频道未能更新，已保留可用的旧视频。' % failures + ' '.join(problems[:1])) if failures else ''
                    with self.lock:
                        self.views[identity] = {'available': failures == 0 or bool(videos) or len(channels) > failures,
                                                'status': '部分频道更新失败' if failures else '已连接',
                                                'checkedAt': dt.datetime.now(TZ).isoformat(timespec='seconds'),
                                                'channels': channels, 'videos': sorted(videos, key=lambda row: -row['timestamp'])[:200],
                                                'stale': bool(failures), 'error': error}
                except (GmailError, KeyError, TypeError, ValueError, OSError) as error:
                    with self.lock:
                        old = dict(self.views.get(identity, {}))
                        old.update({'stale': True, 'status': '需要重新授权' if isinstance(error, GmailError) and error.status == 401 else '同步暂不可用',
                                    'error': str(error) if isinstance(error, GmailError) else '暂未取得完整 YouTube 数据，稍后重试'})
                        self.views[identity] = old
                        active_channels.update(row['id'] for row in old.get('channels', []))
            self.cache = {key: value for key, value in self.cache.items() if key in active_channels}
        except GmailError:
            return

    def run(self):
        while True:
            self.request_refresh(force=True)
            time.sleep(INTERVAL)
