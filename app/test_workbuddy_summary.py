import http.client
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import bridge
from test_wechat_bridge import WeChatFixture
from wechat_bridge import WeChatService
from workbuddy_analysis import WorkBuddyAnalysis, AnalysisError
from workbuddy_summary import WorkBuddySummary, normalize_markdown


class SummaryClient:
    def __init__(self, directory, terminal='completed'):
        self.directory=Path(directory)
        self.terminal=terminal
        self.arguments=None
        self.closed=False

    def initialize(self): pass

    def call(self, name, arguments, timeout=20):
        if name=='workbuddy_start':
            self.arguments=arguments
            (self.directory/'summary-body.md').write_text('## 一句话\n测试新闻发布了**新功能**。\n\n- 信息一\n- 信息二\n\n注意：仅依据正文。',encoding='utf-8')
            return {'job_id':'summary-job','status':'queued'}
        if name=='workbuddy_status':
            return {'job_id':'summary-job','status':self.terminal,'result_truncated':False,
                    'result':{'content':[{'type':'text','text':'done\n— model: auto → test-model · approval: workspace-write · effort: low'}]}}
        raise AssertionError(name)

    def close(self): self.closed=True


class SummaryTests(WeChatFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.populate()
        self.managers=[]
        self.clients=[]
        self.config=Path(self.temp.name)/'workbuddy-mcp.json'
        server=Path(self.temp.name)/'server.js';server.touch()
        node=Path(self.temp.name)/'node.exe';node.touch()
        self.config.write_text(json.dumps({'server':str(server),'node':str(node)}))
        self.output=Path(self.temp.name)/'summaries'

    def tearDown(self):
        for manager in self.managers: manager.executor.shutdown(wait=True)
        super().tearDown()

    def manager(self, terminal='completed'):
        def factory(n,s,d):
            client=SummaryClient(d,terminal);self.clients.append(client);return client
        manager=WorkBuddySummary(self.service,self.config,client_factory=factory,output_root=self.output)
        self.managers.append(manager)
        return manager

    def wait(self,manager,article,expected):
        for _ in range(300):
            status=manager.status(article)
            if status['status']==expected:return status
            time.sleep(.01)
        self.fail('Summary did not finish')

    def test_summary_is_independent_deduplicated_markdown_and_survives_restart(self):
        manager=self.manager();article=self.service.snapshot()['articles'][0]['id']
        with self.service.connect() as db:
            db.execute('INSERT INTO analysis_runs(run_id,article_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',('a'*32,article,'completed',1,1))
        _,created=manager.start(article);self.assertTrue(created)
        status=self.wait(manager,article,'completed')
        self.assertEqual(status['progressPercent'],100)
        self.assertIn('/summary/',status['contentUrl'])
        again,created=manager.start(article)
        self.assertFalse(created);self.assertEqual(again['runId'],status['runId'])
        content=manager.document(article,status['runId']).decode()
        self.assertIn('**新功能**',content)
        self.assertFalse((self.output/article/status['runId']/'analysis.html').exists())
        args=self.clients[0].arguments
        self.assertEqual(args['effort'],'low');self.assertEqual(args['approval_mode'],'workspace-write')
        self.assertIn('150～350字',args['task'])
        self.assertEqual(self.service.snapshot()['articles'][0]['analysisStatus'],'completed')
        self.assertEqual(self.service.snapshot()['articles'][0]['summaryStatus'],'completed')
        second=self.manager()
        self.assertEqual(second.document(article,status['runId']),manager.document(article,status['runId']))

    def test_failed_retry_does_not_collide_or_destroy_analysis(self):
        manager=self.manager('failed');article=self.service.snapshot()['articles'][0]['id']
        manager.start(article);first=self.wait(manager,article,'failed')
        with self.assertRaises(AnalysisError):manager.document(article,first['runId'])
        manager.client_factory=lambda n,s,d:SummaryClient(d)
        manager.start(article);second=self.wait(manager,article,'completed')
        self.assertNotEqual(first['runId'],second['runId'])
        self.assertTrue(manager.document(article,second['runId']))

    def test_old_database_migration_retains_articles_and_analysis(self):
        before=self.service.snapshot()['articles']
        with self.service.connect() as db:
            db.execute('DROP TABLE summary_runs')
            db.execute('INSERT INTO analysis_runs(run_id,article_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',('c'*32,before[0]['id'],'completed',1,1))
        migrated=WeChatService(self.config_path,self.service.data_dir)
        self.assertEqual(len(migrated.snapshot()['articles']),len(before))
        with migrated.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM summary_runs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM analysis_runs').fetchone()[0],1)

    def test_summary_requires_body_and_rejects_uncollected_ids(self):
        manager=self.manager();article=self.service.snapshot()['articles'][0]['id']
        with self.service.connect() as db:db.execute("UPDATE articles SET body='' WHERE id=?",(article,))
        with self.assertRaisesRegex(AnalysisError,'尚未收录正文'):manager.start(article)
        with self.assertRaises(AnalysisError):manager.document('../outside','a'*32)
        with self.assertRaises(AnalysisError):manager.document(article,'b'*32)

    def test_markdown_normalization_and_limits(self):
        self.assertEqual(normalize_markdown('```markdown\n## 标题\n正文\n```'),'## 标题\n正文\n')
        for value in ('','\x00','x'*8001):
            with self.assertRaises(AnalysisError):normalize_markdown(value)
        # HTML remains inert Markdown data; the client renderer never executes it.
        self.assertIn('<script>',normalize_markdown('<script>bad()</script>'))

    def test_shared_queue_limit_counts_analysis_and_summary(self):
        manager=self.manager();article=self.service.snapshot()['articles'][0]['id']
        with self.service.connect() as db:
            for i in range(4):
                db.execute('INSERT INTO analysis_runs(run_id,article_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                           (str(i)*32,article,'running',1,1))
        with self.assertRaisesRegex(AnalysisError,'队列已满'):manager.start(article)
        with self.service.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM summary_runs').fetchone()[0],0)

    def test_http_desktop_boundary_and_content_only_by_collected_id(self):
        manager=self.manager();article=self.service.snapshot()['articles'][0]['id']
        server=bridge.ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,token=bridge.DESKTOP_TOKEN,origin='http://127.0.0.1:8180'):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,headers={'Host':'127.0.0.1:8182','Origin':origin,'X-Desktop-Token':token})
            resp=conn.getresponse();result=(resp.status,resp.read());conn.close();return result
        try:
            with patch('bridge.WECHAT',self.service),patch('bridge.WECHAT_SUMMARY',manager):
                path='/api/wechat/summary/'+article
                self.assertEqual(request('POST',path,token='wrong')[0],403)
                self.assertEqual(request('POST',path,origin='https://evil.test')[0],403)
                self.assertEqual(request('GET',path+'?url=https://evil.test')[0],400)
                self.assertEqual(request('GET','/api/wechat/summary/'+'f'*64)[0],404)
                self.assertEqual(request('POST',path)[0],202)
                status=self.wait(manager,article,'completed')
                code,raw=request('GET',status['contentUrl']);self.assertEqual(code,200)
                self.assertIn('新功能',json.loads(raw)['markdown'])
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
