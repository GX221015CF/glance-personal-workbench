"""Human-run configuration, read-only explorer and bounded owned-object audit.

Run locally, never pass credentials through command-line arguments or chat.
Official downloaded scripts are read as evidence, never installed or executed.
"""
from __future__ import annotations

import getpass
import json
import secrets
import sys
import time
import warnings
from pathlib import Path


from gmail_bridge import protect
from ima_bridge import ROOT, ImaClient, ImaError, ImaService, identifier, now, store_lock, upload_bytes
from school_storage import save_encrypted_json

PROJECT = Path(__file__).resolve().parent
REPORT = PROJECT / 'runtime' / 'ima-stage-a' / 'real-verification.json'
LEDGER = ROOT / 'owned-test.dpapi'
TEST_BYTES = b'Personal Workbench IMA capability test. Synthetic non-sensitive content only.\n'


def read_ledger(path=LEDGER):
    if not path.exists():
        return None
    try:
        data = json.loads(protect(path.read_bytes(), decrypt=True))
        if data.get('version') != 1 or not isinstance(data.get('objects'), dict) or not isinstance(data.get('steps'), dict):
            raise ValueError()
        return data
    except Exception:
        raise ImaError('INVALID_CONFIG') from None


def upload_test_bytes(credential, *, session=None, on_send=None):
    return upload_bytes(credential, TEST_BYTES, 'text/plain', session=session, on_send=on_send)


def exclusive_probe(path=LEDGER):
    return store_lock(path)


