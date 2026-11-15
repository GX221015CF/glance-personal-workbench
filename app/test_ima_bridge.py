"""Synthetic IMA contracts and isolated local HTTP tests, no cloud account calls."""
import copy
import io
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import ima_bridge as ima
import ima_probe as probe

CREDS = {'clientId': 'SYNTHETIC_CLIENT', 'apiKey': 'SYNTHETIC_SECRET'}


class Response:
    def __init__(self, value=None, status=200, content=None):
        self.status_code = status
        self.content = content if content is not None else json.dumps(value).encode()
        self.closed = False

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset:offset + size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.replies.pop(0)

    def put(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.replies.pop(0)


def ok(data):
    return Response({'code': 0, 'data': data, 'msg': 'synthetic'})


class ImaTests(unittest.TestCase):
    def test_rejected_small_posts_are_drained_without_reading_unbounded_bodies(self):
        handler=SimpleNamespace(command='POST',headers={'Content-Length':'2'},
                                rfile=io.BytesIO(b'{}'),connection=Mock())
        ima.discard_small_body(handler)
        self.assertEqual(handler.rfile.read(),b'')
        handler.headers={'Content-Length':'999999999'}
        handler.rfile=io.BytesIO(b'bounded-test')
        ima.discard_small_body(handler)
        self.assertEqual(handler.rfile.tell(),0)

    def test_missing_config_status_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ima.ImaService(Path(directory) / 'config.dpapi')
            self.assertFalse(service.status()['configured'])
            with self.assertRaises(ima.ImaError):
                service.client()

    def test_verify_then_atomic_encrypted_save_and_failure_retains_old_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.dpapi'
            factory = lambda config: ima.ImaClient(config, Session([ok({'addable_knowledge_base_list': [], 'is_end': True})]))
            service = ima.ImaService(path, factory)
            self.assertTrue(service.configure(**{'client_id': CREDS['clientId'], 'api_key': CREDS['apiKey']})['verified'])
            encrypted = path.read_bytes()
            self.assertNotIn(CREDS['apiKey'].encode(), encrypted)
            self.assertTrue(service.status()['configured'])
            self.assertNotIn('clientId', json.dumps(service.status()))
            self.assertFalse(service.status()['currentLoginVerified'])
            service.client_factory = lambda config: ima.ImaClient(config, Session([Response({}, status=401)]))
            with self.assertRaises(ima.ImaError):
                service.configure('other', 'other-key')
            self.assertEqual(path.read_bytes(), encrypted)
            service.client_factory = factory
            with patch.object(ima, 'save_encrypted_json', side_effect=OSError('synthetic')):
                with self.assertRaises(ima.ImaError):
                    service.configure('other', 'other-key')
            self.assertEqual(path.read_bytes(), encrypted)
            path.write_bytes(b'corrupt')
            with self.assertRaises(ima.ImaError):
                service.status()
            self.assertEqual(path.read_bytes(), b'corrupt')

    def test_full_pagination_and_unknown_or_duplicate_records_are_not_dropped(self):
        session = Session([ok({'knowledge_list': [{'media_id': 'f1', 'title': 'file'}], 'is_end': False, 'next_cursor': 'next'}),
                           ok({'knowledge_list': [{'media_id': 'f1', 'title': 'duplicate'}, {'strange': 'unknown'},
                                                  {'folder_id': 'dir', 'name': 'folder'}], 'is_end': True})])
        rows = ima.ImaClient(CREDS, session).browse('library')
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]['kind'], 'unknown')
        self.assertEqual(rows[3]['kind'], 'folder')
        self.assertEqual(session.calls[1][1]['json']['cursor'], 'next')
        self.assertTrue(all(url.startswith(ima.BASE) for url, _ in session.calls))
        self.assertTrue(all(call['allow_redirects'] is False for _, call in session.calls))

    def test_missing_end_cursor_loops_and_partial_network_cannot_be_complete(self):
        for replies in [
            [ok({'knowledge_list': [], 'is_end': False, 'next_cursor': ''})],
            [ok({'knowledge_list': [], 'is_end': False, 'next_cursor': 'a'}), ok({'knowledge_list': [], 'is_end': False, 'next_cursor': 'a'})],
            [ok({'knowledge_list': [], 'is_end': False, 'next_cursor': 'a'}), requests.Timeout('secret')],
            [ok({'knowledge_list': []})],
        ]:
            with self.assertRaises(ima.ImaError):
                ima.ImaClient(CREDS, Session(replies)).browse('library')

    def test_search_accepts_observed_unpaged_results_including_empty(self):
        for rows in [[], [{'media_id': 'file', 'title': 'synthetic', 'media_type': 1,
                           'parent_folder_id': 'parent', 'highlight_content': 'synthetic'}]]:
            with self.subTest(count=len(rows)):
                session = Session([ok({'info_list': rows})])
                result = ima.ImaClient(CREDS, session).search('library', 'synthetic')
                self.assertEqual(len(result), len(rows))
                self.assertEqual(len(session.calls), 1)
                if result:
                    self.assertEqual(result[0]['mediaId'], 'file')
                    self.assertEqual(result[0]['parentFolderId'], 'parent')

    def test_search_keeps_explicit_pagination_and_rejects_partial_contracts(self):
        session = Session([ok({'info_list': [], 'is_end': False, 'next_cursor': 'next'}),
                           ok({'info_list': [{'media_id': 'file'}], 'is_end': True})])
        self.assertEqual(len(ima.ImaClient(CREDS, session).search('library', 'query')), 1)
        self.assertEqual(session.calls[1][1]['json']['cursor'], 'next')
        for data in [{'info_list': [], 'next_cursor': 'next'},
                     {'info_list': [], 'is_end': None},
                     {'info_list': [], 'is_end': False},
                     {'info_list': [None]}, {}]:
            with self.subTest(data=data), self.assertRaises(ima.ImaError):
                ima.ImaClient(CREDS, Session([ok(data)])).search('library', 'query')
        with self.assertRaises(ima.ImaError):
            ima.ImaClient(CREDS, Session([
                ok({'info_list': [], 'is_end': False, 'next_cursor': 'next'}),
                ok({'info_list': []})])).search('library', 'query')

    def test_auth_permission_rate_parameter_and_unknown_errors_are_distinct_and_redacted(self):
        for response, expected in [(Response({}, 401), 'AUTH_REQUIRED'), (Response({}, 403), 'FORBIDDEN'),
                                   (Response({}, 429), 'RATE_LIMITED'), (Response({}, 302), 'UPSTREAM_ERROR'),
                                   (Response({'code': 110030, 'msg': CREDS['apiKey']}), 'FORBIDDEN'),
                                   (Response({'code': 110021}), 'RATE_LIMITED'), (Response({'code': 110001}), 'INVALID_PARAMETER'),
                                   (Response({'code': 110012}), 'NOT_OPEN')]:
            with self.assertRaises(ima.ImaError) as error:
                ima.ImaClient(CREDS, Session([response])).libraries()
            self.assertEqual(error.exception.code, expected)
            self.assertNotIn(CREDS['apiKey'], str(error.exception))
        with self.assertRaises(ima.ImaError) as error:
            ima.ImaClient(CREDS, Session([requests.Timeout(CREDS['apiKey'])]))._request('create_folder', {}, mutation=True)
        self.assertEqual(error.exception.code, 'OUTCOME_UNKNOWN')

    def test_no_arbitrary_operation_or_write_on_read_channel(self):
        client = ima.ImaClient(CREDS, Session([]))
        for value in ['https://example.com', '../create_folder', 'delete_folder', 'create_folder']:
            with self.assertRaises(ima.ImaError):
                client._request(value, {})
        self.assertEqual(client.session.calls, [])
        for value in ['', 0, False, {}, []]:
            with self.assertRaises(ima.ImaError):
                client.browse('library', value)

    def test_tree_detects_cycles_instead_of_silently_truncating(self):
        client = ima.ImaClient(CREDS, Session([]))
        client.browse = lambda *args: [{'kind': 'folder', 'folderId': 'library', 'name': 'cycle'}]
        with self.assertRaises(ima.ImaError):
            client.tree('library')

    def test_media_without_url_or_restricted_url_never_leaks_signed_info(self):
        for data in [{'media_type': 11}, {'media_type': 1},
                     {'media_type': 1, 'url_info': {'url': 'http://127.0.0.1/private', 'headers': {'Authorization': CREDS['apiKey']}}}]:
            session = Session([ok(data)])
            result = ima.ImaClient(CREDS, session).media_access('file', probe=True)
            self.assertFalse(result['readVerified'])
            self.assertNotIn(CREDS['apiKey'], json.dumps(result))
            self.assertNotIn('url_info', result)
            self.assertEqual(len(session.calls), 1)

    def test_signed_media_fetch_is_bounded_and_sends_no_ima_key(self):
        session = Session([ok({'media_type': 13, 'url_info': {'url': 'https://test.cos.ap-shanghai.myqcloud.com/test?signature=SYNTHETIC',
                                                            'headers': {'Authorization': 'TEMP_ONLY'}}}), Response(content=b'synthetic', status=206)])
        with patch.object(ima.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('1.1.1.1', 443))]):
            result = ima.ImaClient(CREDS, session).media_access('file', probe=True)
        self.assertTrue(result['readVerified'])
        sent = session.calls[1][1]
        self.assertEqual(sent['headers']['Range'], 'bytes=0-4095')
        self.assertNotIn('ima-openapi-apikey', sent['headers'])
        self.assertNotIn('signature', json.dumps(result))

    def test_cos_uses_only_temporary_credentials_and_original_small_test_bytes(self):
        temporary = {'secret_id': 'TEMP_ID', 'secret_key': 'TEMP_KEY', 'token': 'TEMP_TOKEN',
                     'bucket_name': 'synthetic-12345', 'region': 'ap-shanghai', 'cos_key': 'synthetic/test.txt',
                     'start_time': int(time.time()) - 10, 'expired_time': int(time.time()) + 120}
        session = Session([Response(status=200)])
        probe.upload_test_bytes(temporary, session=session)
        url, sent = session.calls[0]
        self.assertEqual(sent['data'], probe.TEST_BYTES)
        self.assertTrue(url.endswith('/synthetic/test.txt'))
        self.assertNotIn('ima-openapi-apikey', sent['headers'])
        self.assertIn('q-signature=', sent['headers']['Authorization'])
        for change in [{'bucket_name': 'evil.example.com'}, {'cos_key': '../escape'}, {'expired_time': 0}]:
            with self.assertRaises(ima.ImaError):
                probe.upload_test_bytes({**temporary, **change}, session=Session([]))

    def test_observed_cos_decimal_string_seconds_are_valid_without_key_reset(self):
        credential = {'secret_id': 'TEMP_ID', 'secret_key': 'TEMP_KEY', 'token': 'TEMP_TOKEN',
                      'bucket_name': 'synthetic-12345', 'region': 'ap-shanghai', 'cos_key': 'test.txt',
                      'start_time': '1800000000', 'expired_time': '1800043200'}
        session = Session([Response(status=200)])
        with patch.object(probe.time, 'time', return_value=1800000010):
            result = probe.upload_test_bytes(credential, session=session)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(result['httpStatus'], 200)
        self.assertEqual(result['stage'], 'cos_upload')
        self.assertNotIn('TEMP_KEY', json.dumps(result))

    def test_cos_invalid_expired_future_and_http_rejection_have_scoped_diagnostics(self):
        credential = {'secret_id': 'TEMP_ID', 'secret_key': 'TEMP_KEY', 'token': 'TEMP_TOKEN',
                      'bucket_name': 'synthetic-12345', 'region': 'ap-shanghai', 'cos_key': 'test.txt',
                      'start_time': 1800000000, 'expired_time': 1800043200}
        for change, code in [({'start_time': True}, 'COS_CREDENTIAL_INVALID'),
                             ({'start_time': '1800000000000'}, 'COS_CREDENTIAL_INVALID'),
                             ({'start_time': ' 1800000000'}, 'COS_CREDENTIAL_INVALID'),
                             ({'expired_time': 1800000005}, 'COS_CREDENTIAL_EXPIRED'),
                             ({'start_time': 1800000020}, 'COS_CREDENTIAL_NOT_YET_VALID')]:
            session = Session([])
            with self.subTest(code=code), patch.object(probe.time, 'time', return_value=1800000010):
                with self.assertRaises(ima.ImaError) as error:
                    probe.upload_test_bytes({**credential, **change}, session=session)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(error.exception.diagnostic['stage'], 'cos_credential_validation')
                self.assertEqual(session.calls, [])
                self.assertNotIn('TEMP_KEY', json.dumps(error.exception.diagnostic))
        with patch.object(probe.time, 'time', return_value=1800000010):
            with self.assertRaises(ima.ImaError) as error:
                probe.upload_test_bytes(credential, session=Session([Response(status=403)]))
        self.assertEqual(error.exception.code, 'COS_UPLOAD_REJECTED')
        self.assertEqual(error.exception.diagnostic['httpStatus'], 403)

    def test_cos_waits_for_bounded_new_credential_start_and_rechecks(self):
        credential = {'secret_id': 'TEMP_ID', 'secret_key': 'TEMP_KEY', 'token': 'TEMP_TOKEN',
                      'bucket_name': 'synthetic-12345', 'region': 'ap-shanghai', 'cos_key': 'test.txt',
                      'start_time': '1800000001', 'expired_time': '1800043201'}
        with patch.object(ima.time, 'time', side_effect=[1800000000.93, 1800000001.01]), \
                patch.object(ima.time, 'sleep') as sleep:
            result = probe.upload_test_bytes(credential, session=Session([Response(status=200)]))
        self.assertAlmostEqual(sleep.call_args.args[0], 0.08, places=5)
        self.assertTrue(result['validity']['started'])
        with patch.object(ima.time, 'time', return_value=1800000000.93), \
                patch.object(ima.time, 'sleep'):
            session = Session([])
            with self.assertRaises(ima.ImaError) as error:
                probe.upload_test_bytes(credential, session=session)
        self.assertEqual(error.exception.code, 'COS_CREDENTIAL_NOT_YET_VALID')
        self.assertEqual(session.calls, [])


