"""Paired source research and chronological, review-only model candidates.

Unknown historical inputs are never reconstructed. Parameters are fitted only
on earlier dates whose settlements were already retrieved before evaluation.
The latest 20 dates are a monitoring holdout, not a permanent certification.
"""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
import random
import statistics as stats

from .tempdist import Dist, QUANTILES, _probit

MIN_TRAIN = 40
MIN_TEST = 20
MIN_FIT = 20
MIN_SPREAD = 20


def finite(x):
    return isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x)


def probability_scores(kind, ps, ys, quantiles=None, actual=None):
    if not ps or len(ps) != len(ys) or not all(finite(p) and 0 <= p <= 1 for p in ps):
        return None
    if kind in ('temperature', 'temperature_low') and (sum(ys) != 1 or abs(sum(ps) - 1) > 1e-6):
        return None
    loss = (-math.log(max(1e-8, ps[0] if ys[0] else 1 - ps[0])) if kind == 'rain'
            else -math.log(max(1e-8, sum(p*y for p, y in zip(ps, ys)))))
    result = dict(brier=sum((p-y)**2 for p, y in zip(ps, ys)), log_loss=loss,
                  pairs=list(zip(ps, ys)), bias_f=None, mae_f=None, coverage80=None)
    if quantiles and len(quantiles) == len(QUANTILES) and finite(actual):
        # Conventional forecast minus observed sign, explicitly labeled in UI.
        result.update(bias_f=quantiles[7]-actual, mae_f=abs(quantiles[7]-actual),
                      coverage80=quantiles[3] <= actual <= quantiles[11])
    return result


def record_for_day(record, day, outcomes):
    kind = record['kind']
    tickers = ([day['market']['ticker']] if kind == 'rain' else
               [b['market']['ticker'] for b in day['ladder']])
    # Research never uses a provisional or missing outcome.
    if any(outcomes.get(t, {}).get('status') != 'finalized' for t in tickers):
        return None
    ys = [outcomes[t]['result'] for t in tickers]
    qs = day.get('distribution', {}).get('quantiles')
    baseline = probability_scores(kind, [p for p, _ in record['pairs']], ys, qs, record['actual'])
    if baseline is None:
        return None
    variants = {}
    archived = day.get('experiments', {})
    if archived.get('version') == 1 and archived.get('tickers') == tickers:
        for key, value in archived.get('variants', {}).items():
            score = probability_scores(kind, value.get('probabilities'), ys,
                                       value.get('quantiles'), record['actual'])
            if score:
                variants[key] = dict(score, mode=value['mode'], model=value['model'],
                                     multiplier=value.get('multiplier'), weight_scope=value.get('weight_scope','member'), method='Common post-processing')
    else:
        # Historical raw source diagnostics remain useful, but cannot stand in
        # for full distributions or removal experiments with unknown settings.
        if kind == 'rain':
            for model, p in day.get('models', {}).items():
                score = probability_scores(kind, [p], ys)
                if score:
                    variants['legacy:' + model] = dict(score, mode='source', model=model,
                        multiplier=None, method='Archived input probability')
        elif finite(record['actual']):
            for model, diagnostic in day.get('diagnostics', {}).items():
                if model.startswith('_') or not isinstance(diagnostic, dict):
                    continue
                median = diagnostic.get('median', diagnostic.get('value'))
                if finite(median):
                    error = median - record['actual']
                    variants['legacy:' + model] = dict(brier=None, log_loss=None, pairs=[],
                        bias_f=error, mae_f=abs(error), coverage80=None, mode='source', model=model,
                        multiplier=None, method='Archived point forecast; probability unavailable')
    settled = [outcomes[t].get('retrieved_at') for t in tickers]
    return dict(record, baseline=baseline, variants=variants,
                settled_at=max(settled) if all(settled) else None,
                bounds=[(b['lo'], b['hi']) for b in day.get('ladder', [])],
                ceiling=day.get('distribution', {}).get('ceiling'),
                floor=day.get('distribution', {}).get('floor'))


def mean(values):
    values = [v for v in values if v is not None]
    return stats.mean(values) if values else None


def summarize_scores(scores):
    return {k: mean(s.get(k) for s in scores)
            for k in ('brier', 'log_loss', 'bias_f', 'mae_f', 'coverage80')}


