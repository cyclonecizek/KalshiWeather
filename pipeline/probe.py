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
    for fh in (12, 24, 36, 48):
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
        if not hits:
            print("        --- no regex match. Candidate APCP lines: ---")
            for r in recs:
                if ":APCP:" in r.line and "prob" in r.line:
                    print(f"        {r.line}")
                    break
        break

    if not found:
        print("  no file reachable. Directory listing to try by hand:")
        print(f"    {cfg['base']}/")


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
        srcs = ", ".join(
            s.get("name", "?") for s in meta.get("settlement_sources", []))
        print(f"  {tk:16s} fee={meta.get('fee_multiplier')}  "
              f"src=[{srcs}]  {meta.get('title')}")
    print("\n  Cross-check every settlement source above. The daily rain "
          "markets settle on The Weather Company, not NWS -- if a series "
          "shows something else, its rules differ from the rest.")


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
    probe_openmeteo(s["openmeteo"])
    for name, key in (("HREF", "href"), ("REFS", "refs"), ("NBM", "nbm")):
        if key in s:
            probe_grib(name, s[key])
    return 0


if __name__ == "__main__":
    sys.exit(main())
