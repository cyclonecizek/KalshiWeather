"""Build docs/data/board.json.

Runs on a schedule in GitHub Actions, commits the JSON, and GitHub Pages
serves it as a static file. Nothing in the browser talks to NOMADS or Kalshi
directly -- CORS would block most of it and you can't parse GRIB in a tab
anyway.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .blend import blend, evaluate, variants
from .kalshi import SCHEMA_VERSION, Kalshi, pick_city_market
from .sources import gribprob, meteoblue, nws_text, observations, openmeteo
from .util import load_yaml, local_date_str, local_day_window

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DAY_OFFSETS = (0, 1)


def main():
    settings = load_yaml(ROOT / "config" / "settings.yml")
    cities = load_yaml(ROOT / "config" / "cities.yml")["cities"]
    src = settings["sources"]
    rho = settings.get("pop_stitch_rho", 0.5)
    thr_mm = settings.get("threshold_mm", 0.254)

    print(f"building board for {len(cities)} cities "
          f"| kalshi schema {SCHEMA_VERSION}")

    # ---- model guidance ------------------------------------------------
    probs = {}          # {MODEL: {city: {offset: p}}}
    runs = {}           # {MODEL: cycle string}
    errors = []

    def run(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  {name} FAILED: {exc}")
            traceback.print_exc()
            errors.append(f"{name}: {exc}")
            return None

    om = run("openmeteo", lambda: openmeteo.fetch(
        cities, src["openmeteo"], thr_mm, DAY_OFFSETS))
    if om:
        probs.update(om)

    # meteoblue: one cached call per city per day serves both boards.
    mb_cfg = settings["temperature"]["sources"]["meteoblue"]
    mb = run("meteoblue", lambda: meteoblue.fetch(cities, mb_cfg, DAY_OFFSETS)) or {}
    if mb:
        probs["METEOBLUE"] = {
            city: {off: rec["pop"] for off, rec in days.items()
                   if rec.get("pop") is not None}
            for city, days in mb.items()
        }
    else:
        import os as _os
        if _os.environ.get("METEOBLUE_KEY") or mb_cfg.get("api_key"):
            print("  meteoblue: KEY IS SET but no data came back -- check the "
                  "packages your key covers (rainspot_package) and the "
                  "credit budget above")
        else:
            print("  meteoblue: no METEOBLUE_KEY in the environment. Set it as "
                  "an Actions secret AND make sure the workflow step passes "
                  "it through with `env: METEOBLUE_KEY: ${{ secrets... }}`")

    ndfd = run("ndfd", lambda: nws_text.fetch_ndfd(
        cities, src["ndfd"], rho, DAY_OFFSETS))
    if ndfd:
        probs["NDFD"] = ndfd

    if src["mos"].get("enabled", True):
        mos = run("mos", lambda: nws_text.fetch_mos(
            cities, src["mos"], rho, DAY_OFFSETS))
        if mos:
            probs["MOS"] = mos
    else:
        print("  mos: disabled in settings")

    for key, cfg_key in (("HREF", "href"), ("REFS", "refs"), ("NBM", "nbm")):
        cfg = src.get(cfg_key)
        if not cfg:
            continue
        if cfg.get("enabled") is False:
            print(f"  {key}: disabled in settings, skipping")
            continue
        if _retired(cfg):
            print(f"  {key}: past retirement date, skipping")
            continue
        res = run(key, lambda c=cfg, k=key: gribprob.fetch(
            k, cities, c, rho, DAY_OFFSETS))
        if res:
            probs[key], runs[key] = res

    obs_cfg = src.get("observations", {})
    obs = run("observations", lambda: observations.fetch(
        cities, DAY_OFFSETS, obs_cfg)) or {}

    # ---- market --------------------------------------------------------
    kal = Kalshi(src["kalshi"]["base"])
    discovered = run("kalshi-discovery", kal.discover_rain_series) or {}
    print(f"  kalshi: {len(discovered)} rain series discovered")

    # The live daily rain market is ONE series carrying a market per city,
    # keyed by the ticker suffix (KXRAIN-26SEP04-TTN). The old per-city
    # series (KXRAINDNYC, KXRAINSEA, ...) still exist but have no open
    # markets. Fetch the shared series once and slice it per city.
    shared_ticker = (load_yaml(ROOT / "config" / "cities.yml")
                     .get("rain_series") or "KXRAIN")
    shared = run("kalshi-rain-markets",
                 lambda: kal.markets_for_series(shared_ticker)) or []
    print(f"  kalshi: {len(shared)} open markets in {shared_ticker}")

    # ---- assemble ------------------------------------------------------
    rows = []
    for c in cities:
        code = c.get("rain_code")
        if not code:
            continue
        ticker = shared_ticker
        meta = discovered.get(shared_ticker, {})
        markets = shared

        days = {}
        for off in DAY_OFFSETS:
            date_str = local_date_str(c["tz"], off)
            m = pick_city_market(markets, date_str, code)
            if m is not None:
                kal.hydrate([m])
            if not m:
                continue
            q = Kalshi.quote(m)
            if not q:
                print(f"  {c['name']} {date_str}: market found but no quote")
                continue
            q["fee_multiplier"] = meta.get("fee_multiplier")

            mp = {}
            for model, by_city in probs.items():
                v = (by_city.get(c["name"]) or {}).get(off)
                if v is not None:
                    mp[model] = v

            # rainSPOT gives the wet-area fraction around the station,
            # which is the area-to-point discount the CAM neighbourhood
            # probabilities need. Recorded always, applied only on request.
            rec = (mb.get(c["name"]) or {}).get(off) or {}
            spot = rec.get("rainspot")
            cov = meteoblue.coverage_multiplier(
                spot, mb_cfg.get("coverage_floor", 0.35))
            mult = cov if mb_cfg.get("apply_coverage_to_cam") else 1.0

            # With publish_values off, mLM still feeds the consensus but its
            # value is withheld. Note that a "how much did it move things"
            # delta is NOT a safe compromise: every other family is published
            # and the weights are in settings.yml, so any per-city number
            # derived from mLM can be inverted straight back to their
            # forecast. Presence is the most that can be shown.
            mlm_present = mp.get("METEOBLUE") is not None
            if not mb_cfg.get("publish_values", False):
                mp_public = {k: v for k, v in mp.items() if k != "METEOBLUE"}
            else:
                mp_public = mp

            b = blend(mp, settings, cam_multiplier=mult)
            ob = (obs.get(c["name"]) or {}).get(off)
            if b is not None and ob is not None:
                adj, why = observations.condition_rain(
                    b["consensus"], ob,
                    1.0 - obs_cfg.get("rain_observed_p", 0.98))
                if why:
                    b["consensus_forecast"] = round(b["consensus"], 4)
                    b["consensus"] = adj
                    b["obs_effect"] = why
            # How much of the contract window is already in the past. At 1am
            # UTC "today" in Los Angeles is 82% gone: the models are
            # describing weather that has largely happened and the market has
            # converged. Those rows are not tradeable signal.
            w_start, w_end = local_day_window(c["tz"], off)
            span = (w_end - w_start).total_seconds()
            gone = (datetime.now(timezone.utc) - w_start).total_seconds()
            elapsed = max(0.0, min(1.0, gone / span))

            entry = {"date": date_str, "market": q, "raw_models": mp_public,
                     "mlm_present": mlm_present,
                     # What each competing configuration would have said, so
                     # the scorer can rank them against outcomes later.
                     "variants": variants(
                         mp, settings,
                         mb_cfg.get("publish_values", False)),
                     "elapsed": round(elapsed, 3),
                     "predictability": rec.get("predictability"),
                     "observed": None if not ob else {
                         "precip_mm": ob["precip_mm"], "wet": ob["wet"],
                         "source": ob["source"], "elapsed": ob["elapsed"]},
                     "coverage": None if spot is None else {
                         "wet_fraction": spot["wet_fraction"],
                         "centre_wet": spot["centre_wet"],
                         "multiplier": round(cov, 3),
                         "applied": bool(mb_cfg.get("apply_coverage_to_cam")),
                     }}
            if b:
                entry.update(b)
                ev = evaluate(b["consensus"], q, settings)
                if ev:
                    # Only the actionable rows get an orderbook call. Fetching
                    # depth for every market would double the request count
                    # for information you would not look at.
                    if ev.get("flag") in ("high", "watch"):
                        ev["depth"] = _depth_for(kal, q["ticker"], ev["side"])
                    entry["edge"] = ev
            days[str(off)] = entry

        if days:
            rows.append({
                "city": c["name"],
                "series": f"{ticker}-{code}",
                "station": c.get("station"),
                "icao": c.get("icao"),
                "tz": c["tz"],
                "verified": c.get("verified", False),
                "settlement_sources": meta.get("settlement_sources", []),
                "days": days,
            })

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threshold_inches": settings["threshold_inches"],
        "meteoblue_enabled": "METEOBLUE" in probs,
        "meteoblue_published": bool(mb_cfg.get("publish_values", False)),
        "families": {k: {"label": v["label"], "weight": v["weight"],
                         "members": v["members"]}
                     for k, v in settings["families"].items()},
        "model_runs": runs,
        "errors": errors,
        "cities": rows,
    }

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "board.json").write_text(json.dumps(board, indent=1))

    # Append-only history, so that in a month you can fit the calibration
    # offsets in settings.yml against what actually happened.
    hist = DATA / "history"
    hist.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    (hist / f"{stamp}.json").write_text(json.dumps(board))

    print(f"wrote {len(rows)} cities, {len(errors)} source errors")
    return 0 if rows else 1


def _depth_for(kal, ticker, side):
    """Contracts available at the quoted price, or None if unavailable."""
    from .kalshi import book_depth
    try:
        yes_n, no_n = book_depth(kal.orderbook(ticker))
    except Exception:  # noqa: BLE001
        return None
    return yes_n if side == "YES" else no_n


def _retired(cfg):
    r = cfg.get("retires")
    if not r:
        return False
    return datetime.now(timezone.utc).date() >= datetime.strptime(
        r, "%Y-%m-%d").date()


def _match_series(city_name, discovered):
    needle = city_name.lower().replace(" ", "")
    for tk, meta in discovered.items():
        title = (meta.get("title") or "").lower().replace(" ", "")
        if needle and needle in title:
            return tk
    return None


if __name__ == "__main__":
    sys.exit(main())
