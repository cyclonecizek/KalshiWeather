from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import pytest

from pipeline import settlement, policy
from pipeline.build_temp import build_distribution
from pipeline.products import temperature_config, BOARD_FILES, HISTORY_PREFIXES
from pipeline.sources import hourly, observations
from pipeline.tempdist import Dist, normal_quantiles
from pipeline.run import ROOT
from pipeline.util import load_yaml


def settings():
    return load_yaml(ROOT / 'config/settings.yml')


def test_low_uses_independent_config_and_calibration():
    cfg = settings()
    cfg['temperature']['bias'] = {'Chicago': 15}
    cfg['temperature']['spread_factor'] = 9
    low = temperature_config(cfg, 'temperature_low')
    assert low['bias'] == {} and low['spread_factor'] == 1
    assert not any(m in f['members'] for f in low['families'].values()
                   for m in ('NBM_T', 'NDFD', 'METEOBLUE'))
    assert BOARD_FILES['temperature_low'] == 'board_low.json'
    assert HISTORY_PREFIXES['temperature_low'] != HISTORY_PREFIXES['temperature']
    from test_readiness import fixture, NOW
    from pipeline.calibration_review import model_fingerprint
    cfg, day, quote, edge = fixture()
    cfg['execution']['max_elapsed'] = 1
    day['kind'] = 'temperature_low'
    high_approval = {'New York|temperature|day_ahead': {
        'validated': True, 'n': 30, 'model_version': '2', 'model_fingerprint': model_fingerprint()}}
    result = policy.eligibility('New York', day, quote, edge, cfg, high_approval, NOW)
    assert 'Out-of-sample calibration pending' in result['reasons']


