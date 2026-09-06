"""One eligibility and portfolio policy for the board and paper ledger."""
from datetime import datetime,timezone
import math
from .quality import age_minutes
from .util import kalshi_fee_cents

def order_fee(price,quantity,rate=.07):
    p=price/100
    return math.ceil(rate*quantity*p*(1-p)*100)/100

def eligibility(city,day,quote,edge,settings,calibration=None,now=None):
    cfg=settings['execution'];now=now or datetime.now(timezone.utc);why=[]
    if not day.get('settlement',{}).get('verified'):why.append('Settlement definition unverified')
    if not quote.get('executable'):why.append('Executable bid and ask unavailable')
    if not day.get('fee_verified'):why.append('Series fee metadata unverified')
    if quote.get('status') not in ('open','active'):why.append('Market status unconfirmed or inactive')
    if age_minutes(quote.get('retrieved_at'),now)>cfg.get('max_quote_age_minutes',20):why.append('Quote stale or age unknown')
    if age_minutes(day.get('forecast_retrieved_at'),now)>cfg['max_data_age_minutes']:why.append('Forecast stale or age unknown')
    if day.get('source_error_count',0)>cfg.get('max_source_errors',1):why.append('Too many source failures')
    if day.get('data_quality')!='ok':why.append('Incomplete source data')
    if day.get('n_families',0)<settings.get('min_families_for_signal',2):why.append('Insufficient model families')
    if cfg.get('require_calibration',True):
        key=f"{city}|{day.get('kind')}|{day.get('horizon')}"
        fitted=(calibration or {}).get(key,{})
        if not fitted.get('validated') or fitted.get('n',0)<20 or fitted.get('model_version')!='2':why.append('Out-of-sample calibration pending')
    if day.get('elapsed',0)>cfg.get('max_elapsed',.75):why.append('Reporting window nearly complete')
    try:
        close=datetime.fromisoformat(quote['close_time'].replace('Z','+00:00'))
        if close<=now:why.append('Market closed')
    except (KeyError,ValueError,TypeError,AttributeError):why.append('Market closing time unknown')
    if not edge:why.append('No executable edge')
    else:
        if not cfg['min_edge_cents']<=edge['ev_cents']<=cfg['max_edge_cents']:why.append('Edge outside policy range')
        if quote.get('spread') is None or quote['spread']>cfg['max_spread_cents']:why.append('Missing or wide spread')
        if edge.get('depth') is None or edge['depth']<cfg['min_depth']:why.append('Insufficient confirmed depth')
    return {'eligible':not why,'reasons':why}

def allocate(candidates,settings,ledger=()):
    """Allocate a single daily budget across all cities, sides and both boards."""
    cfg=settings['execution'];today=datetime.now(timezone.utc).date().isoformat()
    used=sum(x['cost_dollars'] for x in ledger if x.get('created_at','').startswith(today))
    seen={x['ticker'] for x in ledger if x.get('created_at','').startswith(today)}
    city_used={}
    for x in ledger:
        if x.get('created_at','').startswith(today):city_used[x['city']]=city_used.get(x['city'],0)+x['cost_dollars']
    for c in sorted(candidates,key=lambda x:-x['edge']['ev_cents']):
        e,q=c['edge'],c['quote'];e['suggested_contracts']=0
        if not e['eligibility']['eligible']:continue
        if q['ticker'] in seen:
            e['eligibility']={'eligible':False,'reasons':['Paper order already recorded today']};continue
        budget=min(cfg['daily_budget_dollars']-used,cfg['max_per_market_dollars'],
            cfg.get('max_per_city_dollars',25)-city_used.get(c['city'],0),
            cfg['bankroll_dollars']*e.get('kelly',0))
        n=max(0,min(math.floor(e.get('depth') or 0),math.floor(budget/(e['price']/100))))
        rate=e.get('fee_rate',.07)
        while n and n*e['price']/100+order_fee(e['price'],n,rate)>budget:n-=1
        if not n:
            e['eligibility']={'eligible':False,'reasons':['Portfolio budget exhausted or stake too small']};continue
        cost=round(n*e['price']/100+order_fee(e['price'],n,rate),2)
        e.update(suggested_contracts=n,suggested_cost_dollars=cost)
        used+=cost;city_used[c['city']]=city_used.get(c['city'],0)+cost;seen.add(q['ticker'])
