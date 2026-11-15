"""Read-only Canvas integration for the local workbench."""
from __future__ import annotations

import argparse
from school_connection_audit import record as record_school_connection
import base64
import copy
import hashlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import unicodedata
import time
import urllib.parse

from bs4 import BeautifulSoup
import requests

from gmail_bridge import protect
from school_storage import private_root, read_path

BASE = 'https://canvas.shufe.edu.cn'
PRIVATE = DEFAULT_PRIVATE = private_root('Canvas')
from workbench_config import DATA_DIR
LEGACY_PRIVATE = DATA_DIR / 'legacy' / 'Canvas'
TZ = dt.timezone(dt.timedelta(hours=8))
SESSION_NAMES = {'_normandy_session', '_legacy_normandy_session'}
REFRESH_INTERVAL = 1800
COOLDOWN = 60


class CanvasError(Exception):
    code = 'UPSTREAM_ERROR'
    status = 502


class LoginRequired(CanvasError):
    code = 'LOGIN_REQUIRED'
    status = 409


class InvalidSession(CanvasError):
    code = 'INVALID_SESSION'
    status = 400


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def save_private(name, value):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / name
    temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        temp.write_bytes(protect(json.dumps(value, ensure_ascii=False).encode('utf-8')))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_private(name):
    path = read_path(PRIVATE, DEFAULT_PRIVATE, LEGACY_PRIVATE, name)
    return json.loads(protect(path.read_bytes(), decrypt=True)) if path.exists() else None


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {'cookies'}:
        raise InvalidSession('Canvas 会话格式无效，请重新连接')
    cookies = bundle['cookies']
    if not isinstance(cookies, list) or not 1 <= len(cookies) <= 2:
        raise InvalidSession('未取得 Canvas 登录会话，请先在 Edge 打开 Canvas')
    for cookie in cookies:
        if (not isinstance(cookie, dict)
                or cookie.get('name') not in SESSION_NAMES
                or not isinstance(cookie.get('domain'), str)
                or cookie.get('domain', '').lstrip('.') != 'canvas.shufe.edu.cn'
                or cookie.get('path', '/') != '/'
                or not isinstance(cookie.get('value'), str) or not cookie['value']):
            raise InvalidSession('仅接受 Canvas 域名的登录会话')
    return copy.deepcopy(bundle)


def make_session(bundle):
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'Accept': 'application/json', 'User-Agent': 'PersonalWorkbench-Canvas/1.0'})
    for cookie in bundle['cookies']:
        session.cookies.set(cookie['name'], cookie['value'], domain='canvas.shufe.edu.cn', path='/', secure=True)
    return session


def read_collection(session, path, params=None):
    """Follow Canvas pagination, restricted to the original read-only resource."""
    if not re.fullmatch(r'/api/v1/courses(?:/\d+/(?:assignments|files|folders))?', path):
        raise CanvasError('不支持的 Canvas 查询地址')
    url = BASE + path
    rows = []
    seen = set()
    for _ in range(50):
        if url in seen:
            raise CanvasError('Canvas 分页重复，已停止同步')
        seen.add(url)
        try:
            response = session.get(url, params=params, timeout=25, allow_redirects=False)
        except requests.RequestException:
            raise CanvasError('Canvas 连接暂时失败，已保留上次结果') from None
        params = None
        if response.is_redirect or response.status_code == 401:
            raise LoginRequired('Canvas 登录已失效，请从上财教学网进入 Canvas 后重新连接')
        if response.status_code == 403:
            raise CanvasError('Canvas 拒绝读取课程内容，请在网页核对访问权限')
        if response.status_code != 200 or len(response.content) > 10_000_000:
            raise CanvasError('Canvas 返回异常，已保留上次结果')
        try:
            text = response.content.decode('utf-8-sig')
            if text.startswith('while(1);'):
                text = text[len('while(1);'):]
            page = json.loads(text)
        except (UnicodeError, ValueError):
            raise CanvasError('Canvas 返回格式变化，请检查登录状态') from None
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise CanvasError('Canvas 数据结构变化，已停止同步')
        rows.extend(page)
        next_url = response.links.get('next', {}).get('url')
        if not next_url:
            return rows
        parsed = urllib.parse.urlsplit(next_url)
        if (parsed.scheme != 'https' or parsed.netloc != 'canvas.shufe.edu.cn'
                or parsed.path != path or parsed.fragment or len(next_url) > 4096):
            raise CanvasError('Canvas 分页地址异常，已停止同步')
        url = next_url
    raise CanvasError('Canvas 数据超过单次同步上限，已保留上次结果')


