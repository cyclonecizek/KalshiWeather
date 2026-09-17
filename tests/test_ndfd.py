from datetime import datetime, timezone
from types import SimpleNamespace
import requests
from pipeline.sources import ndfd
from pipeline.model_inputs import describe_inputs

CITY = dict(name='Test', tz='America/New_York', lat=40, lon=-74)

def xml(value='85'):
    day=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo(CITY['tz'])).date()
    return f'''<dwml><head><product><creation-date>2026-09-17T12:00:00Z</creation-date></product></head><data>
    <time-layout><layout-key>k</layout-key><start-valid-time>{day}T07:00:00-04:00</start-valid-time><end-valid-time>{day}T19:00:00-04:00</end-valid-time></time-layout>
    <parameters><temperature type="maximum" time-layout="k"><value>{value}</value></temperature>
    <probability-of-precipitation time-layout="k"><value>0</value></probability-of-precipitation></parameters></data></dwml>'''

def test_retry_and_keep_original_period_and_provenance(monkeypatch):
    calls=[]
    def get(*args,**kwargs):
        calls.append(kwargs)
        if len(calls)==1:raise requests.Timeout()
        return SimpleNamespace(text=xml(),raise_for_status=lambda:None)
    monkeypatch.setattr(ndfd.requests,'get',get)
    monkeypatch.setattr(ndfd.time,'sleep',lambda _:None)
    result=ndfd.fetch([CITY],{'base':'https://example.invalid'},'temperature')
    assert result['Test'][0]==85
    d=ndfd.DETAILS[('temperature','Test',0)]
    assert d['issued_at'] is None and d['product_generated_at'] and d['retrieved_at']
    assert d['periods'][0]['start'].endswith('07:00:00-04:00')
    assert len(calls)==2
    assert ndfd.DETAILS[('temperature','Test',1)]['status']=='no_period'

def test_failed_refresh_does_not_reuse_old_value(monkeypatch):
    def get(*args,**kwargs):raise requests.Timeout()
    monkeypatch.setattr(ndfd.requests,'get',get)
    monkeypatch.setattr(ndfd.time,'sleep',lambda _:None)
    ndfd.DETAILS[('temperature','Test',0)]={'value':85}
    assert ndfd.fetch([CITY],{'base':'x'},'temperature')['Test']=={}
    d=ndfd.DETAILS[('temperature','Test',0)]
    assert d['status']=='failed' and d['value'] is None and d['retrieved_at'] is None

def test_rain_zero_is_valid(monkeypatch):
    monkeypatch.setattr(ndfd.requests,'get',lambda *a,**k:SimpleNamespace(text=xml(),raise_for_status=lambda:None))
    assert ndfd.fetch([CITY],{'base':'x'},'rain')['Test'][0]==0

def test_excluded_guidance_retains_value_without_weight():
    cfg={'families':{'nws':{'members':['NDFD'],'weight':1}}}
    settings={'temperature':cfg}
    day={'diagnostics':{'_observation_used':True},'nws_guidance':{'value':85,'status':'ok','message':'available'}}
    row=describe_inputs(settings,'temperature',day)[0]
    assert row['value']==85 and row['weight']==0 and not row['included']
    assert 'excluded' in row['status']
    day['nws_guidance']={'value':None,'status':'failed','message':'NWS download failed after 3 attempts'}
    assert 'download failed' in describe_inputs(settings,'temperature',day)[0]['status']
