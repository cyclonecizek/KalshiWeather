"""Refresh current contract quotes without reissuing or altering forecasts."""
from concurrent.futures import ThreadPoolExecutor
from .kalshi import Kalshi,effective_fee_rate
from .quality import atomic_json,now_iso
from .util import load_yaml
from .run import ROOT,DATA,read_json,validate,set_edge_depth
from . import policy,settlement
from .build_temp import evaluate_bracket
from .blend import evaluate
from .products import TEMPERATURE_KINDS, BOARD_FILES, temperature_config
from .brackets import implied_distribution,implied_quantiles


def refresh(board,settings,fetch_market,fetch_fee,depth=None):
    failures=0
    items=[]
    for city in board['cities']:
        for day in city['days'].values():
            for bracket in day.get('ladder',[{'market':day.get('market'),'edge':day.get('edge')} ]):
                items.append((city,day,bracket))
    tickers={b['market']['ticker'] for _,_,b in items if b.get('market')}
    def get(ticker):
        try:
            raw=fetch_market(ticker)
            return ticker,raw if raw and raw.get('ticker')==ticker else None
        except Exception:return ticker,None
    with ThreadPoolExecutor(max_workers=6) as pool:markets=dict(pool.map(get,sorted(tickers)))
    fees={}
    for c in board['cities']:
        try:fees[c['series']]=fetch_fee(c['series'])
        except Exception:fees[c['series']]=None
    calibration=read_json(ROOT/'config/calibration.json')
    for city,day,b in items:
        old=b.get('market') or {};raw=markets.get(old.get('ticker'))
        if raw is None:
            # Invalidate this quote immediately; never restamp its old price.
            failures+=1
            old['executable']=False;old['quote_error']='Latest quote refresh failed'
            old['mid']=None
            edge=b.get('edge')
            if edge:edge['eligibility']={'eligible':False,'reasons':['Latest quote refresh failed']}
            continue
        q=Kalshi.quote(raw)
        multiplier=fees[city['series']]
        rate=effective_fee_rate(multiplier,.07)
        day['fee_verified']=multiplier is not None
        q['fee_multiplier']=multiplier
        if board['kind'] in TEMPERATURE_KINDS:
            b['market']=q;b['edge']=evaluate_bracket(b['model_p'],q,rate,temperature_config(settings,board['kind']))
            edge=b['edge']
        else:
            day['market']=q;day['edge']=evaluate(day['consensus'],q,settings);edge=day['edge']
        if edge:
            edge['fee_rate']=rate
            if depth:depth(q,edge)
            else:edge['depth']=q.get('yes_depth' if edge['side']=='YES' else 'no_depth')
            edge['eligibility']=policy.eligibility(city['city'],day,q,edge,settings,calibration)
            edge['suggested_contracts']=0
    # Recheck settlement against the actual refreshed contract text, not old registry excerpts.
    for city in board['cities']:
        for day in city['days'].values():
            brackets=day.get('ladder',[{'market':day.get('market'),'edge':day.get('edge')}])
            raws=[markets.get(b['market']['ticker']) for b in brackets]
            spec=settlement.verify({'settlement':day.get('settlement',{})},[r for r in raws if r],day['date'])
            if any(r is None for r in raws):spec.update(verified=False,reasons=spec['reasons']+['Current contract rules unavailable'])
            day['settlement']=spec
            if board['kind'] in TEMPERATURE_KINDS:
                probabilities,overround=implied_distribution(brackets)
                for b,p in zip(brackets,probabilities):b['implied']=p
                day['overround']=overround
                quantiles=implied_quantiles(brackets,probabilities)
                day['market_forecast']={'median':quantiles.get(.5),'p10':quantiles.get(.1),'p90':quantiles.get(.9)} if quantiles else None
            for b in brackets:
                if b.get('edge'):
                    b['edge']['eligibility']=policy.eligibility(city['city'],day,b['market'],b['edge'],settings,calibration)
    board['quotes_updated_at']=now_iso()
    board['quote_refresh']={'failures':failures,'contracts':len(items)}
    return board


def main():
    settings=load_yaml(ROOT/'config/settings.yml')
    base=settings['sources']['kalshi']['base']
    kal=Kalshi(base)
    def market(ticker):return Kalshi(base).market(ticker)
    def fee(series):return kal._get('/series/'+series)['series'].get('fee_multiplier')
    for name in BOARD_FILES.values():
        board=read_json(DATA/name)
        if not board.get('cities'):continue
        refresh(board,settings,market,fee,lambda q,e:set_edge_depth(kal,q,e))
        validate(board);atomic_json(DATA/name,board)
        print(f"{name}: refreshed {board['quote_refresh']['contracts']} quotes; {board['quote_refresh']['failures']} failed")

if __name__=='__main__':main()
