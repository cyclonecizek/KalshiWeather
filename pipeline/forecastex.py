"""Public ForecastEx comparisons. Indicative prices never enter the blend or orders."""
import csv
import io
import json
import math
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from .quality import atomic_json, now_iso
from .performance import read, CUTOFF
from .tempdist import Dist

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'docs/data'
API='https://forecastex.com/api/contracts'
RULES='https://data.forecastex.com/regulatory/DailyTemperatureTermsandConditions.pdf'
NOTE=('Indicative last-trade comparison only. Trade timestamps and executable quotes are not supplied. '
      'Weather Underground observations and Kalshi climate-report outcomes may differ. No blend weight or allocations.')


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def fetch_contracts(product):
    rows=[];page=1;seen=set()
    while page is not None:
        if page in seen or len(seen)>=10:raise ValueError('Incomplete ForecastEx pagination')
        seen.add(page)
        for attempt in range(2):
            try:
                r=requests.get(API,params=dict(productId=product,page=page,pageSize=100),timeout=(5,12))
                r.raise_for_status();payload=r.json();break
            except (requests.RequestException,ValueError):
                if attempt:raise
        if payload.get('statusCode')!=200:raise ValueError('ForecastEx response status')
        body=payload['body'];batch=body['data']
        if isinstance(batch,str):batch=json.loads(batch)
        if not isinstance(batch,list):raise ValueError('ForecastEx contracts schema')
        rows.extend(batch);page=body.get('next_page')
    return rows


def isotonic(values):
    """Equal-weight PAV. Never fill missing strikes or fabricate tail probabilities."""
    blocks=[]
    for value in values:
        blocks.append([value,1])
        while len(blocks)>1 and blocks[-2][0]/blocks[-2][1]>blocks[-1][0]/blocks[-1][1]:
            b=blocks.pop();blocks[-1][0]+=b[0];blocks[-1][1]+=b[1]
    return [total/n for total,n in blocks for _ in range(n)]


def curve(rows,kind):
    valid=sorted((r for r in rows if finite(r.get('probability'))),key=lambda r:r['strike'])
    # High YES: P(T>k), low YES: P(T<k)=F(k-1), for integer settlement.
    points=[(r['strike'] if kind=='temperature' else r['strike']-1,
             1-r['probability'] if kind=='temperature' else r['probability']) for r in valid]
    fitted=isotonic([y for x,y in points]);out=[dict(temperature=x,cdf=y) for (x,_),y in zip(points,fitted)]
    median=None
    for a,b in zip(out,out[1:]):
        if b['temperature']==a['temperature']+1 and a['cdf']<.5<=b['cdf']:
            median=b['temperature'];break
    return out,median,any(abs(a-y)>1e-8 for (_,a),y in zip(points,fitted))


def kalshi_threshold(day,strike,kind):
    """Sum complete disjoint Kalshi brackets; never split a bracket by guesswork."""
    ladder=day.get('ladder',[])
    if not ladder or day.get('gaps'):return None
    ordered=sorted(ladder,key=lambda b:float('-inf') if b['lo'] is None else b['lo'])
    if ordered[0]['lo'] is not None or ordered[-1]['hi'] is not None:return None
    if any(a['hi'] is None or b['lo']!=a['hi']+1 for a,b in zip(ordered,ordered[1:])):return None
    ps=[b.get('implied') for b in ordered]
    if not all(finite(p) and 0<=p<=1 for p in ps) or abs(sum(ps)-1)>1e-6:return None
    total=0
    for b,p in zip(ordered,ps):
        lo=b['lo'];hi=b['hi']
        if kind=='temperature':
            if lo is not None and lo>strike:total+=p
            elif hi is not None and hi<=strike:pass
            else:return None
        else:
            if hi is not None and hi<strike:total+=p
            elif lo is not None and lo>=strike:pass
            else:return None
    return total


