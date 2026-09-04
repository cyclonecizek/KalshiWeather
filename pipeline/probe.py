"""Discovery tool. Run this before trusting anything in settings.yml.

    python -m pipeline.probe

NCEP paths move. REFS is in the parallel directory until it goes operational
on 2026-10-06, and HREF disappears the same day. Rather than guess, this
prints what is actually on the server right now, including a sample of the
inventory lines so you can confirm the `idx_regex` matches a real record.
"""

from __future__ import annotations

import re
import sys

import requests

from .gribtools import read_idx
from .kalshi import Kalshi
from .util import latest_cycle, load_yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "kalshi-rain-board/1.0"}


_LINK = re.compile(r'href="([^"?/][^"]*/?)"')


def list_dir(url, session=None):
    """Names in a NOMADS Apache directory listing."""
    s = session or requests.Session()
    r = s.get(url if url.endswith("/") else url + "/", headers=UA, timeout=45)
    r.raise_for_status()
    out = []
    for m in _LINK.finditer(r.text):
        n = m.group(1)
        if n.startswith("..") or n.lower().startswith("http"):
            continue
        out.append(n)
    return out


def discover_path(base, want_prefix, depth=3):
    """Walk a NOMADS tree looking for the real location of a product.

    Beats guessing. NCEP moves things between implementations, parallel and
    production directories swap over on cutover days, and the version number
    in the path changes without notice.
    """
    print(f"  walking {base}")
    try:
        top = list_dir(base)
    except Exception as exc:  # noqa: BLE001
        print(f"    unreachable: {type(exc).__name__} {exc}")
        return
    hits = ([n for n in top if want_prefix.lower() in n.lower()]
            if want_prefix else list(top))
    print(f"    {len(top)} entries; {len(hits)} matching '{want_prefix}'")
    for n in sorted(top)[:25]:
        print(f"      {n}")
    for n in sorted(hits, reverse=True)[:2]:
        sub = base.rstrip("/") + "/" + n.rstrip("/")
        try:
            kids = list_dir(sub)
        except Exception:
            continue
        print(f"    {n} contains: {', '.join(sorted(kids)[:12])}")
        if depth > 1 and kids:
            for k in sorted(kids)[:2]:
                try:
                    g = list_dir(sub + "/" + k.rstrip("/"))
                except Exception:
                    continue
                print(f"      {k} -> {', '.join(sorted(g)[:8])}")


