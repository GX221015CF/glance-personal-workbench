"""Trusted, offline report shell around sanitized WorkBuddy content."""
import base64
from html import escape, unescape
import hashlib
import math
from pathlib import Path
import re

from bs4 import BeautifulSoup
from wechat_bridge import safe_article_url

ROOT = Path(__file__).resolve().parent
REPORT_CSS = (ROOT / 'assets' / 'analysis-report.css').read_text(encoding='utf-8')
REPORT_SCRIPT = (ROOT / 'assets' / 'analysis-report.js').read_text(encoding='utf-8').strip()
SCRIPT_HASH = base64.b64encode(hashlib.sha256(REPORT_SCRIPT.encode('utf-8')).digest()).decode('ascii')
REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; "
              f"script-src 'sha256-{SCRIPT_HASH}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SEMANTIC_CLASSES = {
    'report-title', 'report-subtitle', 'core-conclusion', 'evidence-grid', 'evidence-item',
    'chapter-intro', 'card-grid', 'card', 'mechanism', 'transfer', 'boundary', 'risk',
    'extension', 'closing', 'simulator', 'deep-dive', 'step-flow', 'step', 'comparison',
    'compare-item', 'judgment', 'source-notes', 'self-check', 'appendix',
    'title-accent', 'wide', 'bar-chart', 'bar-row', 'bar-label', 'bar-track',
    'bar-fill', 'bar-val', 'funnel', 'timeline', 'deck', 'dcard', 'lv',
    'blue', 'gold', 'flash', 'matrix', 'mx', 'q1', 'q2', 'q3', 'q4',
}
SIMULATOR_ATTRIBUTES = {'data-min', 'data-max', 'data-step', 'data-value', 'data-factor',
                        'data-offset', 'data-input-label', 'data-output-label', 'data-unit'}

COMPONENT_SELECTORS = {
    '表格': 'table', '卡片网格': '.card-grid', '步骤流': '.step-flow', '对比块': '.comparison',
    '提示框': '.mechanism, .risk, .judgment', '数字组': '.evidence-grid', '边界说明': '.boundary',
    '收束段': '.closing', '引文': 'blockquote',
    '条形图': '.bar-chart', '漏斗': '.funnel', '时间线': '.timeline',
    '等级卡牌': '.deck', '四象限': '.matrix',
}


class ReportValidationError(ValueError):
    pass


def han_count(text):
    return len(re.findall(r'[\u3400-\u9fff]', text))


def source_han_count(text):
    return han_count(text.replace('[配图地址已保存在本地，但没有交给模型做视觉分析]', ''))


def chapter_fragments(soup):
    sections = []
    prelude = ''
    for chunk in re.split(r'(?=<h2(?:\s[^>]*)?>)', str(soup)):
        if not BeautifulSoup(chunk, 'html.parser').get_text(strip=True):
            continue
        heading = re.match(r'<h2(?:\s[^>]*)?>(.*?)</h2>', chunk, re.DOTALL)
        if not heading:
            prelude += chunk
            continue
        label = unescape(re.sub(r'<[^>]*>', '', heading.group(1)))
        if prelude:
            chunk = chunk[:heading.end()] + prelude + chunk[heading.end():]
            prelude = ''
        sections.append((f'chapter-{len(sections):02d}', label, chunk))
    return sections


def report_metrics(fragment):
    body = BeautifulSoup(fragment, 'html.parser')
    self_check = body.select_one('.self-check')
    answers = len(self_check.select('ol > li')) if self_check else 0
    for node in list(body.select('.source-notes, .self-check, .appendix, .report-title, .report-subtitle')):
        node.decompose()
    paragraphs = [han_count(p.get_text()) for p in body.find_all('p') if p.get_text(strip=True)]
    sections = BeautifulSoup(str(body), 'html.parser')
    for node in list(sections.select('.core-conclusion, .closing')):
        node.decompose()
    chapters = chapter_fragments(sections)
    components = {name:len(body.select(selector)) for name,selector in COMPONENT_SELECTORS.items()}
    chapter_components = []
    for _, _, chunk in chapters:
        node = BeautifulSoup(chunk, 'html.parser')
        chapter_components.append([name for name,selector in COMPONENT_SELECTORS.items() if node.select(selector)])
    return {'hanChars':han_count(body.get_text()), 'paragraphCount':len(paragraphs),
            'averageParagraphHan':round(sum(paragraphs) / max(1, len(paragraphs)), 2),
            'maxParagraphHan':max(paragraphs, default=0), 'overlongParagraphs':sum(n > 150 for n in paragraphs),
            'tableCount':components['表格'], 'componentKinds':sum(bool(n) for n in components.values()),
            'components':components, 'chapterCount':len(chapters), 'headings':[label for _,label,_ in chapters],
            'chapterComponents':chapter_components, 'bodyDetails':len(body.select('details')),
            'bodyProvenanceLabels':bool(re.search(r'原文要点|分析与推论|无法核实', body.get_text())),
            'selfCheckAnswers':answers}


