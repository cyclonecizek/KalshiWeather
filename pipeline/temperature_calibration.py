"""Learn bias and spread on separate earlier dates; archive future predictions.

Never promote automatically. New settings must build their own evidence.
"""
import math
import statistics
from .model_research import adjusted_temperature, known_before
from .spread import bounded


def fit(rows,day,issued_at):
    eligible=[r for r in rows if r['date']<day['date'] and known_before(r,issued_at)
        and r.get('model_fingerprint')==day.get('model_fingerprint')]
    eligible=list({r['date']:r for r in sorted(eligible,key=lambda r:r['issued_at'])}.values())
    eligible.sort(key=lambda r:r['date'])
    status=dict(status='collecting',dates=len(eligible),required_dates=40,applied=False,
        message='Need 20 bias-fit dates and 20 later spread-calibration dates; future forecasts are evaluated separately.')
    if len(eligible)<40:return status,None
    cal=eligible[-20:];boundary=min(r['issued_at'] for r in cal)
    train=[r for r in eligible[:-20] if known_before(r,boundary)][-100:]
    if len(train)<20:
        status['message']='Need 20 bias-fit outcomes known before the spread-calibration period.'
        return status,None
    shift=statistics.mean(r['actual']-r['quantiles'][7] for r in train)
    errors=[]
    for r in cal:
        q=adjusted_temperature(r,shift,1)
        errors.append(max((q[7]-r['actual'])/max(.01,q[7]-q[3]),(r['actual']-q[7])/max(.01,q[11]-q[7])))
    rank=min(len(errors),math.ceil((len(errors)+1)*.8))
    factor=max(.5,min(8,sorted(errors)[rank-1]))
    current=dict(quantiles=day['distribution']['quantiles'],floor=day['distribution'].get('floor'),ceiling=day['distribution'].get('ceiling'))
    dist=bounded(adjusted_temperature(current,shift,factor),day)
    status.update(status='candidate',message='Learned bias/spread comparison; not applied to operational probabilities.',
        shift_f=shift,spread_multiplier=factor,fit_dates=len(train),calibration_dates=len(cal),
        fit_end=train[-1]['date'],calibration_start=cal[0]['date'],train_end=cal[-1]['date'],
        median=dist.median(),p10=dist.quantile(.1),p90=dist.quantile(.9))
    return status,dist


class Engine:
    def __init__(self):
        from .performance import select_snapshots, read, DATA, score_day
        outcomes=read(DATA/'outcomes.json',{});self.rows={}
        for (city,date,kind,horizon,version),(issued,snapshot,day) in select_snapshots().items():
            if kind not in ('temperature','temperature_low'):continue
            tickers=[b['market']['ticker'] for b in day.get('ladder',[])]
            if not tickers or any(outcomes.get(t,{}).get('status')!='finalized' for t in tickers):continue
            score=score_day(kind,day,outcomes)
            if not score or score['actual'] is None:continue
            stamps=[outcomes[t].get('retrieved_at') for t in tickers]
            row=dict(date=date,issued_at=issued,settled_at=max(stamps) if all(stamps) else None,
                model_fingerprint=day.get('model_fingerprint'),actual=score['actual'],quantiles=score['quantiles'],
                floor=day['distribution'].get('floor'),ceiling=day['distribution'].get('ceiling'))
            self.rows.setdefault((city,kind,horizon),[]).append(row)

    def attach(self,city,day,issued):
        status,dist=fit(self.rows.get((city,day['kind'],day['horizon']),[]),day,issued)
        day['spread_calibration']=status
        if dist:
            day['experiments']['variants']['distribution:calibrated']=dict(mode='distribution',
                model='Learned bias and spread',method='Prospective separate bias-fit and spread-calibration dates',
                multiplier=status['spread_multiplier'],quantiles=dist.v,floor=dist.floor,ceiling=dist.ceiling,
                probabilities=[dist.prob_between(b['lo'],b['hi']) for b in day['ladder']])
