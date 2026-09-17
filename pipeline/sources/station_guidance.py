"""NOAA MOS/LAMP bulletins via IEM. Comparison only, never blend inputs.

Keep native valid times and precipitation periods. In particular, PPO is
instantaneous occurrence (including traces), not measurable daily rain.
"""
from __future__ import annotations

import copy
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from .. import quality
from ..util import local_day_window

ENDPOINT = 'https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py'
PRODUCTS = {'MOS': ('MAV', 'GFS MOS', 12), 'LAMP': ('LAV', 'GFS LAMP', 3)}
_CACHE = {}


def number(token, low, high):
    try:
        value = int(token)
        return value if low <= value <= high else None
    except (ValueError, TypeError):
        return None


def parse(text, station, product):
    """Parse by fixed columns, preserving blank P06/P12 cells and UTC rolls."""
    name = PRODUCTS[product][1]
    head = re.search(r'^\s*' + re.escape(station) + r'\s+' + name +
                     r'\s+GUIDANCE\s+(\d+)/(\d+)/(\d{4})\s+(\d{2})(\d{2}) UTC', text, re.M)
    if not head:
        raise ValueError('Requested station/product header absent')
    mo, dy, yr, hh, mm = map(int, head.groups())
    run = datetime(yr, mo, dy, hh, mm, tzinfo=timezone.utc)
    block = re.split(r'\n\s*[A-Z][A-Z0-9]{3}\s+GFS ', text[head.end():], maxsplit=1)[0]
    lines = {}
    for line in block.splitlines():
        key = line[:3].strip()
        if key in ('HR', 'UTC', 'TMP', 'DPT', 'CLD', 'WDR', 'WSP', 'PPO', 'P01', 'P06', 'P12', 'N/X', 'X/N'):
            lines[key] = line
    clock = lines.get('UTC', lines.get('HR', ''))
    if not clock or 'TMP' not in lines:
        raise ValueError('Missing forecast clock or temperatures')
    points, periods, extrema = [], [], []
    cursor = run.replace(minute=0)
    for column in range(4, len(clock.rstrip()), 3):
        hour = number(clock[column:column+3].strip(), 0, 23)
        if hour is None:
            raise ValueError('Invalid forecast hour')
        valid = cursor.replace(hour=hour)
        while valid <= cursor:
            valid += timedelta(days=1)
        # First projection is the next UTC hour or later, not the next day
        # when the requested hour is later than the run's hour.
        cursor = valid
        def cell(key):
            return lines.get(key, '')[column:column+3].strip()
        if product == 'MOS':
            # MAV maxima are printed in 00 UTC columns; minima in 12 UTC
            # columns. Never alternate split tokens: missing cells would
            # shift the meaning and date of subsequent values.
            field = 'N/X' if 'N/X' in lines else 'X/N'
            extreme = number(cell(field), -100, 150)
            if extreme is not None and hour in (0, 12):
                extrema.append({'kind': 'maximum' if hour == 0 else 'minimum',
                    'temperature_f': extreme, 'field': field,
                    'bulletin_valid_at': valid.isoformat()})
        point = {'valid_at': valid.isoformat(), 'temperature_f': number(cell('TMP'), -100, 150),
                 'dewpoint_f': number(cell('DPT'), -120, 110),
                 'cloud': cell('CLD') if cell('CLD') in ('CL','FW','SC','BK','OV') else None,
                 'wind_direction_degrees': None, 'wind_speed_kt': number(cell('WSP'), 0, 200)}
        wind = number(cell('WDR'), 0, 36)
        if wind is not None:
            point['wind_direction_degrees'] = wind * 10
        points.append(point)
        for key, hours in [('PPO', 0), ('P01', 1), ('P06', 6), ('P12', 12)]:
            probability = number(cell(key), 0, 100)
            if probability is not None:
                periods.append({'element': key, 'start': (valid-timedelta(hours=hours)).isoformat(),
                                'end': valid.isoformat(), 'hours': hours, 'probability': probability/100,
                                'event': 'precipitation occurrence, including traces' if key == 'PPO'
                                else 'at least 0.01 inch precipitation'})
    if not any(p['temperature_f'] is not None for p in points) and not any(x['kind']=='maximum' for x in extrema):
        raise ValueError('No valid temperatures')
    return {'station': station, 'product': product, 'issued_at': run.isoformat(),
            'points': points, 'precipitation': periods, 'extrema': extrema, 'blend_weight': 0,
            'provider': 'NOAA guidance via Iowa Environmental Mesonet'}


