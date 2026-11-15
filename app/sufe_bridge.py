"""Read-only SUFE EAMS adapter; session material is protected with Windows DPAPI."""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup
from gmail_bridge import protect
from school_storage import private_root, read_path

BASE = 'https://eams.sufe.edu.cn/eams/'
PORTAL = 'https://portal.sufe.edu.cn/main.html#/Index'
PRIVATE = DEFAULT_PRIVATE = private_root('SUFE')
from workbench_config import DATA_DIR
LEGACY_PRIVATE = DATA_DIR / 'legacy' / 'SUFE'
TZ = dt.timezone(dt.timedelta(hours=8))


class SufeError(Exception):
    code = 'UPSTREAM_ERROR'
    status = 502


class LoginRequired(SufeError):
    code = 'LOGIN_REQUIRED'
    status = 409


class InvalidSession(SufeError):
    code = 'INVALID_SESSION'
    status = 400


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def api_envelope(data, snapshot=None, error=None):
    """The v1 contract is independent of the legacy dashboard response."""
    meta = None
    if snapshot is not None:
        meta = {key: snapshot.get(key) for key in (
            'available', 'stale', 'needsLogin', 'refreshing', 'updatedAt',
            'checkedAt', 'lastAttemptAt', 'errorCode', 'message', 'semester',
        )}
        meta.update({'generatedAt': now(), 'timezone': 'Asia/Shanghai', 'source': BASE,
                     'refreshIntervalSeconds': 1800, 'refreshCooldownSeconds': 60})
    return {'apiVersion': '1', 'data': data, 'meta': meta, 'error': error}


