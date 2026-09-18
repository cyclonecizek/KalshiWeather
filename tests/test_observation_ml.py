from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pipeline import observation_ml as ml


def fixture():
    source={'retrieved_at':'2026-09-18T10:00:00Z','hourly':[
        {'time':f'2026-09-18T{h:02d}:00:00Z','median':72+h/10} for h in range(10,24)]}
    day={'date':'2026-09-18','window_start':'2026-09-18T05:00:00Z','window_end':'2026-09-19T05:00:00Z',
         'observed':{'temperature_complete':True,'max_f':77,'hourly':[
             {'time':f'2026-09-18T{h:02d}:51:00Z','temperature_f':70+h/3} for h in range(11,17)]},
         'distribution':{'median':80},'sources':{'A':source,'B':deepcopy(source)},'model_fingerprint':'settings1',
         'ladder':[{'lo':None,'hi':80,'market':{'ticker':'low'}},{'lo':81,'hi':None,'market':{'ticker':'high'}}]}
    return day,[('2026-09-18T10:30:00Z',deepcopy(day))]


def test_future_issued_guidance_cannot_explain_past_observations():
    day,prior=fixture()
    f,reason=ml.features(day,prior,'2026-09-18T17:00:00Z')
    assert f and reason is None and len(f['values'])==len(ml.FEATURES)
    changed=deepcopy(prior[0][1])
    for s in changed['sources'].values():
        s['retrieved_at']='2026-09-18T17:00:00Z'
        for p in s['hourly']:p['median']=150
    future=prior+[('2026-09-18T17:00:00Z',changed)]
    assert ml.features(day,future,'2026-09-18T17:00:00Z')[0]==f
    assert ml.features(day,future[1:],'2026-09-18T17:00:00Z')[0] is None


def test_missing_observations_and_remaining_guidance_fail_closed():
    day,prior=fixture();day['observed']['temperature_complete']=False
    assert ml.features(day,prior,'2026-09-18T17:00:00Z')[0] is None
    day,prior=fixture();day['sources']={}
    assert ml.features(day,prior,'2026-09-18T17:00:00Z')[0] is None


def rows():
    result=[]
    for i in range(30):
        date=datetime(2026,7,1,tzinfo=timezone.utc)+timedelta(days=i)
        f={'values':[70,0,5,4,2,1,1,1,1,12,0,1],'observed_high':70,'baseline':75}
        result.append({'date':date.date().isoformat(),'city':'Test','features':f,'actual':74,
            'settled_at':(date+timedelta(days=2)).isoformat(),'fingerprint':'settings1'})
    return result


def test_fit_interval_and_target_dates_stay_separate(monkeypatch):
    day,_=fixture();engine=ml.Engine.__new__(ml.Engine)
    engine.history={};engine.rows=rows();engine.models={}
    f=deepcopy(engine.rows[0]['features']);f['observed_high']=77
    monkeypatch.setattr(ml,'features',lambda *a:(f,None))
    engine.attach('Test',day,'2026-09-18T17:00:00Z')
    candidate=day['observation_ml']
    assert candidate['status']=='candidate' and candidate['applied'] is False
    assert candidate['fit_before']=='2026-07-21' and candidate['last_training_date']=='2026-07-30'
    assert min(candidate['quantiles'])>=77
    assert abs(sum(candidate['probabilities'])-1)<1e-7
    original=candidate['median']
    engine.rows += [{**rows()[0],'date':'2026-09-18','actual':140},
                    {**rows()[0],'date':'2026-08-01','settled_at':'2026-09-19T00:00:00Z','actual':140}]
    engine.models={};engine.attach('Test',day,'2026-09-18T17:00:00Z')
    assert day['observation_ml']['median']==original
    engine.rows=rows()[:29];engine.models={};engine.attach('Test',day,'2026-09-18T17:00:00Z')
    assert day['observation_ml']['status']=='collecting'


def test_scoring_requires_archived_candidate_and_final_outcome():
    day,_=fixture()
    assert ml.score_candidate(day,{}) is None
    day['distribution'].update(p10=75,p90=85)
    for b,p in zip(day['ladder'],[.6,.4]):b['model_p']=p
    day['observation_ml']={'version':ml.VERSION,'status':'candidate','tickers':['low','high'],
        'probabilities':[.7,.3],'median':79,'p10':76,'p90':82}
    outcomes={'low':{'status':'finalized','result':1,'actual_value':79},'high':{'status':'finalized','result':0}}
    score=ml.score_candidate(day,outcomes)
    assert score['mae']==0 and score['baseline_mae']==1
    outcomes['low']['status']='determined'
    assert ml.score_candidate(day,outcomes) is None
