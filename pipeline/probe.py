"""Check every source before trusting it: where it resolved, latest cycle, and
which wind records actually exist. Run after any NCEP implementation change.

    python -m pipeline.probe
"""
from __future__ import annotations

import os
import time

import yaml

from . import src_ncep
from .common import SESSION, floor_hour, iso
from .grib import WIND_FIELDS, Missing, match_fields, read_idx
from .run import ROOT
from .src_web import DET_URL, ENS_URL, _om_params


class _Ctx:  # just enough context for URL building and Open-Meteo params
    def __init__(self, cfg):
        self.now = int(time.time())
        self.lat, self.lon = cfg["site"]["lat"], cfg["site"]["lon"]
        self.t_end = self.now + 48 * 3600


def probe_grib(label, bases, tmpl, cycles):
    for base in bases:
        for c in cycles:
            url = f"{base}/{src_ncep._fmt(tmpl, c, 1)}"
            try:
                inv = read_idx(url + ".idx")
            except Missing:
                continue
            except Exception as e:
                print(f"  {base}: {e}")
                break
            print(f"  OK   {iso(c)}  {url}")
            found = match_fields(inv, WIND_FIELDS)
            for k in WIND_FIELDS:
                print(f"       {k:4s} {found[k][3] if k in found else '-- not in inventory'}")
            return
        print(f"  --   nothing in {base}")


def main():
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    ctx = _Ctx(cfg)
    hours = [floor_hour(ctx.now) - k * 3600 for k in range(0, 12)]
    syn = [c for c in (floor_hour(ctx.now) - k * 3600 for k in range(0, 30)) if time.gmtime(c).tm_hour % 6 == 0]

    for s in cfg["sources"]:
        print(f"\n[{s['id']}] {s.get('label', '')}  ({s['kind']})")
        k = s["kind"]
        if k == "hrrr":
            probe_grib(s["id"], [s.get("base", src_ncep.HRRR_BASE)], s.get("file", src_ncep.HRRR_FILE), hours)
        elif k == "rrfs":
            probe_grib(s["id"], s["bases"], s["file"], hours)
        elif k == "multi_model":
            for comp in s["components"]:
                print(f"  {comp['id']}:")
                cyc = [c for c in (floor_hour(ctx.now) - k2 * 3600 for k2 in range(0, 30))
                       if time.gmtime(c).tm_hour in set(comp.get("cycles", [0, 6, 12, 18]))]
                probe_grib(s["id"], comp.get("bases") or [comp["base"]], comp["file"], cyc)
        elif k == "ensprod":
            for ftype, tmpl in s["files"].items():
                print(f"  {ftype}:")
                probe_grib(s["id"], s["bases"], tmpl, syn)
        elif k in ("openmeteo_ens", "openmeteo_det"):
            url = ENS_URL if k == "openmeteo_ens" else DET_URL
            r = SESSION.get(url, params=_om_params(s["model"], ctx, (10, 80, 100, 120)), timeout=60)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {r.text[:200]}")
                continue
            h = r.json()["hourly"]
            members = {kk.split("_member")[-1] for kk in h if "_member" in kk}
            print(f"  members: {len(members) + 1}")
            for var in [kk for kk in h if kk != "time" and "_member" not in kk]:
                n = sum(x is not None for x in h[var])
                print(f"       {var:22s} {n:4d} non-null hours")
        elif k == "nws_grid":
            r = SESSION.get(f"https://api.weather.gov/points/{ctx.lat:.4f},{ctx.lon:.4f}", timeout=30)
            print(f"  HTTP {r.status_code}  {r.json().get('properties', {}).get('forecastGridData', '')}")
        elif k == "meteoblue":
            print("  key set" if os.environ.get("METEOBLUE_API_KEY") else "  no METEOBLUE_API_KEY (skipped)")


if __name__ == "__main__":
    main()
