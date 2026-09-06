"""Kalshi market data.

Read-only market endpoints need no key. Discovery is preferred over the
hardcoded tickers in cities.yml because Kalshi adds and renames city series.
"""

from __future__ import annotations

import re
import math
from datetime import datetime, timezone
import time

import requests

UA = {"User-Agent": "kalshi-rain-board/1.0"}

# Bumped whenever the market payload handling changes. build.py prints it, so
# a log tells you immediately whether the running code understands the
# dollars-denominated schema rather than leaving you to infer it.
SCHEMA_VERSION = "2026-09-04-dollars"


class Kalshi:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update(UA)

    def _get(self, path, **params):
        for attempt in range(4):
            r = self.s.get(f"{self.base}{path}", params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            stamp = datetime.now(timezone.utc).isoformat()
            for market in data.get('markets', []) + ([data['market']] if 'market' in data else []):
                market['_retrieved_at'] = stamp
            return data
        r.raise_for_status()

    # -- discovery ---------------------------------------------------------

    def discover_series(self, prefix="KXRAIN"):
        """Every series under a prefix, classified by cadence.

        The ticker tells you almost nothing. A trailing M means monthly --
        KXRAINNYCM is "Monthly rain in New York" -- and there is no matching
        marker for daily. Titles and settlement sources vary per series:
        the same city can have three rain markets settling on NWS, on the
        USGS, and on a climatological report respectively.

        So classify, don't pattern-match, and never auto-pick a series.
        """
        out = {}
        cursor = None
        while True:
            page = self._get("/series", limit=200, cursor=cursor)
            for s in page.get("series", []):
                t = s.get("ticker", "")
                if not t.startswith(prefix):
                    continue
                title = s.get("title") or ""
                out[t] = {
                    "title": title,
                    "cadence": classify_cadence(t, title),
                    "fee_multiplier": s.get("fee_multiplier"),
                    "settlement_sources": [
                        x.get("name", "?") if isinstance(x, dict) else str(x)
                        for x in (s.get("settlement_sources") or [])
                    ],
                    "contract_terms_url": s.get("contract_terms_url"),
                }
            cursor = page.get("cursor")
            if not cursor:
                break
        return out

    # kept for callers that still expect the old name
    def discover_rain_series(self):
        return self.discover_series("KXRAIN")

    # -- quotes ------------------------------------------------------------

    def market(self, ticker: str):
        """Full detail for one market."""
        data = self._get(f"/markets/{ticker}")
        return data.get("market") or data

    def orderbook(self, ticker: str):
        """Raw book, as a last resort for prices."""
        data = self._get(f"/markets/{ticker}/orderbook")
        return data.get("orderbook") or data

    def hydrate(self, markets, limit=400):
        """Fill in prices the list endpoint left out.

        The /markets list response comes back with yes_bid, yes_ask and
        volume all null for these series -- it is a slim projection. Without
        this the whole board silently produces nothing: every quote is None,
        every ladder is empty, and zero cities get written.

        Tries the per-market endpoint first, then the orderbook.
        """
        fixed = 0
        for m in markets[:limit]:
            normalize(m)
            if m.get("yes_bid") is not None and m.get("yes_ask") is not None:
                continue
            t = m.get("ticker")
            if not t:
                continue
            try:
                full = normalize(self.market(t))
                for k in ("yes_bid", "yes_ask", "no_bid", "no_ask", "volume",
                          "open_interest", "last_price", "previous_price", "yes_ask_size_fp",
                          "yes_bid_size_fp", "_retrieved_at"):
                    if full.get(k) is not None:
                        m[k] = full[k]
            except Exception:  # noqa: BLE001
                pass
            if m.get("yes_bid") is None or m.get("yes_ask") is None:
                try:
                    ob = self.orderbook(t)
                    yb, ya = _book_top(ob)
                    if yb is not None:
                        m["yes_bid"] = yb
                    if ya is not None:
                        m["yes_ask"] = ya
                except Exception:  # noqa: BLE001
                    pass
            if m.get("yes_bid") is not None and m.get("yes_ask") is not None:
                fixed += 1
        return fixed

    def markets_for_series(self, series_ticker: str, status="open"):
        out, cursor = [], None
        while True:
            page = self._get(
                "/markets", series_ticker=series_ticker,
                status=status, limit=200, cursor=cursor,
            )
            out.extend(page.get("markets", []))
            cursor = page.get("cursor")
            if not cursor:
                break
        return out

    @staticmethod
    def quote(market: dict):
        """Only actual quotes are executable. Missing sides stay missing."""
        m = normalize(dict(market))
        yb, ya = m.get('yes_bid'), m.get('yes_ask')
        valid = lambda x: isinstance(x, (int, float)) and math.isfinite(x) and 0 <= x <= 100
        yb = yb if valid(yb) else None
        ya = ya if valid(ya) else None
        crossed = yb is not None and ya is not None and yb > ya
        if crossed:
            yb = ya = None
        spread = ya-yb if yb is not None and ya is not None else None
        return dict(ticker=m.get('ticker'),event_ticker=m.get('event_ticker'),
            title=m.get('title') or m.get('subtitle'),yes_bid=yb,yes_ask=ya,
            no_ask=None if yb is None else 100-yb,
            no_bid=None if ya is None else 100-ya,
            mid=(yb+ya)/2 if spread is not None else None,spread=spread,
            last_price=m.get('last_price'),price_source='live_book' if spread is not None else 'unavailable',
            executable=spread is not None,quote_error='crossed book' if crossed else None,
            yes_depth=_num(m.get('yes_ask_size_fp')),no_depth=_num(m.get('yes_bid_size_fp')),
            volume=m.get('volume') or 0,open_interest=m.get('open_interest') or 0,
            liquidity=m.get('volume') or m.get('open_interest') or 0,
            retrieved_at=m.get('_retrieved_at'),updated_at=m.get('updated_time'),
            close_time=m.get('close_time'),expiration_time=m.get('expiration_time'),
            status=m.get('status'),rules_primary=m.get('rules_primary'),
            rules_secondary=m.get('rules_secondary'))


_REPORTED = []


def _report_unparsed(market):
    """Print a market's actual field names the first couple of times a quote
    fails. Guessing at a schema from a silent None is the slowest possible
    way to debug this."""
    if len(_REPORTED) >= 2:
        return
    _REPORTED.append(1)
    keys = sorted(k for k in market if not k.startswith("_"))
    print(f"    [schema] no price parsed from {market.get('ticker')}")
    print(f"    [schema] fields present: {', '.join(keys)}")
    for k in keys:
        if "price" in k or "bid" in k or "ask" in k:
            print(f"    [schema]   {k} = {market[k]!r}")


def _cents(v):
    """Dollar string or number -> integer cents. '0.4200' -> 42."""
    if v is None or v == "":
        return None
    try:
        return round(float(v) * 100, 6) if math.isfinite(float(v)) else None
    except (TypeError, ValueError):
        return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v) if math.isfinite(float(v)) else None
    except (TypeError, ValueError):
        return None


