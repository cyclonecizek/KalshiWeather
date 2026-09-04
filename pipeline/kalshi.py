"""Kalshi market data.

Read-only market endpoints need no key. Discovery is preferred over the
hardcoded tickers in cities.yml because Kalshi adds and renames city series.
"""

from __future__ import annotations

import re
import time

import requests

UA = {"User-Agent": "kalshi-rain-board/1.0"}


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
            return r.json()
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
            if m.get("yes_bid") is not None and m.get("yes_ask") is not None:
                continue
            t = m.get("ticker")
            if not t:
                continue
            try:
                full = self.market(t)
                for k in ("yes_bid", "yes_ask", "no_bid", "no_ask", "volume",
                          "open_interest", "last_price", "previous_price"):
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
        """Normalize a market into the numbers the board needs.

        yes_bid/yes_ask are in cents. `mid` is the fair reference price;
        `no_ask` is what you actually pay to take the NO side.
        """
        yb = market.get("yes_bid")
        ya = market.get("yes_ask")
        if yb is None and ya is None:
            # No book at all. last_price is stale but better than dropping
            # the contract from the ladder, which breaks the overround
            # normalisation for every other bracket in the event.
            lp = market.get("last_price") or market.get("previous_price")
            if lp is None:
                return None
            yb = ya = lp
        elif yb is None:
            yb = max(1, ya - 2)
        elif ya is None:
            ya = min(99, yb + 2)
        spread = ya - yb
        return {
            "ticker": market.get("ticker"),
            "title": market.get("title") or market.get("subtitle"),
            "yes_bid": yb,
            "yes_ask": ya,
            "no_ask": 100 - yb,
            "no_bid": 100 - ya,
            "mid": (yb + ya) / 2.0,
            "spread": spread,
            "volume": market.get("volume") or 0,
            "open_interest": market.get("open_interest") or 0,
            "close_time": market.get("close_time"),
            "expiration_time": market.get("expiration_time"),
        }


def _book_top(ob):
    """Best yes bid and yes ask from an orderbook payload.

    Kalshi books quote both sides in YES terms: the top of the `no` side at
    price p implies a yes ask of 100 - p.
    """
    def top(side):
        rows = ob.get(side) or []
        if not rows:
            return None
        try:
            return max(int(r[0]) for r in rows if r)
        except (ValueError, TypeError, IndexError):
            return None

    yes_bid = top("yes")
    no_bid = top("no")
    yes_ask = None if no_bid is None else 100 - no_bid
    return yes_bid, yes_ask


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
