"""Shared math and time helpers.

The single most important thing in this file is `local_day_window`. Kalshi's
day is the station's local calendar day. Every model is on UTC. A 24-hour QPF
field ending at 12Z is not the contract you are trading, and in summer the
difference is most of an afternoon of convection.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml


def load_yaml(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def local_day_window(tz_name: str, offset_days: int = 0):
    """UTC start/end of a station's local calendar day.

    offset_days=0 -> today at the station, 1 -> tomorrow.
    Returns tz-aware UTC datetimes. The window is [start, end).
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    day = (now_local + timedelta(days=offset_days)).date()
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def local_date_str(tz_name: str, offset_days: int = 0) -> str:
    tz = ZoneInfo(tz_name)
    return (datetime.now(tz) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def aligned_cycle(cycles, lag_hours: float, window_start,
                  now: datetime | None = None, max_back_hours: int = 30):
    """Newest available cycle at or before a window's start.

    NBM runs every hour, which means there is almost always a cycle sitting
    exactly on a station's local midnight. Using it turns the contract window
    into f000-f024, so a day tiles from two clean 12-hour accumulation
    records instead of being approximated by 6-18 and 18-30 offset by a
    couple of hours.

    Falls back to the newest available cycle before the window when no exact
    alignment exists (a 4-cycle-a-day model, say).
    """
    now = now or datetime.now(timezone.utc)
    latest_ok = now - timedelta(hours=lag_hours)
    best = None
    for back in range(0, max_back_hours + 1):
        cand = window_start - timedelta(hours=back)
        if cand.hour not in cycles:
            continue
        if cand > latest_ok:
            continue
        best = cand
        break
    if best is None:
        return None
    return best.strftime("%Y%m%d"), best.hour, best


def latest_cycle(cycles, lag_hours: float, now: datetime | None = None):
    """Most recent model cycle that should have finished posting.

    `lag_hours` is wall-clock delay from cycle time to data availability.
    Returns (date_yyyymmdd, cycle_hour).
    """
    now = now or datetime.now(timezone.utc)
    probe = now - timedelta(hours=lag_hours)
    for back in range(0, 3):
        d = probe - timedelta(days=back)
        for cc in sorted(cycles, reverse=True):
            cand = d.replace(hour=cc, minute=0, second=0, microsecond=0)
            if cand <= probe:
                return cand.strftime("%Y%m%d"), cc
    raise RuntimeError("no cycle found")


# --------------------------------------------------------------------------
# Probability
# --------------------------------------------------------------------------

def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def apply_calibration(p: float, offset: float) -> float:
    """Shift a probability in logit space. offset=0 is a no-op."""
    if offset == 0.0:
        return p
    return expit(logit(p) + offset)


def stitch_pops(pops, rho: float = 0.5) -> float:
    """Combine sub-daily POPs into one daily probability.

    Independence (1 - prod(1-p)) overstates it badly -- rain in the morning
    and rain in the afternoon are the same synoptic system most days.
    Pure max understates it. rho interpolates:

        rho = 0  ->  full independence
        rho = 1  ->  max(pops)
    """
    pops = [p for p in pops if p is not None]
    if not pops:
        return None
    if len(pops) == 1:
        return pops[0]
    indep = 1.0
    for p in pops:
        indep *= (1.0 - p)
    indep = 1.0 - indep
    return rho * max(pops) + (1.0 - rho) * indep


def member_fraction(member_totals, threshold) -> float:
    """Exact probability from ensemble members: what share cleared the bar.

    This is the honest way to get P(daily total >= 0.01") and it's why the
    Open-Meteo ensemble endpoint is worth the extra calls -- no one else's
    POP definition gets baked in.
    """
    vals = [v for v in member_totals if v is not None]
    if not vals:
        return None
    hits = sum(1 for v in vals if v >= threshold)
    # Laplace smoothing so a 31-member unanimous run isn't 0.000 or 1.000
    return (hits + 0.5) / (len(vals) + 1.0)


# --------------------------------------------------------------------------
# Kalshi economics
# --------------------------------------------------------------------------

def kalshi_fee_cents(price_cents: float, multiplier: float = 0.07) -> float:
    """Kalshi's quadratic taker fee, per contract, rounded up to the cent.

        fee = ceil(multiplier * C * P * (1 - P))

    Peaks at 1.75c for a 50c contract at the standard 0.07 multiplier and
    falls toward zero at the tails -- which is part of why cheap longshots
    can carry real edge even when the raw probability gap looks small.
    """
    p = price_cents / 100.0
    return math.ceil(multiplier * p * (1.0 - p) * 100.0)


def edge_for_side(p_model: float, price_cents: float, multiplier: float):
    """Expected value in cents of buying one contract at `price_cents`.

    p_model is the probability that THIS side pays out.
    """
    if p_model is None or price_cents is None or not math.isfinite(p_model) or price_cents <= 0 or price_cents >= 100:
        return None
    fee = kalshi_fee_cents(price_cents, multiplier)
    return p_model * 100.0 - price_cents - fee


def kelly_fraction(p_model: float, price_cents: float, cap: float = 0.25, multiplier: float = 0.07):
    """Fractional Kelly stake for a binary contract bought at price a.

        f* = (p - a) / (1 - a)

    Capped, because your probability estimate is a blend of correlated models
    with unfitted calibration offsets and full Kelly on that is not a plan.
    """
    if price_cents is None or p_model is None:
        return 0.0
    a = (price_cents + kalshi_fee_cents(price_cents, multiplier)) / 100.0
    if a >= 1.0 or p_model <= a:
        return 0.0
    return min((p_model - a) / (1.0 - a), 1.0) * cap