class OwnedProbe:
    def __init__(self, client, library_id, parent_id, *, path=LEDGER, report=REPORT):
        self.client, self.path, self.report = client, path, report
        previous = read_ledger(path)
        if previous:
            if previous['libraryId'] != library_id or previous['selectedParentId'] != parent_id:
                raise ImaError('INVALID_REQUEST')
            self.data = previous
        else:
            run_id = secrets.token_hex(8)
            self.data = {'version': 1, 'runId': run_id, 'createdAt': now(), 'libraryId': identifier(library_id),
                         'selectedParentId': identifier(parent_id), 'prefix': 'Workbench-IMA-Test-' + run_id,
                         'objects': {}, 'steps': {}, 'matrix': {}, 'remaining': []}
            self.save()

    def save(self):
        save_encrypted_json(self.path, self.data)

    def rows(self, parent):
        return self.client.browse(self.data['libraryId'], parent)

    def record(self, step, status, **details):
        if step == 'upload-test-file' and step in self.data['steps']:
            self.data.setdefault('uploadHistory', []).append(self.data['steps'][step])
        self.data['steps'][step] = {'status': status, 'checkedAt': now(), **details}
        self.save()

    def owned(self, label, kind):
        obj = self.data['objects'].get(label)
        if not obj or obj['kind'] != kind or obj.get('runId') != self.data['runId']:
            raise ImaError('INVALID_REQUEST')
        return obj

    def folder(self, label, parent):
        if parent != self.data['selectedParentId']:
            if parent not in [obj['id'] for obj in self.data['objects'].values() if obj['kind'] == 'folder']:
                raise ImaError('INVALID_REQUEST')
        name = self.data['prefix'] + '-' + label
        step = 'create-' + label
        rows = self.rows(parent)
        existing = self.data['objects'].get(label)
        if existing:
            if any(row['kind'] == 'folder' and row['folderId'] == existing['id'] for row in rows):
                return existing['id']
            raise ImaError('OUTCOME_UNKNOWN')
        prior = self.data['steps'].get(step)
        if prior:
            # Never replay an uncertain create. Only a unique new row may be adopted.
            matches = [row for row in rows if row['kind'] == 'folder' and row['name'] == name
                       and row['folderId'] not in prior.get('beforeIds', [])]
            if len(matches) != 1:
                raise ImaError('OUTCOME_UNKNOWN')
            object_id = matches[0]['folderId']
        else:
            if any(row['name'] == name for row in rows):
                raise ImaError('INVALID_REQUEST')
            before_ids = [row['folderId'] or row['mediaId'] for row in rows]
            self.record(step, 'pending', parentId=parent, beforeIds=before_ids)
            try:
                result = self.client._request('create_folder', {'knowledge_base_id': self.data['libraryId'],
                                                               'folder_id': parent, 'name': name}, mutation=True)
                object_id = identifier(result.get('media_id') or result.get('folder_id'))
                rows = self.rows(parent)
                if not any(row['kind'] == 'folder' and row['folderId'] == object_id and row['name'] == name for row in rows):
                    raise ImaError('OUTCOME_UNKNOWN')
            except ImaError as error:
                self.record(step, error.code, parentId=parent, beforeIds=before_ids)
                raise
        self.data['objects'][label] = {'id': object_id, 'kind': 'folder', 'parentId': parent,
                                        'name': name, 'runId': self.data['runId']}
        self.record(step, 'passed', parentId=parent, objectId=object_id)
        return object_id

    def file_locations(self, media_id):
        return [obj['id'] for obj in self.data['objects'].values() if obj['kind'] == 'folder'
                and any(row['mediaId'] == media_id for row in self.rows(obj['id']))]

    def upload(self, parent, *, allocation=None):
        self.owned('root', 'folder')
        if parent != self.data['objects']['root']['id']:
            raise ImaError('INVALID_REQUEST')
        step = 'upload-test-file'
        name = self.data['prefix'] + '-synthetic-test.txt'
        prior = self.data['steps'].get(step, {})
        state = self.data.get('uploadState', {})
        media_id = prior.get('mediaId')
        if media_id:
            locations = self.file_locations(media_id)
            if len(locations) > 1:
                raise ImaError('OUTCOME_UNKNOWN')
            if locations:
                self.data['objects']['file'] = {'id': media_id, 'kind': 'file', 'parentId': locations[0],
                                                'name': name, 'runId': self.data['runId']}
                self.record(step, 'passed', mediaId=media_id, name=name, uploaded=True,
                            ingested=True, parsing='unverified', reconciled=True)
                return media_id
        if allocation is not None:
            # The caller retains exactly one journaled create_media result in memory.
            recovery = self.data['steps'].get('recovery-allocate-media', {})
            if recovery.get('status') != 'allocated' or allocation.get('media_id') != recovery.get('mediaId'):
                raise ImaError('INVALID_REQUEST')
        elif prior and (prior.get('stage') == 'create_media' or not state or state.get('mediaId') != media_id
                        or state.get('addRequestSent')
                        or state.get('uploadRequestSent') and not state.get('uploaded')):
            # Legacy ambiguous attempts and sent-but-unconfirmed requests are read-only.
            raise ImaError('OUTCOME_UNKNOWN')
        stage = 'create_media'
        try:
            if not state.get('uploaded') or allocation is not None:
                if media_id:
                    retired = self.data.setdefault('retiredMedia', [])
                    if not any(row['id'] == media_id for row in retired):
                        retired.append({'id': media_id, 'ingested': False,
                                        'uploadOutcome': 'unknown' if not state else 'not_sent',
                                        'reason': 'no reusable credentials; no documented refresh; not visible in owned tree'})
                if allocation is None:
                    self.record(step, 'pending', name=name, parentId=parent, stage=stage)
                    allocation = self.client._request('create_media', {'knowledge_base_id': self.data['libraryId'],
                        'file_name': name, 'file_size': len(TEST_BYTES), 'content_type': 'text/plain', 'file_ext': 'txt'}, mutation=True)
                media_id = identifier(allocation.get('media_id'))
                credential = allocation.get('cos_credential')
                state = {'mediaId': media_id, 'name': name, 'parentId': parent,
                         'cosKey': credential.get('cos_key') if isinstance(credential, dict) else None,
                         'uploadRequestSent': False, 'uploaded': False, 'addRequestSent': False}
                self.data['uploadState'] = state
                stage = 'cos_credential_validation'
                self.record(step, 'media_allocated', name=name, parentId=parent, mediaId=media_id, stage=stage)

                def before_send(diagnostic):
                    state['uploadRequestSent'] = True
                    self.record(step, 'upload_pending', name=name, parentId=parent, mediaId=media_id,
                                stage='cos_upload', diagnostic=diagnostic)

                diagnostic = upload_test_bytes(credential, on_send=before_send)
                state['uploaded'] = True
                state['uploadRequestSent'] = True
                self.record(step, 'uploaded', name=name, parentId=parent, mediaId=media_id,
                            stage='cos_upload', diagnostic=diagnostic if isinstance(diagnostic, dict) else {})
            stage = 'add_knowledge'
            state['addRequestSent'] = True
            self.record(step, 'ingest_pending', name=name, parentId=parent, mediaId=media_id, stage=stage)
            result = self.client._request('add_knowledge', {'knowledge_base_id': self.data['libraryId'],
                'folder_id': parent, 'media_id': media_id, 'media_type': 13, 'title': name,
                'file_info': {'cos_key': state['cosKey'], 'file_size': len(TEST_BYTES),
                              'last_modify_time': int(time.time()), 'file_name': name}}, mutation=True)
            stage = 'ingest_readback'
            if result.get('media_id') != media_id or not any(row['mediaId'] == media_id for row in self.rows(parent)):
                raise ImaError('OUTCOME_UNKNOWN')
        except ImaError as error:
            diagnostic = error.diagnostic
            if not diagnostic and stage in ('create_media', 'add_knowledge'):
                diagnostic = getattr(self.client, 'last_diagnostic', {})
            if not isinstance(diagnostic, dict):
                diagnostic = {}
            self.record(step, error.code, name=name, parentId=parent, mediaId=media_id,
                        stage=diagnostic.get('stage', stage), diagnostic=diagnostic,
                        upstreamCode=error.upstream_code)
            raise
        self.data['objects']['file'] = {'id': media_id, 'kind': 'file', 'parentId': parent,
                                        'name': name, 'runId': self.data['runId']}
        self.record(step, 'passed', mediaId=media_id, name=name, uploaded=True, ingested=True,
                    parsing='unverified', stage='ingest_readback')
        return media_id

    def rename_child(self):
        root = self.owned('root', 'folder')['id']
        child = self.owned('child', 'folder')
        name = self.data['prefix'] + '-child-renamed'
        before = self.rows(child['id'])
        expected_ids = sorted((row['kind'], str(row['folderId'] or row['mediaId'])) for row in before)
        step = 'rename-folder'
        rows = self.rows(root)
        if self.data['steps'].get(step):
            if not any(row['folderId'] == child['id'] and row['name'] == name for row in rows):
                raise ImaError('OUTCOME_UNKNOWN')
        else:
            self.record(step, 'pending', objectId=child['id'], parentId=root)
            self.client._request('rename_knowledge', {'knowledge_base_id': self.data['libraryId'],
                                                     'media_id': child['id'], 'name': name}, mutation=True)
        rows = self.rows(root)
        after = self.rows(child['id'])
        if not any(row['folderId'] == child['id'] and row['name'] == name and row['parentFolderId'] == root for row in rows):
            raise ImaError('OUTCOME_UNKNOWN')
        if sorted((row['kind'], str(row['folderId'] or row['mediaId'])) for row in after) != expected_ids:
            raise ImaError('OUTCOME_UNKNOWN')
        child['name'] = name
        self.record(step, 'passed', objectId=child['id'], parentId=root, childrenPreserved=True,
                    childObjectIds=[row['folderId'] or row['mediaId'] for row in after], undocumented=True)

    def move(self, destination_label, step):
        obj = self.owned('file', 'file')
        destination = self.owned(destination_label, 'folder')['id']
        source = obj['parentId']
        at_destination = any(row['mediaId'] == obj['id'] for row in self.rows(destination))
        prior = self.data['steps'].get(step)
        ret_code = prior.get('retCode') if prior else None
        if prior:
            if not at_destination or source != destination and any(row['mediaId'] == obj['id'] for row in self.rows(source)):
                raise ImaError('OUTCOME_UNKNOWN')
        else:
            if at_destination or not any(row['mediaId'] == obj['id'] for row in self.rows(source)):
                raise ImaError('INVALID_REQUEST')
            self.record(step, 'pending', mediaId=obj['id'], sourceId=source, destinationId=destination)
            result = self.client._request('move_knowledge', {'src_knowledge_base_id': self.data['libraryId'],
                'dst_knowledge_base_id': self.data['libraryId'], 'dst_folder_id': destination,
                'infos': [{'media_id': obj['id']}]}, mutation=True)
            item = result.get('move_results', {}).get(obj['id'], {})
            ret_code = item.get('ret_code')
            if type(item.get('ret_code')) is not int or item['ret_code'] != 0:
                self.record(step, 'item_failed', mediaId=obj['id'], retCode=item.get('ret_code') if type(item.get('ret_code')) is int else None)
                raise ImaError('OUTCOME_UNKNOWN')
        if not any(row['mediaId'] == obj['id'] for row in self.rows(destination)) or source != destination and any(row['mediaId'] == obj['id'] for row in self.rows(source)):
            raise ImaError('OUTCOME_UNKNOWN')
        obj['parentId'] = destination
        self.record(step, 'passed', mediaId=obj['id'], sourceId=source, destinationId=destination,
                    retCode=ret_code, undocumented=True)

    def finish_report(self):
        # Cloud deletion is deliberately absent until a key-authenticated API
        # contract is available. Never substitute moving into a trash folder.
        child = self.data['objects'].get('child')
        empty = None
        if child:
            try:
                empty = len(self.rows(child['id'])) == 0
            except ImaError:
                pass
        self.data['matrix']['delete_empty_folder'] = {'status': 'not_tested', 'reason': 'no_official_key_delete_contract',
                                                     'testChildConfirmedEmpty': empty}
        for name, steps in {'create_folder': ['create-root', 'create-child'],
                            'upload_and_ingest': ['upload-test-file'], 'rename_folder': ['rename-folder'],
                            'move_file_in_and_back': ['move-in', 'move-back']}.items():
            states = [self.data['steps'].get(step, {}).get('status', 'not_run') for step in steps]
            status = 'passed' if all(value == 'passed' for value in states) else 'not_run' if all(value == 'not_run' for value in states) else 'not_passed'
            self.data['matrix'][name] = {'status': status, 'steps': states}
        remaining = []
        for label, obj in self.data['objects'].items():
            try:
                rows = self.rows(obj['parentId'])
                present = any((row['folderId'] if obj['kind'] == 'folder' else row['mediaId']) == obj['id'] for row in rows)
                remaining.append({'label': label, 'kind': obj['kind'], 'id': obj['id'], 'parentId': obj['parentId'], 'present': present})
            except ImaError:
                remaining.append({'label': label, 'kind': obj['kind'], 'id': obj['id'], 'presence': 'unknown'})
        self.data['remaining'] = remaining
        for name, step in self.data['steps'].items():
            if step.get('status') != 'passed' and name.startswith('create-'):
                remaining.append({'operation': name, 'presence': 'unconfirmed_create', 'parentId': step.get('parentId'),
                                  'expectedName': self.data['prefix'] + '-' + name.removeprefix('create-')})
        upload = self.data['steps'].get('upload-test-file', {})
        if upload.get('mediaId') and 'file' not in self.data['objects']:
            remaining.append({'kind': 'media', 'id': upload['mediaId'], 'presence': 'allocated_or_upload_outcome_unconfirmed'})
        for obj in self.data.get('retiredMedia', []):
            remaining.append({'kind': 'media', 'id': obj['id'], 'presence': 'not_ingested_residual',
                              'uploadOutcome': obj['uploadOutcome']})
        self.save()
        result = {'checkedAt': now(), 'authentication': 'official_client_id_api_key', 'runId': self.data['runId'],
                  'libraryId': self.data['libraryId'], 'selectedParentId': self.data['selectedParentId'],
                  'matrix': self.data['matrix'], 'steps': self.data['steps'], 'remainingObjects': remaining,
                  'uploadHistory': self.data.get('uploadHistory', []),
                  'lastError': self.data.get('lastError'),
                  'cleanup': 'Cloud delete not called. Only listed test objects remain; use ima to remove the test subtree after review.'}
        # beforeIds may contain original user object IDs, only keep test-object proofs.
        result['steps'] = {key: {k: v for k, v in value.items() if k != 'beforeIds'} for key, value in result['steps'].items()}
        self.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.report.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        temporary.replace(self.report)
        return result

    def run(self, *, allocation=None):
        if self.data.get('finished'):
            return self.finish_report()
        self.data.pop('lastError', None)
        try:
            root = self.folder('root', self.data['selectedParentId'])
            self.folder('child', root)
            file_id = self.upload(root, allocation=allocation)
            # Move into the child before rename so the test checks contents too.
            if self.data['steps'].get('move-back', {}).get('status') != 'passed':
                self.move('child', 'move-in')
                try:
                    self.rename_child()
                except ImaError as error:
                    self.record('rename-folder', error.code, undocumented=True)
                self.move('root', 'move-back')
            self.data['matrix']['media_access'] = self.client.media_access(file_id, probe=True)
            hits = self.client.search(self.data['libraryId'], self.data['prefix'])
            self.data['matrix']['search'] = {'requestSucceeded': True,
                                           'testFileFound': any(row['mediaId'] == file_id for row in hits),
                                           'parsingComplete': False}
            self.data['finished'] = True
        except ImaError as error:
            self.data['lastError'] = {'code': error.code, 'upstreamCode': error.upstream_code,
                                      'diagnostic': error.diagnostic,
                                      'stage': self.data['steps'].get('upload-test-file', {}).get('stage')}
            print(str(error), '诊断码', error.code, '官方错误码', error.upstream_code)
        except Exception:
            self.data['lastError'] = {'code': 'LOCAL_ERROR'}
        return self.finish_report()


