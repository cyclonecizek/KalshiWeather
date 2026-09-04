"""HREF, REFS and NBM probability-of-0.01-inch fields.

All three publish a "probability that APCP exceeds 0.254 mm" field over an
accumulation window, so one driver handles them; only the URL pattern and the
inventory regex differ.

Two caveats worth carrying in your head while reading these numbers:

  1. HREF and REFS ensprod probabilities are NEIGHBOURHOOD maxima. They answer
     "does any grid point within roughly 40 km get 0.01 inch", not "does this
     one rain gauge get 0.01 inch". On scattered-convection days that runs
     high against a point station -- sometimes 15-20 points high. That is what
     the calibration offsets in settings.yml are for.

  2. The accumulation windows are fixed offsets from the model cycle, so they
     never line up exactly with a station's local midnight-to-midnight day.
     `pick_window_records` matches to within 3 hours and stitches shorter
     records when it has to.
"""

from __future__ import annotations

import time

import requests

from ..gribtools import (candidate_fhours, fetch_record, pick_window_records,
                         read_idx, sampler_from_bytes)
from ..util import aligned_cycle, local_day_window, stitch_pops

# Rough wall-clock delay from cycle time to data being on NOMADS.
LAG_HOURS = {"HREF": 3.5, "REFS": 3.5, "NBM": 2.0}


def fetch(model_key, cities, cfg, rho=0.5, day_offsets=(0, 1),
          max_fhour=60, fhour_step=6):
    """-> {city: {offset: prob}}, plus the cycle used."""
    session = requests.Session()
    lag = LAG_HOURS.get(model_key, 3.0)
    out = {c["name"]: {} for c in cities}
    catalogues = {}

    # Group by the cycle each city's window wants, rather than forcing one
    # cycle on everybody. For an hourly model this means every city gets a
    # run starting on its own local midnight.
    groups = {}
    for c in cities:
        for off in day_offsets:
            start_utc, end_utc = local_day_window(c["tz"], off)
            picked = aligned_cycle(cfg["cycles"], lag, start_utc)
            if picked is None:
                continue
            ymd, cc, cycle_dt = picked
            s_h = round((start_utc - cycle_dt).total_seconds() / 3600.0)
            e_h = round((end_utc - cycle_dt).total_seconds() / 3600.0)
            if e_h > max_fhour or e_h <= 0:
                continue
            groups.setdefault((ymd, cc, s_h, e_h), []).append((c, off))

    if not groups:
        print(f"  {model_key}: no usable cycle for any city")
        return {}, None

    def catalogue_for(ymd, cc, s_h, e_h):
        hours = candidate_fhours(s_h, e_h, fhour_step, max_fhour)
        key = (ymd, cc, tuple(hours))
        if key in catalogues:
            return catalogues[key]
        cat = []
        for fh in hours:
            url = f"{cfg['base'].rstrip('/')}/" + cfg["pattern"].format(
                ymd=ymd, cc=f"{cc:02d}", fff=f"{fh:03d}", ff=f"{fh:02d}")
            try:
                for r in read_idx(url, session):
                    cat.append((url, r))
            except Exception:
                continue
        catalogues[key] = cat
        return cat

    sampler_cache = {}

    def sampler_for(url, rec):
        k = (url, rec.offset)
        if k not in sampler_cache:
            sampler_cache[k] = sampler_from_bytes(
                fetch_record(url, rec, session))
        return sampler_cache[k]

    used_cycles = set()
    print(f"  {model_key}: {len(groups)} distinct windows to cover")
    t0 = time.monotonic()
    budget_s = cfg.get("budget_seconds", 420)
    for (ymd, cc, s_h, e_h), members in sorted(groups.items()):
        if time.monotonic() - t0 > budget_s:
            print(f"    budget of {budget_s}s spent; skipping remaining "
                  f"windows. A partial board beats a hung job.")
            break
        names = ", ".join(sorted({c["name"] for c, _ in members}))
        print(f"    {ymd} {cc:02d}Z f{s_h:03d}-f{e_h:03d}  ({names})")
        cat = catalogue_for(ymd, cc, s_h, e_h)
        if not cat:
            print(f"  {model_key}: no inventory for {ymd} {cc:02d}Z "
                  f"(path may have moved -- run pipeline.probe)")
            continue
        recs_only = [r for _, r in cat]
        url_of = {id(r): u for u, r in cat}

        chosen = pick_window_records(recs_only, cfg["idx_regex"], cc, s_h, e_h)
        if not chosen:
            continue
        try:
            samplers = [sampler_for(url_of[id(r)], r) for r in chosen]
        except Exception as exc:  # noqa: BLE001
            print(f"  {model_key}: record fetch failed ({exc})")
            continue
        used_cycles.add(f"{ymd}{cc:02d}")

        for c, off in members:
            parts = []
            for sm in samplers:
                v = sm.at(c["lat"], c["lon"])
                if v is None:
                    continue
                parts.append(v / 100.0 if v > 1.0 else v)
            p = stitch_pops(parts, rho=rho)
            if p is not None:
                out[c["name"]][off] = min(max(p, 0.0), 1.0)

    print(f"  {model_key}: finished in {time.monotonic()-t0:.0f}s")
    n = sum(1 for v in out.values() if v)
    print(f"  {model_key}: {n} cities, {len(groups)} windows, "
          f"cycles {sorted(used_cycles)}")
    return out, (sorted(used_cycles)[-1] if used_cycles else None)
