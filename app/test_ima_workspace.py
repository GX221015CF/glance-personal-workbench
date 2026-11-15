"""Production workspace contracts using isolated encrypted stores and synthetic cloud."""
import copy
import http.client
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ima_bridge as ima
import ima_workspace as workspace
import ima_course_sync as sync
from test_ima_course_sync import Source, Cloud, upload, PDF
import subprocess
import sys


class WorkspaceSource(Source):
    def __init__(self):
        super().__init__()
        self.session = Mock()
        for row in self.rows:
            row.update(supported=True, **sync.file_format(row['fileName'], row['size']))

    def course(self, course_id):
        return copy.deepcopy([row for row in self.rows if row['courseId'] == course_id])

    def select(self, pairs):
        result = []
        for pair in pairs:
            found = [row for row in self.rows if row['courseId'] == pair['courseId'] and row['fileId'] == pair['fileId']]
            if len(found) != 1:
                raise sync.SyncError('SOURCE_CONFLICT')
            result.append(copy.deepcopy(found[0]))
        return result


class WorkspaceCloud(Cloud):
    def __init__(self):
        super().__init__()
        self.tree_error = False

    def tree(self, library):
        if self.tree_error:
            raise ima.ImaError('INCOMPLETE_LIST')
        return super().tree(library)

    def _request(self, operation, body, **kwargs):
        if operation not in ('create_folder', 'rename_knowledge', 'move_knowledge'):
            return super()._request(operation, body, **kwargs)
        self.calls.append((operation, copy.deepcopy(body)))
        if operation == 'create_folder':
            folder_id = 'folder-' + str(len(self.calls))
            self.entries.append({'kind': 'folder', 'folderId': folder_id, 'mediaId': folder_id,
                                 'name': body['name'], 'path': '/' + body['name'], 'parentFolderId': body['folder_id']})
            return {'media_id': folder_id}
        if operation == 'rename_knowledge':
            for row in self.entries:
                if row['folderId'] == body['media_id']:
                    row.update(name=body['name'], path='/' + body['name'])
            return {}
        media_id = body['infos'][0]['media_id']
        for row in self.entries:
            if row['kind'] == 'file' and row['mediaId'] == media_id:
                row.update(parentFolderId=body['dst_folder_id'], path='/moved/' + row['name'])
        return {'move_results': {media_id: {'ret_code': 0}}}


class SemesterSource(WorkspaceSource):
    def __init__(self):
        super().__init__()
        self.payloads = {'2': PDF, '3': b'PK\x03\x04isolated-docx-fixture'}
        self.rows = [{'courseId': '88', 'courseName': '隔离新学期课程', 'fileId': file_id,
                      'fileName': name, 'size': len(self.payloads[file_id]), 'contentType': content_type,
                      'createdAt': '2027-02-01T00:00:00Z', 'updatedAt': '2027-02-01T00:00:00Z',
                      'supported': True, **sync.file_format(name, len(self.payloads[file_id]))}
                     for file_id, name, content_type in [('2', '作业一.pdf', 'application/pdf'),
                         ('3', '作业一.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')]]

    def download(self, source):
        payload = self.payloads[source['fileId']]
        digest = sync.valid_content(payload, source['contentType'], source)
        return payload, {'status': 'passed', 'sha256': digest, 'size': len(payload), 'httpStatus': 200}


