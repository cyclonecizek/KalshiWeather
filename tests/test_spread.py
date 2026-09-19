from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from pipeline.spread import Mixture, archive, sensitivity
from pipeline.tempdist import Dist, normal_quantiles
from pipeline.temperature_calibration import fit
from pipeline.model_research import probability_scores
from pipeline.util import load_yaml
from pipeline.products import temperature_config


def day():
    q=normal_quantiles(80,3)
    return dict(date='2026-04-01',model_fingerprint='new',distribution=dict(quantiles=q),
        diagnostics={'A':{'quantiles':normal_quantiles(78,1)},'B':{'quantiles':normal_quantiles(82,1)}},
        model_inputs=[dict(model='A',included=True,weight=.25),dict(model='B',included=True,weight=.75)],
        experiments={'variants':{}},ladder=[dict(lo=None,hi=78),dict(lo=79,hi=81),dict(lo=82,hi=None)])


def test_gem_is_removed_from_all_operational_products():
    cfg=load_yaml('config/settings.yml')
    for product in [cfg,temperature_config(cfg,'temperature'),temperature_config(cfg,'temperature_low')]:
        assert all('GEM_EPS' not in f['members'] for f in product['families'].values())
    assert 'GEM_EPS' not in cfg['sources']['openmeteo']['models']


def test_mixture_preserves_source_probabilities_and_physical_bounds():
    a=Dist(normal_quantiles(70,1));b=Dist(normal_quantiles(90,1))
    mixture=Mixture([(a,1),(b,3)])
    assert mixture.cdf(80)==pytest.approx(.25)
    assert mixture.prob_between(79,81)<.001
    d=day();d['distribution']['ceiling']=80.5
    archive(d)
    for v in d['experiments']['variants'].values():
        assert sum(v['probabilities'])==pytest.approx(1)
        assert v['probabilities'][2]==0
        assert max(v['quantiles'])<=80.5+1e-9


def test_sensitivity_reprices_each_side_and_fails_closed():
    d=day();archive(d)
    checks=sensitivity(d,{'yes_ask':1,'no_ask':99},1,.07)
    assert checks['YES']['complete'] and not checks['YES']['fragile']
    assert checks['NO']['fragile']
    assert sensitivity(d,{'yes_ask':99,'no_ask':1},1,.07)['YES']['fragile']
    assert not sensitivity(d,{'yes_ask':float('nan')},1,.07)['YES']['complete']
    del d['experiments']['variants']['distribution:mixture']
    assert not sensitivity(d,{'yes_ask':1,'no_ask':1},1,.07)['YES']['complete']


def history():
    rows=[]
    for i in range(40):
        at=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=i)
        rows.append(dict(date=at.date().isoformat(),issued_at=at.isoformat(),
            settled_at=(at+timedelta(hours=12)).isoformat(),model_fingerprint='new',
            actual=82+(i%3-1),quantiles=normal_quantiles(80,3)))
    return rows


def test_calibration_uses_separate_dates_and_no_future_results():
    rows=history();d=day();issued='2026-04-01T00:00:00+00:00'
    status,dist=fit(rows,d,issued)
    assert status['status']=='candidate' and not status['applied']
    assert status['fit_dates']==20 and status['calibration_dates']==20
    assert status['fit_end']<status['calibration_start']<d['date']
    poisoned=deepcopy(rows)
    future=dict(rows[-1],date='2026-04-02',actual=-1000)
    late=dict(rows[-1],date='2026-03-01',settled_at='2026-05-01T00:00:00+00:00',actual=-1000)
    wrong=dict(rows[-1],date='2026-03-02',model_fingerprint='old',actual=-1000)
    assert fit(poisoned+[future,late,wrong],d,issued)[0]==status
    assert fit(rows+deepcopy(rows),d,issued)[0]==status
    rows[0]['settled_at']='2026-02-01T00:00:00+00:00'
    assert fit(rows,d,issued)[1] is None


def test_distribution_scores_penalize_unnecessary_spread():
    tight=probability_scores('temperature',[1],[1],normal_quantiles(80,1),80)
    wide=probability_scores('temperature',[1],[1],normal_quantiles(80,5),80)
    assert tight['crps_approx']<wide['crps_approx']
    assert tight['coverage50'] and tight['coverage80'] and tight['coverage90']