def compare(city,day,board,raw,retrieved,error=None):
    kind=board['kind'];station=city['icao'];product=('UL' if kind=='temperature_low' else 'UH')+station[1:]
    start=datetime.fromisoformat(day['date']).replace(tzinfo=ZoneInfo(city['tz']))
    end=start+timedelta(days=1)
    out=dict(city=city['city'],station=station,date=day['date'],kind=kind,product=product,
        retrieved_at=retrieved,trade_time=None,price_type='last_trade',actionable=False,
        forecast_issued_at=board['generated_at'],snapshot_id=board['snapshot_id'],
        model_median=day['distribution']['median'],kalshi_median=(day.get('market_forecast') or {}).get('median'),
        kalshi_window_start=day['window_start'],kalshi_window_end=day['window_end'],
        kalshi_price_times=[b['market'].get('retrieved_at') for b in day['ladder']],
        window_start=start.isoformat(),window_end=end.isoformat(),source='Weather Underground Daily Observations',
        rules_url=RULES,market_url='https://forecastex.com/markets/'+product,
        station_match=False,settlement_equivalent=False,rows=[],status='unavailable',message=error or 'No matching ForecastEx station/date contracts',
        kalshi_tickers=[b['market']['ticker'] for b in day.get('ladder',[])])
    out['window_match']=(start==datetime.fromisoformat(day['window_start']) and end==datetime.fromisoformat(day['window_end']))
    if error:return out
    dist=Dist(day['distribution']['quantiles'],floor=day['distribution'].get('floor'),ceiling=day['distribution'].get('ceiling'))
    stamp=datetime.fromisoformat(day['date']).strftime('%m%d%y')
    used=set()
    for r in raw:
        if r.get('product_id')!=product:continue
        m=re.fullmatch(re.escape(product+'_'+stamp+'_')+r'(-?\d+(?:\.0+)?)',r.get('contract_id',''))
        if not m:continue
        # Exact station and source family, not city-name similarity.
        if not re.search(r'\b'+re.escape(station)+r'\b',r.get('question','')) or r.get('exchange_spec_url')!=RULES:continue
        strike=int(float(m[1]))
        if strike in used:raise ValueError('Duplicate ForecastEx threshold')
        used.add(strike)
        p=r.get('last_yes_price');p=p if finite(p) and 0<=p<=1 else None
        model_p=dist.prob_between(strike+1,None) if kind=='temperature' else dist.prob_between(None,strike-1)
        kp=kalshi_threshold(day,strike,kind)
        out['rows'].append(dict(contract_id=r['contract_id'],strike=strike,probability=p,
            model_probability=model_p,kalshi_probability=kp,open_interest=r.get('open_interest'),
            difference_pp=(p-kp)*100 if p is not None and kp is not None else None))
    out['rows'].sort(key=lambda r:r['strike'])
    if not out['rows']:return out
    out['station_match']=True
    out['curve'],out['median'],out['monotonicity_adjusted']=curve(out['rows'],kind)
    out['status']='indicative' if out['curve'] else 'no_trades'
    out['message']=NOTE if out['curve'] else 'Matched contracts have no reported trades yet.'
    out['median_difference_f']=out['median']-out['kalshi_median'] if finite(out['median']) and finite(out['kalshi_median']) else None
    out['brackets']=[];cdf={p['temperature']:p['cdf'] for p in out['curve']}
    for b in day['ladder']:
        # Need both observed boundaries; do not assume unlisted tails are 0/1.
        lower=0 if b['lo'] is None else cdf.get(b['lo']-1)
        upper=1 if b['hi'] is None else cdf.get(b['hi'])
        p=upper-lower if lower is not None and upper is not None else None
        out['brackets'].append(dict(label=b['label'],lo=b['lo'],hi=b['hi'],probability=p,
            model_probability=b['model_p'],kalshi_probability=b.get('implied')))
    return out


