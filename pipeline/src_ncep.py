"""Convection-allowing NCEP guidance at a point.

HRRR and RRFS: time-lagged ensembles built from the most recent hourly cycles
plus the latest 00/06/12/18Z cycles (which run longer).
REFS and HREF: NCEP publishes ensemble mean and spread products, not member
fields, so each becomes a set of pseudo-members drawn from mean +/- spread.
"""
from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import NormalDist

from .common import (MPH_PER_MS, Context, SourceResult, floor_hour, gust_at_height,
                     iso, log, scalar_to_height, sample_from_levels, sd_from_uv,
                     vector_to_height)
from .grib import WIND_FIELDS, Missing, earth_relative, exists, fetch_point

HRRR_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_FILE = "hrrr.{ymd}/conus/hrrr.t{hh}z.wrfsfcf{fh:02d}.grib2"


def _fmt(tmpl: str, cycle: int, fh: int) -> str:
    g = time.gmtime(cycle)
    return tmpl.format(ymd=time.strftime("%Y%m%d", g), hh=f"{g.tm_hour:02d}", fh=fh)


def _point(url: str, cycle: int, ctx: Context, rotate: bool = True) -> dict | None:
    """Cached point extraction of the standard wind fields. None if not published.

    rotate=False for spread files: a standard deviation is not a vector."""
    cached = ctx.cache.get(url)
    if cached is not None:
        return cached
    try:
        vals, meta = fetch_point(url, WIND_FIELDS, ctx.lat, ctx.lon)
    except Missing:
        return None
    out = {}
    for z in ("10", "80"):
        u, v = vals.get("u" + z), vals.get("v" + z)
        if u is not None and v is not None:
            uv = earth_relative(u, v, meta.get("u" + z), ctx.lon) if rotate else (abs(u), abs(v))
            if uv:
                out["u" + z], out["v" + z] = uv[0] * MPH_PER_MS, uv[1] * MPH_PER_MS
    for k in ("w10", "w80", "g0"):
        if vals.get(k) is not None:
            out[k] = vals[k] * MPH_PER_MS
    if out:                              # only cache real data
        ctx.cache.put(url, cycle, out)
    return out


def _sample_det(p: dict, ctx: Context):
    levels = {}
    for z in (10.0, 80.0):
        k = str(int(z))
        if "u" + k in p and "v" + k in p:
            levels[z] = (p["u" + k], p["v" + k])
    if not levels:
        return None
    return sample_from_levels(levels, ctx, gust10=p.get("g0"), wind10=p.get("w10"))


def _max_fh(cycle: int, short: int, long: int) -> int:
    return long if time.gmtime(cycle).tm_hour % 6 == 0 else short


def _pick_base(bases: list[str], tmpl: str, ctx: Context, cycles: list[int]) -> tuple[str, int] | None:
    for base in bases:
        for c in cycles:
            if exists(f"{base}/{_fmt(tmpl, c, 1)}.idx"):
                return base, c
    return None


def time_lagged(scfg: dict, ctx: Context, base: str | None, tmpl: str,
                short_fh: int, long_fh: int, workers: int) -> SourceResult:
    lag = int(scfg.get("lag_cycles", 4))
    syn = int(scfg.get("synoptic_extra", 2))
    recent = [floor_hour(ctx.now) - k * 3600 for k in range(0, 10)]

    if base is None:
        picked = _pick_base(scfg["bases"], tmpl, ctx, recent)
    else:
        picked = next(((base, c) for c in recent if exists(f"{base}/{_fmt(tmpl, c, 1)}.idx")), None)
    if not picked:
        return SourceResult({}, status="missing", note="no recent cycle found")
    base, latest = picked

    cycles = [latest - k * 3600 for k in range(lag)]
    syn_cycles = [c for c in (latest - k * 3600 for k in range(0, 30))
                  if time.gmtime(c).tm_hour % 6 == 0 and c not in cycles][:syn]
    cycles += syn_cycles

    tasks = []
    for c in cycles:
        for fh in range(0, _max_fh(c, short_fh, long_fh) + 1):
            if ctx.in_window(c + fh * 3600):
                tasks.append((c, fh))

    def run(task):
        c, fh = task
        try:
            p = _point(f"{base}/{_fmt(tmpl, c, fh)}", c, ctx)
        except Exception as e:  # one bad file should not sink the source
            log.warning("%s %s f%03d: %s", scfg["id"], iso(c), fh, e)
            return task, None
        return task, (_sample_det(p, ctx) if p else None)

    members: dict[str, dict] = {}
    with ThreadPoolExecutor(workers) as ex:
        for (c, fh), smp in ex.map(run, tasks):
            if smp:
                mid = time.strftime("%d/%HZ", time.gmtime(c))
                members.setdefault(mid, {})[c + fh * 3600] = smp

    got = [c for c in cycles if time.strftime("%d/%HZ", time.gmtime(c)) in members]
    status = "ok" if len(got) == len(cycles) else ("partial" if got else "missing")
    note = f"{len(got)}/{len(cycles)} cycles"
    if base.endswith("/para"):
        note += "; parallel feed"
    return SourceResult(members, cycle=iso(latest), note=note, status=status)


