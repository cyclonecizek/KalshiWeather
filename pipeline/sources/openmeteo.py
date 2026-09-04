"""Open-Meteo ensemble members -> exact daily rain probability.

Why this source carries real weight despite being a "free API":

  * It returns INDIVIDUAL MEMBERS, so we count the fraction whose local-day
    precipitation sum clears 0.254 mm. No inherited POP definition, no
    neighbourhood inflation, no 12-hour-block stitching guess.
  * Passing `timezone` makes the hourly series start at local midnight, which
    is exactly the Kalshi contract window.
  * ECMWF-ENS, GEFS, ICON, GEM and UKMO are genuinely independent of the
    NBM/NDFD/MOS/HREF family that everyone else on the exchange is staring at.

One request per model covers every city: Open-Meteo accepts comma-separated
coordinate lists and returns one object per location, in order.
"""

from __future__ import annotations

from datetime import datetime

import requests

from ..util import member_fraction


def _member_series(hourly: dict):
    """All per-member precipitation arrays in an hourly block.

    Open-Meteo names them `precipitation_member01` ... and puts the control
    run in plain `precipitation`.
    """
    keys = [k for k in hourly if k.startswith("precipitation")]
    keys.sort()
    return [hourly[k] for k in keys if isinstance(hourly[k], list)]


def _sum_local_day(times, values, date_str):
    """Sum hourly values whose local timestamp falls on `date_str`."""
    total = 0.0
    seen = False
    for t, v in zip(times, values):
        if t[:10] != date_str:
            continue
        seen = True
        if v is not None:
            total += v
    return total if seen else None


def fetch(cities, cfg, threshold_mm=0.254, day_offsets=(0, 1)):
    """-> {model_key: {city_name: {offset: prob}}}"""
    base = cfg["ensemble_base"]
    models = cfg["models"]

    lats = ",".join(f"{c['lat']:.4f}" for c in cities)
    lons = ",".join(f"{c['lon']:.4f}" for c in cities)

    results = {}
    for key, model_id in models.items():
        try:
            r = requests.get(
                base,
                params={
                    "latitude": lats,
                    "longitude": lons,
                    "hourly": "precipitation",
                    "models": model_id,
                    "forecast_days": 3,
                    # Per-location timezone isn't supported, so request UTC-
                    # naive local time per city below instead. See note.
                    "timezone": "auto",
                },
                timeout=240,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"  openmeteo {key}: {exc}")
            continue

        if isinstance(payload, dict):
            payload = [payload]

        per_city = {}
        for city, loc in zip(cities, payload):
            hourly = loc.get("hourly") or {}
            times = hourly.get("time") or []
            series = _member_series(hourly)
            if not times or not series:
                continue

            # `timezone=auto` resolves to the grid point's own zone, which for
            # these stations matches the settlement station's zone. Timestamps
            # come back local and naive, so a string date compare is correct.
            by_offset = {}
            for off in day_offsets:
                target = _target_date(times, off)
                if target is None:
                    continue
                totals = [_sum_local_day(times, s, target) for s in series]
                p = member_fraction(totals, threshold_mm)
                if p is not None:
                    by_offset[off] = p
            per_city[city["name"]] = by_offset

        results[key] = per_city
        print(f"  openmeteo {key}: {len(per_city)} cities")

    return results


def _target_date(times, offset):
    """Open-Meteo's series starts at 00:00 local today, so day N is index N."""
    if not times:
        return None
    day0 = datetime.strptime(times[0][:10], "%Y-%m-%d")
    dates = sorted({t[:10] for t in times})
    try:
        return dates[offset]
    except IndexError:
        return None
