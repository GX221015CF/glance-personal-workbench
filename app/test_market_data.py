"""Quote validation and stale-data handling; no external calls or desktop actions."""
import datetime as dt
import unittest
from unittest.mock import patch

import bridge
from market_fallback import normalize_tencent


class MarketDataTest(unittest.TestCase):
    def test_price_scaling_and_previous_close(self):
        # Decimal prices are already decoded by trends2; do not divide again.
        spec = bridge.FOCUS_MARKETS[2]
        source = {'code': spec['code'], 'preClose': 0.8, 'trends': ['2026-11-04 14:30,0.82,0.824', '2026-11-04 14:31,0.83,0.832']}
        current = dt.datetime(2026, 11, 4, 14, 32, tzinfo=bridge.TZ)
        row = bridge.normalize_focus(spec, source, False, 'checked', current)
        self.assertEqual(row['priceLabel'], '0.832')
        self.assertAlmostEqual(row['changePercent'], 4)
        self.assertEqual(row['status'], '盘中行情')
        self.assertEqual(row['pointCount'], 2)
        self.assertFalse(row['stale'])
        delayed = bridge.normalize_focus(spec, source, False, 'checked', current + dt.timedelta(minutes=10))
        self.assertTrue(delayed['stale'])
        self.assertEqual(delayed['status'], '行情滞后 11分钟')

    def test_us_close_is_not_live_in_china_afternoon(self):
        spec = bridge.FOCUS_MARKETS[1]
        source = {'code': 'SOXX', 'preClose': 450, 'trends': ['2026-11-04 04:59,460,464', '2026-11-04 05:00,465,465']}
        row = bridge.normalize_focus(spec, source, False, 'checked', dt.datetime(2026, 11, 4, 14, 31, tzinfo=bridge.TZ))
        self.assertEqual(row['status'], '非交易时段')
        self.assertFalse(row['stale'])
        self.assertFalse(row['warning'])
        # NY 09:30 is 21:30 in Shanghai in summer and 22:30 in winter.
        self.assertTrue(bridge.market_session('US', dt.datetime(2026, 7, 6, 21, 31, tzinfo=bridge.TZ)))
        self.assertTrue(bridge.market_session('US', dt.datetime(2026, 11, 4, 22, 31, tzinfo=bridge.TZ)))
        self.assertFalse(bridge.market_session('US', dt.datetime(2026, 11, 4, 21, 31, tzinfo=bridge.TZ)))

    def test_bad_or_wrong_quotes_are_rejected(self):
        spec = bridge.FOCUS_MARKETS[0]
        for source in ({}, {'code': 'other', 'preClose': 100, 'trends': ['2026-11-04 14:30,0,101']},
                       {'code': '000905', 'preClose': 0, 'trends': ['2026-11-04 14:30,0,101']},
                       {'code': '000905', 'preClose': 100, 'trends': ['2099-01-01 14:30,0,101']},
                       {'code': '000905', 'preClose': 100, 'trends': ['2026-11-04 14:30,0,nan']}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                bridge.normalize_focus(spec, source, False, 'checked')

    def test_one_failed_focus_does_not_hide_other_cards(self):
        spec = bridge.FOCUS_MARKETS[0]
        saved = {'id': spec['id'], 'available': True, 'price': 100, 'sourceTime': '2026-11-04 14:30'}
        with patch.dict(bridge.STATE, {'focus': {'items': [saved]}}), patch('bridge.fetch', side_effect=OSError), \
             patch('bridge.fetch_tencent', side_effect=OSError), patch('bridge.publish') as publish:
            bridge.refresh_focus()
            payload = publish.call_args.args[1]
            self.assertEqual(len(payload['items']), 4)
            self.assertEqual(payload['items'][0]['sourceTime'], saved['sourceTime'])
            self.assertTrue(payload['items'][0]['stale'])
            self.assertFalse(payload['items'][1]['available'])

    def test_missing_world_quote_keeps_its_own_old_timestamp(self):
        old = {'id': '100.SPX', 'available': True, 'price': 5000, 'timeLabel': '11-03 05:00'}
        response = {'data': {'diff': [{'f13': 100, 'f12': 'N225', 'f2': 40000, 'f4': -100, 'f3': -0.25, 'f124': 1793773800}]}}
        with patch.dict(bridge.STATE, {'world': {'groups': [{'items': [old]}]}}), patch('bridge.fetch', return_value=(response, False, 'checked')), patch('bridge.publish') as publish:
            bridge.refresh_world()
            rows = {row['id']: row for group in publish.call_args.args[1]['groups'] for row in group['items']}
            self.assertEqual(len(rows), 11)
            self.assertEqual(rows['100.SPX']['timeLabel'], old['timeLabel'])
            self.assertTrue(rows['100.SPX']['stale'])
            self.assertEqual(rows['100.N225']['direction'], 'down')
            self.assertFalse(rows['1.000001']['available'])

    def test_tencent_minute_validation_and_current_bucket(self):
        spec = bridge.FOCUS_MARKETS[2]
        quote = [''] * 31
        quote[2:5] = ['159770', '0.918', '0.938']
        quote[30] = '20261112130909'
        entry = {'qt': {'sz159770': quote}, 'data': {'date': '20261112',
                 'data': ['1308 0.917 100', '1310 0.918 200']}}
        source = normalize_tencent(spec, {'code': 0, 'data': {'sz159770': entry}})
        self.assertEqual(source['trends'][-1], '2026-11-12 13:09,0,0.918')
        row = bridge.normalize_focus(spec, source, False, 'checked', dt.datetime(2026, 11, 12, 13, 9, tzinfo=bridge.TZ))
        self.assertEqual(row['status'], '盘中行情')
        for bad in ('nan', '-1', 'inf'):
            entry['data']['data'] = [f'1308 {bad} 100']
            with self.assertRaises(ValueError):
                normalize_tencent(spec, {'code': 0, 'data': {'sz159770': entry}})
        entry['data']['data'] = ['1315 0.918 100']
        with self.assertRaises(ValueError):
            normalize_tencent(spec, {'code': 0, 'data': {'sz159770': entry}})
        quote[2] = 'wrong'
        with self.assertRaises(ValueError):
            normalize_tencent(spec, {'code': 0, 'data': {'sz159770': entry}})

    def test_us_tencent_time_is_converted_from_new_york(self):
        quote = [''] * 31
        quote[2:5] = ['SOXX.OQ', '155.4', '146.59']
        quote[30] = '2026-11-11 16:00:01'
        source = normalize_tencent(bridge.FOCUS_MARKETS[1], {'code': 0, 'data': {'usSOXX': {
            'qt': {'usSOXX': quote}, 'data': {'date': '', 'data': ['1600 155.4 100']}}}})
        self.assertEqual(source['trends'][-1], '2026-11-12 05:00,0,155.4')

    def test_failed_primary_recovers_through_fresh_fallback(self):
        current = dt.datetime(2026, 11, 12, 13, 10, tzinfo=bridge.TZ)
        def fallback(spec):
            return {'code': spec['code'], 'preClose': 1,
                    'trends': ['2026-11-12 13:09,0,1.1', '2026-11-12 13:10,0,1.2']}, 'new-check'
        with patch.dict(bridge.STATE, {}, clear=True), patch('bridge.fetch', side_effect=OSError), \
             patch('bridge.fetch_tencent', side_effect=fallback), patch('bridge.publish') as publish:
            bridge.refresh_focus(current)
            items = publish.call_args.args[1]['items']
        self.assertTrue(all(row['available'] and not row['stale'] for row in items))
        self.assertTrue(all(row['provider'] == '腾讯财经' for row in items))
        self.assertEqual(items[2]['sourceTime'], '2026-11-12 13:10')

    def test_older_fallback_cannot_replace_newer_cache_or_change_its_timestamp(self):
        spec = bridge.FOCUS_MARKETS[2]
        current = dt.datetime(2026, 11, 12, 13, 10, tzinfo=bridge.TZ)
        old = {'id': spec['id'], 'available': True, 'sourceTime': '2026-11-12 13:08',
               'price': .92, 'checkedAt': 'last-success', 'provider': '腾讯财经'}
        stale_source = {'code': spec['code'], 'preClose': 1, 'trends': ['2026-11-12 11:22,0,.9']}
        with patch.dict(bridge.STATE, {'focus': {'items': [old]}}, clear=True), \
             patch('bridge.fetch', return_value=({'data':stale_source}, True, 'old-check')), \
             patch('bridge.fetch_tencent', return_value=(stale_source, 'new-check')), patch('bridge.publish') as publish:
            bridge.refresh_focus(current)
            robot = publish.call_args.args[1]['items'][2]
        self.assertEqual(robot['sourceTime'], old['sourceTime'])
        self.assertEqual(robot['price'], old['price'])
        self.assertEqual(robot['checkedAt'], old['checkedAt'])
        self.assertEqual(robot['status'], '更新失败 · 缓存')


if __name__ == '__main__':
    unittest.main()
