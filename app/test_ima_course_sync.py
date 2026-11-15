"""Isolated batch regressions. No real credentials or cloud writes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import requests

import ima_course_sync as sync
import ima_bridge as ima
from test_ima_bridge import Response

PDF = b'%PDF-1.4\nsynthetic only\n%%EOF\n'


class Source:
    def __init__(self):
        self.payload = PDF
        self.rows = [{'courseId': '1', 'courseName': sync.COURSE, 'fileId': str(index),
                      'fileName': name, 'size': len(PDF), 'contentType': 'application/pdf',
                      'createdAt': '2026-11-10T15:00:00Z', 'updatedAt': '2026-11-10T15:00:00Z'}
                     for index, name in enumerate(sync.FILE_NAMES, 2)]

    def resolve(self, selection=None):
        return copy.deepcopy(self.rows)

    def download(self, source):
        return self.payload, {'status': 'passed', 'sha256': hashlib.sha256(self.payload).hexdigest(),
                              'size': len(self.payload), 'httpStatus': 200}


class Cloud:
    def __init__(self):
        self.calls = []
        self.entries = [{'kind': 'folder', 'folderId': 'target', 'mediaId': 'target',
                         'name': sync.FOLDER, 'path': '/' + sync.FOLDER, 'parentFolderId': 'library'}]
        self.fail = None

    def libraries(self):
        return [{'libraryId': 'library', 'name': sync.LIBRARY}]

    def tree(self, library):
        return copy.deepcopy(self.entries)

    def _request(self, operation, body, **kwargs):
        self.calls.append((operation, copy.deepcopy(body)))
        if self.fail == operation + '_reject':
            self.fail = None
            raise ima.ImaError('RATE_LIMITED')
        if operation == 'create_media':
            result = {'media_id': 'media-' + str(len(self.calls)), 'cos_credential': {'cos_key': 'synthetic.pdf'}}
        elif operation == 'add_knowledge':
            self.entries.append({'kind': 'file', 'folderId': None, 'mediaId': body['media_id'],
                                 'name': body['title'], 'path': '/' + sync.FOLDER + '/' + body['title'],
                                 'parentFolderId': body['folder_id']})
            result = {'media_id': body['media_id']}
        else:
            raise AssertionError(operation)
        if self.fail == operation + '_unknown':
            self.fail = None
            raise ima.ImaError('OUTCOME_UNKNOWN')
        return result

    def search(self, library, query):
        return [copy.deepcopy(row) for row in self.entries if row['kind'] == 'file' and query == row['name']]

    def media_access(self, media_id, **kwargs):
        return {'urlProvided': False, 'readVerified': False, 'requiresImaClient': True}


def upload(credential, payload, content_type, *, on_send):
    on_send({'stage': 'cos_upload', 'uploadRequestSent': True})
    return {'stage': 'cos_upload', 'httpStatus': 200}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.cloud, self.source = Cloud(), Source()
        self.upload = patch.object(sync, 'upload_bytes', side_effect=upload).start()
        self.addCleanup(patch.stopall)

    def runner(self):
        store = sync.SyncStore(self.directory / 'mapping.dpapi', self.directory / 'recovery.dpapi')
        return sync.CourseSync(self.cloud, self.source, store)

    def run_batch(self):
        runner = self.runner()
        return runner.run(runner.preview())

    def test_success_skip_reload_and_encrypted_mapping(self):
        first = self.run_batch()
        self.assertEqual([r['result'] for r in first['outcomes']], ['synced', 'synced'])
        calls = len(self.cloud.calls)
        self.assertEqual([r['result'] for r in self.run_batch()['outcomes']], ['skipped', 'skipped'])
        self.assertEqual(len(self.cloud.calls), calls)
        raw = (self.directory / 'mapping.dpapi').read_bytes()
        self.assertNotIn(sync.FILE_NAMES[0].encode('utf8'), raw)
        for item in first['items']:
            self.assertEqual(item['stage'], 'committed')
            self.assertEqual(item['actualFolderId'], item['initialTargetFolderId'])
            self.assertEqual(item['stages']['parsing']['status'], 'unverified')
            self.assertNotIn('cosKey', item)

    def test_manual_move_and_folder_rename_keep_id_association(self):
        self.run_batch()
        self.cloud.entries[0].update(name='手动改名', path='/手动改名')
        for row in self.cloud.entries[1:]:
            row.update(parentFolderId='manual-folder', path='/手动归档/' + row['name'])
        calls = len(self.cloud.calls)
        result = self.run_batch()
        self.assertEqual([r['result'] for r in result['outcomes']], ['skipped', 'skipped'])
        self.assertEqual(len(self.cloud.calls), calls)
        self.assertTrue(all(r['actualFolderId'] == 'manual-folder' for r in result['items']))
        self.assertTrue(all(r['initialTargetFolderId'] == 'target' for r in result['items']))

    def test_changed_content_is_not_silently_uploaded(self):
        self.run_batch()
        self.source.payload += b'changed'
        calls = len(self.cloud.calls)
        result = self.run_batch()
        self.assertTrue(all(row['code'] == 'CONTENT_CHANGED' for row in result['outcomes']))
        self.assertEqual(len(self.cloud.calls), calls)

    def test_same_name_without_mapping_is_conflict(self):
        self.cloud.entries.append({'kind': 'file', 'mediaId': 'unmapped', 'name': sync.FILE_NAMES[0],
                                   'parentFolderId': 'target', 'path': '/课件PPT/synthetic'})
        result = self.run_batch()
        self.assertEqual(result['outcomes'][0]['code'], 'NAME_CONFLICT')
        self.assertEqual(result['outcomes'][1]['result'], 'synced')

    def test_missing_target_and_missing_committed_media_never_reupload(self):
        self.run_batch()
        calls = len(self.cloud.calls)
        folder = self.cloud.entries.pop(0)
        with self.assertRaises(sync.SyncError) as error:
            self.runner().preview()
        self.assertEqual(error.exception.code, 'TARGET_MISSING')
        self.cloud.entries = [folder]
        result = self.run_batch()
        self.assertTrue(all(row['code'] == 'REMOTE_MISSING' for row in result['outcomes']))
        self.assertEqual(len(self.cloud.calls), calls)

    def test_uncertain_allocation_is_not_repeated_successful_second_is_kept(self):
        self.cloud.fail = 'create_media_unknown'
        first = self.run_batch()
        calls = len(self.cloud.calls)
        second = self.run_batch()
        self.assertEqual(first['outcomes'][0]['result'], 'failed')
        self.assertEqual(second['outcomes'][0]['code'], 'OUTCOME_UNKNOWN')
        self.assertEqual(second['outcomes'][1]['result'], 'skipped')
        self.assertEqual(len(self.cloud.calls), calls)

    def test_explicit_allocation_rejection_can_retry_failed_item_only(self):
        self.cloud.fail = 'create_media_reject'
        self.run_batch()
        result = self.run_batch()
        self.assertEqual([row['result'] for row in result['outcomes']], ['synced', 'skipped'])
        self.assertEqual(sum(op == 'add_knowledge' for op, _ in self.cloud.calls), 2)

    def test_uncertain_add_is_read_back_without_duplicate(self):
        self.cloud.fail = 'add_knowledge_unknown'
        self.run_batch()
        calls = len(self.cloud.calls)
        result = self.run_batch()
        self.assertEqual([row['result'] for row in result['outcomes']], ['reconciled', 'skipped'])
        self.assertEqual(len(self.cloud.calls), calls)

    def test_explicit_add_rejection_reuses_uploaded_media(self):
        self.cloud.fail = 'add_knowledge_reject'
        self.run_batch()
        uploads = self.upload.call_count
        result = self.run_batch()
        self.assertEqual([row['result'] for row in result['outcomes']], ['synced', 'skipped'])
        self.assertEqual(self.upload.call_count, uploads)
        self.assertEqual(sum(op == 'create_media' for op, _ in self.cloud.calls), 2)

    def test_reselected_target_applies_to_pending_ingestion_and_keeps_first_target(self):
        self.cloud.fail = 'add_knowledge_reject'
        self.run_batch()
        runner = self.runner()
        source = self.source.rows[0]
        key = runner.item_key(source, 'library')
        item = runner.store.data['items'][key]
        original_media = item['mediaId']
        uploads = self.upload.call_count
        outcome = runner.sync_one(source, {'libraryId': 'library', 'targetFolderId': 'reselected-folder'})
        self.assertEqual(outcome, 'synced')
        self.assertEqual(item['initialTargetFolderId'], 'target')
        self.assertEqual(item['actualFolderId'], 'reselected-folder')
        self.assertEqual(item['mediaId'], original_media)
        self.assertEqual(self.upload.call_count, uploads)

    def test_unknown_upload_is_not_repeated(self):
        def uncertain(credential, payload, content_type, *, on_send):
            on_send({'stage': 'cos_upload', 'uploadRequestSent': True})
            raise ima.ImaError('COS_UPLOAD_UNKNOWN')
        self.upload.side_effect = uncertain
        self.run_batch()
        calls = len(self.cloud.calls)
        self.upload.side_effect = upload
        result = self.run_batch()
        self.assertTrue(all(row['code'] == 'OUTCOME_UNKNOWN' for row in result['outcomes']))
        self.assertEqual(len(self.cloud.calls), calls)

    def test_pre_upload_failure_can_reallocate_and_retain_residual(self):
        self.upload.side_effect = ima.ImaError('COS_CREDENTIAL_EXPIRED')
        self.run_batch()
        self.upload.side_effect = upload
        result = self.run_batch()
        self.assertTrue(all(row['result'] == 'synced' for row in result['outcomes']))
        self.assertTrue(all(len(row['retiredMedia']) == 1 for row in result['items']))

    def test_save_failure_stops_writes_and_recovers_from_encrypted_fallback(self):
        runner = self.runner()
        preview = runner.preview()
        actual_save = sync.save_encrypted_json
        def fail_main(path, data):
            if path.name == 'mapping.dpapi':
                raise OSError('synthetic')
            actual_save(path, data)
        with patch.object(sync, 'save_encrypted_json', side_effect=fail_main):
            with self.assertRaises(sync.SyncError) as error:
                runner.run(preview)
        self.assertEqual(error.exception.code, 'SAVE_FAILED')
        self.assertEqual(self.cloud.calls, [])
        self.assertTrue((self.directory / 'recovery.dpapi').exists())
        self.assertIsNotNone(self.runner().store.data['batch'])
        self.assertFalse((self.directory / 'recovery.dpapi').exists())

    def test_lock_prevents_overlapping_sync(self):
        path = self.directory / 'mapping.dpapi'
        with ima.store_lock(path):
            with self.assertRaises(ima.ImaError) as error:
                with ima.store_lock(path):
                    self.fail('overlapping lock acquired')
        self.assertEqual(error.exception.code, 'SYNC_BUSY')

    def test_other_process_cannot_acquire_same_mapping_lock(self):
        path = self.directory / 'mapping.dpapi'
        script = ('from pathlib import Path\nfrom ima_bridge import store_lock,ImaError\nimport sys\n'
                  'try:\n with store_lock(Path(sys.argv[1])): print("ACQUIRED")\n'
                  'except ImaError as e: print(e.code)\n')
        with ima.store_lock(path):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(path)],
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'SYNC_BUSY')

    def test_unknown_add_without_remote_result_does_not_resend(self):
        operation = self.cloud._request
        def fail_before_add(name, body, **kwargs):
            if name == 'add_knowledge':
                raise ima.ImaError('OUTCOME_UNKNOWN')
            return operation(name, body, **kwargs)
        with patch.object(self.cloud, '_request', side_effect=fail_before_add):
            self.run_batch()
        before = len(self.cloud.calls)
        result = self.run_batch()
        self.assertTrue(all(row['code'] == 'OUTCOME_UNKNOWN' for row in result['outcomes']))
        self.assertEqual(len(self.cloud.calls), before)

    def test_after_allocation_save_failure_retains_media_for_recovery(self):
        runner = self.runner()
        preview = runner.preview()
        actual_save = sync.save_encrypted_json
        def fail_after_allocation(path, data):
            if path.name == 'mapping.dpapi' and any(row.get('mediaId') for row in data['items'].values()):
                raise OSError('synthetic')
            actual_save(path, data)
        with patch.object(sync, 'save_encrypted_json', side_effect=fail_after_allocation):
            result = runner.run(preview)
        self.assertEqual(result['outcomes'][0]['code'], 'SAVE_FAILED')
        self.assertEqual(len(result['outcomes']), 1)
        self.assertEqual(len(self.cloud.calls), 1)
        restored = self.runner().store
        item = next(iter(restored.data['items'].values()))
        self.assertEqual(item['mediaId'], 'media-1')
        self.assertEqual(item['stage'], 'allocated')

    def test_saved_results_menu_requires_no_canvas_or_ima_call(self):
        with patch.object(sync, 'STORE', self.directory / 'mapping.dpapi'), \
                patch.object(sync, 'RECOVERY', self.directory / 'recovery.dpapi'), \
                patch.object(sys, 'argv', ['ima_course_sync.py']), \
                patch('builtins.input', return_value='2'), patch('builtins.print') as output, \
                patch.object(sync, 'CanvasSource') as source, patch.object(sync, 'ImaService') as client:
            sync.main()
        source.assert_not_called()
        client.assert_not_called()
        self.assertIn('历史结果', str(output.call_args_list))

    def test_status_read_handle_cannot_overlap_atomic_mapping_replace(self):
        store=self.runner().store
        store.save()
        opened=threading.Event();release=threading.Event();writer_finished=threading.Event();failures=[]
        original_read=Path.read_bytes
        def held_read(path):
            if path==store.path and threading.current_thread().name=='ima-status-reader':
                with path.open('rb') as handle:
                    opened.set();release.wait(3);return handle.read()
            return original_read(path)
        def reader():
            try:sync.SyncStore(store.path,store.recovery,read_only=True)
            except Exception as error:failures.append(error)
        def writer():
            try:store.save()
            except Exception as error:failures.append(error)
            finally:writer_finished.set()
        with patch.object(Path,'read_bytes',held_read):
            reading=threading.Thread(target=reader,name='ima-status-reader');reading.start()
            self.assertTrue(opened.wait(2))
            writing=threading.Thread(target=writer,name='ima-state-writer');writing.start()
            try:self.assertFalse(writer_finished.wait(0.2),'mapping replacement raced an open status reader')
            finally:
                release.set();reading.join(3);writing.join(3)
        self.assertEqual(failures,[])


class DownloadTests(unittest.TestCase):
    def test_pdf_validation_rejects_html_wrong_size_and_truncation(self):
        self.assertEqual(sync.valid_pdf(PDF, 'application/pdf', len(PDF)), hashlib.sha256(PDF).hexdigest())
        for payload, content_type, size in [(b'<html>login</html>', 'text/html', 18),
                                          (PDF, 'application/pdf', len(PDF) + 1),
                                          (PDF[:-6], 'application/pdf', len(PDF) - 6),
                                          (PDF, 'application/octet-stream', len(PDF))]:
            with self.subTest(content_type=content_type), self.assertRaises(sync.SyncError):
                sync.valid_pdf(payload, content_type, size)

    def source(self, response, url='https://canvas.shufe.edu.cn/files/2/download?download_frd=1'):
        source = sync.CanvasSource.__new__(sync.CanvasSource)
        source.download_urls = {'2': url}
        source.session = Mock()
        source.session.get.return_value = response
        return source

    def test_bounded_download_uses_canvas_origin_and_pdf_headers(self):
        response = Response(content=PDF)
        response.headers = {'Content-Type': 'application/pdf'}
        source = self.source(response)
        payload, result = source.download({'fileId': '2', 'size': len(PDF)})
        self.assertEqual(payload, PDF)
        self.assertEqual(result['httpStatus'], 200)
        request = source.session.get.call_args
        self.assertFalse(request.kwargs['allow_redirects'])
        self.assertEqual(request.kwargs['headers'], {'Accept': 'application/pdf'})
        self.assertTrue(response.closed)

    def test_redirect_outside_origin_and_mismatched_file_id_are_rejected(self):
        response = Response(status=302)
        response.headers = {'Location': 'https://other.example/file'}
        source = self.source(response)
        with self.assertRaises(sync.SyncError):
            source.download({'fileId': '2', 'size': len(PDF)})
        self.assertEqual(source.session.get.call_count, 1)
        source = self.source(response, 'https://canvas.shufe.edu.cn/files/3/download')
        with self.assertRaises(sync.SyncError):
            source.download({'fileId': '2', 'size': len(PDF)})
        source.session.get.assert_not_called()

    def test_login_redirect_and_network_failure_are_distinct(self):
        response = Response(status=302)
        response.headers = {'Location': '/login/cas'}
        source = self.source(response)
        with self.assertRaises(sync.canvas.LoginRequired):
            source.download({'fileId': '2', 'size': len(PDF)})
        source.session.get.side_effect = requests.Timeout('synthetic')
        with self.assertRaises(sync.SyncError) as error:
            source.download({'fileId': '2', 'size': len(PDF)})
        self.assertEqual(error.exception.code, 'DOWNLOAD_FAILED')


if __name__ == '__main__':
    unittest.main()
