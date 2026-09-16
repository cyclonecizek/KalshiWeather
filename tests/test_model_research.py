from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest

from pipeline import model_research as research
from pipeline.experiments import archive_rain, archive_temperature
from pipeline.blend import blend
from pipeline.build_temp import build_distribution
from pipeline.tempdist import normal_quantiles


def settings():
    families = {'global': {'weight': .6, 'members': ['A', 'B']},
                'point': {'weight': .4, 'members': ['C']}}
    return {'families': families, 'member_weights': {}, 'calibration': {},
            'temperature': {'families': deepcopy(families), 'bias': {},
                'spread_factor': 1, 'sources': {'meteoblue': {'publish_values': False}}}}


def rows(n=60, kind='rain'):
    result = []
    for i in range(n):
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(days=i)
        q = normal_quantiles(80, 1)
        y = i % 2
        baseline = research.probability_scores('rain', [.5], [y])
        result.append(dict(city='A', kind=kind, horizon='morning', date=at.date().isoformat(),
            issued_at=at.isoformat(), settled_at=(at+timedelta(hours=12)).isoformat(),
            model_fingerprint='current', model_version='2', brier=.25, market_brier=.1,
            actual=82 if kind=='temperature' else None, pairs=[(.5, y)],
            quantiles=q if kind=='temperature' else None, floor=None,
            bounds=[(None, 81), (82, None)] if kind=='temperature' else [],
            baseline=baseline, variants={}))
    return result


def test_rain_removal_recomputes_family_weights_and_keeps_original():
    cfg=settings(); probs={'A':.2, 'B':.8, 'C':.9}
    original=deepcopy(cfg)
    day={**blend(probs,cfg),'market':{'ticker':'RAIN'}}
    exp=archive_rain(probs,cfg,day)['variants']
    assert exp['source:A']['probabilities']==pytest.approx([.2])
    assert exp['without:C']['probabilities']==pytest.approx([.5])
    assert exp['weight:A:0.5']['probabilities'][0] > day['consensus']
    assert cfg==original and day['consensus']==pytest.approx(.66)
    assert exp['weight:C:0.5']['probabilities'][0] < day['consensus']
    assert exp['weight:C:0.5']['weight_scope']=='family'
    observed=archive_rain(probs,cfg,{**day,'obs_effect':'observed'})
    assert all(v['probabilities']==[.98] for v in observed['variants'].values())


def test_temperature_experiments_use_complete_distributions_and_probabilities():
    cfg=settings()['temperature']; city={'name':'A'}
    members={'A':{'A':{0:[78,80,82]}},'B':{'A':{0:[82,84,86]}}}
    points={'C':{'A':{0:85}}}
    dist,diag=build_distribution(city,0,members,points,cfg,[])
    day={'diagnostics':diag, 'ladder':[{'lo':None,'hi':81,'market':{'ticker':'LOW'}},
                                     {'lo':82,'hi':None,'market':{'ticker':'HIGH'}}]}
    before=dist.v[:]
    exp=archive_temperature(city,0,members,points,cfg,day)
    assert exp['tickers']==['LOW','HIGH']
    assert all(sum(v['probabilities'])==pytest.approx(1) for v in exp['variants'].values())
    assert exp['variants']['source:A']['quantiles'][7]==80
    assert exp['variants']['without:C']['quantiles'][7]==82
    assert dist.v==before


def test_no_fake_temperature_probability_from_archived_median():
    row=rows(1,'temperature')[0];row['pairs']=[(.5,0),(.5,1)]
    day={'ladder':[{'lo':None,'hi':81,'market':{'ticker':'LOW'}},
                   {'lo':82,'hi':None,'market':{'ticker':'HIGH'}}],
         'distribution':{'quantiles':row['quantiles']},'diagnostics':{'A':{'median':80}}}
    outcomes={'LOW':{'result':0,'status':'finalized','retrieved_at':row['settled_at']},
              'HIGH':{'result':1,'status':'finalized','retrieved_at':row['settled_at']}}
    scored=research.record_for_day(row,day,outcomes)
    assert scored['variants']['legacy:A']['brier'] is None
    assert scored['variants']['legacy:A']['bias_f']==-2
    outcomes['HIGH']['status']='determined'
    assert research.record_for_day(row,day,outcomes) is None