def build(boards,fetch=fetch_contracts):
    items=[(c,d,b) for b in boards if b.get('kind') in ('temperature','temperature_low') for c in b['cities'] for d in c['days'].values()]
    products={('UL' if b['kind']=='temperature_low' else 'UH')+c['icao'][1:] for c,d,b in items}
    def get(p):
        try:return p,(fetch(p),now_iso(),None)
        except Exception as e:return p,([],now_iso(),'ForecastEx request failed: '+type(e).__name__)
    with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(get,sorted(products)))
    rows=[]
    for c,d,b in items:
        p=('UL' if b['kind']=='temperature_low' else 'UH')+c['icao'][1:]
        raw,at,error=results[p]
        try:rows.append(compare(c,d,b,raw,at,error))
        except (ValueError,TypeError,KeyError):rows.append(compare(c,d,b,[],at,'Malformed ForecastEx contract data'))
    return dict(schema_version=1,generated_at=now_iso(),note=NOTE,records=rows)


def publish(data=DATA,fetch=fetch_contracts):
    from .products import BOARD_FILES
    boards=[read(data/BOARD_FILES[k],{}) for k in ('temperature','temperature_low')]
    report=build(boards,fetch)
    # First available capture in the hour before each fixed cutoff is immutable.
    # Avoid writing a full duplicate history every ten-minute price refresh.
    archived=set()
    for path in (data/'forecastex/history').glob('*.json'):
        for r in read(path,{}).get('records',[]):
            archived.add((r['station'],r['date'],r['kind'],r.get('archive_horizon')))
    fresh=[]
    for r in report['records']:
        if r['status']!='indicative':continue
        at=datetime.fromisoformat(r['retrieved_at']);start=datetime.fromisoformat(r['window_start'])
        for h,hours in CUTOFF.items():
            cutoff=start+timedelta(hours=hours);key=(r['station'],r['date'],r['kind'],h)
            if cutoff-timedelta(hours=1)<=at<=cutoff and key not in archived:
                fresh.append(dict(r,archive_horizon=h));archived.add(key)
    if fresh:
        stamp=report['generated_at'].replace(':','').replace('+','_')
        atomic_json(data/'forecastex/history'/f'{stamp}.json',dict(report,records=fresh))
    atomic_json(data/'forecastex.json',report)
    verify(data,fetch_reports=False)
    return report


def settlement_prices(text,target,now):
    """Only paired binary prices after expiry with no outstanding interest.

    Evidence is labeled as post-expiry prices, not a directly reported temperature.
    Daily marks, last trade prices and pre-expiry 0/1 values are not outcomes.
    """
    pairs=defaultdict(dict);conflicts=set()
    for r in csv.DictReader(io.StringIO(text)):
        try:
            expiry=datetime.fromisoformat(r['expiration_date'])
            if expiry.tzinfo is None or expiry>=now or r['date']<expiry.date().isoformat():continue
            if r['date']!=target or float(r['open_interest'])!=0:continue
            p=float(r['settlement_price'])
            if p not in (0,1) or r['subtype'] not in ('YES','NO'):continue
            if not re.fullmatch(r'U[HL][A-Z]{3}_\d{6}_-?\d+(?:\.0+)?',r['event_contract']):continue
            previous=pairs[r['event_contract']].get(r['subtype'])
            if previous is not None and previous!=int(p):conflicts.add(r['event_contract'])
            pairs[r['event_contract']][r['subtype']]=int(p)
        except (KeyError,ValueError,TypeError):continue
    return {t:dict(result=v['YES'],report_date=target,evidence='Post-expiry paired settlement prices; open interest zero')
        for t,v in pairs.items() if t not in conflicts and set(v)=={'YES','NO'} and v['YES']+v['NO']==1}


