"""NBM daily maximum temperature, with NBM's own ensemble spread.

The board's temperature width has been a guess: multi-model disagreement
across five global ensembles, multiplied by a `spread_factor` I picked. That
measures how much models differ from each other, which is a larger quantity
than point-station forecast error -- which is why the board's intervals came
out roughly twice the market's and every central bracket looked overpriced.

A 2026-09-05 probe of the NBM core file found this:

    TMAX:2 m above ground:12-24 hour max fcst:
    TMAX:2 m above ground:12-24 hour max fcst:ens std dev

A mean and a standard deviation, gridded, from a system whose entire purpose
is calibrated probabilistic guidance. That is a measured spread rather than
an assumed one, and it varies by city and by day.

CAVEAT worth keeping: NBM is calibrated against NWS observations, and these
contracts settle on The Weather Company. It replaces a guess with something
principled, not with something verified for this particular target. The
scorer still has the final word.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

from ..gribtools import (fetch_record, read_idx, sampler_from_bytes)
from ..util import aligned_cycle, local_day_window

LAG_HOURS = 2.0

# The plain record is the mean; the sibling ending `ens std dev` is the
# spread. Order matters -- match the std dev pattern first, since the mean
# pattern is a prefix of it.
_SD = re.compile(r":TMAX:2 m above ground:(\d+)-(\d+) hour max fcst:ens std dev")
_MEAN = re.compile(r":TMAX:2 m above ground:(\d+)-(\d+) hour max fcst:\s*$")


def _windows(recs):
    """-> {(start_h, end_h): {'mean': rec, 'sd': rec}}"""
    out = {}
    for r in recs:
        m = _SD.search(r.line)
        kind = "sd"
        if not m:
            m = _MEAN.search(r.line)
            kind = "mean"
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        out.setdefault(key, {})[kind] = r
    return out


def _best_window(windows, day_start_h, day_end_h):
    """The TMAX window overlapping the local day most.

    NBM publishes TMAX over 12-hour windows, which is the daytime maximum
    period rather than a local calendar day. The daily high is almost always
    set in the afternoon, so the window with the greatest overlap is the
    right one -- but it is an approximation, and on a day whose high comes at
    2am from a warm front it is the wrong answer.
    """
    best, best_overlap = None, 0.0
    for (a, b), parts in windows.items():
        if "mean" not in parts:
            continue
        overlap = min(b, day_end_h) - max(a, day_start_h)
        if overlap > best_overlap:
            best, best_overlap = (a, b), overlap
    return best


def fetch(cities, cfg, day_offsets=(0, 1), max_fhour=60):
    """-> {city: {offset: {'mean_f': x, 'sd_f': y}}}"""
    if not cfg.get("enabled", True):
        return {}

    session = requests.Session()
    out = {c["name"]: {} for c in cities}
    catalogues = {}
    samplers = {}

    groups = {}
    for c in cities:
        for off in day_offsets:
            start_utc, end_utc = local_day_window(c["tz"], off)
            picked = aligned_cycle(cfg["cycles"], LAG_HOURS, start_utc)
            if picked is None:
                continue
            ymd, cc, cycle_dt = picked
            s_h = round((start_utc - cycle_dt).total_seconds() / 3600.0)
            e_h = round((end_utc - cycle_dt).total_seconds() / 3600.0)
            if e_h > max_fhour or e_h <= 0:
                continue
            groups.setdefault((ymd, cc, s_h, e_h), []).append((c, off))

    for (ymd, cc, s_h, e_h), members in sorted(groups.items()):
        key = (ymd, cc)
        if key not in catalogues:
            cat = []
            for fh in range(6, max_fhour + 1, 6):
                url = f"{cfg['base'].rstrip('/')}/" + cfg["pattern"].format(
                    ymd=ymd, cc=f"{cc:02d}", fff=f"{fh:03d}", ff=f"{fh:02d}")
                try:
                    for r in read_idx(url, session):
                        cat.append((url, r))
                except Exception:
                    continue
            catalogues[key] = cat
        cat = catalogues[key]
        if not cat:
            continue

        url_of = {id(r): u for u, r in cat}
        windows = _windows([r for _, r in cat])
        win = _best_window(windows, s_h, e_h)
        if win is None:
            continue
        parts = windows[win]

        def sample(rec):
            k = (url_of[id(rec)], rec.offset)
            if k not in samplers:
                samplers[k] = sampler_from_bytes(
                    fetch_record(url_of[id(rec)], rec, session))
            return samplers[k]

        try:
            sm_mean = sample(parts["mean"])
            sm_sd = sample(parts["sd"]) if "sd" in parts else None
        except Exception as exc:  # noqa: BLE001
            print(f"  nbm_temp: record fetch failed ({exc})")
            continue

        for c, off in members:
            mu = sm_mean.at(c["lat"], c["lon"])
            if mu is None:
                continue
            # GRIB carries kelvin; a standard deviation in kelvin is the same
            # magnitude in celsius, so it scales by 9/5 with no offset.
            mean_f = (mu - 273.15) * 9.0 / 5.0 + 32.0 if mu > 200 else mu
            sd = sm_sd.at(c["lat"], c["lon"]) if sm_sd else None
            sd_f = sd * 9.0 / 5.0 if sd is not None else None
            rec = {"mean_f": round(mean_f, 2)}
            if sd_f is not None and sd_f > 0:
                rec["sd_f"] = round(sd_f, 2)
            out[c["name"]][off] = rec

    got = sum(1 for v in out.values() if v)
    with_sd = sum(1 for v in out.values()
                  for r in v.values() if r.get("sd_f") is not None)
    print(f"  nbm_temp: {got} cities, {with_sd} with an NBM ensemble sigma")
    return out
