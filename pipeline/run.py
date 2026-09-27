"""Build docs/data/plume.json from every configured source.

    python -m pipeline.run [--config config.yaml] [--only hrrr,refs]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import yaml

from . import src_ncep, src_web
from .common import FT_TO_M, Context, PointCache, SourceResult, floor_hour, iso, log

KINDS = {
    "hrrr": src_ncep.hrrr,
    "rrfs": src_ncep.rrfs,
    "ensprod": src_ncep.ensprod,
    "multi_model": src_ncep.multi_model,
    "openmeteo_ens": src_web.openmeteo_ens,
    "openmeteo_det": src_web.openmeteo_det,
    "nws_grid": src_web.nws_grid,
    "meteoblue": src_web.meteoblue,
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _r1(x):
    return None if x is None else round(x, 1)


def build(cfg: dict, only: set | None = None) -> dict:
    now = int(time.time())
    site = cfg["site"]
    win = cfg["window"]
    t_start = floor_hour(now) - int(win["hours_back"]) * 3600
    t_end = floor_hour(now) + int(win["hours_ahead"]) * 3600
    timeline = list(range(t_start, t_end + 1, 3600))
    idx = {t: i for i, t in enumerate(timeline)}

    cache = PointCache(os.path.join(ROOT, "cache", "points.json"))
    cache.prune(now - 4 * 86400)
    ctx = Context(now=now, lat=float(site["lat"]), lon=float(site["lon"]),
                  target_m=float(site["height_ft"]) * FT_TO_M,
                  alpha=float(cfg.get("vertical", {}).get("power_law_alpha", 0.14)),
                  t_start=t_start, t_end=t_end, cache=cache)

    sources = []
    for scfg in cfg["sources"]:
        if only and scfg["id"] not in only:
            continue
        t0 = time.time()
        if not scfg.get("enabled", True):
            res = SourceResult({}, status="disabled", note="disabled in config")
        else:
            try:
                res = KINDS[scfg["kind"]](scfg, ctx)
            except Exception as e:
                log.exception("%s failed", scfg["id"])
                res = SourceResult({}, status="error", note=f"{type(e).__name__}: {e}"[:200])
        members = []
        for mid, series in sorted(res.members.items()):
            spd = [None] * len(timeline)
            dirs = [None] * len(timeline)
            gst = [None] * len(timeline)
            for t, (s, d, g) in series.items():
                i = idx.get(int(t))
                if i is None:
                    continue
                spd[i], dirs[i], gst[i] = _r1(s), round(d) % 360, _r1(g)
            if any(v is not None for v in spd):
                members.append({"id": mid, "spd": spd, "dir": dirs, "gst": gst})
        el = round(time.time() - t0, 1)
        log.info("%-10s %-8s %3d members  %5.1fs  %s", scfg["id"], res.status, len(members), el, res.note)
        sources.append({
            "id": scfg["id"], "label": scfg.get("label", scfg["id"]),
            "family": scfg.get("family", "global"), "weight": float(scfg.get("weight", 1)),
            "status": res.status, "cycle": res.cycle, "note": res.note,
            "seconds": el, "members": members,
        })

    cache.save()
    log.info("cache hits: %d", cache.hits)
    return {
        "generated": iso(now), "generated_unix": now,
        "site": site, "target_m": round(ctx.target_m, 1),
        "constraint": cfg["constraint"],
        "times": timeline, "sources": sources,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "data", "plume.json"))
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with open(a.config) as f:
        cfg = yaml.safe_load(f)
    data = build(cfg, set(filter(None, a.only.split(","))) or None)

    total = sum(len(s["members"]) for s in data["sources"])
    if total == 0:
        log.error("no members from any source; keeping the previous plume.json")
        return 1
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, a.out)
    log.info("wrote %s (%d members, %.0f kB)", a.out, total, os.path.getsize(a.out) / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