def normalize(m: dict) -> dict:
    """Support current dollar strings and legacy cent fields, including zero."""
    for key in ('yes_bid','yes_ask','no_bid','no_ask','last_price','previous_price'):
        if m.get(key) is None:
            m[key] = _cents(m.get(key+'_dollars'))
        else:
            m[key] = _num(m[key])
    for dest,other in [('yes_bid','no_ask'),('yes_ask','no_bid'),('no_bid','yes_ask'),('no_ask','yes_bid')]:
        if m.get(dest) is None and m.get(other) is not None:
            m[dest] = 100-m[other]
    for key in ('volume','open_interest'):
        if m.get(key) is None:m[key]=_num(m.get(key+'_fp'))
    return m


def _levels(ob,side):
    book=ob.get('orderbook_fp') or ob.get('orderbook') or ob
    for key,scale in [(side+'_dollars',100),(side,1)]:
        rows=book.get(key)
        if rows:
            out=[]
            for row in rows:
                if len(row)<2:continue
                p,n=_num(row[0]),_num(row[1])
                if p is not None and n is not None and n>0 and 0<=p*scale<=100:
                    out.append((round(p*scale,6),n))
            return out
    return []


def book_depth(ob,side=None,price=None):
    """Depth at the requested executable price, never at an unrelated level."""
    def available(opposite,ask=None):
        rows=_levels(ob,opposite)
        if not rows:return None
        bid=max(p for p,n in rows)
        if ask is not None and abs(100-bid-ask)>1e-6:return None
        return sum(n for p,n in rows if p==bid)
    if side:return available('no' if side=='YES' else 'yes',price)
    return available('no'),available('yes')