def hrrr(scfg: dict, ctx: Context) -> SourceResult:
    return time_lagged(scfg, ctx, scfg.get("base", HRRR_BASE), scfg.get("file", HRRR_FILE),
                       short_fh=18, long_fh=48, workers=16)


def rrfs(scfg: dict, ctx: Context) -> SourceResult:
    # NOMADS: keep concurrency modest to stay well under its rate limit
    return time_lagged(scfg, ctx, None, scfg["file"], short_fh=18, long_fh=84, workers=4)


# ---------------------------------------------------------------- multi-model member sets
def _cycles_back(hours: set, now: int, max_back: int = 60) -> list[int]:
    return [c for c in (floor_hour(now) - k * 3600 for k in range(max_back))
            if time.gmtime(c).tm_hour in hours]


def _component(comp: dict, ctx: Context) -> tuple[list, str, str | None]:
    """Tasks for one model in a member set: the latest `lag` published cycles."""
    bases = comp.get("bases") or [comp["base"]]
    cands = _cycles_back(set(comp.get("cycles", [0, 6, 12, 18])), ctx.now)
    picked = _pick_base(bases, comp["file"], ctx, cands)
    if not picked:
        return [], f"{comp['id']} not found", None
    base, latest = picked
    use = [c for c in cands if c <= latest][: int(comp.get("lag", 2))]
    tasks = [(comp, base, c, fh) for c in use
             for fh in range(0, int(comp.get("max_fh", 48)) + 1)
             if ctx.in_window(c + fh * 3600)]
    return tasks, f"{comp['id']} {', '.join(time.strftime('%HZ', time.gmtime(c)) for c in use)}", base


def multi_model(scfg: dict, ctx: Context) -> SourceResult:
    """Members from several deterministic models, each with its own cycle lag
    (e.g. the HREF membership: HiResW ARW, ARW mem2, FV3, NAM 3 km, HRRR)."""
    tasks, notes, missing = [], [], []
    for comp in scfg["components"]:
        t, note, base = _component(comp, ctx)
        (notes if base else missing).append(note)
        tasks += t

    def run(task):
        comp, base, c, fh = task
        try:
            p = _point(f"{base}/{_fmt(comp['file'], c, fh)}", c, ctx)
        except Exception as e:
            log.warning("%s %s %s f%02d: %s", scfg["id"], comp["id"], iso(c), fh, e)
            return task, None
        return task, (_sample_det(p, ctx) if p else None)

    members: dict[str, dict] = {}
    with ThreadPoolExecutor(int(scfg.get("workers", 4))) as ex:
        for (comp, _, c, fh), smp in ex.map(run, tasks):
            if smp:
                mid = f"{comp['id']} {time.strftime('%d/%HZ', time.gmtime(c))}"
                members.setdefault(mid, {})[c + fh * 3600] = smp

    note = "; ".join(notes + [f"missing: {', '.join(missing)}"] if missing else notes)
    status = "missing" if not members else ("partial" if missing else "ok")
    return SourceResult(members, cycle="", note=note, status=status)


