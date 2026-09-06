"""Build validated, versioned public boards with auditable source provenance."""
from __future__ import annotations
import contextlib,copy,io,json,os,sys,uuid
from datetime import datetime,timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from . import quality,settlement,policy
from .quality import atomic_json,now_iso,age_minutes
from .util import load_yaml,local_date_str,local_day_window
from .kalshi import Kalshi,effective_fee_rate,pick_city_market,book_depth
from .blend import blend,evaluate
from .brackets import build_ladder,pick_ladder,implied_distribution,implied_quantiles,coverage_gaps,check_arbitrage
from .build_temp import build_distribution,evaluate_bracket,_is_for_date
from .tempdist import Dist
from .sources import hourly,openmeteo,temp_sources,observations,nws_text,nbm_temp,gribprob,meteoblue

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'docs/data'

def read_json(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default if default is not None else {}

def capture(name,fn,errors):
    print(f'Fetching {name}',flush=True)
    output=io.StringIO()
    try:
        with contextlib.redirect_stdout(output):result=fn()
    except Exception as exc:
        errors.append(f'{name}: {type(exc).__name__}');result={}
    for line in output.getvalue().splitlines():
        if any(word in line.lower() for word in ('failed','skipping','no inventory','error','403','404','429','timed out')):
            # Do not republish exception URLs, which can contain API keys.
            errors.append(f'{name}: source reported incomplete data')
    return result or {}

def per_city(fn,cities,*args):
    out={}
    def one(c):
        try:return fn([c],*args)
        except Exception as exc:
            quality.record(fn.__name__,c['name'],'failed',type(exc).__name__);return {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for result in ex.map(one,cities):out.update(result)
    return out

def horizon(day,now=None):
    now=now or datetime.now(timezone.utc)
    start=datetime.fromisoformat(day['window_start'])
    hours=(start-now).total_seconds()/3600
    if hours>0:return 'day_ahead'
    elapsed=-hours
    return 'morning' if elapsed<12 else 'afternoon' if elapsed<18 else 'evening'

def source_details(city,off):
    return {model:{'retrieved_at':d['retrieved_at'],'model_run_at':d.get('model_run_at'),
                  'hourly':d['hourly'],'member_count':len(d['maxima'])}
            for (name,offset,model),d in hourly.DETAILS.items() if name==city and off==offset}

def set_edge_depth(kal,quote,edge):
    if not edge:return
    side=edge['side'];price=edge['price']
    depth=quote.get('yes_depth' if side=='YES' else 'no_depth')
    if depth is None and edge.get('flag') in ('high','watch'):
        try:depth=book_depth(kal.orderbook(quote['ticker']),side,price)
        except Exception:pass
    edge['depth']=depth

def changes(day,old,temperature=False):
    if not old:return {'summary':'First comparable snapshot','components':[]}
    vals=[]
    if temperature:
        current=(day.get('distribution') or {}).get('median');past=(old.get('distribution') or {}).get('median')
        if current is not None and past is not None:vals.append(f'High forecast {current-past:+.1f} F')
        x=(day.get('observed') or {}).get('max_f');y=(old.get('observed') or {}).get('max_f')
        if x is not None and y is not None and abs(x-y)>.05:vals.append(f'Observed maximum {x-y:+.1f} F')
        prev={b['market']['ticker']:b for b in old.get('ladder',[])}
        for b in day['ladder']:
            before=prev.get(b['market']['ticker'])
            if before:
                baseline=day.get('baseline_distribution');prior=old.get('baseline_distribution')
                b['changes']=changes({'consensus':b.get('model_p'),'market':b['market'],
                    'consensus_forecast':Dist(baseline['quantiles']).prob_between(b['lo'],b['hi']) if baseline else None},
                    {'consensus':before.get('model_p'),'market':before['market'],
                    'consensus_forecast':Dist(prior['quantiles']).prob_between(before['lo'],before['hi']) if prior else None})
    else:
        x,y=day.get('consensus'),old.get('consensus')
        baseline,prior=day.get('consensus_forecast'),old.get('consensus_forecast')
        parts={}
        if x is not None and y is not None:
            if baseline is not None and prior is not None:
                parts['forecast_change_pp']=(baseline-prior)*100
                parts['observation_effect_change_pp']=((x-baseline)-(y-prior))*100
                vals.append(f"Full-day guidance/source mix {parts['forecast_change_pp']:+.1f} points")
                vals.append(f"Observation effect {parts['observation_effect_change_pp']:+.1f} points")
            else:vals.append(f'Probability {(x-y)*100:+.1f} points')
        price,previous=(day.get('market') or {}).get('mid'),(old.get('market') or {}).get('mid')
        if price is not None and previous is not None:
            parts['market_change_cents']=price-previous
            vals.append(f'Market price {price-previous:+.1f} cents')
            if x is not None and y is not None:parts['gap_change_pp']=(x-y)*100-(price-previous)
        return {'summary':'; '.join(vals) or 'No material change','components':parts,
            'previous_snapshot_at':old.get('generated_at'),
            'method':'Change in unconditioned guidance plus change in observation adjustment; descriptive, not causal.'}
    return {'summary':'; '.join(vals) or 'No material change','components':vals,'previous_snapshot_at':old.get('generated_at')}

def prepare(kind,settings):
    quality.STATUS.clear();hourly.DETAILS.clear();errors=[]
    cities=settlement.configure_cities(load_yaml(ROOT/'config/cities.yml')['cities'],kind)
    if os.getenv('WEATHER_CITIES'):
        selected=set(os.environ['WEATHER_CITIES'].split(','));cities=[c for c in cities if c['name'] in selected]
    src=settings['sources'];tcfg=settings['temperature'];kal=Kalshi(src['kalshi']['base'])
    offsets=(0,1)
    data=capture('ensembles',lambda:hourly.fetch(cities,src['openmeteo'],offsets),errors)
    obs=capture('observations',lambda:per_city(observations.fetch,cities,offsets,src.get('observations')),errors)
    point={};probs={};members={};nbmt={}
    for model,by_city in data.items():
        members[model]={c:{off:d['maxima'] for off,d in days.items()} for c,days in by_city.items()}
        probs[model]={c:{off:hourly.rain_probability(d,(obs.get(c) or {}).get(off))[0]
            for off,d in days.items()} for c,days in by_city.items()}
    if kind=='temperature':
        point['NDFD']=capture('NDFD',lambda:per_city(temp_sources.fetch_ndfd_maxt,cities,src['ndfd'],offsets),errors)
        cfg=tcfg['sources']['nbm_temp']
        if cfg.get('enabled'):
            nbmt=capture('NBM_T',lambda:nbm_temp.fetch(cities,cfg,offsets),errors)
            point['NBM_T']={c:{off:d['mean_f'] for off,d in days.items()} for c,days in nbmt.items()}
    else:
        probs['NDFD']=capture('NDFD',lambda:per_city(nws_text.fetch_ndfd,cities,src['ndfd'],settings.get('pop_stitch_rho',.5),offsets),errors)
        cfg=src.get('nbm',{})
        if cfg and cfg.get('enabled',True):
            nbm=capture('NBM',lambda:gribprob.fetch('NBM',cities,cfg,settings.get('pop_stitch_rho',.5),offsets),errors)
            if nbm:probs['NBM']=nbm[0]
    mbcfg=tcfg['sources']['meteoblue']
    # Public numeric products must not allow reconstructing a restricted
    # component from a known weighted blend. Opt-in publication is explicit.
    mb={}
    if mbcfg.get('publish_values'):
        mb=capture('METEOBLUE',lambda:meteoblue.fetch(cities,mbcfg,offsets),errors)
        point['METEOBLUE']={c:{off:d.get('tmax') for off,d in days.items()} for c,days in mb.items()}
        probs['METEOBLUE']={c:{off:d.get('pop') for off,d in days.items()} for c,days in mb.items()}
    market_cache={};rows=[];retrieved=now_iso()
    if kind=='rain':market_cache['KXRAIN']=kal.markets_for_series('KXRAIN')
    for c in cities:
        ticker=c['series_high'] if kind=='temperature' else 'KXRAIN'
        if ticker not in market_cache:
            try:market_cache[ticker]=kal.markets_for_series(ticker)
            except Exception as exc:
                errors.append(f"{c['name']} market data unavailable");continue
        markets=market_cache[ticker];days={}
        # Series metadata controls fees; failure prevents eligibility.
        try:
            meta=kal._get('/series/'+ticker)['series']
            fee=effective_fee_rate(meta.get('fee_multiplier'),.07)
        except Exception:
            meta={};fee=.07;errors.append(f"{c['name']} fee metadata unavailable")
        for off in offsets:
            date=local_date_str(c['tz'],off);start,end=local_day_window(c['tz'],off)
            matches=[m for m in markets if _is_for_date(m,date)]
            if kind=='rain':matches=[m for m in matches if m['ticker'].endswith('-'+str(c['rain_code']))]
            if not matches:continue
            kal.hydrate(matches)
            spec=settlement.verify(c,matches)
            ob=(obs.get(c['name']) or {}).get(off)
            details=source_details(c['name'],off)
            day={'date':date,'window_start':start.isoformat(),'window_end':end.isoformat(),
                'elapsed':max(0,min(1,(datetime.now(timezone.utc)-start).total_seconds()/(end-start).total_seconds())),
                'settlement':spec,'observed':ob,'sources':details,'forecast_retrieved_at':min((d['retrieved_at'] for d in details.values()),default=None),
                'data_quality':'ok','generated_at':retrieved,'kind':kind,'source_error_count':len(errors),
                'fee_verified':meta.get('fee_multiplier') is not None}
            if mbcfg.get('publish_values') and mb.get(c['name'],{}).get(off):
                day['meteoblue']=mb[c['name']][off]
            day['horizon']=horizon(day)
            if kind=='temperature':
                dist,diag=build_distribution(c,off,members,point,tcfg,errors,obs=ob,obs_cfg=src.get('observations'),
                    nbm_sigma=nbmt.get(c['name'],{}).get(off,{}).get('sd_f'))
                baseline,_=build_distribution(c,off,members,point,tcfg,errors,obs=None,
                    nbm_sigma=nbmt.get(c['name'],{}).get(off,{}).get('sd_f'))
                ladder,_=pick_ladder(build_ladder(matches,Kalshi.quote))
                if not ladder or dist is None:continue
                gaps=coverage_gaps(ladder);implied,overround=implied_distribution(ladder)
                mq=implied_quantiles(ladder,implied)
                day.update(ladder=ladder,gaps=gaps,overround=overround,
                    market_forecast={'median':mq.get(.5),'p10':mq.get(.1),'p90':mq.get(.9)} if mq else None,
                    distribution={'median':dist.median(),'p10':dist.quantile(.1),'p90':dist.quantile(.9),'quantiles':dist.v,'floor':dist.floor},
                    baseline_distribution={'quantiles':baseline.v} if baseline else None,
                    diagnostics=diag,n_families=diag.get('_n_families',0),arbitrage=check_arbitrage(ladder,fee))
                if gaps:day['data_quality']='partial'
                for b,p in zip(ladder,implied):
                    b['implied']=p;b['model_p']=dist.prob_between(b['lo'],b['hi'])
                    b['edge']=evaluate_bracket(b['model_p'],b['market'],fee,tcfg)
                    set_edge_depth(kal,b['market'],b['edge'])
                    if b['edge']:b['edge']['fee_rate']=fee
            else:
                q=Kalshi.quote(matches[0]);mp={m:days_.get(c['name'],{}).get(off) for m,days_ in probs.items()}
                full_day={**mp}
                for model,by_city in data.items():
                    trajectory=by_city.get(c['name'],{}).get(off)
                    if trajectory:full_day[model]=hourly.rain_probability(trajectory,None)[0]
                baseline=blend(full_day,settings)
                # Intraday full-day NDFD/NBM PoPs cannot represent remaining
                # risk. Use trajectory-conditioned global members instead.
                intraday=bool(ob and ob.get('precip_complete'))
                if intraday:
                    mp={m:v for m,v in mp.items() if m in data}
                b=blend(mp,settings)
                if not b:continue
                p=b['consensus'];effect='remaining_hours' if intraday else None
                if ob and ob.get('precip_complete') and ob.get('wet'):p=.98;effect='observed'
                q['fee_multiplier']=meta.get('fee_multiplier')
                day.update(b);day.update(consensus=p,consensus_forecast=baseline['consensus'] if baseline else None,market=q,raw_models={m:v for m,v in mp.items() if v is not None},obs_effect=effect)
                day['edge']=evaluate(p,q,settings);set_edge_depth(kal,q,day['edge'])
                if day['edge']:day['edge']['fee_rate']=fee
            if not details or (off==0 and (not ob or not ob.get('temperature_complete' if kind=='temperature' else 'precip_complete'))):day['data_quality']='partial'
            days[str(off)]=day
        if days:rows.append(dict(city=c['name'],series=ticker,station=c['station'],icao=c['icao'],tz=c['display_tz'],reporting_tz=c['tz'],verified=c['verified'],days=days))
    for model in src['openmeteo']['models']:
        if model not in data:errors.append(f'{model}: unavailable')
    for row in rows:
        for day in row['days'].values():
            day['source_error_count']=len(set(errors))+sum(s['status']=='failed' for s in quality.STATUS.values() if s['city']==row['city'])
    return dict(schema_version=2,model_version='2',kind=kind,generated_at=now_iso(),snapshot_id=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')+'-'+uuid.uuid4().hex[:8],
        errors=sorted(set(errors)),source_status=list(quality.STATUS.values()),cities=rows,
        families=copy.deepcopy(tcfg['families'] if kind=='temperature' else settings['families']),
        meteoblue_enabled=bool(mb),meteoblue_published=bool(mbcfg.get('publish_values')),
        meteoblue_status=meteoblue.publication_status(mbcfg,mb),
        restricted_source_policy='excluded_from_public_numeric_products' if not mbcfg.get('publish_values') else 'publication_enabled',
        execution_policy=copy.deepcopy(settings['execution']))

def validate(board):
    if not board.get('cities'):raise ValueError('No usable cities; keeping last good board')
    for c in board['cities']:
        for d in c['days'].values():
            if board['kind']=='temperature':
                qs=d['distribution']['quantiles']
                if qs!=sorted(qs):raise ValueError('Non-monotone quantiles')
                ps=[b['model_p'] for b in d['ladder']]
                if not d['gaps'] and abs(sum(ps)-1)>1e-5:raise ValueError('Bracket probabilities do not sum to one')
            else:ps=[d['consensus']]
            if any(not isinstance(p,(int,float)) or not 0<=p<=1 for p in ps):raise ValueError('Invalid probability')
            if not board['meteoblue_published']:
                if d.get('meteoblue') or 'METEOBLUE' in d.get('models',{}) or 'mlm' in d.get('families',{}) or 'METEOBLUE' in d.get('diagnostics',{}):raise ValueError('Restricted source in public payload')
    json.dumps(board,allow_nan=False)

def run(kind=None):
    settings=load_yaml(ROOT/'config/settings.yml');boards=[];failed=[]
    for k in ([kind] if kind else ['rain','temperature']):
        try:
            board=prepare(k,settings);validate(board)
            old=read_json(DATA/('board_temp.json' if k=='temperature' else 'board.json'))
            if old.get('schema_version')==2 and len(board['cities'])<len(old.get('cities',[]))*.75:
                raise ValueError('City coverage fell below 75%; retaining previous board')
            previous={(c['city'],d['date']):d for c in old.get('cities',[]) for d in c['days'].values()}
            for c in board['cities']:
                for d in c['days'].values():d['changes']=changes(d,previous.get((c['city'],d['date'])),k=='temperature')
            boards.append(board)
        except Exception as exc:
            failed.append(f'{k}: {type(exc).__name__}: {str(exc)[:180]}')
    calibration=read_json(ROOT/'config/calibration.json')
    ledger=read_json(DATA/'paper/ledger.json',[]);candidates=[]
    for board in boards:
        for c in board['cities']:
            for d in c['days'].values():
                pairs=[(b['market'],b.get('edge')) for b in d.get('ladder',[])] if board['kind']=='temperature' else [(d['market'],d.get('edge'))]
                for q,e in pairs:
                    if e:
                        e['eligibility']=policy.eligibility(c['city'],d,q,e,settings,calibration)
                        candidates.append(dict(city=c['city'],date=d['date'],kind=board['kind'],horizon=d['horizon'],snapshot_id=board['snapshot_id'],quote=q,edge=e))
    policy.allocate(candidates,settings,ledger)
    for board in boards:
        validate(board)
        prefix='temp-' if board['kind']=='temperature' else ''
        atomic_json(DATA/'history'/(prefix+board['snapshot_id']+'.json'),board)
        atomic_json(DATA/('board_temp.json' if prefix else 'board.json'),board)
    for c in candidates:
        e=c['edge'];n=e.get('suggested_contracts',0)
        if n:
            ledger.append(dict(id=uuid.uuid4().hex,created_at=now_iso(),ticker=c['quote']['ticker'],city=c['city'],date=c['date'],kind=c['kind'],horizon=c['horizon'],snapshot_id=c['snapshot_id'],side=e['side'],price=e['price'],quantity=n,cost_dollars=e['suggested_cost_dollars'],status='proposed',fill_assumed=False))
    atomic_json(DATA/'paper/ledger.json',ledger)
    from .performance import publish
    publish(fetch_outcomes=False)
    atomic_json(DATA/'status.json',dict(generated_at=now_iso(),status='degraded' if failed or any(b['errors'] for b in boards) else 'ok',errors=failed,
        boards={b['kind']:{'generated_at':b['generated_at'],'errors':b['errors'],'cities':len(b['cities'])} for b in boards}))
    print(f'Published {len(boards)} board(s); {len(failed)} failed')
    return 1 if failed else 0

if __name__=='__main__':sys.exit(run())