def save_private(name, value):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / name
    encrypted = protect(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temp.open('xb') as output:
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_private(name):
    path = read_path(PRIVATE, DEFAULT_PRIVATE, LEGACY_PRIVATE, name)
    return json.loads(protect(path.read_bytes(), decrypt=True)) if path.exists() else None


def load_connection():
    """Prefer the committed pair; never silently roll back a damaged new store."""
    if read_path(PRIVATE, DEFAULT_PRIVATE, LEGACY_PRIVATE, 'connection.dpapi').exists():
        connection = load_private('connection.dpapi')
        if (not isinstance(connection, dict) or connection.get('storageVersion') != 1
                or not isinstance(connection.get('bundle'), dict)
                or not isinstance(connection['bundle'].get('cookies'), list)
                or not isinstance(connection.get('snapshot'), dict)
                or not all(isinstance(connection['snapshot'].get(key), list)
                           for key in ('courses', 'exams'))):
            raise InvalidSession('本机教务连接文件无效，请重新连接')
        return connection
    # Keep legacy files untouched. Only a successful authenticated read migrates them.
    return {'bundle': load_private('session.dpapi'),
            'snapshot': load_private('snapshot.dpapi')}


def save_connection(bundle, snapshot):
    save_private('connection.dpapi', {'storageVersion': 1, 'bundle': bundle, 'snapshot': snapshot})


def parse_weeks(value):
    weeks = set()
    for parity, start, end in re.findall(r'(单|双)?\[(\d+)(?:-(\d+))?\]', value):
        for week in range(int(start), int(end or start) + 1):
            if not parity or (week % 2 == (1 if parity == '单' else 0)):
                weeks.add(week)
    return sorted(weeks)


def parse_courses(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    table = next((t for t in soup.find_all('table') if all(x in t.get_text() for x in ('课程代码', '上课安排', '课程序号'))), None)
    if not table:
        raise SufeError('课表结构变化或课表未开放，未更新已有课程')
    headers = [c.get_text(' ', strip=True) for c in table.find('tr').find_all(['th', 'td'], recursive=False)]
    courses = []
    for row in table.find_all('tr')[1:]:
        cells = row.find_all(['td', 'th'], recursive=False)
        if len(cells) != len(headers):
            continue
        values = dict(zip(headers, [c.get_text(' ', strip=True) for c in cells]))
        if not re.fullmatch(r'\d+', values.get('课程代码', '')):
            continue
        schedule_cell = cells[headers.index('上课安排')]
        lines = [s.strip() for s in schedule_cell.get_text('\n', strip=True).splitlines() if s.strip()]
        meetings = []
        for line in lines:
            m = re.match(r'^(.*?)\s+星期([一二三四五六日天])\s+(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\s+(.+)$', line)
            if not m:
                raise SufeError('发现无法解析的上课安排，保留旧数据并等待人工检查')
            detail = m.group(5)
            weeks = parse_weeks(detail)
            if not weeks:
                raise SufeError('课表未提供可识别的教学周，等待人工检查')
            room = re.sub(r'(?:单|双)?\[\d+(?:-\d+)?\]', '', detail).strip()
            meetings.append({'teacher': m.group(1), 'weekday': '一二三四五六日'.index(m.group(2).replace('天', '日')) + 1,
                             'start': m.group(3).zfill(5), 'end': m.group(4).zfill(5), 'weeks': weeks, 'room': room, 'raw': line})
        courses.append({'code': values['课程代码'], 'name': values['课程名称'], 'number': values['课程序号'],
                        'category': values.get('课程类别', ''), 'credits': values.get('学分', ''),
                        'teacher': values.get('教师', ''), 'campus': values.get('校区', ''), 'meetings': meetings})
    if not courses:
        raise SufeError('课表为空或结构变化，未覆盖已有课程')
    weeks = []
    for option in soup.select('#startWeek option'):
        dates = re.findall(r'\d{4}-\d{2}-\d{2}', option.get('title', '') + ' ' + option.get_text())
        if len(dates) == 2 and option.get('value', '').isdigit():
            weeks.append({'week': int(option['value']), 'start': dates[0], 'end': dates[1]})
    semester = soup.select_one('select[name="semester.id"] option[selected]')
    year = soup.select_one('#semesterCalendar_year option[selected]')
    return {'courses': courses, 'teachingWeeks': weeks,
            'semester': {'id': semester.get('value') if semester else None,
                         'year': year.get_text(strip=True) if year else '',
                         'term': semester.get_text(strip=True) if semester else ''}}


def parse_exams(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    table = next((t for t in soup.find_all('table') if all(x in t.get_text() for x in ('课程序号', '考试日期', '考试地点'))), None)
    if not table:
        raise SufeError('排考结构变化，未更新已有考试')
    exams = []
    for row in table.find_all('tr')[1:]:
        cells = row.find_all(['td', 'th'], recursive=False)
        if len(cells) < 7:
            continue
        # Selection radio occupies a leading cell; unpublished rows may use colspans.
        values = []
        for cell in cells:
            if cell.find('input', type='radio'):
                continue
            value = cell.get_text(' ', strip=True)
            values.append(value)
            values.extend([''] * (int(cell.get('colspan', 1)) - 1))
        if values and values[0] == '':
            values.pop(0)
        if not values or not re.fullmatch(r'\d{4}', values[0]):
            continue
        if len(values) != 8:
            raise SufeError('排考字段数量变化，等待人工检查')
        number, name, group, date, arrangement, room, status, note = values
        full = ' '.join(values)
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', full)
        times = re.search(r'(\d{1,2}:\d{2})-(\d{1,2}:\d{2})', full)
        published = bool(date_match and times)
        exams.append({'number': number, 'name': name, 'group': group, 'published': published,
                      'date': date_match.group() if date_match else None,
                      'start': times.group(1).zfill(5) if times else None, 'end': times.group(2).zfill(5) if times else None,
                      'room': room if published else '', 'arrangement': arrangement,
                      'status': status, 'note': note, 'message': '' if published else '考试情况尚未发布'})
    if not exams:
        raise SufeError('排考为空或结构变化，未覆盖已有考试')
    return exams


def calendar_view(data, date=None):
    today = date or dt.datetime.now(TZ).date()
    week = next((w['week'] for w in data.get('teachingWeeks', []) if w['start'] <= today.isoformat() <= w['end']), None)
    meetings = []
    if week is not None:
        for course in data.get('courses', []):
            for meeting in course['meetings']:
                if week in meeting['weeks'] and meeting['weekday'] == today.isoweekday():
                    meetings.append({**meeting, 'name': course['name'], 'number': course['number']})
    return {'date': today.isoformat(), 'week': week, 'today': sorted(meetings, key=lambda m: m['start'])}


def make_session(bundle):
    session = requests.Session()
    session.trust_env = False
    session.headers['User-Agent'] = bundle.get('userAgent', 'PersonalWorkbench-SUFE/1.0')
    session.headers['Accept-Language'] = 'zh-CN,zh;q=0.9'
    session.headers['Referer'] = BASE + 'courseTableForStd.action'
    for cookie in bundle.get('cookies', []):
        domain = cookie.get('domain', '').lstrip('.')
        if domain not in ('eams.sufe.edu.cn', 'sufe.edu.cn'):
            continue
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie.get('path', '/'), secure=cookie.get('secure', True))
    return session


def read_page(session, path, data=None):
    # Only fixed read-only EAMS queries. Do not follow SSO redirects or send cookies elsewhere.
    if path not in ('courseTableForStd.action', 'courseTableForStd!courseTable.action', 'stdExamTable!examTable.action'):
        raise SufeError('不支持的学校接口')
    try:
        cookie_rows = list(session.cookies)
    except TypeError:
        cookie_rows = []
    session_values = [cookie.value for cookie in cookie_rows if cookie.name == 'JSESSIONID']
    try:
        response = session.post(BASE + path, data=data, timeout=20, allow_redirects=False) if data else session.get(BASE + path, timeout=20, allow_redirects=False)
    except requests.RequestException:
        raise SufeError('学校连接暂时失败，请稍后再同步') from None
    # Diagnostics must never include redirect URLs/query tickets, response text,
    # request headers or cookies. Fixed categories distinguish SSO from an EAMS
    # internal redirect without leaking the response Location value.
    location = response.headers.get('Location', '')
    location = location if isinstance(location, str) else ''
    destination = 'none'
    if location:
        try:
            parsed = urllib.parse.urlsplit(urllib.parse.urljoin(BASE + path, location))
            if parsed.scheme == 'https' and parsed.hostname == 'eams.sufe.edu.cn':
                destination = {'/eams/courseTableForStd.action': 'eams_bootstrap',
                               '/eams/courseTableForStd!courseTable.action': 'eams_timetable',
                               '/eams/stdExamTable!examTable.action': 'eams_exams',
                               '/eams/sso/login': 'eams_sso_login',
                               '/eams/login.action': 'eams_login'}.get(parsed.path, 'eams_other')
            elif parsed.scheme == 'https' and parsed.hostname == 'login.sufe.edu.cn':
                destination = 'school_authentication'
            elif parsed.scheme == 'https' and parsed.hostname == 'portal.sufe.edu.cn':
                destination = 'school_portal'
            else:
                destination = 'other'
        except (ValueError, TypeError):
            destination = 'invalid'
    status = response.status_code if type(response.status_code) is int else 0
    logging.getLogger(__name__).info('SUFE_READ resource=%s method=%s status=%d redirect=%s',
                                   path, 'POST' if data else 'GET', status, destination)
    prepared = getattr(response, 'request', None)
    outgoing = prepared.headers.get('Cookie', '') if prepared is not None else ''
    outgoing = outgoing if isinstance(outgoing, str) else ''
    sent_sessions = re.findall(r'(?:^|;\s*)JSESSIONID=([^;]*)', outgoing)
    sent_count = sum('=' in part for part in outgoing.split(';'))
    # Only counts and booleans, never cookie names/values or hashes. Keep input
    # values in memory just long enough to verify requests sent them unchanged.
    logging.getLogger(__name__).info(
        'SUFE_COOKIE_CHECK resource=%s input_count=%d input_session=%d sent_count=%d sent_session=%d session_unchanged=%d',
        path, len(cookie_rows), len(session_values), sent_count, len(sent_sessions),
        int(bool(sent_sessions) and all(value in session_values for value in sent_sessions)))
    if response.is_redirect or response.status_code in (401, 403):
        raise LoginRequired('学校登录已失效，请运行影刀登录流程并重新连接')
    if response.status_code != 200:
        raise SufeError('学校暂时无法返回数据，请稍后再同步')
    response.encoding = 'utf-8'
    text = response.text
    if ('type="password"' in text or '上财认证' in text or '统一身份认证' in text) and '课程序号' not in text:
        logging.getLogger(__name__).info('SUFE_READ_REJECT resource=%s reason=login_html', path)
        raise LoginRequired('学校要求重新登录或验证，请在浏览器完成后重新连接')
    if len(text) > 5_000_000:
        raise SufeError('学校响应异常，已停止同步')
    return text


def fetch_all(bundle):
    with make_session(bundle) as session:
        # Bootstrap the current semester through the same form as the normal school UI.
        initial = read_page(session, 'courseTableForStd.action')
        soup = BeautifulSoup(initial, 'html.parser')
        form = soup.find('form')
        params = {}
        if form:
            for field in form.select('input[name], select[name]'):
                if field.name == 'select':
                    option = field.select_one('option[selected]') or field.find('option')
                    if option:
                        params[field['name']] = option.get('value', '')
                else:
                    params[field['name']] = field.get('value', '')
        # EAMS may build the student identifier in JavaScript rather than an input.
        if not params.get('ids'):
            match = re.search(r'(?:name=[\"\']ids[\"\'][^>]*value=[\"\']|[\"\']ids[\"\']\s*,\s*[\"\'])(\d+)', initial)
            if match:
                params['ids'] = match.group(1)
        current_semester = re.search(r'addInput\(form,[\"\']semesterId[\"\'],[\"\'](\d+)[\"\']', initial)
        if current_semester:
            params['semester.id'] = current_semester.group(1)
        # Keep newly discovered parameters when the other field needs a browser fallback.
        for key, value in bundle.get('courseParams', {}).items():
            if not params.get(key):
                params[key] = value
        allowed = ('ignoreHead', 'projectType', 'setting.kind', 'startWeek', 'project.id', 'semester.id', 'ids')
        params = {k: v for k, v in params.items() if k in allowed}
        if not params.get('semester.id') or not params.get('ids'):
            raise SufeError('需要从已登录的教务课表重新连接当前学期')
        result = parse_courses(read_page(session, 'courseTableForStd!courseTable.action', params))
        result['teachingWeeks'] = []
        for option in soup.select('#startWeek option'):
            dates = re.findall(r'\d{4}-\d{2}-\d{2}', option.get('title', ''))
            if len(dates) == 2 and option.get('value', '').isdigit():
                result['teachingWeeks'].append({'week': int(option['value']), 'start': dates[0], 'end': dates[1]})
        if not result['teachingWeeks']:
            raise SufeError('学校未提供教学周日期，已停止更新，避免显示错误日期')
        result['semester']['id'] = params['semester.id']
        result['exams'] = parse_exams(read_page(session, 'stdExamTable!examTable.action', {'semester.id': params['semester.id'], 'examType.id': '1'}))
        result.update({'available': True, 'stale': False, 'needsLogin': False, 'updatedAt': now(), 'source': BASE,
                       'errorCode': None,
                       'message': '已同步学校当前学期课表及期末排考；未发布的考试保持待发布状态'})
        # Preserve server-rotated cookies inside encrypted storage.
        bundle['cookies'] = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path, 'secure': c.secure} for c in session.cookies]
        bundle['courseParams'] = params
        return result, bundle