def validate_report(fragment, source_count):
    soup = BeautifulSoup(fragment, 'html.parser')
    metrics = report_metrics(fragment)
    errors = []
    if any(not soup.find(class_=name) for name in ('report-title', 'core-conclusion', 'closing', 'source-notes', 'self-check')):
        errors.append('缺少标题、结论、收束或页脚说明/自检')
    if source_count and not math.ceil(source_count * .8) <= metrics['hanChars'] <= math.floor(source_count * 1.2):
        errors.append(f"正文{metrics['hanChars']}汉字，应为原文{source_count}字的0.8–1.2倍")
    if not 3 <= metrics['chapterCount'] <= 7:
        errors.append('机制章节需为3–7章')
    if not metrics['components']['边界说明']:
        errors.append('需有边界说明，组件按内容选择')
    if any(not kinds for kinds in metrics['chapterComponents']):
        errors.append('每章需有非纯文字组件')
    if any(a == b for a,b in zip(metrics['chapterComponents'],metrics['chapterComponents'][1:])):
        errors.append('相邻章节组件组合重复')
    if metrics['averageParagraphHan'] > 60 or metrics['overlongParagraphs']:
        errors.append(f"段落平均{metrics['averageParagraphHan']}字，最长{metrics['maxParagraphHan']}字；需平均≤60且单段≤150")
    if metrics['bodyDetails'] or metrics['bodyProvenanceLabels']:
        errors.append('正文不能折叠或插入溯源标签，需移到页脚')
    if metrics['selfCheckAnswers'] != 5:
        errors.append('页脚须逐项回答5条交付自检')
    if errors:
        raise ReportValidationError('设计验收未通过：' + '；'.join(errors))
    return metrics


def report_filename(title):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' ._')[:90] or '公众号'
    return title + '_分析.html'


def simulation_html(attributes, explanation, index):
    """Only one bounded affine expression is supported. No model code is evaluated."""
    try:
        numbers = {key: float(attributes['data-' + key]) for key in ('min', 'max', 'step', 'value', 'factor', 'offset')}
        low, high, step, value, factor, offset = (numbers[key] for key in ('min', 'max', 'step', 'value', 'factor', 'offset'))
        if (not all(math.isfinite(n) and abs(n) <= 1e12 for n in numbers.values())
                or low >= high or not 0 < step <= high - low or not low <= value <= high
                or not all(math.isfinite(factor * x + offset) and abs(factor * x + offset) <= 1e15 for x in (low, high))):
            raise ValueError('Invalid simulation bounds')
    except (KeyError, ValueError, TypeError, OverflowError):
        return '<div class="boundary"><strong>简化模型暂不可交互</strong><p>参数不足或超出安全范围，请以公式及原文证据为准。</p>' + explanation + '</div>'
    label = escape(str(attributes.get('data-input-label', '输入'))[:160], quote=True)
    output_label = escape(str(attributes.get('data-output-label', '结果'))[:160], quote=True)
    unit = escape(str(attributes.get('data-unit', ''))[:80], quote=True)
    identifier = f'simulator-{index}'
    result = factor * value + offset
    position = 100 * (value - low) / (high - low)
    return (f'<div class="simulator" data-factor="{factor}" data-offset="{offset}" data-unit="{unit}">'
            '<strong>简化模型</strong>' + explanation +
            f'<label for="{identifier}">{label} · <span class="simulator-current">{value:g}</span></label>'
            f'<input id="{identifier}" type="range" min="{low}" max="{high}" step="{step}" value="{value}">'
            f'<p>{output_label} <output for="{identifier}" aria-live="polite">{result:g}{unit}</output></p>'
            f'<progress max="100" value="{position}" aria-label="输入在所设范围内的位置"></progress>'
            '<p class="note">仅演示上文声明的线性关系，保持其他条件不变。条形表示输入位置；打印或禁用脚本时保留初始结果。</p></div>')


