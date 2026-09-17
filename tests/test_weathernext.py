from copy import deepcopy
from unittest.mock import patch

from pipeline.sources import hourly, weathernext as wn
from pipeline.model_inputs import describe_inputs
from pipeline.run import source_details, ROOT
from pipeline.util import load_yaml


def setup(kind='temperature'):
    settings=load_yaml(ROOT/'config/settings.yml')
    city={'name':'Test','tz':'Etc/GMT+6'}
    detail={'maxima':[70+i/10 for i in range(64)],'remaining':[72]*64,
            'rain_totals':[0]*32+[1]*32,'past_totals':[0]*64,'future_totals':[0]*32+[1]*32,
            'hourly':[], 'retrieved_at':'2026-09-16T18:00:00+00:00'}
    day={'kind':kind,'observed':None,'experiments':{'variants':{}},
         'ladder':[{'lo':None,'hi':73},{'lo':74,'hi':None}],
         'diagnostics':{},'models':{},'distribution':{'median':80},'consensus':.25}
    return settings,city,detail,day


def test_fetch_selects_full_ensemble_and_explicit_hourly_output():
    with patch.object(hourly,'fetch',return_value={wn.MODEL:{'Test':{}}}) as fetch:
        assert wn.fetch([],{'models':{'ECMWF_ENS':'ecmwf'},'ensemble_base':'test'})=={'Test':{}}
        cfg=fetch.call_args.args[1]
        assert cfg['models']=={wn.MODEL:'google_weathernext2_ensemble'}
        assert cfg['temporal_resolution']=='hourly_1'
        assert cfg['batch_size']==1
        assert cfg['connect_timeout_seconds']==30
        assert cfg['read_timeout_seconds']==60


def test_temperature_archives_probabilities_without_altering_production():
    settings,city,detail,day=setup()
    original=deepcopy(settings)
    wn.attach(city,0,{'Test':{0:detail}},day,settings)
    assert settings==original
    assert day['distribution']=={'median':80} and day['consensus']==.25
    assert day['weathernext']['status']=='ok'
    assert day['weathernext']['member_count']==64
    assert day['weathernext']['weight']==0
    variant=day['experiments']['variants']['source:WEATHERNEXT2']
    assert abs(sum(variant['probabilities'])-1)<1e-9
    assert variant['quantiles']==sorted(variant['quantiles'])
    rows=describe_inputs(settings,'temperature',day)
    assert rows[-1]['model']=='WEATHERNEXT2' and not rows[-1]['included']
    assert rows[-1]['weight']==0 and rows[-1]['value'] is not None


def test_single_station_requests_use_configured_connection_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cities=[{'name':n,'lat':lat,'lon':-100,'tz':'UTC'} for n,lat in [('A',30),('B',40)]]
    summary={'maxima':[70]*64,'rain_totals':[0]*64,'window_start':'2026-09-17T00:00:00+00:00'}
    with patch.object(hourly.requests,'get') as get, patch.object(hourly,'summarize',return_value=summary):
        get.return_value.json.return_value={'hourly':{'time':[]}}
        wn.fetch(cities,{'ensemble_base':'https://example.test/ensemble'})
        assert get.call_count==2
        for call in get.call_args_list:
            assert call.kwargs['timeout']==(30,60)
            assert ',' not in call.kwargs['params']['latitude']


def test_rain_archives_member_probability_and_observation_override():
    settings,city,detail,day=setup('rain')
    wn.attach(city,0,{'Test':{0:detail}},day,settings)
    assert .4 < day['weathernext']['value'] < .6
    assert day['consensus']==.25
    day['observed']={'precip_complete':True,'precip_mm':1}
    wn.attach(city,0,{'Test':{0:detail}},day,settings)
    assert day['weathernext']['value']==.98
    assert day['experiments']['variants']['source:WEATHERNEXT2']['probabilities']==[.98]


def test_missing_or_insufficient_members_do_not_create_scores():
    settings,city,detail,day=setup()
    wn.attach(city,0,{},day,settings)
    assert day['weathernext']['status']=='unavailable'
    assert not day['experiments']['variants']
    detail['maxima']=[75,76]
    wn.attach(city,0,{'Test':{0:detail}},day,settings)
    assert day['weathernext']['status']=='insufficient_members'
    assert not day['experiments']['variants']


def test_research_source_never_changes_production_source_ages_or_counts():
    _,_,detail,_=setup()
    with patch.dict(hourly.DETAILS,{('Test',0,wn.MODEL):detail},clear=True):
        assert source_details('Test',0)=={}
