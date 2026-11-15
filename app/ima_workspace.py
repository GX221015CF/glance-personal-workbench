"""Authenticated course links, complete-library comparisons and durable UI jobs."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import threading
import time
import traceback
import unicodedata

import canvas_bridge as canvas
from ima_bridge import ImaError, ImaService, discard_small_body, envelope, identifier, now, store_lock
from ima_course_sync import CanvasSource, CourseSync, SyncError, SyncStore, STORE, RECOVERY, file_format
from ima_probe import read_ledger

ACTIVE = {'queued', 'running', 'checking'}
RETRYABLE = {'failed', 'not_run'}
UNCERTAIN = {'OUTCOME_UNKNOWN', 'COS_UPLOAD_UNKNOWN', 'SAVE_FAILED'}
WORKSPACE_VERSION = '20261112-material-groups1'
NAME_RULE_VERSION = 2
RETRY_CODES = {'NETWORK_ERROR', 'RATE_LIMITED', 'FORBIDDEN', 'AUTH_REQUIRED', 'LOGIN_REQUIRED',
               'COS_CREDENTIAL_EXPIRED', 'COS_CREDENTIAL_NOT_YET_VALID', 'COS_CREDENTIAL_INVALID',
               'COS_UPLOAD_REJECTED', 'COS_UPLOAD_FAILED', 'DOWNLOAD_FAILED', 'DOWNLOAD_FORBIDDEN', 'UPSTREAM_ERROR'}


def numeric(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 15:
        raise ImaError('INVALID_REQUEST')
    return value


def safe_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or any(ord(c) < 32 for c in value):
        raise ImaError('INVALID_REQUEST')
    return value.strip()


def normalized(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


def failure(error):
    return {'code': getattr(error, 'code', 'LOCAL_ERROR'),
            'upstreamCode': getattr(error, 'upstream_code', None), 'checkedAt': now(),
            'memberAttempts': getattr(error, 'member_attempts', [])}


class WorkspaceSource(CanvasSource):
    @staticmethod
    def member(row, course_id, course_name):
        name = canvas.material_name(row) or '未命名资料'
        item = {'courseId': course_id, 'courseName': course_name, 'fileId': numeric(str(row['id'])),
                'fileName': name, 'size': row.get('size'), 'contentType': row.get('content-type', ''),
                'createdAt': row.get('created_at'), 'updatedAt': row.get('updated_at'),
                'modifiedAt': row.get('modified_at'), 'locked': canvas.file_locked(row),
                'hidden': bool(row.get('hidden_for_user')), 'unlockAt': row.get('unlock_at'), 'lockAt': row.get('lock_at'),
                'sources': row.get('sourceEntries', []), 'metadataUnavailable': bool(row.get('metadataUnavailable'))}
        try:
            item.update(file_format(name, item['size']), supported=not item['locked'] and not item['metadataUnavailable'])
        except SyncError:
            item.update(supported=False, formatError='UNSUPPORTED_FILE')
        return item

    def course(self, course_id):
        numeric(course_id)
        courses = canvas.read_collection(self.session, '/api/v1/courses', {
            'enrollment_state': 'active', 'enrollment_type': 'student', 'per_page': 100})
        selected = [row for row in courses if str(row['id']) == course_id]
        if len(selected) != 1:
            raise SyncError('SOURCE_CONFLICT')
        files = canvas.read_collection(self.session, f'/api/v1/courses/{course_id}/files', {'per_page': 100})
        assignments = canvas.read_collection(self.session, f'/api/v1/courses/{course_id}/assignments', {'per_page': 100})
        saved = canvas.load_private('connection.dpapi') or {}
        files = canvas.course_materials(self.session, course_id, assignments, files, saved.get('snapshot', {}).get('files', []))
        result = []
        for row in files:
            members = [self.member(member, course_id, selected[0]['name']) for member in row['members']]
            item = copy.deepcopy(next((member for member in members if member['supported']), members[0]))
            item.update(groupId=row['groupId'], memberIds=row['memberIds'], members=members,
                        sources=row['sourceEntries'], materialVersion=canvas.MATERIAL_VERSION)
            for member in row['members']:
                self.download_urls[str(member['id'])] = member.get('url', '')
            result.append(item)
        return result

    def select(self, pairs):
        result = []
        for course_id in dict.fromkeys(row['courseId'] for row in pairs):
            files = self.course(course_id)
            for pair in [row for row in pairs if row['courseId'] == course_id]:
                matches = [row for row in files if pair['fileId'] in row.get('memberIds', [row['fileId']])]
                if len(matches) != 1:
                    raise SyncError('SOURCE_CONFLICT')
                file = matches[0]
                if any(row.get('groupId') == file['groupId'] for row in result):
                    continue
                member = next((row for row in file['members'] if row['fileId'] == pair['fileId'] and row['supported']), None)
                result.append({**file, **(member or {}), 'sources': file['sources']})
        return result

    def download(self, source):
        members = source.get('members')
        if not members:
            return super().download(source)
        usable = [member for member in members if member['supported']]
        usable.sort(key=lambda member: (member['fileId'] != source['fileId'], int(member['fileId'])))
        attempts = []
        for member in usable:
            selected = {**source, **member, 'sources': source['sources']}
            try:
                payload, checked = super().download(selected)
                return payload, {**checked, 'actualFileId': selected['fileId'], 'source': selected, 'memberAttempts': attempts}
            except SyncError as error:
                attempts.append({'fileId': member['fileId'], 'code': error.code})
        error = SyncError(attempts[-1]['code'] if attempts else 'SOURCE_METADATA_UNAVAILABLE' if source.get('metadataUnavailable')
                          else 'SOURCE_LOCKED' if source.get('locked') else 'UNSUPPORTED_FILE')
        error.member_attempts = attempts
        raise error


class ImaWorkspace:
    def __init__(self, *, path=STORE, recovery=RECOVERY, client_factory=None, source_factory=WorkspaceSource):
        self.path, self.recovery = path, recovery
        self.client_factory = client_factory or ImaService().client
        self.source_factory = source_factory
        self.worker_id = secrets.token_hex(16)
        self.worker = None

    def store(self, *, read_only=False):
        store = SyncStore(self.path, self.recovery, read_only=read_only)
        for key in ('links', 'comparisons', 'ignoredCandidates', 'previews', 'operations', 'unresolvedJobs', 'confirmations', 'nameMatches'):
            store.data.setdefault(key, {})
        batch = store.data.get('batch')
        if batch and batch['courseId'] not in store.data['links']:
            store.data['links'][batch['courseId']] = {
                'libraryId': batch['libraryId'], 'folderId': batch['targetFolderId'], 'origin': 'stage_b_mapping'}
        return store

    def recover_job(self):
        current = self.store(read_only=True).data.get('job')
        if not current or current['phase'] not in ACTIVE:
            return
        if (current.get('workerId') == self.worker_id and self.worker
                and (self.worker.is_alive() or self.worker.ident is None)):
            return
        try:
            with store_lock(self.path):
                store = self.store()
                job = store.data.get('job')
                worker_pending = bool(self.worker and (self.worker.is_alive() or self.worker.ident is None))
                if job and job['phase'] in ACTIVE and (job.get('workerId') != self.worker_id or not worker_pending):
                    job['phase'] = 'interrupted'
                    for row in job['rows']:
                        if row['status'] == 'running':
                            row.update(status='unknown', error={'code': 'OUTCOME_UNKNOWN'})
                        elif row['status'] == 'queued':
                            row['status'] = 'not_run'
                    store.save()
        except ImaError as error:
            if error.code != 'SYNC_BUSY':
                raise

    def view(self, course_id=''):
        if course_id:
            numeric(course_id)
        self.recover_job()
        data = self.store(read_only=True).data
        # Old snapshots remain readable, but must be checked under the current rule.
        for comparison in data['comparisons'].values():
            if comparison.get('nameRuleVersion') != NAME_RULE_VERSION:
                comparison.update(stale=True, error={'code': 'RULE_CHANGED'})
        link = data['links'].get(course_id)
        key = course_id + ':' + link['libraryId'] if link else ''
        return {'workspaceVersion': WORKSPACE_VERSION, 'nameRuleVersion': NAME_RULE_VERSION,
                'configured': ImaService().status()['configured'] if self.path == STORE else True,
                'links': data['links'], 'comparison': data['comparisons'].get(key),
                'comparisons': {course: data['comparisons'][course + ':' + row['libraryId']]
                                for course, row in data['links'].items() if course + ':' + row['libraryId'] in data['comparisons']},
                'job': data.get('job'), 'formats': sorted(__import__('ima_bridge').FILE_FORMATS),
                'unresolvedJobs': list(data['unresolvedJobs'].values()),
                'maxFileBytes': 20 * 1024 * 1024,
                'capabilities': {'createFolder': True, 'renameFolder': True, 'moveFiles': True,
                                 'deleteFolder': False, 'managementContract': 'undocumented_verified'}}

    def libraries(self):
        return {'libraries': self.client_factory().libraries()}

    def tree(self, library_id):
        identifier(library_id)
        entries = self.client_factory().tree(library_id)
        try:
            ledger = read_ledger()
        except ImaError:
            ledger = None
        test_ids = set()
        if ledger and ledger['libraryId'] == library_id:
            test_ids = {item['id'] for item in ledger['objects'].values()
                        if item['kind'] == 'folder' and item.get('runId') == ledger['runId']}
        for _ in range(len(entries) + 1):
            descendants = {row['folderId'] for row in entries if row['kind'] == 'folder' and row['parentFolderId'] in test_ids}
            if descendants <= test_ids:
                break
            test_ids.update(descendants)
        for row in entries:
            row['isTestDirectory'] = row['kind'] == 'folder' and row['folderId'] in test_ids
        return {'libraryId': library_id, 'entries': entries,
                'complete': True, 'checkedAt': now()}

    def target(self, client, library_id, folder_id):
        identifier(library_id)
        identifier(folder_id)
        libraries = [row for row in client.libraries() if row['libraryId'] == library_id]
        if len(libraries) != 1:
            raise SyncError('TARGET_CONFLICT')
        tree = client.tree(library_id)
        if folder_id == library_id:
            path = libraries[0]['name']
        else:
            rows = [row for row in tree if row['kind'] == 'folder' and row['folderId'] == folder_id]
            if len(rows) != 1:
                raise SyncError('TARGET_MISSING')
            path = libraries[0]['name'] + rows[0]['path']
        return {'libraryId': library_id, 'targetFolderId': folder_id, 'path': path}, tree

    def link(self, course_id, library_id, folder_id):
        numeric(course_id)
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            target, _ = self.target(self.client_factory(), library_id, folder_id)
            store.data['links'][course_id] = {'libraryId': library_id, 'folderId': folder_id,
                                             'path': target['path'], 'savedAt': now()}
            store.save()
        return self.view(course_id)

    @staticmethod
    def ensure_idle(store):
        if store.data.get('job', {}).get('phase') in ACTIVE:
            raise ImaError('SYNC_BUSY')

    def compare(self, course_id, *, _job_id=None):
        numeric(course_id)
        with store_lock(self.path):
            store = self.store()
            if not _job_id or store.data.get('job', {}).get('id') != _job_id:
                self.ensure_idle(store)
            link = store.data['links'].get(course_id)
            if not link:
                raise SyncError('TARGET_MISSING')
            key = course_id + ':' + link['libraryId']
            source = None
            try:
                source = self.source_factory()
                files = source.course(course_id)
                tree = self.client_factory().tree(link['libraryId'])
                rows = self.compare_rows(store, source, files, tree, link, key)
                result = self.comparison_result(course_id, link['libraryId'], files, tree, rows)
                store.data['comparisons'][key] = result
            except (ImaError, SyncError, canvas.CanvasError) as error:
                previous = store.data['comparisons'].get(key)
                result = copy.deepcopy(previous) if previous else {'courseId': course_id, 'libraryId': link['libraryId'],
                          'rows': [], 'checkedAt': None, 'complete': False}
                result.update(stale=True, lastAttemptAt=now(), error=failure(error))
                store.data['comparisons'][key] = result
            finally:
                if source:
                    source.session.close()
            store.save()
            return result

    @staticmethod
    def comparison_result(course_id, library_id, files, tree, rows):
        return {'courseId': course_id, 'courseName': files[0]['courseName'] if files else '',
                'libraryId': library_id, 'checkedAt': now(), 'checkId': secrets.token_hex(16),
                'complete': True, 'stale': False, 'nameRuleVersion': NAME_RULE_VERSION,
                'rows': rows, 'libraryEntryCount': len(tree),
                'counts': {state: sum(row['status'] == state for row in rows)
                           for state in ('exists', 'unsynced', 'review', 'failed', 'new_version', 'anomaly')}}

    def compare_rows(self, store, source, files, tree, link, cache_key):
        engine = CourseSync(None, source, store)
        rows = []
        for file in files:
            key = engine.item_key(file, link['libraryId'], tree)
            item = store.data['items'].get(key)
            item_tree = tree
            if file.get('groupId'):
                for _, entry in engine.mapping_items(file, link['libraryId']):
                    entry['source'].update(groupId=file['groupId'], memberIds=file['memberIds'])
            row = {'fileId': file['fileId'], 'courseId': file['courseId'], 'fileName': file['fileName'],
                   'size': file['size'], 'createdAt': file['createdAt'], 'supported': file['supported'],
                   'sourceUpdatedAt': file.get('updatedAt'),
                   'sourceModifiedAt': file.get('modifiedAt'),
                   'groupId': file.get('groupId'), 'memberIds': file.get('memberIds', [file['fileId']]),
                   'status': 'unsynced', 'candidates': []}
            row['savedAssociations'] = [{'sourceFileId': entry['source']['fileId'], 'mediaId': entry.get('mediaId'),
                                         'association': entry.get('association', 'content_mapping'),
                                         'path': (engine.locate(entry, tree) or {}).get('path'), 'stage': entry['stage']}
                                        for _, entry in engine.mapping_items(file, link['libraryId'])]
            if file.get('metadataUnavailable'):
                row.update(status='review', error={'code': 'SOURCE_METADATA_UNAVAILABLE'})
                rows.append(row)
                continue
            if item and item.get('mediaId'):
                remote = engine.locate(item, item_tree)
                if remote:
                    tracked = engine.source_for_item(file, item)
                    changed = tracked['fileId'] == item['source']['fileId'] and tracked.get('supported', True) and any(
                        item['source'].get(field) != tracked.get(field) for field in ('size', 'updatedAt', 'modifiedAt'))
                    if changed:
                        try:
                            _, download = source.download(tracked)
                            if download.get('actualFileId', tracked['fileId']) != item['source']['fileId']:
                                row['status'] = 'exists'
                            elif download['sha256'] != item['sha256']:
                                row.update(status='new_version', previousSha256=item['sha256'], latestSha256=download['sha256'],
                                           changedSources=[{'fileId': tracked['fileId'], 'mediaId': item['mediaId']}])
                            else:
                                item['source'] = download.get('source', tracked)
                                row['status'] = 'exists'
                        except (ImaError, SyncError, canvas.CanvasError) as error:
                            row.update(status='review', error=failure(error))
                    else:
                        row['status'] = 'exists'
                    row.update(mediaId=item['mediaId'], path=remote['path'],
                               association=item.get('association', 'content_mapping'), sha256=item['sha256'])
                    item.update(actualFolderId=remote['parentFolderId'], remotePath=remote['path'], lastVerifiedAt=now())
                else:
                    code = 'REMOTE_MISSING' if item['stage'] == 'committed' else item.get('error', {}).get('code', 'OUTCOME_UNKNOWN')
                    row.update(status='anomaly' if item['stage'] == 'committed' else 'review' if code in UNCERTAIN else 'failed',
                               error={'code': 'REMOTE_MISSING' if item['stage'] == 'committed' else item.get('error', {}).get('code', 'OUTCOME_UNKNOWN')})
            else:
                candidates = engine.name_matches(file, tree)
                if candidates:
                    evidence = engine.record_name_match(file, link['libraryId'], candidates)
                    row.update(status='exists', association='user_name_rule', candidates=evidence['candidates'],
                               paths=[entry['path'] for entry in candidates])
                    if len(candidates) == 1:
                        row.update(mediaId=candidates[0]['mediaId'], path=candidates[0]['path'])
                elif item and item.get('error'):
                    row.update(status='failed', error=item['error'])
                if not candidates:
                    store.data['nameMatches'].pop(file['courseId'] + ':' + file.get('groupId', file['fileId']) + ':' + link['libraryId'], None)
            for other_key, other in engine.mapping_items(file, link['libraryId']):
                if other_key == key or other['stage'] != 'committed' or not engine.locate(other, tree):
                    continue
                tracked = engine.source_for_item(file, other)
                changed = tracked['fileId'] == other['source']['fileId'] and tracked.get('supported', True) and any(
                    other['source'].get(field) != tracked.get(field) for field in ('size', 'updatedAt', 'modifiedAt'))
                if not changed:
                    continue
                try:
                    _, download = source.download(tracked)
                    if download.get('actualFileId', tracked['fileId']) == other['source']['fileId']:
                        if download['sha256'] != other['sha256']:
                            row.update(status='new_version', previousSha256=other['sha256'], latestSha256=download['sha256'])
                            row.setdefault('changedSources', []).append({'fileId': tracked['fileId'], 'mediaId': other['mediaId']})
                        else:
                            other['source'] = download.get('source', tracked)
                except (ImaError, SyncError, canvas.CanvasError) as error:
                    if row['status'] != 'new_version':
                        row.update(status='review', error=failure(error))
            job = store.data.get('job') or {}
            if row['status'] == 'unsynced' and job.get('target', {}).get('libraryId') == link['libraryId']:
                failed = next((entry for entry in job['rows'] if entry['status'] == 'failed'
                               and entry['source'].get('courseId') == file['courseId']
                               and (entry['source'].get('fileId') in row['memberIds'] or file.get('groupId')
                                    and entry['source'].get('groupId') == file['groupId'])), None)
                if failed:
                    row.update(status='failed', error=failed['error'])
            rows.append(row)
        return rows

    def confirm(self, course_id, file_id, library_id, media_id, check_id, same):
        numeric(course_id)
        numeric(file_id)
        if type(same) is not bool:
            raise ImaError('INVALID_REQUEST')
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            key = course_id + ':' + identifier(library_id)
            if store.data['links'].get(course_id, {}).get('libraryId') != library_id:
                raise ImaError('INVALID_REQUEST')
            comparison = store.data['comparisons'].get(key, {})
            rows = [row for row in comparison.get('rows', []) if row['fileId'] == file_id]
            if comparison.get('checkId') != check_id or comparison.get('stale') or len(rows) != 1:
                raise ImaError('INVALID_REQUEST')
            if not any(row['mediaId'] == media_id for row in rows[0]['candidates']):
                raise ImaError('INVALID_REQUEST')
            client = self.client_factory()
            remote = [row for row in client.tree(library_id) if row['kind'] == 'file' and row['mediaId'] == media_id]
            if len(remote) != 1:
                raise SyncError('REMOTE_MISSING')
            if not same:
                ignored = store.data['ignoredCandidates'].setdefault(key, {}).setdefault(file_id, [])
                if media_id not in ignored:
                    ignored.append(media_id)
            else:
                source = self.source_factory()
                try:
                    file = source.select([{'courseId': course_id, 'fileId': file_id}])[0]
                    expected = rows[0]
                    if not self.source_matches(file, expected):
                        raise SyncError('SOURCE_CONFLICT')
                    self.confirm_existing(store, source, file, remote[0], library_id)
                finally:
                    source.session.close()
            store.save()
        return self.compare(course_id)

    @staticmethod
    def source_matches(file, expected):
        return (file['fileName'] == expected['fileName'] and file['size'] == expected['size']
                and file.get('updatedAt') == expected.get('sourceUpdatedAt', expected.get('updatedAt'))
                and ('sourceModifiedAt' not in expected or file.get('modifiedAt') == expected['sourceModifiedAt']))

    def confirm_existing(self, store, source, file, remote, library_id):
        if not file.get('supported') or file.get('metadataUnavailable'):
            raise SyncError('UNSUPPORTED_FILE')
        if normalized(file['fileName']) != normalized(remote['name']):
            raise SyncError('SOURCE_CONFLICT')
        engine = CourseSync(None, source, store)
        mapping_key = engine.item_key(file, library_id)
        existing = store.data['items'].get(mapping_key)
        if existing and existing.get('mediaId') and existing['mediaId'] != remote['mediaId']:
            raise SyncError('NAME_CONFLICT')
        _, download = source.download(file)
        if existing and existing.get('stage') == 'committed':
            if existing['sha256'] != download['sha256']:
                raise SyncError('CONTENT_CHANGED')
            return 'skipped'
        store.data['items'][mapping_key] = {
            'source': file, 'sha256': download['sha256'], 'size': download['size'],
            'libraryId': library_id, 'mediaId': remote['mediaId'],
            'initialTargetFolderId': remote['parentFolderId'], 'actualFolderId': remote['parentFolderId'],
            'stage': 'committed', 'association': 'user_confirmed', 'confirmedAt': now(),
            'remotePath': remote['path'], 'remoteName': remote['name'], 'lastSuccessAt': now(),
            'lastVerifiedAt': now(), 'retiredMedia': [],
            'stages': {'download': download, 'upload': {'status': 'existing_manual_file'},
                       'ingest': {'status': 'confirmed_existing'}, 'parsing': {'status': 'unverified'}}}
        return 'confirmed'

    def confirm_batch(self, course_id, library_id, check_id, selections, request_id):
        numeric(course_id)
        identifier(library_id)
        identifier(request_id)
        if not isinstance(selections, list) or not 1 <= len(selections) <= 500:
            raise ImaError('INVALID_REQUEST')
        file_ids = []
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {'fileId', 'mediaId'}:
                raise ImaError('INVALID_REQUEST')
            file_ids.append(numeric(selection['fileId']))
            if selection['mediaId'] is not None:
                identifier(selection['mediaId'])
        if len(set(file_ids)) != len(file_ids):
            raise ImaError('INVALID_REQUEST')
        signature = hashlib.sha256(json.dumps([course_id, library_id, check_id, selections], sort_keys=True).encode()).hexdigest()
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            receipt = store.data['confirmations'].get(request_id)
            if receipt and receipt['signature'] != signature:
                raise ImaError('INVALID_REQUEST')
            if receipt and receipt.get('complete'):
                return receipt
            if store.data['links'].get(course_id, {}).get('libraryId') != library_id:
                raise ImaError('INVALID_REQUEST')
            comparison = store.data['comparisons'].get(course_id + ':' + library_id, {})
            if receipt and receipt.get('expected'):
                expected = receipt['expected']
            else:
                if comparison.get('checkId') != check_id or comparison.get('stale') or not comparison.get('complete'):
                    raise ImaError('INVALID_REQUEST')
                expected = {row['fileId']: row for row in comparison['rows']}
            if any(file_id not in expected for file_id in file_ids):
                raise ImaError('INVALID_REQUEST')
            receipt = receipt or {'requestId': request_id, 'signature': signature, 'courseId': course_id,
                                  'libraryId': library_id, 'rows': [], 'complete': False, 'createdAt': now(),
                                  'expected': {file_id: copy.deepcopy(expected[file_id]) for file_id in file_ids}}
            store.data['confirmations'][request_id] = receipt
            store.save()
            source = self.source_factory()
            try:
                files = source.course(course_id)
                by_id = {file['fileId']: file for file in files}
                client = self.client_factory()
                tree = client.tree(library_id)
                done = {row['fileId'] for row in receipt['rows']}
                for selection in selections:
                    file_id = selection['fileId']
                    if file_id in done:
                        continue
                    row = {'fileId': file_id, 'status': 'failed'}
                    try:
                        file = by_id.get(file_id)
                        if not file or not self.source_matches(file, expected[file_id]):
                            raise SyncError('SOURCE_CONFLICT')
                        engine = CourseSync(None, source, store)
                        item = store.data['items'].get(engine.item_key(file, library_id))
                        if item and item.get('stage') == 'committed':
                            if selection['mediaId'] and selection['mediaId'] != item['mediaId']:
                                raise ImaError('INVALID_REQUEST')
                            candidates = [entry for entry in tree if entry.get('mediaId') == item['mediaId'] and entry['kind'] == 'file']
                        else:
                            allowed = expected[file_id].get('candidates', [])
                            media_id = selection['mediaId'] or (allowed[0]['mediaId'] if len(allowed) == 1 else None)
                            if media_id is None:
                                row.update(status='needs_selection', candidates=allowed)
                                receipt['rows'].append(row)
                                store.save()
                                continue
                            if not any(entry['mediaId'] == media_id for entry in allowed):
                                raise ImaError('INVALID_REQUEST')
                            candidates = [entry for entry in tree if entry.get('mediaId') == media_id and entry['kind'] == 'file']
                        if len(candidates) != 1:
                            raise SyncError('REMOTE_MISSING')
                        row.update(status=self.confirm_existing(store, source, file, candidates[0], library_id), mediaId=candidates[0]['mediaId'])
                    except (ImaError, SyncError, canvas.CanvasError) as error:
                        row['error'] = failure(error)
                    receipt['rows'].append(row)
                    store.save()
                link = store.data['links'][course_id]
                cache_key = course_id + ':' + library_id
                tree = client.tree(library_id)
                rows = self.compare_rows(store, source, files, tree, link, cache_key)
                comparison = self.comparison_result(course_id, library_id, files, tree, rows)
                store.data['comparisons'][cache_key] = comparison
                receipt.update(complete=True, finishedAt=now(), comparison=comparison)
                store.save()
                return receipt
            finally:
                source.session.close()

    def preview(self, pairs, library_id, folder_id, new_version=False):
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 500 or type(new_version) is not bool:
            raise ImaError('INVALID_REQUEST')
        seen = set()
        for pair in pairs:
            if not isinstance(pair, dict) or set(pair) != {'courseId', 'fileId'}:
                raise ImaError('INVALID_REQUEST')
            key = numeric(pair['courseId']) + ':' + numeric(pair['fileId'])
            if key in seen:
                raise ImaError('INVALID_REQUEST')
            seen.add(key)
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            target, tree = self.target(self.client_factory(), library_id, folder_id)
            source = self.source_factory()
            try:
                files = list({(file['courseId'], file.get('groupId', file['fileId'])): file for file in source.select(pairs)}.values())
                if any(not file['supported'] for file in files):
                    rejected = next(file for file in files if not file['supported'])
                    raise SyncError('SOURCE_METADATA_UNAVAILABLE' if rejected.get('metadataUnavailable')
                                    else 'SOURCE_LOCKED' if rejected.get('locked') else 'UNSUPPORTED_FILE')
                token = secrets.token_hex(16)
                engine = CourseSync(None, source, store)
                conflicts = []
                for index, file in enumerate(files):
                    comparison = store.data['comparisons'].get(file['courseId'] + ':' + library_id, {})
                    changed = next((row.get('changedSources') for row in comparison.get('rows', [])
                                    if row.get('groupId') and row['groupId'] == file.get('groupId')), None)
                    if new_version and changed:
                        file = engine.source_for_item(file, {'source': changed[0]})
                        files[index] = file
                    item = store.data['items'].get(engine.item_key(file, library_id))
                    if item and (item.get('stage') == 'committed' or item.get('mediaId') and engine.locate(item, tree)):
                        continue
                    candidates = [entry for entry in tree if entry['kind'] == 'file' and normalized(entry['name']) == normalized(file['fileName'])]
                    if candidates:
                        conflicts.append({'courseId': file['courseId'], 'fileId': file['fileId'], 'fileName': file['fileName'],
                                          'candidates': [{'mediaId': entry['mediaId'], 'name': entry['name'], 'path': entry['path']} for entry in candidates]})
                preview = {'token': token, 'createdAt': time.time(), 'sources': files, 'target': target,
                           'newVersion': new_version, 'action': 'upload', 'conflicts': conflicts,
                           'nameRuleVersion': NAME_RULE_VERSION}
                store.data['previews'] = {token: preview}
                store.save()
                return preview
            finally:
                source.session.close()

    def resolve_preview(self, token, decisions):
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            original = store.data['previews'].get(token)
            if not original or time.time() - original['createdAt'] > 900 or not isinstance(decisions, list):
                raise ImaError('INVALID_REQUEST')
            conflicts = {(row['courseId'], row['fileId']): row for row in original.get('conflicts', [])}
            resolved = {}
            for decision in decisions:
                if not isinstance(decision, dict) or set(decision) != {'courseId', 'fileId', 'action', 'mediaId'}:
                    raise ImaError('INVALID_REQUEST')
                key = (decision['courseId'], decision['fileId'])
                if key not in conflicts or key in resolved or decision['action'] not in ('exists', 'confirm', 'skip', 'copy'):
                    raise ImaError('INVALID_REQUEST')
                allowed = [entry['mediaId'] for entry in conflicts[key]['candidates']]
                if decision['action'] == 'confirm' and decision['mediaId'] not in allowed:
                    raise ImaError('INVALID_REQUEST')
                if decision['action'] != 'confirm' and decision['mediaId'] is not None:
                    raise ImaError('INVALID_REQUEST')
                resolved[key] = {**decision, 'candidateIds': allowed}
            if set(resolved) != set(conflicts):
                raise ImaError('INVALID_REQUEST')
            preview = copy.deepcopy(original)
            preview.update(token=secrets.token_hex(16), decisions=list(resolved.values()))
            store.data['previews'] = {preview['token']: preview}
            store.save()
            return preview

    def start(self, token, request_id):
        identifier(request_id)
        current = self.store(read_only=True).data.get('job')
        if current and current['requestId'] == request_id:
            return current
        with store_lock(self.path):
            store = self.store()
            previous = store.data.get('job')
            if previous and previous['requestId'] == request_id:
                return previous
            self.ensure_idle(store)
            preview = store.data['previews'].get(token)
            if not preview or time.time() - preview['createdAt'] > 900:
                raise ImaError('INVALID_REQUEST')
            if preview.get('conflicts') and 'decisions' not in preview and preview.get('nameRuleVersion') != NAME_RULE_VERSION:
                raise SyncError('NAME_CONFLICT')
            choices = {(row['courseId'], row['fileId']): row for row in preview.get('decisions', [])}
            job = {'id': secrets.token_hex(16), 'requestId': request_id, 'workerId': self.worker_id,
                   'phase': 'queued', 'action': preview['action'], 'target': preview['target'],
                   'createdAt': now(), 'newVersion': preview.get('newVersion', False),
                   'rows': [{'source': file, 'status': 'queued', 'disposition': choices.get((file.get('courseId'), file.get('fileId')))} for file in preview['sources']]}
            if previous and any(row['status'] == 'unknown' for row in previous['rows']):
                store.data['unresolvedJobs'][previous['id']] = previous
            store.data['job'] = job
            store.save()
            self.worker = threading.Thread(target=self.run_job, args=(job['id'],), daemon=True)
        self.worker.start()
        return job

    def run_job(self, job_id):
        courses = set()
        try:
            with store_lock(self.path):
                store = self.store()
                job = store.data['job']
                if job['id'] != job_id:
                    return
                job['phase'] = 'running'
                store.save()
                client = self.client_factory()
                source = self.source_factory()
                try:
                    engine = CourseSync(client, source, store)
                    upload_target_checked = False
                    for row in job['rows']:
                        if row['status'] != 'queued':
                            continue
                        row['status'] = 'running'
                        store.save()
                        try:
                            if job['action'] == 'move':
                                self.move_one(store, client, row, job['target'])
                                row['status'] = 'success'
                                courses.update(item['source']['courseId'] for item in store.data['items'].values()
                                               if item.get('mediaId') == row['source']['mediaId'])
                            else:
                                disposition = row.get('disposition')
                                courses.add(row['source']['courseId'])
                                if disposition and disposition['action'] == 'skip':
                                    row['status'] = 'omitted'
                                    store.save()
                                    continue
                                if (not disposition or disposition['action'] in ('copy', 'exists')) and not upload_target_checked:
                                    self.target(client, job['target']['libraryId'], job['target']['targetFolderId'])
                                    upload_target_checked = True
                                try:
                                    file = source.select([{'courseId': row['source']['courseId'], 'fileId': row['source']['fileId']}])[0]
                                except SyncError as error:
                                    if error.code != 'SOURCE_CONFLICT' or not row['source'].get('groupId'):
                                        raise
                                    matches = [file for file in source.course(row['source']['courseId'])
                                               if file.get('groupId') == row['source']['groupId']]
                                    if len(matches) != 1:
                                        raise SyncError('SOURCE_CONFLICT')
                                    file = matches[0]
                                expected_source = engine.source_for_item(file, {'source': row['source']})
                                same_member = expected_source['fileId'] == row['source']['fileId']
                                if same_member and any(expected_source.get(field) != row['source'].get(field) for field in ('fileName', 'size', 'updatedAt', 'modifiedAt')):
                                    raise SyncError('SOURCE_CONFLICT')
                                if not same_member and (not file.get('groupId') or file['groupId'] != row['source'].get('groupId')
                                                        or normalized(file['fileName']) != normalized(row['source']['fileName'])):
                                    raise SyncError('SOURCE_CONFLICT')
                                file = expected_source
                                courses.add(file['courseId'])
                                if disposition and disposition['action'] != 'exists':
                                    tree = client.tree(job['target']['libraryId'])
                                    current_ids = {entry['mediaId'] for entry in tree if entry['kind'] == 'file' and normalized(entry['name']) == normalized(file['fileName'])}
                                    if not set(disposition['candidateIds']) <= current_ids:
                                        raise SyncError('REMOTE_MISSING')
                                    if disposition['action'] == 'confirm':
                                        remote = [entry for entry in tree if entry['kind'] == 'file' and entry['mediaId'] == disposition['mediaId']]
                                        if len(remote) != 1:
                                            raise SyncError('REMOTE_MISSING')
                                        outcome = self.confirm_existing(store, source, file, remote[0], job['target']['libraryId'])
                                    else:
                                        outcome = engine.sync_one(file, job['target'], new_version=job['newVersion'],
                                                                  ignored_media_ids=set(disposition['candidateIds']))
                                else:
                                    outcome = engine.sync_one(file, job['target'], new_version=job['newVersion'], name_rule=True)
                                row['status'] = 'skipped' if outcome == 'skipped' else 'success'
                                row['outcome'] = outcome
                                store.data['links'].setdefault(file['courseId'], {
                                    'libraryId': job['target']['libraryId'], 'folderId': job['target']['targetFolderId']})
                                mapping = store.data['items'].get(engine.item_key(file, job['target']['libraryId'], client.tree(job['target']['libraryId']), prefer_source=job['newVersion']))
                                if mapping and mapping.get('mediaId'):
                                    row['mediaId'] = mapping['mediaId']
                                else:
                                    match = store.data['nameMatches'].get(file['courseId'] + ':' + file.get('groupId', file['fileId']) + ':' + job['target']['libraryId'])
                                    if match:
                                        row.update(association=match['association'], candidates=match['candidates'])
                        except (ImaError, SyncError, canvas.CanvasError) as error:
                            details = failure(error)
                            write_confirmed = job['action'] == 'move' and row.get('retCode') == 0 and not row.get('readbackVerified')
                            if job['action'] == 'upload':
                                mapping_key = engine.item_key(row['source'], job['target']['libraryId'], prefer_source=job.get('newVersion', False))
                                item = store.data['items'].get(mapping_key)
                                write_confirmed = bool(item and item['stage'] == 'add_acknowledged')
                                if item:
                                    item['error'] = details
                            if write_confirmed:
                                details = {**details, 'code': 'OUTCOME_UNKNOWN', 'readbackError': error.code}
                            row.update(status='unknown' if error.code in UNCERTAIN or write_confirmed else 'failed',
                                       error=details, retryable=error.code in RETRY_CODES and not write_confirmed)
                            if error.code == 'SAVE_FAILED':
                                raise
                        store.save()
                    job['phase'] = 'checking'
                    store.save()
                finally:
                    source.session.close()
        except Exception as error:
            try:
                with store_lock(self.path):
                    store = self.store()
                    job = store.data.get('job')
                    if job and job['id'] == job_id:
                        job['phase'] = 'interrupted'
                        frames = traceback.extract_tb(error.__traceback__)
                        job['localError'] = {'type': type(error).__name__,
                                             'function': frames[-1].name if frames else None,
                                             'line': frames[-1].lineno if frames else None}
                        for row in job['rows']:
                            if row['status'] == 'running':
                                row.update(status='unknown', error={'code': 'OUTCOME_UNKNOWN'})
                            elif row['status'] == 'queued':
                                row['status'] = 'not_run'
                        store.save()
            except Exception:
                pass
            return
        for course_id in courses:
            self.compare(course_id, _job_id=job_id)
        with store_lock(self.path):
            store = self.store()
            store.data['job'].update(phase='complete', finishedAt=now())
            store.save()

    def retry(self, job_id):
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            job = store.data.get('job')
            if not job or job['id'] != job_id:
                raise ImaError('INVALID_REQUEST')
            for row in job['rows']:
                if row['status'] in RETRYABLE and (row['status'] == 'not_run' or row.get('retryable', True)):
                    row.update(status='queued')
                    row.pop('error', None)
            if not any(row['status'] == 'queued' for row in job['rows']):
                return job
            job.update(phase='queued', workerId=self.worker_id)
            store.save()
            self.worker = threading.Thread(target=self.run_job, args=(job_id,), daemon=True)
        self.worker.start()
        return job

    def reconcile(self, job_id):
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            current = store.data.get('job')
            job = current if current and current['id'] == job_id else store.data['unresolvedJobs'].get(job_id)
            if not job:
                raise ImaError('INVALID_REQUEST')
            client = self.client_factory()
            tree = client.tree(job['target']['libraryId'])
            for row in job['rows']:
                if row['status'] != 'unknown':
                    continue
                if job['action'] == 'move':
                    media_id = row['source']['mediaId']
                else:
                    engine = CourseSync(client, None, store)
                    item = store.data['items'].get(engine.item_key(row['source'], job['target']['libraryId'], tree, prefer_source=job.get('newVersion', False)))
                    media_id = item.get('mediaId') if item else None
                matches = [entry for entry in tree if entry['kind'] == 'file' and entry['mediaId'] == media_id]
                if len(matches) == 1 and (job['action'] != 'move' or matches[0]['parentFolderId'] == job['target']['targetFolderId']):
                    if job['action'] == 'upload':
                        engine.commit(item, matches[0])
                    else:
                        self.move_one(store, client, row, job['target'])
                    row.update(status='success', reconciledAt=now())
                    row.pop('error', None)
            if not any(row['status'] == 'unknown' for row in job['rows']):
                store.data['unresolvedJobs'].pop(job_id, None)
            store.save()
            return job

    def move_preview(self, library_id, media_ids, target_library_id, folder_id):
        identifier(library_id)
        if not isinstance(media_ids, list) or not 1 <= len(media_ids) <= 500 or len(set(media_ids)) != len(media_ids):
            raise ImaError('INVALID_REQUEST')
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            client = self.client_factory()
            target, _ = self.target(client, target_library_id, folder_id)
            tree = client.tree(library_id)
            files = []
            for media_id in media_ids:
                identifier(media_id)
                matches = [row for row in tree if row['kind'] == 'file' and row['mediaId'] == media_id]
                if len(matches) != 1:
                    raise SyncError('REMOTE_MISSING')
                files.append({'mediaId': media_id, 'libraryId': library_id,
                              'fileName': matches[0]['name'], 'parentFolderId': matches[0]['parentFolderId']})
            token = secrets.token_hex(16)
            preview = {'token': token, 'createdAt': time.time(), 'sources': files, 'target': target, 'action': 'move'}
            store.data['previews'] = {token: preview}
            store.save()
            return preview

    def move_one(self, store, client, row, target):
        file = row['source']
        before = client.tree(file['libraryId'])
        destination = client.tree(target['libraryId']) if file['libraryId'] != target['libraryId'] else before
        already = [entry for entry in destination if entry['kind'] == 'file' and entry['mediaId'] == file['mediaId']
                   and entry['parentFolderId'] == target['targetFolderId']]
        if len(already) != 1:
            if row.get('sent'):
                raise SyncError('OUTCOME_UNKNOWN')
            if not any(entry['kind'] == 'file' and entry['mediaId'] == file['mediaId'] for entry in before):
                raise SyncError('REMOTE_MISSING')
            row['sent'] = True
            store.save()
            try:
                result = client._request('move_knowledge', {'src_knowledge_base_id': file['libraryId'],
                    'dst_knowledge_base_id': target['libraryId'], 'dst_folder_id': target['targetFolderId'],
                    'infos': [{'media_id': file['mediaId']}]}, mutation=True)
            except ImaError as error:
                if error.code in RETRY_CODES:
                    row['sent'] = False
                raise
            code = result.get('move_results', {}).get(file['mediaId'], {}).get('ret_code')
            row['retCode'] = code if type(code) is int else None
            if type(code) is not int:
                raise SyncError('OUTCOME_UNKNOWN')
            if code != 0:
                row['sent'] = False
                raise ImaError('UPSTREAM_ERROR', code)
        after = client.tree(target['libraryId'])
        found = [entry for entry in after if entry['kind'] == 'file' and entry['mediaId'] == file['mediaId']]
        if len(found) != 1 or found[0]['parentFolderId'] != target['targetFolderId']:
            raise SyncError('OUTCOME_UNKNOWN')
        if file['libraryId'] != target['libraryId'] and any(entry['mediaId'] == file['mediaId'] for entry in client.tree(file['libraryId'])):
            raise SyncError('OUTCOME_UNKNOWN')
        for item in store.data['items'].values():
            if item.get('mediaId') == file['mediaId'] and item['libraryId'] == file['libraryId']:
                item.update(libraryId=target['libraryId'], actualFolderId=target['targetFolderId'],
                            remotePath=found[0]['path'], lastVerifiedAt=now())
        row['readbackVerified'] = True

    def folder(self, action, library_id, folder_id, name, request_id):
        if action not in ('create', 'rename'):
            raise ImaError('INVALID_REQUEST')
        identifier(request_id)
        name = safe_name(name)
        with store_lock(self.path):
            store = self.store()
            self.ensure_idle(store)
            client = self.client_factory()
            target, before = self.target(client, library_id, folder_id)
            digest = hashlib.sha256(json.dumps([action, library_id, folder_id, name]).encode()).hexdigest()
            operations = store.data['operations']
            existing = [row for row in operations.values() if row['digest'] == digest and row['status'] == 'pending']
            operation = operations.get(request_id) or (existing[0] if existing else None)
            if operation and operation['digest'] != digest:
                raise ImaError('INVALID_REQUEST')
            if operation and operation['status'] == 'passed':
                return operation
            if operation is None:
                if action == 'create' and any(row['kind'] == 'folder' and row['name'] == name and row['parentFolderId'] == folder_id for row in before):
                    raise SyncError('NAME_CONFLICT')
                if action == 'rename' and folder_id == library_id:
                    raise ImaError('INVALID_REQUEST')
                operation = {'digest': digest, 'action': action, 'libraryId': library_id, 'folderId': folder_id,
                             'name': name, 'status': 'pending', 'beforeIds': [row['folderId'] for row in before if row['kind'] == 'folder'],
                             'children': [row['folderId'] or row['mediaId'] for row in before if row['parentFolderId'] == folder_id]}
                if action == 'rename':
                    operation['parentId'] = next(row['parentFolderId'] for row in before if row['kind'] == 'folder' and row['folderId'] == folder_id)
                operations[request_id] = operation
                store.save()
                try:
                    if action == 'create':
                        result = client._request('create_folder', {'knowledge_base_id': library_id, 'folder_id': folder_id, 'name': name}, mutation=True)
                        operation['objectId'] = result.get('media_id') or result.get('folder_id')
                    else:
                        client._request('rename_knowledge', {'knowledge_base_id': library_id, 'media_id': folder_id, 'name': name}, mutation=True)
                        operation['objectId'] = folder_id
                except ImaError as error:
                    operation['error'] = failure(error)
                store.save()
            try:
                after = client.tree(library_id)
            except ImaError as error:
                operation['readbackError'] = failure(error)
                store.save()
                raise ImaError('OUTCOME_UNKNOWN', diagnostic={'stage': 'folder_readback', 'readbackError': error.code}) from None
            if action == 'create':
                matches = [row for row in after if row['kind'] == 'folder' and row['name'] == name and row['parentFolderId'] == folder_id
                           and row['folderId'] not in operation['beforeIds']]
            else:
                matches = [row for row in after if row['kind'] == 'folder' and row['folderId'] == folder_id
                           and row['name'] == name and row['parentFolderId'] == operation['parentId']]
                children = sorted(str(row['folderId'] or row['mediaId']) for row in after if row['parentFolderId'] == folder_id)
                if children != sorted(str(value) for value in operation['children']):
                    raise SyncError('OUTCOME_UNKNOWN')
            if len(matches) != 1:
                raise SyncError('OUTCOME_UNKNOWN')
            operation.update(status='passed', objectId=matches[0]['folderId'], verifiedAt=now())
            store.save()
            return {key: value for key, value in operation.items() if key not in ('beforeIds', 'children')}


def handle_request(handler, service, token):
    prefix = '/api/ima/v1/workspace/'
    if not handler.path.startswith(prefix):
        return False
    if not handler.desktop_request() or not secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), token):
        discard_small_body(handler)
        handler.json_response(403, envelope(error={'code': 'FORBIDDEN', 'message': '本机鉴权失败'}))
        return True
    route = handler.path[len(prefix):]
    specs = {'view': {'courseId'}, 'libraries': set(), 'tree': {'libraryId'},
             'link': {'courseId', 'libraryId', 'folderId'}, 'check': {'courseId'},
             'confirm': {'courseId', 'fileId', 'libraryId', 'mediaId', 'checkId', 'same'},
             'confirm-batch': {'courseId', 'libraryId', 'checkId', 'selections', 'requestId'},
             'preview': {'files', 'libraryId', 'folderId', 'newVersion'},
             'resolve-preview': {'token', 'decisions'},
             'start': {'token', 'requestId'}, 'retry': {'jobId'}, 'reconcile': {'jobId'},
             'move-preview': {'libraryId', 'mediaIds', 'targetLibraryId', 'folderId'},
             'folder': {'action', 'libraryId', 'folderId', 'name', 'requestId'}}
    try:
        if handler.command != 'POST' or route not in specs:
            discard_small_body(handler)
            handler.json_response(405, envelope(error={'code': 'METHOD_NOT_ALLOWED', 'message': '请选择已提供的ima操作'}))
            return True
        length = handler.headers.get('Content-Length', '')
        if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or not 2 <= int(length) <= 131072:
            raise ImaError('INVALID_REQUEST')
        handler.connection.settimeout(5)
        body = json.loads(handler.rfile.read(int(length)))
        if not isinstance(body, dict) or set(body) != specs[route]:
            raise ImaError('INVALID_REQUEST')
        if route == 'view': result = service.view(body['courseId'])
        elif route == 'libraries': result = service.libraries()
        elif route == 'tree': result = service.tree(body['libraryId'])
        elif route == 'link': result = service.link(body['courseId'], body['libraryId'], body['folderId'])
        elif route == 'check': result = service.compare(body['courseId'])
        elif route == 'confirm': result = service.confirm(body['courseId'], body['fileId'], body['libraryId'], body['mediaId'], body['checkId'], body['same'])
        elif route == 'confirm-batch': result = service.confirm_batch(body['courseId'], body['libraryId'], body['checkId'], body['selections'], body['requestId'])
        elif route == 'preview': result = service.preview(body['files'], body['libraryId'], body['folderId'], body['newVersion'])
        elif route == 'resolve-preview': result = service.resolve_preview(body['token'], body['decisions'])
        elif route == 'start': result = service.start(body['token'], body['requestId'])
        elif route == 'retry': result = service.retry(body['jobId'])
        elif route == 'reconcile': result = service.reconcile(body['jobId'])
        elif route == 'move-preview': result = service.move_preview(body['libraryId'], body['mediaIds'], body['targetLibraryId'], body['folderId'])
        else: result = service.folder(body['action'], body['libraryId'], body['folderId'], body['name'], body['requestId'])
        handler.json_response(200, envelope(result))
    except (ImaError, SyncError, canvas.CanvasError) as error:
        status = (400 if error.code == 'INVALID_REQUEST' else 423 if error.code == 'SYNC_BUSY'
                  else 409 if error.code in ('NAME_CONFLICT', 'TARGET_MISSING', 'CONTENT_CHANGED', 'OUTCOME_UNKNOWN') else 502)
        handler.json_response(status, envelope(error={**failure(error), 'message': str(error)}))
    except (ValueError, TypeError):
        handler.json_response(400, envelope(error={'code': 'INVALID_REQUEST', 'message': 'ima请求参数无效'}))
    except Exception:
        handler.json_response(503, envelope(error={'code': 'LOCAL_ERROR', 'message': 'ima操作未完成，请查看本机状态后继续'}))
    return True
