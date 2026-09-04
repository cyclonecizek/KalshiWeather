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

from .blend import blend, evaluate
from .kalshi import Kalshi, pick_city_market
from .sources import gribprob, meteoblue, nws_text, openmeteo
from .util import load_yaml, local_date_str

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DAY_OFFSETS = (0, 1)


def main():
    settings = load_yaml(ROOT / "config" / "settings.yml")
    cities = load_yaml(ROOT / "config" / "cities.yml")["cities"]
    src = settings["sources"]
    rho = settings.get("pop_stitch_rho", 0.5)
    thr_mm = settings.get("threshold_mm", 0.254)

    print(f"building board for {len(cities)} cities")

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
        print("  meteoblue: no key set, skipping")

    ndfd = run("ndfd", lambda: nws_text.fetch_ndfd(
        cities, src["ndfd"], rho, DAY_OFFSETS))
    if ndfd:
        probs["NDFD"] = ndfd

    mos = run("mos", lambda: nws_text.fetch_mos(
        cities, src["mos"], rho, DAY_OFFSETS))
    if mos:
        probs["MOS"] = mos

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

            if not mb_cfg.get("publish_values", False):
                mp_public = {k: v for k, v in mp.items() if k != "METEOBLUE"}
            else:
                mp_public = mp

            b = blend(mp, settings, cam_multiplier=mult)
            entry = {"date": date_str, "market": q, "raw_models": mp_public,
                     "predictability": rec.get("predictability"),
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