def timestamp(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('missing timezone')
        return parsed.astimezone(TZ).isoformat(timespec='seconds')
    except (ValueError, AttributeError):
        raise CanvasError('Canvas 时间格式变化，已停止同步') from None


def text_content(markup):
    soup = BeautifulSoup(markup or '', 'html.parser')
    for element in soup(['script', 'style']):
        element.decompose()
    return soup.get_text('\n', strip=True)


def assignment_file_ids(assignment, course_id):
    """Use only official file identities in the teacher's assignment description."""
    soup = BeautifulSoup(assignment.get('description') or '', 'html.parser')
    result = []
    for link in soup.select('[href], [data-api-endpoint]'):
        for attribute in ('data-api-endpoint', 'href'):
            value = link.get(attribute)
            if not isinstance(value, str):
                continue
            try:
                parsed = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, value))
            except ValueError:
                continue
            if parsed.scheme != 'https' or parsed.netloc != 'canvas.shufe.edu.cn' or parsed.username or parsed.password:
                continue
            match = re.fullmatch(r'/(?:api/v1/)?(?:courses/([0-9]{1,15})/)?files/([0-9]{1,15})(?:/(?:download|preview))?/?', parsed.path)
            if match and (not match[1] or str(int(match[1])) == course_id):
                file_id = str(int(match[2]))
                if file_id not in result:
                    result.append(file_id)
    return result


def read_file(session, course_id, file_id):
    if not all(re.fullmatch(r'[0-9]{1,15}', value) for value in (course_id, file_id)):
        raise CanvasError('Canvas 附件编号格式异常')
    try:
        response = session.get(f'{BASE}/api/v1/courses/{course_id}/files/{file_id}', timeout=25, allow_redirects=False)
        if response.is_redirect or response.status_code == 401:
            raise LoginRequired('Canvas 附件需要重新登录，已保留上次资料')
        if response.status_code != 200 or len(response.content) > 1_000_000:
            error = CanvasError('作业附件元数据读取未完成，请核对附件访问情况')
            error.http_status = response.status_code
            raise error
        text = response.content.decode('utf-8-sig').removeprefix('while(1);')
        row = json.loads(text)
        if not isinstance(row, dict) or str(row.get('id')) != file_id or not (row.get('display_name') or row.get('filename')):
            raise ValueError()
        return row
    except (requests.RequestException, UnicodeError, ValueError):
        raise CanvasError('作业附件元数据读取异常，已保留上次资料') from None


MATERIAL_VERSION = 2


def material_name(row):
    return row.get('display_name') or row.get('filename') or row.get('name') or ''


def material_group_id(course_id, name):
    identity = str(course_id) + '\0' + unicodedata.normalize('NFKC', name).strip().casefold()
    return 'group-' + hashlib.sha256(identity.encode()).hexdigest()[:32]


def file_locked(row, at=None):
    if row.get('locked_for_user', row.get('locked', False)):
        return True
    at = at or dt.datetime.now(TZ)
    unlock = timestamp(row.get('unlock_at', row.get('unlockAt')))
    lock = timestamp(row.get('lock_at', row.get('lockAt')))
    return bool(unlock and dt.datetime.fromisoformat(unlock) > at
                or lock and dt.datetime.fromisoformat(lock) <= at)


def group_materials(files, course_id=None):
    """Keep original identities while presenting one course-scoped complete name."""
    groups = {}
    for file in files:
        for member in file.get('members', [file]):
            course = str(course_id or member['courseId'])
            name = unicodedata.normalize('NFKC', material_name(member)).strip().casefold()
            if not name or name == '作业附件（信息暂不可读取）':
                name = 'unreadable-id:' + str(member['id'])
            identity = course + '\0' + name
            members = groups.setdefault(identity, {})
            members[str(member['id'])] = member
    result = []
    for identity, by_id in groups.items():
        members = sorted(by_id.values(), key=lambda row: (
            bool(row.get('metadataUnavailable')), file_locked(row),
            not isinstance(row.get('size'), int) or row['size'] <= 0, int(row['id'])))
        representative = members[0]
        sources = []
        for member in members:
            for entry in member.get('sourceEntries', member.get('sources', [])):
                if entry not in sources:
                    sources.append(entry)
        course, name = identity.split('\0', 1)
        grouped = {**representative, 'groupId': material_group_id(course, name),
                   'memberIds': sorted(by_id, key=int), 'members': members,
                   'sourceEntries': sources, 'sources': sources, 'materialVersion': MATERIAL_VERSION}
        result.append(grouped)
    return result


