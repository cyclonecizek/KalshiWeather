"""Paired, horizon-specific verification against actual market settlement.

Fits are reported as candidates using earlier dates; holdout dates are never
used to fit parameters. No calibration is automatically enabled for orders.
"""
from __future__ import annotations
import json,math,statistics,sys
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .quality import atomic_json,now_iso
from .kalshi import Kalshi
from .util import load_yaml
from .tempdist import Dist,adjust
ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'docs/data'
CUTOFF={'day_ahead':-12,'morning':8,'afternoon':15,'evening':20}

def read(path,default):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return default

def select_snapshots():
    selected={}
    for path in sorted((DATA/'history').glob('*.json')):
        board=read(path,{})
        if board.get('schema_version')!=2:continue
        stamp=board['generated_at'];at=datetime.fromisoformat(stamp)
        for c in board.get('cities',[]):
            for d in c['days'].values():
                start=datetime.fromisoformat(d['window_start'])
                for h,hours in CUTOFF.items():
                    cutoff=start+timedelta(hours=hours)
                    if not cutoff-timedelta(hours=6)<=at<=cutoff:continue
                    key=(c['city'],d['date'],board['kind'],h,board.get('model_version'))
                    if key not in selected or stamp>selected[key][0]:selected[key]=(stamp,board['snapshot_id'],d)
    return selected

def actual_value(m):
    # An interval label or a fair-price payout is not an observed temperature.
    raw=str(m.get('expiration_value') or '').strip()
    import re
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',raw):return None
    value=float(raw)
    return value if -100<=value<=140 else None

