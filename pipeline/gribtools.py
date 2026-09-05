"""Pull single GRIB2 records out of NOMADS without downloading whole files.

Every NOMADS GRIB file has a sibling `.idx` inventory listing each record's
byte offset. Read the inventory, find the one record you want, then issue an
HTTP Range request for just those bytes. An HREF prob file is ~200 MB; the
one record you need is a few hundred KB. On a GitHub Actions runner that is
the difference between a job that finishes and one that times out.
"""

from __future__ import annotations

import io
import re
import tempfile
from dataclasses import dataclass

import numpy as np
import requests

UA = {"User-Agent": "kalshi-rain-board/1.0"}


@dataclass
class IdxRecord:
    num: int
    offset: int
    length: int | None
    line: str
    acc_start: int | None = None
    acc_end: int | None = None


_ACC_RE = re.compile(r"(\d+)-(\d+)\s+hour acc fcst")
_SINGLE_ACC_RE = re.compile(r"(\d+)\s+hour acc fcst")


def read_idx(url: str, session: requests.Session | None = None):
    """Parse a `.idx` inventory. Returns records with byte ranges resolved."""
    s = session or requests.Session()
    r = s.get(url + ".idx", headers=UA, timeout=60)
    r.raise_for_status()

    raw = []
    for line in r.text.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        raw.append((int(parts[0]), int(parts[1]), line))

    recs = []
    for i, (num, offset, line) in enumerate(raw):
        length = raw[i + 1][1] - offset if i + 1 < len(raw) else None
        rec = IdxRecord(num=num, offset=offset, length=length, line=line)
        m = _ACC_RE.search(line)
        if m:
            rec.acc_start, rec.acc_end = int(m.group(1)), int(m.group(2))
        else:
            m2 = _SINGLE_ACC_RE.search(line)
            if m2:
                rec.acc_end = int(m2.group(1))
                rec.acc_start = 0
        recs.append(rec)
    return recs


def fetch_record(url: str, rec: IdxRecord, session: requests.Session | None = None):
    s = session or requests.Session()
    end = "" if rec.length is None else str(rec.offset + rec.length - 1)
    headers = dict(UA)
    headers["Range"] = f"bytes={rec.offset}-{end}"
    r = s.get(url, headers=headers, timeout=180)
    r.raise_for_status()
    return r.content


# Every record in a model shares one grid, so the tree is cached on grid
# identity rather than rebuilt per record. NBM CONUS is ~2.5M points: a tree
# costs seconds and hundreds of MB, and building twenty of them is what turned
# a 4-minute job into a hang.
_TREES = {}
_LATLONS = {}


def _grid_key(lats, lons):
    return (lats.shape, float(lats.flat[0]), float(lons.flat[0]),
            float(lats.flat[-1]), float(lons.flat[-1]))


class Sampler:
    """Nearest-gridpoint lookup on an unstructured/curvilinear GRIB grid."""

    def __init__(self, values: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                 kind: str = "raw"):
        from scipy.spatial import cKDTree

        self.values = values.ravel()
        # Unit handling is OPT-IN, and only for probabilities.
        #
        # This used to auto-detect percent-vs-fraction from the field maximum
        # for every field. That is right for a probability and catastrophic
        # for anything else: NBM temperature is in kelvin, so a 320 K field
        # looked like percent, got multiplied by 0.01, and produced a 3 F
        # forecast that dragged the blended median down by 30 degrees.
        #
        # A field's units are a property of the field, not something to infer
        # from its values, so the caller now says which it is.
        self.kind = kind
        if kind == "probability":
            finite = self.values[np.isfinite(self.values)]
            vmax = float(finite.max()) if finite.size else 0.0
            self.scale = 0.01 if vmax > 1.0 else 1.0
            self.units_note = "percent" if self.scale == 0.01 else "fraction"
        else:
            self.scale = 1.0
            self.units_note = "raw (unscaled)"
        key = _grid_key(lats, lons)
        tree = _TREES.get(key)
        if tree is None:
            lat = np.radians(lats.ravel())
            lon = np.radians(np.where(lons > 180, lons - 360, lons).ravel())
            xyz = np.column_stack([
                np.cos(lat) * np.cos(lon),
                np.cos(lat) * np.sin(lon),
                np.sin(lat),
            ])
            tree = cKDTree(xyz)
            _TREES[key] = tree
            print(f"      built KD-tree for {lats.size:,}-point grid")
        self.tree = tree

    def at(self, lat_deg: float, lon_deg: float):
        lat, lon = np.radians(lat_deg), np.radians(lon_deg)
        q = np.array([
            np.cos(lat) * np.cos(lon),
            np.cos(lat) * np.sin(lon),
            np.sin(lat),
        ])
        _, idx = self.tree.query(q)
        v = self.values[idx]
        if np.ma.is_masked(v) or np.isnan(v):
            return None
        return float(v) * self.scale


