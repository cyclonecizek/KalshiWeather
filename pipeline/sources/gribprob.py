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

import requests

from ..gribtools import (fetch_record, pick_window_records, read_idx,
                         sampler_from_bytes)
from ..util import latest_cycle, local_day_window, stitch_pops

# Rough wall-clock delay from cycle time to data being on NOMADS.
LAG_HOURS = {"HREF": 3.5, "REFS": 3.5, "NBM": 2.0}


def fetch(model_key, cities, cfg, rho=0.5, day_offsets=(0, 1),
          max_fhour=60, fhour_step=1):
    """-> {city: {offset: prob}}, plus the cycle used."""
    session = requests.Session()

    try:
        ymd, cc = latest_cycle(cfg["cycles"], LAG_HOURS.get(model_key, 3.0))
    except Exception as exc:  # noqa: BLE001
        print(f"  {model_key}: no cycle ({exc})")
        return {}, None

    from datetime import datetime, timezone
    cycle_dt = datetime.strptime(f"{ymd}{cc:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc)

    # Build the inventory once. Records live across many f-hour files, so
    # collect (url, record) pairs and let the window picker choose.
    catalogue = []
    for fh in range(fhour_step, max_fhour + 1, fhour_step):
        url = f"{cfg['base'].rstrip('/')}/" + cfg["pattern"].format(
            ymd=ymd, cc=f"{cc:02d}", fff=f"{fh:03d}", ff=f"{fh:02d}")
        try:
            recs = read_idx(url, session)
        except Exception:
            continue
        for r in recs:
            catalogue.append((url, r))

    if not catalogue:
        print(f"  {model_key}: no inventory found for {ymd} {cc:02d}Z "
              f"(path may have moved -- run pipeline.probe)")
        return {}, None

    recs_only = [r for _, r in catalogue]
    url_of = {id(r): u for u, r in catalogue}

    # One decode per needed record, shared across all cities.
    sampler_cache = {}

    def sampler_for(rec):
        if id(rec) not in sampler_cache:
            blob = fetch_record(url_of[id(rec)], rec, session)
            sampler_cache[id(rec)] = sampler_from_bytes(blob)
        return sampler_cache[id(rec)]

    out = {c["name"]: {} for c in cities}

    for off in day_offsets:
        # Each city has its own local day, so group cities by identical window
        # to avoid re-picking records per city.
        by_window = {}
        for c in cities:
            start_utc, end_utc = local_day_window(c["tz"], off)
            s_h = (start_utc - cycle_dt).total_seconds() / 3600.0
            e_h = (end_utc - cycle_dt).total_seconds() / 3600.0
            by_window.setdefault((round(s_h), round(e_h)), []).append(c)

        for (s_h, e_h), group in by_window.items():
            if e_h > max_fhour or e_h <= 0:
                continue
            chosen = pick_window_records(
                recs_only, cfg["idx_regex"], cc, s_h, e_h)
            if not chosen:
                continue
            try:
                samplers = [sampler_for(r) for r in chosen]
            except Exception as exc:  # noqa: BLE001
                print(f"  {model_key}: record fetch failed ({exc})")
                continue

            for c in group:
                parts = []
                for sm in samplers:
                    v = sm.at(c["lat"], c["lon"])
                    if v is None:
                        continue
                    # Fields are published as percent; some builds emit 0-1.
                    parts.append(v / 100.0 if v > 1.0 else v)
                p = stitch_pops(parts, rho=rho)
                if p is not None:
                    out[c["name"]][off] = min(max(p, 0.0), 1.0)

    n = sum(1 for v in out.values() if v)
    print(f"  {model_key}: {n} cities from {ymd} {cc:02d}Z")
    return out, f"{ymd}{cc:02d}"