def course_materials(session, course_id, assignments, files, previous_files=()):
    """Collect teacher file IDs from both entry points, then group complete names."""
    result = {}
    previous = {str(member['id']): member for row in previous_files if row.get('courseId') == course_id
                for member in row.get('members', [row])}
    for file in files:
        file_id = str(file['id'])
        if not re.fullmatch(r'[0-9]{1,15}', file_id):
            raise CanvasError('Canvas 文件编号格式异常')
        result.setdefault(file_id, {**file, 'sourceEntries': [{'kind': 'course_files'}]})
    for assignment in assignments:
        assignment_id = str(assignment['id'])
        if not re.fullmatch(r'[0-9]{1,15}', assignment_id):
            raise CanvasError('Canvas 作业编号格式异常')
        for file_id in assignment_file_ids(assignment, course_id):
            if file_id not in result:
                try:
                    metadata = read_file(session, course_id, file_id)
                except LoginRequired:
                    raise
                except CanvasError as error:
                    saved = previous.get(file_id, {})
                    metadata = {'id': file_id, 'filename': saved.get('name') or '作业附件（信息暂不可读取）',
                                'size': saved.get('size'), 'content-type': saved.get('contentType', ''),
                                'created_at': saved.get('createdAt'), 'updated_at': saved.get('updatedAt'),
                                'modified_at': saved.get('modifiedAt'), 'locked_for_user': True,
                                'metadataUnavailable': True, 'metadataHttpStatus': getattr(error, 'http_status', None),
                                'lastMetadataSuccessAt': saved.get('lastMetadataSuccessAt'), 'preservedFolder': saved.get('folder', '')}
                result[file_id] = {**metadata, 'sourceEntries': []}
            source = {'kind': 'assignment', 'assignmentId': assignment_id, 'name': assignment.get('name') or '课程作业',
                      'url': f'{BASE}/courses/{course_id}/assignments/{assignment_id}'}
            if source not in result[file_id]['sourceEntries']:
                result[file_id]['sourceEntries'].append(source)
    return group_materials(list(result.values()), course_id)


def normalize_assignment(row, course):
    submission = row.get('submission') or {}
    return {
        'id': str(row['id']), 'courseId': course['id'], 'courseName': course['name'],
        'name': row['name'], 'description': text_content(row.get('description')),
        'url': f"{BASE}/courses/{course['id']}/assignments/{row['id']}",
        'dueAt': timestamp(row.get('due_at')), 'unlockAt': timestamp(row.get('unlock_at')),
        'lockAt': timestamp(row.get('lock_at')), 'updatedAt': timestamp(row.get('updated_at')),
        'submissionTypes': row.get('submission_types', []), 'locked': bool(row.get('locked_for_user')),
        'submission': {
            'state': submission.get('workflow_state', 'unknown'),
            'submittedAt': timestamp(submission.get('submitted_at')), 'attempt': submission.get('attempt'),
            'late': bool(submission.get('late')), 'missing': bool(submission.get('missing')),
            'excused': bool(submission.get('excused')),
        },
    }


def assignment_status(assignment, at=None):
    submission = assignment['submission']
    state = submission['state']
    if submission['excused']:
        return 'excused'
    if state in ('graded', 'pending_review'):
        return state
    if state == 'submitted' or submission['submittedAt']:
        return 'submitted'
    types = assignment['submissionTypes']
    if 'on_paper' in types:
        return 'offline'
    if not types or set(types) <= {'none', 'not_graded'}:
        return 'not_required'
    if state == 'unknown':
        return 'unknown'
    due = assignment['dueAt']
    if submission['missing'] or (due and dt.datetime.fromisoformat(due) < (at or dt.datetime.now(TZ))):
        return 'overdue'
    return 'unsubmitted'


