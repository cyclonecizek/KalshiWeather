"""Build docs/data/board_temp.json.

Pipeline: ensemble members -> quantile curve per family -> Vincentized blend
-> bias and spread correction -> integrate over each Kalshi bracket -> compare
to the (overround-adjusted) market.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .brackets import (build_ladder, check_arbitrage, coverage_gaps,
                       implied_distribution)
from .kalshi import SCHEMA_VERSION, Kalshi
from .sources import meteoblue, temp_sources
from .tempdist import (Dist, adjust, blend_quantiles, members_to_quantiles,
                       normal_quantiles)
from .util import (edge_for_side, kalshi_fee_cents, kelly_fraction, load_yaml,
                   local_date_str)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DAY_OFFSETS = (0, 1)


def main():
    settings = load_yaml(ROOT / "config" / "settings.yml")
    cities = load_yaml(ROOT / "config" / "cities.yml")["cities"]
    tcfg = settings["temperature"]
    src = settings["sources"]

    errors = []

    def run(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  {name} FAILED: {exc}")
            traceback.print_exc()
            errors.append(f"{name}: {exc}")
            return None

    # ---- guidance ------------------------------------------------------
    members = run("openmeteo-temp", lambda: temp_sources.fetch_openmeteo(
        cities, src["openmeteo"], DAY_OFFSETS)) or {}

    point = {}
    v = run("ndfd-maxt", lambda: temp_sources.fetch_ndfd_maxt(
        cities, src["ndfd"], DAY_OFFSETS))
    if v:
        point["NDFD"] = v
    if src["mos"].get("enabled", True):
        v = run("mos-maxt", lambda: temp_sources.fetch_mos_maxt(
            cities, src["mos"], DAY_OFFSETS))
        if v:
            point["MOS"] = v
    else:
        print("  mos: disabled in settings")
    mb_cfg = tcfg["sources"]["meteoblue"]
    mb = run("meteoblue", lambda: meteoblue.fetch(cities, mb_cfg, DAY_OFFSETS))
    if mb:
        point["METEOBLUE"] = {
            city: {off: rec["tmax"] for off, rec in days.items()
                   if rec.get("tmax") is not None}
            for city, days in mb.items()
        }
    else:
        print("  meteoblue: no key set, skipping "
              "(METEOBLUE_KEY env / settings temperature.sources.meteoblue.api_key)")

    # ---- market --------------------------------------------------------
    kal = Kalshi(src["kalshi"]["base"])
    series_map = {}
    for pref in tcfg["series_prefixes"]:
        series_map.update(run(f"kalshi-{pref}",
                              lambda p=pref: kal.discover_series(p)) or {})
    print(f"  kalshi: {len(series_map)} temperature series")

    rows = []
    for c in cities:
        ticker = c.get("series_high")
        if not ticker:
            continue
        if ticker not in series_map:
            print(f"  {c['name']}: {ticker} not in discovered series")
            continue
        if series_map[ticker].get("cadence") in ("monthly", "weekly", "special"):
            print(f"  {c['name']}: {ticker} is not a daily contract, skipping")
            continue
        meta = series_map[ticker]
        try:
            markets = kal.markets_for_series(ticker)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{c['name']} markets: {exc}")
            continue

        days = {}
        for off in DAY_OFFSETS:
            date_str = local_date_str(c["tz"], off)
            day_markets = [m for m in markets
                           if _is_for_date(m, date_str)]
            if len(day_markets) < 2:
                continue
            # The list endpoint returns these with null prices. Without this
            # every quote is None and the board writes zero cities.
            kal.hydrate(day_markets)

            dist, diag = build_distribution(
                c, off, members, point, tcfg, errors,
                extras=(mb.get(c["name"]) or {}).get(off) or {})
            ladder = build_ladder(day_markets, Kalshi.quote)
            if not ladder:
                print(f"  {c['name']} {date_str}: {len(day_markets)} markets "
                      f"but no usable quotes")
                continue

            implied, overround = implied_distribution(ladder)
            from .kalshi import effective_fee_rate
            fee_mult = effective_fee_rate(meta.get("fee_multiplier"),
                                          tcfg["edge"]["fee_multiplier"])

            for i, b in enumerate(ladder):
                b["implied"] = None if implied[i] is None else round(implied[i], 4)
                if dist is None:
                    continue
                p = dist.prob_between(b["lo"], b["hi"])
                b["model_p"] = round(p, 4)
                b["edge"] = evaluate_bracket(p, b["market"], fee_mult, tcfg)

            days[str(off)] = {
                "date": date_str,
                "ladder": ladder,
                "overround": None if overround is None else round(overround, 4),
                "arbitrage": check_arbitrage(ladder),
                "gaps": coverage_gaps(ladder),
                "distribution": None if dist is None else {
                    "median": round(dist.median(), 2),
                    "p10": round(dist.quantile(0.10), 2),
                    "p90": round(dist.quantile(0.90), 2),
                    "quantiles": [round(x, 2) for x in dist.v],
                },
                "diagnostics": diag,
            }

        if days:
            rows.append({
                "city": c["name"], "series": ticker,
                "station": c.get("station"), "icao": c.get("icao"),
                "tz": c["tz"], "verified": c.get("verified", False),
                "days": days,
            })

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "temperature",
        "families": {k: {"label": v["label"], "weight": v["weight"],
                         "members": v["members"]}
                     for k, v in tcfg["families"].items()},
        "spread_factor": tcfg.get("spread_factor", 1.0),
        "meteoblue_enabled": "METEOBLUE" in point,
        "meteoblue_spread_used": bool(tcfg.get("sources", {})
                                      .get("meteoblue", {})
                                      .get("use_temperature_spread", False)),
        "meteoblue_published": bool(tcfg.get("sources", {})
                                    .get("meteoblue", {})
                                    .get("publish_values", False)),
        "errors": errors,
        "cities": rows,
    }

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "board_temp.json").write_text(json.dumps(board, indent=1))
    hist = DATA / "history"
    hist.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    (hist / f"temp-{stamp}.json").write_text(json.dumps(board))

    print(f"wrote {len(rows)} cities, {len(errors)} errors")
    return 0 if rows else 1


# ---------------------------------------------------------------------------

def build_distribution(city, off, members, point, tcfg, errors, extras=None):
    """Family quantile curves -> Vincentized, bias-corrected Dist.

    Two meteoblue extras change the numbers here:

      temperature_spread  replaces the guessed sigma for mLM's own point
                          forecast with meteoblue's ensemble standard
                          deviation, per city per day.
      predictability      widens the whole blended distribution on days
                          meteoblue says are hard to call. This is the only
                          source that flags those days in advance.
    """
    extras = extras or {}
    fams = tcfg["families"]
    sigmas = tcfg.get("deterministic_sigma", {})
    mb_cfg = tcfg.get("sources", {}).get("meteoblue", {})
    publish_mb = mb_cfg.get("publish_values", False)

    fam_sets, fam_weights, diag = [], [], {}

    for fam_key, fam in fams.items():
        curves = []
        for m in fam["members"]:
            if m in members:
                mem = (members[m].get(city["name"]) or {}).get(off)
                q = members_to_quantiles(mem) if mem else None
                if q:
                    curves.append(q)
                    diag[m] = {"type": "ensemble", "n": len(mem),
                               "median": round(sorted(mem)[len(mem) // 2], 1)}
            elif m in point:
                mu = (point[m].get(city["name"]) or {}).get(off)
                if mu is not None:
                    s = sigmas.get(m, {}).get(off) or sigmas.get(m, {}).get(str(off)) or 2.5
                    # meteoblue ships its own ensemble sigma; prefer it over
                    # the placeholder whenever it came back.
                    if m == "METEOBLUE" and extras.get("temp_spread"):
                        s = float(extras["temp_spread"])
                    curves.append(normal_quantiles(mu, s))
                    # meteoblue's licence restricts republishing their data,
                    # and docs/data/ is committed and served publicly. Record
                    # that the source contributed, not what it said.
                    if m == "METEOBLUE" and not publish_mb:
                        diag[m] = {"type": "point", "value": None,
                                   "sigma": s, "redacted": True}
                    else:
                        diag[m] = {"type": "point", "value": round(mu, 1),
                                   "sigma": s}
        if not curves:
            continue
        # Equal weight within a family, then family weight across.
        fam_sets.append(blend_quantiles(curves, [1.0] * len(curves)))
        fam_weights.append(fam["weight"])

    if not fam_sets:
        return None, diag

    blended = blend_quantiles(fam_sets, fam_weights)
    bias = (tcfg.get("bias") or {}).get(city["name"], 0.0)
    sf = tcfg.get("spread_factor", 1.0)

    pred = extras.get("predictability")
    widen = meteoblue.predictability_widening(
        pred, mb_cfg.get("predictability_k", 0.6))
    sf_eff = sf * widen

    diag["_bias"] = bias
    diag["_spread_factor"] = round(sf_eff, 3)
    diag["_predictability"] = pred
    diag["_predictability_widening"] = round(widen, 3)
    diag["_n_families"] = len(fam_sets)
    return Dist(adjust(blended, bias, sf_eff)), diag


def evaluate_bracket(p, quote, fee_mult, tcfg):
    ecfg = tcfg["edge"]
    ev_yes = edge_for_side(p, quote["yes_ask"], fee_mult)
    ev_no = edge_for_side(1.0 - p, quote["no_ask"], fee_mult)

    side, ev, price = "YES", ev_yes, quote["yes_ask"]
    if ev_no is not None and (ev is None or ev_no > ev):
        side, ev, price = "NO", ev_no, quote["no_ask"]
    if ev is None:
        return None

    p_side = p if side == "YES" else 1.0 - p
    illiquid = (quote.get("liquidity", quote["volume"]) < ecfg["min_volume"]
                or quote["spread"] > ecfg["max_spread_cents"])

    if illiquid:
        flag = "thin"
    elif ev >= ecfg["flag_high_cents"]:
        flag = "high"
    elif ev >= ecfg["flag_watch_cents"]:
        flag = "watch"
    else:
        flag = None

    return {
        "side": side, "price": price, "ev_cents": round(ev, 2),
        "fee_cents": kalshi_fee_cents(price, fee_mult),
        "kelly": round(kelly_fraction(p_side, price, ecfg["kelly_cap"]), 4),
        "flag": flag, "illiquid": illiquid,
    }


def discover(kal, prefixes):
    out, cursor = {}, None
    pats = [re.compile(rf"{p}[A-Z]{{2,5}}$") for p in prefixes]
    while True:
        page = kal._get("/series", limit=200, cursor=cursor)
        for s in page.get("series", []):
            t = s.get("ticker", "")
            if any(p.match(t) for p in pats):
                out[t] = {"title": s.get("title"),
                          "fee_multiplier": s.get("fee_multiplier"),
                          "settlement_sources": s.get("settlement_sources", [])}
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def match_series(city_name, series_map):
    needle = city_name.lower().replace(" ", "")
    for tk, meta in series_map.items():
        if needle in (meta.get("title") or "").lower().replace(" ", ""):
            return tk
    return None


def _is_for_date(m, date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    stamp = d.strftime("%y%b%d").upper()
    ev = ((m.get("event_ticker") or "") + (m.get("ticker") or "")).upper()
    if stamp in ev:
        return True
    return (m.get("close_time") or "")[:10] == date_str


if __name__ == "__main__":
    sys.exit(main())
