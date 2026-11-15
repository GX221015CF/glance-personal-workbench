"""Fixed Tencent minute-feed fallback, converted to the existing quote format."""
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from wechat_bridge import RestrictedRedirect

SYMBOLS = {'1.000905': 'sh000905', '0.159770': 'sz159770',
           '124.HSTECH': 'hkHSTECH', '105.SOXX': 'usSOXX'}
TZ = dt.timezone(dt.timedelta(hours=8))


def normalize_tencent(spec, payload):
    symbol = SYMBOLS[spec['id']]
    if payload.get('code') != 0:
        raise ValueError('Tencent source unavailable')
    entry = payload.get('data', {}).get(symbol, {})
    quote = entry.get('qt', {}).get(symbol, [])
    expected = spec['code'] + '.OQ' if spec['code'] == 'SOXX' else spec['code']
    if len(quote) < 31 or quote[2] != expected:
        raise ValueError('Unexpected Tencent instrument')
    source_zone = ZoneInfo('America/New_York') if spec['venue'] == 'US' else TZ
    quoted = None
    for pattern in ('%Y%m%d%H%M%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            quoted = dt.datetime.strptime(quote[30], pattern).replace(tzinfo=source_zone)
            break
        except ValueError:
            continue
    if quoted is None:
        raise ValueError('Missing Tencent quote time')
    day = entry.get('data', {}).get('date') or quoted.strftime('%Y%m%d')
    if day != quoted.strftime('%Y%m%d'):
        raise ValueError('Tencent series and quote dates differ')
    points = {}
    for line in entry.get('data', {}).get('data', []):
        fields = line.split()
        if len(fields) < 2 or not re.fullmatch(r'\d{4}', fields[0]):
            raise ValueError('Invalid Tencent minute data')
        when = dt.datetime.strptime(day + fields[0], '%Y%m%d%H%M').replace(tzinfo=source_zone)
        # Tencent labels the current minute bucket by its ending minute.
        if when > quoted:
            if (when - quoted).total_seconds() > 60:
                raise ValueError('Future Tencent minute bucket')
            when = quoted
        price = float(fields[1])
        if not 0 < price < float('inf'):
            raise ValueError('Invalid Tencent price')
        points[when.astimezone(TZ).strftime('%Y-%m-%d %H:%M')] = price
    if not points:
        raise ValueError('Missing Tencent minute series')
    # The final quote supplies both its own price and timestamp.
    points[quoted.astimezone(TZ).strftime('%Y-%m-%d %H:%M')] = float(quote[3])
    return {'code': spec['code'], 'preClose': float(quote[4]),
            'trends': [f'{when},0,{price}' for when, price in sorted(points.items())]}


def fetch_tencent(spec):
    symbol = SYMBOLS[spec['id']]
    url = 'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=' + symbol
    def allowed(target):
        parsed = urllib.parse.urlsplit(target)
        return parsed.scheme == 'https' and parsed.netloc == 'web.ifzq.gtimg.cn'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RestrictedRedirect(allowed))
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})
    with opener.open(request, timeout=10) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Tencent response too large')
    return normalize_tencent(spec, json.loads(raw)), dt.datetime.now(TZ).isoformat(timespec='seconds')
