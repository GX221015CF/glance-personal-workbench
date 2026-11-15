"""Explicit, bounded Canvas-to-IMA batch. No school-refresh side effects."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import urljoin, urlsplit

import requests

import canvas_bridge as canvas
from gmail_bridge import protect
from ima_bridge import ROOT, FILE_FORMATS, ImaError, ImaService, identifier, now, store_lock, upload_bytes
from school_storage import save_encrypted_json

COURSE = '财务管理（0111）'
FILE_NAMES = ('第十章-资本结构.pdf', '第九章-长期融资.pdf')
LIBRARY = '财务管理'
FOLDER = '课件PPT'
STORE = ROOT / 'course-sync.dpapi'
RECOVERY = ROOT / 'course-sync-recovery.dpapi'
REPORT = Path(__file__).resolve().parent / 'runtime' / 'ima-stage-b' / 'latest-result.json'
LIMIT = 20 * 1024 * 1024
STORE_IO_LOCK = threading.RLock()
ERRORS = {
    'SOURCE_CONFLICT': '来源课程或文件未能唯一匹配，请核对名称和权限。',
    'TARGET_CONFLICT': '目标知识库或文件夹未能唯一匹配，请重新选择目标。',
    'TARGET_MISSING': '原目标文件夹已失效，请重新选择目标。',
    'NAME_CONFLICT': '目标已有同名文件，但缺少可证明关联的同步记录，请先核对。',
    'CONTENT_CHANGED': '同一来源文件的内容指纹已变化，已保留原映射，请核对新版本。',
    'REMOTE_MISSING': '已同步媒体当前无法找到，已保留映射，请核对云端位置。',
    'INVALID_PDF': '下载内容未通过PDF类型、大小或文件特征校验，未上传。',
    'DOWNLOAD_FAILED': 'Canvas原文件下载失败，已保留已有同步结果。',
    'DOWNLOAD_FORBIDDEN': 'Canvas拒绝读取这份文件，请检查该来源的访问权限。',
    'SOURCE_LOCKED': '资料尚未开放或已锁定，请查看Canvas开放时间。',
    'SOURCE_METADATA_UNAVAILABLE': '文件信息暂不可读取，请刷新学校资料后重试。',
    'OUTCOME_UNKNOWN': '写入结果待确认，已先回读，暂停重复发送。',
    'SAVE_FAILED': '本机同步记录保存失败，已停止后续写入，请检查磁盘和恢复记录。',
    'INVALID_STORE': '同步记录无法安全读取，原文件已保留。',
    'UNSUPPORTED_FILE': '文件格式暂不支持，或大小超过本机20MiB上传上限。',
}


class SyncError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(ERRORS[code])


def one(rows, code):
    if len(rows) != 1:
        raise SyncError(code)
    return rows[0]


def valid_pdf(payload, content_type, expected_size):
    if (content_type.split(';')[0].strip().lower() != 'application/pdf'
            or not isinstance(payload, bytes) or not 0 < len(payload) <= LIMIT
            or type(expected_size) is not int or len(payload) != expected_size
            or not re.match(rb'%PDF-[12]\.[0-9]', payload[:8])
            or b'%%EOF' not in payload[-4096:]):
        raise SyncError('INVALID_PDF')
    return hashlib.sha256(payload).hexdigest()


def file_format(name, size):
    extension = name.rsplit('.', 1)[-1].lower()
    if extension not in FILE_FORMATS or type(size) is not int or not 0 < size <= LIMIT:
        raise SyncError('UNSUPPORTED_FILE')
    media_type, content_type, kind = FILE_FORMATS[extension]
    return {'extension': extension, 'mediaType': media_type, 'uploadContentType': content_type, 'signatureKind': kind}


def valid_content(payload, content_type, source):
    spec = file_format(source.get('fileName', 'file.pdf'), source['size'])
    if spec['signatureKind'] == 'pdf':
        return valid_pdf(payload, content_type, source['size'])
    if not payload or len(payload) != source['size'] or len(payload) > LIMIT:
        raise SyncError('INVALID_PDF')
    kind = spec['signatureKind']
    signatures = {'ole': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'zip': b'PK\x03\x04',
                  'png': b'\x89PNG\r\n\x1a\n', 'jpeg': b'\xff\xd8\xff'}
    if kind in signatures and not payload.startswith(signatures[kind]):
        raise SyncError('INVALID_PDF')
    if kind == 'webp' and not (payload.startswith(b'RIFF') and payload[8:12] == b'WEBP'):
        raise SyncError('INVALID_PDF')
    if kind == 'text' and (b'\x00' in payload or b'type="password"' in payload[:8192].lower()):
        raise SyncError('INVALID_PDF')
    if kind != 'text' and 'text/html' in content_type.lower():
        raise SyncError('INVALID_PDF')
    return hashlib.sha256(payload).hexdigest()


class CanvasSource:
    def __init__(self):
        saved = canvas.load_private('connection.dpapi')
        if not saved:
            raise canvas.LoginRequired('请使用工作台“更新学校数据”恢复Canvas连接。')
        self.session = canvas.make_session(canvas.validate_bundle(saved['bundle']))
        self.download_urls = {}

    def resolve(self, selection=None):
        courses = canvas.read_collection(self.session, '/api/v1/courses', {
            'enrollment_state': 'active', 'enrollment_type': 'student', 'per_page': 100})
        course = one([row for row in courses if
                      (str(row['id']) == selection['courseId'] if selection else row.get('name') == COURSE)], 'SOURCE_CONFLICT')
        course_id = str(course['id'])
        if not course_id.isdecimal():
            raise SyncError('SOURCE_CONFLICT')
        files = canvas.read_collection(self.session, f'/api/v1/courses/{course_id}/files', {'per_page': 100})
        result = []
        for index, name in enumerate(FILE_NAMES):
            found = one([row for row in files if
                         (str(row['id']) == selection['fileIds'][index] if selection else
                          (row.get('display_name') or row.get('filename')) == name)], 'SOURCE_CONFLICT')
            file_id = str(found['id'])
            if (not file_id.isdecimal() or canvas.file_locked(found)
                    or found.get('content-type') != 'application/pdf'
                    or type(found.get('size')) is not int or not 0 < found['size'] <= LIMIT):
                raise SyncError('SOURCE_CONFLICT')
            self.download_urls[file_id] = found.get('url', '')
            result.append({'courseId': course_id, 'courseName': course['name'], 'fileId': file_id,
                           'fileName': found.get('display_name') or found['filename'],
                           'size': found['size'], 'contentType': found['content-type'],
                           'createdAt': found.get('created_at'), 'updatedAt': found.get('updated_at')})
        return result

    def download(self, source):
        url = self.download_urls[source['fileId']]
        try:
            for _ in range(5):
                parsed = urlsplit(url)
                if (parsed.scheme != 'https' or parsed.netloc != 'canvas.shufe.edu.cn'
                        or not re.fullmatch(r'/files/\d+/download', parsed.path)
                        or parsed.path != '/files/' + source['fileId'] + '/download'
                        or parsed.username or parsed.password or parsed.fragment):
                    raise SyncError('DOWNLOAD_FAILED')
                response = self.session.get(url, headers={'Accept': source.get('contentType') or 'application/pdf'},
                                            timeout=(8, 30), allow_redirects=False, stream=True)
                try:
                    if response.status_code == 401:
                        raise canvas.LoginRequired('Canvas登录已失效，请在工作台恢复学校连接。')
                    if response.status_code in (301, 302, 303, 307, 308):
                        destination = urljoin(url, response.headers.get('Location', ''))
                        if '/login' in urlsplit(destination).path:
                            raise canvas.LoginRequired('Canvas登录已失效，请在工作台恢复学校连接。')
                        url = destination
                        continue
                    if response.status_code != 200:
                        raise SyncError('DOWNLOAD_FORBIDDEN' if response.status_code == 403 else 'DOWNLOAD_FAILED')
                    chunks, length = [], 0
                    for chunk in response.iter_content(65536):
                        length += len(chunk)
                        if length > LIMIT or length > source['size']:
                            raise SyncError('INVALID_PDF')
                        chunks.append(chunk)
                    payload = b''.join(chunks)
                    digest = valid_content(payload, response.headers.get('Content-Type', ''), source)
                    return payload, {'status': 'passed', 'httpStatus': 200,
                                     'size': len(payload), 'sha256': digest, 'checkedAt': now()}
                finally:
                    response.close()
        except requests.RequestException:
            raise SyncError('DOWNLOAD_FAILED') from None
        raise SyncError('DOWNLOAD_FAILED')


class SyncStore:
    def __init__(self, path=STORE, recovery=RECOVERY, *, read_only=False):
        self.path, self.recovery = path, recovery
        with STORE_IO_LOCK:
            self.load(read_only=read_only)

    def load(self, *, read_only):
        path, recovery = self.path, self.recovery
        selected = recovery if recovery.exists() else path
        if selected.exists():
            try:
                self.data = json.loads(protect(selected.read_bytes(), decrypt=True))
                if self.data.get('version') != 1 or not isinstance(self.data.get('items'), dict):
                    raise ValueError()
            except Exception:
                raise SyncError('INVALID_STORE') from None
            if selected == recovery and not read_only:
                try:
                    save_encrypted_json(path, self.data)
                    recovery.unlink()
                except OSError:
                    raise SyncError('SAVE_FAILED') from None
        else:
            self.data = {'version': 1, 'batch': None, 'items': {}, 'createdAt': now()}

    def save(self):
        # Windows readers can deny an atomic replacement while their handle is
        # open. Status handlers share this short I/O lock with the worker.
        with STORE_IO_LOCK:
            self.data['updatedAt'] = now()
            try:
                save_encrypted_json(self.path, self.data)
            except OSError:
                try:
                    save_encrypted_json(self.recovery, self.data)
                except OSError:
                    pass
                raise SyncError('SAVE_FAILED') from None

    def event(self, item, stage, **details):
        item['stage'] = stage
        item.setdefault('history', []).append({'stage': stage, 'at': now(), **details})
        self.save()


class CourseSync:
    def __init__(self, client, source, store):
        self.client, self.source, self.store = client, source, store

    def preview(self):
        saved = self.store.data.get('batch')
        sources = self.source.resolve(saved)
        libraries = self.client.libraries()
        library = one([row for row in libraries if
                       (row['libraryId'] == saved['libraryId'] if saved else row['name'] == LIBRARY)], 'TARGET_CONFLICT')
        tree = self.client.tree(library['libraryId'])
        folder = one([row for row in tree if row['kind'] == 'folder' and
                      (row['folderId'] == saved['targetFolderId'] if saved else row['name'] == FOLDER)],
                     'TARGET_MISSING' if saved else 'TARGET_CONFLICT')
        target = {'libraryId': library['libraryId'], 'libraryName': library['name'],
                  'targetFolderId': folder['folderId'], 'path': library['name'] + folder['path']}
        return {'sources': sources, 'target': target}

    def locate(self, item, tree):
        matches = [row for row in tree if row['kind'] == 'file' and row['mediaId'] == item.get('mediaId')]
        if len(matches) > 1:
            raise SyncError('OUTCOME_UNKNOWN')
        return matches[0] if matches else None

    def commit(self, item, remote):
        if item['stage'] != 'committed' and remote['name'] != item['source']['fileName']:
            raise SyncError('OUTCOME_UNKNOWN')
        item.update(actualFolderId=remote['parentFolderId'], remoteName=remote['name'], remotePath=remote['path'],
                    lastVerifiedAt=now(), lastSuccessAt=item.get('lastSuccessAt') or now())
        item['stages']['readback'] = {'status': 'passed', 'checkedAt': now(), 'nameVerified': True,
                                     'actualFolderId': remote['parentFolderId']}
        item['stages']['ingest'] = {'status': 'passed', 'checkedAt': now()}
        item.pop('error', None)
        self.store.event(item, 'committed')

    def mapping_items(self, source, library_id):
        ids = source.get('memberIds', [source['fileId']])
        return [(key, row) for key, row in self.store.data['items'].items()
                if row['source']['courseId'] == source['courseId'] and row['libraryId'] == library_id
                and (row['source']['fileId'] in ids or source.get('groupId')
                     and (row['source'].get('groupId') or canvas.material_group_id(row['source']['courseId'], row['source']['fileName'])) == source['groupId'])]

    @staticmethod
    def source_for_item(source, item):
        if item:
            member = next((row for row in source.get('members', []) if row['fileId'] == item['source']['fileId']), None)
            if member:
                return {**source, **member, 'sources': source.get('sources', [])}
        return source

    def item_key(self, source, library_id, tree=None, prefer_source=False):
        matches = self.mapping_items(source, library_id)
        if not source.get('groupId') and len(matches) > 1:
            raise SyncError('OUTCOME_UNKNOWN')
        if matches:
            visible = {row['mediaId'] for row in tree or [] if row['kind'] == 'file'}
            matches.sort(key=lambda pair: (prefer_source and pair[1]['source']['fileId'] != source['fileId'],
                                           tree is not None and pair[1].get('mediaId') not in visible,
                                           pair[1]['stage'] != 'committed', int(pair[1]['source']['fileId'])))
            return matches[0][0]
        key = source['courseId'] + ':' + source.get('groupId', source['fileId'])
        return key if key not in self.store.data['items'] else key + ':' + library_id

    @staticmethod
    def name_matches(source, tree):
        name = unicodedata.normalize('NFKC', source['fileName']).strip().casefold()
        return [row for row in tree if row['kind'] == 'file'
                and unicodedata.normalize('NFKC', row['name']).strip().casefold() == name]

    def record_name_match(self, source, library_id, matches):
        record = {'association': 'user_name_rule', 'ruleVersion': 1,
                  'courseId': source['courseId'], 'fileId': source['fileId'],
                  'groupId': source.get('groupId'), 'memberIds': source.get('memberIds', [source['fileId']]),
                  'fileName': source['fileName'], 'libraryId': library_id, 'checkedAt': now(),
                  'candidates': [{'mediaId': row['mediaId'], 'name': row['name'], 'path': row['path'],
                                  'parentFolderId': row['parentFolderId'], 'size': row.get('size')}
                                 for row in matches]}
        self.store.data.setdefault('nameMatches', {})[source['courseId'] + ':' + source.get('groupId', source['fileId']) + ':' + library_id] = record
        return record

    def sync_one(self, source, target, *, new_version=False, ignored_media_ids=(), name_rule=False):
        initial_tree = self.client.tree(target['libraryId']) if source.get('groupId') else None
        key = self.item_key(source, target['libraryId'], initial_tree, prefer_source=new_version)
        item = self.store.data['items'].get(key)
        source = self.source_for_item(source, item)
        if name_rule and not ignored_media_ids and (item is None or not item.get('mediaId')
                                                    and item['stage'] in ('downloaded', 'allocation_rejected')):
            matches = self.name_matches(source, initial_tree if initial_tree is not None else self.client.tree(target['libraryId']))
            if matches:
                self.record_name_match(source, target['libraryId'], matches)
                self.store.save()
                return 'skipped'
        payload, download = self.source.download(source)
        actual_source = download.get('source', source)
        if (item and actual_source['fileId'] != item['source']['fileId'] and not item.get('mediaId')
                and item['stage'] in ('downloaded', 'allocation_rejected')):
            previous_id = item['source']['fileId']
            item.update(source=actual_source, sha256=download['sha256'], size=len(payload))
            self.store.event(item, 'downloaded', previousSourceFileId=previous_id, actualFileId=actual_source['fileId'])
        if item and item['stage'] == 'committed' and actual_source['fileId'] != item['source']['fileId']:
            remote = self.locate(item, initial_tree if initial_tree is not None else self.client.tree(item['libraryId']))
            if remote:
                self.commit(item, remote)
                return 'skipped'
        source = actual_source
        if item and item['sha256'] != download['sha256']:
            if not new_version or item['stage'] != 'committed':
                raise SyncError('CONTENT_CHANGED')
            archived = copy.deepcopy(item)
            versions = archived.pop('versions', [])
            versions.append(archived)
            item = {'versions': versions, 'source': source, 'sha256': download['sha256'], 'size': len(payload),
                    'libraryId': target['libraryId'], 'initialTargetFolderId': target['targetFolderId'],
                    'actualFolderId': None, 'stage': 'downloaded', 'stages': {}, 'retiredMedia': []}
            self.store.data['items'][key] = item
        if item is None:
            item = {'source': source, 'sha256': download['sha256'], 'size': len(payload),
                    'libraryId': target['libraryId'], 'initialTargetFolderId': target['targetFolderId'],
                    'actualFolderId': None, 'stage': 'downloaded', 'stages': {}, 'retiredMedia': []}
            self.store.data['items'][key] = item
        item['source'] = source
        item['stages']['download'] = download
        self.store.save()
        tree = self.client.tree(item['libraryId'])
        remote = self.locate(item, tree) if item.get('mediaId') else None
        if remote:
            action = 'skipped' if item['stage'] == 'committed' else 'reconciled'
            self.commit(item, remote)
            return action
        if item['stage'] == 'committed':
            raise SyncError('REMOTE_MISSING')
        if item['stage'] in ('allocating', 'upload_pending', 'add_pending', 'add_acknowledged'):
            raise SyncError('OUTCOME_UNKNOWN')
        archived_ids = {row['mediaId'] for row in item.get('versions', []) if row.get('mediaId')}
        if new_version and source.get('groupId'):
            archived_ids.update(row['mediaId'] for _, row in self.mapping_items(source, target['libraryId']) if row.get('mediaId'))
        archived_ids.update(ignored_media_ids)
        source_name = unicodedata.normalize('NFKC', source['fileName']).strip().casefold()
        if any(row['kind'] == 'file' and unicodedata.normalize('NFKC', row['name']).strip().casefold() == source_name
               and row['mediaId'] not in archived_ids
               for row in tree):
            raise SyncError('NAME_CONFLICT')
        item['attemptTargetFolderId'] = target['targetFolderId']
        if item['stage'] != 'uploaded':
            if item.get('mediaId'):
                if not any(row['mediaId'] == item['mediaId'] for row in item['retiredMedia']):
                    item['retiredMedia'].append({'mediaId': item['mediaId'], 'reason': 'no_upload_sent_or_explicit_rejection',
                                                 'ingested': False})
            self.store.event(item, 'allocating')
            try:
                allocation = self.client._request('create_media', {'knowledge_base_id': item['libraryId'],
                    'file_name': source['fileName'], 'file_size': len(payload),
                    'content_type': source.get('uploadContentType', 'application/pdf'),
                    'file_ext': source.get('extension', 'pdf')}, mutation=True)
            except ImaError as error:
                if error.code in ('INVALID_PARAMETER', 'FORBIDDEN', 'AUTH_REQUIRED', 'RATE_LIMITED', 'NOT_OPEN'):
                    self.store.event(item, 'allocation_rejected', code=error.code)
                raise
            item['mediaId'] = identifier(allocation.get('media_id'))
            credential = allocation.get('cos_credential')
            item['cosKey'] = credential.get('cos_key') if isinstance(credential, dict) else None
            self.store.event(item, 'allocated')

            def before_send(diagnostic):
                self.store.event(item, 'upload_pending', diagnostic=copy.deepcopy(diagnostic))

            try:
                diagnostic = upload_bytes(credential, payload, source.get('uploadContentType', 'application/pdf'), on_send=before_send)
            except ImaError as error:
                if error.code in ('COS_UPLOAD_REJECTED', 'COS_UPLOAD_FAILED'):
                    self.store.event(item, 'upload_rejected', code=error.code, diagnostic=error.diagnostic)
                raise
            item['stages']['upload'] = {'status': 'passed', 'httpStatus': diagnostic['httpStatus'], 'checkedAt': now()}
            self.store.event(item, 'uploaded')
        self.store.event(item, 'add_pending')
        try:
            result = self.client._request('add_knowledge', {'knowledge_base_id': item['libraryId'],
                'folder_id': item['attemptTargetFolderId'], 'media_id': item['mediaId'], 'media_type': source.get('mediaType', 1),
                'title': source['fileName'], 'file_info': {'cos_key': item['cosKey'], 'file_size': item['size'],
                    'last_modify_time': int(time.time()), 'file_name': source['fileName']}}, mutation=True)
        except ImaError as error:
            if error.code in ('INVALID_PARAMETER', 'FORBIDDEN', 'AUTH_REQUIRED', 'RATE_LIMITED', 'NOT_OPEN'):
                self.store.event(item, 'uploaded', addRejected=error.code)
            raise
        if result.get('media_id') != item['mediaId']:
            raise SyncError('OUTCOME_UNKNOWN')
        self.store.event(item, 'add_acknowledged')
        remote = self.locate(item, self.client.tree(item['libraryId']))
        if not remote or remote['parentFolderId'] != item['attemptTargetFolderId']:
            raise SyncError('OUTCOME_UNKNOWN')
        self.commit(item, remote)
        return 'synced'

    def inspect(self, item):
        # Readability, indexing and parsing are independent of successful ingestion.
        try:
            rows = self.client.search(item['libraryId'], item['source']['fileName'])
            item['stages']['search'] = {'status': 'found' if any(row['mediaId'] == item['mediaId'] for row in rows) else 'not_found',
                                       'checkedAt': now()}
        except ImaError as error:
            item['stages']['search'] = {'status': 'failed', 'code': error.code, 'checkedAt': now()}
        try:
            item['stages']['mediaAccess'] = self.client.media_access(item['mediaId'], probe=True)
        except ImaError as error:
            item['stages']['mediaAccess'] = {'status': 'failed', 'code': error.code}
        item['stages']['parsing'] = {'status': 'unverified'}
        self.store.save()

    def run(self, preview):
        if not self.store.data.get('batch'):
            self.store.data['batch'] = {'courseId': preview['sources'][0]['courseId'],
                'fileIds': [row['fileId'] for row in preview['sources']],
                'libraryId': preview['target']['libraryId'], 'targetFolderId': preview['target']['targetFolderId']}
            self.store.save()
        outcomes = []
        for source in preview['sources']:
            key = source['courseId'] + ':' + source['fileId']
            try:
                action = self.sync_one(source, preview['target'])
                if action != 'skipped':
                    self.inspect(self.store.data['items'][key])
                outcomes.append({'fileName': source['fileName'], 'result': action})
            except (SyncError, ImaError, canvas.CanvasError) as error:
                code = error.code
                item = self.store.data['items'].get(key)
                if item and code != 'SAVE_FAILED':
                    item['error'] = {'code': code, 'stage': item['stage'], 'checkedAt': now(),
                                     'upstreamCode': getattr(error, 'upstream_code', None),
                                     'diagnostic': getattr(error, 'diagnostic', {})}
                    self.store.save()
                outcomes.append({'fileName': source['fileName'], 'result': 'failed', 'code': code})
                if code == 'SAVE_FAILED':
                    break
        return {'checkedAt': now(), 'target': preview['target'], 'outcomes': outcomes,
                'items': [{key: copy.deepcopy(value) for key, value in item.items() if key != 'cosKey'}
                          for item in self.store.data['items'].values()]}


def save_report(result):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    temporary.replace(REPORT)


def show_saved(store):
    print('本机同步记录（历史结果，未进行学校或云端检查）')
    if not store.data['items']:
        print('尚无本批同步记录。')
    for item in store.data['items'].values():
        status = '已确认入库' if item['stage'] == 'committed' else '尚未完成'
        print(item['source']['fileName'], '·', status)
        print('  上次成功：', item.get('lastSuccessAt', '尚未成功'), '· 位置：', item.get('remotePath', '尚未确认'))
        if item.get('error'):
            print('  最近尝试的诊断码：', item['error']['code'])


def main():
    parser = argparse.ArgumentParser(description='财务管理两份已选PDF → ima课件PPT')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--run', action='store_true', help='预览后执行已选两文件，已成功的内容跳过')
    modes.add_argument('--preview', action='store_true', help='只核对来源与目标，不上传')
    modes.add_argument('--status', action='store_true', help='只查看本机保存结果')
    args = parser.parse_args()
    with store_lock(STORE):
        store = SyncStore(STORE, RECOVERY)
        if args.status:
            show_saved(store)
            return
        if not args.run and not args.preview:
            print('本批文件：', '、'.join(FILE_NAMES))
            choice = input('输入1预览并同步；输入2查看本机保存结果；直接回车结束：').strip()
            if choice == '2':
                show_saved(store)
                return
            if choice != '1':
                return
        source = CanvasSource()
        try:
            runner = CourseSync(ImaService().client(), source, store)
            preview = runner.preview()
            print('课程：', preview['sources'][0]['courseName'])
            print('目标：', preview['target']['path'])
            for row in preview['sources']:
                print(f"  {row['fileName']} · {row['size']:,}字节")
            if args.preview:
                return
            result = runner.run(preview)
            save_report(result)
            labels = {'synced': '已同步并确认入库', 'skipped': '内容一致，已跳过',
                      'reconciled': '已回读恢复原结果', 'failed': '未完成'}
            for row in result['outcomes']:
                print(row['fileName'], labels[row['result']], row.get('code', ''))
            print('详细结果已保存在：', REPORT)
            if any(row['result'] == 'failed' for row in result['outcomes']):
                raise SystemExit(2)
        finally:
            source.session.close()


if __name__ == '__main__':
    try:
        main()
    except (SyncError, ImaError, canvas.CanvasError) as error:
        print(str(error), '诊断码', error.code)
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print('操作已停止。再次运行会先核对保存记录及云端状态。')
    except Exception:
        print('本机同步未完成，请保留加密映射和阶段报告。未输出凭据或原始异常。')
        raise SystemExit(1)
