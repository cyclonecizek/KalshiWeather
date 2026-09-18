"""Prospective, comparison-only ridge nowcast. Never changes operational odds."""
from collections import defaultdict
from datetime import datetime, timedelta
import json
import math
import numpy as np
from .tempdist import normal_quantiles, Dist

VERSION = 'observation-ridge-v1'
MIN_FIT_DATES = 20
MIN_INTERVAL_DATES = 10
FEATURES = ['observed_high', 'current_minus_high', 'baseline_remaining',
            'raw_remaining', 'source_disagreement', 'error_1h', 'error_3h',
            'error_6h', 'warming_rate', 'reporting_hour', 'season_sin', 'season_cos']


def at(s):
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def features(day, prior, issued):
    """Match observations only to forecasts archived before each observation."""
    now, start, end = at(issued), at(day.get('window_start')), at(day.get('window_end'))
    obs = day.get('observed') or {}
    floor = obs.get('max_f')
    baseline = day.get('distribution', {}).get('median')
    if not now or not start or not end or not start <= now < end:
        return None, 'Available for today only'
    if not obs.get('temperature_complete') or not finite(floor) or not finite(baseline):
        return None, 'Adequate temperature observations required'
    if any(at(obs.get(k)) and at(obs[k]) > now for k in ('latest_at','retrieved_at')):
        return None, 'Observation timestamps exceed forecast issuance'
    observations = sorted([(at(p.get('time')), p.get('temperature_f')) for p in obs.get('hourly', [])
                           if at(p.get('time')) and finite(p.get('temperature_f'))
                           and start <= at(p['time']) <= now], key=lambda p:p[0])
    if len(observations) < 3 or (now-observations[-1][0]).total_seconds() > 5400:
        return None, 'At least three recent observations required'
    errors = []
    for t, value in observations:
        if t < now-timedelta(hours=6):
            continue
        guidance = {}
        for stamp, previous in prior:
            if not at(stamp) or at(stamp) > t:
                continue
            if previous.get('settlement',{}).get('icao') != day.get('settlement',{}).get('icao'):
                continue
            for name, source in previous.get('sources', {}).items():
                retrieved = at(source.get('retrieved_at'))
                if not retrieved or retrieved > t:
                    continue
                points = [(abs((at(p['time'])-t).total_seconds()), p['median'])
                          for p in source.get('hourly', []) if at(p.get('time')) and finite(p.get('median'))]
                if points:
                    distance, predicted = min(points)
                    if distance <= 1800 and (name not in guidance or retrieved > guidance[name][0]):
                        guidance[name] = (retrieved, predicted)
        if guidance:
            errors.append((t, value-float(np.mean([v[1] for v in guidance.values()]))))
    residuals = [[e for t,e in errors if t >= now-timedelta(hours=h)] for h in (1,3,6)]
    if len(errors) < 3 or any(not r for r in residuals):
        return None, 'Need prior-issued guidance matching observations over 1, 3 and 6 hours'
    peaks = []
    for source in day.get('sources', {}).values():
        retrieved = at(source.get('retrieved_at'))
        if not retrieved or retrieved > now:
            continue
        values = [p['median'] for p in source.get('hourly', []) if at(p.get('time'))
                  and now < at(p['time']) < end and finite(p.get('median'))]
        if values:
            peaks.append(max(values))
    if len(peaks) < 2:
        return None, 'Two remaining-hour model trajectories required'
    recent = [(t,v) for t,v in observations if t >= now-timedelta(hours=3)]
    if len(recent) < 2 or recent[-1][0] == recent[0][0]:
        return None, 'Recent warming rate unavailable'
    rate = (recent[-1][1]-recent[0][1])/((recent[-1][0]-recent[0][0]).total_seconds()/3600)
    angle = 2*math.pi*start.timetuple().tm_yday/365.25
    x = [floor, observations[-1][1]-floor, max(0,baseline-floor),
         max(0,float(np.mean(peaks))-floor), float(np.std(peaks)),
         *[float(np.mean(r)) for r in residuals], rate,
         (now-start).total_seconds()/3600, math.sin(angle), math.cos(angle)]
    return dict(version=VERSION, values=x, observed_high=floor, baseline=baseline,
                matched_observations=len(errors), as_of=issued), None


def load_archive(data):
    history = defaultdict(list)
    for path in sorted((data/'history').glob('*.json')):
        try:
            board = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if board.get('kind') != 'temperature' or board.get('schema_version') != 2:
            continue
        for city in board.get('cities', []):
            for day in city.get('days', {}).values():
                history[(city['city'],day['date'])].append((board['generated_at'],day))
    return history


def training_rows(selected, outcomes, history):
    rows=[]
    for (city,date,kind,horizon,version),(stamp,snapshot,day) in selected.items():
        if kind!='temperature' or horizon=='day_ahead':
            continue
        targets=[outcomes.get(b['market']['ticker'],{}) for b in day.get('ladder',[])]
        if not targets or any(o.get('status')!='finalized' or not at(o.get('retrieved_at')) for o in targets):
            continue
        winners=[o for o in targets if o.get('result')==1]
        if len(winners)!=1 or not finite(winners[0].get('actual_value')):
            continue
        f,reason=features(day,history.get((city,date),[]),stamp)
        actual=winners[0]['actual_value']
        if f is None or actual < f['observed_high']-.6:
            continue
        rows.append(dict(city=city,date=date,horizon=horizon,features=f,actual=actual,
            settled_at=max(o['retrieved_at'] for o in targets),issued_at=stamp,
            fingerprint=day.get('model_fingerprint')))
    return rows


