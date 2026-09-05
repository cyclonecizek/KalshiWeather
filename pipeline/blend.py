"""Turn a bag of model probabilities into one number, then into a trade.

The blending step exists because the five sources you started with are not
five independent opinions:

    MOS  ──┐
    HREF ──┼──> NBM ──> NDFD
           │
    (many others)

NBM ingests MOS and HREF. NDFD is NBM with a forecaster's hand on it. A flat
mean of {HREF, NBM, REFS, NDFD, MOS} counts the blend's view three times and
the convection-allowing view once and a half. So: mean within a family, then
weight across families.
"""

from __future__ import annotations

from .util import (apply_calibration, edge_for_side, kalshi_fee_cents,
                   kelly_fraction)


def blend(model_probs: dict, settings: dict, cam_multiplier: float = 1.0):
    """model_probs: {MODEL_KEY: prob}. -> dict with consensus and diagnostics.

    `cam_multiplier` scales the convection-allowing family only. HREF and
    REFS ensprod fields are neighbourhood maxima -- they answer "does
    anywhere within ~40 km get 0.01 inch". Multiplying by the observed wet
    area fraction converts that toward a point probability:

        P(point) ~= P(area) x E[wet fraction | anything wet]

    Comes from meteoblue's rainSPOT when enabled; 1.0 is a no-op.
    """
    fams = settings["families"]
    cal = settings.get("calibration", {})

    cam_members = set(fams.get("cam", {}).get("members", []))
    adjusted = {}
    for k, v in model_probs.items():
        if v is None:
            continue
        p = apply_calibration(v, cal.get(k, 0.0))
        if k in cam_members and cam_multiplier != 1.0:
            p = max(0.0, min(1.0, p * cam_multiplier))
        adjusted[k] = p

    fam_means, fam_weights = {}, {}
    for fam_key, fam in fams.items():
        vals = [adjusted[m] for m in fam["members"] if m in adjusted]
        if not vals:
            continue
        fam_means[fam_key] = sum(vals) / len(vals)
        fam_weights[fam_key] = fam["weight"]

    if not fam_means:
        return None

    total_w = sum(fam_weights.values())
    consensus = sum(
        fam_means[k] * fam_weights[k] for k in fam_means) / total_w

    vals = list(adjusted.values())
    spread = max(vals) - min(vals) if len(vals) > 1 else 0.0
    fam_spread = (max(fam_means.values()) - min(fam_means.values())
                  if len(fam_means) > 1 else 0.0)

    return {
        "consensus": consensus,
        "models": adjusted,
        "families": fam_means,
        "n_models": len(adjusted),
        "n_families": len(fam_means),
        "spread": spread,
        "family_spread": fam_spread,
        "low_confidence": len(fam_means) < settings.get(
            "min_families_for_signal", 2),
    }


def variants(model_probs: dict, settings: dict, publish_mlm=False):
    """Several competing consensuses from the same inputs, for scoring.

    The only honest way to answer "is meteoblue better than the blend" is to
    run both against real outcomes and compare Brier scores. A claim from a
    vendor's marketing page is not evidence about YOUR stations and YOUR
    settlement source.

    So every run records what each configuration WOULD have said. After a few
    weeks `pipeline.score` grades them side by side, and if mLM wins you can
    switch on evidence instead of instinct.

    Costs nothing extra: the model values are already fetched.
    """
    fams = settings["families"]
    cal = settings.get("calibration", {})
    adjusted = {k: apply_calibration(v, cal.get(k, 0.0))
                for k, v in model_probs.items() if v is not None}

    def mean_of(keys):
        vals = [adjusted[k] for k in keys if k in adjusted]
        return sum(vals) / len(vals) if vals else None

    out = {}
    full = blend(model_probs, settings)
    if full:
        out["blend_all"] = round(full["consensus"], 4)

    # Each family alone.
    for fam_key, fam in fams.items():
        v = mean_of(fam["members"])
        if v is None:
            continue
        # A solo mLM number IS meteoblue's forecast. Withhold it for the same
        # reason the model column is withheld -- this file is published.
        if fam_key == "mlm" and not publish_mlm:
            continue
        out[f"only_{fam_key}"] = round(v, 4)

    # The blend with mLM removed, so its marginal contribution is measurable
    # without ever publishing its value.
    no_mlm = {k: v for k, v in model_probs.items() if k != "METEOBLUE"}
    b2 = blend(no_mlm, settings)
    if b2:
        out["blend_without_mlm"] = round(b2["consensus"], 4)

    return out


def evaluate(consensus: float, quote: dict, settings: dict):
    """Which side to take, how much it's worth, and whether to flag it."""
    from .kalshi import effective_fee_rate
    ecfg = settings["edge"]
    mult = effective_fee_rate(quote.get("fee_multiplier"),
                              ecfg["fee_multiplier"])

    ev_yes = edge_for_side(consensus, quote["yes_ask"], mult)
    ev_no = edge_for_side(1.0 - consensus, quote["no_ask"], mult)

    side, ev, price = "YES", ev_yes, quote["yes_ask"]
    if ev_no is not None and (ev is None or ev_no > ev):
        side, ev, price = "NO", ev_no, quote["no_ask"]
    if ev is None:
        return None

    p_side = consensus if side == "YES" else 1.0 - consensus
    kelly = kelly_fraction(p_side, price, ecfg.get("kelly_cap", 0.25))

    # Liquidity gates: an 18-cent "edge" on a market with 30 contracts of
    # volume and a 9-cent spread is not an edge, it's an empty book.
    illiquid = (
        quote.get("liquidity", quote["volume"]) < ecfg["min_volume"]
        or quote["spread"] > ecfg["max_spread_cents"]
    )

    if illiquid:
        flag = "thin"
    elif ev >= ecfg["flag_high_cents"]:
        flag = "high"
    elif ev >= ecfg["flag_watch_cents"]:
        flag = "watch"
    else:
        flag = None

    return {
        "side": side,
        "price": price,
        "ev_cents": round(ev, 2),
        "ev_yes": None if ev_yes is None else round(ev_yes, 2),
        "ev_no": None if ev_no is None else round(ev_no, 2),
        "fee_cents": kalshi_fee_cents(price, mult),
        "kelly": round(kelly, 4),
        "gap_points": round((consensus * 100.0) - quote["mid"], 1),
        "flag": flag,
        "illiquid": illiquid,
    }