def verify(data=DATA,fetch_reports=True):
    cache=read(data/'forecastex/outcomes.json',{});selected={};errors=[]
    for path in sorted((data/'forecastex/history').glob('*.json')):
        for r in read(path,{}).get('records',[]):
            if r.get('status')!='indicative':continue
            at=datetime.fromisoformat(r['retrieved_at']);start=datetime.fromisoformat(r['window_start'])
            for h,hours in CUTOFF.items():
                if r.get('archive_horizon') and h!=r['archive_horizon']:continue
                cutoff=start+timedelta(hours=hours)
                if cutoff-timedelta(hours=6)<=at<=cutoff:
                    key=(r['station'],r['date'],r['kind'],h)
                    if key not in selected or at>datetime.fromisoformat(selected[key]['retrieved_at']):selected[key]=r
    needed={x['contract_id'] for r in selected.values() for x in r['rows']}
    now=datetime.now(timezone.utc)
    if fetch_reports:
        dates=sorted({(datetime.fromisoformat(r['date'])+timedelta(days=off)).date().isoformat()
            for r in selected.values() if any(x['contract_id'] not in cache for x in r['rows']) or r['date']>=(now-timedelta(days=7)).date().isoformat() for off in (1,2)
            if (datetime.fromisoformat(r['date'])+timedelta(days=off)).date()<now.date()})[-30:]
        for date in dates:
            url='https://data.forecastex.com/prices/daily_prices_'+date.replace('-','')+'.csv'
            try:
                response=requests.get(url,timeout=(5,20));response.raise_for_status()
                cache.update({t:dict(o,url=url,retrieved_at=now_iso()) for t,o in settlement_prices(response.text,date,now).items() if t in needed})
            except Exception as e:errors.append(date+': '+type(e).__name__)
    kalshi=read(data/'outcomes.json',{});scored=[]
    for (station,date,kind,h),r in selected.items():
        pairs=[(x,cache.get(x['contract_id'])) for x in r['rows'] if finite(x.get('probability'))]
        valid=[(x,o) for x,o in pairs if o and r['retrieved_at']<o['retrieved_at']]
        record=dict(station=station,city=r['city'],date=date,kind=kind,horizon=h,contracts=len(valid),
            fx_brier=sum((x['probability']-o['result'])**2 for x,o in valid)/len(valid) if valid else None,
            fx_settlement_inferred=None,kalshi_actual=None,settlement_difference_f=None,
            model_mae_on_kalshi=None,kalshi_mae_on_kalshi=None,fx_mae_on_kalshi=None)
        # Infer an exact integer only when adjacent resolved thresholds bound it.
        lower=-100;upper=150
        for x,o in valid:
            k=x['strike'];yes=o['result']
            if kind=='temperature':
                if yes:lower=max(lower,k+1)
                else:upper=min(upper,k)
            else:
                if yes:upper=min(upper,k-1)
                else:lower=max(lower,k)
        if lower==upper:record['fx_settlement_inferred']=lower
        elif lower>upper:
            record.update(fx_brier=None,contracts=0,outcome_warning='Contradictory threshold outcomes; unscored')
        actuals=[kalshi.get(t,{}) for t in r['kalshi_tickers']]
        winners=[o for o in actuals if o.get('result')==1 and finite(o.get('actual_value'))]
        if actuals and all(o.get('status')=='finalized' for o in actuals) and len(winners)==1:
            actual=winners[0]['actual_value'];record['kalshi_actual']=actual
            for key,value in [('model',r['model_median']),('kalshi',r['kalshi_median']),('fx',r.get('median'))]:
                if finite(value):record[key+'_mae_on_kalshi']=abs(value-actual)
            if lower==upper:record['settlement_difference_f']=lower-actual
        scored.append(record)
    report=dict(generated_at=now_iso(),records=scored,errors=errors,archived_snapshots=len(selected),
        note='One snapshot per station/date/product/cutoff. ForecastEx prices have unknown trade age. Brier uses post-expiry paired settlement-price evidence. Exact ForecastEx temperatures are inferred only from adjacent thresholds. MAE against Kalshi is a cross-target diagnostic, not a same-outcome skill ranking.')
    atomic_json(data/'forecastex/outcomes.json',cache)
    atomic_json(data/'forecastex_verification.json',report)
    return report


if __name__=='__main__':
    import sys
    report=verify() if '--score' in sys.argv else publish()
    print('ForecastEx:',len(report['records']),'comparisons; research only')
