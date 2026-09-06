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


def split_ladders(ladder):
    """Separate overlapping bracket sets for the same event.

    Kalshi sometimes lists two complete ladders on one day -- observed on
    2026-09-05, Boston carried <70,70-71,...,>77 AND <80,80-81,...,>87
    simultaneously, presumably a re-strike as the forecast moved.

    Treated as one set they are neither exclusive nor exhaustive, and
    normalising across both produces a distribution spanning the union of
    the two: a 15-degree "market range" and a row of 1-cent brackets that
    look like enormous edges. A +98c edge is not an opportunity, it is this.

    Chains brackets where each one starts exactly where the last ended.
    -> list of coherent ladders, richest book first.
    """
    remaining = sorted(ladder,
                       key=lambda b: (b["lo"] if b["lo"] is not None else -9999))
    chains = []
    while remaining:
        chain = [remaining.pop(0)]
        progressed = True
        while progressed:
            progressed = False
            tail = chain[-1]
            if tail["hi"] is None:          # open-ended top closes the chain
                break
            for i, b in enumerate(remaining):
                if b["lo"] is not None and b["lo"] == tail["hi"] + 1:
                    chain.append(remaining.pop(i))
                    progressed = True
                    break
        chains.append(chain)

    def weight(ch):
        return sum((b.get("market") or {}).get("open_interest", 0) or 0
                   for b in ch)

    chains.sort(key=lambda ch: (-weight(ch), -len(ch)))
    return chains


def pick_ladder(ladder):
    """The one coherent ladder to trade, plus how many were discarded."""
    chains = split_ladders(ladder)
    if len(chains) <= 1:
        return ladder, 0
    return chains[0], len(chains) - 1


def implied_distribution(ladder):
    """Market-implied probabilities, normalized to sum to 1.

    Returns (probs_by_index, overround). Overround is the raw sum minus 1 --
    it's the house edge baked into the spreads, and comparing an un-normalized
    market to a normalized model makes every bracket look overpriced.
    """
    if coverage_gaps(ladder) or any(b["market"].get("mid") is None for b in ladder):
        return [None]*len(ladder), None
    raw = [b["market"]["mid"] / 100.0 for b in ladder]
    total = sum(raw)
    if total <= 0:
        return [None] * len(ladder), None
    return [r / total for r in raw], total - 1.0


def implied_quantiles(ladder, probs, qs=(0.1, 0.5, 0.9)):
    """Turn a bracket ladder back into a temperature forecast.

    A price is a forecast wearing different units. If the market prices the
    88-89 bracket at 33c and 90-91 at 21c, it is making a distributional
    statement about tomorrow's high, and it can be read back out as a median
    and an interval.

    That is the comparison a forecaster actually wants: "I say 91, the market
    says 88." Reading it as "the 90-91 contract is 22c" buries a 3-degree
    disagreement inside a price.

    Walks the cumulative distribution and interpolates within the bracket
    where each quantile falls. Open-ended end brackets get a nominal 3-degree
    width so the tails don't run away.
    """
    pts = []
    cum = 0.0
    for b, p in zip(ladder, probs):
        if p is None:
            continue
        lo, hi = b.get("lo"), b.get("hi")
        # Continuity: bracket [lo, hi] covers temperature lo-0.5 to hi+0.5.
        left = (hi + 0.5 - 3.0) if lo is None else lo - 0.5
        right = (lo - 0.5 + 3.0) if hi is None else hi + 0.5
        pts.append((left, right, cum, cum + p))
        cum += p
    if not pts or cum <= 0:
        return {}

    out = {}
    for q in qs:
        target = q * cum
        for left, right, c0, c1 in pts:
            if c1 >= target:
                frac = 0.0 if c1 == c0 else (target - c0) / (c1 - c0)
                out[q] = round(left + (right - left) * frac, 1)
                break
        else:
            out[q] = round(pts[-1][1], 1)
    return out


def check_arbitrage(ladder, fee_mult=.07):
    """Report only complete, disjoint ladders and net single-basket profit."""
    from .util import kalshi_fee_cents
    if len(ladder)<2 or coverage_gaps(ladder):return None
    if any(not b['market'].get('executable') for b in ladder):return None
    flags={}
    for side,payout in [('yes',100),('no',(len(ladder)-1)*100)]:
        prices=[b['market'].get(side+'_ask') for b in ladder]
        if any(p is None for p in prices):continue
        cost=sum(p+kalshi_fee_cents(p,fee_mult) for p in prices)
        if cost<payout:flags['buy_all_'+side]=round(payout-cost,2)
    return flags or None


def coverage_gaps(ladder):
    problems=[]
    if not ladder:return ['No brackets']
    if ladder[0]['lo'] is not None:problems.append('Missing lower tail')
    if ladder[-1]['hi'] is not None:problems.append('Missing upper tail')
    for a,b in zip(ladder,ladder[1:]):
        if a['hi'] is None or b['lo'] is None or b['lo']!=a['hi']+1:
            problems.append(f"{a['label']} -> {b['label']}")
    return problems