def test_mismatched_experiment_tickers_and_incoherent_probabilities_not_scored():
    row=rows(1)[0]
    day={'market':{'ticker':'RAIN'},'experiments':{'version':1,'tickers':['OTHER'],
        'variants':{'source:A':{'mode':'source','model':'A','probabilities':[1]}}}}
    out={'RAIN':{'result':0,'status':'finalized','retrieved_at':row['settled_at']}}
    assert research.record_for_day(row,day,out)['variants']=={}
    assert research.probability_scores('temperature',[.5,.6],[0,1]) is None
    assert research.probability_scores('rain',[float('nan')],[0]) is None


def add_weight_experiments(data):
    for i,r in enumerate(data):
        # A wins training, B wins holdout. Selection must remain A.
        a,b=(.05,.15) if i<40 else (.35,.01)
        for key,value in [('weight:A:0.5',a),('weight:B:0.5',b)]:
            r['variants'][key]=dict(r['baseline'], brier=value, mode='weight',
                model=key.split(':')[1], multiplier=.5, method='Common post-processing')


def test_weight_selection_does_not_reselect_using_holdout():
    data=rows();add_weight_experiments(data)
    candidate=research.weight_candidate(data)
    assert candidate['parameters']['model']=='A'
    assert candidate['holdout']['brier']==pytest.approx(.35)
    assert candidate['status']=='not_supported'
    assert candidate['train_end']<candidate['test_start']
    assert not candidate['applied']


def test_missing_chosen_holdout_variant_cannot_switch_to_other_source():
    data=rows();add_weight_experiments(data)
    del data[-1]['variants']['weight:A:0.5']
    candidate=research.weight_candidate(data)
    assert candidate['status']=='collecting'
    assert candidate['candidate']=='weight:A:0.5'


def test_settlements_retrieved_after_evaluation_cannot_train_candidates():
    data=rows();add_weight_experiments(data)
    for r in data:r['settled_at']='2027-01-01T00:00:00+00:00'
    assert research.weight_candidate(data)['train_n']==0
    assert not research.rain_candidate(data)['ready_for_review']


def test_temperature_bias_fit_separate_from_spread_and_holdout():
    data=rows(kind='temperature')
    for i,r in enumerate(data):
        r['actual']=82 if i<20 else 83 if i<40 else 90
        r['pairs']=[(.5,0),(.5,1)]
        r['baseline']=research.probability_scores('temperature',[.5,.5],[0,1],r['quantiles'],r['actual'])
    candidate=research.temperature_candidate(data)
    assert candidate['parameters']['additional_shift_f']==2
    assert candidate['fit_end']<candidate['calibration_start']
    assert candidate['train_end']<candidate['test_start']
    assert candidate['holdout']['mae_f']==pytest.approx(8)
    assert not candidate['ready_for_review']
    # Alter held-out truth; the fitted parameters must remain identical.
    for r in data[-20:]:r['actual']=70
    assert research.temperature_candidate(data)['parameters']==candidate['parameters']


def test_calibrated_distribution_preserves_observed_floor_and_collapsed_shape():
    r=rows(1,'temperature')[0];r['quantiles']=[80]*15;r['floor']=79.5
    q=research.adjusted_temperature(r,-10,2)
    assert all(v>=79.5 for v in q)
    r['floor']=None
    q=research.adjusted_temperature(r,0,2)
    assert q[3]<q[7]<q[11] and q==sorted(q)


def test_wrong_fingerprint_and_duplicate_dates_cannot_inflate_training():
    data=rows();add_weight_experiments(data)
    for r in data:r['model_fingerprint']='old'
    report=research.build_report(data+data,'current')
    group=report['groups'][0]
    assert group['dates']==60 and group['current_dates']==0
    assert group['weights']['status']=='collecting'


def test_intervals_cluster_duplicate_pairs_within_dates():
    observations=[(f'2026-01-{i+1:02}',float(i)) for i in range(6)]
    bound=research.paired_interval(observations*100)
    assert bound['date_blocks']==6 and bound['block_length']==3
    assert research.paired_interval(observations[:4]) is None


def test_rain_calibration_requires_wet_and_dry_dates_and_ignores_test_labels():
    data=rows()
    candidate=research.rain_candidate(data)
    for r in data[-20:]:r['pairs']=[(.5,1)]
    assert research.rain_candidate(data)['parameters']==candidate['parameters']
    for r in data[:40]:r['pairs']=[(.5,0)]
    assert research.rain_candidate(data)['status']=='collecting'