def refresh_outcomes(selected,cache):
    kal=Kalshi(load_yaml(ROOT/'config/settings.yml')['sources']['kalshi']['base'])
    needed=set()
    for _,_,d in selected.values():
        needed.update(b['market']['ticker'] for b in d.get('ladder',[]))
        if d.get('market'):needed.add(d['market']['ticker'])
    adjustments=read(DATA/'adjustments.json',[])
    for a in adjustments:needed.update(a.get('tickers',[]))
    for ticker in sorted(needed):
        if cache.get(ticker,{}).get('status')=='finalized':continue
        try:
            m=kal.market(ticker)
            result=m.get('result')
            # Require final settlement; intermediate determination can change.
            if m.get('status') not in ('finalized','settled') or result not in ('yes','no'):continue
            cache[ticker]={'result':1 if result=='yes' else 0,'actual_value':actual_value(m),
                'status':'finalized','retrieved_at':now_iso(),'source_url':f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"}
        except Exception:continue
    return cache

def reliability(pairs,bins=10):
    out=[]
    for i in range(bins):
        values=[(p,y) for p,y in pairs if min(bins-1,int(p*bins))==i]
        if values:out.append({'bin':i,'n':len(values),'forecast':statistics.mean(p for p,y in values),'observed':statistics.mean(y for p,y in values)})
    return out

def score_day(kind,d,outcomes):
    if kind=='rain':
        m=d['market'];o=outcomes.get(m['ticker'])
        if o is None or m.get('mid') is None:return None
        p=d['consensus'];q=m['mid']/100;y=o['result']
        return {'brier':(p-y)**2,'market_brier':(q-y)**2,'log_loss':-math.log(max(1e-8,p if y else 1-p)),
            'pairs':[(p,y)],'actual':None,'median':None,'error':None,'covered80':None,'quantiles':None}
    ladder=d['ladder'];dist=d['distribution']
    if d.get('gaps') or any(b['market']['ticker'] not in outcomes or b.get('implied') is None for b in ladder):return None
    ys=[outcomes[b['market']['ticker']]['result'] for b in ladder]
    if sum(ys)!=1:return None
    pairs=[(b['model_p'],y) for b,y in zip(ladder,ys)]
    winner=next(b for b,y in zip(ladder,ys) if y)
    actual=outcomes[winner['market']['ticker']].get('actual_value')
    return {'brier':sum((p-y)**2 for p,y in pairs),
        'market_brier':sum((b['implied']-y)**2 for b,y in zip(ladder,ys)),
        'log_loss':-math.log(max(1e-8,winner['model_p'])),'pairs':pairs,
        'actual':actual,'median':dist['median'],'error':None if actual is None else actual-dist['median'],
        'covered80':None if actual is None else dist['p10']<=actual<=dist['p90'],
        'quantiles':dist['quantiles']}

def summarize(records):
    groups=defaultdict(list)
    for r in records:groups[(r['city'],r['kind'],r['horizon'])].append(r)
    out=[]
    for (city,kind,h),rows in sorted(groups.items()):
        errors=[r['error'] for r in rows if r['error'] is not None]
        cov=[r['covered80'] for r in rows if r['covered80'] is not None]
        pairs=[pair for r in rows for pair in r['pairs']]
        item=dict(city=city,kind=kind,horizon=h,n=len(rows),brier=statistics.mean(r['brier'] for r in rows),
            market_brier=statistics.mean(r['market_brier'] for r in rows),log_loss=statistics.mean(r['log_loss'] for r in rows),
            bias=statistics.mean(errors) if errors else None,mae=statistics.mean(abs(x) for x in errors) if errors else None,
            actual_temperature_n=len(errors),coverage80=statistics.mean(cov) if cov else None,reliability=reliability(pairs))
        item['brier_skill']=1-item['brier']/item['market_brier'] if item['market_brier'] else None
        # Date-ordered holdout. The additive correction is fitted only from
        # earlier errors. Report as a candidate, never replace configured bias.
        exact=sorted([r for r in rows if r['actual'] is not None],key=lambda r:r['date'])
        if len(exact)>=60:
            train,test=exact[:-20],exact[-20:]
            shift=statistics.mean(r['error'] for r in train)
            residuals=[r['error']-shift for r in train]
            item['candidate_calibration']={'additional_bias_f':shift,'train_n':len(train),'test_n':len(test),
                'train_end':train[-1]['date'],'test_start':test[0]['date'],
                'holdout_mae_original':statistics.mean(abs(r['error']) for r in test),
                'holdout_mae_adjusted':statistics.mean(abs(r['error']-shift) for r in test),
                'training_error_sd_f':statistics.pstdev(residuals),'validated':False}
        out.append(item)
    return out

def adjustment_scores(adjustments,outcomes):
    result=[]
    for a in adjustments:
        ys=[outcomes.get(t,{}).get('result') for t in a['tickers']]
        if any(y is None for y in ys):continue
        if a['kind']=='temperature' and sum(ys)!=1:continue
        result.append({'id':a['id'],'city':a['city'],'date':a['date'],'kind':a['kind'],'reason':a['reason'],
            'automatic_brier':sum((p-y)**2 for p,y in zip(a['automatic_probabilities'],ys)),
            'adjusted_brier':sum((p-y)**2 for p,y in zip(a['adjusted_probabilities'],ys))})
    return result

def publish(fetch_outcomes=True):
    selected=select_snapshots();outcomes=read(DATA/'outcomes.json',{})
    if fetch_outcomes:outcomes=refresh_outcomes(selected,outcomes)
    records=[]
    for (city,date,kind,h,version),(at,snapshot,d) in selected.items():
        scores=score_day(kind,d,outcomes)
        if scores:records.append(dict(city=city,date=date,kind=kind,horizon=h,model_version=version,issued_at=at,snapshot_id=snapshot,**scores))
    adj=read(DATA/'adjustments.json',[])
    report={'generated_at':now_iso(),'model_version':'2','selection':'Latest snapshot within 6 hours before each fixed reporting-hour cutoff',
        'cutoffs':CUTOFF,'groups':summarize(records),'records':records,'adjustments':adjustment_scores(adj,outcomes),
        'note':'Paper orders are proposals. No fills or realized profits are assumed.'}
    atomic_json(DATA/'performance.json',report);atomic_json(DATA/'outcomes.json',outcomes)
    return report

if __name__=='__main__':
    report=publish();print(f"Scored {len(report['records'])} matched forecasts in {len(report['groups'])} groups")