def choose(items, heading):
    print('\n' + heading)
    for number, item in enumerate(items, 1):
        print(f"  {number}. {item.get('path') or item['name']}")
    while True:
        value = input('输入序号，或输入q结束：').strip()
        if value.lower() == 'q':
            raise KeyboardInterrupt()
        if value.isdecimal() and 1 <= int(value) <= len(items):
            return items[int(value) - 1]
        print(f'请输入上方1到{len(items)}之间的序号，再按回车。输入q可结束。')


def save_read_report(report):
    path = REPORT.parent / 'read-verification.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    report['checkedAt'] = now()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(path)


def main():
    service = ImaService()
    print('ima连接与只读浏览 · 第一阶段')
    print('密钥只在本机输入，以Windows DPAPI加密保存。请勿发进聊天或截图。')
    if not sys.stdin.isatty():
        print('请双击tools/ima-connect.cmd，在本机终端操作。')
        return
    try:
        status = service.status()
    except ImaError:
        print('旧配置无法读取，原文件保留。可以填写新密钥验证后替换。')
        status = {'configured': False}
    if not status['configured'] or input('已存在加密配置。回车复用，输入c重新配置：').strip().lower() == 'c':
        print('请在 https://ima.qq.com/agent-interface 获取本工作台自己的Client ID/API Key。')
        # No echo fallback is allowed if the terminal cannot protect the entry.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            client_id = getpass.getpass('Client ID（隐藏输入）：').strip()
            api_key = getpass.getpass('API Key（隐藏输入）：').strip()
        try:
            result = service.configure(client_id, api_key)
        finally:
            client_id = api_key = None
        print('凭据已通过真实列库验证并加密保存，可添加内容的知识库数：', result['libraryCount'])
    client = service.client()
    libraries = client.libraries()
    read_report = {'configured': True, 'authentication': 'official_client_id_api_key',
                   'libraryCount': len(libraries), 'treeComplete': False, 'searchRun': False,
                   'mediaAccess': None, 'cloudWrites': False,
                   'checks': {'libraries': {'status': 'passed', 'checkedAt': now()},
                              'tree': {'status': 'not_run'}, 'search': {'status': 'not_run'},
                              'mediaAccess': {'status': 'not_run'}}}
    save_read_report(read_report)
    if not libraries:
        print('认证请求成功，当前没有可添加内容的知识库。请在ima检查库权限。')
        return
    library = choose(libraries, '选择知识库（只读列出可添加内容的库）')
    read_report['selectedLibraryId'] = library['libraryId']
    try:
        rows = client.tree(library['libraryId'])
    except ImaError as error:
        read_report['checks']['tree'] = {'status': 'failed', 'code': error.code,
                                         'upstreamCode': error.upstream_code, 'checkedAt': now()}
        save_read_report(read_report)
        raise
    read_report.update(treeComplete=True,
                       counts={kind: sum(row['kind'] == kind for row in rows) for kind in ('folder', 'file', 'unknown')})
    read_report['checks']['tree'] = {'status': 'passed', 'checkedAt': now()}
    save_read_report(read_report)
    print(f'完整分页与递归读取完成，共{len(rows)}条，包含文件夹与未知条目。')
    for row in rows:
        print('[' + row['kind'] + '] ' + row['path'])
    query = input('可输入一个搜索词验证；直接回车跳过：').strip()
    hits = []
    if query:
        read_report['searchRun'] = True
        try:
            hits = client.search(library['libraryId'], query)
            read_report['checks']['search'] = {'status': 'passed', 'resultCount': len(hits), 'checkedAt': now()}
            print('搜索结果数：', len(hits))
            for row in hits:
                print('[' + row['kind'] + '] ' + row['name'])
        except ImaError as error:
            read_report['checks']['search'] = {'status': 'failed', 'code': error.code,
                                              'upstreamCode': error.upstream_code, 'checkedAt': now()}
            print('搜索未通过，已保留列库和目录验证，可继续独立的原文验证。诊断码', error.code,
                  '官方错误码', error.upstream_code)
        save_read_report(read_report)
    matched_files = [row for row in hits if row['kind'] == 'file']
    files = matched_files or [row for row in rows if row['kind'] == 'file']
    if files:
        print('\n接下来可检查一份原文件能否访问。输入y后，再输入文件前面的序号。')
        print('结果会以文字显示在这里；阅读全文请使用ima客户端。')
    if files and input('检查原文件访问？输入y继续，直接回车跳过：').strip().lower() == 'y':
        heading = '搜索命中的文件，请输入要检查的文件序号' if matched_files else '知识库全部文件，请输入要检查的文件序号'
        selected = choose(files, heading)
        print('正在检查：', selected.get('path') or selected['name'])
        try:
            media_check = client.media_access(selected['mediaId'], probe=True)
            read_report['mediaAccess'] = media_check
            read_report['checks']['mediaAccess'] = {
                'status': 'passed' if media_check['readVerified'] else 'limited', 'checkedAt': now()}
            if media_check['readVerified']:
                print('原文件访问检查通过：本机已成功读取一小段原文。阅读全文请使用ima客户端。')
            elif media_check['urlProvided']:
                print('ima提供了原文入口，本机访问尚未确认。请在ima客户端查看。')
            else:
                print('ima未提供这份资料的原文入口。请在ima客户端查看。')
        except ImaError as error:
            read_report['checks']['mediaAccess'] = {'status': 'failed', 'code': error.code,
                                                   'upstreamCode': error.upstream_code, 'checkedAt': now()}
            print('原文验证未通过，前面验证结果已保留。诊断码', error.code, '官方错误码', error.upstream_code)
        save_read_report(read_report)
    print('\n连接、搜索及原文检查的已完成结果已保存。')
    print('下一步为可选的目录管理测试，用来确认工作台能否在ima新建文件夹、改名和移动文件。')
    print('选择位置后，会新建专用测试文件夹和一份无敏感小TXT，只操作本轮测试对象。')
    print('测试对象会保留供核对，之后可在ima清理；空目录删除接口尚待核实。')
    if input('输入1进入目录管理测试；直接回车结束：').strip().upper() not in ('1', 'TEST'):
        return
    parents = [{'folderId': library['libraryId'], 'name': '当前知识库根目录'}]
    parents += [row for row in rows if row['kind'] == 'folder']
    parent = choose(parents, '选择测试父目录，只在其下新建本轮专用子树')
    print('已选择测试位置：', parent.get('path') or parent['name'])
    print('将在此位置新建专用测试父子目录和一份无敏感小TXT。')
    if input('输入“确认”开始测试，直接回车取消：').strip().upper() not in ('确认', 'YES'):
        return
    with exclusive_probe():
        probe = OwnedProbe(client, library['libraryId'], parent['folderId'])
        report = probe.run()
    print('能力矩阵：', json.dumps(report['matrix'], ensure_ascii=False))
    print('测试对象留存：', json.dumps(report['remainingObjects'], ensure_ascii=False))
    print('报告已保存：', REPORT)


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\n操作已停止；已发出的云端请求可能完成，重新打开时先核对报告和原测试对象。')
    except ImaError as error:
        print(str(error), '诊断码', error.code, '官方错误码', error.upstream_code)
    except Exception:
        print('本机工具未完成，未输出密钥或原始异常。请保留现有配置和测试报告后核对。')
