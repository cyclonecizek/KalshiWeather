"""Verify every module the pipeline needs is present and current.

Python's import system reports exactly one missing name per run, so a repo
that is several files behind takes several runs to diagnose -- fix one, hit
the next. Each round costs a full workflow run to learn one fact.

This checks the whole manifest in one pass and prints every problem at once,
so a stale checkout produces one complete list instead of a sequence of
single-symbol errors.

    python -m pipeline.selfcheck
"""

from __future__ import annotations

import importlib
import sys

# module -> symbols that must exist in it. Add an entry whenever a builder
# starts importing something new; that is what keeps this honest.
MANIFEST = {
    "pipeline.run": ["run", "prepare", "validate"],
    "pipeline.policy": ["eligibility", "allocate"],
    "pipeline.sources.hourly": ["fetch", "complete_total", "rain_probability"],
    "pipeline.performance": ["publish", "score_day"],
    "pipeline.adjustments": ["create"],
    "pipeline.settlement": ["configure_cities", "verify"],
    "pipeline.util": [
        "load_yaml", "local_day_window", "local_date_str", "aligned_cycle",
        "latest_cycle", "stitch_pops", "member_fraction", "kalshi_fee_cents",
        "edge_for_side", "kelly_fraction", "apply_calibration",
    ],
    "pipeline.kalshi": [
        "Kalshi", "SCHEMA_VERSION", "normalize", "book_depth",
        "effective_fee_rate", "classify_cadence", "pick_city_market",
    ],
    "pipeline.blend": ["blend", "evaluate", "variants"],
    "pipeline.brackets": [
        "build_ladder", "implied_distribution", "implied_quantiles",
        "check_arbitrage", "coverage_gaps", "parse_bracket",
    ],
    "pipeline.tempdist": [
        "Dist", "adjust", "apply_observation", "blend_quantiles",
        "members_to_quantiles", "normal_quantiles",
    ],
    "pipeline.gribtools": [
        "read_idx", "fetch_record", "sampler_from_bytes",
        "pick_window_records", "candidate_fhours", "window_coverage",
    ],
    "pipeline.sources.openmeteo": ["fetch"],
    "pipeline.sources.nws_text": ["fetch_ndfd", "fetch_mos"],
    "pipeline.sources.gribprob": ["fetch"],
    "pipeline.sources.temp_sources": [
        "fetch_openmeteo", "fetch_ndfd_maxt", "fetch_mos_maxt",
    ],
    "pipeline.sources.meteoblue": [
        "fetch", "coverage_multiplier", "predictability_widening",
    ],
    "pipeline.sources.nbm_temp": ["fetch"],
    "pipeline.sources.observations": [
        "fetch", "condition_rain", "heating_remaining",
    ],
}

# Sources the board can run without. A missing one degrades the board;
# anything else stops it.
OPTIONAL = {"pipeline.sources.observations", "pipeline.sources.meteoblue",
            "pipeline.sources.nbm_temp"}


def check():
    """-> (fatal_problems, warnings)"""
    fatal, warn = [], []
    for mod, symbols in MANIFEST.items():
        try:
            m = importlib.import_module(mod)
        except ImportError as exc:
            path = mod.replace(".", "/") + ".py"
            msg = f"{path} is MISSING ({exc})"
            (warn if mod in OPTIONAL else fatal).append(msg)
            continue
        absent = [s for s in symbols if not hasattr(m, s)]
        if absent:
            path = mod.replace(".", "/") + ".py"
            msg = (f"{path} is STALE -- missing {', '.join(absent)}. "
                   f"Replace this file with the current version.")
            (warn if mod in OPTIONAL else fatal).append(msg)
    return fatal, warn


def report(prefix="  "):
    """Print findings. -> True when the pipeline can run."""
    fatal, warn = check()
    for w in warn:
        print(f"{prefix}optional: {w}")
    for f in fatal:
        print(f"{prefix}PROBLEM: {f}")
    if fatal:
        print(f"{prefix}{len(fatal)} file(s) need replacing. All of them are "
              f"listed above -- fix them together rather than one run at a "
              f"time.")
        return False
    if not warn:
        print(f"{prefix}selfcheck: all modules present and current")
    return True


if __name__ == "__main__":
    sys.exit(0 if report("") else 1)
