"""Predictive distributions for a daily high temperature.

Rain is one binary. Temperature is a set of mutually exclusive brackets, so
a single number is useless -- you need the whole distribution and then you
integrate it over each bracket.

Quantile averaging sets the blend's location and shape; the builder also
retains model disagreement when setting its spread. Whole-degree brackets
use half-degree boundaries. Gaussian tails and spread assumptions remain
subject to out-of-sample verification. Observations impose a physical floor.

"""

from __future__ import annotations

import math
from bisect import bisect_left

QUANTILES = [0.01, 0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
             0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99]

_SQRT2 = math.sqrt(2.0)


def _ndtr(x):
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def members_to_quantiles(members, qs=QUANTILES):
    """Empirical quantiles of a member list, linear interpolation."""
    v = sorted(m for m in members if isinstance(m,(int,float)) and math.isfinite(m))
    if len(v) < 3:
        return None
    out = []
    n = len(v)
    for q in qs:
        pos = q * (n - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        out.append(v[lo] * (1 - frac) + v[hi] * frac)
    return out


def normal_quantiles(mu, sigma, qs=QUANTILES):
    """Turn a deterministic forecast + an error sigma into a distribution.

    Used for MOS and NDFD, which give a single number. sigma should come from
    that source's historical mean absolute error at this station, not a guess.
    """
    return [mu + sigma * _probit(q) for q in qs]


def _probit(p):
    """Inverse normal CDF (Acklam's rational approximation)."""
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def blend_quantiles(sets, weights):
    """Vincentize: weighted mean of quantile curves, level by level.

    `sets` is a list of equal-length quantile lists, `weights` matching.
    Returns one quantile list. This is the step that keeps the blended
    distribution as sharp as its inputs instead of smearing them together.
    """
    pairs = [(s, w) for s, w in zip(sets, weights) if s]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    n = len(pairs[0][0])
    return [
        sum(s[i] * w for s, w in pairs) / total
        for i in range(n)
    ]


def adjust(quants, bias=0.0, spread_factor=1.0):
    """Shift and rescale a quantile curve.

    `bias` is the station correction: the standing difference between the
    models' gridded 2m temperature and what the climate report prints. It is
    usually the largest single correction on the board and it is city-specific
    -- Central Park runs cooler than the surrounding grid, airport stations
    run warmer.

    `spread_factor` > 1 widens. Ensembles under-disperse 2m temperature, so
    expect to land above 1.0 once you fit it.
    """
    if not quants:
        return None
    mid = quants[len(quants) // 2]
    return [bias + mid + (q - mid) * spread_factor for q in quants]


class Dist:
    """A continuous distribution defined by a quantile curve, with Gaussian tails."""

    def __init__(self, quants, qs=QUANTILES, floor=None):
        self.floor = floor
        self.v = list(quants)
        self.q = list(qs)
        # Tail sigmas fitted from the outermost quantile pairs, so the tail
        # matches the body's local spread rather than an arbitrary constant.
        lo_z, lo2_z = _probit(self.q[0]), _probit(self.q[1])
        hi_z, hi2_z = _probit(self.q[-1]), _probit(self.q[-2])
        self.lo_sigma = max((self.v[1] - self.v[0]) / (lo2_z - lo_z), 0.3)
        self.hi_sigma = max((self.v[-1] - self.v[-2]) / (hi_z - hi2_z), 0.3)

    def cdf(self, x):
        v, q = self.v, self.q
        if self.floor is not None and x <= self.floor:
            return 0.0
        if x <= v[0]:
            z = (x - v[0]) / self.lo_sigma + _probit(q[0])
            return min(_ndtr(z), q[0])
        if x >= v[-1]:
            z = (x - v[-1]) / self.hi_sigma + _probit(q[-1])
            return max(_ndtr(z), q[-1])
        i = bisect_left(v, x)
        if i == 0:
            return q[0]
        x0, x1 = v[i - 1], v[i]
        q0, q1 = q[i - 1], q[i]
        if x1 == x0:
            return q1
        return q0 + (q1 - q0) * (x - x0) / (x1 - x0)

    def prob_between(self, lo_int, hi_int):
        """P(reported whole-degree high is in [lo_int, hi_int]).

        lo_int=None means open below, hi_int=None means open above.
        The +/- 0.5 is the continuity correction for whole-degree reporting.
        """
        lo = -math.inf if lo_int is None else lo_int - 0.5
        hi = math.inf if hi_int is None else hi_int + 0.5
        a = 0.0 if lo == -math.inf else self.cdf(lo)
        b = 1.0 if hi == math.inf else self.cdf(hi)
        return max(0.0, min(1.0, b - a))

    def median(self):
        return self.v[len(self.v) // 2]

    def interval(self, width=0.80):
        lo_q, hi_q = (1 - width) / 2, 1 - (1 - width) / 2
        return self.quantile(lo_q), self.quantile(hi_q)

    def quantile(self, p):
        v, q = self.v, self.q
        if p <= q[0]:
            value = v[0] + self.lo_sigma * (_probit(p) - _probit(q[0]))
            return max(self.floor,value) if self.floor is not None else value
        if p >= q[-1]:
            return v[-1] + self.hi_sigma * (_probit(p) - _probit(q[-1]))
        i = bisect_left(q, p)
        if i == 0:
            return v[0]
        q0, q1 = q[i - 1], q[i]
        v0, v1 = v[i - 1], v[i]
        if q1 == q0:
            return v1
        return v0 + (v1 - v0) * (p - q0) / (q1 - q0)


def apply_observation(quants, obs_max, heat_left=None, tolerance=0.5,
                      min_spread=1.4, qs=QUANTILES, remaining=None):
    """Condition on the observed maximum and remaining-hour trajectories.

    Without trajectories only apply the floor; the clock is not evidence
    that the original forecast will occur. Residual settlement uncertainty
    is added before truncation so it cannot undo the physical lower bound.
    """
    if not quants:
        return None
    if obs_max is None:
        return list(quants)
    floor = obs_max - tolerance
    if remaining is not None:
        values = [max(obs_max, v) for v in remaining if v is not None and math.isfinite(v)]
        if not values:
            values = [obs_max] * 3
        base = members_to_quantiles(values) or [obs_max] * len(qs)
    else:
        base = list(quants)
    sigma = max(0, min_spread) / (_probit(.99) - _probit(.01))
    out = [max(floor, v + sigma * _probit(p)) for v,p in zip(base,qs)]
    return sorted(out)


def c_to_f(c):
    return None if c is None else c * 9.0 / 5.0 + 32.0
