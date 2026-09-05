"""What the station has already recorded today.

This is the layer that turns a forecasting problem into a much easier one.

By 3pm local a station has usually already set most of the day's high. That
observed maximum is a hard floor: every temperature bracket below it is worth
exactly zero, and the only live question is how much further it climbs in the
remaining hours. Markets stay open until 11:59pm local, so there is a long
window in which the settled answer is partly known and the morning price is
not yet reflecting it.

Rain is sharper still. Once 0.01" has fallen at the station, the contract is
effectively decided and anything trading below par is mispriced. And when the
day is three-quarters gone with nothing recorded, a morning forecast of 60%
is badly stale -- the right number is the conditional probability of rain in
the hours that remain.

Three sources, tried in order, because a single government endpoint is a
single point of failure. www.nws.noaa.gov already blocks GitHub runners
outright, which is how the MOS source died.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "kalshi-weather-board (research)"}
TRACE_MM = 0.0
THRESHOLD_MM = 0.254


def c_to_f(c):
    return None if c is None else c * 9.0 / 5.0 + 32.0


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def _from_iowa(icao, start_local, end_local, tz_name):
    """Iowa State Mesonet ASOS archive. Comma CSV, very tolerant of clients."""
    sid = icao[1:] if icao.startswith("K") and len(icao) == 4 else icao
    r = requests.get(
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
        params={
            "station": sid, "data": ["tmpf", "p01i"],
            "year1": start_local.year, "month1": start_local.month,
            "day1": start_local.day,
            "year2": end_local.year, "month2": end_local.month,
            "day2": end_local.day,
            "tz": tz_name, "format": "onlycomma", "latlon": "no",
            "missing": "M", "trace": "T", "report_type": "3",
        }, headers=UA, timeout=45)
    r.raise_for_status()

    temps, precip = [], []
    for row in csv.DictReader(io.StringIO(r.text)):
        stamp = (row.get("valid") or "")[:19]
        if not stamp or stamp[:10] < start_local.strftime("%Y-%m-%d"):
            continue
        t = row.get("tmpf")
        if t not in (None, "", "M"):
            try:
                temps.append(float(t))
            except ValueError:
                pass
        p = row.get("p01i")
        if p == "T":
            precip.append(TRACE_MM)          # trace settles NO on Kalshi
        elif p not in (None, "", "M"):
            try:
                precip.append(float(p) * 25.4)
            except ValueError:
                pass
    return temps, precip


def _from_awc(icao, hours=26):
    """Aviation Weather Center METAR API. Temps in C, precip in inches."""
    r = requests.get("https://aviationweather.gov/api/data/metar",
                     params={"ids": icao, "format": "json", "hours": hours},
                     headers=UA, timeout=45)
    r.raise_for_status()
    rows = r.json()
    out = []
    for m in rows if isinstance(rows, list) else []:
        ts = m.get("obsTime") or m.get("reportTime")
        if isinstance(ts, (int, float)):
            when = datetime.fromtimestamp(ts, timezone.utc)
        else:
            try:
                when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                continue
        out.append((when, c_to_f(m.get("temp")),
                    (m.get("precip") or 0) * 25.4 if m.get("precip") else None))
    return out


def _from_nws(icao, since_utc):
    """api.weather.gov observations. A different host from the blocked one."""
    r = requests.get(f"https://api.weather.gov/stations/{icao}/observations",
                     params={"start": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")},
                     headers=UA, timeout=45)
    r.raise_for_status()
    out = []
    for f in (r.json().get("features") or []):
        p = f.get("properties") or {}
        try:
            when = datetime.fromisoformat(
                str(p.get("timestamp")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        temp = (p.get("temperature") or {}).get("value")
        rain = (p.get("precipitationLastHour") or {}).get("value")
        out.append((when, c_to_f(temp), rain))
    return out


# ---------------------------------------------------------------------------

def fetch(cities, day_offsets=(0,), cfg=None):
    """-> {city: {offset: {max_f, precip_mm, n_obs, source, elapsed}}}

    Only the current local day can have observations, so offset 1 is skipped.
    """
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        return {}

    out = {}
    now = datetime.now(timezone.utc)

    for c in cities:
        tz = ZoneInfo(c["tz"])
        now_local = now.astimezone(tz)
        start_local = now_local.replace(hour=0, minute=0, second=0,
                                        microsecond=0)
        if 0 not in day_offsets:
            continue

        temps = precip = None
        source = None
        for name, fn in (
            ("iowa", lambda: _from_iowa(c["icao"], start_local,
                                        start_local + timedelta(days=1),
                                        c["tz"])),
            ("awc", lambda: _split(_from_awc(c["icao"]), start_local, tz)),
            ("nws", lambda: _split(_from_nws(c["icao"],
                                             start_local.astimezone(timezone.utc)),
                                   start_local, tz)),
        ):
            try:
                temps, precip = fn()
                if temps or precip:
                    source = name
                    break
            except Exception:  # noqa: BLE001
                continue

        if source is None:
            continue

        elapsed = (now_local - start_local).total_seconds() / 86400.0
        out[c["name"]] = {0: {
            "max_f": round(max(temps), 1) if temps else None,
            "precip_mm": round(sum(precip), 3) if precip else 0.0,
            "wet": bool(precip and sum(precip) >= THRESHOLD_MM),
            "n_obs": len(temps),
            "source": source,
            "elapsed": round(min(1.0, max(0.0, elapsed)), 3),
            "local_hour": now_local.hour,
        }}

    got = sum(1 for v in out.values() if v[0]["max_f"] is not None)
    print(f"  observations: {len(out)} cities ({got} with temperature)")
    return out


def _split(rows, start_local, tz):
    temps, precip = [], []
    for when, tf, mm in rows:
        if when.astimezone(tz) < start_local:
            continue
        if tf is not None:
            temps.append(tf)
        if mm:
            precip.append(mm)
    return temps, precip


# ---------------------------------------------------------------------------
# applying observations
# ---------------------------------------------------------------------------

# Fraction of a day's remaining warming potential by local hour. Highs are
# normally set between 2pm and 5pm; after that the chance of exceeding what
# has already been recorded collapses.
_HEAT_LEFT = {
    0: 1.00, 1: 1.00, 2: 1.00, 3: 1.00, 4: 1.00, 5: 1.00,
    6: 0.99, 7: 0.98, 8: 0.96, 9: 0.92, 10: 0.86, 11: 0.78,
    12: 0.66, 13: 0.52, 14: 0.38, 15: 0.25, 16: 0.15, 17: 0.08,
    18: 0.04, 19: 0.02, 20: 0.01, 21: 0.005, 22: 0.002, 23: 0.001,
}


def heating_remaining(local_hour: int) -> float:
    return _HEAT_LEFT.get(int(local_hour) % 24, 0.5)


def condition_rain(p_forecast, obs, tolerance=0.02):
    """Daily rain probability, given what the gauge has already caught.

    Two cases:

      Already wet -> the event has happened. Not returned as 1.0, because
      settlement is The Weather Company rather than this station's METAR,
      so a small amount of basis risk survives.

      Still dry -> the forecast covered the whole day, but part of it is
      gone. Under a roughly constant hazard through the day,

          P(rain in remaining | none so far) = 1 - (1 - p)^f

      where f is the fraction of the day left. At 9pm with a morning
      forecast of 60%, that is about 13%, not 60%.
    """
    if not obs:
        return p_forecast, None
    if obs.get("wet"):
        return 1.0 - tolerance, "observed"
    f = max(0.0, 1.0 - float(obs.get("elapsed", 0.0)))
    if f >= 0.999 or p_forecast is None:
        return p_forecast, None
    adjusted = 1.0 - (1.0 - p_forecast) ** f
    return adjusted, "conditioned"
