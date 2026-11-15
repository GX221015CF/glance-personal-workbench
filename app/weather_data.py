"""Validated forecast. Model history must never masquerade as observations."""
import datetime as dt
import math

TZ = dt.timezone(dt.timedelta(hours=8))


def valid_temperature(low, high):
    return all(type(value) in (int, float) and math.isfinite(value) for value in (low, high)) and -80 <= low <= high <= 65


def weather_payload(data, today=None):
    today = today or dt.datetime.now(TZ).date()
    source = data['daily']
    dates = source['time']
    if not isinstance(dates, list) or len(dates) != len(set(dates)):
        raise ValueError('Invalid or duplicate weather dates')
    current_dates = [(today + dt.timedelta(days=i)).isoformat() for i in (0, 1)]
    daily = {key: [] for key in ('time', 'weather_code', 'temperature_2m_max', 'temperature_2m_min')}
    for date in current_dates:
        index = dates.index(date)
        low, high = source['temperature_2m_min'][index], source['temperature_2m_max'][index]
        code = source['weather_code'][index]
        if not valid_temperature(low, high) or type(code) is not int or not 0 <= code <= 99:
            raise ValueError('Invalid current weather forecast')
        for key in daily:
            daily[key].append(source[key][index])
    # No validated station-observation feed is currently connected. Ignore any
    # past_days supplied by the forecast source; the client falls back to today.
    baseline = {'available': False, 'sampleCount': 0, 'kind': 'none'}
    return {'available': True, 'daily': daily, 'baseline': baseline, 'stale': False, 'weatherVersion': 3}
