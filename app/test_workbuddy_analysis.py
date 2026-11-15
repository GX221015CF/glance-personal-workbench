import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import bridge
from test_wechat_bridge import WeChatFixture
from wechat_bridge import WeChatService
from wechat_bridge import analysis_progress
from workbuddy_analysis import MAX_ARTICLE_CHARS, AnalysisError, WorkBuddyAnalysis, article_text, sanitize_fragment
from analysis_report import report_filename, SCRIPT_HASH


def analysis_fixture():
    table = '<table><tr><th>属性</th><th>判断</th></tr><tr><td>条件</td><td>成立</td></tr><tr><td>成本</td><td>可控</td></tr></table>'
    paragraphs = '<p>这是隔离测试中的具体事实。</p>' * 45
    return ('<p class="report-title">测试判断</p><p class="core-conclusion">测试结论</p>'
            '<h2>证据决定判断强度</h2>' + table + '<div class="mechanism"><p>具体事实</p></div>' + paragraphs +
            '<h2>先筛选才能控制成本</h2>' + table + '<ol class="step-flow"><li>先判断条件</li><li>再检查依据</li></ol>'
            '<div class="comparison"><div class="compare-item">方案甲</div><div class="compare-item">方案乙</div></div>' + paragraphs +
            '<h2>边界决定如何迁移</h2>' + table + '<div class="card-grid"><div class="card">独立概念</div></div>'
            '<div class="boundary"><p>判断的前提需保持一致。</p></div>' + paragraphs + '<p class="closing">测试收束。</p>'
            '<div class="source-notes"><p>模型分析与无法核实 / 需要外部核验的内容在页脚说明。</p></div>'
            '<div class="self-check"><ol>' + '<li>测试自检回答。</li>' * 5 + '</ol></div>')


class FakeClient:
    def __init__(self, cwd, terminal='completed'):
        self.cwd = Path(cwd)
        self.terminal = terminal
        self.closed = False
        self.calls = []
        self.arguments = None

    def initialize(self):
        return None

    def call(self, name, arguments, timeout=20):
        self.calls.append(name)
        if name == 'workbuddy_start':
            self.arguments = arguments
            self.cwd.joinpath('analysis-body.html').write_text(
                analysis_fixture() + '<script>bad()</script>',
                encoding='utf-8')
            return {'job_id': 'fake-workbuddy-job', 'status': 'queued'}
        if name == 'workbuddy_status':
            return {'job_id': 'fake-workbuddy-job', 'status': self.terminal, 'result_truncated': False,
                    'result': {'content': [{'type': 'text', 'text': 'done\n— model: auto → test-model · approval: workspace-write · effort: medium'}]}}
        raise AssertionError(name)

    def close(self):
        self.closed = True


