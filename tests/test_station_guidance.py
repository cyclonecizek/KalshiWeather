from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.sources import station_guidance as g

FIXTURES = Path(__file__).parent/'fixtures'
NOW = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)


def bulletin(product):
    return (FIXTURES/('mav_mdw.txt' if product=='MOS' else 'lav_mdw.txt')).read_text()


def test_live_mav_fixture_preserves_sparse_probability_columns_and_rollover():
    d = g.parse(bulletin('MOS'), 'KMDW', 'MOS')
    assert len(d['points']) == 21
    assert d['points'][0]['temperature_f'] == 70
    assert d['points'][2]['valid_at'] == '2026-09-17T00:00:00+00:00'
    p = d['precipitation'][0]
    assert p['element'] == 'P06' and p['probability'] == .32
    assert p['start'] == '2026-09-16T18:00:00+00:00'
    assert p['end'] == '2026-09-17T00:00:00+00:00'


def test_live_lamp_preserves_half_hour_issue_and_trace_vs_measurable_rain():
    d = g.parse(bulletin('LAMP'), 'KMDW', 'LAMP')
    assert d['issued_at'] == '2026-09-16T17:30:00+00:00'
    assert len(d['points']) == 25
    assert d['points'][0]['valid_at'] == '2026-09-16T18:00:00+00:00'
    assert d['points'][0]['temperature_f'] == 74
    assert d['precipitation'][0]['hours'] == 0
    assert 'traces' in d['precipitation'][0]['event']
    assert d['precipitation'][1]['hours'] == 1
    assert d['precipitation'][1]['probability'] == 0
    assert d['blend_weight'] == 0


def test_exact_station_and_product_required():
    for station, product in [('KORD', 'LAMP'), ('KMDW','MOS')]:
        with pytest.raises(ValueError):
            g.parse(bulletin('LAMP'), station, product)


def test_missing_temperatures_and_probabilities_are_not_zero():
    text = bulletin('LAMP').replace('TMP  74', 'TMP 999').replace('PPO   3', 'PPO 999')
    d = g.parse(text, 'KMDW', 'LAMP')
    assert d['points'][0]['temperature_f'] is None
    assert not any(p['element']=='PPO' and p['end']==d['points'][0]['valid_at'] for p in d['precipitation'])


def test_window_keeps_original_period_and_marks_crossing_not_daily_pop():
    d = {**g.parse(bulletin('MOS'), 'KMDW', 'MOS'), 'status':'ok'}
    start = datetime(2026,9,17,6,tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    result = g.for_window(d,start,end)
    assert all(start <= datetime.fromisoformat(p['valid_at']) < end for p in result['points'])
    assert any(p['crosses_reporting_boundary'] for p in result['precipitation'])
    assert 'daily_probability' not in result
    assert 'not a daily maximum' in result['coverage_note']
    assert g.for_window(d,end+timedelta(days=5),end+timedelta(days=6))['status']=='out_of_range'


def test_stale_future_and_failed_requests_stay_visible():
    for now, expected in [(NOW,'ok'),(NOW+timedelta(hours=5),'stale'),(NOW-timedelta(hours=2),'stale')]:
        g._CACHE.clear()
        with patch.object(g.requests,'get') as get:
            get.return_value.text=bulletin('LAMP')
            get.return_value.url='https://example.test/bulletin'
            result=g.retrieve('KMDW','LAMP',now)
            assert result['status']==expected
    g._CACHE.clear()
    with patch.object(g.requests,'get',side_effect=g.requests.Timeout()):
        result=g.retrieve('KMDW','MOS',NOW)
        assert result['status']=='unavailable'
        assert result['points']==[]
    g._CACHE.clear()


def test_month_rollover_and_negative_temperature():
    text=bulletin('LAMP').replace('9/16/2026','12/31/2026').replace('TMP  74','TMP  -4')
    d=g.parse(text,'KMDW','LAMP')
    assert d['points'][0]['temperature_f']==-4
    assert d['points'][6]['valid_at']=='2027-01-01T00:00:00+00:00'


def test_explicit_mos_high_is_not_the_tmp_peak():
    d={**g.parse(bulletin('MOS'),'KMDW','MOS'),'status':'ok'}
    start=datetime(2026,9,17,6,tzinfo=timezone.utc)
    day=g.for_window(d,start,start+timedelta(days=1))
    assert day['mos_maximum']['temperature_f']==76
    assert day['sampled_max_f']==73
    assert day['mos_maximum']['period_start']=='2026-09-17T13:00:00+00:00'
    assert day['mos_maximum']['period_end']=='2026-09-18T01:00:00+00:00'
    today=g.for_window(d,start-timedelta(days=1),start)
    assert today['mos_maximum'] is None  # 12Z bulletin omits today's max.
    assert today['sampled_max_f'] is not None


def test_missing_minimum_does_not_shift_following_maximum_and_reversed_label_works():
    for text in [bulletin('MOS').replace('N/X','X/N'),
                 bulletin('MOS').replace('N/X                    66','N/X                   999')]:
        d={**g.parse(text,'KMDW','MOS'),'status':'ok'}
        start=datetime(2026,9,17,8,tzinfo=timezone.utc)
        result=g.for_window(d,start,start+timedelta(days=1))
        assert result['mos_maximum']['temperature_f']==76
        assert result['mos_maximum']['period_end']=='2026-09-18T03:00:00+00:00'


def test_missing_maximum_stays_missing_without_substituting_next_day_or_tmp():
    text=bulletin('MOS').replace('          76','         999',1)
    d={**g.parse(text,'KMDW','MOS'),'status':'ok'}
    start=datetime(2026,9,17,6,tzinfo=timezone.utc)
    assert g.for_window(d,start,start+timedelta(days=1))['mos_maximum'] is None
    assert g.for_window(d,start+timedelta(days=1),start+timedelta(days=2))['mos_maximum']['temperature_f']==74


@pytest.mark.parametrize('cycle',[0,6,12,18])
def test_extreme_dates_across_cycles_and_year_rollover(cycle):
    run=datetime(2026,12,31,cycle,tzinfo=timezone.utc)
    times=[run+timedelta(hours=h) for h in range(6,73,3)]
    text=f'KMDW   GFS MOS GUIDANCE    12/31/2026  {cycle:02d}00 UTC\n'
    text+='HR  '+''.join(f'{t.hour:3d}' for t in times)+'\n'
    text+='N/X '+''.join(' 80' if t.hour==0 else ' 40' if t.hour==12 else '   ' for t in times)+'\n'
    text+='TMP '+''.join('999' for t in times)+'\n'
    d={**g.parse(text,'KMDW','MOS'),'status':'ok'}
    for extreme in d['extrema']:
        if extreme['kind']!='maximum':continue
        marker=datetime.fromisoformat(extreme['bulletin_valid_at'])
        start=marker-timedelta(hours=18)
        day=g.for_window(d,start,start+timedelta(days=1))
        assert day['mos_maximum']['temperature_f']==80
        assert day['sampled_max_f'] is None
