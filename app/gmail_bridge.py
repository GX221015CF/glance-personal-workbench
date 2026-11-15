"""Multi-account Gmail metadata reader. Credentials stay in Windows DPAPI storage."""
from __future__ import annotations

import argparse
import base64
import copy
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SCOPE = 'https://www.googleapis.com/auth/gmail.metadata'
LOGIN_SCOPE = SCOPE + ' openid email profile'
USERINFO = 'https://openidconnect.googleapis.com/v1/userinfo'
CALLBACK = 'http://127.0.0.1:8182/oauth/gmail/callback'
from workbench_config import DATA_DIR
LEGACY_PRIVATE_ROOT = DATA_DIR / 'legacy' / 'Gmail'
PRIVATE_ROOT = DATA_DIR / 'Google'
TZ = dt.timezone(dt.timedelta(hours=8))


class GmailError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def protect(raw, decrypt=False):
    if os.name != 'nt':
        raise GmailError('此凭据存储需要 Windows 用户加密支持')

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise GmailError('无法访问本机加密凭据，请使用原 Windows 用户')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else PRIVATE_ROOT

    def read(self, name, default):
        target = self.root / (name + '.dpapi')
        # Explicit stores (calendar, tests, audit files) remain isolated. A corrupt
        # shared file must surface an error instead of restoring older credentials.
        if self.root == PRIVATE_ROOT and not target.exists():
            target = LEGACY_PRIVATE_ROOT / (name + '.dpapi')
        try:
            return json.loads(protect(target.read_bytes(), decrypt=True))
        except FileNotFoundError:
            return copy.deepcopy(default)

    def write(self, name, value):
        encrypted = protect(json.dumps(value, ensure_ascii=False).encode())
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / (name + '.dpapi')
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(encrypted)
        temporary.replace(target)


def validate_client(document):
    config = document.get('installed')
    if not isinstance(config, dict) or not re.fullmatch(r'[A-Za-z0-9._-]+\.apps\.googleusercontent\.com', config.get('client_id', '')) or not config.get('client_secret'):
        raise GmailError('请使用 Google 下载的“桌面应用”OAuth 客户端 JSON')
    return {'client_id': config['client_id'], 'client_secret': config['client_secret']}