def render_document(article, fragment, created_at, audit=None):
    # This function only receives the fragment passed through FragmentSanitizer.
    soup = BeautifulSoup(fragment, 'html.parser')
    def take(class_name, fallback=''):
        node = soup.find(class_=class_name)
        return node.extract() if node else fallback

    title_node = take('report-title')
    if title_node:
        # Preserve intentional line breaks and emphasis from the sanitized title.
        # Other component containers have no meaning inside a heading.
        for node in list(title_node.find_all(True)):
            if node.name not in {'br', 'strong', 'b', 'em', 'span'}:
                node.unwrap()
            else:
                node.attrs = {'class': ['title-accent']} if 'title-accent' in node.get('class', []) else {}
        title_html = title_node.decode_contents()
    else:
        title_html = escape(article['title'])
    subtitle_node = take('report-subtitle')
    subtitle = subtitle_node.get_text(' ', strip=True) if subtitle_node else '原文依据、模型分析与待核实内容分开阅读。'
    conclusion_node = take('core-conclusion')
    conclusion = conclusion_node.get_text(' ', strip=True) if conclusion_node else '以下为模型分析，请结合原文依据与核验边界阅读。'
    source_notes = take('source-notes')
    self_check = take('self-check')
    appendix = take('appendix')
    metrics = report_metrics(fragment)
    if self_check:
        answers = self_check.select('ol > li')
        if len(answers) >= 2:
            names = '、'.join(name for name,count in metrics['components'].items() if count)
            answers[0].clear()
            answers[0].append(f"工作台独立统计：{metrics['tableCount']}张表格，{metrics['componentKinds']}类组件（{names}）；三类提示框合并为一类计数。")
            answers[1].clear()
            answers[1].append(f"工作台独立统计：正文{metrics['hanChars']}汉字，{metrics['paragraphCount']}个段落平均{metrics['averageParagraphHan']}汉字，最长{metrics['maxParagraphHan']}汉字，超过150字的段落{metrics['overlongParagraphs']}段。")
    closing_node = take('closing')
    closing = closing_node.get_text(' ', strip=True) if closing_node else '理解一项主张，也要知道它依靠什么证据、止于什么边界。'
    for index, simulator in enumerate(soup.select('.simulator')):
        markup = simulation_html(simulator.attrs, simulator.decode_contents(), index)
        simulator.replace_with(BeautifulSoup(markup, 'html.parser'))
    sections = chapter_fragments(soup)
    nav = ''.join(f'<a href="#{identifier}"><span>{index:02d}</span>{escape(label)}</a>'
                  for index, (identifier, label, _) in enumerate(sections))
    chapters = ''.join(f'<section id="{identifier}" class="chapter"><div class="chapter-kicker">CHAPTER {index:02d}</div>{chunk}</section>'
                       for index, (identifier, _, chunk) in enumerate(sections))
    count = metrics['hanChars']
    minutes = max(1, math.ceil(count / 450))
    safe_url = safe_article_url(article.get('url', ''))
    original = (f'<a class="original" href="{escape(safe_url, quote=True)}" target="_blank" rel="noopener noreferrer">手动打开微信原文 ↗</a>'
                if safe_url else '')
    audit_html = ''
    if audit:
        request, result = audit['request'], audit['result']
        prefs = request['preferences']
        items = ''.join('<li>' + escape(item) + '</li>' for item in prefs['items'])
        sources = '、'.join(s['file'] for s in prefs['sources'] if s['selected']) or '无'
        audit_html = ('<details class="source"><summary>本次解读的模型与个人偏好</summary>'
                      f'<p>请求模型：{escape(request["arguments"]["model"])}；MCP报告模型：{escape(result.get("reportedModel") or "未返回具体模型")}；'
                      f'请求推理强度：{escape(request["arguments"]["effort"])}。</p>'
                      f'<p>提示词版本：{escape(request["promptVersion"])}；偏好来源：{escape(sources)}；已传入{len(prefs["items"])}条表达偏好。</p>'
                      f'<ul>{items}</ul><p>{escape("；".join(prefs["warnings"]))}</p>'
                      '<p>偏好仅控制表达，不代表继承完整聊天历史；模型名称为MCP报告值。完整调用记录保存在本机analysis-audit目录。</p></details>')
    layout = 'layout'
    input_description = ('用户提供的微信网页正文' if article.get('inputMode') == 'saved-html' else 'RSS收录正文')
    input_limit = ('内容范围以用户提供的网页文件为限' if article.get('inputMode') == 'saved-html'
                   else '第三方Feed可能不完整或有延迟')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="{escape(REPORT_CSP, quote=True)}">
<title>{escape(article['title'])} · 知识分析</title><style>{REPORT_CSS}</style></head><body><div class="report" data-template="visual-v6">
<header class="hero"><p class="eyebrow">知识分析</p><h1>{title_html}</h1>
<div class="core-conclusion">{escape(conclusion)}</div><p class="subtitle">{escape(subtitle)}</p>
<p class="meta">{escape(article['source'])} · {escape(article['publishedAt'])} · 约{minutes}分钟</p></header>
<div class="{layout}"><nav aria-label="文章目录"><div class="nav-title">阅读目录</div><div class="nav-links">{nav}</div></nav>
<main>{chapters}<p class="closing">{escape(closing)}</p>
 </main></div>
<footer><p class="meta">来源 {escape(article['source'])} · 文章日期 {escape(article['publishedAt'])}<br>正文约{count}汉字 · 约{minutes}分钟（按450字/分钟估算）</p>
<section class="source"><strong>来源与判断边界</strong><p>本页基于{input_description}，由WorkBuddy重新组织。作者陈述与模型推导不等于外部事实核验；本次未自动访问微信原文或进行图片视觉分析。</p>{source_notes}{original}</section>
{self_check}{appendix}{audit_html}<p>生成于 {escape(created_at)}（北京时间）。{input_limit}，外部主张须另行核对。</p></footer>
</div><script>{REPORT_SCRIPT}</script></body></html>'''.encode('utf-8')
