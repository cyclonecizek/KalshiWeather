from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import pytest
import requests
from pipeline import policy,settlement
from pipeline.calibration_review import review,model_fingerprint
from pipeline.util import load_yaml
from pipeline.sources import hourly
from pipeline.refresh_quotes import refresh
from pipeline.run import ROOT

NOW=datetime.now(timezone.utc)

def settings():return load_yaml(ROOT/'config/settings.yml')

def fixture():
    cfg=settings();spec=settlement.registry()['New York']['temperature']
    date=NOW.date().isoformat()
    end=datetime.combine(NOW.date()+timedelta(days=1),datetime.min.time(),timezone(timedelta(hours=-5)))
    d={'date':date,'window_start':(end-timedelta(days=1)).isoformat(),'window_end':end.isoformat(),
       'forecast_retrieved_at':NOW.isoformat(),'kind':'temperature','horizon':'day_ahead',
       'settlement':{**spec,'verified':True},'fee_verified':True,'data_quality':'ok',
       'n_families':1,'n_guidance_centres':3,'elapsed':0,'source_error_count':3}
    q={'ticker':'TEST','yes_bid':39,'yes_ask':40,'no_ask':61,'retrieved_at':NOW.isoformat(),
       'close_time':end.isoformat(),'executable':True,'status':'open','spread':1,'yes_depth':100,'no_depth':100}
    e={'side':'YES','price':40,'ev_cents':15,'depth':100,'fee_rate':.07}
    d['ladder']=[{'model_p':.6,'market':q,'edge':e,'lo':None,'hi':80}]
    return cfg,d,q,e

def test_usable_centres_can_qualify_despite_optional_failures():
    cfg,d,q,e=fixture();cfg['execution']['require_calibration']=False;cfg['execution']['max_elapsed']=1
    assert policy.eligibility('New York',d,q,e,cfg,now=NOW)['eligible']
    d['n_guidance_centres']=1
    assert not policy.eligibility('New York',d,q,e,cfg,now=NOW)['eligible']

def test_full_eligibility_path_requires_review_matching_model():
    cfg,d,q,e=fixture();cfg['execution']['max_elapsed']=1
    approved={'New York|temperature|day_ahead':{'validated':True,'n':20,'model_version':'2','model_fingerprint':model_fingerprint()}}
    assert policy.eligibility('New York',d,q,e,cfg,approved,now=NOW)['eligible']
    e['eligibility']=policy.eligibility('New York',d,q,e,cfg,approved,now=NOW)
    e['kelly']=.10
    policy.allocate([{'city':'New York','quote':q,'edge':e}],cfg)
    assert e['suggested_contracts']>0 and 0<e['suggested_cost_dollars']<=15
    approved['New York|temperature|day_ahead']['model_fingerprint']='another-model'
    assert not policy.eligibility('New York',d,q,e,cfg,approved,now=NOW)['eligible']

def test_review_counts_days_not_multiple_snapshots_and_does_not_auto_approve():
    rows=[{'city':'A','kind':'rain','horizon':'morning','model_version':'2','model_fingerprint':model_fingerprint(),'date':f'2026-09-{i+1:02}',
        'issued_at':'2026-09-01T00:00:00+00:00','brier':.01,'market_brier':.25,'pairs':[[.9,1]]} for i in range(20)]
    result=review(rows+rows)['A|rain|morning']
    assert result['n']==20 and result['ready_for_review']
    assert 'validated' not in result
    assert not review(rows[:5])['A|rain|morning']['ready_for_review']

def test_contract_window_rejects_changed_station_source_and_dst_boundary():
    spec=settlement.registry()['New York']['temperature']
    raw={'rules_primary':'Maximum temperature CLINYC according to The Weather Company', 'close_time':'2026-09-14T05:00:00Z'}
    assert settlement.verify({'settlement':spec},[raw],'2026-09-13')['verified']
    for changes in [{'close_time':'2026-09-14T04:00:00Z'}, {'rules_primary':'Another station and source'}]:
        assert not settlement.verify({'settlement':spec},[{**raw,**changes}],'2026-09-13')['verified']

def test_quote_refresh_preserves_forecast_snapshot_and_invalidates_failed_prices():
    cfg,d,q,e=fixture()
    board={'kind':'temperature','snapshot_id':'original','generated_at':NOW.isoformat(),
           'cities':[{'city':'New York','series':'KXHIGHNY','days':{'0':d}}]}
    d['distribution']={'median':80,'quantiles':[80]*15}
    before=deepcopy(d['distribution'])
    raw={**q,'_retrieved_at':NOW.isoformat(),'yes_ask':42,'yes_bid':41,
         'rules_primary':'Maximum temperature CLINYC according to The Weather Company'}
    refresh(board,cfg,lambda _:raw,lambda _:1)
    assert board['generated_at']==NOW.isoformat() and board['snapshot_id']=='original'
    assert d['distribution']==before and d['ladder'][0]['market']['yes_ask']==42
    stamp=d['ladder'][0]['market']['retrieved_at']
    def fail(_):raise requests.Timeout()
    refresh(board,cfg,fail,lambda _:1)
    assert d['ladder'][0]['market']['retrieved_at']==stamp
    assert not d['ladder'][0]['market']['executable']
    assert not d['ladder'][0]['edge']['eligibility']['eligible']

def test_ensemble_batches_retry_and_share_per_city_cache(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    cities=[{'name':str(i),'lat':i,'lon':i,'tz':'UTC'} for i in range(3)]
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return [{'hourly':{'time':[]}} for _ in range(len(calls[-1]['latitude'].split(',')))]
    def get(url,params,timeout):
        calls.append(params)
        if len(calls)==1:raise requests.Timeout()
        return Response()
    monkeypatch.setattr(hourly.requests,'get',get)
    cfg={'ensemble_base':'https://example.test','models':{'TEST':'test'},'batch_size':2}
    assert len(hourly.fetch(cities,cfg)['TEST'])==3
    assert len(calls)==3 and max(len(c['latitude'].split(',')) for c in calls)==2
    hourly.fetch(cities[:2],cfg)
    assert len(calls)==3

def test_calibration_approval_is_owner_only_and_audited(tmp_path,monkeypatch):
    from pipeline import calibration_review as cal
    monkeypatch.setattr(cal,'ROOT',tmp_path)
    (tmp_path/'config').mkdir()
    with pytest.raises(ValueError,match='owner'):cal.approve('A|rain|morning','outsider','owner')
    report={'performance_generated_at':NOW.isoformat(),'model_fingerprint':'fingerprint',
            'groups':{'A|rain|morning':{'ready_for_review':False,'n':5}}}
    monkeypatch.setattr(cal,'publish',lambda:report)
    with pytest.raises(ValueError,match='criteria'):cal.approve('A|rain|morning','owner','owner')
    assert not (tmp_path/'config/calibration.json').exists()
    report['groups']['A|rain|morning'].update(ready_for_review=True,n=20,evidence_sha256='evidence')
    cal.approve('A|rain|morning','owner','owner')
    approved=json.loads((tmp_path/'config/calibration.json').read_text())['A|rain|morning']
    assert approved['validated'] and approved['reviewed_by']=='owner'
    assert approved['model_fingerprint']=='fingerprint' and approved['evidence_sha256']=='evidence'