def sampler_from_bytes(blob: bytes, kind: str = "raw") -> Sampler:
    """Decode one GRIB record and wrap it in a Sampler.

    `kind="probability"` enables percent-vs-fraction detection. Leave it at
    "raw" for temperatures, wind, anything with real units.
    """
    import os

    import pygrib

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as fh:
        fh.write(blob)
        path = fh.name
    try:
        grbs = pygrib.open(path)
        msg = grbs.message(1)
        vals = msg.values
        # latlons() recomputes ~2.5M coordinate pairs every call. Every record
        # in a model shares one grid, so key on cheap GRIB metadata and only
        # pay for it once.
        try:
            meta = (msg.Ni, msg.Nj, msg.gridType,
                    round(float(msg.latitudeOfFirstGridPointInDegrees), 4),
                    round(float(msg.longitudeOfFirstGridPointInDegrees), 4))
        except Exception:  # noqa: BLE001
            meta = None
        if meta is not None and meta in _LATLONS:
            lats, lons = _LATLONS[meta]
        else:
            lats, lons = msg.latlons()
            if meta is not None:
                _LATLONS[meta] = (lats, lons)
                print(f"      cached grid geometry {msg.Ni}x{msg.Nj}")
        grbs.close()
        return Sampler(np.asarray(vals), lats, lons, kind=kind)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def candidate_fhours(want_start_h, want_end_h, step=6, max_fhour=60):
    """Only the forecast hours that could hold a record covering the window.

    Reading every inventory from f001 to f060 costs 60 HTTP round trips per
    cycle. With cities across five timezones that is several hundred fetches
    before any actual data is read. An accumulation record ending at hour H
    lives in the f{H} file, so only multiples of `step` inside the window --
    plus its exact end -- can possibly matter.
    """
    lo = max(step, int(want_start_h))
    hi = min(max_fhour, int(want_end_h))
    hours = {h for h in range(lo, hi + 1) if h % step == 0}
    if 0 < want_end_h <= max_fhour:
        hours.add(int(want_end_h))
    return sorted(hours)


def pick_window_records(recs, idx_regex: str, cycle_hour: int,
                        want_start_h: float, want_end_h: float,
                        tolerance_h: float = 3.0):
    """Choose the record(s) covering a target window, in hours past cycle.

    Prefers a single accumulation record that matches the local day within
    `tolerance_h`. Falls back to a set of shorter non-overlapping records
    that tile the window, which the caller then stitches.
    """
    pat = re.compile(idx_regex)
    cands = [
        r for r in recs
        if pat.search(r.line) and r.acc_start is not None and r.acc_end is not None
    ]
    if not cands:
        return []

    exact = [
        r for r in cands
        if abs(r.acc_start - want_start_h) <= tolerance_h
        and abs(r.acc_end - want_end_h) <= tolerance_h
    ]
    if exact:
        exact.sort(key=lambda r: (r.acc_end - r.acc_start))
        return [exact[-1]]

    inside = sorted(
        [r for r in cands
         if r.acc_start >= want_start_h - tolerance_h
         and r.acc_end <= want_end_h + tolerance_h],
        key=lambda r: r.acc_start,
    )
    tiled, cursor = [], None
    for r in inside:
        if cursor is None or r.acc_start >= cursor:
            tiled.append(r)
            cursor = r.acc_end
    return tiled


def window_coverage(records, want_start_h, want_end_h):
    """Fraction of the target window the chosen records actually span.

    Without this a set of four 1-hour records looks like a valid tiling of a
    24-hour day, and the resulting "daily probability" describes four hours.
    Nothing downstream can detect that -- the number is a plausible-looking
    float either way.
    """
    span = max(1e-6, want_end_h - want_start_h)
    covered = 0.0
    for r in records:
        if r.acc_start is None or r.acc_end is None:
            continue
        lo = max(r.acc_start, want_start_h)
        hi = min(r.acc_end, want_end_h)
        if hi > lo:
            covered += hi - lo
    return min(1.0, covered / span)