# ---------------------------------------------------------------- ensemble products
def _normal_pairs(n: int, seed: int = 7) -> list[tuple[float, float]]:
    nd = NormalDist()
    zs = [nd.inv_cdf((i + 0.5) / n) for i in range(n)]
    zv = zs[:]
    random.Random(seed).shuffle(zv)
    return list(zip(zs, zv))


def _ens_members(mean: dict, sprd: dict, ctx: Context, n: int):
    """Pseudo-members at the evaluation height from mean/spread fields (mph)."""
    lv_mean = {z: (mean["u%d" % z], mean["v%d" % z]) for z in (10, 80)
               if "u%d" % z in mean and "v%d" % z in mean}
    uv = vector_to_height({float(k): v for k, v in lv_mean.items()}, ctx.target_m, ctx.alpha)
    if uv is None:
        return None, "no mean u/v"
    su = scalar_to_height({float(z): sprd.get("u%d" % z) for z in (10, 80)}, ctx.target_m, ctx.alpha)
    sv = scalar_to_height({float(z): sprd.get("v%d" % z) for z in (10, 80)}, ctx.target_m, ctx.alpha)

    w10 = mean.get("w10")
    if w10 is None and 10 in lv_mean:
        w10 = math.hypot(*lv_mean[10])
    excess = (mean["g0"] - w10) if (mean.get("g0") is not None and w10 is not None) else None

    out = []
    if su is not None and sv is not None:
        for zu, zv in _normal_pairs(n):
            s, d = sd_from_uv(uv[0] + zu * su, uv[1] + zv * sv)
            out.append((s, d, s + max(0.0, excess) if excess is not None else None))
        return out, "mean ± u/v spread"

    ws = scalar_to_height({10.0: mean.get("w10"), 80.0: mean.get("w80")}, ctx.target_m, ctx.alpha)
    ss = scalar_to_height({10.0: sprd.get("w10"), 80.0: sprd.get("w80")}, ctx.target_m, ctx.alpha)
    _, d = sd_from_uv(*uv)
    if ws is not None and ss is not None:
        for zu, _ in _normal_pairs(n):
            s = max(0.0, ws + zu * ss)
            out.append((s, d, s + max(0.0, excess) if excess is not None else None))
        return out, "mean ± speed spread; direction from mean vector"

    s, d = sd_from_uv(*uv)
    return [(s, d, gust_at_height(mean.get("g0"), w10, s))], "mean only, no spread"


def ensprod(scfg: dict, ctx: Context) -> SourceResult:
    files = scfg["files"]
    n = int(scfg.get("pseudo_members", 20))
    syn = [c for c in (floor_hour(ctx.now) - k * 3600 for k in range(0, 30))
           if time.gmtime(c).tm_hour % 6 == 0]
    picked = _pick_base(scfg["bases"], files["mean"], ctx, syn)
    if not picked:
        return SourceResult({}, status="missing", note="no recent cycle found")
    base, cycle = picked

    fhs = [fh for fh in range(1, int(scfg.get("max_fh", 48)) + 1) if ctx.in_window(cycle + fh * 3600)]

    def run(fh):
        try:
            m = _point(f"{base}/{_fmt(files['mean'], cycle, fh)}", cycle, ctx)
            s = _point(f"{base}/{_fmt(files['sprd'], cycle, fh)}", cycle, ctx, rotate=False) if "sprd" in files else {}
        except Exception as e:
            log.warning("%s f%02d: %s", scfg["id"], fh, e)
            return fh, None, None
        if not m:
            return fh, None, None
        mem, how = _ens_members(m, s or {}, ctx, n)
        return fh, mem, how

    members: dict[str, dict] = {}
    hows = set()
    with ThreadPoolExecutor(4) as ex:
        for fh, mem, how in ex.map(run, fhs):
            if how:
                hows.add(how)
            if not mem:
                continue
            for i, smp in enumerate(mem):
                members.setdefault(f"p{i + 1:02d}", {})[cycle + fh * 3600] = smp

    note = "; ".join(sorted(hows)) or "no usable fields"
    if base.endswith("/para"):
        note += "; parallel feed"
    return SourceResult(members, cycle=iso(cycle), note=note,
                        status="ok" if members else "missing")