class WorkBuddyAnalysisTests(WeChatFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.populate()
        fixture_body = json.dumps({'nodes':[{'type':'p','children':[{'type':'text','text':'测试依据。' * 500}]}], 'images':[]},ensure_ascii=False)
        with self.service.connect() as db:
            db.execute('UPDATE articles SET body=?', (fixture_body,))
        self.sources_path = self.config_path
        self.temp_outputs = Path(self.temp.name) / 'analysis-output'
        self.workbuddy_config = Path(self.temp.name) / 'workbuddy-mcp.json'
        self.mcp_server = Path(self.temp.name) / 'server.js'
        self.node = Path(self.temp.name) / 'node.exe'
        self.mcp_server.touch()
        self.node.touch()
        self.workbuddy_config.write_text(json.dumps({'server':str(self.mcp_server), 'node':str(self.node)}), encoding='utf-8')
        self.fake_clients = []
        self.managers = []

    def tearDown(self):
        # Terminal status is visible before the worker finishes its audit/close.
        # Join workers before removing the Windows SQLite fixture directory.
        for manager in self.managers:
            manager.executor.shutdown(wait=True)
        super().tearDown()

    def manager(self, terminal='completed'):
        def factory(node, server, cwd):
            client = FakeClient(cwd, terminal)
            self.fake_clients.append(client)
            return client
        manager = WorkBuddyAnalysis(self.service, self.workbuddy_config, client_factory=factory, output_root=self.temp_outputs)
        self.managers.append(manager)
        return manager

    def wait_for(self, manager, article_id, expected):
        for _ in range(200):
            value = manager.status(article_id)
            if value['status'] == expected:
                return value
            time.sleep(0.01)
        self.fail('analysis job did not reach ' + expected)

    def test_article_text_and_html_fragment_are_safe(self):
        text = article_text([{'type': 'p', 'children': [{'type': 'text', 'text': '原文'}, {'type': 'image', 'index': 0}]}])
        self.assertIn('原文', text)
        self.assertIn('没有交给模型做视觉分析', text)
        safe = sanitize_fragment('<h2 onclick="x">分析</h2><script>secret()</script><p>可读 <strong>重点</strong></p><img src="https://evil.test/x">')
        self.assertIn('<h2>分析</h2>', safe)
        self.assertIn('<strong>重点</strong>', safe)
        for unsafe in ('onclick', 'secret', 'evil.test', '<img'):
            self.assertNotIn(unsafe, safe)
        with self.assertRaises(AnalysisError):
            sanitize_fragment('<script>only unsafe</script>')

    def test_stage_progress_is_persisted_consistent_and_does_not_use_elapsed_time(self):
        stages = ['等待 WorkBuddy 处理', '正在读取正文与个人偏好', '正在连接 WorkBuddy',
                  '正在提交 WorkBuddy 任务', 'WorkBuddy 正在排队', 'WorkBuddy 正在分析正文',
                  'WorkBuddy 已返回，正在检查结果', '正在安全清理并排版', '正在保存解读报告']
        self.assertEqual([analysis_progress('running', s) for s in stages], [10,15,20,25,30,45,80,90,95])
        manager = self.manager()
        article_id = self.service.snapshot()['articles'][0]['id']
        run_id = 'b' * 32
        with self.service.connect() as db:
            db.execute('INSERT INTO analysis_runs(run_id,article_id,status,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                       (run_id,article_id,'running','WorkBuddy 正在分析正文',1,1))
        self.assertEqual(manager.status(article_id)['progressPercent'],45)
        self.assertEqual(self.service.snapshot()['articles'][0]['analysisPercent'],45)
        manager._update(run_id,'failed',error='连接失败')
        self.assertEqual(manager.status(article_id)['progressPercent'],45)
        self.assertEqual(manager.status(article_id)['status'],'failed')
        manager._update(run_id,'completed','解读已完成')
        self.assertEqual(manager.status(article_id)['progressPercent'],100)

    def test_task_prompt_uses_reader_facing_truncation_notice(self):
        article = {'source':'公众号', 'title':'标题', 'publishedAt':'2026-11-06 12:00', 'summary':'摘要'}
        complete = WorkBuddyAnalysis._task(article, '正文', 'analysis-body.html')
        self.assertNotIn('"body_truncated":', complete)
        self.assertIn('完整正文无需特意声明“未截断”', complete)

        clipped = WorkBuddyAnalysis._task(article, '正' * (MAX_ARTICLE_CHARS + 1), 'analysis-body.html')
        self.assertNotIn('"body_truncated":', clipped)
        self.assertIn(f'提供正文已因长度限制截取为前 {MAX_ARTICLE_CHARS} 个字符', clipped)
        self.assertIn('用自然中文说明“提供的正文已截取”', clipped)

    def test_old_database_gets_analysis_table_without_losing_articles(self):
        before = self.service.snapshot()['articles']
        with self.service.connect() as db:
            db.execute('DROP TABLE analysis_runs')
        migrated = WeChatService(self.sources_path, self.service.data_dir)
        with migrated.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(analysis_runs)')}
        self.assertIn('workbuddy_job_id', columns)
        self.assertEqual(len(migrated.snapshot()['articles']), len(before))

    def test_real_workflow_wrapper_deduplicates_and_persists_safe_html(self):
        manager = self.manager()
        article_id = self.service.snapshot()['articles'][0]['id']
        first, created = manager.start(article_id)
        self.assertTrue(created)
        result = self.wait_for(manager, article_id, 'completed')
        again, created_again = manager.start(article_id)
        self.assertFalse(created_again)
        self.assertEqual(again['runId'], result['runId'])
        document = manager.document(article_id, result['runId']).decode('utf-8')
        self.assertIn('来源与判断边界', document)
        self.assertIn('具体事实', document)
        self.assertIn('模型分析', document)
        self.assertIn('class="layout"', document)
        self.assertIn('<nav aria-label="文章目录">', document)
        self.assertIn('--measure:64em', document)
        self.assertIn('CHAPTER 00', document)
        self.assertIn('@media(max-width:560px)', document)
        self.assertIn('无法核实 / 需要外部核验', document)
        self.assertIn('公众号原文', document) if '公众号原文' in document else None
        for unsafe in ('onclick', 'bad()', 'evil.test', '<img'):
            self.assertNotIn(unsafe, document)
        self.assertTrue(self.fake_clients[0].closed)
        run_dir = self.temp_outputs / article_id / result['runId']
        title = self.service.article(article_id)['title']
        self.assertEqual({item.name for item in run_dir.iterdir()}, {'analysis-body.html', 'analysis.html', report_filename(title)})
        self.assertEqual((run_dir / report_filename(title)).read_bytes(), (run_dir / 'analysis.html').read_bytes())

    def test_failed_job_can_be_retried_without_path_collision(self):
        manager = self.manager('failed')
        article_id = self.service.snapshot()['articles'][0]['id']
        manager.start(article_id)
        first = self.wait_for(manager, article_id, 'failed')
        self.assertIn('未能完整完成', first['error'])
        manager.client_factory = lambda node, server, cwd: self.fake_clients.append(FakeClient(cwd)) or self.fake_clients[-1]
        second, created = manager.start(article_id)
        self.assertTrue(created)
        second = self.wait_for(manager, article_id, 'completed')
        self.assertNotEqual(first['runId'], second['runId'])

    def test_explicit_regeneration_retains_previous_report_and_deduplicates_active_job(self):
        manager = self.manager()
        article_id = self.service.snapshot()['articles'][0]['id']
        first, _ = manager.start(article_id)
        duplicate, created = manager.start(article_id, regenerate=True)
        self.assertFalse(created)
        self.assertEqual(duplicate['runId'], first['runId'])
        original = self.wait_for(manager, article_id, 'completed')
        original_html = manager.document(article_id, original['runId'])
        second, created = manager.start(article_id, regenerate=True)
        self.assertTrue(created)
        self.assertNotEqual(second['runId'], original['runId'])
        self.wait_for(manager, article_id, 'completed')
        self.assertEqual(manager.document(article_id, original['runId']), original_html)

    def test_preferences_are_fresh_bounded_and_audited_without_reading_by_agent(self):
        prefroot = Path(self.temp.name) / 'profiles'
        prefroot.mkdir()
        profile = prefroot / 'USER.md'
        original = '# 用户\n- **Notes:**\n  - 结论先行，用生活化类比讲机制。\n  - API Key: SECRET-do-not-export\n## Context\n不传给模型的私人资料\n'
        profile.write_text(original, encoding='utf-8')
        cfg = self.workbuddy_config.with_name('workbuddy-preferences.json')
        cfg.write_text(json.dumps({'enabled': True, 'sources': [{'file': 'USER.md', 'sections': ['Notes']}]}), encoding='utf-8')
        manager = self.manager()
        manager.preference_root = prefroot
        article_id = self.service.snapshot()['articles'][0]['id']
        manager.start(article_id)
        result = self.wait_for(manager, article_id, 'completed')
        # Wait for finally's audit write after the terminal state is visible.
        audit = manager.audit_root / article_id / result['runId']
        for _ in range(100):
            if (audit / 'result.json').exists(): break
            time.sleep(.01)
        request = json.loads((audit / 'request.json').read_text(encoding='utf-8'))
        actual = json.loads((audit / 'result.json').read_text(encoding='utf-8'))
        self.assertIn('生活化类比', request['arguments']['task'])
        self.assertNotIn('SECRET', json.dumps(request))
        self.assertNotIn('私人资料', json.dumps(request, ensure_ascii=False))
        self.assertEqual(request['arguments'], self.fake_clients[0].arguments)
        self.assertEqual(actual['reportedModel'], 'test-model')
        self.assertEqual(actual['localStatus'], 'completed')
        self.assertEqual(profile.read_text(encoding='utf-8'), original)
        document = manager.document(article_id, result['runId']).decode()
        self.assertIn('本次解读的模型与个人偏好', document)
        self.assertIn('test-model', document)
        self.assertIn('USER.md', document)
        # Completed reports and audit remain available after manager restart.
        second = self.manager()
        self.assertEqual(second.document(article_id, result['runId']), manager.document(article_id, result['runId']))

    def test_constructing_manager_does_not_fail_live_jobs_recovery_is_explicit(self):
        manager = self.manager()
        article_id = self.service.snapshot()['articles'][0]['id']
        with self.service.connect() as db:
            db.execute('INSERT INTO analysis_runs(run_id,article_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                       ('a' * 32, article_id, 'running', time.time(), time.time()))
        observer = self.manager()
        self.assertEqual(observer.status(article_id)['status'], 'running')
        observer.recover_interrupted()
        self.assertEqual(manager.status(article_id)['status'], 'failed')

    def test_unexpected_model_files_and_mcp_errors_fail_without_publishing(self):
        manager = self.manager()
        article_id = self.service.snapshot()['articles'][0]['id']
        class ErrorClient(FakeClient):
            def call(self, name, arguments, timeout=20):
                value = super().call(name, arguments, timeout)
                if name == 'workbuddy_status': value['result']['isError'] = True
                return value
        manager.client_factory = lambda n, s, d: ErrorClient(d)
        manager.start(article_id)
        result = self.wait_for(manager, article_id, 'failed')
        with self.assertRaises(AnalysisError): manager.document(article_id, result['runId'])

    def test_model_options_reject_parameter_injection(self):
        manager = self.manager()
        config = json.loads(self.workbuddy_config.read_text())
        config.update(model='auto --tools Bash')
        self.workbuddy_config.write_text(json.dumps(config), encoding='utf-8')
        with self.assertRaises(AnalysisError): manager._options()

    def test_article_without_body_is_not_submitted(self):
        article_id = self.service.snapshot()['articles'][0]['id']
        with self.service.connect() as db:
            db.execute("UPDATE articles SET body='' WHERE id=?", (article_id,))
        manager = self.manager()
        with self.assertRaisesRegex(AnalysisError, '尚未收录正文'):
            manager.start(article_id)

    def test_http_start_requires_desktop_token_and_document_is_local_safe_html(self):
        manager = self.manager()
        server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, *, origin='http://127.0.0.1:8180', token=bridge.DESKTOP_TOKEN):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Host': '127.0.0.1:8182', 'Origin': origin, 'X-Desktop-Token': token}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result

        try:
            with patch('bridge.WECHAT', self.service), patch('bridge.WECHAT_ANALYSIS', manager):
                article_id = self.service.snapshot()['articles'][0]['id']
                self.assertEqual(request('POST', f'/api/wechat/analysis/{article_id}', token='wrong')[0], 403)
                self.assertEqual(request('POST', f'/api/wechat/analysis/{article_id}/regenerate', token='wrong')[0], 403)
                self.assertEqual(request('POST', f'/api/wechat/analysis/{article_id}', origin='https://evil.test')[0], 403)
                self.assertEqual(request('GET', f'/api/wechat/analysis/{article_id}?url=https://evil.test')[0], 400)
                status, _, raw = request('POST', f'/api/wechat/analysis/{article_id}')
                self.assertEqual(status, 202)
                run = json.loads(raw)['analysis']['runId']
                result = self.wait_for(manager, article_id, 'completed')
                status, headers, body = request('GET', f'/api/wechat/analysis/{article_id}/{result["runId"]}/document')
                self.assertEqual(status, 200)
                self.assertIn("script-src 'sha256-" + SCRIPT_HASH + "'", headers['Content-Security-Policy'])
                self.assertEqual(run, result['runId'])
                self.assertIn(b'WorkBuddy', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
