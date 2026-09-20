from copy import deepcopy
from datetime import datetime,timezone
import json
import pytest
from pipeline import forecastex as fx
from pipeline.tempdist import normal_quantiles


def fixture(kind='temperature'):
    city=dict(city='Chicago',icao='KMDW',tz='America/Chicago')
    d=dict(date='2026-09-20',window_start='2026-09-20T00:00:00-06:00',window_end='2026-09-21T00:00:00-06:00',
        distribution=dict(median=80,quantiles=normal_quantiles(80,2)),market_forecast=dict(median=80),ladder=[])
    for i,(lo,hi,p) in enumerate([(None,78,.2),(79,80,.5),(81,None,.3)]):
        d['ladder'].append(dict(lo=lo,hi=hi,label=str(i),model_p=p,implied=p,market=dict(ticker='K'+str(i),retrieved_at='2026-09-20T11:50:00Z')))
    b=dict(kind=kind,cities=[dict(city,days={'0':d})],generated_at='2026-09-20T11:00:00Z',snapshot_id='snapshot')
    product=('UL' if kind=='temperature_low' else 'UH')+'MDW'
    raw=[dict(product_id=product,contract_id=product+'_092026_'+str(k),question='Temperature at (KMDW)',
              exchange_spec_url=fx.RULES,last_yes_price=p,open_interest=100) for k,p in [(78,.8),(79,.6),(80,.3),(81,.1)]]
    return city,d,b,raw


def test_exact_station_date_source_and_threshold_mapping():
    c,d,b,raw=fixture();old=deepcopy(b)
    r=fx.compare(c,d,b,raw,'2026-09-20T11:50:00Z')
    assert r['station_match'] and not r['actionable'] and not r['settlement_equivalent']
    assert not r['window_match'] and r['window_start'].endswith('-05:00')
    assert r['median']==80 and r['trade_time'] is None
    assert r['rows'][0]['kalshi_probability']==pytest.approx(.8)
    assert r['rows'][1]['kalshi_probability'] is None  # splits 79–80 bracket
    assert r['rows'][2]['kalshi_probability']==pytest.approx(.3)
    assert sum(x['probability'] for x in r['brackets'])==pytest.approx(1)
    assert b==old
    for field,value in [('question','Temperature at KORD'),('exchange_spec_url','https://example.com/rules'),('contract_id','UHMDW_092126_78')]:
        bad=[dict(raw[0],**{field:value})]
        assert not fx.compare(c,d,b,bad,'2026-09-20T11:50:00Z')['station_match']


def test_low_strict_inequality_and_missing_boundaries():
    c,d,b,raw=fixture('temperature_low')
    for r,p in zip(raw,[.1,.2,.6,.8]):r['last_yes_price']=p
    r=fx.compare(c,d,b,raw,'2026-09-20T11:50:00Z')
    assert r['median']==79
    assert r['rows'][1]['kalshi_probability']==pytest.approx(.2) # below79
    assert r['rows'][2]['kalshi_probability'] is None # below80 splits bracket
    assert r['rows'][3]['kalshi_probability']==pytest.approx(.7)
    raw[2]['last_yes_price']=None
    assert fx.compare(c,d,b,raw,'2026-09-20T11:50:00Z')['median'] is None


def test_no_monotone_or_tail_fabrication():
    c,d,b,raw=fixture()
    raw[1]['last_yes_price']=.9
    r=fx.compare(c,d,b,raw,'2026-09-20T11:50:00Z')
    assert r['monotonicity_adjusted']
    assert r['curve'][0]['cdf']==r['curve'][1]['cdf']
    assert fx.curve([dict(strike=78,probability=.9)],'temperature')[1] is None
    assert fx.curve([dict(strike=78,probability=.9),dict(strike=80,probability=.1)],'temperature')[1] is None
    raw[0]['last_yes_price']=float('nan')
    r=fx.compare(c,d,b,raw,'2026-09-20T11:50:00Z')
    assert r['brackets'][0]['probability'] is None
    json.dumps(r,allow_nan=False)


def test_request_failure_is_local_and_old_values_are_not_restamped():
    c,d,b,raw=fixture()
    def fail(_):raise TimeoutError()
    report=fx.build([b],fail)
    assert report['records'][0]['status']=='unavailable'
    assert report['records'][0]['rows']==[]
    assert 'TimeoutError' in report['records'][0]['message']


