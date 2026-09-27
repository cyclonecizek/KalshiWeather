from copy import deepcopy
from datetime import date, timedelta

from pipeline.focused_research import VERSION, report, specification, register, weathernext_summary
from pipeline.model_research import probability_scores


def rows(n=20):
    result=[]
    for i in range(n):
        day=(date(2026,9,28)+timedelta(days=i)).isoformat()
        baseline=probability_scores('rain',[.5],[0])
        candidate=probability_scores('rain',[.05],[0])
        result.append(dict(date=day,issued_at=day+'T10:00:00+00:00',
            focused_registration=VERSION,brier=.25,market_brier=.2,baseline=baseline,
            variants={'weight:GEFS:0.5':{**candidate,'mode':'weight','model':'GEFS','multiplier':.5}}))
    return result


def test_fixed_hypotheses_do_not_change_low_temperature_or_live_values():
    assert specification('temperature','day_ahead')['key']=='spread:0.75'
    assert specification('temperature','morning')['key']=='spread:1.25'
    assert specification('temperature','evening')['key']=='spread:1.5'
    assert specification('temperature_low','morning') is None
    day=dict(consensus=.4,experiments={'variants':{}})
    register(day)
    assert day['consensus']==.4 and day['experiments']['variants']=={}


def test_unregistered_or_pre_review_forecasts_cannot_enter_evaluation():
    history=rows()
    history[0]['focused_registration']=None
    history[1]['date']='2026-09-26'
    result=report(history,'rain','morning')
    assert result['paired_dates']==18 and not result['ready_for_review']
    assert not result['applied']


def test_fixed_twenty_date_evaluation_is_not_reselected_or_rolled():
    history=rows(25)
    first=report(history[:20],'rain','morning')
    assert first['ready_for_review'] and not first['applied']
    for r in history[20:]:
        r['variants']['weight:GEFS:0.5']['brier']=1
    later=report(history,'rain','morning')
    assert later==first
    # Duplicate records from one date cannot satisfy the date threshold.
    assert report([history[0]]*30,'rain','morning')['dates']==1


def test_missing_candidate_cannot_be_replaced_by_a_better_alternative():
    history=rows()
    history[2]['variants']={'without:GEFS':deepcopy(history[1]['variants']['weight:GEFS:0.5'])}
    result=report(history,'rain','morning')
    assert result['paired_dates']==19 and result['missing_dates']==[history[2]['date']]
    assert not result['ready_for_review']


def test_stage_bias_uses_actual_values_and_does_not_invent_old_diagnostics():
    rows=[dict(date='2026-09-28',actual=80,weathernext_diagnostics=dict(raw_median_f=76,conditioned_median_f=78,final_median_f=79)),dict(date='2026-09-29',actual=82)]
    stages=weathernext_summary(rows)['stages']
    assert [s['bias_f'] for s in stages]==[-4,-2,-1]
    assert all(s['dates']==1 for s in stages)
    assert all(s['bias_f'] is None for s in weathernext_summary([])['stages'])
