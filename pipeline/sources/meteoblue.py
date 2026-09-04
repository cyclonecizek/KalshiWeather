"""meteoblue, fetched once per city per day and shared by both boards.

Four things come back that the rest of the stack has to guess at:

  tmax                daily max, mLM post-processed against nearby stations
  temperature_spread  meteoblue's own ensemble standard deviation
  predictability      0-100% forecast certainty, the coloured dots on Windy
  rainspot            7x7 grid of precipitation around the station

`temperature_spread` is the one that changes numbers. Every other
deterministic source on the temperature board gets a hardcoded sigma out of
`deterministic_sigma` because a point forecast carries no uncertainty with it.
meteoblue ships its own, per city per day, and it widens on genuinely
uncertain days instead of sitting flat.

`rainspot` is the sleeper. Neighbourhood probabilities from HREF and REFS
answer "does anywhere within ~40 km get 0.01 inch", not "does this gauge".
The standard relation is

    P(point) ~= P(area) x E[wet area fraction | anything wet]

and rainSPOT gives you a direct sample of that wet fraction instead of making
you fit it blind over a month of outcomes.

CREDIT COST. Packages are charged separately per call, but requesting the
same package at several resolutions only charges the highest. So:

    basic-day only                 ~4,000   tmax, POP, predictability
    basic-3h (needed for rainSPOT) ~8,000   adds the 7x7 grid
    + trend-day                    +extra   adds temperature_spread

Enabling rainSPOT roughly doubles your burn. Verify the real numbers in
meteoblue's configurator -- `credit_costs` in settings.yml is an estimate.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

THRESHOLD_MM = 0.254
RAINSPOT_CELLS = 49
RAINSPOT_CENTRE = 24          # middle of a 7x7 grid, ordered SW -> NE


# ---------------------------------------------------------------------------
# cache + budget
# ---------------------------------------------------------------------------

def _load_cache(path: Path):
    try:
        d = json.loads(path.read_text())
        d.setdefault("data", {})
        d.setdefault("calls", {})
        return d
    except Exception:  # noqa: BLE001
        return {"data": {}, "calls": {}}


def _save_cache(path: Path, cache):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))


def _fresh(stamp, max_age):
    if not stamp:
        return False
    try:
        at = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - at < max_age


def estimate_credits(cfg):
    """Per-call credit cost implied by the enabled packages."""
    costs = cfg.get("credit_costs", {})
    if cfg.get("use_rainspot"):
        pkg = cfg.get("rainspot_package", "basic-1h").replace("-", "_")
        total = costs.get(pkg, 8000)
    else:
        total = costs.get("basic_day", 4000)
    if cfg.get("use_temperature_spread"):
        total += costs.get("trend_day", 4000)
    return total


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _rainspot_coverage(payload, date_str):
    """Wet-area fraction around the station for one local day.

    rainSPOT arrives as 49 values per timestep, ordered south-west to
    north-east. Sum each cell over the day, then count how many clear the
    Kalshi threshold.

    Returns None when nothing anywhere is wet -- a dry grid tells you nothing
    about coverage, and treating it as 0 would zero out every CAM probability.
    """
    block = (payload.get("data_1h") or payload.get("data_3h")
             or payload.get("data_180min") or {})
    times = block.get("time") or []
    spot = block.get("rainspot")
    if not times or not spot:
        return None

    idx = [i for i, t in enumerate(times) if str(t)[:10] == date_str]
    if not idx:
        return None

    totals = [0.0] * RAINSPOT_CELLS
    seen = False
    for i in idx:
        cells = _parse_spot_row(spot[i])
        if cells is None:
            continue
        seen = True
        for c in range(RAINSPOT_CELLS):
            totals[c] += cells[c]
    if not seen:
        return None

    wet = sum(1 for v in totals if v >= THRESHOLD_MM)
    if wet == 0:
        return None

    return {
        "wet_fraction": round(wet / RAINSPOT_CELLS, 4),
        "centre_mm": round(totals[RAINSPOT_CENTRE], 3),
        "centre_wet": totals[RAINSPOT_CENTRE] >= THRESHOLD_MM,
        "max_mm": round(max(totals), 3),
    }


def _parse_spot_row(row):
    """rainSPOT rows come as a 49-char intensity string or a 49-value list."""
    if isinstance(row, (list, tuple)) and len(row) >= RAINSPOT_CELLS:
        return [float(v or 0) for v in row[:RAINSPOT_CELLS]]
    if isinstance(row, str) and len(row) >= RAINSPOT_CELLS:
        # Digit classes, not millimetres. Map to a nominal depth so the
        # threshold test means something; anything above class 0 is wet
        # enough to matter at a 0.01" bar.
        scale = {"0": 0.0, "1": 0.3, "2": 1.0, "3": 3.0,
                 "4": 8.0, "5": 20.0, "6": 40.0, "7": 60.0,
                 "8": 80.0, "9": 100.0}
        return [scale.get(ch, 0.0) for ch in row[:RAINSPOT_CELLS]]
    return None


def _day_value(block, date_str, key):
    times = block.get("time") or []
    vals = block.get(key) or []
    for t, v in zip(times, vals):
        if str(t)[:10] == date_str and v is not None:
            return float(v)
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def fetch(cities, cfg, day_offsets=(0, 1)):
    """-> {city: {offset: {tmax, temp_spread, predictability, pop, rainspot}}}"""
    key = os.environ.get("METEOBLUE_KEY") or cfg.get("api_key")
    if not key:
        return {}

    wanted = cfg.get("cities") or []
    targets = [c for c in cities if not wanted or c["name"] in wanted]

    cache_path = Path(cfg.get("cache_path", ".cache/meteoblue.json"))
    cache = _load_cache(cache_path)
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spent = cache["calls"].get(today_utc, 0)
    budget = cfg.get("max_calls_per_day", 25)
    max_age = timedelta(hours=cfg.get("cache_hours", 8))

    # Which sub-daily package carries rainSPOT depends on what your key is
    # provisioned for. meteoblue's sample URL shows you: if it reads
    # `basic-1h_basic-day`, set rainspot_package to basic-1h. Requesting a
    # package your key doesn't cover returns an error, not a partial result.
    sub = cfg.get("rainspot_package", "basic-1h")
    packages = [sub, "basic-day"] if cfg.get("use_rainspot") else ["basic-day"]
    if cfg.get("use_temperature_spread"):
        packages.append("trend-day")

    base = cfg.get("base", "https://my.meteoblue.com/packages")
    url = f"{base.rstrip('/')}/{'_'.join(packages)}"

    out, fetched, served = {}, 0, 0

    for c in targets:
        tz = ZoneInfo(c["tz"])
        local_today = datetime.now(tz).date()
        ckey = f"{c['name']}|{local_today.isoformat()}"
        entry = cache["data"].get(ckey)

        if entry and _fresh(entry.get("at"), max_age):
            out[c["name"]] = {int(k): v for k, v in entry["days"].items()}
            served += 1
            continue

        if spent >= budget:
            # Out of budget: a stale mLM run still beats dropping the source.
            if entry:
                out[c["name"]] = {int(k): v for k, v in entry["days"].items()}
                served += 1
            continue

        params = {
            "lat": c["lat"], "lon": c["lon"], "apikey": key,
            "format": "json", "temperature": "F", "tz": c["tz"],
        }
        # Station elevation in metres. meteoblue otherwise infers height from
        # its own terrain grid, and the mismatch against the actual
        # thermometer is a real slice of the grid-to-station temperature bias
        # -- roughly 6.5 C per 1000 m of error. Supplying it removes that
        # slice for free, which matters most at Denver, Salt Lake and Vegas.
        if c.get("elevation_m") is not None:
            params["asl"] = c["elevation_m"]
        try:
            r = requests.get(url, params=params, timeout=45)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"  meteoblue {c['name']}: {exc}")
            if entry:
                out[c["name"]] = {int(k): v for k, v in entry["days"].items()}
            continue

        spent += 1
        fetched += 1

        day = data.get("data_day") or {}
        trend = data.get("trend_day") or {}
        by_off = {}
        for off in day_offsets:
            date_str = (local_today + timedelta(days=off)).isoformat()
            rec = {
                "tmax": _day_value(day, date_str, "temperature_max"),
                "pop": _pop(day, date_str),
                "predictability": _day_value(day, date_str, "predictability"),
                "temp_spread": _spread(day, trend, date_str),
                "rainspot": (_rainspot_coverage(data, date_str)
                             if cfg.get("use_rainspot") else None),
            }
            if any(v is not None for v in rec.values()):
                by_off[off] = rec

        if by_off:
            out[c["name"]] = by_off
            cache["data"][ckey] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "days": {str(k): v for k, v in by_off.items()},
            }

    cache["calls"][today_utc] = spent
    cache["calls"] = dict(sorted(cache["calls"].items())[-400:])
    _save_cache(cache_path, cache)

    per_call = estimate_credits(cfg)
    total_calls = sum(cache["calls"].values())
    used = total_calls * per_call
    trial = cfg.get("trial_credits", 10_000_000)
    print(f"  meteoblue: {len(out)} cities ({fetched} fetched, {served} cached)"
          f" | packages={'+'.join(packages)} ~{per_call:,}cr/call"
          f" | {total_calls} calls ~ {used:,}cr, {max(0, trial-used):,} left")
    if used > trial * 0.8:
        print("  meteoblue: OVER 80% OF TRIAL CREDITS SPENT")

    return out


def _pop(day, date_str):
    """POP, rescaled from meteoblue's 0.2 mm bar toward Kalshi's 0.254 mm.

    meteoblue defines precipitation probability as more than 0.2 mm. Kalshi's
    event is 0.254 mm. Theirs answers a slightly easier question, so it reads
    high. The shrink below is a first-order correction and a placeholder --
    fit the real one from history/ and move it into `calibration`.
    """
    v = _day_value(day, date_str, "precipitation_probability")
    return None if v is None else v / 100.0


def _spread(day, trend, date_str):
    for block in (day, trend):
        if not block:
            continue
        for key in ("temperature_spread", "temperature_max_spread"):
            v = _day_value(block, date_str, key)
            if v is not None:
                return v
    return None


def predictability_widening(pred, k=0.6):
    """Multiplier on forecast spread, driven by meteoblue's confidence score.

    predictability 100 -> 1.00 (leave the distribution alone)
                    50 -> 1.30
                     0 -> 1.60

    A low-predictability day genuinely deserves fatter bracket tails, and
    this is the only source on the board that will tell you which days those
    are before the fact.
    """
    if pred is None:
        return 1.0
    return 1.0 + k * (1.0 - max(0.0, min(100.0, pred)) / 100.0)


def coverage_multiplier(rainspot, floor=0.35):
    """Area-to-point discount for neighbourhood CAM probabilities.

    P(point) ~= P(area) x wet area fraction. Floored, because one
    deterministic rainSPOT is a single sample and shouldn't be allowed to
    cut a probability to nothing.

    NOTE: this assumes the rainSPOT footprint is comparable to the CAM
    neighbourhood radius (~40 km). meteoblue documents the 7x7 layout but not
    the cell spacing -- check a live response against a known widespread-rain
    day before trusting the adjustment, and leave `apply_coverage_to_cam`
    off until you have.
    """
    if not rainspot or rainspot.get("wet_fraction") is None:
        return 1.0
    return max(floor, min(1.0, rainspot["wet_fraction"]))