def test_hourly_minimum_is_memberwise_and_covers_reporting_day(monkeypatch):
    start = datetime(2026, 9, 19, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(hourly, 'local_day_window', lambda *_: (start, start+timedelta(days=1)))
    values = [70]*24
    values[0], values[12], values[23] = 62, 90, 55
    payload = {'time': [(start+timedelta(hours=i)).isoformat() for i in range(25)],
               'temperature_2m': values+[10],
               'temperature_2m_member01': [v+2 for v in values]+[5]}
    d = hourly.summarize(payload, {'tz': 'Etc/GMT+6'}, 0, start+timedelta(hours=15))
    assert d['minima'] == [55, 57] and d['maxima'] == [90, 92]
    assert d['remaining_minima'] == [55, 57]  # late cooling, not just pre-dawn
    payload['temperature_2m'][2] = None
    assert hourly.summarize(payload, {'tz': 'UTC'}, 0, start)['minima'] == [57]


@pytest.mark.parametrize('remaining,expected', [([65, 66, 67], 60), ([53, 54, 55], 54), ([None]*3, 60)])
def test_observed_minimum_caps_forecast_but_allows_later_cooling(monkeypatch, remaining, expected):
    cfg = temperature_config(settings(), 'temperature_low')
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(hourly, 'DETAILS', {('Chicago', 0, 'GEFS'): {'remaining_minima': remaining}})
    obs = {'min_f': 60, 'max_f': 89, 'temperature_complete': True,
           'latest_at': now.isoformat(), 'source': 'test'}
    dist, diag = build_distribution({'name': 'Chicago'}, 0,
        {'GEFS': {'Chicago': {0: [48, 49, 50]}}}, {}, cfg, [], obs=obs)
    assert dist.median() == pytest.approx(expected)
    assert dist.ceiling == 60.5 and dist.floor is None
    assert max(dist.v) <= 60.5 and dist.quantile(.999999) <= 60.5
    assert dist.prob_between(61, None) == 0
    assert sum(dist.prob_between(lo, hi) for lo, hi in [(None, 53), (54, 60), (61, None)]) == pytest.approx(1)
    assert diag['_observed_min'] == 60 and '_observed_max' not in diag
    obs['temperature_complete'] = False
    unconditioned, _ = build_distribution({'name': 'Chicago'}, 0,
        {'GEFS': {'Chicago': {0: [48, 49, 50]}}}, {}, cfg, [], obs=obs)
    assert unconditioned.ceiling is None and unconditioned.median() == 49


def test_observations_keep_both_extrema():
    start = datetime(2026, 9, 19, tzinfo=timezone.utc)
    rows = [(start+timedelta(hours=i), t, None) for i, t in enumerate([65, 60, 61])]
    d = observations.summarize(rows, start, start+timedelta(days=1), start+timedelta(hours=3), 'test')
    assert d['min_f'] == 60 and d['max_f'] == 65


def test_low_rules_reject_high_wrong_series_station_and_dst_day():
    spec = settlement.registry()['Chicago']['temperature_low']
    raw = {'event_ticker': 'KXLOWTCHI-26SEP19', 'ticker': 'KXLOWTCHI-26SEP19-T64',
           'rules_primary': 'Minimum temperature at Chicago (CLIMDW) according to The Weather Company',
           'close_time': '2026-09-20T06:00:00Z'}
    assert settlement.verify({'settlement': spec}, [raw], '2026-09-19')['verified']
    for changes in ({'rules_primary': raw['rules_primary'].replace('Minimum', 'Maximum')},
                    {'event_ticker': 'KXHIGHCHI-26SEP19'},
                    {'rules_primary': raw['rules_primary'].replace('CLIMDW', 'CLIORD')},
                    {'close_time': '2026-09-20T05:00:00Z'}):
        assert not settlement.verify({'settlement': spec}, [{**raw, **changes}], '2026-09-19')['verified']


def test_low_adjustment_cannot_cross_observed_ceiling(tmp_path, monkeypatch):
    from pipeline import adjustments
    monkeypatch.setattr(adjustments, 'DATA', tmp_path)
    (tmp_path/'history').mkdir()
    sid = '2026-09-19T080000.000000Z-abcd'
    q = [min(60.5, x) for x in normal_quantiles(60, 2)]
    dist = Dist(q, ceiling=60.5)
    bounds = [(None, 59), (60, 60), (61, None)]
    d = dict(date='2026-09-19', window_end='2026-09-20T06:00:00+00:00', horizon='morning',
             distribution={'quantiles': q, 'ceiling': 60.5},
             ladder=[dict(lo=lo, hi=hi, model_p=dist.prob_between(lo, hi), market={'ticker': str(i)})
                     for i, (lo, hi) in enumerate(bounds)])
    board = dict(kind='temperature_low', generated_at='2026-09-19T08:00:00+00:00',
                 cities=[{'city': 'Chicago', 'days': {'0': d}}])
    (tmp_path/'history'/('low-'+sid+'.json')).write_text(json.dumps(board))
    out = adjustments.create(dict(snapshot_id=sid, kind='temperature_low', city='Chicago',
        date=d['date'], reason='Clear skies and light winds', shift_f=10, spread_factor=2),
        '2026-09-19T08:05:00+00:00', 'test', 'owner')
    assert max(out['quantiles']) <= 60.5
    assert out['adjusted_probabilities'][-1] == 0
    assert sum(out['adjusted_probabilities']) == pytest.approx(1)


def test_low_builder_quote_refresh_and_verification(monkeypatch):
    from pipeline import run, refresh_quotes, performance
    from pipeline.kalshi import Kalshi
    from pipeline.util import local_date_str, local_day_window
    cfg = settings()
    cfg['temperature']['sources']['meteoblue']['publish_values'] = False
    monkeypatch.setenv('WEATHER_CITIES', 'Chicago')
    city = next(c for c in settlement.configure_cities(load_yaml(ROOT/'config/cities.yml')['cities'], 'temperature_low') if c['name']=='Chicago')
    date = local_date_str(city['tz'], 1)
    start, end = local_day_window(city['tz'], 1)
    event = 'KXLOWTCHI-'+datetime.fromisoformat(date).strftime('%y%b%d').upper()
    raw = dict(event_ticker=event, rules_primary='Minimum temperature CLIMDW according to The Weather Company',
               close_time=end.isoformat(), status='active', yes_bid=45, yes_ask=50,
               no_bid=50, no_ask=55, volume=500, open_interest=500,
               yes_ask_size_fp=100, yes_bid_size_fp=100)
    markets = [{**raw, 'ticker': event+'-T59', 'strike_type': 'less', 'cap_strike': 60},
               {**raw, 'ticker': event+'-T60', 'strike_type': 'greater', 'floor_strike': 59}]
    class FakeKalshi:
        quote = staticmethod(Kalshi.quote)
        def __init__(self, *args):pass
        def markets_for_series(self, ticker):
            assert ticker == 'KXLOWTCHI'
            return deepcopy(markets)
        def hydrate(self, items):pass
        def _get(self, path):return {'series': {'fee_multiplier': 1}}
    def forecasts(*args):
        detail = dict(minima=[57, 58, 59], maxima=[87, 88, 89], remaining=[87, 88, 89],
                      remaining_minima=[57, 58, 59], rain_totals=[0, 0, 0], hourly=[],
                      retrieved_at=datetime.now(timezone.utc).isoformat())
        hourly.DETAILS[('Chicago', 1, 'GEFS')] = detail
        return {'GEFS': {'Chicago': {1: detail}}}
    monkeypatch.setattr(run, 'Kalshi', FakeKalshi)
    monkeypatch.setattr(run.hourly, 'fetch', forecasts)
    monkeypatch.setattr(run.station_guidance, 'fetch', lambda *a: {})
    monkeypatch.setattr(run.weathernext, 'fetch', lambda *a: {})
    monkeypatch.setattr(run.observations, 'fetch', lambda *a: {})
    def forbidden(*args):raise AssertionError('High-only source requested for a low forecast')
    monkeypatch.setattr(run.temp_sources, 'fetch_ndfd_maxt', forbidden)
    monkeypatch.setattr(run.nbm_temp, 'fetch', forbidden)
    b = run.prepare('temperature_low', cfg)
    run.validate(b)
    day = b['cities'][0]['days']['1']
    assert day['distribution']['median'] == 58
    assert day['settlement']['verified'] and 'observation_ml' not in day
    assert 'source:GEFS' in day['experiments']['variants']
    assert sum(x['model_p'] for x in day['ladder']) == pytest.approx(1)
    original = deepcopy(day['distribution'])
    refresh_quotes.refresh(b, cfg, lambda ticker: next(x for x in markets if x['ticker']==ticker), lambda _: 1)
    assert day['distribution'] == original and b['quote_refresh']['failures'] == 0
    assert all('Out-of-sample calibration pending' in x['edge']['eligibility']['reasons'] for x in day['ladder'])
    outcomes = {m['ticker']: {'result': int(i==0), 'actual_value': 58, 'status': 'finalized'} for i, m in enumerate(markets)}
    score = performance.score_day('temperature_low', day, outcomes)
    assert score['actual'] == 58 and score['error'] == 0
