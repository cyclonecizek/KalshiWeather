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

    def discover_rain_series(self):
        """Every series whose ticker looks like a daily-rain city market.

        Returns {ticker: {title, fee_multiplier, settlement_sources}}.
        """
        out = {}
        cursor = None
        while True:
            page = self._get("/series", limit=200, cursor=cursor)
            for s in page.get("series", []):
                t = s.get("ticker", "")
                if re.fullmatch(r"KXRAIN[A-Z]{2,5}", t):
                    out[t] = {
                        "title": s.get("title"),
                        "fee_multiplier": s.get("fee_multiplier"),
                        "settlement_sources": s.get("settlement_sources", []),
                        "contract_terms_url": s.get("contract_terms_url"),
                    }
            cursor = page.get("cursor")
            if not cursor:
                break
        return out

    # -- quotes ------------------------------------------------------------

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
        if yb is None or ya is None:
            return None
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
