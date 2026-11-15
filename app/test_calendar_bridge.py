import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

import bridge
from calendar_bridge import CalendarService, CalendarError, event_fields, validate_official
from gmail_bridge import CredentialStore


def event(**changes):
    return {'id': str(uuid.uuid4()), 'revision': 0, 'title': '纪念日', 'startDate': '2026-11-14',
            'endDate': '2026-11-14', 'type': 'anniversary', 'description': '个人说明', 'annual': True, **changes}


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CredentialStore(Path(self.temp.name))
        self.service = CalendarService(store=self.store)

    def test_personal_events_survive_service_recreation_and_remain_encrypted(self):
        saved = self.service.save(event())
        restored = CalendarService(store=self.store).snapshot()
        self.assertIn(saved, restored['events'])
        self.assertNotIn('个人说明'.encode(), (Path(self.temp.name) / 'personal-events.dpapi').read_bytes())
        self.assertEqual(saved['source'], 'personal')

    def test_invalid_date_ranges_titles_and_recurrence_are_rejected(self):
        for changes in [{'title': '  '}, {'endDate': '2026-11-13'}, {'startDate': '2026-02-30'},
                        {'startDate': '26-11-14'}, {'title': 'a' * 101}, {'type': 'unknown'},
                        {'annual': True, 'type': 'competition'}, {'annual': True, 'endDate': '2026-11-15'}]:
            with self.subTest(changes=changes), self.assertRaises(CalendarError):
                self.service.save(event(**changes))

    def test_leap_day_is_accepted_as_single_day_anniversary(self):
        saved = self.service.save(event(startDate='2028-02-29', endDate='2028-02-29'))
        self.assertTrue(saved['annual'])

    def test_multi_day_event_crosses_month_and_year(self):
        saved = self.service.save(event(startDate='2026-12-30', endDate='2027-01-02', annual=False, type='competition'))
        self.assertEqual(saved['endDate'], '2027-01-02')

    def test_revision_prevents_lost_updates_and_duplicate_create_is_idempotent(self):
        payload = event()
        first = self.service.save(payload)
        self.assertEqual(self.service.save(payload), first)
        update = {key: first[key] for key in payload}
        update['title'] = '已修改'
        second = self.service.save(update)
        self.assertEqual(second['revision'], 2)
        with self.assertRaises(CalendarError) as error:
            self.service.save(update)
        self.assertEqual(error.exception.status, 409)

    def test_delete_is_soft_and_does_not_remove_official_events(self):
        saved = self.service.save(event())
        self.service.delete({'id': saved['id'], 'revision': saved['revision']})
        self.service.delete({'id': saved['id'], 'revision': saved['revision']})
        current = self.service.snapshot()['events']
        self.assertFalse(any(item['id'] == saved['id'] for item in current))
        self.assertTrue(self.store.read('personal-events', {})['events'][0]['deletedAt'])
        self.assertEqual(len(current), 44)
        with self.assertRaises(CalendarError):
            self.service.delete({'id': current[0]['id'], 'revision': 1})

    def test_failed_write_preserves_previous_personal_events(self):
        saved = self.service.save(event())
        with patch.object(self.store, 'write', side_effect=OSError):
            with self.assertRaises(CalendarError) as error:
                self.service.save(event(title='新事件'))
        self.assertEqual(error.exception.code, 'STORAGE_ERROR')
        self.assertEqual([e for e in self.service.snapshot()['events'] if e['source'] == 'personal'], [saved])

    def test_invalid_official_reload_keeps_previous_data_and_personal_events(self):
        original = self.service.snapshot()
        saved = self.service.save(event())
        bad = Path(self.temp.name) / 'broken.json'
        bad.write_text('{}')
        self.service.official_path = bad
        result = self.service.snapshot()
        self.assertTrue(result['meta']['officialError'])
        self.assertEqual(len([e for e in result['events'] if e['source'] == 'official']), len(original['events']))
        self.assertIn(saved, result['events'])

    def test_corrupt_personal_store_is_not_overwritten(self):
        (Path(self.temp.name) / 'personal-events.dpapi').write_bytes(b'corrupt')
        self.assertFalse(self.service.snapshot()['meta']['personalAvailable'])
        with self.assertRaises(CalendarError):
            self.service.save(event())
        self.assertEqual((Path(self.temp.name) / 'personal-events.dpapi').read_bytes(), b'corrupt')

    def test_failed_verification_retains_both_sources_of_events(self):
        saved = self.service.save(event())
        before = copy.deepcopy(self.service.official)
        with patch('calendar_bridge.requests.Session', side_effect=OSError):
            self.service.verify()
        result = self.service.snapshot()
        self.assertEqual(result['meta']['verification']['state'], 'failed')
        self.assertEqual(self.service.official, before)
        self.assertIn(saved, result['events'])

    def test_official_import_rejects_duplicate_ids_and_untrusted_source(self):
        data = copy.deepcopy(self.service.official)
        data['events'].append(data['events'][0])
        with self.assertRaises(CalendarError):
            validate_official(data)
        data = copy.deepcopy(self.service.official)
        data['sources'][0]['url'] = 'https://example.com/calendar'
        with self.assertRaises(CalendarError):
            validate_official(data)


class CalendarHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        service = CalendarService(store=CredentialStore(Path(self.temp.name)))
        self.service_patch = patch('bridge.CALENDAR', service)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    def request(self, method, path='', body=None, token=bridge.DESKTOP_TOKEN, origin=bridge.DESKTOP_ORIGINS[0]):
        headers = {'Host': '127.0.0.1:8182', 'Origin': origin, 'X-Desktop-Token': token, 'Content-Type': 'application/json'}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, '/api/calendar/v1' + path, body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_authorization_and_method_boundaries(self):
        self.assertEqual(self.request('GET', token='wrong')[0], 403)
        self.assertEqual(self.request('GET', origin='https://example.com')[0], 403)
        self.assertEqual(self.request('GET', '/events')[0], 405)
        self.assertEqual(self.request('GET', '?x=1')[0], 400)
        self.assertEqual(self.request('POST', '/unknown')[0], 404)

    def test_create_read_update_and_delete_through_http(self):
        status, payload = self.request('POST', '/events', event())
        self.assertEqual(status, 200)
        saved = payload['data']
        status, payload = self.request('GET')
        self.assertIn(saved, payload['data']['events'])
        update = {key: saved[key] for key in event()}
        update['title'] = '更新后的事件'
        status, payload = self.request('POST', '/events', update)
        self.assertEqual(payload['data']['revision'], 2)
        status, _ = self.request('POST', '/delete', {'id': saved['id'], 'revision': 2})
        self.assertEqual(status, 200)
        _, payload = self.request('GET')
        self.assertFalse(any(e['id'] == saved['id'] for e in payload['data']['events']))


if __name__ == '__main__':
    unittest.main()
