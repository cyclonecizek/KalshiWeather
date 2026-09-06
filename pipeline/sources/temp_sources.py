"""Daily-high-temperature guidance.

The target is the NWS Daily Climate Report high: the maximum observation at
the settlement station over the local calendar day, printed as a whole number
of degrees Fahrenheit. Two systematic gaps between that and any model output:

  * GRID vs STATION. Models give 2m temperature on a grid cell. The climate
    report gives a specific thermometer. The standing difference is stable per
    station and is what `bias` in settings.yml exists to remove.

  * SAMPLING. The report takes the max over observations sampled every few
    minutes; hourly model output takes the max over 24 samples. Hourly output
    therefore runs slightly cool, typically a few tenths, more on a sharp
    frontal-passage day. This folds into the same bias term.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from ..tempdist import c_to_f


def _daily_max_per_member(hourly, date_str):
    """-> list of daily max values, one per ensemble member."""
    times = hourly.get("time") or []
    idx = [i for i, t in enumerate(times) if t[:10] == date_str]
    if not idx:
        return []
    keys = sorted(k for k in hourly
                  if k.startswith("temperature_2m") and isinstance(hourly[k], list))
    out = []
    for k in keys:
        vals = [hourly[k][i] for i in idx if hourly[k][i] is not None]
        if vals:
            out.append(max(vals))
    return out


def fetch_openmeteo(cities, cfg, day_offsets=(0, 1)):
    from . import hourly
    data = hourly.fetch(cities, cfg, day_offsets)
    return {m: {c: {off: d['maxima'] for off, d in days.items()}
                for c, days in by_city.items()} for m, by_city in data.items()}


# ---------------------------------------------------------------------------
# NDFD MaxT (deterministic)
# ---------------------------------------------------------------------------

def fetch_ndfd_maxt(cities, cfg, day_offsets=(0, 1)):
    """-> {city: {offset: degF}}"""
    from xml.etree import ElementTree as ET

    now = datetime.utcnow()
    out = {}
    for c in cities:
        try:
            r = requests.get(cfg["base"], params={
                "whichClient": "NDFDgen", "lat": c["lat"], "lon": c["lon"],
                "product": "time-series", "maxt": "maxt", "Unit": "e",
                "begin": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "end": (now + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%S"),
            }, timeout=45)
            r.raise_for_status()
            root = ET.fromstring(r.text)
        except Exception as exc:  # noqa: BLE001
            print(f"  ndfd maxt {c['name']}: {exc}")
            continue

        layouts = {}
        for lay in root.iter("time-layout"):
            layouts[lay.findtext("layout-key")] = [
                datetime.fromisoformat(e.text)
                for e in lay.findall("start-valid-time") if e.text
            ]

        pairs = []
        for node in root.iter("temperature"):
            if node.get("type") != "maximum":
                continue
            times = layouts.get(node.get("time-layout"), [])
            vals = [int(e.text) if e.text else None for e in node.findall("value")]
            pairs = [(t, v) for t, v in zip(times, vals) if v is not None]
            break

        tz = ZoneInfo(c["tz"])
        today = datetime.now(tz).date()
        by_off = {}
        for off in day_offsets:
            target = today + timedelta(days=off)
            for t, v in pairs:
                if t.astimezone(tz).date() == target:
                    by_off[off] = float(v)
                    break
        out[c["name"]] = by_off

    print(f"  ndfd maxt: {len(out)} cities")
    return out


# ---------------------------------------------------------------------------
# GFS MOS max temperature
# ---------------------------------------------------------------------------

def fetch_mos_maxt(cities, cfg, day_offsets=(0, 1)):
    """MAV bulletins print X/N -- daytime max and overnight min, alternating.

    -> {city: {offset: degF}}
    """
    import re

    if not cfg.get("enabled", True):
        return {}

    out = {}
    for c in cities:
        try:
            r = requests.get(cfg["mav"], params={"sta": c["icao"]},
                             headers={"User-Agent": (
                                 "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                                 "10_15_7) AppleWebKit/537.36 (KHTML, like "
                                 "Gecko) Chrome/125.0 Safari/537.36")},
                             timeout=45)
            r.raise_for_status()
            text = r.text
        except Exception as exc:  # noqa: BLE001
            print(f"  mos maxt {c['icao']}: {exc}")
            continue

        head = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{4})\s+UTC", text)
        xn = re.search(r"^\s*X/N\s+(.*)$", text, re.M)
        if not head or not xn:
            continue

        vals = [int(t) for t in xn.group(1).split() if _is_int(t)]
        if not vals:
            continue

        # A 00Z or 12Z run prints max/min alternating; which comes first
        # depends on the cycle. A 12Z run leads with the day's max.
        cycle = int(head.group(4)[:2])
        maxes = vals[0::2] if cycle in (6, 12) else vals[1::2]

        by_off = {}
        for off in day_offsets:
            if off < len(maxes):
                by_off[off] = float(maxes[off])
        out[c["name"]] = by_off

    print(f"  mos maxt: {len(out)} cities")
    return out


def _is_int(t):
    try:
        int(t)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# meteoblue mLM (optional, paid key)
# ---------------------------------------------------------------------------

# meteoblue lives in pipeline/sources/meteoblue.py -- it feeds BOTH boards
# from a single cached call, so it can't sit in a temperature-only module.
