"""Fixed IMA key API, local DPAPI configuration and read-only workbench routes.

No school cookies, WorkBuddy identity, arbitrary endpoint or automatic upload.
Cloud mutations live in the separately guarded owned-test probe.
"""
from __future__ import annotations

import datetime as dt
import contextlib
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from gmail_bridge import protect
from school_storage import private_root, save_encrypted_json

ROOT = private_root('Canvas').parent.parent / 'Integrations' / 'IMA'
CONFIG = ROOT / 'connection.dpapi'
BASE = 'https://ima.qq.com/openapi/wiki/v1/'
READS = {'get_addable_knowledge_base_list', 'get_knowledge_list', 'search_knowledge', 'get_media_info'}
PROBE_WRITES = {'create_folder', 'create_media', 'add_knowledge', 'rename_knowledge', 'move_knowledge'}
FILE_FORMATS = {
    'pdf': (1, 'application/pdf', 'pdf'),
    'doc': (3, 'application/msword', 'ole'),
    'docx': (3, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'zip'),
    'ppt': (4, 'application/vnd.ms-powerpoint', 'ole'),
    'pptx': (4, 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'zip'),
    'xls': (5, 'application/vnd.ms-excel', 'ole'),
    'xlsx': (5, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'zip'),
    'csv': (5, 'text/csv', 'text'), 'txt': (13, 'text/plain', 'text'),
    'md': (7, 'text/markdown', 'text'), 'html': (20, 'text/html', 'text'),
    'png': (9, 'image/png', 'png'), 'jpg': (9, 'image/jpeg', 'jpeg'),
    'jpeg': (9, 'image/jpeg', 'jpeg'), 'webp': (9, 'image/webp', 'webp'),
    'xmind': (14, 'application/x-xmind', 'zip'), 'epub': (21, 'application/epub+zip', 'zip'),
}
MESSAGES = {
    'NOT_CONFIGURED': '尚未配置ima，请运行本机ima连接工具。',
    'INVALID_CONFIG': '本机ima配置无法读取，原文件已保留，请重新配置。',
    'AUTH_REQUIRED': 'ima密钥无效、已失效或认证被拒绝，请在官方页面核对密钥。',
    'FORBIDDEN': 'ima拒绝此操作，当前密钥或知识库权限不足。',
    'RATE_LIMITED': 'ima请求频率受限，请稍后重试。',
    'INVALID_PARAMETER': 'ima参数被拒绝，需核对接口契约，原数据保持。',
    'NOT_OPEN': '该ima接口未开放或当前不可用。',
    'UPSTREAM_ERROR': 'ima服务暂时未能完成请求。',
    'NETWORK_ERROR': 'ima网络连接失败，请稍后重试。',
    'OUTCOME_UNKNOWN': '请求结果待核对，先重新读取目录，不要重复写入。',
    'INVALID_RESPONSE': 'ima响应结构不符，未确认操作成功。',
    'INCOMPLETE_LIST': 'ima分页未完整结束，不能将当前列表视为完整目录。',
    'INVALID_REQUEST': '本机请求参数不符合限定范围。',
    'MEDIA_ACCESS_RESTRICTED': '此原文无法通过受限后台访问，请使用ima客户端查看。',
    'COS_CREDENTIAL_INVALID': '文件上传临时凭据的结构或时间格式不符合已核实契约，上传尚未发出。',
    'COS_CREDENTIAL_EXPIRED': '文件上传临时凭据已过期，需安全取得新的上传凭据。',
    'COS_CREDENTIAL_NOT_YET_VALID': '文件上传临时凭据尚未生效，请核对服务端与本机时间后再继续。',
    'COS_UPLOAD_REJECTED': 'COS拒绝了测试文件上传，请核对上传临时凭据和签名诊断。',
    'COS_UPLOAD_FAILED': 'COS未接受测试文件上传，请查看上传阶段的HTTP状态。',
    'COS_UPLOAD_UNKNOWN': 'COS上传结果待确认，先回读远端状态，暂停重复上传。',
    'SYNC_BUSY': '已有ima同步正在执行，请等待该任务结束后查看结果。',
}


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')