def retrieve(station, product, now):
    key = (station, product)
    cached = _CACHE.get(key)
    if cached and 0 <= (now-cached[0]).total_seconds() < (60 if cached[1]['status']=='unavailable' else 600):
        return copy.deepcopy(cached[1])
    result = {'station': station, 'product': product, 'blend_weight': 0,
              'status': 'unavailable', 'points': [], 'precipitation': []}
    try:
        for attempt in range(2):
            try:
                response = requests.get(ENDPOINT, params={'pil': PRODUCTS[product][0]+station[1:],
                    'matches': station, 'fmt': 'text', 'limit': 1}, timeout=(15, 25),
                    headers={'User-Agent': 'KalshiWeather/1.0 station-guidance'})
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt:
                    raise
        result = parse(response.text, station, product)
        result.update(retrieved_at=now.isoformat(), source_url=response.url)
        age = quality.age_minutes(result['issued_at'], now)
        result['status'] = 'ok' if age <= PRODUCTS[product][2]*60 else 'stale'
        result['message'] = 'Comparison only; not weighted in the blend.' if result['status']=='ok' else 'Old or future-dated issue; do not treat as current guidance.'
    except (requests.RequestException, ValueError, OverflowError):
        result['message'] = 'No usable bulletin returned for the exact settlement station.'
        result['retrieved_at'] = now.isoformat()
    _CACHE[key] = (now, result)
    return copy.deepcopy(result)


def for_window(source, start, end):
    result = {k: v for k, v in source.items() if k not in ('points', 'precipitation', 'extrema')}
    # The 00 UTC label denotes the preceding local daytime, not a 24-hour
    # settlement period. Supported stations use fixed local standard days.
    maxima = [x for x in source.get('extrema', []) if x['kind']=='maximum'
              and start <= datetime.fromisoformat(x['bulletin_valid_at'])-timedelta(hours=12) < end]
    result['mos_maximum'] = ({**maxima[0], 'period_start': (start+timedelta(hours=7)).isoformat(),
        'period_end': (start+timedelta(hours=19)).isoformat(),
        'period_definition': '07:00–19:00 local standard time; not the full settlement day',
        'definition_url': 'https://www.weather.gov/media/mdl/mdltpb05-05.pdf'} if len(maxima)==1 else None)
    points = [p for p in source['points'] if start <= datetime.fromisoformat(p['valid_at']) < end]
    periods = []
    for p in source['precipitation']:
        a, b = datetime.fromisoformat(p['start']), datetime.fromisoformat(p['end'])
        overlaps = (start <= b < end) if p['hours']==0 else (b > start and a < end)
        if overlaps:
            periods.append({**p, 'crosses_reporting_boundary': a < start or b > end})
    result.update(points=points, precipitation=periods)
    values = [p['temperature_f'] for p in points if p['temperature_f'] is not None]
    result['sampled_max_f'] = max(values) if values else None
    result['coverage_start'] = points[0]['valid_at'] if points else None
    result['coverage_end'] = points[-1]['valid_at'] if points else None
    result['coverage_note'] = 'Maximum of available forecast time samples, not a daily maximum forecast. May cover only part of the reporting day; no interpolation or extrapolation.'
    if source['status']=='ok' and not points:
        result['status'] = 'out_of_range'
        result['message'] = 'This bulletin does not reach the selected reporting day.'
    return result


def fetch(cities, day_offsets=(0, 1)):
    now = datetime.now(timezone.utc)
    stations = sorted({c['icao'] for c in cities})
    jobs = [(station, product) for station in stations for product in PRODUCTS]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = dict(zip(jobs, executor.map(lambda args: retrieve(*args, now), jobs)))
    out = {}
    for city in cities:
        out[city['name']] = {}
        for product in PRODUCTS:
            source = results[(city['icao'], product)]
            quality.record(product, city['name'], source['status'], source['message'],
                           retrieved_at=source.get('retrieved_at'), model_run_at=source.get('issued_at'))
        for offset in day_offsets:
            start, end = local_day_window(city['tz'], offset)
            out[city['name']][offset] = {product: for_window(results[(city['icao'], product)], start, end)
                                       for product in PRODUCTS}
    return out