def normalize_file(row, course, folders):
    folder = folders.get(str(row.get('folder_id')), row.get('preservedFolder', ''))
    result = {
        'id': str(row['id']), 'courseId': course['id'], 'courseName': course['name'],
        'name': row.get('display_name') or row['filename'], 'folder': folder,
        'size': row.get('size', 0), 'contentType': row.get('content-type', ''),
        'createdAt': timestamp(row.get('created_at')), 'updatedAt': timestamp(row.get('updated_at')),
        'modifiedAt': timestamp(row.get('modified_at')),
        'locked': file_locked(row), 'hidden': bool(row.get('hidden_for_user')),
        'unlockAt': timestamp(row.get('unlock_at')), 'lockAt': timestamp(row.get('lock_at')),
        'url': f"{BASE}/courses/{course['id']}/files/{row['id']}?wrap=1",
        'sources': row.get('sourceEntries', [{'kind': 'course_files'}]),
        'metadataUnavailable': bool(row.get('metadataUnavailable')),
        'metadataHttpStatus': row.get('metadataHttpStatus'),
        'lastMetadataSuccessAt': row.get('lastMetadataSuccessAt') if row.get('metadataUnavailable') else now(),
    }
    if row.get('groupId'):
        result.update(groupId=row['groupId'], memberIds=row['memberIds'], materialVersion=MATERIAL_VERSION,
                      members=[normalize_file(member, course, folders) for member in row['members']])
    return result


def fetch_all(bundle, previous=None):
    courses, assignments, files = [], [], []
    with make_session(bundle) as session:
        course_rows = read_collection(session, '/api/v1/courses', {
            'enrollment_state': 'active', 'enrollment_type': 'student', 'include[]': 'term', 'per_page': 100,
        })
        if len(course_rows) > 100:
            raise CanvasError('Canvas 课程数量超过单次同步上限')
        for row in course_rows:
            course_id = str(row['id'])
            if not course_id.isdecimal():
                raise CanvasError('Canvas 课程编号格式异常')
            course = {'id': course_id, 'name': row['name'], 'code': row.get('course_code', ''),
                      'term': (row.get('term') or {}).get('name', ''), 'url': f'{BASE}/courses/{course_id}'}
            prefix = f'/api/v1/courses/{course_id}'
            course_assignments = read_collection(session, prefix + '/assignments', {'include[]': 'submission', 'per_page': 100})
            course_files = read_collection(session, prefix + '/files', {'per_page': 100})
            course_files = course_materials(session, course_id, course_assignments, course_files, (previous or {}).get('files', []))
            course_folders = read_collection(session, prefix + '/folders', {'per_page': 100})
            folders = {str(folder['id']): folder.get('full_name', '').partition('/')[2] for folder in course_folders}
            assignments.extend(normalize_assignment(item, course) for item in course_assignments)
            files.extend(normalize_file(item, course, folders) for item in course_files)
            course.update({'assignmentCount': len(course_assignments), 'fileCount': len(course_files)})
            courses.append(course)
        rotated_by_name = {c.name: {'name': c.name, 'value': c.value, 'domain': 'canvas.shufe.edu.cn', 'path': '/', 'secure': True}
                           for c in session.cookies if c.name in SESSION_NAMES and c.domain.lstrip('.') == 'canvas.shufe.edu.cn'}
    data = {'available': True, 'stale': False, 'needsLogin': False, 'errorCode': None,
            'message': '已同步 Canvas 课程资料与作业安排', 'updatedAt': now(),
            'courses': courses, 'assignments': assignments, 'files': files}
    data['materialVersion'] = MATERIAL_VERSION
    data['sourceFiles'] = [member for file in files for member in file.get('members', [file])]
    data['materialWarningCount'] = sum(file['metadataUnavailable'] for file in files)
    if data['materialWarningCount']:
        data['message'] += f"，{data['materialWarningCount']} 份作业附件信息暂不可读，已保留已有信息及入口"
    return data, validate_bundle({'cookies': list(rotated_by_name.values())})


def envelope(data=None, snapshot=None, error=None):
    meta = None
    if snapshot is not None:
        meta = {key: snapshot.get(key) for key in (
            'available', 'stale', 'needsLogin', 'refreshing', 'errorCode', 'message',
            'updatedAt', 'checkedAt', 'lastAttemptAt', 'materialWarningCount',
        )}
        meta.update({'generatedAt': now(), 'timezone': 'Asia/Shanghai', 'source': BASE,
                     'refreshIntervalSeconds': REFRESH_INTERVAL, 'refreshCooldownSeconds': COOLDOWN})
    return {'apiVersion': '1', 'data': data, 'meta': meta, 'error': error}


