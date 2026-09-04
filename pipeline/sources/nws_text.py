"""The two NWS sources that need no GRIB: NDFD PoP12 and GFS MOS.

NDFD is the forecaster-edited grid. Treat it as a human's opinion of NBM
rather than a separate model -- that's why it shares a family with NBM in
settings.yml.

GFS MOS (MAV) is raw statistical guidance off the GFS. It's the weakest of
the five but it's the only one with no human and no blend in the loop, so it
occasionally disagrees with everything else in a useful way. NAM MOS (MET)
retires 2026-10-06; don't add it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests

from ..util import stitch_pops

# www.nws.noaa.gov/cgi-bin returned 403 for every station from a GitHub
# runner. That is usually user-agent filtering, sometimes a datacentre IP
# block -- a browser UA is the cheap thing to try before giving up.
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/125.0 Safari/537.36")}


# ---------------------------------------------------------------------------
# NDFD
# ---------------------------------------------------------------------------

def fetch_ndfd(cities, cfg, rho=0.5, day_offsets=(0, 1)):
    """PoP12 grids -> daily probability. -> {city: {offset: prob}}"""
    base = cfg["base"]
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=4)

    out = {}
    # The multi-point form takes a listLatLon, but the per-point form gives
    # cleaner XML and 23 calls is nothing. One at a time.
    for c in cities:
        try:
            r = requests.get(
                base,
                params={
                    "whichClient": "NDFDgen",
                    "lat": c["lat"],
                    "lon": c["lon"],
                    "product": "time-series",
                    "pop12": "pop12",
                    "Unit": "e",
                    "begin": now.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                timeout=45,
            )
            r.raise_for_status()
            pops = _parse_dwml_pop(r.text)
        except Exception as exc:  # noqa: BLE001
            print(f"  ndfd {c['name']}: {exc}")
            continue

        tz = ZoneInfo(c["tz"])
        by_offset = {}
        today_local = datetime.now(tz).date()
        for off in day_offsets:
            target = today_local + timedelta(days=off)
            # Each PoP12 block is stamped with the START of its 12-hour period.
            # A block belongs to the local day its start falls in.
            vals = [
                v / 100.0 for (t, v) in pops
                if t.astimezone(tz).date() == target
            ]
            p = stitch_pops(vals, rho=rho)
            if p is not None:
                by_offset[off] = p
        out[c["name"]] = by_offset

    print(f"  ndfd: {len(out)} cities")
    return out


def _parse_dwml_pop(xml_text):
    """-> [(datetime_utc, pop_percent), ...]"""
    root = ET.fromstring(xml_text)

    layouts = {}
    for lay in root.iter("time-layout"):
        key = lay.findtext("layout-key")
        starts = [
            datetime.fromisoformat(e.text)
            for e in lay.findall("start-valid-time")
            if e.text
        ]
        layouts[key] = starts

    for node in root.iter("probability-of-precipitation"):
        key = node.get("time-layout")
        times = layouts.get(key, [])
        vals = []
        for e in node.findall("value"):
            vals.append(int(e.text) if e.text not in (None, "") else None)
        return [
            (t.astimezone(timezone.utc), v)
            for t, v in zip(times, vals) if v is not None
        ]
    return []


# ---------------------------------------------------------------------------
# GFS MOS (MAV)
# ---------------------------------------------------------------------------

_P06_RE = re.compile(r"^\s*P06\s+(.*)$", re.M)
_HR_RE = re.compile(r"^\s*HR\s+(.*)$", re.M)


def fetch_mos(cities, cfg, rho=0.5, day_offsets=(0, 1)):
    """-> {city: {offset: prob}}"""
    if not cfg.get("enabled", True):
        return {}

    out = {}
    for c in cities:
        try:
            r = requests.get(cfg["mav"], params={"sta": c["icao"]},
                             headers=UA, timeout=45)
            r.raise_for_status()
            parsed = _parse_mav(r.text)
        except Exception as exc:  # noqa: BLE001
            print(f"  mos {c['icao']}: {exc}")
            continue

        if not parsed:
            continue
        run_utc, pairs = parsed
        tz = ZoneInfo(c["tz"])
        today_local = datetime.now(tz).date()

        by_offset = {}
        for off in day_offsets:
            target = today_local + timedelta(days=off)
            # P06 is valid for the 6 hours ENDING at the stamped hour, so
            # attribute it to the local day containing the period's midpoint.
            vals = [
                p / 100.0 for (valid, p) in pairs
                if (valid - timedelta(hours=3)).astimezone(tz).date() == target
            ]
            v = stitch_pops(vals, rho=rho)
            if v is not None:
                by_offset[off] = v
        out[c["name"]] = by_offset

    print(f"  mos: {len(out)} cities")
    return out


def _parse_mav(text):
    """Pull P06 out of a fixed-width MAV bulletin.

    Bulletins look like:
        KNYC   GFS MOS GUIDANCE    9/03/2026  1200 UTC
        HR     18 21 00 03 ...
        P06        12    25 ...
    P06 is printed only every other column (6-hourly on a 3-hourly grid), so
    columns are matched positionally, not by splitting on whitespace.
    """
    head = re.search(
        r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{4})\s+UTC", text
    )
    if not head:
        return None
    mo, dy, yr, hhmm = head.groups()
    run = datetime(int(yr), int(mo), int(dy), int(hhmm[:2]), tzinfo=timezone.utc)

    hr_m = _HR_RE.search(text)
    p6_m = _P06_RE.search(text)
    if not hr_m or not p6_m:
        return None

    hr_line, p6_line = hr_m.group(1), p6_m.group(1)

    hours, cols = [], []
    for i in range(0, len(hr_line) - 2, 3):
        tok = hr_line[i:i + 3].strip()
        if tok.isdigit():
            hours.append(int(tok))
            cols.append(i)

    pairs, cursor = [], run
    prev = None
    for h, i in zip(hours, cols):
        # walk the clock forward so day rollovers are handled
        while cursor.hour != h:
            cursor += timedelta(hours=1)
        if prev is not None and cursor <= prev:
            cursor += timedelta(days=1)
        prev = cursor

        tok = p6_line[i:i + 3].strip() if i < len(p6_line) else ""
        if tok.isdigit():
            pairs.append((cursor, int(tok)))

    return run, pairs