class ImaError(Exception):
    def __init__(self, code, upstream_code=None, *, diagnostic=None):
        self.code = code
        self.upstream_code = upstream_code if type(upstream_code) is int else None
        self.diagnostic = diagnostic or {}
        super().__init__(MESSAGES.get(code, MESSAGES['UPSTREAM_ERROR']))


def identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or re.search(r'[\x00-\x20\x7f]', value):
        raise ImaError('INVALID_REQUEST')
    return value


def credentials(value):
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str)
            and 1 <= len(value[key]) <= 4096 and not re.search(r'[\s\x00-\x1f\x7f]', value[key])
            for key in ('clientId', 'apiKey')):
        raise ImaError('INVALID_CONFIG')
    return {key: value[key] for key in ('clientId', 'apiKey')}


def load_config(path=CONFIG):
    if not path.exists():
        return None
    try:
        value = json.loads(protect(path.read_bytes(), decrypt=True))
        if value.get('version') != 1:
            raise ValueError()
        credentials(value)
        return value
    except Exception:
        raise ImaError('INVALID_CONFIG') from None


def new_session():
    session = requests.Session()
    session.trust_env = False
    return session


@contextlib.contextmanager
def store_lock(path):
    """One Windows process at a time owns the specified encrypted store."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ImaError('SYNC_BUSY') from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def upload_bytes(credential, payload, content_type, *, session=None, on_send=None):
    """Upload bounded original bytes using only the returned COS credentials."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 20 * 1024 * 1024 or content_type not in {row[1] for row in FILE_FORMATS.values()}:
        raise ImaError('INVALID_REQUEST')
    diagnostic = {'stage': 'cos_credential_validation', 'uploadRequestSent': False}
    if not isinstance(credential, dict):
        raise ImaError('COS_CREDENTIAL_INVALID', diagnostic=diagnostic)
    fields = ['secret_id', 'secret_key', 'token', 'bucket_name', 'region', 'cos_key']
    diagnostic['fields'] = {key: {'present': key in credential, 'type': type(credential.get(key)).__name__}
                            for key in fields + ['start_time', 'expired_time']}
    if any(not isinstance(credential.get(key), str) or not credential[key] for key in fields):
        raise ImaError('COS_CREDENTIAL_INVALID', diagnostic=diagnostic)
    if not re.fullmatch(r'[a-z0-9-]+-\d+', credential['bucket_name']) or not re.fullmatch(r'[a-z0-9-]+', credential['region']):
        raise ImaError('COS_CREDENTIAL_INVALID', diagnostic=diagnostic)
    times = []
    for key in ['start_time', 'expired_time']:
        value = credential.get(key)
        if isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value):
            value = int(value)
        valid = type(value) is int and 946684800 <= value < 10000000000
        diagnostic['fields'][key]['unit'] = 'seconds' if valid else 'unsupported_or_invalid'
        if not valid:
            raise ImaError('COS_CREDENTIAL_INVALID', diagnostic=diagnostic)
        times.append(value)
    start, expiry = times
    current = time.time()
    # A freshly issued credential can lead this clock by a fraction of a second.
    # Wait locally within a fixed bound, then enforce the real validity interval.
    until_start = start - current
    if expiry > start and 0 < until_start <= 2:
        time.sleep(until_start + 0.01)
        diagnostic['waitedForStartSeconds'] = round(until_start + 0.01, 3)
        current = time.time()
    diagnostic['validity'] = {'expiryAfterStart': expiry > start, 'started': start <= current,
                              'unexpired': current < expiry,
                              'startRelativeSeconds': round(start - current, 3),
                              'expiryRelativeSeconds': round(expiry - current, 3)}
    if expiry <= start:
        raise ImaError('COS_CREDENTIAL_INVALID', diagnostic=diagnostic)
    if current >= expiry:
        raise ImaError('COS_CREDENTIAL_EXPIRED', diagnostic=diagnostic)
    if current < start:
        raise ImaError('COS_CREDENTIAL_NOT_YET_VALID', diagnostic=diagnostic)
    host = credential['bucket_name'] + '.cos.' + credential['region'] + '.myqcloud.com'
    key = credential['cos_key']
    if key.startswith('/') or any(part in ('.', '..') for part in key.split('/')) or re.search(r'[\x00-\x1f]', key):
        raise ImaError('COS_CREDENTIAL_INVALID', diagnostic=diagnostic)
    pathname = '/' + quote(key, safe='/-_.~')
    sign_headers = {'content-length': str(len(payload)), 'host': host}
    names = sorted(sign_headers)
    encoded = '&'.join(name + '=' + quote(sign_headers[name], safe='-_.~') for name in names)
    http_string = 'put\n' + pathname + '\n\n' + encoded + '\n'
    key_time = str(start) + ';' + str(expiry)
    sign_key = hmac.new(credential['secret_key'].encode(), key_time.encode(), hashlib.sha1).hexdigest()
    to_sign = 'sha1\n' + key_time + '\n' + hashlib.sha1(http_string.encode()).hexdigest() + '\n'
    signature = hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()
    auth = ('q-sign-algorithm=sha1&q-ak=' + quote(credential['secret_id'], safe='') + '&q-sign-time=' + key_time
            + '&q-key-time=' + key_time + '&q-header-list=' + ';'.join(names) + '&q-url-param-list=&q-signature=' + signature)
    session = session or new_session()
    diagnostic.update(stage='cos_upload', uploadRequestSent=True)
    if on_send:
        on_send(diagnostic)
    try:
        response = session.put('https://' + host + pathname, data=payload,
                               headers={'Content-Type': content_type, 'Authorization': auth,
                                        'x-cos-security-token': credential['token']},
                               timeout=(8, 30), allow_redirects=False)
        try:
            diagnostic['httpStatus'] = response.status_code
            if response.status_code not in (200, 201, 204):
                code = ('COS_UPLOAD_REJECTED' if response.status_code in (401, 403)
                        else 'COS_UPLOAD_FAILED' if 400 <= response.status_code < 500 else 'COS_UPLOAD_UNKNOWN')
                raise ImaError(code, diagnostic=diagnostic)
            return diagnostic
        finally:
            response.close()
    except requests.RequestException:
        raise ImaError('COS_UPLOAD_UNKNOWN', diagnostic=diagnostic) from None


