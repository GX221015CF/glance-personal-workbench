import base64
import hashlib
import unittest

from bs4 import BeautifulSoup
from analysis_report import (render_document, simulation_html, report_filename, SCRIPT_HASH, REPORT_SCRIPT,
                             REPORT_CSP, report_metrics, validate_report, ReportValidationError)
from workbuddy_analysis import sanitize_fragment, WorkBuddyAnalysis, AnalysisError

ARTICLE = {'title': '测试主题', 'source': '测试公众号', 'publishedAt': '2026-11-09T12:00:00+08:00',
           'url': 'https://mp.weixin.qq.com/s/example', 'summary': '摘要'}
FRAGMENT = '''<p class="report-title">先看结论<br>再看机制</p><p class="report-subtitle">一句导读</p>
<p class="core-conclusion">核心判断来自原文。</p><div class="evidence-grid">
<div class="evidence-item"><strong>未披露</strong><p>原文没有实验样本量</p></div></div>
<h2>机制决定迁移边界</h2><p class="chapter-intro">他们先关注表象，机制却在下层。</p>
<div class="mechanism"><p>关键机制。</p></div><div class="transfer"><strong>可迁移</strong><p>适用条件。</p></div>
<div class="boundary"><strong>边界</strong><p>前提。</p></div><blockquote>真实引语</blockquote>
<div class="risk">风险</div><div class="extension">延伸</div><p class="closing">证据决定结论强度。</p>'''


