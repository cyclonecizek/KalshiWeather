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
