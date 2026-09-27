"""Fixed, prospective hypotheses chosen after the September 27 review.

Archive registration with the forecast. Historical experiments used to choose
these hypotheses cannot become their evaluation set. Never promote a winner.
"""
VERSION = '2026-09-27-v1'
FIRST_DATE = '2026-09-28'
REQUIRED_DATES = 20
HIGH_SPREAD = {'day_ahead': .75, 'morning': 1.25, 'afternoon': 1.25, 'evening': 1.5}


def specification(kind, horizon):
    if kind == 'rain':
        return dict(key='weight:GEFS:0.5', title='Half GEFS weight for rain',
                    model='GEFS', mode='weight', multiplier=.5)
    if kind == 'temperature' and horizon in HIGH_SPREAD:
        factor = HIGH_SPREAD[horizon]
        return dict(key=f'spread:{factor}', title=f'High-temperature spread ×{factor}',
                    model=f'High-temperature spread ×{factor}', mode='distribution', multiplier=factor)
    return None


def register(day):
    # Register all horizons: verification selects fixed cutoffs, which may
    # differ from the issuance-time label on a forecast just before a cutoff.
    day['experiments']['focused_registration'] = VERSION


def report(rows, kind, horizon):
    from .model_research import evaluate_candidate, summarize_scores, paired_interval, mean
    spec = specification(kind, horizon)
    if not spec:
        return None
    eligible = sorted((r for r in rows if r.get('focused_registration') == VERSION
                       and r['date'] >= FIRST_DATE), key=lambda r: (r['date'], r['issued_at']))
    eligible = list({r['date']: r for r in eligible}.values())[:REQUIRED_DATES]
    paired = [r for r in eligible if spec['key'] in r['variants']]
    missing = [r['date'] for r in eligible if spec['key'] not in r['variants']]
    result = dict(version=VERSION, title=spec['title']+' · '+horizon.replace('_', ' '), candidate=spec['key'],
        first_eligible_date=FIRST_DATE, required_dates=REQUIRED_DATES,
        dates=len(eligible), paired_dates=len(paired), missing_dates=missing,
        status='collecting', ready_for_review=False, applied=False,
        reasons=['Fixed hypothesis: first 20 newly archived settled dates per station and horizon. No historical backfill or automatic adoption.'])
    if paired:
        scores = [r['variants'][spec['key']] for r in paired]
        diffs = [(r['date'], s['brier']-r['brier']) for r, s in zip(paired, scores)]
        result.update(original=summarize_scores([r['baseline'] for r in paired]),
            holdout=summarize_scores(scores), brier_difference=mean(v for _, v in diffs),
            difference_interval=paired_interval(diffs),
            test_start=paired[0]['date'], test_end=paired[-1]['date'])
    if len(eligible) == REQUIRED_DATES and not missing:
        result.update(evaluate_candidate(paired, scores, dict(candidate=spec['key'], **{k:v for k,v in spec.items() if k not in ('key','title')})))
    if missing:
        result['reasons'].append('Some registered dates lack this exact experiment; no substitute candidate is used.')
    return result


def weathernext_summary(rows):
    from .model_research import finite, mean
    exact = [r for r in rows if finite(r.get('actual')) and r.get('weathernext_diagnostics')]
    stages = []
    for key, label in [('raw_median_f', 'Raw member daily extrema'),
                       ('conditioned_median_f', 'After observations'),
                       ('final_median_f', 'After common bias and spread processing')]:
        paired = [r for r in exact if finite(r['weathernext_diagnostics'].get(key))]
        errors = [r['weathernext_diagnostics'][key]-r['actual'] for r in paired]
        stages.append(dict(stage=label, dates=len({r['date'] for r in paired}),
                           n=len(paired), bias_f=mean(errors), mae_f=mean(abs(e) for e in errors)))
    return dict(stages=stages, note='Positive bias is warm; negative is cold. Raw values are per-member daily extrema, not extrema of the hourly ensemble median. Six-hour native sampling is a possible cause of missed peaks, not a proven explanation. Unknown model issue times remain unknown.')
