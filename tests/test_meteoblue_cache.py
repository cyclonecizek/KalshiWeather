from datetime import datetime, timezone, timedelta
import json
import requests
from pipeline.sources import meteoblue as mb
from pipeline.model_inputs import describe_inputs

C = {'name':'Chicago','lat':41.78,'lon':-87.75,'tz':'UTC','elevation_m':188}

def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('METEOBLUE_KEY','test')
    cfg={'publish_values':True,'cache_path':str(tmp_path/'cache.json'),'max_calls_per_day':80,'cache_hours':8}
    dates=[(datetime.now(timezone.utc).date()+timedelta(days=i)).isoformat() for i in range(3)]
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'data_day':{'time':dates,'temperature_max':[80,81,82],'precipitation_probability':[10,20,30]}}
    def get(*a,**k):calls.append(k);return Response()
    monkeypatch.setattr(mb.requests,'get',get)
    return cfg,calls,dates

def test_location_identity_and_shared_board_cache(tmp_path,monkeypatch):
    cfg,calls,_=setup(tmp_path,monkeypatch)
    mb.fetch([C],cfg)
    mb.fetch([C],cfg)
    assert len(calls)==1
    mb.fetch([{**C,'lat':41.98,'lon':-87.9}],cfg)
    assert len(calls)==2

def test_stale_data_is_display_only_with_original_timestamp(tmp_path,monkeypatch):
    cfg,calls,_=setup(tmp_path,monkeypatch)
    mb.fetch([C],cfg)
    path=__import__('pathlib').Path(cfg['cache_path']);cache=json.loads(path.read_text())
    stamp=(datetime.now(timezone.utc)-timedelta(hours=9)).isoformat()
    for entry in cache['data'].values():entry['at']=stamp
    path.write_text(json.dumps(cache));cfg['max_calls_per_day']=1
    assert mb.fetch([C],cfg)=={}
    assert mb.DISPLAY['Chicago'][0]['stale']
    assert mb.DISPLAY['Chicago'][0]['retrieved_at']==stamp
    assert mb.publication_status(cfg,{})['stale_stations']==1
    assert mb.BUDGET['calls_used']==1 and len(calls)==1

def test_absolute_dates_survive_midnight(tmp_path,monkeypatch):
    cfg,calls,dates=setup(tmp_path,monkeypatch)
    mb.fetch([C],cfg)
    path=__import__('pathlib').Path(cfg['cache_path']);cache=json.loads(path.read_text())
    future=datetime.combine(datetime.fromisoformat(dates[1]).date(),datetime.min.time(),timezone.utc)+timedelta(minutes=30)
    for entry in cache['data'].values():entry['at']=(future-timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(cache))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return future.astimezone(tz)
    monkeypatch.setattr(mb,'datetime',Clock)
    result=mb.fetch([C],cfg)
    assert result['Chicago'][0]['tmax']==81
    assert result['Chicago'][1]['tmax']==82
    assert len(calls)==1

def test_failed_refresh_retains_stale_comparison(tmp_path,monkeypatch):
    cfg,calls,_=setup(tmp_path,monkeypatch)
    mb.fetch([C],cfg)
    path=__import__('pathlib').Path(cfg['cache_path']);cache=json.loads(path.read_text())
    for entry in cache['data'].values():entry['at']=(datetime.now(timezone.utc)-timedelta(hours=10)).isoformat()
    path.write_text(json.dumps(cache))
    def fail(*a,**k):raise requests.Timeout()
    monkeypatch.setattr(mb.requests,'get',fail)
    assert mb.fetch([C],cfg)=={}
    assert mb.DISPLAY['Chicago'][0]['stale']
    assert mb.STATUS['Chicago']=='request_failed'

def test_excluded_meteoblue_remains_visible_at_zero_weight():
    settings={'temperature':{'families':{'mlm':{'members':['METEOBLUE'],'weight':1}},'sources':{'meteoblue':{'publish_values':True}}}}
    day={'diagnostics':{'_observation_used':True},'meteoblue':{'tmax':80,'stale':True,'retrieved_at':'2026-09-17T00:00:00Z'}}
    row=describe_inputs(settings,'temperature',day)[0]
    assert row['value']==80 and not row['included'] and row['weight']==0
    assert 'Older guidance' in row['status']

def test_expired_included_source_blocks_policy_but_comparison_does_not():
    from pipeline.policy import eligibility
    now=datetime.now(timezone.utc)
    day={'model_inputs':[{'model':'METEOBLUE','included':True}],
         'meteoblue':{'expires_at':(now-timedelta(seconds=1)).isoformat()}}
    cfg={'execution':{'max_data_age_minutes':180,'require_calibration':False}}
    reasons=eligibility('Chicago',day,{},None,cfg,now=now)['reasons']
    assert 'Meteoblue input is stale; refresh forecast' in reasons
    day['model_inputs'][0]['included']=False
    assert not any('Meteoblue' in r for r in eligibility('Chicago',day,{},None,cfg,now=now)['reasons'])