def google_forbidden_message(error, url):
    # Inspect only structured reason codes. Never display Google's raw body,
    # which may contain account details, request data, or credentials.
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
    if reasons & {'SERVICE_DISABLED', 'accessNotConfigured', 'serviceDisabled'}:
        return 'Gmail API 尚未启用。请在 OAuth 客户端所属的 Google Cloud 项目中启用 Gmail API，等待几分钟后从工作台重新添加账号。'
    if reasons & {'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'insufficientPermissions'}:
        return 'Gmail 授权权限不足。请从工作台重新添加账号，并允许读取邮件元数据（gmail.metadata）。'
    if reasons & {'domainPolicy', 'ORG_RESTRICTION_VIOLATION'}:
        return '账号的组织策略禁止访问 Gmail API，请联系该 Google Workspace 账号的管理员。'
    if reasons & {'rateLimitExceeded', 'userRateLimitExceeded', 'dailyLimitExceeded', 'RATE_LIMIT_EXCEEDED', 'QUOTA_EXCEEDED'}:
        return 'Gmail API 请求限额已达到，请稍后重试或检查 Google Cloud 的 API 配额。'
    stage = 'Google 令牌交换' if url == 'https://oauth2.googleapis.com/token' else 'Gmail API 请求'
    return stage + '被拒绝（HTTP 403）。请检查同一项目的 Gmail API 启用状态、账号权限与组织策略。'


def google_json(url, *, bearer=None, form=None):
    # Callers cannot select arbitrary hosts, URLs, or Gmail write operations.
    if not (url in ('https://oauth2.googleapis.com/token', USERINFO) or url.startswith('https://gmail.googleapis.com/gmail/v1/users/me/')):
        raise GmailError('不支持此 Google 请求')
    headers = {'Accept': 'application/json', 'User-Agent': 'PersonalWorkbench-Gmail/1.0'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    body = urllib.parse.urlencode(form).encode() if form else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (400, 401):
            raise GmailError('授权已失效或未获许可，请重新连接此邮箱', 401) from None
        if error.code == 403:
            raise GmailError(google_forbidden_message(error, url), 403) from None
        raise GmailError('Google 服务暂不可用，稍后会重试', 503) from None
    except (OSError, ValueError):
        raise GmailError('无法连接 Gmail，稍后会重试', 503) from None


def account_id(email):
    return hashlib.sha256(email.casefold().encode()).hexdigest()[:20]


def profile_avatar(token, email):
    scopes = token.get('scope', '').split()
    if not any(scope in scopes for scope in ('profile', 'https://www.googleapis.com/auth/userinfo.profile')):
        return {'url': '', 'status': 'authorization'}
    try:
        profile = google_json(USERINFO, bearer=token['access_token'])
        if profile.get('email_verified') is not True or str(profile.get('email', '')).casefold() != email.casefold():
            return {'url': '', 'status': 'unavailable'}
        picture = profile.get('picture', '')
        if not isinstance(picture, str) or len(picture) > 2048:
            return {'url': '', 'status': 'unavailable'}
        parsed = urllib.parse.urlsplit(picture)
        host = parsed.hostname or ''
        if parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in (None, 443) and (host == 'googleusercontent.com' or host.endswith('.googleusercontent.com')):
            return {'url': picture, 'status': 'ready'}
    except (GmailError, KeyError, ValueError, TypeError):
        pass
    return {'url': '', 'status': 'unavailable'}


def inbox_url(email, thread=None):
    base = 'https://mail.google.com/mail/u/?authuser=' + urllib.parse.quote(email, safe='')
    return base + '#inbox' + ('/' + thread if thread else '')


def normalize_message(message, email):
    if not re.fullmatch(r'[0-9a-f]+', str(message.get('id', ''))) or not re.fullmatch(r'[0-9a-f]+', str(message.get('threadId', ''))):
        raise GmailError('收到无法识别的邮件标识')
    headers = {row.get('name', '').casefold(): row.get('value', '') for row in (message.get('payload') or {}).get('headers') or []}
    timestamp = int(message.get('internalDate') or 0) / 1000
    when = dt.datetime.fromtimestamp(timestamp, TZ)
    return {'id': message['id'], 'accountId': account_id(email), 'account': email,
            'subject': headers.get('subject') or '（无主题）', 'sender': headers.get('from') or '未知发件人',
            'unread': 'UNREAD' in (message.get('labelIds') or []), 'timestamp': timestamp,
            'timeLabel': when.strftime('%m-%d %H:%M'), 'url': inbox_url(email, message['threadId'])}


class GmailService:
    def __init__(self, store=None):
        self.store = store or CredentialStore()
        self.lock = threading.RLock()
        self.job_lock = threading.Lock()
        self.pending = {}
        self.views = {}
        self.avatars = {}
        self.last_request = 0
        self.config_error = False
        self.refresh_again = False
        self.account_versions = {}

    def settings(self):
        try:
            return self.store.read('client', None), self.store.read('accounts', {})
        except (GmailError, OSError, ValueError):
            raise GmailError('无法读取本机加密配置，请检查 Windows 用户或重新导入客户端配置', 503) from None

    def snapshot(self):
        with self.lock:
            try:
                client, saved = self.settings()
                error = ''
            except GmailError as problem:
                client, saved, error = None, {}, str(problem)
            accounts, messages = [], []
            for email in sorted(saved):
                view = self.views.get(email, {})
                avatar = self.avatars.get(email, saved[email].get('avatar', {'url': '', 'status': 'authorization'}))
                accounts.append({'id': account_id(email), 'email': email, 'url': inbox_url(email),
                                 'avatarUrl': avatar['url'], 'avatarStatus': avatar['status'],
                                 'unread': view.get('unread'), 'checkedAt': view.get('checkedAt', ''),
                                 'status': view.get('status', '等待同步'), 'error': view.get('error', ''),
                                 'needsReconnect': view.get('needsReconnect', False),
                                 'stale': view.get('stale', False), 'available': view.get('available', False)})
                messages.extend(view.get('messages', []))
            return {'configured': bool(client), 'accounts': accounts, 'messages': sorted(messages, key=lambda row: -row['timestamp']),
                    'refreshing': self.job_lock.locked(), 'error': error, 'scope': 'metadata', 'intervalSeconds': 300}

    def begin_connect(self, target_id=None):
        with self.lock:
            client, accounts = self.settings()
            if not client:
                raise GmailError('尚未配置 Google 登录，请先完成页面中的接入设置', 409)
            email = None
            if target_id is not None:
                email = next((email for email in accounts if account_id(email) == target_id), None)
                if email is None:
                    raise GmailError('未找到需要重新连接的邮箱，请刷新账号列表', 404)
            now = time.monotonic()
            self.pending = {key: value for key, value in self.pending.items() if value['expires'] > now}
            if len(self.pending) >= 5:
                raise GmailError('请先完成已打开的登录窗口，或稍后重试', 429)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            self.pending[state] = {'verifier': verifier, 'expires': now + 600, 'email': email,
                                   'accountVersions': dict(self.account_versions)}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            query = urllib.parse.urlencode({'client_id': client['client_id'], 'redirect_uri': CALLBACK, 'response_type': 'code',
                                           'scope': LOGIN_SCOPE, 'access_type': 'offline', 'prompt': 'select_account consent',
                                           'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
            if email:
                query += '&' + urllib.parse.urlencode({'login_hint': email})
            return 'https://accounts.google.com/o/oauth2/v2/auth?' + query

    def complete_connect(self, query):
        states, codes = query.get('state', []), query.get('code', [])
        if len(states) != 1:
            raise GmailError('登录请求无效，请从工作台重新添加账号')
        with self.lock:
            pending = self.pending.pop(states[0], None)
            client, _ = self.settings()
        if not pending or pending['expires'] < time.monotonic() or not client:
            raise GmailError('登录请求已过期，请从工作台重新添加账号')
        if query.get('error') or len(codes) != 1 or len(codes[0]) > 4096:
            raise GmailError('此次登录未完成，未添加账号')
        token = google_json('https://oauth2.googleapis.com/token', form={**client, 'grant_type': 'authorization_code',
                            'code': codes[0], 'redirect_uri': CALLBACK, 'code_verifier': pending['verifier']})
        if SCOPE not in token.get('scope', '').split() or not token.get('refresh_token') or not token.get('access_token'):
            raise GmailError('未取得持续读取邮件列表的授权，请重新连接')
        profile = google_json('https://gmail.googleapis.com/gmail/v1/users/me/profile', bearer=token['access_token'])
        email = profile.get('emailAddress', '')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise GmailError('无法确认邮箱身份，未保存授权')
        email = email.casefold()
        if pending.get('email') and email != pending['email'].casefold():
            raise GmailError('此次登录的邮箱与要重连的账号不一致，请返回工作台重新连接并选择对应邮箱')
        avatar = profile_avatar(token, email)
        with self.lock:
            _, accounts = self.settings()
            if self.account_versions.get(email, 0) != pending['accountVersions'].get(email, 0):
                raise GmailError('此邮箱连接已被删除，请从工作台重新添加账号')
            # Each verified Google email retains its own token. Reconnecting updates only that account.
            previous = accounts.get(email, {})
            accounts[email] = {'refresh_token': token['refresh_token']}
            if avatar['status'] == 'ready':
                accounts[email]['avatar'] = avatar
            elif previous.get('avatar'):
                accounts[email]['avatar'] = previous['avatar']
                avatar = previous['avatar']
            self.avatars[email] = avatar
            self.store.write('accounts', accounts)
            view = dict(self.views.get(email, {}))
            view.update(status='等待同步', needsReconnect=False, error='')
            self.views[email] = view
        self.request_refresh(force=True)

    def remove_account(self, target_id):
        with self.lock:
            _, accounts = self.settings()
            email = next((email for email in accounts if account_id(email) == target_id), None)
            if email is None:
                return False
            del accounts[email]
            # Persist first so a failed write leaves the visible connection intact.
            try:
                self.store.write('accounts', accounts)
            except OSError:
                raise GmailError('删除失败，本机连接记录未能保存，请稍后重试', 503) from None
            self.account_versions[email] = self.account_versions.get(email, 0) + 1
            self.views.pop(email, None)
            self.avatars.pop(email, None)
            self.pending = {key: value for key, value in self.pending.items() if value.get('email') != email}
            return True

    def request_refresh(self, force=False):
        with self.lock:
            if not force and time.monotonic() - self.last_request < 15:
                return
            if not self.job_lock.acquire(blocking=False):
                if force:
                    self.refresh_again = True
                return
            self.last_request = time.monotonic()
        def job():
            try:
                while True:
                    self.refresh_all()
                    with self.lock:
                        if self.refresh_again:
                            self.refresh_again = False
                            continue
                        self.job_lock.release()
                        return
            except Exception:
                # Unexpected failures must also make the next refresh possible.
                with self.lock:
                    self.job_lock.release()
                raise
        threading.Thread(target=job, daemon=True).start()

    def refresh_all(self):
        try:
            with self.lock:
                client, accounts = self.settings()
            if not client:
                return
            for email, credential in accounts.items():
                try:
                    token = google_json('https://oauth2.googleapis.com/token', form={**client, 'grant_type': 'refresh_token', 'refresh_token': credential['refresh_token']})
                    bearer = token['access_token']
                    with self.lock:
                        if self.settings()[1].get(email, {}).get('refresh_token') != credential['refresh_token']:
                            continue
                        avatar = self.avatars.get(email, credential.get('avatar', {}))
                    # Avatar failures are independent of mail synchronization.
                    if not avatar.get('checkedAt') or time.monotonic() - avatar['checkedAt'] > 3600:
                        fetched = profile_avatar(token, email)
                        if fetched['status'] != 'ready' and avatar.get('url'):
                            fetched = dict(avatar)
                        fetched['checkedAt'] = time.monotonic()
                        with self.lock:
                            if self.settings()[1].get(email, {}).get('refresh_token') != credential['refresh_token']:
                                continue
                            self.avatars[email] = fetched
                    base = 'https://gmail.googleapis.com/gmail/v1/users/me/'
                    label = google_json(base + 'labels/INBOX', bearer=bearer)
                    listing = google_json(base + 'messages?labelIds=INBOX&maxResults=20', bearer=bearer)
                    messages = []
                    for item in listing.get('messages') or []:
                        if not re.fullmatch(r'[0-9a-f]+', str(item.get('id', ''))):
                            raise GmailError('收到无法识别的邮件标识')
                        url = base + 'messages/' + item['id'] + '?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date&fields=id,threadId,labelIds,internalDate,payload/headers'
                        messages.append(normalize_message(google_json(url, bearer=bearer), email))
                    with self.lock:
                        if self.settings()[1].get(email, {}).get('refresh_token') != credential['refresh_token']:
                            continue
                        self.views[email] = {'available': True, 'status': '已连接', 'unread': label.get('messagesUnread'),
                                             'checkedAt': dt.datetime.now(TZ).isoformat(timespec='seconds'), 'messages': messages}
                except (GmailError, KeyError, ValueError, TypeError, OSError, OverflowError) as error:
                    with self.lock:
                        if self.settings()[1].get(email, {}).get('refresh_token') != credential.get('refresh_token'):
                            continue
                        old = dict(self.views.get(email, {}))
                        old.update({'stale': True, 'status': '需要重新授权' if isinstance(error, GmailError) and error.status == 401 else '同步暂不可用',
                                    'needsReconnect': isinstance(error, GmailError) and error.status == 401,
                                    'error': str(error) if isinstance(error, GmailError) else '暂未取得完整邮件列表，稍后会重试'})
                        self.views[email] = old
        except GmailError:
            return

    def run(self):
        while True:
            self.request_refresh(force=True)
            time.sleep(300)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import a local Google Desktop OAuth client into Windows encrypted storage.')
    parser.add_argument('command', choices=['import-client'])
    parser.add_argument('--file', required=True)
    args = parser.parse_args()
    try:
        document = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
        client = validate_client(document)
        store = CredentialStore()
        previous = store.read('client', None)
        if previous and previous != client and (store.read('accounts', {}) or store.read('youtube-accounts', {})):
            raise GmailError('已连接账号时不覆盖不同客户端，请先处理已有授权')
        store.write('client', client)
        print('Google client imported into Windows user-encrypted storage. Refresh the Gmail page.')
    except (GmailError, OSError, ValueError):
        parser.exit(1, 'Import failed. Check the file is a Google Desktop OAuth client JSON; existing credentials were not replaced.\n')