class GroupedSource(WorkspaceSource):
    def __init__(self):
        super().__init__()
        self.members = copy.deepcopy(self.rows)
        self.members[1]['fileName'] = self.members[0]['fileName']
        self.regroup()

    def regroup(self):
        grouped = workspace.canvas.group_materials([{**row, 'id': row['fileId'], 'name': row['fileName']} for row in self.members])[0]
        representative = min(self.members, key=lambda row: (not row.get('supported', True), int(row['fileId'])))
        self.rows = [{**representative, 'groupId': grouped['groupId'], 'memberIds': grouped['memberIds'],
                      'members': copy.deepcopy(self.members)}]

    def select(self, pairs):
        if any(pair['fileId'] not in self.rows[0]['memberIds'] for pair in pairs):
            raise sync.SyncError('SOURCE_CONFLICT')
        return copy.deepcopy(self.rows)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source, self.cloud = WorkspaceSource(), WorkspaceCloud()
        self.service = workspace.ImaWorkspace(path=self.directory / 'mapping.dpapi', recovery=self.directory / 'recovery.dpapi',
                                             source_factory=lambda: self.source, client_factory=lambda: self.cloud)
        self.uploader = patch.object(sync, 'upload_bytes', side_effect=upload).start()
        self.addCleanup(patch.stopall)
        self.service.link('1', 'library', 'target')

    def pairs(self):
        return [{'courseId': row['courseId'], 'fileId': row['fileId']} for row in self.source.rows]

    def manual_candidates(self):
        for index, file in enumerate(self.source.rows):
            self.cloud.entries.append({'kind': 'file', 'folderId': None, 'mediaId': 'manual-' + str(index),
                                       'name': file['fileName'], 'path': '/历史/' + file['fileName'], 'parentFolderId': 'target'})
        return self.service.compare('1')

    def test_group_saves_once_for_multiple_ids_and_keeps_identity_when_representative_changes(self):
        self.source = GroupedSource()
        pairs = [{'courseId': '1', 'fileId': '2'}, {'courseId': '1', 'fileId': '3'}]
        first = self.service.preview(pairs, 'library', 'target')
        self.assertEqual(len(first['sources']), 1)
        group = first['sources'][0]['groupId']
        self.service.start(first['token'], 'group-first');self.service.worker.join(5)
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 1)
        self.assertEqual(self.service.compare('1')['counts']['exists'], 1)
        self.source.members.append({**self.source.members[0], 'fileId': '1'})
        self.source.members.reverse();self.source.regroup()
        self.assertEqual(self.source.rows[0]['groupId'], group)
        job = self.upload_batch('group-repeat')
        self.assertEqual(job['rows'][0]['status'], 'skipped')
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 1)
        reloaded = workspace.ImaWorkspace(path=self.service.path, recovery=self.service.recovery,
                                         source_factory=lambda: self.source, client_factory=lambda: self.cloud)
        self.assertEqual(reloaded.compare('1')['counts']['exists'], 1)
        self.assertEqual(len(reloaded.store().data['items']), 1)
        script = """import sys,json
from pathlib import Path
import ima_workspace as w
import ima_course_sync as s
service=w.ImaWorkspace(path=Path(sys.argv[1]),recovery=Path(sys.argv[2]))
assert service.view('1')['comparison']['counts']['exists']==1
store=service.store(read_only=True)
assert s.CourseSync(None,None,store).item_key(json.loads(sys.argv[3]),'library') in store.data['items']
print('isolated group identity reload passed, cloud writes 0')
"""
        process = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(self.service.path),
                                  str(self.service.recovery), json.dumps(self.source.rows[0])],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)


    def test_group_reuses_any_legacy_member_mapping_and_keeps_multiple_old_associations(self):
        self.upload_batch('legacy-files')
        old = copy.deepcopy(self.service.store().data['items'])
        self.source = GroupedSource()
        result = self.service.compare('1')
        self.assertEqual(result['counts']['exists'], 1)
        self.assertEqual(len(result['rows'][0]['savedAssociations']), 2)
        self.assertEqual(len(self.service.store().data['items']), 2)
        before = len(self.cloud.calls)
        job = self.upload_batch('legacy-group-repeat')
        self.assertEqual(job['rows'][0]['status'], 'skipped')
        self.assertEqual(len(self.cloud.calls), before)
        self.assertEqual({key: row['mediaId'] for key, row in old.items()},
                         {key: row['mediaId'] for key, row in self.service.store().data['items'].items()})

    def test_legacy_group_identity_survives_all_original_ids_disappearing_and_cloud_rename(self):
        self.upload_batch('legacy-identity')
        self.source = GroupedSource();self.service.compare('1')
        self.source.members = [{**self.source.members[0], 'fileId': '1'}]
        self.source.regroup()
        for remote in self.cloud.entries:
            if remote['kind'] == 'file':remote.update(name='用户改名.pdf', path='/用户归档/用户改名.pdf')
        result = self.service.compare('1')
        self.assertEqual(result['counts']['exists'], 1)
        self.assertEqual(len(result['rows'][0]['savedAssociations']), 2)
        self.assertEqual(len(self.service.store().data['items']), 2)

    def test_group_mapped_source_changes_use_its_fingerprint_with_a_different_representative(self):
        self.source = GroupedSource();self.upload_batch('group-original')
        self.source.members.append({**self.source.members[0], 'fileId': '1'})
        self.source.members[0]['updatedAt'] = '2026-11-13T00:00:00Z'
        self.source.payload = PDF + b'changed'
        self.source.regroup()
        row = self.service.compare('1')['rows'][0]
        self.assertEqual(self.source.rows[0]['fileId'], '1')
        self.assertEqual(row['status'], 'new_version')

    def test_group_second_legacy_mapping_content_change_targets_that_source_version(self):
        self.upload_batch('legacy-version-originals')
        self.source = GroupedSource()
        self.source.members[1]['updatedAt'] = '2026-11-13T00:00:00Z'
        self.source.regroup()
        original = self.source.download
        def download(file):
            if file['fileId'] == '3':
                payload = PDF + b'changed-second'
                return payload, {'status': 'passed', 'sha256': hashlib.sha256(payload).hexdigest(),
                                 'size': len(payload), 'httpStatus': 200}
            return original(file)
        with patch.object(self.source, 'download', side_effect=download):
            check = self.service.compare('1')
            self.assertEqual(check['rows'][0]['status'], 'new_version')
            self.assertEqual(check['rows'][0]['changedSources'][0]['fileId'], '3')
            preview = self.service.preview(self.pairs(), 'library', 'target', True)
            self.assertEqual(preview['sources'][0]['fileId'], '3')
            self.service.start(preview['token'], 'legacy-second-version');self.service.worker.join(5)
        self.assertEqual(self.service.view('1')['job']['rows'][0]['status'], 'success', self.service.view('1')['job']['rows'][0])
        items = self.service.store().data['items']
        self.assertEqual(items['1:3']['source']['fileId'], '3')
        self.assertEqual(len(items['1:3']['versions']), 1)
        self.assertNotIn('versions', items['1:2'])

    def test_group_unknown_new_version_never_reconciles_against_a_sibling_media(self):
        self.upload_batch('version-unknown-original')
        self.source = GroupedSource()
        self.source.members[1]['updatedAt'] = '2026-11-13T00:00:00Z'
        self.source.regroup()
        original = self.source.download
        def download(file):
            if file['fileId'] == '3':
                payload = PDF + b'new-version'
                return payload, {'status': 'passed', 'sha256': hashlib.sha256(payload).hexdigest(),
                                 'size': len(payload), 'httpStatus': 200}
            return original(file)
        with patch.object(self.source, 'download', side_effect=download):
            self.service.compare('1')
            self.cloud.fail = 'add_knowledge_unknown'
            preview = self.service.preview(self.pairs(), 'library', 'target', True)
            self.service.start(preview['token'], 'version-unknown');self.service.worker.join(5)
        job = self.service.view('1')['job']
        self.assertEqual(job['rows'][0]['status'], 'unknown')
        store = self.service.store()
        actual_media = store.data['items']['1:3']['mediaId']
        remote = next(row for row in self.cloud.entries if row.get('mediaId') == actual_media)
        self.cloud.entries.remove(remote)
        before = len(self.cloud.calls)
        pending = self.service.reconcile(job['id'])
        self.assertEqual(pending['rows'][0]['status'], 'unknown')
        self.assertEqual(self.service.store().data['items']['1:3']['stage'], 'add_pending')
        self.cloud.entries.append(remote)
        result = self.service.reconcile(job['id'])
        self.assertEqual(result['rows'][0]['status'], 'success')
        self.assertEqual(self.service.store().data['items']['1:3']['mediaId'], actual_media)
        self.assertEqual(len(self.cloud.calls), before)

    def test_group_retry_uses_stable_group_when_original_representative_disappears(self):
        self.source = GroupedSource();self.cloud.fail = 'create_media_reject'
        job = self.upload_batch('group-allocation-rejected')
        self.assertEqual(job['rows'][0]['status'], 'failed')
        self.source.members = [member for member in self.source.members if member['fileId'] != '2']
        self.source.payload = PDF + b'alternate-source'
        self.source.members[0]['size'] = len(self.source.payload)
        self.source.regroup()
        self.service.retry(job['id']);self.service.worker.join(5)
        self.assertEqual(self.service.view('1')['job']['rows'][0]['status'], 'success')
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 1)
        self.assertEqual(next(iter(self.service.store().data['items'].values()))['source']['fileId'], '3')

    def test_group_download_forbidden_stays_visible_and_explicit_retry_can_recover(self):
        self.source = GroupedSource()
        with patch.object(self.source, 'download', side_effect=sync.SyncError('DOWNLOAD_FORBIDDEN')):
            job = self.upload_batch('group-forbidden')
        self.assertEqual(job['rows'][0]['error']['code'], 'DOWNLOAD_FORBIDDEN')
        self.assertEqual(self.service.view('1')['comparison']['counts']['failed'], 1)
        self.assertEqual(self.service.store().data['items'], {})
        self.service.retry(job['id']);self.service.worker.join(5)
        self.assertEqual(self.service.view('1')['job']['rows'][0]['status'], 'success')
        self.assertEqual(self.service.view('1')['comparison']['counts']['exists'], 1)

    def test_group_unknown_write_reconciles_after_reload_without_another_allocation(self):
        self.source = GroupedSource();self.cloud.fail = 'add_knowledge_unknown'
        job = self.upload_batch('group-unknown')
        self.assertEqual(job['rows'][0]['status'], 'unknown')
        before = len(self.cloud.calls)
        reloaded = workspace.ImaWorkspace(path=self.service.path, recovery=self.service.recovery,
                                         source_factory=lambda: self.source, client_factory=lambda: self.cloud)
        result = reloaded.reconcile(job['id'])
        self.assertEqual(result['rows'][0]['status'], 'success')
        self.assertEqual(len(self.cloud.calls), before)
        self.assertEqual(reloaded.compare('1')['counts']['exists'], 1)

    def test_name_rule_normalizes_full_names_and_tracks_all_locations_without_fingerprints(self):
        self.source.rows[0]['fileName'] = ' Ａ章.PDF '
        self.cloud.entries.extend([
            {'kind': 'file', 'mediaId': 'name-a', 'name': 'A章.pdf', 'path': '/甲/A章.pdf', 'parentFolderId': 'target'},
            {'kind': 'file', 'mediaId': 'name-b', 'name': 'ａ章.PDF', 'path': '/乙/ａ章.PDF', 'parentFolderId': 'elsewhere'}])
        result = self.service.compare('1')
        row = result['rows'][0]
        self.assertEqual(row['status'], 'exists')
        self.assertEqual(row['association'], 'user_name_rule')
        self.assertEqual(len(row['paths']), 2)
        self.assertNotIn('mediaId', row)
        self.assertNotIn('sha256', row)
        store = self.service.store()
        self.assertEqual(store.data['items'], {})
        self.assertEqual(len(next(iter(store.data['nameMatches'].values()))['candidates']), 2)
        self.assertEqual(self.cloud.calls, [])

    def test_name_rule_keeps_real_chapter_week_word_order_and_extensions_distinct(self):
        pairs = [('第六章-投资决策.pdf', '第六周-投资决策.pdf'),
                 ('第七章-收益与风险.pdf', '第七周-风险与收益.pdf'),
                 ('作业.doc', '作业.docx'), ('作业.pdf', '作业.docx')]
        for source_name, cloud_name in pairs:
            with self.subTest(source=source_name):
                self.source.rows[0]['fileName'] = source_name
                self.cloud.entries = [row for row in self.cloud.entries if row['kind'] == 'folder']
                self.cloud.entries.append({'kind': 'file', 'mediaId': 'different', 'name': cloud_name,
                                           'path': '/' + cloud_name, 'parentFolderId': 'target'})
                self.assertEqual(self.service.compare('1')['rows'][0]['status'], 'unsynced')

    def test_name_rule_migrates_old_ignored_candidates_and_stales_old_snapshot(self):
        first = self.manual_candidates()
        store = self.service.store()
        store.data['ignoredCandidates'] = {'1:library': {'2': ['manual-0']}}
        store.data['comparisons']['1:library'].pop('nameRuleVersion')
        store.save()
        old = self.service.view('1')['comparison']
        self.assertTrue(old['stale'])
        self.assertEqual(old['checkedAt'], first['checkedAt'])
        latest = self.service.compare('1')
        self.assertEqual(latest['counts']['exists'], 2)
        self.assertFalse(latest['stale'])
        self.assertEqual(latest['nameRuleVersion'], workspace.NAME_RULE_VERSION)
        self.assertEqual(self.service.store().data['ignoredCandidates']['1:library']['2'], ['manual-0'])

    def test_name_rule_default_save_skips_without_download_upload_or_exact_mapping(self):
        self.manual_candidates()
        with patch.object(self.source, 'download', side_effect=AssertionError('name rule needs no content hash')):
            preview = self.service.preview(self.pairs(), 'library', 'target')
            self.service.start(preview['token'], 'name-default')
            self.service.worker.join(5)
        job = self.service.view('1')['job']
        self.assertEqual(job['phase'], 'complete')
        self.assertEqual([row['status'] for row in job['rows']], ['skipped', 'skipped'])
        self.assertEqual(self.service.store().data['items'], {})
        self.assertEqual(self.cloud.calls, [])
        reloaded = workspace.ImaWorkspace(path=self.service.path, recovery=self.service.recovery,
                                         source_factory=lambda: self.source, client_factory=lambda: self.cloud)
        self.assertEqual(reloaded.compare('1')['counts']['exists'], 2)
        self.assertEqual(reloaded.start(preview['token'], 'name-default')['id'], job['id'])
        script = """import sys
from pathlib import Path
import ima_workspace as w
s=w.ImaWorkspace(path=Path(sys.argv[1]),recovery=Path(sys.argv[2]))
v=s.view('1')
assert v['comparison']['counts']['exists']==2
assert len(s.store().data['nameMatches'])==2
assert s.store().data['items']=={}
print('isolated name-rule process reload passed')
"""
        process = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(self.service.path), str(self.service.recovery)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_name_rule_full_read_failure_keeps_previous_evidence_time_and_other_library_is_unchecked(self):
        first = self.manual_candidates()
        self.cloud.tree_error = True
        latest = self.service.compare('1')
        self.assertTrue(latest['stale'])
        self.assertEqual(latest['counts']['exists'], 2)
        self.assertEqual(latest['checkedAt'], first['checkedAt'])
        self.assertEqual(len(self.service.store().data['nameMatches']), 2)
        self.cloud.tree_error = False
        store = self.service.store()
        store.data['links']['1']['libraryId'] = 'another-library'
        store.save()
        self.assertIsNone(self.service.view('1')['comparison'])

    def test_name_rule_default_rechecks_remote_before_skipping_and_copy_requires_bound_candidates(self):
        self.manual_candidates()
        preview = self.service.preview(self.pairs(), 'library', 'target')
        resolved = self.service.resolve_preview(preview['token'], [
            {'courseId': '1', 'fileId': '2', 'action': 'exists', 'mediaId': None},
            {'courseId': '1', 'fileId': '3', 'action': 'copy', 'mediaId': None}])
        self.service.start(resolved['token'], 'name-mixed-copy')
        self.service.worker.join(5)
        job = self.service.view('1')['job']
        self.assertEqual([row['status'] for row in job['rows']], ['skipped', 'success'])
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 1)
        self.assertEqual(len(self.service.store().data['items']), 1)
        repeated = self.upload_batch('name-repeat')
        self.assertEqual([row['status'] for row in repeated['rows']], ['skipped', 'skipped'])
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 1)

    def test_name_rule_does_not_reuse_another_library_or_course_mapping(self):
        self.upload_batch('scope-original')
        store = self.service.store()
        original = copy.deepcopy(store.data['items'])
        store.data['links']['1'] = {'libraryId': 'new-library', 'folderId': 'new-library'}
        store.save()
        with patch.object(self.cloud, 'tree', return_value=[]):
            result = self.service.compare('1')
        self.assertEqual(result['counts']['unsynced'], 2)
        self.assertEqual(self.service.store().data['items'], original)
        self.source.rows[0]['courseId'] = '88'
        store = self.service.store()
        store.data['links']['88'] = {'libraryId': 'new-library', 'folderId': 'new-library'}
        store.save()
        with patch.object(self.cloud, 'tree', return_value=[]):
            self.assertEqual(self.service.compare('88')['counts']['unsynced'], 1)

    def test_name_rule_remote_disappearance_clears_only_its_name_evidence(self):
        self.manual_candidates()
        self.cloud.entries = [entry for entry in self.cloud.entries if entry.get('mediaId') != 'manual-0']
        latest = self.service.compare('1')
        self.assertEqual(latest['counts']['exists'], 1)
        self.assertEqual(latest['counts']['unsynced'], 1)
        matches = self.service.store().data['nameMatches']
        self.assertNotIn('1:2:library', matches)
        self.assertIn('1:3:library', matches)

    def test_batch_confirmation_persists_all_rows_with_one_context_and_constant_tree_reads(self):
        check = self.manual_candidates()
        selections = [{'fileId': row['fileId'], 'mediaId': None} for row in self.source.rows]
        with patch.object(self.cloud, 'tree', wraps=self.cloud.tree) as tree:
            result = self.service.confirm_batch('1', 'library', check['checkId'], selections, 'confirm-all')
            self.assertEqual(tree.call_count, 2)
        self.assertEqual([row['status'] for row in result['rows']], ['confirmed', 'confirmed'])
        self.assertEqual(result['comparison']['counts']['exists'], 2)
        reloaded = workspace.ImaWorkspace(path=self.service.path, recovery=self.service.recovery,
                                         source_factory=lambda: self.source, client_factory=lambda: self.cloud)
        duplicate = reloaded.confirm_batch('1', 'library', check['checkId'], selections, 'confirm-all')
        self.assertEqual(duplicate['rows'], result['rows'])
        self.assertTrue(all(item['association'] == 'user_confirmed' for item in reloaded.store().data['items'].values()))
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(reloaded.compare('1')['counts']['exists'], 2)

    def test_batch_multiple_candidates_complete_unique_items_and_retry_explicit_selection(self):
        self.manual_candidates()
        file = self.source.rows[0]
        self.cloud.entries.append({'kind': 'file', 'folderId': None, 'mediaId': 'another',
                                   'name': file['fileName'], 'path': '/另一位置/' + file['fileName'], 'parentFolderId': 'target'})
        check = self.service.compare('1')
        selections = [{'fileId': row['fileId'], 'mediaId': None} for row in self.source.rows]
        first = self.service.confirm_batch('1', 'library', check['checkId'], selections, 'ambiguous')
        self.assertEqual([row['status'] for row in first['rows']], ['needs_selection', 'confirmed'])
        second = self.service.confirm_batch('1', 'library', first['comparison']['checkId'],
            [{'fileId': file['fileId'], 'mediaId': 'another'}], 'selected-position')
        self.assertEqual(second['comparison']['counts']['exists'], 2)
        self.assertEqual(self.cloud.calls, [])

    def test_batch_remote_disappearance_or_changed_source_preserves_other_confirmed_rows(self):
        check = self.manual_candidates()
        self.cloud.entries = [entry for entry in self.cloud.entries if entry.get('mediaId') != 'manual-0']
        first = self.service.confirm_batch('1', 'library', check['checkId'],
            [{'fileId': row['fileId'], 'mediaId': None} for row in self.source.rows], 'missing-remote')
        self.assertEqual([row['status'] for row in first['rows']], ['failed', 'confirmed'])
        self.assertEqual(first['rows'][0]['error']['code'], 'REMOTE_MISSING')
        self.assertEqual(len(self.service.store().data['items']), 1)
        with self.assertRaises(ima.ImaError):
            self.service.confirm_batch('1', 'library', check['checkId'], [{'fileId': '2', 'mediaId': 'manual-0'}], 'expired-check')
        self.assertEqual(self.cloud.calls, [])

    def test_batch_source_change_and_wrong_context_or_candidate_are_rejected(self):
        check = self.manual_candidates()
        self.source.rows[0]['updatedAt'] = '2026-11-12T01:00:00Z'
        result = self.service.confirm_batch('1', 'library', check['checkId'],
            [{'fileId': row['fileId'], 'mediaId': None} for row in self.source.rows], 'source-changed')
        self.assertEqual(result['rows'][0]['error']['code'], 'SOURCE_CONFLICT')
        self.assertEqual(result['rows'][1]['status'], 'confirmed')
        with self.assertRaises(ima.ImaError):
            self.service.confirm_batch('1', 'wrong-library', check['checkId'], [{'fileId': '2', 'mediaId': None}], 'wrong-context')
        with self.assertRaises(ima.ImaError):
            self.service.confirm_batch('1', 'library', check['checkId'], [], 'empty')

    def test_unresolved_name_conflicts_cannot_start_and_decisions_are_bound_to_preview(self):
        self.manual_candidates()
        preview = self.service.preview(self.pairs(), 'library', 'target')
        self.assertEqual(len(preview['conflicts']), 2)
        with ima.store_lock(self.service.path):
            store = self.service.store()
            store.data['previews'][preview['token']].pop('nameRuleVersion')
            store.save()
        with self.assertRaises(sync.SyncError):
            self.service.start(preview['token'], 'without-decision')
        with self.assertRaises(ima.ImaError):
            self.service.resolve_preview(preview['token'], [{'courseId': '1', 'fileId': '2', 'action': 'copy', 'mediaId': None}])
        choices = [{'courseId': row['courseId'], 'fileId': row['fileId'], 'action': 'skip', 'mediaId': None} for row in self.source.rows]
        resolved = self.service.resolve_preview(preview['token'], choices)
        with self.assertRaises(ima.ImaError):
            self.service.start(preview['token'], 'stale-token')
        job = self.service.start(resolved['token'], 'skip-all')
        self.service.worker.join(5)
        self.assertTrue(all(row['status'] == 'omitted' for row in self.service.view('1')['job']['rows']))
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(self.service.store().data['items'], {})

    def test_name_choices_confirm_existing_or_copy_and_repeat_do_not_duplicate(self):
        self.manual_candidates()
        preview = self.service.preview(self.pairs(), 'library', 'target')
        choices = [{'courseId': '1', 'fileId': '2', 'action': 'confirm', 'mediaId': 'manual-0'},
                   {'courseId': '1', 'fileId': '3', 'action': 'copy', 'mediaId': None}]
        resolved = self.service.resolve_preview(preview['token'], choices)
        self.service.start(resolved['token'], 'mixed-choices')
        self.service.worker.join(5)
        job = self.service.view('1')['job']
        self.assertTrue(all(row['status'] == 'success' for row in job['rows']))
        self.assertEqual(job['rows'][0]['outcome'], 'confirmed')
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 1)
        items = self.service.store().data['items']
        self.assertEqual(items['1:2']['mediaId'], 'manual-0')
        self.assertNotEqual(items['1:3']['mediaId'], 'manual-1')
        before = len(self.cloud.calls)
        self.assertEqual(self.service.start(resolved['token'], 'mixed-choices')['id'], job['id'])
        self.upload_batch('repeat-after-choices')
        self.assertEqual(len(self.cloud.calls), before)

    def test_copy_permission_does_not_bypass_new_conflicts_after_preview(self):
        self.manual_candidates()
        preview = self.service.preview(self.pairs(), 'library', 'target')
        resolved = self.service.resolve_preview(preview['token'],
            [{'courseId': '1', 'fileId': row['fileId'], 'action': 'copy', 'mediaId': None} for row in self.source.rows])
        self.cloud.entries.append({'kind': 'file', 'mediaId': 'new-unreviewed', 'folderId': None,
            'name': self.source.rows[0]['fileName'], 'path': '/新增/' + self.source.rows[0]['fileName'], 'parentFolderId': 'target'})
        self.service.start(resolved['token'], 'new-conflict')
        self.service.worker.join(5)
        job = self.service.view('1')['job']
        self.assertEqual(job['rows'][0]['error']['code'], 'NAME_CONFLICT')
        self.assertEqual(job['rows'][1]['status'], 'success')

    def test_confirmation_retry_skips_success_and_completes_only_failed_download(self):
        check = self.manual_candidates()
        download = self.source.download
        def partial(file):
            if file['fileId'] == '2':
                raise sync.SyncError('DOWNLOAD_FAILED')
            return download(file)
        selections = [{'fileId': row['fileId'], 'mediaId': None} for row in self.source.rows]
        with patch.object(self.source, 'download', side_effect=partial):
            first = self.service.confirm_batch('1', 'library', check['checkId'], selections, 'partial-download')
        self.assertEqual([row['status'] for row in first['rows']], ['failed', 'confirmed'])
        latest = self.service.view('1')['comparison']
        second = self.service.confirm_batch('1', 'library', latest['checkId'], selections, 'retry-download')
        self.assertEqual([row['status'] for row in second['rows']], ['confirmed', 'skipped'])
        self.assertEqual(second['comparison']['counts']['exists'], 2)
        self.assertEqual(self.cloud.calls, [])

    def test_interrupted_confirmation_receipt_resumes_with_original_validated_context(self):
        check = self.manual_candidates()
        selections = [{'fileId': row['fileId'], 'mediaId': None} for row in self.source.rows]
        with patch.object(self.source, 'course', side_effect=workspace.canvas.CanvasError('isolated read interrupted')):
            with self.assertRaises(workspace.canvas.CanvasError):
                self.service.confirm_batch('1', 'library', check['checkId'], selections, 'resumable-confirm')
        self.service.compare('1')
        recovered = self.service.confirm_batch('1', 'library', check['checkId'], selections, 'resumable-confirm')
        self.assertTrue(all(row['status'] == 'confirmed' for row in recovered['rows']))
        self.assertEqual(self.cloud.calls, [])

    def test_confirmation_allows_separate_pdf_and_docx_and_receipt_survives_process_reload(self):
        self.source = SemesterSource()
        self.service.link('88', 'library', 'target')
        check = self.manual_candidates_for_course('88')
        selections = [{'fileId': file['fileId'], 'mediaId': None} for file in self.source.rows]
        result = self.service.confirm_batch('88', 'library', check['checkId'], selections, 'two-formats')
        self.assertEqual(result['comparison']['counts']['exists'], 2)
        mappings = self.service.store().data['items']
        self.assertNotEqual(mappings['88:2']['mediaId'], mappings['88:3']['mediaId'])
        script = '''import json,sys
from pathlib import Path
from ima_workspace import ImaWorkspace
p=Path(sys.argv[1]);r=Path(sys.argv[2])
service=ImaWorkspace(path=p,recovery=r,client_factory=lambda:None,source_factory=lambda:None)
result=service.confirm_batch('88','library',sys.argv[3],json.loads(sys.argv[4]),'two-formats')
assert result['complete'] and len(result['rows'])==2
assert all(row['status']=='confirmed' for row in result['rows'])
print('PERSISTED_USER_CONFIRMATIONS_NO_API_CALLS')
'''
        child = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(self.service.path), str(self.service.recovery),
                                check['checkId'], json.dumps(selections)], capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn('NO_API_CALLS', child.stdout)

    def manual_candidates_for_course(self, course_id):
        for index, file in enumerate(self.source.rows):
            self.cloud.entries.append({'kind': 'file', 'folderId': None, 'mediaId': 'manual-' + str(index),
                'name': file['fileName'], 'path': '/历史/' + file['fileName'], 'parentFolderId': 'target'})
        return self.service.compare(course_id)

    def test_confirmation_and_copy_reject_stale_source_or_remote_changes_before_execution(self):
        self.manual_candidates()
        preview = self.service.preview(self.pairs(), 'library', 'target')
        resolved = self.service.resolve_preview(preview['token'], [
            {'courseId': '1', 'fileId': '2', 'action': 'confirm', 'mediaId': 'manual-0'},
            {'courseId': '1', 'fileId': '3', 'action': 'copy', 'mediaId': None}])
        self.source.rows[0]['updatedAt'] = '2027-01-01T00:00:00Z'
        self.cloud.entries = [entry for entry in self.cloud.entries if entry.get('mediaId') != 'manual-1']
        self.service.start(resolved['token'], 'changed-after-decision')
        self.service.worker.join(5)
        rows = self.service.view('1')['job']['rows']
        self.assertEqual([row['error']['code'] for row in rows], ['SOURCE_CONFLICT', 'REMOTE_MISSING'])
        self.assertEqual(self.cloud.calls, [])

    def test_copy_after_target_deletion_fails_without_falling_back_or_writing(self):
        self.manual_candidates()
        preview = self.service.preview(self.pairs(), 'library', 'target')
        resolved = self.service.resolve_preview(preview['token'], [
            {'courseId': '1', 'fileId': row['fileId'], 'action': 'copy', 'mediaId': None} for row in self.source.rows])
        self.cloud.entries = [row for row in self.cloud.entries if row.get('folderId') != 'target']
        self.service.start(resolved['token'], 'target-deleted')
        self.service.worker.join(5)
        self.assertTrue(all(row['error']['code'] == 'TARGET_MISSING' for row in self.service.view('1')['job']['rows']))
        self.assertEqual(self.cloud.calls, [])

    def test_http_batch_confirmation_and_preview_resolution_use_restricted_authenticated_routes(self):
        import bridge
        import threading
        check = self.manual_candidates()
        with patch.object(bridge, 'IMA_WORKSPACE', self.service), patch.object(bridge, 'DESKTOP_TOKEN', 'SYNTHETIC_TOKEN'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def post(route, body, token='SYNTHETIC_TOKEN'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                try:
                    connection.request('POST', '/api/ima/v1/workspace/' + route, body=json.dumps(body), headers={
                        'Host': '127.0.0.1:8182', 'Origin': 'http://127.0.0.1:8180',
                        'X-Desktop-Token': token, 'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            try:
                body = {'courseId': '1', 'libraryId': 'library', 'checkId': check['checkId'],
                        'selections': [{'fileId': '2', 'mediaId': None}], 'requestId': 'http-confirm'}
                self.assertEqual(post('confirm-batch', body, 'wrong-token')[0], 403)
                self.assertEqual(post('confirm-batch', {**body, 'url': 'https://example.test'})[0], 400)
                status, result = post('confirm-batch', body)
                self.assertEqual(status, 200)
                self.assertEqual(result['data']['rows'][0]['status'], 'confirmed')
                status, result = post('preview', {'files': self.pairs(), 'libraryId': 'library', 'folderId': 'target', 'newVersion': False})
                self.assertEqual(status, 200)
                preview = result['data']
                self.assertEqual(len(preview['conflicts']), 1)
                status, result = post('resolve-preview', {'token': preview['token'], 'decisions': [
                    {'courseId': '1', 'fileId': '3', 'action': 'skip', 'mediaId': None}]})
                self.assertEqual(status, 200)
                status, result = post('start', {'token': result['data']['token'], 'requestId': 'http-decisions'})
                self.assertEqual(status, 200)
                self.service.worker.join(5)
                self.assertEqual([row['status'] for row in self.service.view('1')['job']['rows']], ['skipped', 'omitted'])
                self.assertEqual(self.cloud.calls, [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def upload_batch(self, request='request1', new_version=False):
        preview = self.service.preview(self.pairs(), 'library', 'target', new_version)
        job = self.service.start(preview['token'], request)
        self.service.worker.join(5)
        self.assertFalse(self.service.worker.is_alive())
        return self.service.view('1')['job']

    def test_complete_check_and_uploaded_rows_leave_recommendation(self):
        first = self.service.compare('1')
        self.assertEqual(first['counts']['unsynced'], 2)
        job = self.upload_batch()
        self.assertEqual(job['phase'], 'complete')
        self.assertTrue(all(row['status'] == 'success' for row in job['rows']))
        latest = self.service.view('1')['comparison']
        self.assertEqual(latest['counts']['exists'], 2)
        self.assertEqual(latest['counts']['unsynced'], 0)

    def test_new_semester_empty_library_first_save_repeated_process_restore_and_moved_ids(self):
        self.source = SemesterSource()
        self.cloud.entries = []
        self.service.link('88', 'library', 'library')
        self.assertEqual(self.service.compare('88')['counts']['unsynced'], 2)
        preview = self.service.preview(self.pairs(), 'library', 'library')
        self.service.start(preview['token'], 'semester-first')
        self.service.worker.join(5)
        self.assertEqual(self.service.view('88')['comparison']['counts']['exists'], 2)
        self.assertEqual(len(self.service.store().data['items']), 2)
        before = len(self.cloud.calls)
        preview = self.service.preview(self.pairs(), 'library', 'library')
        self.service.start(preview['token'], 'semester-repeat')
        self.service.worker.join(5)
        self.assertTrue(all(row['status'] == 'skipped' for row in self.service.view('88')['job']['rows']))
        self.assertEqual(len(self.cloud.calls), before)
        fixture = self.directory / 'cloud.json'
        fixture.write_text(json.dumps(self.cloud.entries), encoding='utf-8')
        script = '''import json,sys
from pathlib import Path
from ima_workspace import ImaWorkspace
from test_ima_workspace import SemesterSource,WorkspaceCloud
cloud=WorkspaceCloud();cloud.entries=json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
service=ImaWorkspace(path=Path(sys.argv[1]),recovery=Path(sys.argv[2]),source_factory=SemesterSource,client_factory=lambda:cloud)
assert service.compare('88')['counts']['exists']==2
pairs=[{'courseId':'88','fileId':file_id} for file_id in ['2','3']]
preview=service.preview(pairs,'library','library');service.start(preview['token'],'semester-process');service.worker.join(5)
assert all(row['status']=='skipped' for row in service.view('88')['job']['rows'])
assert not cloud.calls
print('RESTORED_AND_SKIPPED_WITH_ZERO_WRITES')
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(self.service.path),
                                 str(self.service.recovery), str(fixture)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ZERO_WRITES', result.stdout)
        for row in self.cloud.entries:
            row.update(parentFolderId='user-archive', path='/用户改名后的目录/' + row['name'])
        moved = self.service.compare('88')
        self.assertTrue(all(row['status'] == 'exists' and row['path'].startswith('/用户改名后的目录/') for row in moved['rows']))
        self.assertEqual(len(self.cloud.calls), before)
        self.source.payloads['2'] = PDF.replace(b'synthetic', b'different')
        self.source.rows[0]['updatedAt'] = '2027-02-02T00:00:00Z'
        self.assertEqual(self.service.compare('88')['rows'][0]['status'], 'new_version')
        new_file = copy.deepcopy(self.source.rows[0]);new_file.update(fileId='4', fileName='第二次作业.pdf')
        self.source.rows.append(new_file)
        self.assertEqual(self.service.compare('88')['rows'][-1]['status'], 'unsynced')

    def test_new_course_and_library_do_not_borrow_old_course_file_id_mapping(self):
        self.upload_batch()
        other = copy.deepcopy(self.source.rows[0]);other.update(courseId='89', courseName='新课程')
        self.source.rows.append(other)
        original_tree = self.cloud.tree
        with patch.object(self.cloud, 'libraries', return_value=[{'libraryId': 'library', 'name': '旧库'}, {'libraryId': 'new-library', 'name': '新空白库'}]), \
                patch.object(self.cloud, 'tree', side_effect=lambda library: [] if library == 'new-library' else original_tree(library)):
            self.service.link('89', 'new-library', 'new-library')
            result = self.service.compare('89')
            self.assertEqual(result['counts']['exists'], 0)
            self.assertEqual(result['counts']['unsynced'], 1)
            self.assertEqual(self.service.view('1')['links']['1']['libraryId'], 'library')

    def test_test_directories_are_marked_by_owned_ids_including_descendants(self):
        self.cloud.entries.extend([
            {'kind': 'folder', 'folderId': 'owned', 'parentFolderId': 'library', 'name': '已改名测试', 'path': '/已改名测试'},
            {'kind': 'folder', 'folderId': 'child', 'parentFolderId': 'owned', 'name': '子目录', 'path': '/已改名测试/子目录'},
            {'kind': 'folder', 'folderId': 'ordinary', 'parentFolderId': 'library', 'name': 'Workbench-IMA-Test-same-name', 'path': '/普通'},
        ])
        ledger = {'libraryId': 'library', 'runId': 'isolated', 'objects': {'root': {'kind': 'folder', 'id': 'owned', 'runId': 'isolated'}}}
        with patch.object(workspace, 'read_ledger', return_value=ledger):
            rows = self.service.tree('library')['entries']
        self.assertEqual({row['folderId'] for row in rows if row['isTestDirectory']}, {'owned', 'child'})
        self.assertEqual(self.cloud.calls, [])

    def test_unreadable_source_metadata_keeps_mapping_and_other_successful_rows(self):
        self.upload_batch()
        before = len(self.cloud.calls)
        media_ids = {key: item['mediaId'] for key, item in self.service.store().data['items'].items()}
        self.source.rows[0].update(metadataUnavailable=True, supported=False)
        comparison = self.service.compare('1')
        self.assertEqual(comparison['rows'][0]['status'], 'review')
        self.assertEqual(comparison['rows'][0]['error']['code'], 'SOURCE_METADATA_UNAVAILABLE')
        self.assertEqual(comparison['rows'][1]['status'], 'exists')
        self.assertEqual(media_ids, {key: item['mediaId'] for key, item in self.service.store().data['items'].items()})
        self.assertEqual(len(self.cloud.calls), before)

    def test_unknown_history_name_is_candidate_and_user_confirmation_survives_reload(self):
        self.cloud.entries.append({'kind': 'file', 'folderId': None, 'mediaId': 'manual-file',
                                   'name': self.source.rows[0]['fileName'], 'path': '/archive/manual', 'parentFolderId': 'target'})
        result = self.service.compare('1')
        self.assertEqual(result['rows'][0]['status'], 'exists')
        self.assertEqual(result['rows'][0]['association'], 'user_name_rule')
        self.assertIsNone(result['rows'][0]['candidates'][0]['size'])
        self.service.confirm('1', '2', 'library', 'manual-file', result['checkId'], True)
        reloaded = workspace.ImaWorkspace(path=self.service.path, recovery=self.service.recovery,
            source_factory=lambda: self.source, client_factory=lambda: self.cloud)
        result = reloaded.compare('1')
        self.assertEqual(result['rows'][0]['status'], 'exists')
        self.assertEqual(result['rows'][0]['association'], 'user_confirmed')
        self.assertEqual(self.cloud.calls, [])

    def test_reject_candidate_changes_status_and_old_check_id_is_rejected(self):
        self.cloud.entries.append({'kind': 'file', 'folderId': None, 'mediaId': 'manual',
            'name': self.source.rows[0]['fileName'], 'path': '/manual', 'parentFolderId': 'target'})
        result = self.service.compare('1')
        updated = self.service.confirm('1', '2', 'library', 'manual', result['checkId'], False)
        self.assertEqual(updated['rows'][0]['status'], 'exists')
        with self.assertRaises(ima.ImaError):
            self.service.confirm('1', '2', 'library', 'manual', result['checkId'], True)

    def test_partial_read_keeps_timestamp_and_never_claims_not_found(self):
        first = self.service.compare('1')
        self.cloud.tree_error = True
        latest = self.service.compare('1')
        self.assertTrue(latest['stale'])
        self.assertEqual(latest['checkedAt'], first['checkedAt'])
        self.assertEqual(latest['error']['code'], 'INCOMPLETE_LIST')
        self.assertEqual(latest['rows'], first['rows'])

    def test_changed_metadata_uses_content_fingerprint_before_marking_new_version(self):
        self.upload_batch()
        self.source.rows[0]['updatedAt'] = '2026-11-11T00:00:00Z'
        self.assertEqual(self.service.compare('1')['rows'][0]['status'], 'exists')
        self.source.rows[0]['updatedAt'] = '2026-11-11T01:00:00Z'
        self.source.payload = PDF.replace(b'synthetic', b'different')
        self.assertEqual(self.service.compare('1')['rows'][0]['status'], 'new_version')

    def test_manual_move_and_renamed_folder_update_path(self):
        self.upload_batch()
        self.cloud.entries[0].update(name='改名后', path='/改名后')
        for row in self.cloud.entries[1:]:
            row.update(path='/改名后/' + row['name'])
        result = self.service.compare('1')
        self.assertTrue(all(row['status'] == 'exists' and row['path'].startswith('/改名后') for row in result['rows']))
        self.assertEqual(self.service.view('2')['comparison'], None)

    def test_duplicate_job_request_and_repeat_upload_do_not_duplicate(self):
        job = self.upload_batch()
        before = len(self.cloud.calls)
        duplicate = self.service.start('expired-preview', 'request1')
        self.assertEqual(duplicate['id'], job['id'])
        again = self.upload_batch('request2')
        self.assertTrue(all(row['status'] == 'skipped' for row in again['rows']))
        self.assertEqual(len(self.cloud.calls), before)

    def test_same_start_request_returns_current_job_even_while_worker_holds_store_lock(self):
        job = self.upload_batch('stable-locked-request')
        with ima.store_lock(self.service.path):
            reused = self.service.start('old-token', 'stable-locked-request')
        self.assertEqual(reused['id'], job['id'])

    def test_failed_items_only_are_retried(self):
        self.cloud.fail = 'create_media_reject'
        job = self.upload_batch()
        self.assertEqual([row['status'] for row in job['rows']], ['failed', 'success'])
        self.service.retry(job['id'])
        self.service.worker.join(5)
        latest = self.service.view('1')['job']
        self.assertTrue(all(row['status'] == 'success' for row in latest['rows']))
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 2)

    def test_unknown_add_is_reconciled_before_any_retry(self):
        self.cloud.fail = 'add_knowledge_unknown'
        job = self.upload_batch()
        self.assertEqual(job['rows'][0]['status'], 'unknown')
        before = len(self.cloud.calls)
        latest = self.service.reconcile(job['id'])
        self.assertEqual(latest['rows'][0]['status'], 'success')
        self.assertEqual(len(self.cloud.calls), before)

    def test_old_active_job_is_interrupted_without_replay(self):
        store = self.service.store()
        store.data['job'] = {'id': 'old', 'workerId': 'old-process', 'requestId': 'old', 'phase': 'running',
            'rows': [{'source': self.source.rows[0], 'status': 'running'}, {'source': self.source.rows[1], 'status': 'queued'}]}
        store.save()
        result = self.service.view('1')['job']
        self.assertEqual(result['phase'], 'interrupted')
        self.assertEqual([row['status'] for row in result['rows']], ['unknown', 'not_run'])
        self.assertEqual(self.cloud.calls, [])

    def test_ended_worker_releases_queued_job_for_safe_recovery(self):
        import threading
        ended = threading.Thread(target=lambda: None)
        ended.start()
        ended.join()
        self.service.worker = ended
        store = self.service.store()
        store.data['job'] = {'id': 'ended', 'workerId': self.service.worker_id, 'requestId': 'ended',
                             'phase': 'checking', 'rows': [{'source': self.source.rows[0], 'status': 'skipped'}]}
        store.save()
        result = self.service.view('1')['job']
        self.assertEqual(result['phase'], 'interrupted')
        self.assertEqual(result['rows'][0]['status'], 'skipped')
        self.assertEqual(self.cloud.calls, [])

    def test_folder_create_rename_and_repeat_request_are_read_back(self):
        created = self.service.folder('create', 'library', 'target', '合成测试', 'create1')
        before = len(self.cloud.calls)
        self.assertEqual(self.service.folder('create', 'library', 'target', '合成测试', 'create1')['objectId'], created['objectId'])
        self.assertEqual(len(self.cloud.calls), before)
        renamed = self.service.folder('rename', 'library', created['objectId'], '重命名', 'rename1')
        self.assertEqual(renamed['status'], 'passed')

    def test_move_checks_item_return_code_and_actual_destination(self):
        self.upload_batch()
        folder = self.service.folder('create', 'library', 'target', '归档', 'folder')
        media_ids = [item['mediaId'] for item in self.service.store().data['items'].values()]
        preview = self.service.move_preview('library', media_ids, 'library', folder['objectId'])
        self.service.start(preview['token'], 'move1')
        self.service.worker.join(5)
        job = self.service.view('1')['job']
        self.assertTrue(all(row['status'] == 'success' and row['retCode'] == 0 and row['readbackVerified'] for row in job['rows']))
        self.assertTrue(all(item['actualFolderId'] == folder['objectId'] for item in self.service.store().data['items'].values()))

    def test_unsupported_file_is_rejected_in_preview(self):
        self.source.rows[0]['supported'] = False
        with self.assertRaises(sync.SyncError) as error:
            self.service.preview(self.pairs(), 'library', 'target')
        self.assertEqual(error.exception.code, 'UNSUPPORTED_FILE')
        self.assertEqual(self.cloud.calls, [])

    def test_explicit_new_version_preserves_old_media_and_default_does_not_replace(self):
        self.upload_batch()
        old_ids={item['mediaId'] for item in self.service.store().data['items'].values()}
        self.source.payload=PDF.replace(b'synthetic',b'different')
        for row in self.source.rows:row['updatedAt']='2026-11-11T03:00:00Z'
        failed=self.upload_batch('version-without-confirmation')
        self.assertTrue(all(row['error']['code']=='CONTENT_CHANGED' and row['retryable'] is False for row in failed['rows']))
        result=self.upload_batch('explicit-version',new_version=True)
        self.assertTrue(all(row['status']=='success' for row in result['rows']))
        self.assertTrue(old_ids.issubset({row['mediaId'] for row in self.cloud.entries if row['kind']=='file'}))
        self.assertTrue(all(len(item['versions'])==1 for item in self.service.store().data['items'].values()))

    def test_uncertain_folder_create_is_reconciled_without_second_create(self):
        operation=self.cloud._request
        def uncertain(name,body,**kwargs):
            result=operation(name,body,**kwargs)
            if name=='create_folder':raise ima.ImaError('OUTCOME_UNKNOWN')
            return result
        with patch.object(self.cloud,'_request',side_effect=uncertain):
            created=self.service.folder('create','library','target','未知结果测试','folder-request')
        self.assertEqual(created['status'],'passed')
        before=len(self.cloud.calls)
        self.service.folder('create','library','target','未知结果测试','folder-request')
        self.assertEqual(len(self.cloud.calls),before)

    def test_move_nonzero_item_code_remains_failure_and_can_retry(self):
        self.upload_batch()
        folder=self.service.folder('create','library','target','目标','folder-request')
        media_ids=[item['mediaId'] for item in self.service.store().data['items'].values()]
        operation=self.cloud._request
        def rejected(name,body,**kwargs):
            if name=='move_knowledge':return {'move_results':{body['infos'][0]['media_id']:{'ret_code':220001}}}
            return operation(name,body,**kwargs)
        preview=self.service.move_preview('library',media_ids,'library',folder['objectId'])
        with patch.object(self.cloud,'_request',side_effect=rejected):
            self.service.start(preview['token'],'rejected-move')
            self.service.worker.join(5)
        job=self.service.view('1')['job']
        self.assertTrue(all(row['status']=='failed' and row['retCode']==220001 for row in job['rows']))
        self.service.retry(job['id']);self.service.worker.join(5)
        self.assertTrue(all(row['status']=='success' for row in self.service.view('1')['job']['rows']))

    def test_comparison_switches_library_without_reusing_other_library_cache(self):
        old=self.service.compare('1')
        with patch.object(self.cloud,'libraries',return_value=[{'libraryId':'other','name':'其他库'}]):
            self.service.link('1','other','other')
        view=self.service.view('1')
        self.assertIsNone(view['comparison'])
        self.assertNotIn('1',view['comparisons'])
        self.assertEqual(old['libraryId'],'library')

    def test_move_success_followed_by_read_failure_is_unknown_and_read_only_reconciles(self):
        self.upload_batch()
        folder=self.service.folder('create','library','target','目标','folder-request')
        media_id=next(iter(self.service.store().data['items'].values()))['mediaId']
        operation=self.cloud._request
        tree=self.cloud.tree
        failed=[False]
        def mutate(name,body,**kwargs):
            result=operation(name,body,**kwargs)
            if name=='move_knowledge':failed[0]=True
            return result
        def read(library):
            if failed[0]:
                failed[0]=False
                raise ima.ImaError('FORBIDDEN')
            return tree(library)
        preview=self.service.move_preview('library',[media_id],'library',folder['objectId'])
        with patch.object(self.cloud,'_request',side_effect=mutate),patch.object(self.cloud,'tree',side_effect=read):
            self.service.start(preview['token'],'move-readback-failure');self.service.worker.join(5)
        job=self.service.view('1')['job']
        self.assertEqual(job['rows'][0]['status'],'unknown')
        self.assertEqual(job['rows'][0]['error']['readbackError'],'FORBIDDEN')
        before=len(self.cloud.calls)
        restored=self.service.reconcile(job['id'])
        self.assertEqual(restored['rows'][0]['status'],'success')
        self.assertEqual(len(self.cloud.calls),before)

    def test_add_success_followed_by_read_failure_is_unknown_not_auth_retry(self):
        operation=self.cloud._request;tree=self.cloud.tree;failed=[False]
        def mutate(name,body,**kwargs):
            result=operation(name,body,**kwargs)
            if name=='add_knowledge':failed[0]=True
            return result
        def read(library):
            if failed[0]:
                failed[0]=False
                raise ima.ImaError('FORBIDDEN')
            return tree(library)
        with patch.object(self.cloud,'_request',side_effect=mutate),patch.object(self.cloud,'tree',side_effect=read):
            job=self.upload_batch('add-readback-failure')
        self.assertTrue(all(row['status']=='unknown' for row in job['rows']))
        before=len(self.cloud.calls)
        self.service.reconcile(job['id'])
        self.assertEqual(len(self.cloud.calls),before)
        self.assertTrue(all(item['stage']=='committed' for item in self.service.store().data['items'].values()))

    def test_earlier_unknown_job_remains_reconcilable_after_new_task(self):
        self.cloud.fail='add_knowledge_unknown'
        first=self.upload_batch('uncertain-first')
        self.assertEqual(first['rows'][0]['status'],'unknown')
        self.upload_batch('next-task')
        view=self.service.view('1')
        self.assertEqual(len(view['unresolvedJobs']),1)
        self.assertEqual(view['unresolvedJobs'][0]['id'],first['id'])
        before=len(self.cloud.calls)
        recovered=self.service.reconcile(first['id'])
        self.assertTrue(all(row['status']=='success' for row in recovered['rows']))
        self.assertEqual(len(self.cloud.calls),before)
        self.assertEqual(self.service.view('1')['unresolvedJobs'],[])

    def test_candidate_confirmation_rejects_changed_source_and_changed_link(self):
        self.cloud.entries.append({'kind':'file','folderId':None,'mediaId':'manual-file',
            'name':self.source.rows[0]['fileName'],'path':'/manual','parentFolderId':'target'})
        result=self.service.compare('1')
        self.source.rows[0]['updatedAt']='2026-11-11T10:00:00Z'
        with self.assertRaises(sync.SyncError) as error:
            self.service.confirm('1','2','library','manual-file',result['checkId'],True)
        self.assertEqual(error.exception.code,'SOURCE_CONFLICT')
        self.assertEqual(self.service.store().data['items'],{})
        store=self.service.store();store.data['links']['1']['libraryId']='other';store.save()
        with self.assertRaises(ima.ImaError):
            self.service.confirm('1','2','library','manual-file',result['checkId'],True)
        self.assertEqual(self.cloud.calls,[])

    def test_production_handler_reuses_local_auth_and_rejects_arbitrary_fields(self):
        import bridge
        with patch.object(bridge, 'IMA_WORKSPACE', self.service), patch.object(bridge, 'DESKTOP_TOKEN', 'SYNTHETIC_TOKEN'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
            import threading
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for origin, token, body, expected in [
                    ('http://127.0.0.1:8180', 'SYNTHETIC_TOKEN', {'courseId': '1'}, 200),
                    ('http://127.0.0.1:8180', 'bad', {'courseId': '1'}, 403),
                    ('https://evil.example', 'SYNTHETIC_TOKEN', {'courseId': '1'}, 403),
                    ('http://127.0.0.1:8180', 'SYNTHETIC_TOKEN', {'url': 'https://evil.example'}, 400)]:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    connection.request('POST', '/api/ima/v1/workspace/view', body=json.dumps(body), headers={
                        'Host': '127.0.0.1:8182', 'Origin': origin, 'X-Desktop-Token': token, 'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    payload = response.read()
                    self.assertNotIn(b'apiKey', payload)
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class HiddenAttachmentTests(unittest.TestCase):
    def test_hidden_assignment_docx_is_supported_when_official_lock_is_false(self):
        source = workspace.WorkspaceSource.__new__(workspace.WorkspaceSource)
        source.session, source.download_urls = Mock(), {}
        file = {'id': 1714743, 'display_name': '第一次作业.docx', 'size': 15616,
                'content-type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'locked_for_user': False, 'hidden_for_user': True, 'lock_at': None, 'unlock_at': None,
                'url': 'https://canvas.shufe.edu.cn/files/1714743/download'}
        with patch.object(workspace.canvas, 'read_collection', side_effect=[[{'id': 41554, 'name': '财务管理（0111）'}], [file], []]), \
             patch.object(workspace.canvas, 'load_private', return_value=None):
            row = source.course('41554')[0]
        self.assertFalse(row['locked'])
        self.assertTrue(row['supported'])
    def test_hidden_docx_previews_and_saves_through_production_source_with_isolated_cloud(self):
        from test_ima_bridge import Response
        with tempfile.TemporaryDirectory() as directory:
            source = workspace.WorkspaceSource.__new__(workspace.WorkspaceSource)
            source.session, source.download_urls = Mock(), {}
            payload = b'PK\x03\x04' + bytes(15612)
            download = Response(content=payload);download.headers = {'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
            source.session.get.return_value = download
            metadata = {'id': 1714743, 'display_name': '第一次作业.docx', 'size': 15616,
                        'content-type': download.headers['Content-Type'], 'hidden_for_user': True,
                        'locked_for_user': False, 'url': 'https://canvas.shufe.edu.cn/files/1714743/download'}
            def collection(session, path, params=None):
                if path.endswith('/courses'):return [{'id': 41554, 'name': '财务管理（0111）'}]
                if path.endswith('/files'):return [metadata]
                return []
            cloud = WorkspaceCloud()
            service = workspace.ImaWorkspace(path=Path(directory)/'map.dpapi', recovery=Path(directory)/'recovery.dpapi',
                                            source_factory=lambda: source, client_factory=lambda: cloud)
            with patch.object(workspace.canvas, 'read_collection', side_effect=collection), \
                 patch.object(workspace.canvas, 'load_private', return_value=None), \
                 patch.object(sync, 'upload_bytes', side_effect=upload):
                service.link('41554', 'library', 'target')
                preview = service.preview([{'courseId': '41554', 'fileId': '1714743'}], 'library', 'target')
                self.assertTrue(preview['sources'][0]['supported'])
                service.start(preview['token'], 'hidden-docx');service.worker.join(5)
                result = service.view('41554')
                self.assertEqual(result['job']['rows'][0]['status'], 'success')
                self.assertEqual(result['comparison']['counts']['exists'], 1)
                self.assertEqual(sum(op == 'add_knowledge' for op, _ in cloud.calls), 1)
                self.assertEqual(source.session.get.call_args.kwargs['headers']['Accept'], download.headers['Content-Type'])

    def test_authorized_hidden_member_download_fallback_and_true_permission_failures(self):
        source = workspace.WorkspaceSource.__new__(workspace.WorkspaceSource)
        source.session, source.download_urls = Mock(), {'2': 'https://canvas.shufe.edu.cn/files/2/download',
                                                        '3': 'https://canvas.shufe.edu.cn/files/3/download'}
        members = [{'fileId': value, 'fileName': '作业.pdf', 'supported': True, 'size': len(PDF)} for value in ['2', '3']]
        file = {**members[0], 'courseId': '1', 'groupId': 'isolated-group', 'memberIds': ['2', '3'], 'members': members, 'sources': []}
        from test_ima_bridge import Response
        success = Response(content=PDF);success.headers = {'Content-Type': 'application/pdf'}
        source.session.get.side_effect = [Response(status=403), success]
        payload, checked = source.download(file)
        self.assertEqual(payload, PDF);self.assertEqual(checked['actualFileId'], '3')
        self.assertEqual(checked['memberAttempts'], [{'fileId': '2', 'code': 'DOWNLOAD_FORBIDDEN'}])
        source.session.get.side_effect = [Response(status=403), Response(status=403)]
        with self.assertRaises(sync.SyncError) as error:
            source.download(file)
        self.assertEqual(error.exception.code, 'DOWNLOAD_FORBIDDEN')
        self.assertEqual(len(error.exception.member_attempts), 2)
        source.session.get.side_effect = [Response(status=401), Response(content=PDF)]
        with self.assertRaises(workspace.canvas.LoginRequired):
            source.download(file)
        source.session.get.reset_mock();source.session.get.side_effect = None
        for member in members:member['supported'] = False
        with self.assertRaises(sync.SyncError):source.download({**file, 'locked': True})
        source.session.get.assert_not_called()



class FormatsTests(unittest.TestCase):
    def test_common_official_formats_and_limit(self):
        for extension in ['pdf', 'ppt', 'pptx', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt', 'md', 'png', 'epub']:
            self.assertTrue(sync.file_format('file.' + extension, 100)['mediaType'])
        for name, size in [('file.zip', 100), ('file.exe', 100), ('file.pdf', 21 * 1024 * 1024)]:
            with self.assertRaises(sync.SyncError):
                sync.file_format(name, size)

    def test_content_signatures_and_login_html(self):
        self.assertTrue(sync.valid_content(b'PK\x03\x04synthetic', 'application/octet-stream', {'fileName': 'file.pptx', 'size': 13}))
        with self.assertRaises(sync.SyncError):
            sync.valid_content(b'<html>login', 'text/html', {'fileName': 'file.pptx', 'size': 11})


if __name__ == '__main__':
    unittest.main()
