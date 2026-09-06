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
                       implied_distribution, implied_quantiles, pick_ladder)
from .kalshi import SCHEMA_VERSION, Kalshi
from .sources import meteoblue, nbm_temp, temp_sources

try:
    from .sources import observations          # optional; see build.py
except ImportError:
    observations = None
from .tempdist import (Dist, adjust, blend_quantiles, members_to_quantiles,
                       normal_quantiles)

# Optional, same reasoning as the observations import: a stale tempdist.py
# should cost the intraday correction, not the whole board.
try:
    from .tempdist import apply_observation
except ImportError:
    apply_observation = None
from .util import (edge_for_side, kalshi_fee_cents, kelly_fraction, load_yaml,
                   local_date_str)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DAY_OFFSETS = (0, 1)


def build_distribution(city, off, members, point, tcfg, errors, extras=None,
                       obs=None, obs_cfg=None, nbm_sigma=None):
    from .sources.hourly import DETAILS
    from .quality import age_minutes
    fam_sets, weights, diag = [], [], {}
    sigmas = tcfg.get('deterministic_sigma', {})
    cfgo = obs_cfg or {}
    usable_obs = bool(obs and obs.get('temperature_complete') and
                      age_minutes(obs.get('latest_at')) <= 90 and obs.get('max_f') is not None)
    for fam_key, fam in tcfg['families'].items():
        curves=[]
        for model in fam['members']:
            if model == 'METEOBLUE' and not tcfg['sources']['meteoblue'].get('publish_values'):
                continue
            mem=(members.get(model,{}).get(city['name']) or {}).get(off)
            detail=DETAILS.get((city['name'],off,model))
            if mem and len(mem)>=3:
                q=members_to_quantiles(mem)
                if usable_obs and detail:
                    q=apply_observation(q,obs['max_f'],remaining=detail['remaining'],
                        tolerance=cfgo.get('tolerance_f',.5),min_spread=cfgo.get('min_spread_f',1.4))
                curves.append(q)
                diag[model]={'type':'ensemble','n':len(mem),'median':round(q[len(q)//2],2)}
            elif not usable_obs:
                mu=(point.get(model,{}).get(city['name']) or {}).get(off)
                if mu is None:continue
                sigma=sigmas.get(model,{}).get(off) or sigmas.get(model,{}).get(str(off)) or 2.5
                if model=='NBM_T' and nbm_sigma:sigma=nbm_sigma
                curves.append(normal_quantiles(mu,sigma))
                diag[model]={'type':'point','value':mu,'sigma':sigma}
        if curves:
            curve=blend_quantiles(curves,[1]*len(curves))
            centers=[q[len(q)//2] for q in curves]
            center=sum(centers)/len(centers)
            variance=sum((x-center)**2 for x in centers)/len(centers)
            sigma=max((curve[11]-curve[3])/2.5631,.3)
            factor=(1+tcfg.get('disagreement_factor',1)*variance/sigma**2)**.5
            fam_sets.append(adjust(curve,0,factor))
            weights.append(fam['weight'])
    if not fam_sets:return None,diag
    quants=blend_quantiles(fam_sets,weights)
    # A disagreement term retains between-family uncertainty. Its strength
    # is explicit and must be tested out of sample, not tuned to market width.
    means=[x[len(x)//2] for x in fam_sets]
    mu=sum(x*w for x,w in zip(means,weights))/sum(weights)
    between=(sum(w*(x-mu)**2 for x,w in zip(means,weights))/sum(weights))**.5
    spread=tcfg.get('spread_factor',1)
    bias=tcfg.get('bias',{}).get(city['name'],0)
    quants=adjust(quants,bias,spread)
    from .tempdist import _probit,QUANTILES
    sigma=max((quants[11]-quants[3])/2.5631,.3)
    quants=adjust(quants,0,(1+tcfg.get('disagreement_factor',1)*between**2/sigma**2)**.5)
    floor=None
    if usable_obs:
        floor=obs['max_f']-cfgo.get('tolerance_f',.5)
        quants=[max(floor,v) for v in quants]
        diag['_observed_max']=obs['max_f']
        diag['_obs_source']=obs['source']
        diag['_intraday_method']='remaining_hour_ensembles'
    diag.update(_n_families=len(fam_sets),_bias=bias,_spread_factor=spread,
                _between_family_sd=between,_observation_used=usable_obs)
    return Dist(quants,floor=floor),diag


def evaluate_bracket(p, quote, fee_mult, tcfg):
    ecfg = tcfg["edge"]
    if not quote.get("executable"):
        return None
    ev_yes = edge_for_side(p, quote["yes_ask"], fee_mult)
    ev_no = edge_for_side(1.0 - p, quote["no_ask"], fee_mult)

    side, ev, price = "YES", ev_yes, quote["yes_ask"]
    if ev_no is not None and (ev is None or ev_no > ev):
        side, ev, price = "NO", ev_no, quote["no_ask"]
    if ev is None:
        return None

    p_side = p if side == "YES" else 1.0 - p
    # Say WHY, not just that. A row dropped without a reason is
    # indistinguishable from a row that was never computed, and the two need
    # very different responses.
    reasons = []
    liq = quote.get("liquidity", quote["volume"])
    if liq < ecfg["min_volume"]:
        reasons.append(f"only {liq} open interest")
    if quote.get("spread") is None or quote["spread"] > ecfg["max_spread_cents"]:
        # NOTE: ev is already computed at the ask, so the spread has been
        # paid for once. This gate is about whether the quote is TRUSTWORTHY,
        # not about cost -- a wide book often means a stale or nominal price.
        reasons.append("missing or wide bid/ask spread")
    illiquid = bool(reasons)


    if illiquid:
        flag = "thin"
    elif ev >= ecfg.get("implausible_cents", 25.0):
        # Too good to be true, so treat it as a fault report rather than a
        # trade. Real mispricings on these markets are single digits.
        flag = "suspect"
    elif ev >= ecfg["flag_high_cents"]:
        flag = "high"
    elif ev >= ecfg["flag_watch_cents"]:
        flag = "watch"
    else:
        flag = None

    return {
        "side": side, "price": price, "ev_cents": round(ev, 2),
        "fee_cents": kalshi_fee_cents(price, fee_mult),
        "kelly": round(kelly_fraction(p_side, price, ecfg["kelly_cap"], fee_mult), 4),
        "flag": flag, "illiquid": illiquid,
        "gated_because": "; ".join(reasons) or None,
    }


def _depth_for(kal, ticker, side):
    """Contracts available at the quoted price. Only called for flagged rows."""
    from .kalshi import book_depth
    try:
        yes_n, no_n = book_depth(kal.orderbook(ticker))
    except Exception:  # noqa: BLE001
        return None
    return yes_n if side == "YES" else no_n


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
    # Ticker stamp only. The close_time fallback was wrong: a Sep 5 contract
    # closes on Sep 6, so it matched BOTH day 0 and day 1 and pulled the same
    # markets into two different ladders.
    return stamp in ev


def main():
    from .run import run
    return run("temperature")


if __name__ == "__main__":
    sys.exit(main())
