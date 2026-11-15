"""Local interest matching and deterministic merging for AIHOT API data."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import urllib.parse

CONFIG = Path(__file__).resolve().parent / 'config' / 'aihot-interests.json'


def normalized(text):
    text = unicodedata.normalize('NFKC', str(text or '')).casefold()
    return re.sub(r'[‐‑‒–—﹘﹣－_]', '-', text)


def keyword_pattern(keyword):
    value = normalized(keyword)
    compact = re.sub(r'[\s.\-]+', '', value)
    brands = {'openai': r'open[\s.\-]*ai', 'chatgpt': r'chat[\s\-]*gpt',
              'deepseek': r'deep[\s\-]*seek', 'openrouter': r'open[\s\-]*router',
              'claudecode': r'claude[\s\-]*code', 'notebooklm': r'notebook[\s\-]*lm',
              'xai': r'x[\s.\-]*ai'}
    pattern = brands.get(compact) or re.escape(value).replace(r'\ ', r'[\s\-]+')
    if value.isascii():
        # Permit model versions (GPT-6 / GPT6), but reject Grokking, Copus, etc.
        if re.match(r'[a-z0-9]', value):
            pattern = r'(?<![a-z0-9])' + pattern
        if re.search(r'[a-z0-9]$', value):
            versioned = {'gpt', 'qwen', 'qwq', 'claude', 'gemini', 'grok', 'opus', 'fable', 'haiku', 'sonnet', 'deepseek', 'gemma'}
            pattern += r'(?![a-z])' if compact in versioned else r'(?![a-z0-9])'
    return re.compile(pattern)


class Interests:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.window = config.get('window', '24h')
        self.max_items = config.get('maxItems', 12)
        self.query_limit = config.get('queryLimit', 4)
        self.request_delay = config.get('requestDelaySeconds', 0.75)
        self.excluded_categories = set(config.get('excludedCategories', []))
        if self.window not in ('24h', '7d') or not 1 <= self.max_items <= 50 or not 1 <= self.query_limit <= 20:
            raise ValueError('Invalid news window or limit')
        if not 0.1 <= self.request_delay <= 5:
            raise ValueError('Invalid query pacing')
        self.groups, self.queries = [], []
        seen = set()
        for group in config.get('groups', []):
            label = group.get('label')
            keywords = group.get('keywords') or []
            if not isinstance(label, str) or not label.strip() or not keywords:
                raise ValueError('Each interest group needs a label and keywords')
            if any(not isinstance(word, str) or not 2 <= len(word.strip()) <= 100 for word in keywords):
                raise ValueError('Invalid keyword')
            self.groups.append((label, [keyword_pattern(word) for word in keywords]))
            for query in group.get('queries', keywords):
                if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
                    raise ValueError('Invalid server query')
                query = query.strip()
                if query.casefold() not in seen:
                    seen.add(query.casefold())
                    self.queries.append(query)
        if not self.groups or not self.queries or len(self.queries) > 80:
            raise ValueError('Interest groups or query count invalid')
        self.version = hashlib.sha256(('matcher-v3:' + json.dumps(config, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:16]

    @classmethod
    def load(cls):
        return cls(json.loads(CONFIG.read_text(encoding='utf-8-sig')))

    def matches(self, item):
        if self.excluded(item):
            return []
        source = item.get('source') or {}
        fields = [item.get('title'), item.get('originalTitle'), item.get('summary'), item.get('leadParagraph'),
                  source.get('name') if isinstance(source, dict) else source]
        text = normalized('\n'.join(str(value) for value in fields if value))
        return [label for label, patterns in self.groups if any(pattern.search(text) for pattern in patterns)]

    def excluded(self, item):
        if 'paper' not in self.excluded_categories:
            return False
        category = item.get('category')
        if isinstance(category, dict):
            category = category.get('slug') or category.get('name')
        if normalized(category) in ('paper', 'papers', 'research', '论文', '论文研究'):
            return True
        title = normalized(item.get('title'))
        original = (item.get('links') or {}).get('original') or item.get('originalUrl') or ''
        return bool(re.search(r'论文|研究论文|研究[：:]|\bresearch paper\b|\bpaper[：:]', title) or urllib.parse.urlparse(original).hostname in ('arxiv.org', 'www.arxiv.org'))

    def filter_items(self, items):
        result = []
        for item in items:
            groups = self.matches(item)
            if groups:
                result.append({**item, '_matchedGroups': groups})
        return result

    def filter_report(self, report):
        result = copy.deepcopy(report)
        lead = result.get('lead') or {}
        result['lead'] = {**lead, '_matchedGroups': self.matches(lead)} if self.matches(lead) else {}
        result['sections'] = []
        for section in report.get('sections') or []:
            if 'paper' in self.excluded_categories and normalized(section.get('label')) in ('论文研究', '论文', 'paper', 'research'):
                continue
            items = self.filter_items(section.get('items') or [])
            if items:
                result['sections'].append({**section, 'items': items})
        result['flashes'] = self.filter_items(report.get('flashes') or [])
        return result

    def query_url(self, query, mode='selected'):
        if mode not in ('selected', 'all'):
            raise ValueError('Invalid news mode')
        return 'https://aihot.news/api/v1/items?' + urllib.parse.urlencode([
            ('mode', mode), ('window', self.window), ('q', query), ('limit', str(self.query_limit))])


def timeline_timestamp(item):
    def parse(value):
        try:
            timestamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            return timestamp.timestamp() if timestamp.tzinfo else 0
        except (ValueError, TypeError, AttributeError, OverflowError):
            return 0
    published, discovered = parse(item.get('publishedAt')), parse(item.get('discoveredAt'))
    return published if published and discovered - published > 72 * 3600 else discovered or published


def merge_news(items, interests):
    unique = {}
    for item in interests.filter_items(items):
        key = item.get('id') or (item.get('links') or {}).get('aihot')
        if not key:
            continue
        if key not in unique or (item.get('selected') and not unique[key].get('selected')):
            unique[key] = item
    return sorted(unique.values(), key=lambda item: (-timeline_timestamp(item), str(item.get('id') or '')))[:interests.max_items]