def test_pagination_and_duplicate_rejection(monkeypatch):
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return dict(statusCode=200,body=dict(data='[]',next_page=2))
    def get(*a,**kw):calls.append(kw['params']['page']);return Response()
    monkeypatch.setattr(fx.requests,'get',get)
    with pytest.raises(ValueError,match='pagination'):fx.fetch_contracts('UHMDW')
    assert calls==[1,2]
    c,d,b,raw=fixture()
    with pytest.raises(ValueError,match='Duplicate'):fx.compare(c,d,b,raw+[raw[0]],'2026-09-20T11:50:00Z')


def test_settlement_prices_reject_daily_marks_open_interest_and_unpaired_sides():
    header='event_contract,subtype,expiration_date,date,settlement_price,open_interest\n'
    lines='UHMDW_091926_79,YES,2026-09-20T01:00:00-05:00,2026-09-20,1,0\nUHMDW_091926_79,NO,2026-09-20T01:00:00-05:00,2026-09-20,0,0\n'
    now=datetime(2026,9,22,tzinfo=timezone.utc)
    assert fx.settlement_prices(header+lines,'2026-09-20',now)['UHMDW_091926_79']['result']==1
    assert not fx.settlement_prices(header+lines.replace(',1,0',',.99,0'),'2026-09-20',now)
    assert not fx.settlement_prices(header+lines.replace(',1,0',',1,10'),'2026-09-20',now)
    assert not fx.settlement_prices(header+lines.splitlines()[0],'2026-09-20',now)
    assert not fx.settlement_prices(header+lines,'2026-09-20',datetime(2026,9,20,tzinfo=timezone.utc))
    conflict='UHMDW_091926_79,YES,2026-09-20T01:00:00-05:00,2026-09-20,0,0\n'
    assert not fx.settlement_prices(header+lines+conflict,'2026-09-20',now)


def test_archive_is_immutable_and_cutoff_selected(monkeypatch,tmp_path):
    c,d,b,raw=fixture()
    (tmp_path/'board_temp.json').write_text(json.dumps(b))
    monkeypatch.setattr(fx,'now_iso',lambda:'2026-09-20T12:30:00+00:00') # 07:30 civil time
    fx.publish(tmp_path,lambda _:raw)
    paths=list((tmp_path/'forecastex/history').glob('*.json'))
    assert len(paths)==1
    original=paths[0].read_text()
    raw[1]['last_yes_price']=.01
    monkeypatch.setattr(fx,'now_iso',lambda:'2026-09-20T12:40:00+00:00')
    fx.publish(tmp_path,lambda _:raw)
    assert len(list((tmp_path/'forecastex/history').glob('*.json')))==1
    assert paths[0].read_text()==original
    report=json.loads((tmp_path/'forecastex_verification.json').read_text())
    assert len(report['records'])==1 and report['records'][0]['horizon']=='morning'
    assert report['records'][0]['fx_settlement_inferred'] is None


def test_verification_infers_only_single_integer_and_separates_targets(tmp_path):
    c,d,b,raw=fixture();r=fx.compare(c,d,b,raw,'2026-09-20T12:30:00+00:00');r['archive_horizon']='morning'
    folder=tmp_path/'forecastex/history';folder.mkdir(parents=True)
    (folder/'snapshot.json').write_text(json.dumps(dict(records=[r])))
    out={x['contract_id']:dict(result=int(x['strike']<80),retrieved_at='2026-09-22T00:00:00+00:00') for x in r['rows']}
    (tmp_path/'forecastex/outcomes.json').write_text(json.dumps(out))
    (tmp_path/'outcomes.json').write_text(json.dumps({t:dict(status='finalized',result=int(t=='K2'),actual_value=81) for t in r['kalshi_tickers']}))
    result=fx.verify(tmp_path,False)['records'][0]
    assert result['fx_settlement_inferred']==80
    assert result['kalshi_actual']==81 and result['settlement_difference_f']==-1
    assert result['fx_mae_on_kalshi']==1
    del out['UHMDW_092026_79']
    (tmp_path/'forecastex/outcomes.json').write_text(json.dumps(out))
    assert fx.verify(tmp_path,False)['records'][0]['fx_settlement_inferred'] is None
    out['UHMDW_092026_78']['result']=0
    out['UHMDW_092026_81']['result']=1
    (tmp_path/'forecastex/outcomes.json').write_text(json.dumps(out))
    assert fx.verify(tmp_path,False)['records'][0]['fx_brier'] is None