def probe_grib(name, cfg):
    print(f"\n=== {name} ===")
    print(f"base: {cfg['base']}")
    try:
        ymd, cc = latest_cycle(cfg["cycles"], 3.5)
    except Exception as exc:  # noqa: BLE001
        print(f"  cycle resolution failed: {exc}")
        return

    print(f"trying cycle {ymd} {cc:02d}Z")
    found = False
    windows = set()
    for fh in (6, 12, 18, 24, 30, 36, 48):
        url = f"{cfg['base'].rstrip('/')}/" + cfg["pattern"].format(
            ymd=ymd, cc=f"{cc:02d}", fff=f"{fh:03d}", ff=f"{fh:02d}")
        try:
            recs = read_idx(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  f{fh:03d}  MISS  {type(exc).__name__}")
            continue
        found = True
        pat = re.compile(cfg["idx_regex"])
        hits = [r for r in recs if pat.search(r.line)]
        print(f"  f{fh:03d}  {len(recs)} records, {len(hits)} match idx_regex")
        for r in hits[:4]:
            print(f"        {r.line}")
        for r in hits:
            if r.acc_start is not None:
                windows.add(r.acc_end - r.acc_start)
        if not hits:
            print("        --- no regex match. EVERY APCP prob threshold "
                  "in this file: ---")
            seen_thr = set()
            for r in recs:
                if ":APCP:" not in r.line or "prob >" not in r.line:
                    continue
                thr = r.line.split("prob >")[1].split(":")[0]
                if thr in seen_thr:
                    continue
                seen_thr.add(thr)
                print(f"        {r.line}")
            if not seen_thr:
                print("        (none -- this file has no APCP probabilities)")
            else:
                print(f"        thresholds present (mm): "
                      f"{sorted(seen_thr, key=float)}")
                print("        0.254 mm = 0.01 in. If it is absent here, the "
                      "0.01in product lives in a different HREF file "
                      "(try .eas. or .pmmn. instead of .prob.)")

    if found:
        if windows:
            print(f"  accumulation windows available: "
                  f"{sorted(windows)} hours")
            if 24 not in windows:
                print("  NOTE: no 24-hour window. A local calendar day will be "
                      "tiled from shorter records, which costs one byte-range "
                      "fetch per tile. Check longer forecast hours for a 24h "
                      "product before accepting that.")
    else:
        print("  no file reachable -- walking the tree to find the real path:")
        root = cfg["base"].rsplit("/", 1)[0]
        for prefix in cfg.get("discover_prefixes", [cfg["pattern"].split(".")[0]]):
            discover_path(cfg["base"], prefix)
        discover_path(root, "")


def probe_kalshi(base):
    print("\n=== Kalshi rain series ===")
    k = Kalshi(base)
    try:
        series = k.discover_rain_series()
    except Exception as exc:  # noqa: BLE001
        print(f"  discovery failed: {exc}")
        return
    print(f"  {len(series)} series matching KXRAIN*")
    for tk in sorted(series):
        meta = series[tk]
        srcs = ", ".join(meta.get("settlement_sources", []))
        print(f"  {tk:16s} {meta.get('cadence','?'):8s} "
              f"fee={meta.get('fee_multiplier')}  "
              f"src=[{srcs}]  {meta.get('title')}")
    print("\n  Settlement sources vary PER SERIES -- The Weather Company, "
          "The Weather Channel, NWS, AccuWeather and the USGS all appear. "
          "A trailing M means monthly. Never auto-pick a series; put the "
          "exact ticker you verified into cities.yml.")


def probe_markets(base, prefixes=("KXRAIN", "KXHIGH")):
    """Open a few real markets per series so cadence stops being a guess.

    The series list alone cannot tell you whether "Seattle rain" resolves
    daily, monthly or once a season. The markets under it can.
    """
    k = Kalshi(base)
    for prefix in prefixes:
        print(f"\n=== {prefix} series and their markets ===")
        try:
            series = k.discover_series(prefix)
        except Exception as exc:  # noqa: BLE001
            print(f"  discovery failed: {exc}")
            continue

        for tk in sorted(series):
            meta = series[tk]
            srcs = ", ".join(meta["settlement_sources"]) or "none listed"
            print(f"\n  {tk}  [{meta['cadence']}]  {meta['title']}")
            print(f"    settles on: {srcs}")
            if meta["cadence"] in ("monthly", "weekly", "special"):
                print("    (skipping market fetch -- not a daily contract)")
                continue
            try:
                ms = k.markets_for_series(tk)
            except Exception as exc:  # noqa: BLE001
                print(f"    markets unavailable: {exc}")
                continue
            if not ms:
                print("    no open markets")
                continue
            print(f"    {len(ms)} open markets")
            # A single series can carry one market per city, with the city as
            # the ticker suffix (KXRAIN-26SEP04-TTN). Surface the whole set --
            # that suffix list is what cities.yml needs.
            suffixes = sorted({(m.get("ticker") or "").split("-")[-1]
                               for m in ms})
            if len(suffixes) > 3:
                print(f"    city codes in ticker suffix ({len(suffixes)}): "
                      f"{' '.join(suffixes)}")
            # Dump one raw market. The list endpoint returned null prices
            # on 2026-09-04, so this shows which fields actually carry them.
            import json as _json
            print("    RAW first market:")
            print("      " + _json.dumps(ms[0], indent=6)[:1400])
            try:
                full = k.market(ms[0]["ticker"])
                print("    RAW /markets/{ticker}:")
                print("      " + _json.dumps(full, indent=6)[:1400])
            except Exception as exc:
                print(f"    per-market fetch failed: {exc}")
            try:
                print("    RAW orderbook:")
                print("      " + _json.dumps(
                    k.orderbook(ms[0]["ticker"]), indent=6)[:800])
            except Exception as exc:
                print(f"    orderbook failed: {exc}")
            print("    first 3 markets:")
            for m in ms[:3]:
                print(f"      {m.get('ticker')}  close={m.get('close_time')}")
                print(f"        {m.get('title') or m.get('subtitle')}")
                print(f"        bid={m.get('yes_bid')} ask={m.get('yes_ask')} "
                      f"vol={m.get('volume')} "
                      f"strike={m.get('strike_type')} "
                      f"floor={m.get('floor_strike')} cap={m.get('cap_strike')}")


def probe_openmeteo(cfg):
    print("\n=== Open-Meteo ensemble models ===")
    for key, model_id in cfg["models"].items():
        try:
            r = requests.get(cfg["ensemble_base"], params={
                "latitude": 40.78, "longitude": -73.97,
                "hourly": "precipitation", "models": model_id,
                "forecast_days": 2, "timezone": "auto"}, timeout=45)
            if r.status_code != 200:
                print(f"  {key:12s} HTTP {r.status_code}  {r.text[:120]}")
                continue
            h = (r.json().get("hourly") or {})
            members = [k for k in h if k.startswith("precipitation")]
            print(f"  {key:12s} OK  {len(members)} members  ({model_id})")
        except Exception as exc:  # noqa: BLE001
            print(f"  {key:12s} FAIL {exc}")


def main():
    s = load_yaml(ROOT / "config" / "settings.yml")["sources"]
    probe_kalshi(s["kalshi"]["base"])
    probe_markets(s["kalshi"]["base"])
    probe_openmeteo(s["openmeteo"])
    for name, key in (("HREF", "href"), ("REFS", "refs"), ("NBM", "nbm")):
        if key in s:
            probe_grib(name, s[key])
    return 0


if __name__ == "__main__":
    sys.exit(main())
