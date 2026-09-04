"""Score the board against what actually settled, and emit fitted config.

This is the piece that turns `history/` from a pile of files into the thing
that makes the board worth running. Every knob in settings.yml -- the
per-city temperature bias, the spread factor, the meteoblue POP offset, the
deterministic sigmas -- is a guess until this has run against real outcomes.

Three questions it answers, in order of how much they matter:

  1. Does the model beat the market? If the market's Brier score is lower
     than yours, nothing else on this page matters and you should not be
     trading. This is the question people skip.

  2. What is each source's standing bias? Emitted as paste-ready YAML.
     For temperature the per-city bias is the single largest lever on the
     board -- a 2F grid-to-station offset against 2F brackets is worth more
     than any model you could add.

  3. Is the spread right? An ensemble that is too narrow underprices the
     outer brackets, which is exactly where the cheap contracts live.

Usage:  python -m pipeline.score            (writes a report to stdout)
        Actions -> Score calibration        (writes it to the run summary)
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from .kalshi import Kalshi
from .util import expit, load_yaml, logit

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "docs" / "data" / "history"


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------

def settled_result(kal: Kalshi, ticker: str, cache: dict):
    """-> 1.0 if the market settled YES, 0.0 if NO, None if still open."""
    if ticker in cache:
        return cache[ticker]
    try:
        data = kal._get(f"/markets/{ticker}")
        m = data.get("market") or data
        res = (m.get("result") or "").lower()
    except Exception:  # noqa: BLE001
        res = ""
    val = {"yes": 1.0, "no": 0.0}.get(res)
    cache[ticker] = val
    return val


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def brier(pairs):
    """Mean squared error of probabilistic forecasts. Lower is better."""
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs, offset=0.0):
    if not pairs:
        return None
    tot = 0.0
    for p, y in pairs:
        q = expit(logit(p) + offset)
        q = min(max(q, 1e-6), 1 - 1e-6)
        tot -= y * math.log(q) + (1 - y) * math.log(1 - q)
    return tot / len(pairs)


def fit_offset(pairs, lo=-2.0, hi=2.0, step=0.02):
    """Logit shift that minimises log loss. Drops straight into calibration:."""
    if len(pairs) < 20:
        return None
    best, best_loss = 0.0, log_loss(pairs, 0.0)
    x = lo
    while x <= hi:
        l = log_loss(pairs, x)
        if l < best_loss:
            best, best_loss = x, l
        x += step
    return round(best, 3)


def reliability(pairs, bins=5):
    """Predicted vs observed frequency by bucket. The shape of the miss."""
    if not pairs:
        return []
    buckets = defaultdict(list)
    for p, y in pairs:
        buckets[min(bins - 1, int(p * bins))].append((p, y))
    out = []
    for b in sorted(buckets):
        vals = buckets[b]
        out.append({
            "range": f"{b/bins:.0%}-{(b+1)/bins:.0%}",
            "n": len(vals),
            "predicted": sum(p for p, _ in vals) / len(vals),
            "observed": sum(y for _, y in vals) / len(vals),
        })
    return out


# ---------------------------------------------------------------------------
# rain
# ---------------------------------------------------------------------------

def score_rain(kal, cache):
    """-> per-source pairs, market pairs, realised P&L of flagged trades."""
    snaps = sorted(HIST.glob("2*.json"))
    latest = {}                       # (city, date) -> day entry
    for f in snaps:
        try:
            board = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        if board.get("_sample"):
            continue
        for c in board.get("cities", []):
            for off, day in (c.get("days") or {}).items():
                key = (c["city"], day.get("date"))
                if key[1]:
                    latest[key] = (day, off)

    by_source = defaultdict(list)
    market_pairs, consensus_pairs = [], []
    trades = []

    for (city, date), (day, off) in sorted(latest.items()):
        mk = day.get("market") or {}
        ticker = mk.get("ticker")
        if not ticker:
            continue
        y = settled_result(kal, ticker, cache)
        if y is None:
            continue

        for src, p in (day.get("raw_models") or {}).items():
            if p is not None:
                by_source[src].append((p, y))
        if day.get("consensus") is not None:
            consensus_pairs.append((day["consensus"], y))
        if mk.get("mid") is not None:
            market_pairs.append((mk["mid"] / 100.0, y))

        e = day.get("edge")
        if e and e.get("flag") in ("high", "watch"):
            win = (y == 1.0) if e["side"] == "YES" else (y == 0.0)
            pnl = (100 - e["price"] - e.get("fee_cents", 2)) if win \
                else -(e["price"] + e.get("fee_cents", 2))
            trades.append({"city": city, "date": date, "side": e["side"],
                           "price": e["price"], "ev": e["ev_cents"],
                           "won": win, "pnl": pnl, "day": off})

    return by_source, market_pairs, consensus_pairs, trades


# ---------------------------------------------------------------------------
# temperature
# ---------------------------------------------------------------------------

def score_temp(kal, cache):
    """-> per-city observed-minus-predicted, PIT z-scores, trade results."""
    snaps = sorted(HIST.glob("temp-*.json"))
    latest = {}
    for f in snaps:
        try:
            board = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        if board.get("_sample"):
            continue
        for c in board.get("cities", []):
            for off, day in (c.get("days") or {}).items():
                key = (c["city"], day.get("date"))
                if key[1]:
                    latest[key] = (day, off)

    errors = defaultdict(list)        # city -> [observed - median]
    zs = []                           # standardised errors, for spread fitting
    trades = []

    for (city, date), (day, off) in sorted(latest.items()):
        ladder = day.get("ladder") or []
        dist = day.get("distribution") or {}
        if not ladder or not dist:
            continue

        # The settled bracket tells you the high to within its own width.
        observed = None
        for b in ladder:
            t = (b.get("market") or {}).get("ticker")
            if not t:
                continue
            y = settled_result(kal, t, cache)
            if y == 1.0:
                lo, hi = b.get("lo"), b.get("hi")
                if lo is not None and hi is not None:
                    observed = (lo + hi) / 2.0
                elif hi is not None:
                    observed = hi - 0.5      # open-ended low bracket
                elif lo is not None:
                    observed = lo + 0.5      # open-ended high bracket
                break
        if observed is None:
            continue

        med = dist.get("median")
        if med is not None:
            errors[city].append(observed - med)
            p10, p90 = dist.get("p10"), dist.get("p90")
            if p10 is not None and p90 is not None and p90 > p10:
                sigma = (p90 - p10) / 2.5631      # 80% interval -> 1 sd
                if sigma > 0:
                    zs.append((observed - med) / sigma)

        for b in ladder:
            e = b.get("edge")
            t = (b.get("market") or {}).get("ticker")
            if not e or e.get("flag") not in ("high", "watch") or not t:
                continue
            y = settled_result(kal, t, cache)
            if y is None:
                continue
            win = (y == 1.0) if e["side"] == "YES" else (y == 0.0)
            pnl = (100 - e["price"] - e.get("fee_cents", 2)) if win \
                else -(e["price"] + e.get("fee_cents", 2))
            trades.append({"city": city, "date": date, "bracket": b.get("label"),
                           "side": e["side"], "price": e["price"],
                           "ev": e["ev_cents"], "won": win, "pnl": pnl})

    return errors, zs, trades


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def main():
    if not HIST.exists():
        print("No history/ yet. Run the board for a few weeks first.")
        return 0

    settings = load_yaml(ROOT / "config" / "settings.yml")
    kal = Kalshi(settings["sources"]["kalshi"]["base"])
    cache = {}
    L = []

    def w(s=""):
        L.append(s)

    w("# Calibration report")
    w()

    # ---------------- rain ----------------
    by_source, market_pairs, consensus_pairs, rain_trades = score_rain(kal, cache)
    w("## Rain")
    w()
    if not consensus_pairs:
        w("No settled rain forecasts yet.")
    else:
        mb = brier(market_pairs)
        cb = brier(consensus_pairs)
        w(f"Settled forecasts scored: **{len(consensus_pairs)}**")
        w()
        w("| | Brier | vs market |")
        w("|---|---|---|")
        w(f"| Market mid | {mb:.4f} | — |")
        w(f"| **Model consensus** | **{cb:.4f}** | "
          f"{'BETTER' if cb < mb else 'worse'} by {abs(cb-mb):.4f} |")
        w()
        if cb >= mb:
            w("> The market is scoring at least as well as the model. Until "
              "that reverses, the flagged trades are noise and the right "
              "position size is zero.")
            w()
        w("| Source | n | Brier | fitted logit offset |")
        w("|---|---|---|---|")
        for src in sorted(by_source, key=lambda s: brier(by_source[s]) or 9):
            pairs = by_source[src]
            off = fit_offset(pairs)
            w(f"| {src} | {len(pairs)} | {brier(pairs):.4f} | "
              f"{'—' if off is None else f'{off:+.3f}'} |")
        w()
        w("Paste into `calibration:` in settings.yml (only where n >= 20 — "
          "a smaller sample fits noise):")
        w()
        w("```yaml")
        w("calibration:")
        for src in sorted(by_source):
            off = fit_offset(by_source[src])
            if off is not None:
                w(f"  {src}: {off}")
        w("```")
        w()
        worst = max(by_source, key=lambda s: brier(by_source[s]) or 0) \
            if by_source else None
        if worst:
            w(f"Reliability for {worst} (its worst-scoring source):")
            w()
            w("| predicted | n | mean predicted | observed |")
            w("|---|---|---|---|")
            for r in reliability(by_source[worst]):
                w(f"| {r['range']} | {r['n']} | {r['predicted']:.1%} | "
                  f"{r['observed']:.1%} |")
            w()

    # ---------------- temperature ----------------
    errors, zs, temp_trades = score_temp(kal, cache)
    w("## Temperature")
    w()
    if not errors:
        w("No settled temperature forecasts yet.")
    else:
        n = sum(len(v) for v in errors.values())
        w(f"Settled days scored: **{n}** across {len(errors)} cities")
        w()
        w("| City | n | mean error (F) | sd |")
        w("|---|---|---|---|")
        for city in sorted(errors, key=lambda c: -abs(_mean(errors[c]))):
            v = errors[city]
            sd = statistics.pstdev(v) if len(v) > 1 else 0.0
            w(f"| {city} | {len(v)} | {_mean(v):+.2f} | {sd:.2f} |")
        w()
        w("Mean error is observed minus forecast, so it drops straight into "
          "`temperature.bias` with the same sign. Only cities with n >= 20 "
          "are listed below.")
        w()
        w("One caveat on precision: the settled high is only known to the "
          "width of the winning bracket, so each observation carries about "
          "+/- 1F of quantisation noise. That averages out, but it means a "
          "20-day sample resolves bias to maybe half a degree, not better. "
          "Wait for 60 days before trusting a value under 1F.")
        w()
        w("```yaml")
        w("temperature:")
        w("  bias:")
        for city in sorted(errors):
            if len(errors[city]) >= 20:
                w(f"    {city}: {_mean(errors[city]):.2f}")
        w("```")
        w()
        if len(zs) >= 20:
            spread = statistics.pstdev(zs)
            cur = settings["temperature"].get("spread_factor", 1.0)
            w(f"Standardised error spread: **{spread:.3f}** "
              f"(1.000 means the distribution width is right).")
            w()
            if spread > 1.05:
                w(f"> Too narrow — the outer brackets are underpriced. "
                  f"Set `spread_factor: {cur * spread:.2f}`.")
            elif spread < 0.95:
                w(f"> Too wide — you are overpaying for the tails. "
                  f"Set `spread_factor: {cur * spread:.2f}`.")
            else:
                w("> Width is about right. Leave `spread_factor` alone.")
            w()

    # ---------------- realised trades ----------------
    w("## Would the flagged trades have made money?")
    w()
    all_trades = ([dict(t, kind="rain") for t in rain_trades] +
                  [dict(t, kind="temp") for t in temp_trades])
    if not all_trades:
        w("No flagged trades have settled yet.")
    else:
        pnl = sum(t["pnl"] for t in all_trades)
        wins = sum(1 for t in all_trades if t["won"])
        exp = sum(t["ev"] for t in all_trades)
        w(f"- Flagged and settled: **{len(all_trades)}** "
          f"({wins} won, {len(all_trades)-wins} lost)")
        w(f"- Realised: **{pnl:+.0f}¢** per 1 contract "
          f"({pnl/len(all_trades):+.2f}¢ average)")
        w(f"- Predicted: {exp:+.0f}¢ ({exp/len(all_trades):+.2f}¢ average)")
        w()
        if exp > 0 and pnl < exp * 0.3:
            w("> Realised is far below predicted. That gap is the board's "
              "overconfidence, not variance — fit the offsets above before "
              "sizing anything up.")
            w()
        w("A sample under ~100 settled trades tells you very little either "
          "way. Resist reading a good week as a signal.")
        w()

    report = "\n".join(L)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report + "\n")
    return 0


def _mean(v):
    return sum(v) / len(v) if v else 0.0


if __name__ == "__main__":
    sys.exit(main())