class SufeService:
    def __init__(self):
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.refreshing = False
        self.state = {'available': False, 'stale': False, 'needsLogin': True, 'errorCode': 'LOGIN_REQUIRED', 'message': '请先连接已登录的上财教务会话', 'courses': [], 'exams': []}
        try:
            cached = load_connection()['snapshot']
            if cached:
                self.state = {**cached, 'stale': True, 'message': '正在核验学校连接，当前为上次同步的数据'}
        except Exception:
            self.state['message'] = '本机学校数据无法读取，请重新连接'
        self.last_attempt = 0

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.state)
            data.update(calendar_view(data))
            data['refreshing'] = self.refreshing
            data['loginUrl'] = PORTAL
            return data

    def request_refresh(self):
        with self.lock:
            if self.refreshing:
                return {'accepted': False, 'reason': 'already_running', 'retryAfterSeconds': 0}
            remaining = 60 - (time.monotonic() - self.last_attempt)
            if self.last_attempt and remaining > 0:
                return {'accepted': False, 'reason': 'cooldown', 'retryAfterSeconds': math.ceil(remaining)}
            self.refreshing = True
            self.last_attempt = time.monotonic()
            self.state['lastAttemptAt'] = now()
        threading.Thread(target=self._refresh, daemon=True).start()
        return {'accepted': True, 'reason': 'started', 'retryAfterSeconds': 0}

    def api_snapshot(self, resource='snapshot'):
        snapshot = self.snapshot()
        fields = {
            'snapshot': ('courses', 'exams', 'teachingWeeks', 'date', 'week', 'today', 'loginUrl'),
            'courses': ('courses',),
            'exams': ('exams',),
            'today': ('date', 'week', 'today'),
        }
        if resource == 'status':
            data = {'courseCount': len(snapshot['courses']), 'examCount': len(snapshot['exams']),
                    'publishedExamCount': sum(e['published'] for e in snapshot['exams']),
                    'loginUrl': snapshot['loginUrl']}
        else:
            data = {key: snapshot.get(key, [] if key == 'teachingWeeks' else None) for key in fields[resource]}
        return api_envelope(data, snapshot)

    def _refresh(self):
        self.sync_lock.acquire()
        try:
            bundle = load_connection()['bundle']
            if not bundle:
                raise LoginRequired('请在浏览器登录上财门户后连接教务会话')
            data, bundle = fetch_all(bundle)
            data.update({'lastAttemptAt': self.state.get('lastAttemptAt'), 'checkedAt': now()})
            save_connection(bundle, data)
            with self.lock:
                self.state = data
        except Exception as error:
            message = str(error) if isinstance(error, SufeError) else '学校数据同步失败，保留上次结果，请检查本机连接'
            with self.lock:
                self.state.update({'stale': bool(self.state.get('available')), 'needsLogin': isinstance(error, LoginRequired),
                                   'errorCode': error.code if isinstance(error, SufeError) else 'SYNC_FAILED',
                                   'message': message, 'checkedAt': now()})
        finally:
            with self.lock:
                self.refreshing = False
            self.sync_lock.release()

    def connect_encrypted(self, encrypted):
        try:
            bundle = json.loads(protect(encrypted, decrypt=True))
        except Exception:
            raise InvalidSession('无法读取本机加密会话，请使用当前 Windows 用户重新连接') from None
        if not isinstance(bundle, dict) or set(bundle) != {'cookies'}:
            raise InvalidSession('教务会话数据格式不正确')
        cookies = bundle['cookies']
        if not isinstance(cookies, list) or not 1 <= len(cookies) <= 30:
            raise InvalidSession('未读取到有效教务会话，请先打开个人课表')
        for cookie in cookies:
            if (not isinstance(cookie, dict)
                    or str(cookie.get('domain', '')).lstrip('.') != 'eams.sufe.edu.cn'
                    or not isinstance(cookie.get('name'), str) or not cookie['name']
                    or not isinstance(cookie.get('value'), str)):
                raise InvalidSession('仅接受上财教务系统域名的会话')
        with self.sync_lock:
            attempted = now()
            data, bundle = fetch_all(bundle)
            data.update({'lastAttemptAt': attempted, 'checkedAt': now()})
            save_connection(bundle, data)
            with self.lock:
                self.state = data
        return {'connected': True, 'courses': len(data['courses']), 'exams': len(data['exams']),
                'publishedExams': sum(e['published'] for e in data['exams']), 'updatedAt': data['updatedAt']}

    def start(self):
        def worker():
            while True:
                if not self.snapshot().get('needsLogin') or (any(read_path(PRIVATE, DEFAULT_PRIVATE, LEGACY_PRIVATE, name).exists() for name in ('connection.dpapi', 'session.dpapi')) and not self.last_attempt):
                    self.request_refresh()
                time.sleep(1800)
        threading.Thread(target=worker, daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['begin-import', 'finish-import', 'check'])
    args = parser.parse_args()
    if args.action == 'begin-import':
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        save_private('import-key.dpapi', {'created': time.time(), 'key': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()})
        pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        print(base64.b64encode(pub).decode())
    elif args.action == 'finish-import':
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        private = load_private('import-key.dpapi')
        if not private or time.time() - private['created'] > 900:
            raise SystemExit('导入已超时，请重新开始')
        envelope = json.load(sys.stdin)
        key = serialization.load_pem_private_key(private['key'].encode(), password=None)
        secret = key.decrypt(base64.b64decode(envelope['key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        bundle = json.loads(AESGCM(secret).decrypt(base64.b64decode(envelope['iv']), base64.b64decode(envelope['data']), None))
        result, bundle = fetch_all(bundle)
        save_connection(bundle, result)
        (PRIVATE / 'import-key.dpapi').unlink()
        print(json.dumps({'connected': True, 'courses': len(result['courses']), 'exams': len(result['exams']), 'publishedExams': sum(e['published'] for e in result['exams']), 'teachingWeeks': len(result['teachingWeeks']), 'semester': result['semester']}, ensure_ascii=False))
    else:
        result, _ = fetch_all(load_connection()['bundle'])
        print(json.dumps({'courses': len(result['courses']), 'exams': len(result['exams']), 'calendar': calendar_view(result)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
