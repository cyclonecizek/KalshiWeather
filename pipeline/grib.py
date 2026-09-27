"""Pull single grid-point values out of remote GRIB2 files.

Only the records we need are downloaded (HTTP Range from the .idx inventory),
then eccodes finds the nearest grid point. Lambert-conformal u/v that are
grid-relative (HRRR, RRFS, HREF, REFS) are rotated to earth-relative."""
from __future__ import annotations

import math
import os
import re
import tempfile
import threading
import time

from .common import SESSION, log

try:
    import eccodes
    eccodes.codes_grib_multi_support_on()
except ImportError:  # probe.py can still list inventories without it
    eccodes = None

# Standard wind records. First instantaneous match in the inventory wins.
WIND_FIELDS = {
    "u10": r":UGRD:10 m above ground:",
    "v10": r":VGRD:10 m above ground:",
    "u80": r":UGRD:80 m above ground:",
    "v80": r":VGRD:80 m above ground:",
    "w10": r":WIND:10 m above ground:",
    "w80": r":WIND:80 m above ground:",
    "g0": r":GUST:surface:",
}
# Reject time-processed records (hourly max, averages) and probability records.
_SKIP = re.compile(r"(\bmax\b|\bmin\b|\bave\b|\bacc\b|prob|%)", re.I)


class _RateLimit:
    """Evenly spaced requests. NOMADS blocks IPs that exceed ~120 hits/minute."""

    def __init__(self, per_minute: float):
        self.gap = 60.0 / per_minute
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


NOMADS_LIMIT = _RateLimit(90)


class Missing(Exception):
    """File or inventory not published (yet)."""


def exists(url: str) -> bool:
    try:
        if "nomads.ncep.noaa.gov" in url:
            NOMADS_LIMIT.wait()
        r = SESSION.head(url, timeout=15, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def read_idx(idx_url: str) -> list[tuple[str, int, int | None, str]]:
    """Returns [(record_no, start, end_or_None, description)]."""
    r = _get(idx_url, timeout=30)
    if r.status_code in (403, 404):
        raise Missing(idx_url)
    r.raise_for_status()
    recs = []
    for line in r.text.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        try:
            start = int(parts[1])
        except ValueError:
            continue
        recs.append((parts[0], start, ":" + ":".join(parts[2:])))
    out = []
    for i, (no, start, desc) in enumerate(recs):
        end = None
        for j in range(i + 1, len(recs)):       # sub-messages share an offset
            if recs[j][1] > start:
                end = recs[j][1] - 1
                break
        out.append((no, start, end, desc))
    return out


def match_fields(inv, wanted: dict[str, str]) -> dict[str, tuple]:
    found = {}
    for name, pat in wanted.items():
        rx = re.compile(pat)
        for rec in inv:
            m = rx.search(rec[3])
            if m and not _SKIP.search(rec[3][m.end():]):
                found[name] = rec
                break
    return found


def _nearest(gid, lat, lon):
    for lo in (lon, lon % 360.0):
        try:
            res = eccodes.codes_grib_find_nearest(gid, lat, lo)
            r = res[0]
            val = r["value"] if isinstance(r, dict) else getattr(r, "value")
            return float(val)
        except Exception:
            continue
    raise RuntimeError("nearest-point lookup failed")


def _grid_meta(gid) -> dict:
    meta = {"grid": eccodes.codes_get(gid, "gridType")}
    try:
        meta["rel"] = int(eccodes.codes_get(gid, "uvRelativeToGrid"))
    except Exception:
        meta["rel"] = 0
    if meta["grid"] == "lambert":
        meta["lov"] = float(eccodes.codes_get(gid, "LoVInDegrees"))
        meta["latin1"] = float(eccodes.codes_get(gid, "Latin1InDegrees"))
    return meta


def _decode_all(blob: bytes, lat: float, lon: float) -> list[tuple]:
    """(value, meta) for every GRIB message (and sub-message) in blob, in order."""
    out = []
    fd, path = tempfile.mkstemp(suffix=".grib2")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        with open(path, "rb") as f:
            while True:
                gid = eccodes.codes_grib_new_from_file(f)
                if gid is None:
                    break
                try:
                    val = _nearest(gid, lat, lon)
                    miss = eccodes.codes_get(gid, "missingValue")
                    if abs(val - miss) < 1e-6 or abs(val) > 1e10:
                        val = None
                    out.append((val, _grid_meta(gid)))
                finally:
                    eccodes.codes_release(gid)
    finally:
        os.unlink(path)
    return out


def _get(url: str, **kw):
    if "nomads.ncep.noaa.gov" in url:
        NOMADS_LIMIT.wait()
    return SESSION.get(url, **kw)


def fetch_point(grib_url: str, wanted: dict[str, str], lat: float, lon: float,
                idx_url: str | None = None) -> tuple[dict, dict]:
    """Returns (values, meta) for each wanted field found. Raises Missing.

    Adjacent records are fetched in one range request to keep hit counts low."""
    if eccodes is None:
        raise RuntimeError("eccodes not installed")
    inv = read_idx(idx_url or grib_url + ".idx")
    found = sorted(match_fields(inv, wanted).items(), key=lambda kv: kv[1][1])

    groups: list[list] = []          # runs of contiguous, whole-message records
    for name, rec in found:
        whole = "." not in rec[0]
        g = groups[-1] if groups else None
        if (g and whole and g[-1][2] and "." not in g[-1][1][0]
                and g[-1][1][2] is not None and rec[1] == g[-1][1][2] + 1):
            g.append((name, rec, whole))
        else:
            groups.append([(name, rec, whole)])

    vals, meta = {}, {}
    for g in groups:
        start, end = g[0][1][1], g[-1][1][2]
        rng = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
        r = _get(grib_url, headers={"Range": rng}, timeout=60)
        if r.status_code in (403, 404, 416):
            raise Missing(grib_url)
        r.raise_for_status()
        decoded = _decode_all(r.content, lat, lon)
        if len(g) == 1 and not g[0][2]:                  # one sub-message record
            k = int(g[0][1][0].split(".")[1]) - 1
            pairs = [(g[0][0], decoded[k] if k < len(decoded) else (None, {}))]
        else:
            pairs = [(name, decoded[i] if i < len(decoded) else (None, {})) for i, (name, _, _) in enumerate(g)]
        for name, (v, m) in pairs:
            vals[name], meta[name] = v, m
    return vals, meta


def earth_relative(u: float, v: float, meta: dict, lon: float) -> tuple[float, float] | None:
    """Rotate grid-relative Lambert u/v to earth-relative (NCEP formula)."""
    if not meta or not meta.get("rel"):
        return u, v
    if meta.get("grid") != "lambert":
        log.warning("grid-relative winds on %s grid not handled; dropping", meta.get("grid"))
        return None
    lov = meta["lov"] if meta["lov"] <= 180 else meta["lov"] - 360
    angle = math.sin(math.radians(meta["latin1"])) * math.radians(lon - lov)
    c, s = math.cos(angle), math.sin(angle)
    return c * u + s * v, -s * u + c * v
