"""Kalshi temperature brackets: parse them, and read the market's own
distribution back out of the prices.

A high-temperature event is a set of mutually exclusive, collectively
exhaustive brackets. That structure gives you two things rain markets don't:

  * The market's implied probabilities should sum to 1. They never do -- the
    sum of mids runs over 100 because every bracket carries a spread. That
    excess is the overround, and you have to strip it before comparing the
    market's shape to yours, or you will read a uniform 3-point overpricing
    as edge in every bracket at once.

  * Occasionally the book is genuinely incoherent: you can buy YES across
    every bracket for less than 100 cents total, or buy NO across every
    bracket for less than (n-1) x 100. That's arbitrage, not a forecast
    disagreement, and it doesn't care whether your model is any good.
"""

from __future__ import annotations

import math
import re


def parse_bracket(market: dict):
    """-> (lo_int, hi_int) in whole degrees F. None means open-ended.

    Prefers the API's structured strike fields; falls back to the title.
    """
    st = (market.get("strike_type") or "").lower()
    floor_s = market.get("floor_strike")
    cap_s = market.get("cap_strike")

    if st in ("greater", "greater_or_equal") and floor_s is not None:
        lo = math.ceil(floor_s) if st == "greater_or_equal" else math.floor(floor_s) + 1
        return lo, None
    if st in ("less", "less_or_equal") and cap_s is not None:
        hi = math.floor(cap_s) if st == "less_or_equal" else math.ceil(cap_s) - 1
        return None, hi
    if st == "between" and floor_s is not None and cap_s is not None:
        return math.ceil(floor_s), math.floor(cap_s)

    if floor_s is not None and cap_s is not None:
        return math.ceil(floor_s), math.floor(cap_s)
    if floor_s is not None:
        return math.ceil(floor_s), None
    if cap_s is not None:
        return None, math.floor(cap_s)

    return _parse_title(market.get("subtitle") or market.get("title") or "")


_RANGE = re.compile(r"(-?\d+)\s*°?\s*(?:to|-|–)\s*(-?\d+)")
_ABOVE = re.compile(r"(-?\d+)\s*°?\s*(?:or above|or higher|\+)", re.I)
_BELOW = re.compile(r"(-?\d+)\s*°?\s*(?:or below|or lower)", re.I)


def _parse_title(t: str):
    m = _RANGE.search(t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _ABOVE.search(t)
    if m:
        return int(m.group(1)), None
    m = _BELOW.search(t)
    if m:
        return None, int(m.group(1))
    return None, None


def build_ladder(markets, quote_fn):
    """-> sorted list of bracket dicts with quotes attached."""
    out = []
    for m in markets:
        lo, hi = parse_bracket(m)
        if lo is None and hi is None:
            continue
        q = quote_fn(m)
        if not q:
            continue
        out.append({
            "lo": lo, "hi": hi,
            "label": _label(lo, hi),
            "market": q,
        })
    out.sort(key=lambda b: (b["lo"] if b["lo"] is not None else -999))
    return out


def _label(lo, hi):
    if lo is None:
        return f"≤{hi}"
    if hi is None:
        return f"≥{lo}"
    if lo == hi:
        return f"{lo}"
    return f"{lo}–{hi}"


def implied_distribution(ladder):
    """Market-implied probabilities, normalized to sum to 1.

    Returns (probs_by_index, overround). Overround is the raw sum minus 1 --
    it's the house edge baked into the spreads, and comparing an un-normalized
    market to a normalized model makes every bracket look overpriced.
    """
    raw = [b["market"]["mid"] / 100.0 for b in ladder]
    total = sum(raw)
    if total <= 0:
        return [None] * len(ladder), None
    return [r / total for r in raw], total - 1.0


def check_arbitrage(ladder):
    """Coherence checks that don't depend on any forecast being right."""
    n = len(ladder)
    if n < 2:
        return None
    yes_cost = sum(b["market"]["yes_ask"] for b in ladder)
    no_cost = sum(b["market"]["no_ask"] for b in ladder)
    flags = {}
    if yes_cost < 100:
        flags["buy_all_yes"] = round(100 - yes_cost, 2)
    if no_cost < (n - 1) * 100:
        flags["buy_all_no"] = round((n - 1) * 100 - no_cost, 2)
    return flags or None


def coverage_gaps(ladder):
    """Holes or overlaps in the ladder -- usually means a parse went wrong."""
    problems = []
    for a, b in zip(ladder, ladder[1:]):
        if a["hi"] is None or b["lo"] is None:
            continue
        if b["lo"] != a["hi"] + 1:
            problems.append(f"{a['label']} -> {b['label']}")
    return problems