class CanvasService:
    def __init__(self):
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.refreshing = False
        self.last_attempt = 0
        self.state = {'available': False, 'stale': False, 'needsLogin': True,
                      'errorCode': 'LOGIN_REQUIRED', 'message': '请连接已登录的 Canvas 会话',
                      'courses': [], 'assignments': [], 'files': []}
        try:
            cached = load_private('connection.dpapi')
            if cached:
                self.state = {**cached['snapshot'], 'stale': True, 'message': '正在核验 Canvas 连接，当前为上次数据'}
        except Exception:
            self.state['message'] = '本机 Canvas 数据无法读取，请重新连接'

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.state)
            data['refreshing'] = self.refreshing
        # Migrate presentation in memory, preserving cached source time and original IDs.
        if data.get('materialVersion') != MATERIAL_VERSION:
            data['sourceFiles'] = data['files']
            data['files'] = group_materials(data['files'])
            data['materialVersion'] = MATERIAL_VERSION
            for course in data['courses']:
                course['fileCount'] = sum(file['courseId'] == course['id'] for file in data['files'])
        for assignment in data['assignments']:
            assignment['status'] = assignment_status(assignment)
        data['counts'] = {'courses': len(data['courses']), 'assignments': len(data['assignments']),
                          'files': len(data['files']),
                          'pending': sum(a['status'] in ('unsubmitted', 'overdue') for a in data['assignments'])}
        return data

    def api_snapshot(self, resource='snapshot'):
        snapshot = self.snapshot()
        fields = {'snapshot': ('courses', 'assignments', 'files', 'sourceFiles', 'materialVersion', 'counts'), 'status': ('counts',),
                  'courses': ('courses',), 'assignments': ('assignments',), 'files': ('files', 'materialVersion')}
        return envelope({key: snapshot[key] for key in fields[resource]}, snapshot)

    def connect(self, bundle):
        bundle = validate_bundle(bundle)
        with self.sync_lock:
            attempted = now()
            data, bundle = fetch_all(bundle, self.snapshot())
            data.update({'checkedAt': now(), 'lastAttemptAt': attempted})
            # Session and snapshot are committed together in one encrypted atomic replacement.
            save_private('connection.dpapi', {'bundle': bundle, 'snapshot': data})
            with self.lock:
                self.state = data
        return {'connected': True, **self.snapshot()['counts'], 'updatedAt': data['updatedAt']}

    def connect_encrypted(self, encrypted):
        try:
            bundle = json.loads(protect(encrypted, decrypt=True))
        except Exception:
            raise InvalidSession('无法解密 Canvas 会话，请使用当前 Windows 用户重新连接') from None
        return self.connect(bundle)

    def request_refresh(self):
        with self.lock:
            if self.refreshing:
                return {'accepted': False, 'reason': 'already_running', 'retryAfterSeconds': 0}
            remaining = COOLDOWN - (time.monotonic() - self.last_attempt)
            if self.last_attempt and remaining > 0:
                return {'accepted': False, 'reason': 'cooldown', 'retryAfterSeconds': math.ceil(remaining)}
            self.refreshing = True
            self.last_attempt = time.monotonic()
            self.state['lastAttemptAt'] = now()
        threading.Thread(target=self._refresh, daemon=True).start()
        return {'accepted': True, 'reason': 'started', 'retryAfterSeconds': 0}

    def _refresh(self):
        with self.sync_lock:
            try:
                connection = load_private('connection.dpapi')
                if not connection:
                    raise LoginRequired('请先从教学网进入 Canvas，再连接工作台')
                data, bundle = fetch_all(validate_bundle(connection['bundle']), connection.get('snapshot'))
                data.update({'checkedAt': now(), 'lastAttemptAt': self.state.get('lastAttemptAt')})
                save_private('connection.dpapi', {'bundle': bundle, 'snapshot': data})
                with self.lock:
                    self.state = data
            except Exception as error:
                with self.lock:
                    self.state.update({
                        'stale': bool(self.state['available']), 'needsLogin': isinstance(error, LoginRequired),
                        'errorCode': error.code if isinstance(error, CanvasError) else 'SYNC_FAILED',
                        'message': str(error) if isinstance(error, CanvasError) else 'Canvas 同步失败，已保留上次数据',
                        'checkedAt': now(),
                    })
            finally:
                with self.lock:
                    self.refreshing = False

    def start(self):
        def worker():
            while True:
                if not self.snapshot()['needsLogin'] or (read_path(PRIVATE, DEFAULT_PRIVATE, LEGACY_PRIVATE, 'connection.dpapi').exists() and not self.last_attempt):
                    self.request_refresh()
                time.sleep(REFRESH_INTERVAL)
        threading.Thread(target=worker, daemon=True).start()


