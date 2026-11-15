"""Local data bridge and allowlisted desktop shortcuts for this Glance installation."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import gzip
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from weather_data import weather_payload
from news_interests import Interests, merge_news, timeline_timestamp
from gmail_bridge import GmailService, GmailError
from youtube_bridge import YouTubeService
from sufe_bridge import SufeService, SufeError, api_envelope
from canvas_bridge import CanvasService, handle_request as handle_canvas_request
from academic_bridge import AcademicService, handle_request as handle_academic_request, refresh_teaching_and_academic
from school_saved_check import handle_request as handle_school_saved_check
from desktop_relay import handle_request as handle_desktop_relay
from study_marks import handle_request as handle_study_marks
from ima_bridge import ImaService, handle_request as handle_ima_request
from ima_workspace import ImaWorkspace, handle_request as handle_ima_workspace
from school_connection_audit import record as record_school_connection, enable as enable_school_connection_audit
from school_connector.bridge_auth import NativeBridgeAuth
from calendar_bridge import CalendarService, handle_request as handle_calendar_request
from wechat_bridge import WeChatService, handle_request as handle_wechat_request
from market_fallback import fetch_tencent
from workbuddy_analysis import WorkBuddyAnalysis, handle_request as handle_wechat_analysis_request
from workbuddy_summary import WorkBuddySummary, handle_request as handle_wechat_summary_request

from workbench_config import DATA_DIR, OFFLINE
ROOT = Path(__file__).resolve().parent
CACHE = DATA_DIR / 'bridge-cache'
CACHE.mkdir(parents=True, exist_ok=True)
TZ = dt.timezone(dt.timedelta(hours=8), 'Asia/Shanghai')
LOCK = threading.RLock()
STATE = {}
GMAIL = GmailService()
YOUTUBE = YouTubeService()
SUFE = SufeService()
CANVAS = CanvasService()
IMA = ImaService()
IMA_WORKSPACE = ImaWorkspace()
ACADEMIC = AcademicService()
CALENDAR = CalendarService()
WECHAT = WeChatService()
WECHAT_ANALYSIS = WorkBuddyAnalysis(WECHAT)
WECHAT_SUMMARY = WorkBuddySummary(WECHAT, executor=WECHAT_ANALYSIS.executor)
DESKTOP_ORIGINS = ('http://127.0.0.1:8180', 'http://localhost:8180')
DESKTOP_ROOT = DATA_DIR / 'desktop-actions'
DESKTOP_ACTIONS = {'game': '游戏模式', 'office': '办公模式', 'status': '当前状态'}
DESKTOP_TOKEN = secrets.token_urlsafe(32)
SCHOOL_NATIVE_AUTH = NativeBridgeAuth()
DESKTOP_LAST_LAUNCH = 0
FOCUS_MARKETS = [
    {'id': '1.000905', 'name': '中证500', 'code': '000905', 'venue': 'CN', 'decimals': 2, 'note': 'A股中盘', 'url': 'https://quote.eastmoney.com/zs000905.html'},
    {'id': '105.SOXX', 'name': '半导体ETF', 'code': 'SOXX', 'venue': 'US', 'decimals': 2, 'note': '国富观察替代', 'url': 'https://quote.eastmoney.com/us/SOXX.html'},
    {'id': '0.159770', 'name': '机器人ETF', 'code': '159770', 'venue': 'CN', 'decimals': 3, 'note': '永赢观察替代', 'url': 'https://quote.eastmoney.com/sz159770.html'},
    {'id': '124.HSTECH', 'name': '恒生科技', 'code': 'HSTECH', 'venue': 'HK', 'decimals': 2, 'note': '港股科技', 'url': 'https://quote.eastmoney.com/q/124.HSTECH.html'},
]
WORLD_MARKETS = [
    ('中国', [('1.000001', '上证指数'), ('0.399001', '深证成指'), ('0.399006', '创业板指'), ('1.000300', '沪深300')]),
    ('香港', [('100.HSI', '恒生指数'), ('100.HSCEI', '恒生国企')]),
    ('日本', [('100.N225', '日经225')]),
    ('韩国', [('100.KS11', '韩国KOSPI')]),
    ('美国', [('100.SPX', '标普500'), ('100.NDX', '纳斯达克100'), ('100.DJIA', '道琼斯')]),
]
RETRY_UNTIL = {}
AIHOT_RETRY_UNTIL = 0
UA = 'aihot-skill/1.7.2 (+https://aihot.news/aihot-skill/)'
actor_file = DATA_DIR / 'aihot-actor-id'
try:
    actor = actor_file.read_text(encoding='utf-8').strip()
    if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}', actor):
        UA += ' aihot-actor/' + actor
except (OSError, UnicodeError):
    pass


def now_iso():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def stamp(value):
    if not value:
        return ''
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(TZ).strftime('%m-%d %H:%M')
    except (ValueError, TypeError):
        return str(value)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def cache_path(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.json')


def fetch(url, *, aihot=False):
    """ETag-aware GET. Retain last successful payload on a network failure."""
    global AIHOT_RETRY_UNTIL
    path = cache_path(url)
    cached = read_json(path, {})
    if time.time() < max(RETRY_UNTIL.get(url, 0), AIHOT_RETRY_UNTIL if aihot else 0):
        if cached.get('data') is not None:
            return cached['data'], True, cached.get('checkedAt', '')
        raise RuntimeError('Source asked the client to retry later')
    if aihot and cached.get('data') is not None:
        try:
            age = time.time() - dt.datetime.fromisoformat(cached['checkedAt']).timestamp()
            # Keyword polling is every 15 minutes; reuse that window on restarts.
            fresh_for = 900 if urllib.parse.urlparse(url).path == '/api/v1/items' else 60
            if 0 <= age < fresh_for:
                return cached['data'], False, cached['checkedAt']
        except (KeyError, ValueError, TypeError):
            pass
    headers = {'User-Agent': UA if aihot else 'Mozilla/5.0', 'Accept-Encoding': 'gzip'}
    if aihot:
        if not url.startswith(('https://aihot.news/api/v1/', 'https://aihot.virxact.com/api/v1/')):
            raise ValueError('Unexpected AIHOT API origin')
    else:
        if urllib.parse.urlparse(url).netloc != 'push2.eastmoney.com':
            raise ValueError('Unexpected market API origin')
        headers['Referer'] = 'https://quote.eastmoney.com/'
    if cached.get('etag'):
        headers['If-None-Match'] = cached['etag']
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=18) as response:
            raw = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            data = json.loads(raw)
            checked = now_iso()
            save_json(path, {'data': data, 'etag': response.headers.get('ETag'), 'checkedAt': checked})
            return data, False, checked
    except urllib.error.HTTPError as error:
        if error.code == 304 and cached.get('data') is not None:
            cached['checkedAt'] = now_iso()
            save_json(path, cached)
            return cached['data'], False, cached['checkedAt']
        if error.code == 429:
            try:
                wait = int(error.headers.get('Retry-After') or 60)
                detail = json.loads(error.read())
                wait = max(wait, int(detail.get('retryAfter', 0)))
            except (ValueError, TypeError):
                wait = 600
            RETRY_UNTIL[url] = time.time() + wait
            if aihot:
                AIHOT_RETRY_UNTIL = max(AIHOT_RETRY_UNTIL, RETRY_UNTIL[url])
        elif error.code in (566, 567):
            RETRY_UNTIL[url] = time.time() + 900
            if aihot:
                AIHOT_RETRY_UNTIL = max(AIHOT_RETRY_UNTIL, RETRY_UNTIL[url])
        logging.warning('Source HTTP %s: %s', error.code, url.split('?')[0])
    except (OSError, ValueError) as error:
        logging.warning('Source unavailable (%s): %s', type(error).__name__, url.split('?')[0])
    if cached.get('data') is not None:
        return cached['data'], True, cached.get('checkedAt', '')
    raise RuntimeError('No source data is currently available')


def publish(name, value):
    with LOCK:
        STATE[name] = value
    save_json(CACHE / (name + '-view.json'), value)


def clean_link(value):
    value = value or ''
    return value if urllib.parse.urlparse(value).scheme in ('http', 'https') else ''


def normalize_item(item):
    links = item.get('links') or {}
    return {
        'id': item.get('id') or '', 'category': item.get('category'),
        'originalUrl': clean_link(links.get('original')), 'timeline': timeline_timestamp(item),
        'title': item.get('title') or '',
        'summary': item.get('summary') or '',
        'reason': item.get('reason') or '',
        'source': (item.get('source') or {}).get('name') or '',
        'selected': item.get('selected') is True,
        'matchedLabel': ' · '.join(item.get('_matchedGroups') or []),
        'url': clean_link(links.get('aihot') or links.get('original')),
        'timeLabel': ('原文 ' + stamp(item['publishedAt'])) if item.get('publishedAt') else ('AIHOT收录 ' + stamp(item.get('discoveredAt'))),
    }


def refresh_daily():
    interests = Interests.load()
    index, stale_index, checked = fetch('https://aihot.news/api/v1/dailies?limit=1', aihot=True)
    items = index.get('items') or []
    if not items:
        publish('daily', {'available': False, 'message': 'AIHOT暂未发布可用日报', 'checkedAt': checked})
        return
    date = items[0].get('date', '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise ValueError('Invalid report date from index')
    detail, stale_report, checked = fetch('https://aihot.news/api/v1/dailies/' + date, aihot=True)
    report = detail.get('report')
    if not isinstance(report, dict):
        raise ValueError('Missing report')
    report = interests.filter_report(report)
    lead = report.get('lead') or {}
    sections = [{'label': section.get('label') or '', 'items': [normalize_item(i) for i in section.get('items') or []]} for section in report.get('sections') or []]
    publish('daily', {
        'available': True, 'date': report.get('date') or date,
        'url': clean_link((report.get('links') or {}).get('aihot')),
        'leadTitle': lead.get('title') or '', 'leadParagraph': lead.get('leadParagraph') or '',
        'sections': sections, 'flashes': [normalize_item(i) for i in report.get('flashes') or []],
        'stale': stale_index or stale_report, 'checkedAt': checked,
        'generatedLabel': stamp(report.get('generatedAt')),
        'hasMatches': bool(lead or sections or report.get('flashes')),
        'leadMatchedLabel': ' · '.join(lead.get('_matchedGroups') or []),
        'filterVersion': interests.version,
    })


def combined_news():
    """One reader-facing list; daily picks get space alongside recent updates."""
    interests = Interests.load()
    with LOCK:
        daily, selected = dict(STATE.get('daily', {})), dict(STATE.get('selected', {}))
    daily = daily if daily.get('filterVersion') == interests.version else {}
    selected = selected if selected.get('filterVersion') == interests.version else {}
    daily_rows = [dict(row, inDaily=True) for section in daily.get('sections', []) for row in section['items']]
    daily_rows.extend(dict(row, inDaily=True) for row in daily.get('flashes', []))
    records = []
    for row in daily_rows + selected.get('items', []):
        if not interests.matches(row):
            continue
        keys = {str(row.get(field) or '').strip() for field in ('id', 'url', 'originalUrl')}
        keys.discard('')
        title_key = re.sub(r'\W+', '', row.get('title', '').casefold())
        if title_key:
            keys.add('title:' + title_key)
        same = next((item for item in records if item['_keys'] & keys), None)
        if same:
            same['_keys'].update(keys)
            same['inDaily'] = same.get('inDaily', False) or row.get('inDaily', False)
            same['selected'] = same.get('selected', False) or row.get('selected', False)
            for field in ('summary', 'reason', 'timeLabel', 'source', 'timeline', 'category'):
                if row.get(field):
                    same[field] = row[field]
        else:
            records.append({**row, '_keys': keys})
    # Include daily picks, then fill the remaining spaces with newest updates.
    picked = records[:interests.max_items]
    picked.sort(key=lambda row: (-float(row.get('timeline') or 0), row.get('title', '')))
    for row in picked:
        row.pop('_keys')
        row['timeLabel'] = row.get('timeLabel') if row.get('timeline') else '日报 ' + daily.get('date', '')
        row['key'] = news_key(row)
    return {
        'available': bool(daily.get('available') or selected.get('available')),
        'message': '正在读取关注资讯，请稍后刷新', 'items': picked, 'maxItems': interests.max_items,
        'dailyDate': daily.get('date', ''), 'dailyUrl': daily.get('url', ''),
        'running': selected.get('running', not bool(selected)),
        'partial': selected.get('partial', False) or not bool(daily),
        'stale': daily.get('stale', False) or selected.get('stale', False),
        'completedQueries': selected.get('completedQueries', 0), 'queryCount': len(interests.queries),
    }


def news_key(row):
    identity = row.get('url') or row.get('originalUrl') or row.get('id') or re.sub(r'\W+', '', row.get('title', '').casefold())
    return hashlib.sha256(str(identity).encode()).hexdigest()[:20]


def refresh_selected():
    interests = Interests.load()
    collected, checks, failures = [], [], []
    stale, completed = False, 0
    with LOCK:
        previous = dict(STATE.get('selected', {}))
    cutoff = time.time() - (86400 if interests.window == '24h' else 7 * 86400)
    previous_rows = [row for row in previous.get('items', [])
                     if previous.get('filterVersion') == interests.version
                     and float(row.get('timeline') or 0) >= cutoff and interests.matches(row)]

    def snapshot(running):
        fresh = [normalize_item(item) for item in merge_news(collected, interests)]
        fresh_keys = {news_key(row) for row in fresh}
        retained = [row for row in previous_rows if news_key(row) not in fresh_keys] if running or failures else []
        rows = sorted(fresh + retained, key=lambda row: -float(row.get('timeline') or 0))[:interests.max_items]
        publish('selected', {
            'available': True, 'items': rows,
            'stale': stale or bool(retained), 'checkedAt': max(checks) if checks else now_iso(),
            'filterVersion': interests.version, 'partial': running or bool(failures), 'running': running,
            'completedQueries': completed, 'queryCount': len(interests.queries),
            'failedQueries': len(failures), 'window': interests.window, 'maxItems': interests.max_items,
        })

    snapshot(True)
    for query in interests.queries:
        if time.time() < AIHOT_RETRY_UNTIL:
            failures.extend(interests.queries[completed:])
            break
        try:
            data, cached, checked = fetch(interests.query_url(query), aihot=True)
            # Keep scope unchanged when an exact keyword has no selected hits.
            if not data.get('items'):
                time.sleep(interests.request_delay)
                data, cached_all, checked_all = fetch(interests.query_url(query, 'all'), aihot=True)
                cached = cached or cached_all
                checked = min(checked, checked_all)
            collected.extend(data.get('items') or [])
            stale = stale or cached
            checks.append(checked)
        except Exception as error:
            logging.warning('Interest query failed (%s): %s', query, type(error).__name__)
            failures.append(query)
        completed += 1
        snapshot(True)
        time.sleep(interests.request_delay)
    snapshot(False)


def reset_event_timestamp(event):
    times = []
    for field in ('createdAt', 'updatedAt', 'confirmedAt'):
        try:
            value = dt.datetime.fromisoformat(str(event.get(field) or '').replace('Z', '+00:00'))
            if value.tzinfo is None:
                value = value.replace(tzinfo=TZ)
            times.append(value.timestamp())
        except (ValueError, TypeError, OverflowError):
            continue
    return max(times, default=0)


def normalize_resets(events):
    ordered = sorted((event for event in events if isinstance(event, dict)), key=reset_event_timestamp, reverse=True)
    chosen = []
    for kind in ('reset_credit', 'direct_reset'):
        event = next((e for e in ordered if e.get('type') == kind), None)
        if event:
            timestamp = reset_event_timestamp(event)
            identity = str(event.get('id') or kind + ':' + str(event.get('createdAt') or event.get('url') or ''))
            revision = hashlib.sha256(json.dumps([identity, event.get('status'), timestamp], ensure_ascii=False).encode()).hexdigest()
            chosen.append({
                'id': identity,
                'revision': revision,
                'timestamp': timestamp,
                'label': '发重置卡' if kind == 'reset_credit' else '额度重置',
                'status': '已确认' if event.get('status') == 'confirmed' else '已预告，待确认',
                'timeLabel': ('确认帖 ' + stamp(event.get('confirmedAt'))) if event.get('confirmedAt') else ('预告帖 ' + stamp(event.get('createdAt'))),
                'url': clean_link(event.get('url')),
            })
    return sorted(chosen, key=lambda event: event['timestamp'], reverse=True)


def refresh_resets():
    data, stale, checked = fetch('https://aihot.news/api/v1/codex-resets', aihot=True)
    events = data.get('events') or []
    chosen = normalize_resets(events)
    publish('resets', {'available': True, 'url': clean_link(events[0].get('url')) if events else 'https://aihot.news/codex-reset', 'events': chosen, 'checkedLabel': stamp(data.get('checkedAt')), 'stale': stale, 'checkedAt': checked})


def sparkline(values):
    if len(values) < 2 or any(not isinstance(v, (float, int)) or v <= 0 for v in values):
        return ''
    lo, hi = min(values), max(values)
    return ' '.join(f'{i * 400 / (len(values) - 1):.2f},{32 if hi == lo else 62 - (v-lo)/(hi-lo)*60:.2f}' for i, v in enumerate(values))


def refresh_index():
    url = 'https://push2.eastmoney.com/api/qt/stock/trends2/get?secid=1.000905&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1'
    data, stale, checked = fetch(url)
    source = data.get('data') or {}
    trends = source.get('trends') or []
    points = []
    for line in trends:
        columns = line.split(',')
        if len(columns) >= 3 and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', columns[0]):
            price = float(columns[2])
            if price > 0:
                points.append((columns[0], price))
    previous = float(source.get('preClose') or 0)
    if len(points) < 2 or previous <= 0:
        raise ValueError('Incomplete CSI500 source data')
    when, price = points[-1]
    change = (price / previous - 1) * 100
    local_now = dt.datetime.now(TZ)
    same_day = when[:10] == local_now.strftime('%Y-%m-%d')
    source_time = dt.datetime.strptime(when, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
    minute = local_now.hour * 60 + local_now.minute
    during_session = local_now.weekday() < 5 and (570 <= minute <= 690 or 780 <= minute <= 900)
    delayed = during_session and (local_now - source_time).total_seconds() > 300
    publish('index', {
        'available': True, 'name': '中证500', 'code': '000905', 'price': price, 'changePercent': change,
        'direction': 'up' if change > 0 else 'down' if change < 0 else 'flat',
        'sourceTime': when, 'startTime': points[0][0][-5:], 'endTime': when[-5:],
        'sameDay': same_day, 'stale': stale or not same_day or delayed, 'checkedAt': checked,
        'points': sparkline([v for _, v in points]), 'pointCount': len(points),
        'url': 'https://quote.eastmoney.com/zs000905.html',
    })


def market_session(venue, current):
    """Expected weekday hours; timestamps still expose holidays/source delays."""
    local = current.astimezone(ZoneInfo('America/New_York')) if venue == 'US' else current.astimezone(TZ)
    minute = local.hour * 60 + local.minute
    sessions = {'CN': [(570, 690), (780, 900)], 'HK': [(570, 720), (780, 960)], 'US': [(570, 960)]}
    return local.weekday() < 5 and any(start <= minute <= end for start, end in sessions[venue])


def normalize_focus(spec, source, stale, checked, current=None):
    current = current or dt.datetime.now(TZ)
    points = []
    for line in source.get('trends') or []:
        columns = line.split(',')
        if len(columns) < 3 or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', columns[0]):
            continue
        price = float(columns[2])
        if not 0 < price < float('inf'):
            raise ValueError('Invalid price')
        points.append((columns[0], price))
    previous = float(source.get('preClose') or 0)
    if not points or not 0 < previous < float('inf'):
        raise ValueError('Missing quote or previous close')
    if source.get('code') != spec['code']:
        raise ValueError('Unexpected instrument')
    when, price = points[-1]
    source_time = dt.datetime.strptime(when, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
    age = (current - source_time).total_seconds()
    during = market_session(spec['venue'], current)
    delayed = during and age > 300
    if age < -120:
        raise ValueError('Future quote timestamp')
    change = price - previous
    return {
        **spec, 'available': True, 'price': price, 'priceLabel': f'{price:.{spec["decimals"]}f}',
        'change': change, 'changeLabel': f'{change:+.{spec["decimals"]}f}', 'changePercent': change / previous * 100,
        'direction': 'up' if change > 0 else 'down' if change < 0 else 'flat',
        'points': sparkline([value for _, value in points]), 'pointCount': len(points),
        'sourceTime': when, 'timeLabel': when[5:], 'checkedAt': checked,
        'sameDay': when[:10] == current.strftime('%Y-%m-%d'), 'startTime': points[0][0][-5:], 'endTime': when[-5:],
        'stale': stale or delayed, 'cached': stale, 'provider': '东方财富',
        'warning': during and (stale or delayed),
        'status': (f'行情滞后 {int(age // 60)}分钟' if delayed else
                   '更新失败 · 缓存' if stale and during else '盘中行情' if during else '非交易时段'),
    }


def refresh_focus(current=None):
    current = current or dt.datetime.now(TZ)
    def load(spec):
        with LOCK:
            previous = next((dict(row) for row in STATE.get('focus', {}).get('items', []) if row.get('id') == spec['id']), None)
        candidate = None
        try:
            url = 'https://push2.eastmoney.com/api/qt/stock/trends2/get?secid=' + spec['id'] + '&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1'
            data, stale, checked = fetch(url)
            candidate = normalize_focus(spec, data.get('data') or {}, stale, checked, current)
        except Exception as error:
            logging.warning('Focus %s refresh failed: %s', spec['code'], type(error).__name__)
        # Keep a newer successful fallback if the primary source regresses.
        if previous and previous.get('available') and (not candidate or previous.get('sourceTime', '') > candidate['sourceTime']):
            candidate = {**previous, 'stale': True, 'cached': True}
        if not candidate or candidate['stale'] and market_session(spec['venue'], current):
            try:
                source, checked = fetch_tencent(spec)
                fallback = normalize_focus(spec, source, False, checked, current)
                fallback['provider'] = '腾讯财经'
                if not candidate or fallback['sourceTime'] >= candidate.get('sourceTime', ''):
                    candidate = fallback
            except Exception as error:
                logging.warning('Tencent fallback %s unavailable: %s', spec['code'], type(error).__name__)
        if not candidate:
            return {**spec, 'available': False, 'status': '暂不可用'}
        age_minutes = int((current - dt.datetime.strptime(candidate['sourceTime'], '%Y-%m-%d %H:%M').replace(tzinfo=TZ)).total_seconds() // 60)
        if market_session(spec['venue'], current) and age_minutes > 5:
            candidate.update(stale=True, status=f'行情滞后 {age_minutes}分钟')
        elif candidate.get('cached'):
            candidate['status'] = '更新失败 · 缓存' if market_session(spec['venue'], current) else '非交易时段'
        candidate['warning'] = candidate.get('stale', False) and market_session(spec['venue'], current)
        candidate['attemptedAt'] = now_iso()
        candidate['statusDetail'] = (f"来源 {candidate.get('provider', '东方财富')}；行情时间 {candidate['sourceTime']} 北京时间；"
                                     f"最近成功获取 {candidate.get('checkedAt', '未知')}；本次检查 {candidate['attemptedAt']}")
        return candidate
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(load, FOCUS_MARKETS))
    # Preserve the previous CSI500 endpoint without polling it a second time.
    publish('index', items[0])
    publish('focus', {'available': True, 'items': items, 'checkedAt': now_iso()})


def refresh_world():
    instruments = [item for _, group in WORLD_MARKETS for item in group]
    ids = ','.join(key for key, _ in instruments)
    data, stale, checked = fetch('https://push2.eastmoney.com/api/qt/ulist.np/get?secids=' + ids + '&fields=f12,f14,f2,f3,f4,f124,f13&fltt=2&invt=2')
    source = {str(row.get('f13')) + '.' + str(row.get('f12')): row for row in (data.get('data') or {}).get('diff') or []}
    if not source:
        raise ValueError('Empty world market response')
    with LOCK:
        previous = {row['id']: row for group in STATE.get('world', {}).get('groups', []) for row in group['items']}
    groups = []
    for region, instruments in WORLD_MARKETS:
        items = []
        for key, name in instruments:
            row = source.get(key) or {}
            try:
                price, change, percent = [float(row[field]) for field in ('f2', 'f4', 'f3')]
                timestamp = float(row['f124'])
                if not 0 < price < float('inf') or not all(abs(value) < float('inf') for value in (change, percent)) or timestamp <= 0:
                    raise ValueError('Invalid quote')
                when = dt.datetime.fromtimestamp(timestamp, TZ)
                if when > dt.datetime.now(TZ) + dt.timedelta(minutes=2):
                    raise ValueError('Future quote')
                item = {'id': key, 'name': name, 'available': True, 'price': price, 'change': change, 'changePercent': percent,
                        'direction': 'up' if change > 0 else 'down' if change < 0 else 'flat',
                        'timeLabel': when.strftime('%m-%d %H:%M'), 'stale': stale,
                        'url': 'https://quote.eastmoney.com/q/' + key + '.html'}
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                item = {**previous[key], 'stale': True} if key in previous else {'id': key, 'name': name, 'available': False}
            items.append(item)
        groups.append({'name': region, 'items': items})
    publish('world', {'available': True, 'groups': groups, 'stale': stale, 'checkedAt': checked})


def refresh_weather():
    url = ('https://api.open-meteo.com/v1/forecast?latitude=31.23&longitude=121.47'
           '&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=Asia%2FShanghai&forecast_days=2')
    with urllib.request.urlopen(url, timeout=18) as response:
        data = json.load(response)
    publish('weather', {**weather_payload(data), 'checkedAt': now_iso()})


def worker(name, interval, callback):
    # Reuse fresh cache across process restarts instead of repeating API calls.
    cached = read_json(CACHE / (name + '-view.json'), {})
    if name == 'resets' and cached and any(not event.get('revision') for event in cached.get('events', [])):
        source = read_json(cache_path('https://aihot.news/api/v1/codex-resets'), {}).get('data', {})
        if source.get('events'):
            cached['events'] = normalize_resets(source['events'])
            save_json(CACHE / 'resets-view.json', cached)
    if name == 'weather' and cached.get('weatherVersion') != 3:
        cached = {}
    if name in ('daily', 'selected'):
        # Never show old unfiltered content after a rule change or upgrade.
        try:
            if cached.get('filterVersion') != Interests.load().version:
                cached = {}
        except (OSError, ValueError, TypeError):
            cached = {}
    delay = 0
    if cached:
        with LOCK:
            STATE[name] = cached
        try:
            delay = max(0, interval - (time.time() - dt.datetime.fromisoformat(cached['checkedAt']).timestamp()))
        except (KeyError, ValueError):
            pass
    if name in ('daily', 'selected') and cached.get('partial'):
        delay = 0
    if delay:
        time.sleep(delay)
    while True:
        try:
            callback()
        except Exception as error:
            logging.warning('%s refresh failed: %s', name, type(error).__name__)
            with LOCK:
                old = dict(STATE.get(name, {'available': False}))
            old['stale'] = True
            old['message'] = '数据暂时不可用，等待下一次更新'
            publish(name, old)
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def handle_sufe_v1(self):
        prefix = '/api/sufe/v1'
        path = urllib.parse.urlsplit(self.path).path
        if path != prefix and not path.startswith(prefix + '/'):
            return False

        def fail(status, code, message, headers=None):
            self.json_response(status, api_envelope(None, error={'code': code, 'message': message}), headers)

        if not self.school_api_request():
            fail(403, 'FORBIDDEN', '需要本机工作台来源与有效会话令牌')
            return True
        resource = 'snapshot' if path == prefix else path[len(prefix) + 1:]
        reads = ('snapshot', 'status', 'courses', 'exams', 'today', 'openapi.json')
        if resource not in (*reads, 'refresh', 'connect') or path == prefix + '/snapshot':
            fail(404, 'NOT_FOUND', '接口不存在')
            return True
        if self.path != path:
            fail(400, 'INVALID_REQUEST', '此版本接口不接受查询参数')
            return True
        expected = 'GET' if resource in reads else 'POST'
        if self.command != expected:
            fail(405, 'METHOD_NOT_ALLOWED', '此接口需要使用 ' + expected, {'Allow': expected})
            return True
        length = self.headers.get('Content-Length', '0')
        if self.headers.get('Transfer-Encoding') or not length.isdecimal():
            fail(400, 'INVALID_REQUEST', '请求长度无效')
            return True
        if resource != 'connect' and int(length) != 0:
            fail(400, 'INVALID_REQUEST', '此接口不接受请求体')
            return True
        if resource in reads:
            if resource == 'openapi.json':
                self.json_response(200, read_json(ROOT / 'docs' / 'sufe-api.openapi.json', {}))
            else:
                self.json_response(200, SUFE.api_snapshot(resource))
            return True
        if resource == 'refresh':
            result = refresh_teaching_and_academic(SUFE, ACADEMIC)
            if result['reason'] == 'cooldown':
                payload = api_envelope(result, SUFE.snapshot(), {'code': 'REFRESH_COOLDOWN', 'message': '请等待同步冷却时间结束'})
                self.json_response(429, payload, {'Retry-After': str(result['retryAfterSeconds'])})
            else:
                self.json_response(202, api_envelope(result, SUFE.snapshot()))
            return True
        if not 1 <= int(length) <= 131072:
            fail(400, 'INVALID_REQUEST', '加密会话大小无效')
            return True
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
            fail(415, 'UNSUPPORTED_MEDIA_TYPE', '请发送 application/octet-stream 加密会话')
            return True
        self.connection.settimeout(10)
        try:
            encrypted = self.rfile.read(int(length))
            if len(encrypted) != int(length):
                fail(400, 'INVALID_REQUEST', '加密会话接收不完整')
            else:
                result = SUFE.connect_encrypted(encrypted)
                record_school_connection('sufe', 'session_connect', result, SUFE.snapshot())
                self.json_response(200, api_envelope(result, SUFE.snapshot()))
        except SufeError as error:
            fail(error.status, error.code, str(error))
        except Exception:
            fail(503, 'SYNC_FAILED', '学校会话连接失败，请检查本机服务后重试')
        return True

    def local_request(self):
        return self.client_address[0] == '127.0.0.1' and self.headers.get('Host') in ('127.0.0.1:8182', 'localhost:8182')

    def desktop_request(self):
        return self.local_request() and self.headers.get('Origin') in DESKTOP_ORIGINS

    def school_api_request(self):
        return ((self.desktop_request() and secrets.compare_digest(self.headers.get('X-Desktop-Token', ''), DESKTOP_TOKEN))
                or SCHOOL_NATIVE_AUTH.authorize(self))

    def json_response(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        if self.desktop_request():
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.desktop_request() or not self.path.startswith(('/api/desktop', '/api/gmail', '/api/youtube', '/api/sufe', '/api/canvas', '/api/academic', '/api/calendar', '/api/wechat', '/api/ima')):
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.send_header('Access-Control-Allow-Headers', 'X-Desktop-Token, Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        global DESKTOP_LAST_LAUNCH
        if handle_ima_workspace(self, IMA_WORKSPACE, DESKTOP_TOKEN):
            return
        if handle_ima_request(self, IMA, DESKTOP_TOKEN):
            return
        if handle_study_marks(self, DESKTOP_TOKEN):
            return
        if handle_desktop_relay(self, {'sufe': SUFE, 'canvas': CANVAS, 'academic': ACADEMIC}, SCHOOL_NATIVE_AUTH.authorize):
            return
        if handle_school_saved_check(self, {'sufe': SUFE, 'canvas': CANVAS, 'academic': ACADEMIC}, SCHOOL_NATIVE_AUTH.authorize):
            return
        if handle_wechat_summary_request(self, WECHAT, WECHAT_SUMMARY, DESKTOP_TOKEN):
            return
        if handle_wechat_analysis_request(self, WECHAT, WECHAT_ANALYSIS, DESKTOP_TOKEN):
            return
        if handle_wechat_request(self, WECHAT, DESKTOP_TOKEN):
            return
        if handle_calendar_request(self, CALENDAR, DESKTOP_TOKEN):
            return
        if handle_academic_request(self, ACADEMIC, DESKTOP_TOKEN, SCHOOL_NATIVE_AUTH.authorize):
            return
        if handle_canvas_request(self, CANVAS, DESKTOP_TOKEN, SCHOOL_NATIVE_AUTH.authorize):
            return
        if self.handle_sufe_v1():
            return
        # Never accept a command/path from the client. GET cannot launch anything.
        if not self.desktop_request() or not secrets.compare_digest(self.headers.get('X-Desktop-Token', ''), DESKTOP_TOKEN):
            self.send_error(403)
            return
        if self.path == '/api/sufe/connect':
            length = self.headers.get('Content-Length', '')
            if self.headers.get('Transfer-Encoding') or not length.isdecimal() or not 1 <= int(length) <= 131072:
                self.json_response(400, {'message': '加密会话大小无效'})
                return
            self.connection.settimeout(10)
            try:
                encrypted = self.rfile.read(int(length))
                if len(encrypted) != int(length):
                    self.json_response(400, {'message': '加密会话接收不完整'})
                    return
                result = SUFE.connect_encrypted(encrypted)
                self.json_response(200, result)
            except SufeError as error:
                self.json_response(400, {'message': str(error)})
            except Exception:
                self.json_response(503, {'message': '学校会话连接失败，请检查本机服务后重试'})
            return
        if self.path == '/api/sufe/refresh':
            if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                self.json_response(400, {'message': '此接口不接受额外参数'})
                return
            SUFE.request_refresh()
            self.json_response(202, {'message': '已请求同步；学校要求验证时需在浏览器完成登录并重新连接'})
            return
        if self.path.startswith(('/api/gmail', '/api/youtube')):
            prefix, service = ('/api/youtube', YOUTUBE) if self.path.startswith('/api/youtube') else ('/api/gmail', GMAIL)
            if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                self.json_response(400, {'message': '此接口不接受额外参数'})
                return
            try:
                if self.path == prefix + '/connect':
                    self.json_response(200, {'authorizationUrl': service.begin_connect()})
                elif prefix == '/api/gmail' and re.fullmatch(r'/api/gmail/reconnect/[a-f0-9]{20}', self.path):
                    self.json_response(200, {'authorizationUrl': service.begin_connect(self.path.rsplit('/', 1)[-1])})
                elif prefix == '/api/gmail' and re.fullmatch(r'/api/gmail/remove/[a-f0-9]{20}', self.path):
                    removed = service.remove_account(self.path.rsplit('/', 1)[-1])
                    self.json_response(200, {'removed': removed, 'snapshot': service.snapshot()})
                elif self.path == prefix + '/refresh':
                    service.request_refresh()
                    self.json_response(202, {'message': '已请求同步'})
                else:
                    self.json_response(404, {'message': '接口不存在'})
            except GmailError as error:
                self.json_response(error.status, {'message': str(error)})
            return
        action = self.path.removeprefix('/api/desktop/') if self.path.startswith('/api/desktop/') else ''
        if action not in DESKTOP_ACTIONS:
            self.json_response(404, {'message': '未找到此快捷方式'})
            return
        if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
            self.json_response(400, {'message': '快捷方式不接受额外参数'})
            return
        target = DESKTOP_ROOT / (action + '.bat')
        with LOCK:
            if time.monotonic() - DESKTOP_LAST_LAUNCH < 5:
                self.json_response(429, {'message': '请稍等几秒，避免重复打开窗口'})
                return
            if not target.is_file():
                self.json_response(503, {'message': '本机快捷方式脚本不存在'})
                return
            try:
                subprocess.Popen(
                    [str(Path(os.environ['SystemRoot']) / 'System32' / 'cmd.exe'), '/d', '/c', str(target)],
                    cwd=str(DESKTOP_ROOT), creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            except OSError:
                self.json_response(503, {'message': '无法打开脚本窗口，请使用桌面快捷方式'})
                return
            DESKTOP_LAST_LAUNCH = time.monotonic()
        suffix = '；请查看状态窗口' if action == 'status' else '；请在 Windows 提示中确认，结果见脚本窗口'
        self.json_response(202, {'started': True, 'message': '已打开' + DESKTOP_ACTIONS[action] + suffix})

    def do_GET(self):
        if handle_ima_workspace(self, IMA_WORKSPACE, DESKTOP_TOKEN):
            return
        if handle_ima_request(self, IMA, DESKTOP_TOKEN):
            return
        if handle_study_marks(self, DESKTOP_TOKEN):
            return
        if handle_wechat_summary_request(self, WECHAT, WECHAT_SUMMARY, DESKTOP_TOKEN):
            return
        if handle_wechat_analysis_request(self, WECHAT, WECHAT_ANALYSIS, DESKTOP_TOKEN):
            return
        if handle_wechat_request(self, WECHAT, DESKTOP_TOKEN):
            return
        if handle_calendar_request(self, CALENDAR, DESKTOP_TOKEN):
            return
        if handle_academic_request(self, ACADEMIC, DESKTOP_TOKEN, SCHOOL_NATIVE_AUTH.authorize):
            return
        if handle_canvas_request(self, CANVAS, DESKTOP_TOKEN, SCHOOL_NATIVE_AUTH.authorize):
            return
        if self.handle_sufe_v1():
            return
        if not self.local_request():
            self.send_error(403)
            return
        if self.path in ('/api/sufe', '/api/sufe/courses', '/api/sufe/exams', '/api/sufe/today'):
            if not self.desktop_request() or not secrets.compare_digest(self.headers.get('X-Desktop-Token', ''), DESKTOP_TOKEN):
                self.send_error(403)
                return
            data = SUFE.snapshot()
            if self.path != '/api/sufe':
                part = self.path.rsplit('/', 1)[-1]
                data = {key: data.get(key) for key in ('available', 'stale', 'needsLogin', 'message', 'updatedAt', 'refreshing', 'semester', 'date', 'week', part)}
            self.json_response(200, data)
            return
        if self.path.startswith(('/oauth/gmail/callback', '/oauth/youtube/callback')):
            kind, service = ('youtube', YOUTUBE) if self.path.startswith('/oauth/youtube/callback') else ('gmail', GMAIL)
            label = 'YouTube' if kind == 'youtube' else '邮箱'
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != '/oauth/' + kind + '/callback' or len(self.path) > 10000:
                self.send_error(400)
                return
            try:
                service.complete_connect(urllib.parse.parse_qs(parts.query))
                title, message, status = label + '连接成功', '可关闭此窗口，返回工作台查看。继续点击添加账号即可连接其他账号。', 200
            except GmailError as error:
                title, message, status = label + '尚未连接', str(error), error.status
            body = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                    '<title>' + title + '</title><body style="font:16px/1.8 system-ui;background:#151519;color:#eee;max-width:600px;margin:15vh auto;padding:24px">'
                    '<h1>' + title + '</h1><p>' + html.escape(message) + '</p><a style="color:#d5bd83" href="http://127.0.0.1:8180/' + kind + '">返回工作台 ' + kind + '</a></body></html>').encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ('/api/gmail', '/api/youtube'):
            if not self.desktop_request() or not secrets.compare_digest(self.headers.get('X-Desktop-Token', ''), DESKTOP_TOKEN):
                self.send_error(403)
                return
            self.json_response(200, (YOUTUBE if self.path == '/api/youtube' else GMAIL).snapshot())
            return
        if self.path == '/api/desktop':
            if not self.desktop_request():
                self.send_error(403)
                return
            self.json_response(200, {'token': DESKTOP_TOKEN, 'actions': {key: (DESKTOP_ROOT / (key + '.bat')).is_file() for key in DESKTOP_ACTIONS}})
            return
        if self.path == '/api/news':
            self.json_response(200, combined_news())
            return
        key = {'/health': None, '/api/daily': 'daily', '/api/selected': 'selected', '/api/resets': 'resets', '/api/index': 'index', '/api/focus': 'focus', '/api/world': 'world', '/api/weather': 'weather'}.get(self.path, 'missing')
        if key == 'missing':
            self.send_error(404)
            return
        with LOCK:
            payload = {'ok': True, 'service': 'personal-workbench-bridge'} if key is None else STATE.get(key, {'available': False, 'message': '正在读取数据，请稍后刷新'})
        if key == 'weather' and (payload.get('daily', {}).get('time') or [''])[0] != dt.datetime.now(TZ).date().isoformat():
            payload = {'available': False, 'message': '正在更新今天和明天的天气'}
        self.json_response(200, payload)

    def log_message(self, *_):
        pass


def main():
    enable_school_connection_audit()
    network = read_json(ROOT / 'config' / 'network.json', {})
    proxies = {}
    for field, variable in [('httpProxy', 'HTTP_PROXY'), ('httpsProxy', 'HTTPS_PROXY')]:
        value = network.get(field)
        if value:
            parsed = urllib.parse.urlparse(value)
            if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username:
                raise ValueError('Only the configured local proxy is supported')
            proxies['http' if field == 'httpProxy' else 'https'] = value
    # Empty settings mean no application proxy; let the current network/TUN route.
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler(proxies)))
    os.environ['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    logging.basicConfig(filename=DATA_DIR / 'bridge.log', level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    # Bind before starting workers so a duplicate launch cannot cause duplicate polling.
    server = ThreadingHTTPServer(('127.0.0.1', 8182), Handler)
    try:
        SCHOOL_NATIVE_AUTH.publish()
    except Exception:
        # Disable only native access; existing dashboard and Yingdao access still work.
        logging.error('School native authentication unavailable')
    WECHAT_ANALYSIS.recover_interrupted()
    WECHAT_SUMMARY.recover_interrupted()
    if OFFLINE:
        server.serve_forever()
        return
    SUFE.start()
    CANVAS.start()
    threading.Thread(target=GMAIL.run, daemon=True).start()
    threading.Thread(target=YOUTUBE.run, daemon=True).start()
    threading.Thread(target=WECHAT.run, daemon=True).start()
    for name, interval, callback in [('daily', 900, refresh_daily), ('selected', 900, refresh_selected), ('resets', 600, refresh_resets), ('focus', 60, refresh_focus), ('world', 900, refresh_world), ('weather', 300, refresh_weather)]:
        threading.Thread(target=worker, args=(name, interval, callback), daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
