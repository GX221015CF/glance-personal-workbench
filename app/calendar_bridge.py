"""Local date-only calendar, with separately maintained official and personal events."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import urllib.parse
import uuid

import requests
from bs4 import BeautifulSoup
from gmail_bridge import CredentialStore

ROOT = Path(__file__).resolve().parent
OFFICIAL = ROOT / 'config' / 'school-calendar.json'
from workbench_config import DATA_DIR
PRIVATE = DATA_DIR / 'Calendar'
TZ = dt.timezone(dt.timedelta(hours=8))
TYPES = ('teaching', 'registration', 'exam', 'holiday', 'campus', 'anniversary', 'competition', 'personal', 'other')
PERSONAL_FIELDS = {'id', 'revision', 'title', 'startDate', 'endDate', 'type', 'description', 'annual'}


class CalendarError(Exception):
    def __init__(self, message, status=400, code='INVALID_EVENT'):
        super().__init__(message)
        self.status = status
        self.code = code


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def date_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise CalendarError('请填写有效日期')
    try:
        result = dt.date.fromisoformat(value)
    except ValueError:
        raise CalendarError('请填写有效日期') from None
    if not 1900 <= result.year <= 2199:
        raise CalendarError('日期年份应在 1900 至 2199 之间')
    return result


def event_fields(payload):
    if not isinstance(payload, dict) or set(payload) - PERSONAL_FIELDS:
        raise CalendarError('事件字段无效')
    title = payload.get('title')
    description = payload.get('description', '')
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100:
        raise CalendarError('请填写标题，最多 100 个字')
    if not isinstance(description, str) or len(description) > 2000:
        raise CalendarError('简要说明最多 2000 个字')
    start = date_value(payload.get('startDate'))
    end = date_value(payload.get('endDate'))
    if end < start:
        raise CalendarError('结束日期不能早于开始日期')
    kind = payload.get('type')
    if kind not in TYPES:
        raise CalendarError('请选择有效事件类型')
    annual = payload.get('annual', False)
    if not isinstance(annual, bool) or annual and (start != end or kind != 'anniversary'):
        raise CalendarError('每年重复适用于单日纪念日')
    return {'title': title.strip(), 'description': description.strip(), 'startDate': start.isoformat(),
            'endDate': end.isoformat(), 'type': kind, 'annual': annual}


def official_url(url):
    if not isinstance(url, str):
        raise CalendarError('校历来源地址无效')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.sufe.edu.cn', 'ieco.sufe.edu.cn') or parsed.username or parsed.port:
        raise CalendarError('校历来源应为上财官方 HTTPS 页面')
    return url


def validate_official(document):
    if not isinstance(document, dict) or document.get('schemaVersion') != 1:
        raise CalendarError('校历文件版本无效')
    sources = document.get('sources')
    events = document.get('events')
    if not isinstance(sources, list) or not sources or not isinstance(events, list) or not events:
        raise CalendarError('校历文件需要来源和事件')
    source_ids = set()
    for source in sources:
        if source['id'] in source_ids or not re.fullmatch(r'\d{4}-\d{4}', source['academicYear']):
            raise CalendarError('校历来源标识或学年无效')
        source_ids.add(source['id'])
        official_url(source['url'])
        if source.get('publishedAt'):
            date_value(source['publishedAt'])
        for asset in source['assets']:
            official_url(asset['url'])
            if not re.fullmatch(r'[a-f0-9]{64}', asset['sha256']):
                raise CalendarError('校历原图指纹无效')
    ids = set()
    for event in events:
        if event['id'] in ids or not event['id'].startswith('sufe-'):
            raise CalendarError('学校事件标识无效或重复')
        ids.add(event['id'])
        if event['source'] != 'official' or event['sourceId'] not in source_ids or event['type'] not in TYPES:
            raise CalendarError('学校事件来源或类型无效')
        date_value(event['startDate'])
        date_value(event['endDate'])
        if event['endDate'] < event['startDate'] or not event['title'] or not event['rawText']:
            raise CalendarError('学校事件日期或原文无效')
        source = next(s for s in sources if s['id'] == event['sourceId'])
        if event['academicYear'] != source['academicYear'] or event['sourceUrl'] != source['url']:
            raise CalendarError('学校事件学年或来源不一致')
        official_url(event['sourceAsset'])
        if not any(a['url'] == event['sourceAsset'] for a in source['assets']):
            raise CalendarError('学校事件原图不在来源清单中')
    return copy.deepcopy(document)


class CalendarService:
    def __init__(self, store=None, official_path=OFFICIAL):
        self.store = store or CredentialStore(PRIVATE)
        self.official_path = Path(official_path)
        self.lock = threading.RLock()
        self.verify_lock = threading.Lock()
        self.verifying = False
        self.last_verify_attempt = None
        self.official = None
        self.official_error = ''
        self.reload_official()

    def reload_official(self):
        try:
            candidate = validate_official(json.loads(self.official_path.read_text(encoding='utf-8')))
            with self.lock:
                self.official = candidate
                self.official_error = ''
        except Exception:
            self.official_error = '校历文件暂不可用，保留已有校历，请检查导入文件。'

    def personal_document(self):
        try:
            document = self.store.read('personal-events', {'schemaVersion': 1, 'events': []})
            if document.get('schemaVersion') != 1 or not isinstance(document.get('events'), list):
                raise ValueError('invalid storage')
            for event in document['events']:
                event_fields({key: event[key] for key in PERSONAL_FIELDS if key in event})
            return document
        except Exception:
            raise CalendarError('个人事件无法读取，请检查本机存储后重试', 503, 'STORAGE_ERROR') from None

    def snapshot(self):
        self.reload_official()
        with self.lock:
            personal_error = ''
            try:
                personal = [e for e in self.personal_document()['events'] if not e.get('deletedAt')]
            except CalendarError as error:
                personal, personal_error = [], str(error)
            official = self.official or {'sources': [], 'events': [], 'notes': []}
            try:
                verification = self.store.read('source-verification', {})
                fingerprint = hashlib.sha256(json.dumps(official['sources'], sort_keys=True).encode()).hexdigest()
                if verification.get('fingerprint') != fingerprint:
                    verification = {}
            except Exception:
                verification = {'state': 'failed', 'message': '上次核验记录无法读取'}
            return {'events': copy.deepcopy(official['events'] + personal), 'sources': copy.deepcopy(official['sources']),
                    'notes': copy.deepcopy(official.get('notes', [])),
                    'meta': {'timezone': 'Asia/Shanghai', 'today': dt.datetime.now(TZ).date().isoformat(),
                             'officialAvailable': bool(self.official), 'officialError': self.official_error,
                             'personalAvailable': not personal_error, 'personalError': personal_error,
                             'verifying': self.verifying, 'verification': verification,
                             'generatedAt': now()}}

    def save(self, payload):
        fields = event_fields(payload)
        try:
            identifier = str(uuid.UUID(payload.get('id', '')))
        except (ValueError, TypeError, AttributeError):
            raise CalendarError('个人事件标识无效') from None
        revision = payload.get('revision')
        if type(revision) is not int or revision < 0:
            raise CalendarError('事件版本无效')
        with self.lock:
            document = self.personal_document()
            existing = next((e for e in document['events'] if e['id'] == identifier), None)
            if existing and not existing.get('deletedAt') and revision == 0 and existing['revision'] == 1 and all(existing[k] == v for k, v in fields.items()):
                return copy.deepcopy(existing)
            if (existing and (existing['revision'] != revision or existing.get('deletedAt'))) or (not existing and revision != 0):
                raise CalendarError('该事件已在其他页面修改，请重新读取后再编辑', 409, 'REVISION_CONFLICT')
            if not existing and sum(not e.get('deletedAt') for e in document['events']) >= 2000:
                raise CalendarError('个人事件已达 2000 条，请整理后再添加')
            stamp = now()
            event = {**fields, 'id': identifier, 'source': 'personal', 'revision': revision + 1,
                     'createdAt': existing['createdAt'] if existing else stamp, 'updatedAt': stamp}
            if existing:
                document['events'][document['events'].index(existing)] = event
            else:
                document['events'].append(event)
            try:
                self.store.write('personal-events', document)
            except Exception:
                raise CalendarError('事件未能保存，请检查本机存储后重试', 503, 'STORAGE_ERROR') from None
            return copy.deepcopy(event)

    def delete(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'id', 'revision'} or type(payload['revision']) is not int:
            raise CalendarError('删除请求无效')
        with self.lock:
            document = self.personal_document()
            event = next((e for e in document['events'] if e['id'] == payload['id']), None)
            if not event:
                raise CalendarError('个人事件不存在', 404, 'NOT_FOUND')
            if event.get('deletedAt'):
                return
            if event['revision'] != payload['revision']:
                raise CalendarError('事件已被修改，请重新读取后再删除', 409, 'REVISION_CONFLICT')
            event.update({'deletedAt': now(), 'revision': event['revision'] + 1})
            try:
                self.store.write('personal-events', document)
            except Exception:
                raise CalendarError('事件未能删除，请检查本机存储后重试', 503, 'STORAGE_ERROR') from None

    def request_verify(self):
        with self.lock:
            if self.verifying:
                return
            if self.last_verify_attempt and (dt.datetime.now(TZ) - self.last_verify_attempt).total_seconds() < 60:
                raise CalendarError('请在一分钟后再核验', 429, 'VERIFY_COOLDOWN')
            self.verifying = True
            self.last_verify_attempt = dt.datetime.now(TZ)
        threading.Thread(target=self.verify, daemon=True).start()

    def verify(self):
        with self.verify_lock:
            with self.lock:
                document = copy.deepcopy(self.official)
            try:
                if not document:
                    raise ValueError('no official data')
                changed = False
                with requests.Session() as session:
                    session.trust_env = False
                    for source in document['sources']:
                        page = session.get(official_url(source['url']), timeout=15, allow_redirects=False)
                        page.raise_for_status()
                        if page.is_redirect:
                            raise ValueError('redirect')
                        soup = BeautifulSoup(page.content, 'html.parser')
                        page_images = {urllib.parse.urljoin(source['url'], image.get('src', '')) for image in soup.select('img')}
                        for asset in source['assets']:
                            if asset['url'] not in page_images:
                                changed = True
                            response = session.get(official_url(asset['url']), timeout=15, allow_redirects=False)
                            response.raise_for_status()
                            if response.is_redirect or len(response.content) > 10_000_000:
                                raise ValueError('invalid image')
                            changed |= hashlib.sha256(response.content).hexdigest() != asset['sha256']
                result = {'state': 'changed' if changed else 'verified', 'checkedAt': now(),
                          'message': '官方原图有变化，当前保留已核对事件，待重新导入。' if changed else '官方原图与已核对版本一致。'}
            except Exception:
                result = {'state': 'failed', 'checkedAt': now(), 'message': '暂时无法核验官方原图，保留已有校历。'}
            result['fingerprint'] = hashlib.sha256(json.dumps(document['sources'] if document else [], sort_keys=True).encode()).hexdigest()
            try:
                with self.lock:
                    self.store.write('source-verification', result)
            except Exception:
                pass
            finally:
                with self.lock:
                    self.verifying = False


def handle_request(handler, service, token):
    path = urllib.parse.urlsplit(handler.path).path
    prefix = '/api/calendar/v1'
    if path != prefix and not path.startswith(prefix + '/'):
        return False

    def respond(status, data=None, error=None):
        handler.json_response(status, {'apiVersion': '1', 'data': data, 'error': error})

    try:
        if not handler.desktop_request() or not secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), token):
            raise CalendarError('需要本机工作台来源与有效令牌', 403, 'FORBIDDEN')
        methods = {prefix: 'GET', prefix + '/events': 'POST', prefix + '/delete': 'POST', prefix + '/verify': 'POST'}
        if path not in methods:
            raise CalendarError('日历接口不存在', 404, 'NOT_FOUND')
        if handler.path != path:
            raise CalendarError('日历接口不接受查询参数')
        if handler.command != methods[path]:
            raise CalendarError('日历请求方法不正确', 405, 'METHOD_NOT_ALLOWED')
        length = handler.headers.get('Content-Length', '0')
        if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or int(length) > 20000:
            raise CalendarError('日历请求长度无效')
        if path in (prefix, prefix + '/verify'):
            if int(length):
                raise CalendarError('此日历接口不接受请求体')
            if path.endswith('/verify'):
                service.request_verify()
                respond(202, {'accepted': True})
            else:
                respond(200, service.snapshot())
        else:
            if not int(length) or handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise CalendarError('请发送 JSON 事件')
            handler.connection.settimeout(10)
            raw = handler.rfile.read(int(length))
            if len(raw) != int(length):
                raise CalendarError('事件接收不完整')
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                raise CalendarError('事件 JSON 无效') from None
            if path.endswith('/delete'):
                service.delete(payload)
                respond(200, {'deleted': True})
            else:
                respond(200, service.save(payload))
    except CalendarError as error:
        respond(error.status, error={'code': error.code, 'message': str(error)})
    except Exception:
        respond(503, error={'code': 'CALENDAR_UNAVAILABLE', 'message': '日历服务暂不可用，请稍后重试'})
    return True


def main():
    parser = argparse.ArgumentParser(description='Import a manually verified official calendar without changing personal events.')
    parser.add_argument('action', choices=['import-official'])
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    document = validate_official(json.loads(args.file.read_text(encoding='utf-8')))
    temporary = OFFICIAL.with_name(OFFICIAL.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(OFFICIAL)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'imported': True, 'sources': len(document['sources']), 'events': len(document['events'])}))


if __name__ == '__main__':
    main()