class ImaClient:
    def __init__(self, config, session=None):
        self._credentials = credentials(config)
        self.session = session or new_session()

    def _request(self, operation, body, *, mutation=False):
        allowed = PROBE_WRITES if mutation else READS
        if operation not in allowed:
            raise ImaError('INVALID_REQUEST')
        self.last_diagnostic = {'stage': operation, 'httpStatus': None}
        headers = {'ima-openapi-clientid': self._credentials['clientId'],
                   'ima-openapi-apikey': self._credentials['apiKey'], 'Content-Type': 'application/json'}
        try:
            response = self.session.post(BASE + operation, json=body, headers=headers,
                                         timeout=(8, 30), allow_redirects=False, stream=True)
            try:
                status = response.status_code
                self.last_diagnostic['httpStatus'] = status
                if status == 401:
                    raise ImaError('AUTH_REQUIRED')
                if status == 403:
                    raise ImaError('FORBIDDEN')
                if status == 429:
                    raise ImaError('RATE_LIMITED')
                if status != 200:
                    raise ImaError('OUTCOME_UNKNOWN' if mutation else 'UPSTREAM_ERROR')
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > 8 * 1024 * 1024:
                        raise ImaError('OUTCOME_UNKNOWN' if mutation else 'INVALID_RESPONSE')
                    chunks.append(chunk)
                value = json.loads(b''.join(chunks))
            finally:
                response.close()
        except requests.RequestException:
            raise ImaError('OUTCOME_UNKNOWN' if mutation else 'NETWORK_ERROR') from None
        except (ValueError, TypeError):
            raise ImaError('OUTCOME_UNKNOWN' if mutation else 'INVALID_RESPONSE') from None
        if not isinstance(value, dict) or type(value.get('code')) is not int:
            raise ImaError('OUTCOME_UNKNOWN' if mutation else 'INVALID_RESPONSE')
        if value['code'] != 0:
            self.last_diagnostic['upstreamCode'] = value['code']
            code = {110001: 'INVALID_PARAMETER', 110012: 'NOT_OPEN', 110021: 'RATE_LIMITED',
                    110030: 'FORBIDDEN'}.get(value['code'], 'UPSTREAM_ERROR')
            raise ImaError(code, value['code'])
        if not isinstance(value.get('data'), dict):
            raise ImaError('OUTCOME_UNKNOWN' if mutation else 'INVALID_RESPONSE')
        return value['data']

    def _pages(self, operation, body, field):
        rows, cursor, seen = [], '', set()
        for _ in range(1000):
            data = self._request(operation, {**body, 'cursor': cursor})
            page = data.get(field)
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise ImaError('INVALID_RESPONSE')
            rows.extend(page)
            if len(rows) > 50000:
                raise ImaError('INCOMPLETE_LIST')
            # Observed search responses can be a single result set without paging
            # markers. Do not extend this exception to directories or later pages.
            if operation == 'search_knowledge' and not cursor and 'is_end' not in data and 'next_cursor' not in data:
                return rows
            if type(data.get('is_end')) is not bool:
                raise ImaError('INVALID_RESPONSE')
            if data['is_end']:
                return rows
            cursor = data.get('next_cursor')
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ImaError('INCOMPLETE_LIST')
            seen.add(cursor)
        raise ImaError('INCOMPLETE_LIST')

    def libraries(self):
        rows = self._pages('get_addable_knowledge_base_list', {'limit': 50}, 'addable_knowledge_base_list')
        return [{'libraryId': identifier(row.get('id')), 'name': str(row.get('name') or '未命名知识库')}
                for row in rows]

    @staticmethod
    def entry(row, parent):
        media_id = row.get('media_id')
        folder_id = row.get('folder_id')
        folder = bool(folder_id) or row.get('media_type') == 99
        object_id = folder_id if folder_id else media_id
        if object_id is not None:
            identifier(object_id)
        return {'kind': 'folder' if folder else 'file' if media_id else 'unknown',
                'folderId': object_id if folder else None, 'mediaId': media_id,
                'name': str(row.get('name') or row.get('title') or '未命名条目'),
                'parentFolderId': row.get('parent_folder_id') or parent,
                'mediaType': row.get('media_type'), 'unrecognized': object_id is None}

    def browse(self, library_id, folder_id=None):
        library_id = identifier(library_id)
        parent = identifier(folder_id) if folder_id is not None else library_id
        body = {'knowledge_base_id': library_id, 'limit': 50}
        if folder_id is not None:
            body['folder_id'] = parent
        rows = self._pages('get_knowledge_list', body, 'knowledge_list')
        return [self.entry(row, parent) for row in rows]

    def search(self, library_id, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ImaError('INVALID_REQUEST')
        rows = self._pages('search_knowledge', {'knowledge_base_id': identifier(library_id), 'query': query}, 'info_list')
        return [self.entry(row, None) for row in rows]

    def tree(self, library_id, folder_id=None):
        root = identifier(library_id if folder_id is None else folder_id)
        queue, seen, result = [(root, '')], set(), []
        while queue:
            parent, prefix = queue.pop(0)
            if parent in seen or len(seen) >= 2000:
                raise ImaError('INCOMPLETE_LIST')
            seen.add(parent)
            for row in self.browse(library_id, parent):
                row['path'] = prefix + '/' + row['name']
                result.append(row)
                if len(result) > 50000:
                    raise ImaError('INCOMPLETE_LIST')
                if row['kind'] == 'folder':
                    queue.append((row['folderId'], row['path']))
        return result

    def media_access(self, media_id, *, probe=False):
        data = self._request('get_media_info', {'media_id': identifier(media_id)})
        info = data.get('url_info')
        result = {'mediaType': data.get('media_type'), 'urlProvided': bool(isinstance(info, dict) and info.get('url')),
                  'readVerified': False, 'requiresImaClient': True}
        if not result['urlProvided'] or not probe:
            return result
        try:
            url = info['url']
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
                return result
            # Private storage signed URLs stay in memory. Never proxy a caller URL.
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
                return result
            if not (parsed.hostname == 'ima.qq.com' or parsed.hostname.endswith('.myqcloud.com')):
                return result
            headers = info.get('headers') or {}
            if not isinstance(headers, dict) or any(key.lower() not in ('authorization', 'x-cos-security-token', 'accept') for key in headers):
                return result
            response = self.session.get(url, headers={**headers, 'Range': 'bytes=0-4095'},
                                        timeout=(8, 20), allow_redirects=False, stream=True)
            try:
                if response.status_code in (200, 206):
                    next(response.iter_content(4096), b'')
                    result.update(readVerified=True, requiresImaClient=False)
            finally:
                response.close()
        except (requests.RequestException, ValueError, OSError, TypeError):
            pass
        return result


class ImaService:
    def __init__(self, path=CONFIG, client_factory=ImaClient):
        self.path = path
        self.client_factory = client_factory

    def status(self):
        value = load_config(self.path)
        return {'configured': value is not None, 'lastVerifiedAt': value.get('verifiedAt') if value else None,
                'authentication': 'official_client_id_api_key', 'currentLoginVerified': False}

    def client(self):
        value = load_config(self.path)
        if value is None:
            raise ImaError('NOT_CONFIGURED')
        return self.client_factory(value)

    def configure(self, client_id, api_key):
        value = {**credentials({'clientId': client_id, 'apiKey': api_key}), 'version': 1}
        libraries = self.client_factory(value).libraries()
        value['verifiedAt'] = now()
        try:
            save_encrypted_json(self.path, value)
        except OSError:
            raise ImaError('INVALID_CONFIG') from None
        return {'verified': True, 'libraryCount': len(libraries), 'verifiedAt': value['verifiedAt']}


def envelope(data=None, error=None):
    return {'apiVersion': '1', 'data': data, 'meta': {'generatedAt': now(), 'source': 'https://ima.qq.com'}, 'error': error}


def discard_small_body(handler):
    """Consume a bounded rejected POST so Windows does not reset the response."""
    length = handler.headers.get('Content-Length', '')
    if handler.command == 'POST' and not handler.headers.get('Transfer-Encoding') and length.isdecimal() and int(length) <= 131072:
        try:
            handler.connection.settimeout(2)
            handler.rfile.read(int(length))
        except OSError:
            pass


def handle_request(handler, service, token):
    prefix = '/api/ima/v1/'
    if not handler.path.startswith(prefix):
        return False
    if not handler.desktop_request() or not secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), token):
        discard_small_body(handler)
        handler.json_response(403, envelope(error={'code': 'FORBIDDEN', 'message': '本机鉴权失败'}))
        return True
    try:
        route = handler.path[len(prefix):]
        if route == 'status' and handler.command == 'GET':
            result = service.status()
        elif handler.command == 'POST' and route in ('libraries', 'browse', 'search', 'media-access'):
            length = handler.headers.get('Content-Length', '0')
            if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or not 2 <= int(length) <= 8192:
                raise ImaError('INVALID_REQUEST')
            handler.connection.settimeout(5)
            body = json.loads(handler.rfile.read(int(length)))
            expected = {'libraries': set(), 'browse': {'libraryId', 'folderId'}, 'search': {'libraryId', 'query'}, 'media-access': {'mediaId'}}[route]
            if not isinstance(body, dict) or set(body) != expected:
                raise ImaError('INVALID_REQUEST')
            client = service.client()
            if route == 'libraries':
                result = {'libraries': client.libraries(), 'complete': True, 'verifiedAt': now()}
            elif route == 'browse':
                result = {'entries': client.browse(body['libraryId'], body['folderId']), 'complete': True}
            elif route == 'search':
                result = {'entries': client.search(body['libraryId'], body['query']), 'complete': True}
            else:
                result = client.media_access(body['mediaId'], probe=True)
        else:
            discard_small_body(handler)
            handler.json_response(405, envelope(error={'code': 'METHOD_NOT_ALLOWED', 'message': '本阶段只提供固定只读入口'}))
            return True
        handler.json_response(200, envelope(result))
    except ImaError as error:
        status = 429 if error.code == 'RATE_LIMITED' else 409 if error.code in ('NOT_CONFIGURED', 'AUTH_REQUIRED') else 400 if error.code == 'INVALID_REQUEST' else 502
        handler.json_response(status, envelope(error={'code': error.code, 'message': str(error), 'upstreamCode': error.upstream_code}))
    except Exception:
        handler.json_response(503, envelope(error={'code': 'LOCAL_ERROR', 'message': 'ima本机读取失败，请核对配置工具；原数据已保留'}))
    return True
