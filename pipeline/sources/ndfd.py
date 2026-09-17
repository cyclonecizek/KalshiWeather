"""NDFD values and provenance, independent of operational blend membership."""
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
import time
import requests
from .. import quality
from ..util import stitch_pops

DETAILS = {}


def fetch(cities, cfg, kind, day_offsets=(0, 1), rho=.5):
    out = {}
    for city in cities:
        now = datetime.now(timezone.utc)
        tz = ZoneInfo(city['tz'])
        today = now.astimezone(tz).date()
        # Include the whole current day, even after a daytime period started.
        begin = datetime.combine(today, datetime.min.time(), tz).astimezone(timezone.utc)
        element = 'maxt' if kind == 'temperature' else 'pop12'
        root = None
        failure = None
        for attempt in range(3):
            try:
                response = requests.get(cfg['base'], params={
                    'whichClient': 'NDFDgen', 'lat': city['lat'], 'lon': city['lon'],
                    'product': 'time-series', element: element, 'Unit': 'e',
                    'begin': begin.strftime('%Y-%m-%dT%H:%M:%S'),
                    'end': (now+timedelta(days=4)).strftime('%Y-%m-%dT%H:%M:%S'),
                }, timeout=(10, 30))
                response.raise_for_status()
                root = ET.fromstring(response.text)
                if root.tag != 'dwml':
                    raise ValueError('Not a DWML forecast')
                break
            except (requests.RequestException, ET.ParseError, ValueError) as exc:
                failure = type(exc).__name__
                if attempt < 2:
                    time.sleep(attempt+1)
        retrieved = quality.now_iso() if root is not None and root.tag == 'dwml' else None
        valid = root is not None and root.tag == 'dwml'
        product_time = root.findtext('./head/product/creation-date') if valid else None
        layouts = {}
        if valid:
            for layout in root.iter('time-layout'):
                layouts[layout.findtext('layout-key')] = list(zip(
                    [e.text for e in layout.findall('start-valid-time')],
                    [e.text for e in layout.findall('end-valid-time')]))
        periods = []
        for node in root.iter('temperature' if kind == 'temperature' else 'probability-of-precipitation') if valid else []:
            if kind == 'temperature' and node.get('type') != 'maximum':
                continue
            for (start, end), value in zip(layouts.get(node.get('time-layout'), []), node.findall('value')):
                try:
                    number = float(value.text)
                    at = datetime.fromisoformat(start)
                    stop = datetime.fromisoformat(end)
                    if at.tzinfo is None or stop <= at:
                        continue
                    if not (-100 <= number <= 150 if kind == 'temperature' else 0 <= number <= 100):
                        continue
                except (ValueError, TypeError):
                    continue
                periods.append({'start': start, 'end': end, 'value': number})
        days = {}
        for off in day_offsets:
            target = today+timedelta(days=off)
            selected = [p for p in periods if datetime.fromisoformat(p['start']).astimezone(tz).date() == target]
            value = (selected[0]['value'] if selected else None) if kind == 'temperature' else stitch_pops([p['value']/100 for p in selected], rho=rho)
            status = 'ok' if value is not None else 'no_period' if valid else 'failed'
            DETAILS[(kind, city['name'], off)] = dict(value=value, status=status,
                retrieved_at=retrieved, attempted_at=quality.now_iso(), product_generated_at=product_time,
                issued_at=None, periods=selected,
                message={'ok': 'NWS guidance available', 'no_period': 'NWS returned no valid period for this day',
                         'failed': 'NWS download failed after 3 attempts'}[status])
            if value is not None:
                days[off] = value
        out[city['name']] = days
        quality.record('NDFD', city['name'], 'ok' if days else 'missing' if valid else 'failed',
                       failure if not valid else None, retrieved_at=retrieved,
                       product_generated_at=product_time)
        if not valid:
            print(f"NDFD {city['name']}: download failed after 3 attempts")
    return out