class ReportTests(unittest.TestCase):
    def render(self, fragment=FRAGMENT):
        return render_document(ARTICLE, sanitize_fragment(fragment), '2026-11-09 12:00:00').decode('utf-8')

    def test_opening_order_semantic_components_and_offline_shell(self):
        document = self.render()
        soup = BeautifulSoup(document, 'html.parser')
        order = [document.index(value) for value in ('<header class="hero">', '<div class="core-conclusion">',
                                                      '<nav aria-label="文章目录">', '<div class="evidence-grid">')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(soup.h1.get_text(' ', strip=True), '先看结论 再看机制')
        self.assertIn('正文约', soup.select_one('footer .meta').get_text())
        self.assertIn(ARTICLE['source'], soup.select_one('.hero .meta').get_text())
        self.assertIn('CHAPTER 00', soup.get_text())
        self.assertEqual(soup.select_one('.closing').get_text(), '证据决定结论强度。')
        for name in ('mechanism', 'transfer', 'boundary', 'risk', 'extension'):
            self.assertIsNotNone(soup.select_one('.' + name))
        self.assertFalse(soup.select('link, img, iframe, script[src]'))
        self.assertEqual(len(soup.select('script')), 1)
        self.assertEqual(soup.script.string, REPORT_SCRIPT)
        self.assertIn('@media print', document)
        for value in ('#0a0e13', '#121a23', '#1f2c3a', '#e8eef5', '#93a3b5', '#2dd4bf',
                      '#60a5fa', '#f5b942', '#f87171', '15.5px/1.82', '--measure:64em'):
            self.assertIn(value, document)

    def test_mechanism_chapters_use_a_compact_directory_without_narrowing_body_measure(self):
        for count in (3, 5, 7):
            soup = BeautifulSoup(self.render(''.join(f'<h2>判断{i}</h2><p>解释。</p>' for i in range(count))), 'html.parser')
            self.assertNotIn('has-sidebar', soup.select_one('.layout')['class'])
            self.assertEqual(len(soup.select('nav a')), count)

    def test_sanitizer_keeps_only_known_components_and_declarative_simulator_values(self):
        cleaned = sanitize_fragment('<div class="mechanism evil" style="color:red" onclick="x">机制</div>'
                                    '<script>alert(1)</script><input type="range" oninput="x">'
                                    '<div class="simulator" data-value="4" data-code="evil" data-unit="&quot;&lt;x&gt;">说明</div>')
        self.assertIn('class="mechanism"', cleaned)
        self.assertIn('data-value="4"', cleaned)
        for value in ('onclick', 'style=', 'alert(', '<input', 'data-code', ' evil'):
            self.assertNotIn(value, cleaned)

    def test_safe_simulator_initial_values_and_invalid_numbers(self):
        attributes = {'data-min': '0', 'data-max': '100', 'data-step': '1', 'data-value': '20',
                      'data-factor': '3', 'data-offset': '2', 'data-unit': '分钟'}
        html = simulation_html(attributes, '<p>原文系数3，简化模型。</p>', 0)
        soup = BeautifulSoup(html, 'html.parser')
        self.assertEqual(soup.output.get_text(), '62分钟')
        self.assertEqual(soup.input['type'], 'range')
        self.assertEqual(soup.progress['value'], '20.0')
        for key, value in (('data-factor', 'nan'), ('data-max', '0'), ('data-step', '-1'),
                           ('data-value', '999'), ('data-offset', '1e300'), ('data-factor', '<script>')):
            with self.subTest(key=key, value=value):
                fallback = simulation_html({**attributes, key:value}, '<p>说明保留。</p>', 0)
                self.assertNotIn('<input', fallback)
                self.assertIn('说明保留', fallback)
        fragment = '<div class="simulator" ' + ' '.join(f'{key}="{value}"' for key,value in attributes.items()) + '><p>假设。</p></div>'
        self.assertIn('type="range"', self.render(fragment))

    def test_csp_only_allows_fixed_script_and_filename_is_safe(self):
        actual = base64.b64encode(hashlib.sha256(REPORT_SCRIPT.encode()).digest()).decode()
        self.assertEqual(SCRIPT_HASH, actual)
        self.assertIn("script-src 'sha256-" + actual + "'", REPORT_CSP)
        self.assertNotIn("script-src 'unsafe-inline'", REPORT_CSP)
        self.assertNotIn('unsafe-eval', REPORT_CSP)
        self.assertEqual(report_filename('../主题:测试'), '主题_测试_分析.html')
        self.assertNotIn('http', report_filename('主题'))

    def test_prompt_preserves_requested_depth_provenance_and_write_boundary(self):
        task = WorkBuddyAnalysis._task(ARTICLE, '给定正文', 'analysis-body.html')
        for value in ('0.8–1.2', '60–66', '只保留', '零外部依赖', '3–7章', '可迁移',
                      '移到页脚', '不补编事实', '恰好创建', '用户表达偏好', '解读完成'):
            self.assertIn(value, task)

    def test_delivery_rejects_missing_required_layout(self):
        with self.assertRaisesRegex(AnalysisError, '缺少标题'):
            WorkBuddyAnalysis._render_output(None, ARTICLE, '<h2>标题</h2><p>正文。</p>', '2026-11-09', {}, {})

    def test_body_is_never_automatically_folded(self):
        original = '<h2>一个判断</h2><p class="chapter-intro">章节导语。</p><p>' + '原文说明。' * 200 + '</p>'
        soup = BeautifulSoup(self.render(original), 'html.parser')
        self.assertIsNone(soup.select_one('main details'))
        self.assertIn('原文说明。' * 200, soup.main.get_text())
        self.assertIsNone(soup.select_one('.chapter-takeaway'))
        cleaned = sanitize_fragment('<details class="deep-dive" open onclick="evil()"><summary>展开</summary><p>更多解释</p></details>')
        self.assertIn('<details class="deep-dive">', cleaned)
        self.assertNotIn(' open', cleaned)
        self.assertNotIn('onclick', cleaned)

    def test_information_design_gate_checks_real_counts_and_footer_only_provenance(self):
        from test_workbuddy_analysis import analysis_fixture
        fragment = sanitize_fragment(analysis_fixture())
        metrics = validate_report(fragment, 2000)
        self.assertEqual(metrics['tableCount'], 3)
        self.assertGreaterEqual(metrics['componentKinds'], 6)
        self.assertFalse(metrics['bodyProvenanceLabels'])
        with self.assertRaisesRegex(ReportValidationError, '0.8–1.2'):
            validate_report(fragment, 5000)
        with self.assertRaisesRegex(ReportValidationError, '单段≤150'):
            validate_report(fragment.replace('<h2>', '<p>' + '中' * 151 + '</p><h2>', 1), 2000)
        with self.assertRaisesRegex(ReportValidationError, '不能折叠'):
            validate_report(fragment.replace('<h2>', '<details><summary>隐藏正文</summary>正文</details><h2>', 1), 2000)
        with self.assertRaisesRegex(ReportValidationError, '溯源标签'):
            validate_report(fragment.replace('<h2>', '<p>无法核实</p><h2>', 1), 2000)
        # A meaningful flow or chart is valid without a table quota.
        validate_report(fragment.replace('<table>', '<div>').replace('</table>', '</div>'), 2000)
        with self.assertRaisesRegex(ReportValidationError, '边界说明'):
            validate_report(fragment.replace('class="boundary"', 'class="mechanism"'), 2000)
        rendered = BeautifulSoup(self.render(fragment), 'html.parser')
        answers = rendered.select('footer .self-check ol > li')
        self.assertIn('7类组件', answers[0].get_text())
        self.assertIn('1723汉字', answers[1].get_text())

    def test_visual_components_survive_cleanup_and_are_counted(self):
        fragment = '''<h2>事实支持可比较的数量</h2>
        <div class="bar-chart wide"><div class="bar-row"><span class="bar-label">样本</span>
        <div class="bar-track"><div class="bar-fill" style="--v:.72"></div></div><span class="bar-val">72</span></div></div>
        <ol class="funnel wide"><li style="--w:1"><strong>起点</strong><span>100</span></li>
        <li style="--w:.6"><strong>终点</strong><span>60</span></li></ol>
        <ol class="timeline"><li><strong>第一阶段</strong><p>事件。</p></li></ol>
        <div class="deck wide"><div class="dcard blue"><span class="lv">机制</span><strong>第一类</strong></div>
        <div class="dcard gold">证据</div><div class="dcard flash">特殊等级</div></div>
        <div class="matrix wide"><div class="mx q1">高高</div><div class="mx q2">低高</div>
        <div class="mx q3">低低</div><div class="mx q4">高低</div></div>'''
        cleaned = sanitize_fragment(fragment)
        soup = BeautifulSoup(self.render(fragment), 'html.parser')
        for name in ('wide','bar-chart','bar-row','bar-label','bar-track','bar-fill','bar-val',
                     'funnel','timeline','deck','dcard','lv','blue','gold','flash','matrix','mx','q1','q2','q3','q4'):
            self.assertIsNotNone(soup.select_one('.' + name), name)
        self.assertEqual(soup.select_one('.bar-fill')['style'], '--v:0.72')
        self.assertEqual([li['style'] for li in soup.select('.funnel > li')], ['--w:1', '--w:0.6'])
        metrics = report_metrics(cleaned)
        for name in ('条形图','漏斗','时间线','等级卡牌','四象限'):
            self.assertEqual(metrics['components'][name], 1)

    def test_chart_styles_are_bounded_numeric_and_context_specific(self):
        for value in ('0', '.72', '1', '0.000001', '1.000000'):
            soup = BeautifulSoup(sanitize_fragment(f'<div class="bar-fill" style="--v:{value}"></div>'), 'html.parser')
            self.assertIn('style', soup.div.attrs)
        for value in ('-1', '1.01', 'NaN', 'Infinity', '1e0', '.1234567', '72%',
                      'var(--x)', 'calc(1)', 'url(https://evil.test)',
                      '0.5;color:red', '0.5;--x:1', '0.5/*comment*/'):
            with self.subTest(value=value):
                cleaned = sanitize_fragment(f'<div class="bar-fill" style="--v:{value}">文字</div>')
                self.assertNotIn('style=', cleaned)
                self.assertIn('文字', cleaned)
        for fragment in ('<p class="bar-fill" style="--v:.5">文字</p>',
                         '<div class="bar-fill" style="--w:.5"></div>',
                         '<li style="--w:.5">文字</li>',
                         '<ol class="funnel" style="--w:.5"><li><ul><li style="--w:.5">文字</li></ul></li></ol>',
                         '<ol class="funnel"><li>一</li></ol><ol><li style="--w:.5">二</li></ol>'):
            self.assertNotIn('style=', sanitize_fragment(fragment))
        nested = sanitize_fragment('<ol class="funnel"><li style="--w:1"><ol class="funnel"><li style="--w:.5">内</li></ol></li><li style="--w:.4">外</li></ol>')
        self.assertEqual(nested.count('style='), 3)

    def test_new_title_preserves_emphasis_without_component_styles(self):
        soup = BeautifulSoup(self.render('<p class="report-title">把任务<br>交给<span class="title-accent">合适的工具</span></p>'), 'html.parser')
        self.assertIsNotNone(soup.h1.br)
        self.assertEqual(soup.h1.select_one('.title-accent').get_text(), '合适的工具')
        self.assertEqual(soup.select_one('.report')['data-template'], 'visual-v6')

    def test_saved_html_input_has_accurate_provenance(self):
        document = render_document({**ARTICLE, 'inputMode':'saved-html'}, sanitize_fragment(FRAGMENT), '2026-11-09').decode('utf-8')
        self.assertIn('基于用户提供的微信网页正文', document)
        self.assertNotIn('基于RSS收录正文', document)


if __name__ == '__main__':
    unittest.main()