def _book_top(ob):
    yes=_levels(ob,'yes');no=_levels(ob,'no')
    return (max((p for p,n in yes),default=None),
            100-max(p for p,n in no) if no else None)


_MONTHLY = re.compile(r"month", re.I)
_WEEKLY = re.compile(r"week(end|ly)", re.I)
_DAILY = re.compile(r"daily", re.I)


def classify_cadence(ticker: str, title: str) -> str:
    """-> 'daily' | 'monthly' | 'weekly' | 'special' | 'unknown'

    'unknown' is the honest answer for most of them and means: go look at the
    actual markets under this series before trading it.
    """
    if _MONTHLY.search(title) or ticker.endswith("M"):
        return "monthly"
    if _WEEKLY.search(title):
        return "weekly"
    if _DAILY.search(title):
        return "daily"
    if "SB" in ticker or "super bowl" in title.lower():
        return "special"
    return "unknown"


def effective_fee_rate(fee_multiplier, base=0.07):
    """Kalshi's fee coefficient, from the API's multiplier field.

    The API returns fee_multiplier=1 for these markets. That is a multiplier
    ON the standard quadratic fee, not the coefficient itself -- feeding 1
    into ceil(m * P * (1-P) * 100) gives 25c on a 50c contract instead of
    1.75c, which rejects every trade on the board as unprofitable.
    """
    try:
        m = float(fee_multiplier)
    except (TypeError, ValueError):
        return base
    # Anything >= 1 is a scaling factor; anything smaller is already a rate.
    return base * m if m >= 1.0 else m


def pick_city_market(markets, target_date: str, city_code: str):
    """Find one city's market inside a shared multi-city series.

    KXRAIN is a single series carrying ~44 markets, one per city, with the
    city as the ticker suffix: KXRAIN-26SEP04-TTN. Date and city both have
    to match, so this is not the same lookup as a per-city series.
    """
    from datetime import datetime

    stamp = datetime.strptime(target_date, "%Y-%m-%d").strftime("%y%b%d").upper()
    code = (city_code or "").upper()
    for m in markets:
        t = (m.get("ticker") or "").upper()
        if not t.endswith("-" + code):
            continue
        if stamp in t or (m.get("close_time") or "")[:10] >= target_date:
            if stamp in t:
                return m
    # fall back on close_time when the date stamp format differs
    for m in markets:
        t = (m.get("ticker") or "").upper()
        if t.endswith("-" + code) and (m.get("close_time") or "")[:10] == target_date:
            return m
    return None


def pick_daily_market(markets, target_date: str):
    """Find the single yes/no 'will it rain' market for a given local date.

    Kalshi encodes the date in the event ticker as YYMMMDD (e.g. 26SEP03).
    Falls back to matching close_time's date.
    """
    from datetime import datetime

    d = datetime.strptime(target_date, "%Y-%m-%d")
    stamp = d.strftime("%y%b%d").upper()

    for m in markets:
        ev = (m.get("event_ticker") or "") + (m.get("ticker") or "")
        if stamp in ev.upper():
            return m

    for m in markets:
        ct = m.get("close_time") or ""
        if ct[:10] == target_date:
            return m
    return None