def handle_request(handler, service, desktop_token, native_authorize=None):
    prefix = '/api/canvas/v1'
    path = urllib.parse.urlsplit(handler.path).path
    if path != prefix and not path.startswith(prefix + '/'):
        return False

    def fail(status, code, message, headers=None):
        handler.json_response(status, envelope(error={'code': code, 'message': message}), headers)

    if not ((handler.desktop_request() and secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), desktop_token))
            or (native_authorize is not None and native_authorize(handler))):
        fail(403, 'FORBIDDEN', '需要本机工作台来源与有效会话令牌')
        return True
    resource = 'snapshot' if path == prefix else path[len(prefix) + 1:]
    reads = ('snapshot', 'status', 'courses', 'assignments', 'files')
    if resource not in (*reads, 'refresh', 'connect') or path == prefix + '/snapshot':
        fail(404, 'NOT_FOUND', '接口不存在')
        return True
    if handler.path != path:
        fail(400, 'INVALID_REQUEST', '此接口不接受查询参数')
        return True
    expected = 'GET' if resource in reads else 'POST'
    if handler.command != expected:
        fail(405, 'METHOD_NOT_ALLOWED', '此接口需要使用 ' + expected, {'Allow': expected})
        return True
    length = handler.headers.get('Content-Length', '0')
    if handler.headers.get('Transfer-Encoding') or not length.isdecimal():
        fail(400, 'INVALID_REQUEST', '请求长度无效')
        return True
    if resource != 'connect' and int(length):
        fail(400, 'INVALID_REQUEST', '此接口不接受请求体')
        return True
    if resource in reads:
        handler.json_response(200, service.api_snapshot(resource))
        return True
    if resource == 'refresh':
        result = service.request_refresh()
        if result['reason'] == 'cooldown':
            handler.json_response(429, envelope(result, service.snapshot(), {'code': 'REFRESH_COOLDOWN', 'message': '请等待同步冷却时间结束'}),
                                  {'Retry-After': str(result['retryAfterSeconds'])})
        else:
            handler.json_response(202, envelope(result, service.snapshot()))
        return True
    if not 1 <= int(length) <= 131072:
        fail(400, 'INVALID_REQUEST', '加密会话大小无效')
        return True
    if handler.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
        fail(415, 'UNSUPPORTED_MEDIA_TYPE', '请发送 application/octet-stream 加密会话')
        return True
    handler.connection.settimeout(10)
    try:
        encrypted = handler.rfile.read(int(length))
        if len(encrypted) != int(length):
            fail(400, 'INVALID_REQUEST', '加密会话接收不完整')
        else:
            result = service.connect_encrypted(encrypted)
            record_school_connection('canvas', 'session_connect', result, service.snapshot())
            handler.json_response(200, envelope(result, service.snapshot()))
    except CanvasError as error:
        fail(error.status, error.code, str(error))
    except Exception:
        fail(503, 'SYNC_FAILED', 'Canvas 连接失败，请检查本机服务后重试')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['begin-import', 'finish-import', 'check'])
    action = parser.parse_args().action
    if action == 'begin-import':
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        save_private('import-key.dpapi', {'created': time.time(), 'key': key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()})
        public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        print(base64.b64encode(public).decode())
    elif action == 'finish-import':
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        private = load_private('import-key.dpapi')
        if not private or time.time() - private['created'] > 900:
            raise SystemExit('Canvas 会话导入已超时，请重新开始')
        envelope_data = json.loads(sys.stdin.read(200000))
        key = serialization.load_pem_private_key(private['key'].encode(), password=None)
        secret = key.decrypt(base64.b64decode(envelope_data['key']), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        bundle = json.loads(AESGCM(secret).decrypt(base64.b64decode(envelope_data['iv']), base64.b64decode(envelope_data['data']), None))
        result = CanvasService().connect(bundle)
        (PRIVATE / 'import-key.dpapi').unlink()
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(CanvasService().api_snapshot('status'), ensure_ascii=False))


if __name__ == '__main__':
    main()
