# Plume Constraint

Hourly probability-of-violation dashboard for a wind constraint at a fixed
height, built from every available model: HRRR and RRFS (time-lagged), REFS,
HREF members, NAM 3 km, NBM, NDFD, meteoblue, and the ECMWF, AIFS, GEFS, ICON and GEM ensembles.

## Set up

1. Push this folder to a new GitHub repository.
2. Settings > Pages: deploy from branch `main`, folder `/docs`.
3. Optional: Settings > Secrets > Actions: add `METEOBLUE_API_KEY`.
4. Actions > Update plume > Run workflow. A manual run also runs the probe job;
   read its log to confirm each source resolved and which wind levels exist.
5. After that it runs every hour at :20 on its own.

`docs/data/plume.json` ships with demo data so the page renders before the
first run; the first run overwrites it.

## Change the site or constraint

Everything is in `config.yaml`: location, height, thresholds, direction arcs,
time window, and per-source weights. Weights are split across a source's
members each hour, so member count does not decide influence.

## Run locally

    pip install -r requirements.txt
    python -m pipeline.probe            # check sources
    python -m pipeline.run              # full build
    python -m pipeline.run --only hrrr  # one source
    cd docs && python -m http.server    # view at localhost:8000

## Notes

- HREF is built from its member models (HiResW ARW, ARW mem2, FV3, NAM 3 km
  and HRRR, each current plus previous cycle), because NCEP only publishes
  HREF mean and probability products. NAM 3 km is also its own source.
  All of these retire when RRFS/REFS go operational and will then show
  "missing".
- NOMADS requests are rate-limited to 90 per minute; adjacent GRIB records
  are fetched in one request.
- RRFS and REFS are configured to try `prod`, then `v1.0`, then `para` on
  NOMADS, so the October 2026 cutover needs no change unless NCO uses a path
  not in that list. HREF drops out on its own once it is retired.
- NCEP GRIB winds on Lambert grids are rotated from grid-relative to
  earth-relative before use.
- `cache/points.json` holds point values already extracted from GRIB files so
  each run only downloads new cycles. It prunes itself after four days.
- If every source fails, the previous `plume.json` is kept and the page shows
  a stale-data warning after 90 minutes.