class ReadProbeTests(unittest.TestCase):
    def client(self):
        client = Mock()
        client.libraries.return_value = [{'libraryId': 'library', 'name': 'synthetic library'}]
        client.tree.return_value = [{'kind': 'file', 'mediaId': 'file', 'name': 'synthetic file', 'path': '/synthetic file'}]
        client.media_access.return_value = {'urlProvided': True, 'readVerified': True, 'requiresImaClient': False}
        return client

    def run_probe(self, directory, client, answers):
        service = Mock()
        service.status.return_value = {'configured': True}
        service.client.return_value = client
        with patch.object(probe, 'REPORT', Path(directory) / 'real-verification.json'), \
                patch.object(probe, 'ImaService', return_value=service), \
                patch.object(probe.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=answers), patch('builtins.print') as output:
            probe.main()
        service.configure.assert_not_called()
        return output

    def test_search_failure_preserves_success_and_allows_media_check(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            client.search.side_effect = ima.ImaError('INVALID_RESPONSE')
            self.run_probe(directory, client, ['', '1', 'PRIVATE_QUERY', 'y', '1', ''])
            text = (Path(directory) / 'read-verification.json').read_text(encoding='utf8')
            report = json.loads(text)
            self.assertTrue(report['treeComplete'])
            self.assertEqual(report['checks']['libraries']['status'], 'passed')
            self.assertEqual(report['checks']['search']['code'], 'INVALID_RESPONSE')
            self.assertEqual(report['checks']['mediaAccess']['status'], 'passed')
            self.assertNotIn('PRIVATE_QUERY', text)
            self.assertNotIn('synthetic file', text)
            self.assertFalse(report['cloudWrites'])

    def test_tree_failure_is_saved_without_claiming_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            client.tree.side_effect = ima.ImaError('INCOMPLETE_LIST')
            with self.assertRaises(ima.ImaError):
                self.run_probe(directory, client, ['', '1'])
            report = json.loads((Path(directory) / 'read-verification.json').read_text(encoding='utf8'))
            self.assertEqual(report['checks']['libraries']['status'], 'passed')
            self.assertEqual(report['checks']['tree']['status'], 'failed')
            self.assertFalse(report['treeComplete'])
            client.search.assert_not_called()

    def test_interrupt_retains_completed_tree_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            client.search.return_value = []
            with self.assertRaises(KeyboardInterrupt):
                self.run_probe(directory, client, ['', '1', 'query', KeyboardInterrupt()])
            report = json.loads((Path(directory) / 'read-verification.json').read_text(encoding='utf8'))
            self.assertTrue(report['treeComplete'])
            self.assertEqual(report['checks']['search']['resultCount'], 0)
            self.assertEqual(report['checks']['mediaAccess']['status'], 'not_run')

    def test_media_failure_preserves_search_and_never_starts_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            client.search.return_value = []
            client.media_access.side_effect = ima.ImaError('FORBIDDEN', 110030)
            self.run_probe(directory, client, ['', '1', 'query', 'y', '1', ''])
            report = json.loads((Path(directory) / 'read-verification.json').read_text(encoding='utf8'))
            self.assertEqual(report['checks']['search']['status'], 'passed')
            self.assertEqual(report['checks']['mediaAccess']['code'], 'FORBIDDEN')
            self.assertIsNone(report['mediaAccess'])
            client._request.assert_not_called()

    def test_media_choice_uses_search_matches_with_clear_check_only_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            client.tree.return_value.append({'kind': 'file', 'mediaId': 'matched-file',
                                             'name': 'matched', 'path': '/folder/matched'})
            client.search.return_value = [{'kind': 'file', 'mediaId': 'matched-file', 'name': 'matched'}]
            output = self.run_probe(directory, client, ['', '1', 'query', 'y', '1', ''])
            client.media_access.assert_called_once_with('matched-file', probe=True)
            text = str(output.call_args_list)
            self.assertIn('搜索命中的文件', text)
            self.assertIn('原文件访问检查通过', text)
            self.assertIn('阅读全文请使用ima客户端', text)

    def test_numbered_directory_test_requires_named_target_and_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            with patch.object(probe, 'exclusive_probe') as lock, patch.object(probe, 'OwnedProbe') as owned:
                owned.return_value.run.return_value = {'matrix': {}, 'remainingObjects': []}
                self.run_probe(directory, client, ['', '1', '', '', '1', '1', '确认'])
            lock.assert_called_once()
            owned.assert_called_once_with(client, 'library', 'library')

    def test_directory_test_cancel_and_legacy_commands(self):
        for answers, starts in [(['', '1', '', '', '1', '1', ''], False),
                                (['', '1', '', '', 'TEST', '1', 'YES'], True)]:
            with self.subTest(starts=starts), tempfile.TemporaryDirectory() as directory:
                client = self.client()
                with patch.object(probe, 'exclusive_probe') as lock, patch.object(probe, 'OwnedProbe') as owned:
                    owned.return_value.run.return_value = {'matrix': {}, 'remainingObjects': []}
                    self.run_probe(directory, client, answers)
                self.assertEqual(owned.called, starts)
                self.assertEqual(lock.called, starts)


class Cloud:
    """Synthetic cloud with actual mutable folder/file membership for readback tests."""
    def __init__(self, rename=True, move_code=0):
        self.rows = {'library': [{'kind': 'file', 'mediaId': 'USER_EXISTING', 'folderId': None, 'name': 'private original', 'parentFolderId': 'library'}]}
        self.calls = []
        self.rename_enabled, self.move_code = rename, move_code

    def browse(self, library, parent=None):
        return copy.deepcopy(self.rows.get(parent or library, []))

    def _request(self, operation, body, **kwargs):
        self.calls.append((operation, copy.deepcopy(body)))
        if operation == 'create_folder':
            identifier = 'folder-' + str(len(self.calls))
            parent = body['folder_id']
            self.rows.setdefault(parent, []).append({'kind': 'folder', 'folderId': identifier, 'mediaId': identifier,
                                                      'name': body['name'], 'parentFolderId': parent})
            self.rows[identifier] = []
            return {'media_id': identifier}
        if operation == 'create_media':
            return {'media_id': 'TEST_MEDIA', 'cos_credential': {'cos_key': 'synthetic.txt'}}
        if operation == 'add_knowledge':
            self.rows[body['folder_id']].append({'kind': 'file', 'mediaId': body['media_id'], 'folderId': None,
                                                 'name': body['title'], 'parentFolderId': body['folder_id']})
            return {'media_id': body['media_id']}
        if operation == 'rename_knowledge':
            if self.rename_enabled:
                for rows in self.rows.values():
                    for row in rows:
                        if row['folderId'] == body['media_id']:
                            row['name'] = body['name']
            return {}
        if operation == 'move_knowledge':
            media_id = body['infos'][0]['media_id']
            if self.move_code == 0:
                for rows in self.rows.values():
                    for row in list(rows):
                        if row['mediaId'] == media_id:
                            rows.remove(row)
                            row['parentFolderId'] = body['dst_folder_id']
                            self.rows[body['dst_folder_id']].append(row)
                            return {'move_results': {media_id: {'ret_code': 0}}}
            return {'move_results': {media_id: {'ret_code': self.move_code}}}
        raise AssertionError(operation)

    def media_access(self, *args, **kwargs):
        return {'urlProvided': False, 'readVerified': False, 'requiresImaClient': True}

    def search(self, library, query):
        return [row for rows in self.rows.values() for row in rows if query in row['name']]


class ProbeTests(unittest.TestCase):
    def make(self, directory, cloud):
        return probe.OwnedProbe(cloud, 'library', 'library', path=Path(directory) / 'owned.dpapi', report=Path(directory) / 'report.json')

    def test_owned_objects_roundtrip_and_no_original_or_delete_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            original = copy.deepcopy(cloud.rows['library'][0])
            audit = self.make(directory, cloud)
            with patch.object(probe, 'upload_test_bytes'):
                result = audit.run()
            self.assertEqual(result['matrix']['create_folder']['status'], 'passed')
            self.assertEqual(result['matrix']['rename_folder']['status'], 'passed')
            self.assertEqual(result['matrix']['move_file_in_and_back']['status'], 'passed')
            self.assertEqual(result['matrix']['delete_empty_folder']['status'], 'not_tested')
            self.assertEqual(cloud.rows['library'][0], original)
            self.assertEqual(len(result['remainingObjects']), 3)
            self.assertTrue(all(row['present'] for row in result['remainingObjects']))
            self.assertEqual(len(cloud.rows[audit.data['objects']['child']['id']]), 0)
            self.assertEqual(audit.data['steps']['rename-folder']['childObjectIds'], ['TEST_MEDIA'])
            before = len(cloud.calls)
            self.make(directory, cloud).run()
            self.assertEqual(len(cloud.calls), before)

    def test_code_zero_no_rename_side_effect_fails_actual_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud(rename=False)
            audit = self.make(directory, cloud)
            with patch.object(probe, 'upload_test_bytes'):
                result = audit.run()
            self.assertEqual(result['matrix']['rename_folder']['status'], 'not_passed')
            self.assertEqual(result['matrix']['move_file_in_and_back']['status'], 'passed')

    def test_each_move_ret_code_checked_and_unknown_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud(move_code=220001)
            audit = self.make(directory, cloud)
            with patch.object(probe, 'upload_test_bytes'):
                result = audit.run()
            self.assertEqual(result['steps']['move-in']['status'], 'item_failed')
            self.assertNotEqual(result['matrix']['move_file_in_and_back']['status'], 'passed')
            count = len(cloud.calls)
            self.make(directory, cloud).run()
            self.assertEqual(len(cloud.calls), count)

    def test_legacy_failure_resumes_with_exact_journaled_allocation_and_same_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            audit = self.make(directory, cloud)
            root = audit.folder('root', 'library')
            child = audit.folder('child', root)
            audit.record('upload-test-file', 'AUTH_REQUIRED', mediaId='OLD_MEDIA',
                         parentId=root, name=audit.data['prefix'] + '-synthetic-test.txt')
            audit.record('recovery-allocate-media', 'allocated', mediaId='TEST_MEDIA')
            with patch.object(probe, 'upload_test_bytes') as upload:
                result = self.make(directory, cloud).run(allocation={
                    'media_id': 'TEST_MEDIA', 'cos_credential': {'cos_key': 'synthetic.txt'}})
            upload.assert_called_once()
            self.assertEqual(result['matrix']['upload_and_ingest']['status'], 'passed')
            self.assertTrue(any(row['status'] == 'AUTH_REQUIRED' for row in result['uploadHistory']))
            self.assertTrue(any(row['id'] == 'OLD_MEDIA' for row in result['remainingObjects']))
            self.assertEqual(sum(op == 'create_folder' for op, _ in cloud.calls), 2)
            self.assertEqual(sum(op == 'create_media' for op, _ in cloud.calls), 0)
            self.assertEqual(sum(op == 'add_knowledge' for op, _ in cloud.calls), 1)
            self.assertEqual(audit.data['objects']['child']['id'], child)

    def test_pre_upload_validation_failure_reallocates_but_keeps_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            with patch.object(probe, 'upload_test_bytes', side_effect=ima.ImaError(
                    'COS_CREDENTIAL_EXPIRED', diagnostic={'stage': 'cos_credential_validation', 'uploadRequestSent': False})):
                failed = self.make(directory, cloud).run()
            self.assertEqual(failed['lastError']['code'], 'COS_CREDENTIAL_EXPIRED')
            self.assertEqual(failed['matrix']['rename_folder']['status'], 'not_run')
            with patch.object(probe, 'upload_test_bytes'):
                result = self.make(directory, cloud).run()
            self.assertEqual(result['matrix']['upload_and_ingest']['status'], 'passed')
            self.assertEqual(sum(op == 'create_folder' for op, _ in cloud.calls), 2)
            self.assertEqual(sum(op == 'add_knowledge' for op, _ in cloud.calls), 1)

    def test_confirmed_upload_before_add_resumes_without_reupload_or_reallocation(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            audit = self.make(directory, cloud)
            record = audit.record
            def interrupt_after_upload(step, status, **details):
                record(step, status, **details)
                if step == 'upload-test-file' and status == 'uploaded':
                    raise KeyboardInterrupt()
            with patch.object(probe, 'upload_test_bytes'), patch.object(audit, 'record', side_effect=interrupt_after_upload):
                with self.assertRaises(KeyboardInterrupt):
                    audit.run()
            with patch.object(probe, 'upload_test_bytes') as upload:
                result = self.make(directory, cloud).run()
            upload.assert_not_called()
            self.assertEqual(result['matrix']['upload_and_ingest']['status'], 'passed')
            self.assertEqual(sum(op == 'create_media' for op, _ in cloud.calls), 1)
            self.assertEqual(sum(op == 'add_knowledge' for op, _ in cloud.calls), 1)

    def test_uncertain_cos_request_does_not_reupload_or_allocate(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            def uncertain(credential, *, on_send):
                diagnostic = {'stage': 'cos_upload', 'uploadRequestSent': True}
                on_send(diagnostic)
                raise ima.ImaError('COS_UPLOAD_UNKNOWN', diagnostic=diagnostic)
            with patch.object(probe, 'upload_test_bytes', side_effect=uncertain):
                self.make(directory, cloud).run()
            before = len(cloud.calls)
            with patch.object(probe, 'upload_test_bytes') as upload:
                result = self.make(directory, cloud).run()
            upload.assert_not_called()
            self.assertEqual(len(cloud.calls), before)
            self.assertEqual(result['matrix']['upload_and_ingest']['status'], 'not_passed')

    def test_uncertain_reallocation_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            with patch.object(probe, 'upload_test_bytes', side_effect=ima.ImaError('COS_CREDENTIAL_EXPIRED')):
                self.make(directory, cloud).run()
            operation = cloud._request
            def uncertain(operation_name, body, **kwargs):
                result = operation(operation_name, body, **kwargs)
                if operation_name == 'create_media':
                    raise ima.ImaError('OUTCOME_UNKNOWN')
                return result
            with patch.object(cloud, '_request', side_effect=uncertain):
                self.make(directory, cloud).run()
            before = len(cloud.calls)
            self.make(directory, cloud).run()
            self.assertEqual(len(cloud.calls), before)

    def test_uncertain_add_is_reconciled_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            operation = cloud._request
            def uncertain(operation_name, body, **kwargs):
                result = operation(operation_name, body, **kwargs)
                if operation_name == 'add_knowledge':
                    raise ima.ImaError('OUTCOME_UNKNOWN')
                return result
            with patch.object(probe, 'upload_test_bytes'), patch.object(cloud, '_request', side_effect=uncertain):
                self.make(directory, cloud).run()
            with patch.object(probe, 'upload_test_bytes') as upload:
                result = self.make(directory, cloud).run()
            upload.assert_not_called()
            self.assertEqual(result['matrix']['upload_and_ingest']['status'], 'passed')
            self.assertEqual(sum(op == 'create_media' for op, _ in cloud.calls), 1)
            self.assertEqual(sum(op == 'add_knowledge' for op, _ in cloud.calls), 1)

    def test_uncertain_create_is_reconciled_without_duplicate_and_preserves_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            cloud = Cloud()
            audit = self.make(directory, cloud)
            operation = cloud._request
            def uncertain(*args, **kwargs):
                operation(*args, **kwargs)
                raise ima.ImaError('OUTCOME_UNKNOWN')
            cloud._request = uncertain
            with self.assertRaises(ima.ImaError):
                audit.folder('root', 'library')
            self.assertEqual(len(cloud.calls), 1)
            audit = self.make(directory, cloud)
            self.assertEqual(audit.folder('root', 'library'), 'folder-1')
            self.assertEqual(len(cloud.calls), 1)

    def test_refuse_original_objects_and_changing_target_of_existing_run(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = self.make(directory, Cloud())
            with self.assertRaises(ima.ImaError):
                audit.move('USER_EXISTING', 'move-original')
            with self.assertRaises(ima.ImaError):
                audit.folder('child', 'USER_EXISTING')
            with self.assertRaises(ima.ImaError):
                probe.OwnedProbe(audit.client, 'other', 'library', path=audit.path, report=audit.report)
            self.assertEqual(audit.client.calls, [])

    def test_two_process_style_probe_locks_cannot_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owned.dpapi'
            with probe.exclusive_probe(path):
                with self.assertRaises(ima.ImaError):
                    with probe.exclusive_probe(path):
                        pass


class HttpTests(unittest.TestCase):
    def test_actual_bridge_handler_uses_same_local_auth_and_fixed_ima_routes(self):
        import bridge
        with tempfile.TemporaryDirectory() as directory:
            service = ima.ImaService(Path(directory) / 'empty.dpapi')
            with patch.object(bridge, 'IMA', service), patch.object(bridge, 'DESKTOP_TOKEN', 'SYNTHETIC_TOKEN'):
                server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for origin, token, expected in [('http://127.0.0.1:8180', 'SYNTHETIC_TOKEN', 200),
                                                    ('https://example.com', 'SYNTHETIC_TOKEN', 403),
                                                    ('http://127.0.0.1:8180', 'wrong', 403)]:
                        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                        client.request('GET', '/api/ima/v1/status', headers={'Host': '127.0.0.1:8182', 'Origin': origin, 'X-Desktop-Token': token})
                        response = client.getresponse()
                        self.assertEqual(response.status, expected)
                        data = json.loads(response.read())
                        self.assertNotIn('apiKey', json.dumps(data))
                        client.close()
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                    client.request('OPTIONS', '/api/ima/v1/browse', headers={'Host': '127.0.0.1:8182', 'Origin': 'http://127.0.0.1:8180'})
                    response = client.getresponse()
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.getheader('Access-Control-Allow-Origin'), 'http://127.0.0.1:8180')
                    response.read()
                    client.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_fixed_read_routes_auth_and_no_secret_response(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ima.ImaService(Path(directory) / 'empty.dpapi')
            class Handler(BaseHTTPRequestHandler):
                def desktop_request(self):
                    return self.client_address[0] == '127.0.0.1' and self.headers.get('Origin') == 'http://127.0.0.1:8180'

                def json_response(self, status, payload):
                    raw = json.dumps(payload).encode()
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self):
                    ima.handle_request(self, service, 'SYNTHETIC_LOCAL_TOKEN')

                do_POST = do_GET

                def log_message(self, *_):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method, path, body=None, origin='http://127.0.0.1:8180', token='SYNTHETIC_LOCAL_TOKEN'):
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    client.request(method, path, body=body, headers={'Origin': origin, 'X-Desktop-Token': token})
                    response = client.getresponse()
                    status, payload = response.status, json.loads(response.read())
                    client.close()
                    return status, payload
                self.assertEqual(request('GET', '/api/ima/v1/status')[1]['data']['configured'], False)
                self.assertEqual(request('GET', '/api/ima/v1/status', origin='https://evil.example')[0], 403)
                self.assertEqual(request('GET', '/api/ima/v1/status', token='bad')[0], 403)
                self.assertEqual(request('POST', '/api/ima/v1/libraries', body='{}')[0], 409)
                self.assertEqual(request('POST', '/api/ima/v1/libraries', body='{"url":"https://example.com"}')[0], 400)
                self.assertEqual(request('POST', '/api/ima/v1/delete', body='{}')[0], 405)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