def fit(rows):
    x=np.array([r['features']['values'] for r in rows],dtype=float)
    cities=sorted({r['city'] for r in rows})
    counts=defaultdict(int)
    for r in rows:counts[r['date']]+=1
    w=np.array([1/counts[r['date']] for r in rows]);w*=len(rows)/sum(w)
    mean=np.average(x,axis=0,weights=w)
    sd=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));sd=np.maximum(sd,1)
    design=np.column_stack([np.ones(len(x)),(x-mean)/sd,
        np.array([[float(r['city']==c) for c in cities] for r in rows])])
    target=np.array([max(0,r['actual']-r['features']['observed_high']) for r in rows])
    penalty=np.eye(design.shape[1])*10;penalty[0,0]=0
    beta=np.linalg.solve(design.T@(w[:,None]*design)+penalty,design.T@(w*target))
    return mean,sd,cities,beta


def predict(model, f, city):
    mean,sd,cities,beta=model
    x=np.r_[1,(np.array(f['values'])-mean)/sd,[float(city==c) for c in cities]]
    return f['observed_high']+max(0,float(x@beta))


class Engine:
    def __init__(self, data):
        from .performance import select_snapshots, read
        from .calibration_review import model_fingerprint
        self.history=load_archive(data)
        fingerprint=model_fingerprint()
        selected={k:v for k,v in select_snapshots().items() if v[2].get('model_fingerprint')==fingerprint}
        self.rows=training_rows(selected,read(data/'outcomes.json',{}),self.history)
        self.models={}

    def attach(self, city, day, issued):
        f,reason=features(day,self.history.get((city,day['date']),[]),issued)
        usable=[r for r in self.rows if r['date']<day['date'] and at(r['settled_at'])<at(issued)
                and r['fingerprint']==day.get('model_fingerprint')]
        dates=sorted({r['date'] for r in usable})
        result=dict(version=VERSION,status='collecting',applied=False,training_dates=len(dates),
                    required_dates=MIN_FIT_DATES+MIN_INTERVAL_DATES,features=f,
                    message=reason or 'Collecting settled dates under the current model settings')
        day['observation_ml']=result
        if f is None or len(dates)<MIN_FIT_DATES+MIN_INTERVAL_DATES:
            return
        key=(day['date'],day.get('model_fingerprint'),tuple(dates))
        if key not in self.models:
            split=dates[-MIN_INTERVAL_DATES]
            train=[r for r in usable if r['date']<split]
            cal=[r for r in usable if r['date']>=split]
            model=fit(train)
            # Each date contributes equally to the empirical interval estimate.
            errors=defaultdict(list)
            for r in cal:errors[r['date']].append(abs(predict(model,r['features'],r['city'])-r['actual']))
            weighted=sorted((error,1/len(values)) for values in errors.values() for error in values)
            threshold=.8*sum(w for _,w in weighted);cumulative=0
            radius=.5
            for error,weight in weighted:
                cumulative+=weight
                if cumulative>=threshold:
                    radius=max(.5,error);break
            self.models[key]=(model,radius,split)
        model,radius,split=self.models[key]
        median=predict(model,f,city)
        q=[max(f['observed_high'],v) for v in normal_quantiles(median,radius/1.2815515655)]
        dist=Dist(q,floor=f['observed_high'])
        result.update(status='candidate',message='Research comparison only; not used for allocation',
            median=median,p10=dist.quantile(.1),p90=dist.quantile(.9),quantiles=q,
            adjustment_f=median-f['baseline'],fit_before=split,interval_dates=MIN_INTERVAL_DATES,
            last_training_date=dates[-1],trained_as_of=issued,
            probabilities=[dist.prob_between(b['lo'],b['hi']) for b in day.get('ladder',[])],
            tickers=[b['market']['ticker'] for b in day.get('ladder',[])])


def score_candidate(day, outcomes):
    c=day.get('observation_ml') or {}
    if c.get('version')!=VERSION or c.get('status')!='candidate':return None
    tickers=[b['market']['ticker'] for b in day.get('ladder',[])]
    if c.get('tickers')!=tickers or len(c.get('probabilities',[]))!=len(tickers):return None
    obs=[outcomes.get(t,{}) for t in tickers]
    if any(o.get('status')!='finalized' for o in obs):return None
    winners=[o for o in obs if o.get('result')==1]
    if len(winners)!=1 or not finite(winners[0].get('actual_value')):return None
    y=winners[0]['actual_value'];baseline=day['distribution']
    return dict(mae=abs(c['median']-y),baseline_mae=abs(baseline['median']-y),
        covered80=c['p10']<=y<=c['p90'],baseline_covered80=baseline['p10']<=y<=baseline['p90'],
        brier=sum((p-o['result'])**2 for p,o in zip(c['probabilities'],obs)),
        baseline_brier=sum((b['model_p']-o['result'])**2 for b,o in zip(day['ladder'],obs)))
