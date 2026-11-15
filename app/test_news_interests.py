import copy
import datetime as dt
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import bridge
from news_interests import Interests, merge_news, timeline_timestamp


class InterestsTest(unittest.TestCase):
    def setUp(self):
        self.rules = Interests.load()

    def test_user_keywords_and_variations(self):
        for title in ('open ai更新', 'OPEN-AI announces a release', 'gPt-6', 'ChatGPT上线', 'GPT6更新',
                      'Astra', 'TIBO', 'claude code', 'Anthropic', 'OPUS', 'Fable', 'Haiku', 'Sonnet',
                      'Gemini', '谷歌', 'Antigravity', 'Google', 'DeepSeek Harness', 'deep-seek harness',
                      'DSH', '大肥鱼', '梁文峰', '梁文锋', 'OpenRouter', 'OPEN ROUTER',
                      '腾讯', '混元', '千问', 'QWEN', 'grok', '马斯克', '卡兹克',
                      'Codex', 'DeepMind', 'NotebookLM', 'Gemma', 'Hunyuan', '通义千问', 'x.ai',
                      'Qwen3.8更新', 'Gemini4发布', 'Grok4.7上线', 'Claude5更新'):
            with self.subTest(title=title):
                self.assertTrue(self.rules.matches({'title': title}))

    def test_false_matches_and_unrelated_fields(self):
        for title in ('AMD earnings report', 'AstraZeneca drug research', 'Grokking algorithms',
                      'dshboard tool', 'GPTQ quantization', 'copus music', 'haikuman', 'mysonnetter'):
            with self.subTest(title=title):
                self.assertFalse(self.rules.matches({'title': title}))
        self.assertFalse(self.rules.matches({'title': 'Unrelated', 'reason': '比OpenAI更重要', 'links': {'original': 'https://qwen.example'}}))
        self.assertTrue(self.rules.matches({'title': '新模型上线', 'source': {'name': '腾讯混元官方博客'}}))
        self.assertTrue(self.rules.matches({'title': '中文标题', 'originalTitle': 'Claude release'}))

    def test_daily_filters_without_reordering_or_mutating(self):
        report = {'lead': {'title': 'AMD earnings', 'leadParagraph': 'Hardware figures'}, 'sections': [
            {'label': '模型', 'items': [{'title': 'Mistral new model'}, {'title': 'GPT发布'}, {'title': 'Claude更新'}]},
            {'label': '其他', 'items': [{'title': 'Revolut unrelated payment news'}]}],
            'flashes': [{'title': 'Grok上线'}, {'title': 'Random business news'}]}
        original = copy.deepcopy(report)
        filtered = self.rules.filter_report(report)
        self.assertEqual(report, original)
        self.assertEqual(filtered['lead'], {})
        self.assertEqual([s['label'] for s in filtered['sections']], ['模型'])
        self.assertEqual([i['title'] for i in filtered['sections'][0]['items']], ['GPT发布', 'Claude更新'])
        self.assertEqual(len(filtered['flashes']), 1)
        self.assertTrue(filtered['flashes'][0]['_matchedGroups'])

    def test_timeline_backfill_and_deduplication(self):
        records = [
            {'id': 'backfill', 'title': 'OpenAI old story', 'publishedAt': '2026-10-01T00:00:00Z', 'discoveredAt': '2026-11-04T00:00:00Z'},
            {'id': 'new', 'title': 'Claude launch', 'publishedAt': '2026-11-03T00:00:00Z', 'discoveredAt': '2026-11-04T01:00:00Z', 'selected': False},
            {'id': 'new', 'title': 'Claude launch', 'publishedAt': '2026-11-03T00:00:00Z', 'discoveredAt': '2026-11-04T01:00:00Z', 'selected': True},
            {'id': 'nohit', 'title': 'AMD earnings', 'discoveredAt': '2026-11-04T02:00:00Z'}]
        rows = merge_news(records, self.rules)
        self.assertEqual([row['id'] for row in rows], ['new', 'backfill'])
        self.assertTrue(rows[0]['selected'])
        expected = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(timeline_timestamp(records[0]), expected)

    def test_exact_server_query_and_same_scope_fallback(self):
        selected = parse_qs(urlparse(self.rules.query_url('Open AI')).query)
        public = parse_qs(urlparse(self.rules.query_url('Open AI', 'all')).query)
        self.assertEqual(selected['q'], ['Open AI'])
        self.assertEqual(selected['window'], ['24h'])
        self.assertEqual({**selected, 'mode': ['all']}, public)

    def test_selected_worker_queries_then_filters_and_labels_public_hits(self):
        rules = Interests({'groups': [{'label': 'AI', 'keywords': ['OpenAI', 'Claude'], 'queries': ['OpenAI', 'Claude']}], 'maxItems': 12})
        def fetch(url, **kwargs):
            query = parse_qs(urlparse(url).query)
            if query['q'] == ['OpenAI']:
                items = [{'id': 'one', 'title': 'OpenAI update', 'selected': True, 'discoveredAt': '2026-11-04T00:00:00Z'}, {'id': 'noise', 'title': 'Unrelated'}]
            elif query['mode'] == ['selected']:
                items = []
            else:
                items = [{'id': 'two', 'title': 'Claude update', 'selected': False, 'discoveredAt': '2026-11-04T01:00:00Z'}]
            return {'items': items}, False, '2026-11-04T15:00:00+08:00'
        with patch('bridge.Interests.load', return_value=rules), patch('bridge.fetch', side_effect=fetch) as source, patch('bridge.time.sleep'), patch('bridge.publish') as publish, patch('bridge.AIHOT_RETRY_UNTIL', 0):
            bridge.refresh_selected()
            self.assertEqual(source.call_count, 3)
            payload = publish.call_args.args[1]
            self.assertFalse(payload['partial'])
            self.assertEqual([row['title'] for row in payload['items']], ['Claude update', 'OpenAI update'])
            self.assertFalse(payload['items'][0]['selected'])
            self.assertEqual(payload['items'][0]['matchedLabel'], 'AI')

    def test_first_keyword_result_is_published_before_second_query(self):
        rules = Interests({'groups': [{'label': 'AI', 'keywords': ['OpenAI', 'Claude'], 'queries': ['OpenAI', 'Claude']}], 'maxItems': 12})
        publications = []
        def fetch(url, **kwargs):
            query = parse_qs(urlparse(url).query)['q'][0]
            if query == 'Claude':
                self.assertTrue(any(view['completedQueries'] == 1 and view['items'] for view in publications))
            return {'items': [{'id': query, 'title': query + ' update', 'discoveredAt': '2026-11-04T00:00:00Z'}]}, False, bridge.now_iso()
        with patch('bridge.Interests.load', return_value=rules), patch('bridge.fetch', side_effect=fetch), patch('bridge.time.sleep'), patch('bridge.publish', side_effect=lambda name, view: publications.append(copy.deepcopy(view))), patch('bridge.AIHOT_RETRY_UNTIL', 0):
            bridge.refresh_selected()
        self.assertTrue(publications[0]['running'])
        self.assertEqual(publications[0]['completedQueries'], 0)
        self.assertFalse(publications[-1]['running'])

    def test_previous_filtered_cards_stay_visible_during_next_round(self):
        previous = {'filterVersion': self.rules.version, 'items': [
            {'title': 'Claude cached release', 'url': 'https://aihot.news/items/retained', 'timeline': bridge.time.time(), 'matchedLabel': 'Claude'},
            {'title': 'OpenAI expired release', 'url': 'expired', 'timeline': bridge.time.time() - 90000},
            {'title': 'OpenAI论文', 'url': 'paper', 'category': 'paper', 'timeline': bridge.time.time()}]}
        publications = []
        with patch.dict(bridge.STATE, {'selected': previous}), patch('bridge.fetch', return_value=({'items': []}, False, bridge.now_iso())), patch('bridge.time.sleep'), patch('bridge.publish', side_effect=lambda name, view: publications.append(copy.deepcopy(view))), patch('bridge.AIHOT_RETRY_UNTIL', 0):
            bridge.refresh_selected()
        self.assertEqual([row['title'] for row in publications[0]['items']], ['Claude cached release'])
        self.assertTrue(publications[0]['stale'])
        self.assertEqual(publications[-1]['items'], [])

    def test_card_key_is_stable_when_summary_or_progress_changes(self):
        row = {'title': 'Claude release', 'url': 'https://aihot.news/items/stable'}
        self.assertEqual(bridge.news_key(row), bridge.news_key({**row, 'summary': 'New summary', 'reason': 'Reason'}))
        self.assertNotEqual(bridge.news_key(row), bridge.news_key({**row, 'url': row['url'] + '-other'}))

    def test_cooldown_stops_further_searches(self):
        with patch('bridge.AIHOT_RETRY_UNTIL', bridge.time.time() + 300), patch('bridge.fetch') as fetch, patch('bridge.publish') as publish:
            bridge.refresh_selected()
            fetch.assert_not_called()
            payload = publish.call_args.args[1]
            self.assertTrue(payload['partial'])
            self.assertEqual(payload['failedQueries'], len(self.rules.queries))

    def test_feed_cap_and_multiple_topic_labels(self):
        items = [{'id': str(n), 'title': 'OpenAI与Claude', 'discoveredAt': f'2026-11-04T00:{n:02d}:00Z'} for n in range(20)]
        rows = merge_news(items, self.rules)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]['id'], '19')
        self.assertEqual(len(rows[0]['_matchedGroups']), 2)

    def test_papers_excluded_before_keyword_matching(self):
        for row in ({'title': 'OpenAI report', 'category': 'paper'},
                    {'title': 'Claude benchmark', 'category': {'slug': 'paper'}},
                    {'title': 'Google研究：新方法'},
                    {'title': 'DeepSeek论文发布'},
                    {'title': 'GPT evaluation', 'links': {'original': 'https://arxiv.org/abs/1234'}}):
            self.assertFalse(self.rules.matches(row))
        self.assertTrue(self.rules.matches({'title': 'Gemini产品更新', 'summary': '支持阅读论文', 'category': 'ai-products'}))
        report = {'sections': [{'label': '论文研究', 'items': [{'title': 'OpenAI report'}]},
                               {'label': '产品发布/更新', 'items': [{'title': 'Claude Code更新'}]}]}
        self.assertEqual([section['label'] for section in self.rules.filter_report(report)['sections']], ['产品发布/更新'])

    def test_combined_feed_deduplicates_daily_against_live_updates(self):
        daily_row = {'title': 'Claude更新', 'url': 'https://aihot.news/items/same', 'matchedLabel': 'Claude', 'timeline': 0}
        recent_row = {**daily_row, 'selected': True, 'timeline': 1234, 'timeLabel': '原文 11-04 12:00', 'reason': '官方精选理由'}
        daily = {'available': True, 'filterVersion': self.rules.version, 'date': '2026-11-04', 'sections': [{'items': [daily_row]}]}
        selected = {'available': True, 'filterVersion': self.rules.version, 'items': [recent_row, {'title': 'OpenAI论文', 'url': 'paper', 'category': 'paper'}]}
        with patch.dict(bridge.STATE, {'daily': daily, 'selected': selected}):
            merged = bridge.combined_news()
        self.assertEqual(len(merged['items']), 1)
        self.assertTrue(merged['items'][0]['inDaily'])
        self.assertTrue(merged['items'][0]['selected'])
        self.assertEqual(merged['items'][0]['reason'], '官方精选理由')
        self.assertEqual(merged['items'][0]['timeLabel'], '原文 11-04 12:00')
        self.assertNotIn('inDaily', daily_row)

    def test_daily_only_article_uses_daily_date_without_invented_publication_time(self):
        daily = {'available': True, 'filterVersion': self.rules.version, 'date': '2026-11-04', 'sections': [{'items': [
            {'title': 'DeepSeek Harness更新', 'url': 'https://aihot.news/items/day', 'matchedLabel': 'DeepSeek', 'timeline': 0}]}]}
        with patch.dict(bridge.STATE, {'daily': daily, 'selected': {'filterVersion': 'old-version', 'items': [{'title': 'Old news'}]}}):
            merged = bridge.combined_news()
        self.assertEqual(len(merged['items']), 1)
        self.assertEqual(merged['items'][0]['timeLabel'], '日报 2026-11-04')

    def test_recent_cached_query_does_not_repeat_request(self):
        cached = {'data': {'items': []}, 'checkedAt': bridge.now_iso()}
        with patch('bridge.read_json', return_value=cached), patch('bridge.urllib.request.urlopen') as network, patch('bridge.AIHOT_RETRY_UNTIL', 0):
            data, stale, checked = bridge.fetch(self.rules.query_url('Claude'), aihot=True)
            network.assert_not_called()
            self.assertFalse(stale)
            self.assertEqual(data, cached['data'])

    def test_unfinished_cached_search_resumes_without_full_interval_delay(self):
        cached = {'filterVersion': self.rules.version, 'partial': True, 'checkedAt': bridge.now_iso()}
        with patch('bridge.read_json', return_value=cached), patch('bridge.time.sleep') as sleep, patch.dict(bridge.STATE, {}):
            def stop():
                raise KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                bridge.worker('selected', 900, stop)
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