def paired_interval(dated_differences, draws=400):
    """Three-date moving-block percentile interval, deterministic and paired.

    For pooled input, retain all stations/brackets from a date as a block.
    This is an approximate monitoring interval, not an independence claim.
    """
    blocks = defaultdict(list)
    for date, value in dated_differences:
        if finite(value):
            blocks[date].append(value)
    dates = sorted(blocks)
    if len(dates) < 5:
        return None
    seed = int(hashlib.sha256(json.dumps(dated_differences, sort_keys=True).encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        chosen = []
        while len(chosen) < len(dates):
            start = rng.randrange(len(dates))
            chosen.extend(dates[(start+j) % len(dates)] for j in range(3))
        samples.append(stats.mean(v for date in chosen[:len(dates)] for v in blocks[date]))
    samples.sort()
    return {'low': samples[int(.025*draws)], 'high': samples[min(draws-1, int(.975*draws))],
            'level': .95, 'date_blocks': len(dates), 'block_length': 3}


def reliability(scores):
    pairs = [pair for s in scores for pair in s.get('pairs', [])]
    bins = []
    for i in range(10):
        ps = [(p, y) for p, y in pairs if min(9, int(p*10)) == i]
        if ps:
            bins.append(dict(bin=i, n=len(ps), forecast=stats.mean(p for p, y in ps),
                             observed=stats.mean(y for p, y in ps)))
    return bins


def source_summaries(rows):
    out = []
    for key in sorted({k for r in rows for k in r['variants']}):
        matched = [r for r in rows if key in r['variants']]
        scores = [r['variants'][key] for r in matched]
        first = scores[0]
        paired = [r for r in matched if r['variants'][key].get('brier') is not None]
        differences = [(r['date'], r['variants'][key]['brier'] - r['brier']) for r in paired]
        interval = paired_interval(differences)
        out.append(dict(id=key, mode=first['mode'], model=first['model'],
            multiplier=first.get('multiplier'), method=first['method'], n=len(matched),
            dates=len({r['date'] for r in matched}), probability_dates=len(paired),
            **summarize_scores(scores), full_blend_brier=mean(r['brier'] for r in paired),
            market_brier=mean(r['market_brier'] for r in paired),
            brier_difference=mean(v for _, v in differences), difference_interval=interval,
            reliability=reliability(scores)))
    return out


def known_before(row, stamp):
    try:
        return datetime.fromisoformat(row['settled_at']) < datetime.fromisoformat(stamp)
    except (KeyError, TypeError, ValueError):
        return False


def split_history(rows):
    rows = sorted(rows, key=lambda r: r['date'])
    test = rows[-MIN_TEST:] if len(rows) >= MIN_TEST else []
    boundary = min((r['issued_at'] for r in test), default=None)
    train = [r for r in rows[:-MIN_TEST] if known_before(r, boundary)] if boundary else []
    return train[-120:], test


def pending(reason, **counts):
    return dict(status='collecting', ready_for_review=False, reasons=[reason], **counts)


def evaluate_candidate(test, scores, params):
    diffs = [(r['date'], s['brier']-r['brier']) for r, s in zip(test, scores)]
    market_diffs = [(r['date'], s['brier']-r['market_brier']) for r, s in zip(test, scores)]
    interval, market_interval = paired_interval(diffs), paired_interval(market_diffs)
    summary = summarize_scores(scores)
    reasons = []
    if len(test) < MIN_TEST:
        reasons.append('Fewer than 20 later evaluation dates')
    if interval is None or interval['high'] >= 0:
        reasons.append('Improvement over the full blend is not established')
    if market_interval is None or market_interval['high'] >= 0:
        reasons.append('Improvement over the same-snapshot market is not established')
    if summary['coverage80'] is not None and not .7 <= summary['coverage80'] <= .9:
        reasons.append('80% interval coverage is outside the 70–90% review screen')
    calibration_bins = reliability(scores)
    count = sum(b['n'] for b in calibration_bins)
    calibration_error = sum(b['n']*abs(b['forecast']-b['observed']) for b in calibration_bins)/count if count else None
    if calibration_error is None or calibration_error > .1:
        reasons.append('Probability calibration error exceeds 10 percentage points or is unavailable')
    return dict(status='review' if not reasons else 'not_supported', ready_for_review=not reasons,
        reasons=reasons, parameters=params, test_n=len(test), test_start=test[0]['date'], test_end=test[-1]['date'],
        holdout=summary, original=summarize_scores([r['baseline'] for r in test]),
        market_brier=mean(r['market_brier'] for r in test),
        brier_difference=mean(v for _, v in diffs), difference_interval=interval,
        market_difference_interval=market_interval, calibration_error=calibration_error,
        reliability=calibration_bins, applied=False)


def weight_candidate(rows):
    train, test = split_history(rows)
    counts = dict(train_n=len(train), test_n=len(test), required_train=MIN_TRAIN, required_test=MIN_TEST)
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        return pending('Need 40 earlier settled dates and 20 later evaluation dates with the same model settings.', **counts)
    # A fixed candidate set and complete training cases avoids choosing a
    # source based on the held-out outcomes or on easier availability periods.
    keys = set.intersection(*(set(r['variants']) for r in train))
    keys = sorted(k for k in keys if train[0]['variants'][k]['mode'] in ('without', 'weight'))
    if not keys:
        return pending('No common archived weight/removal experiments across the training dates.', **counts)
    chosen = min(keys, key=lambda k: (stats.mean(r['variants'][k]['brier'] for r in train), k))
    if stats.mean(r['variants'][chosen]['brier']-r['brier'] for r in train) >= 0:
        return dict(status='keep_current', ready_for_review=False, reasons=['No training candidate beats the existing weights.'], **counts)
    available = [r for r in test if chosen in r['variants']]
    if len(available) != len(test):
        return pending('The training-selected candidate is missing on some holdout dates; do not substitute another winner.', candidate=chosen, **counts)
    first = train[0]['variants'][chosen]
    result = evaluate_candidate(test, [r['variants'][chosen] for r in test],
                dict(candidate=chosen, model=first['model'], mode=first['mode'], multiplier=first['multiplier'], weight_scope=first.get('weight_scope','member')))
    result.update(train_n=len(train), train_end=train[-1]['date'], candidate_count=len(keys))
    return result


def adjusted_temperature(row, shift, factor):
    q = row['quantiles']; median = q[7]
    # Small, explicit minimum shape permits calibration of collapsed curves.
    values = [median+shift+(1 if p >= .5 else -1)*factor*max(abs(x-median), abs(_probit(p))*.25)
              for x, p in zip(q, QUANTILES)]
    if row.get('floor') is not None:
        values = [max(row['floor'], x) for x in values]
    if row.get('ceiling') is not None:
        values = [min(row['ceiling'], x) for x in values]
    return values


def temperature_candidate(rows):
    exact = [r for r in rows if finite(r['actual']) and r.get('quantiles') and r.get('bounds')]
    train, test = split_history(exact)
    counts = dict(train_n=len(train), test_n=len(test), required_train=40, required_test=20)
    if len(train) < 40 or len(test) < 20:
        return pending('Need 20 bias-fit dates, 20 later spread-calibration dates, and 20 untouched evaluation dates.', **counts)
    spread_rows = train[-MIN_SPREAD:]
    boundary = min(r['issued_at'] for r in spread_rows)
    fit = [r for r in train[:-MIN_SPREAD] if known_before(r, boundary)]
    if len(fit) < MIN_FIT:
        return pending('Need 20 bias-fit outcomes already known before the spread-calibration period.', **counts)
    shift = stats.mean(r['actual']-r['quantiles'][7] for r in fit)
    residuals = []
    for r in spread_rows:
        q = adjusted_temperature(r, shift, 1)
        residuals.append(max((q[7]-r['actual'])/max(.01, q[7]-q[3]),
                             (r['actual']-q[7])/max(.01, q[11]-q[7])))
    rank = min(len(residuals), math.ceil((len(residuals)+1)*.8))
    factor = max(.5, min(8, sorted(residuals)[rank-1]))
    scores = []
    for r in test:
        q = adjusted_temperature(r, shift, factor)
        dist = Dist(q, floor=r.get('floor'), ceiling=r.get('ceiling'))
        score = probability_scores('temperature', [dist.prob_between(lo, hi) for lo, hi in r['bounds']],
                                   [y for _, y in r['pairs']], q, r['actual'])
        if score is None:
            return pending('A candidate distribution could not be scored against the archived brackets.', **counts)
        scores.append(score)
    result = evaluate_candidate(test, scores, dict(additional_shift_f=shift, spread_multiplier=factor,
        minimum_sigma_f=.25, method='Earlier bias fit plus separate empirical 80% spread calibration'))
    if result['holdout']['mae_f'] > result['original']['mae_f']:
        result['reasons'].append('Temperature MAE deteriorated on the holdout')
        result.update(ready_for_review=False, status='not_supported')
    result.update(train_n=len(train), fit_n=len(fit), calibration_n=len(spread_rows),
                  train_end=train[-1]['date'], fit_end=fit[-1]['date'], calibration_start=spread_rows[0]['date'])
    return result


def logistic(p, slope, offset):
    p = max(1e-6, min(1-1e-6, p))
    z = max(-35, min(35, slope*math.log(p/(1-p))+offset))
    return 1/(1+math.exp(-z))


def rain_candidate(rows):
    train, test = split_history(rows)
    counts = dict(train_n=len(train), test_n=len(test), required_train=40, required_test=20)
    if len(train) < 40 or len(test) < 20:
        return pending('Need 40 earlier settled dates and 20 later evaluation dates for rain calibration.', **counts)
    pairs = [r['pairs'][0] for r in train]
    wet = sum(y for _, y in pairs)
    if min(wet, len(pairs)-wet) < 5:
        return pending('Need at least five wet and five dry training dates.', **counts)
    candidates = []
    for slope in (.5, .75, 1, 1.25):
        lo, hi = -10., 10.
        # Intercept is fitted on earlier labels only, with mild shrinkage.
        for _ in range(50):
            offset = (lo+hi)/2
            gradient = sum(logistic(p, slope, offset)-y for p, y in pairs)+offset
            if gradient > 0: hi = offset
            else: lo = offset
        candidates.append((stats.mean((logistic(p, slope, offset)-y)**2 for p, y in pairs), slope, offset))
    _, slope, offset = min(candidates)
    scores = [probability_scores('rain', [logistic(r['pairs'][0][0], slope, offset)], [r['pairs'][0][1]]) for r in test]
    result = evaluate_candidate(test, scores, dict(slope=slope, logit_offset=offset, method='Regularized logistic probability adjustment'))
    result.update(train_n=len(train), train_end=train[-1]['date'])
    return result


def build_report(rows, current_fingerprint):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['city'], row['kind'], row['horizon'])].append(row)
    out = []
    for (city, kind, horizon), values in sorted(groups.items()):
        # One latest selected snapshot per date; never treat brackets as dates.
        values = list({r['date']: r for r in sorted(values, key=lambda r: r['issued_at'])}.values())
        fingerprint = current_fingerprint.get(kind) if isinstance(current_fingerprint, dict) else current_fingerprint
        current = [r for r in values if r.get('model_fingerprint') == fingerprint]
        out.append(dict(city=city, kind=kind, horizon=horizon, dates=len(values),
            current_dates=len(current), excluded_prior_dates=len(values)-len(current),
            sources=source_summaries(values), weights=weight_candidate(current),
            calibration=(temperature_candidate(current) if kind in ('temperature', 'temperature_low') else rain_candidate(current))))
    return dict(schema_version=1, model_fingerprint=current_fingerprint, groups=out,
        note='Research forecasts only. No candidate changes live probabilities, approves calibration, or places orders.',
        methods={'bias':'Forecast minus observed: positive is warm.',
          'comparison':'Paired station/date/horizon and archived market snapshot; negative score difference favors the experiment.',
          'interval':'Approximate 95% moving-block bootstrap with three consecutive dates per block.',
          'holdout':'Candidate chosen using earlier dates only; latest 20 dates evaluate it. Training requires settlement retrieval before evaluation issuance.',
          'caution':'Short records, repeated monitoring, correlated weather, and many comparisons can exaggerate apparent gains. Require prospective confirmation before adoption.'})
