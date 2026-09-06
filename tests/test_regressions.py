from datetime import datetime,timedelta,timezone
from pathlib import Path
from copy import deepcopy
import json
import pytest
from pipeline.kalshi import Kalshi,normalize,_book_top,book_depth
from pipeline.tempdist import Dist,normal_quantiles,apply_observation
from pipeline.sources.hourly import complete_total,rain_probability
from pipeline.sources.observations import summarize,condition_rain,amount
from pipeline import policy,performance,adjustments,run,settlement
from pipeline.sanitize import sanitize
from pipeline.util import load_yaml
UTC=timezone.utc
NOW=datetime(2026,9,6,12,tzinfo=UTC)

@pytest.mark.parametrize('payload,expected',[
 ({'yes_bid_dollars':'0.4010','yes_ask_dollars':'0.4500'},(40.1,45)),
 ({'no_bid_dollars':'0.5500','no_ask_dollars':'0.6000'},(40,45)),
 ({'yes_bid':0,'yes_ask':1},(0,1)),
 ({'yes_bid':40,'yes_ask':45},(40,45)),
])
def test_quotes_preserve_units_and_zero(payload,expected):
 q=Kalshi.quote(payload)
 assert (q['yes_bid'],q['yes_ask'])==pytest.approx(expected)
 assert q['executable']

@pytest.mark.parametrize('payload',[{'yes_bid':40},{'last_price_dollars':'.40'}, {'yes_bid':45,'yes_ask':40}])
def test_missing_and_crossed_quotes_never_invent_asks(payload):
 assert not Kalshi.quote(payload)['executable']

def test_hydrate_normalizes_detail_response(monkeypatch):
 k=Kalshi('https://unused');monkeypatch.setattr(k,'market',lambda t:{'yes_bid_dollars':'.40','yes_ask_dollars':'.45','yes_ask_size_fp':'10.5'})
 ms=[{'ticker':'TEST'}];k.hydrate(ms)
 assert ms[0]['yes_ask']==45
 assert ms[0]['yes_ask_size_fp']=='10.5'

def test_books_support_both_schemas_and_bind_depth_to_price():
 for book in [{'yes':[[40,10]],'no':[[55,20]]},{'orderbook_fp':{'yes_dollars':[['.40','10']],'no_dollars':[['.55','20']]}}]:
  assert _book_top(book)==(40,45)
  assert book_depth(book,'YES',45)==20
  assert book_depth(book,'YES',44) is None

def test_evening_forecast_can_move_down_or_up():
 original=normal_quantiles(90,3)
 cool=Dist(apply_observation(original,82,remaining=[79,80,81]))
 hot=Dist(apply_observation(original,82,remaining=[87,88,89]))
 assert cool.median()==82
 assert hot.median()==88

def test_without_hourly_forecast_clock_does_not_collapse():
 q=normal_quantiles(90,3)
 assert apply_observation(q,82,.01)==apply_observation(q,82,.99)

def test_floor_survives_spread_and_probabilities_sum():
 q=apply_observation(normal_quantiles(90,3),90,remaining=[85,87,89],min_spread=1.4)
 assert min(q)>=89.5 and q==sorted(q)
 d=Dist(q,floor=89.5)
 assert d.prob_between(None,89)==0
 assert sum(d.prob_between(lo,hi) for lo,hi in [(None,89),(90,91),(92,None)])==pytest.approx(1)

def test_precip_intervals_use_end_time_and_require_complete_data():
 start=NOW.replace(hour=0);end=start+timedelta(days=1)
 times=[start+timedelta(hours=i) for i in range(25)]
 assert complete_total(times,[999]+[1]*24,start,end)==24
 assert complete_total(times,[999]+[None]*24,start,end) is None
 assert complete_total(times,[999]+[1]*23+[None],start,end) is None

def test_unknown_precip_stays_unknown_and_does_not_condition():
 start=NOW.replace(hour=0)
 rows=[(start+timedelta(hours=i),70,None) for i in range(12)]
 ob=summarize(rows,start,start+timedelta(days=1),NOW,'test')
 assert ob['precip_mm'] is None and ob['wet'] is None and not ob['precip_complete']
 assert condition_rain(.6,ob)==(.6,None)

def test_remaining_rain_risk_uses_timing_not_clock():
 detail={'rain_totals':[1,1,1],'past_totals':[0,0,0],'future_totals':[1,1,1]}
 p,why=rain_probability(detail,{'precip_complete':True,'precip_mm':0},.254)
 assert p>.8 and why=='remaining_hours'

def test_precip_repeated_hour_not_double_counted():
 start=NOW.replace(hour=0)
 rows=[(start+timedelta(hours=2,minutes=10),70,.2),(start+timedelta(hours=2,minutes=20),70,.2)]
 ob=summarize(rows,start,start+timedelta(days=1),NOW,'test')
 assert ob['precip_mm']==.2
 assert amount({'value':.001,'unitCode':'wmoUnit:m'})==1
 assert amount({'value':1,'unitCode':'unit:unknown'}) is None

def settings():return load_yaml(Path(__file__).parents[1]/'config/settings.yml')

def test_policy_rejects_stale_unverified_uncalibrated():
 cfg=settings();d={'horizon':'morning','kind':'rain','forecast_retrieved_at':NOW.isoformat(),'data_quality':'ok','n_families':3}
 q={'executable':True,'retrieved_at':(NOW-timedelta(hours=3)).isoformat(),'close_time':(NOW+timedelta(days=1)).isoformat(),'spread':2}
 e={'ev_cents':8,'depth':30}
 reasons=policy.eligibility('City',d,q,e,cfg,{},NOW)['reasons']
 assert any('Settlement' in r for r in reasons)
 assert any('Quote stale' in r for r in reasons)
 assert any('calibration' in r for r in reasons)
 assert 'Market closed' not in reasons

def test_portfolio_shares_budget_and_counts_fees():
 cfg=settings();cfg['execution'].update(daily_budget_dollars=10,max_per_market_dollars=10,max_per_city_dollars=10,bankroll_dollars=1000)
 rows=[dict(city='City',quote={'ticker':str(i)},edge={'eligibility':{'eligible':True,'reasons':[]},'ev_cents':8,'price':40,'depth':100,'kelly':1}) for i in range(3)]
 policy.allocate(rows,cfg)
 assert sum(r['edge'].get('suggested_cost_dollars',0) for r in rows)<=10
 assert sum(r['edge']['suggested_contracts'] for r in rows)<25

def test_redaction_removes_values_and_reconstruction_components():
 b={'meteoblue_published':False,'cities':[{'days':{'0':{'mlm_present':True,'models':{'METEOBLUE':.7,'NDFD':.5},'families':{'mlm':.7},'raw_models':{'NDFD':.5},'variants':{'blend_without_mlm':.5},'consensus':.6}}}]}
 d=sanitize(b)['cities'][0]['days']['0']
 assert 'models' not in d and 'families' not in d and 'variants' not in d and d['consensus']==.6

def test_arbitrage_requires_complete_ladder_and_fees():
 from pipeline.brackets import check_arbitrage
 ladder=[{'lo':None,'hi':80,'label':'low','market':{'executable':True,'yes_ask':49,'no_ask':52}},
         {'lo':81,'hi':None,'label':'high','market':{'executable':True,'yes_ask':49,'no_ask':52}}]
 assert check_arbitrage(ladder) is None # 2c gross, 4c single-basket fees
 ladder[0]['lo']=75
 assert check_arbitrage(ladder) is None

def test_settlement_station_is_product_specific():
 cities=load_yaml(Path(__file__).parents[1]/'config/cities.yml')['cities']
 t=next(c for c in settlement.configure_cities(cities,'temperature') if c['name']=='Chicago')
 r=next(c for c in settlement.configure_cities(cities,'rain') if c['name']=='Chicago')
 assert t['icao']=='KMDW' and r['icao']=='KORD'
 assert t['tz']=='Etc/GMT+6'

def test_scoring_never_invents_a_temperature_from_winning_bracket():
 d={'distribution':{'median':90,'p10':88,'p90':92,'quantiles':normal_quantiles(90,2)},'gaps':[],
 'ladder':[{'lo':None,'hi':89,'model_p':.4,'implied':.5,'market':{'ticker':'LOW'}},{'lo':90,'hi':None,'model_p':.6,'implied':.5,'market':{'ticker':'HIGH'}}]}
 outcomes={'LOW':{'result':0},'HIGH':{'result':1}}
 scores=performance.score_day('temperature',d,outcomes)
 assert scores['actual'] is None and scores['error'] is None and scores['covered80'] is None
 assert scores['brier']==pytest.approx(.32)
 outcomes['HIGH']['actual_value']=94
 assert performance.score_day('temperature',d,outcomes)['error']==4

def test_snapshot_selection_respects_cutoff(tmp_path,monkeypatch):
 monkeypatch.setattr(performance,'DATA',tmp_path);(tmp_path/'history').mkdir()
 for hour in [7,9]:
  b={'schema_version':2,'model_version':'2','snapshot_id':str(hour),'kind':'rain','generated_at':NOW.replace(hour=hour).isoformat(),
     'cities':[{'city':'City','days':{'0':{'date':'2026-09-06','window_start':NOW.replace(hour=0).isoformat()}}}]}
  (tmp_path/'history'/f'{hour}.json').write_text(json.dumps(b))
 selected=performance.select_snapshots()
 assert selected[('City','2026-09-06','rain','morning','2')][1]=='7'

def test_calibration_holdout_is_not_in_training():
 rows=[]
 for i in range(60):
  rows.append(dict(city='City',kind='temperature',horizon='morning',date=f'2026-{1+i//28:02}-{1+i%28:02}',actual=90,error=2 if i<40 else 12,covered80=True,brier=.2,market_brier=.3,log_loss=.2,pairs=[(.5,1)]))
 c=performance.summarize(rows)[0]['candidate_calibration']
 assert c['additional_bias_f']==2
 assert c['holdout_mae_adjusted']==10
 assert c['train_end']<c['test_start']

def test_adjustment_validates_time_and_uses_archived_forecast(tmp_path,monkeypatch):
 monkeypatch.setattr(adjustments,'DATA',tmp_path);(tmp_path/'history').mkdir()
 sid='2026-09-06T100000.000000Z-abcdef12'
 board={'generated_at':NOW.replace(hour=10).isoformat(),'cities':[{'city':'City','days':{'0':{'date':'2026-09-06','window_end':(NOW+timedelta(hours=12)).isoformat(),'horizon':'morning','market':{'ticker':'TEST'},'consensus':.4}}}]}
 (tmp_path/'history'/f'{sid}.json').write_text(json.dumps(board))
 p=dict(snapshot_id=sid,kind='rain',city='City',date='2026-09-06',pop_percent=60,reason='Evening storm risk remains high')
 a=adjustments.create(p,NOW.isoformat(),'issue-1','owner')
 assert a['automatic_probabilities']==[.4] and a['adjusted_probabilities']==[.6]
 with pytest.raises(ValueError):adjustments.create(p,(NOW+timedelta(days=1)).isoformat(),'issue-2','owner')
 with pytest.raises(ValueError):adjustments.create({**p,'snapshot_id':'../../secret'},NOW.isoformat(),'issue-2','owner')

@pytest.mark.parametrize('kind',['rain','temperature'])
def test_complete_builder_and_publisher_with_recorded_inputs(monkeypatch,tmp_path,kind):
 from pipeline.sources import hourly,observations,nws_text,temp_sources,nbm_temp,gribprob
 from pipeline.util import local_date_str,local_day_window
 from pipeline.run import _is_for_date
 monkeypatch.setenv('WEATHER_CITIES','New York')
 now=datetime.now(UTC)
 def ensemble(cities,cfg,offsets):
  result={}
  for model in ['GEFS','ICON_EPS','GEM_EPS']:
   result[model]={}
   for c in cities:
    days={}
    for off in offsets:
     start,end=local_day_window(c['tz'],off)
     detail=dict(maxima=[79,80,81],remaining=[78,79,80],rain_totals=[0,1,2],past_totals=[0,0,0],future_totals=[0,1,2],
       hourly=[{'time':(start+timedelta(hours=i)).isoformat(),'median':75+i/10,'p10':74,'p90':80} for i in range(24)],
       retrieved_at=now.isoformat(),model_run_at=None,window_start=start.isoformat(),window_end=end.isoformat())
     days[off]=detail;hourly.DETAILS[(c['name'],off,model)]=detail
    result[model][c['name']]=days
  return result
 monkeypatch.setattr(hourly,'fetch',ensemble)
 monkeypatch.setattr(observations,'fetch',lambda cities,*args:{c['name']:{0:dict(max_f=78,source='test',latest_at=now.isoformat(),temperature_complete=True,precip_complete=True,precip_mm=0,wet=False,hourly=[],coverage=1)} for c in cities})
 monkeypatch.setattr(nws_text,'fetch_ndfd',lambda cities,*args:{c['name']:{0:.4,1:.4} for c in cities})
 monkeypatch.setattr(temp_sources,'fetch_ndfd_maxt',lambda cities,*args:{c['name']:{0:80,1:81} for c in cities})
 monkeypatch.setattr(nbm_temp,'fetch',lambda cities,*args:{c['name']:{0:{'mean_f':80,'sd_f':2},1:{'mean_f':81,'sd_f':2}} for c in cities})
 monkeypatch.setattr(gribprob,'fetch',lambda *args:({'New York':{0:.4,1:.4}},'cycle'))
 def markets(self,ticker,*args):
  out=[]
  for off in [0,1]:
   date=local_date_str('Etc/GMT+5',off);stamp=datetime.strptime(date,'%Y-%m-%d').strftime('%y%b%d').upper();_,end=local_day_window('Etc/GMT+5',off)
   base=dict(event_ticker=ticker+'-'+stamp,rules_primary='CLINYC The Weather Company strictly greater than 0 inches',rules_secondary='',yes_bid=30,yes_ask=35,volume=1000,open_interest=1000,close_time=end.isoformat(),_retrieved_at=now.isoformat(),yes_ask_size_fp='50',yes_bid_size_fp='50')
   if ticker=='KXRAIN':out.append(dict(base,ticker=ticker+'-'+stamp+'-NYC'))
   else:
    for suffix,st,lo,hi in [('L','less',None,80),('H','greater',79,None)]:out.append(dict(base,ticker=ticker+'-'+stamp+'-'+suffix,strike_type=st,floor_strike=lo,cap_strike=hi))
  return out
 monkeypatch.setattr(Kalshi,'markets_for_series',markets)
 monkeypatch.setattr(Kalshi,'_get',lambda *args,**kwargs:{'series':{'fee_multiplier':1}})
 monkeypatch.setattr(Kalshi,'hydrate',lambda *args:0)
 monkeypatch.setattr(run,'DATA',tmp_path);monkeypatch.setattr(performance,'DATA',tmp_path)
 assert run.run(kind)==0
 path=tmp_path/('board_temp.json' if kind=='temperature' else 'board.json')
 board=json.loads(path.read_text());run.validate(board)
 assert board['schema_version']==2 and len(board['cities'])==1
 assert (tmp_path/'history').exists() and (tmp_path/'performance.json').exists()
 day=board['cities'][0]['days']['0']
 edge=day['ladder'][0]['edge'] if kind=='temperature' else day['edge']
 assert not edge['eligibility']['eligible'] and edge['suggested_contracts']==0

def test_failed_build_preserves_previous_file(tmp_path,monkeypatch):
 monkeypatch.setattr(run,'DATA',tmp_path);monkeypatch.setattr(performance,'DATA',tmp_path)
 original='{"schema_version":2,"sentinel":"last good board"}'
 (tmp_path/'board.json').write_text(original)
 monkeypatch.setattr(run,'prepare',lambda *args: {'cities':[]})
 assert run.run('rain')==1
 assert (tmp_path/'board.json').read_text()==original
 assert json.loads((tmp_path/'status.json').read_text())['status']=='degraded'


def test_duplicate_hour_cannot_hide_missing_precip_interval():
 start=NOW.replace(hour=0);end=start+timedelta(days=1)
 times=[start+timedelta(hours=i) for i in range(25)]
 times[12]=times[11]
 assert complete_total(times,[1]*25,start,end) is None


def test_precip_hour_count_does_not_hide_an_observation_gap():
 start=NOW.replace(hour=0)
 rows=[(start+timedelta(hours=i),70,0) for i in range(1,12) if i!=5]
 ob=summarize(rows,start,start+timedelta(days=1),NOW,'test')
 assert not ob['precip_complete']


def test_change_breakdown_reconciles_model_market_gap():
 old=dict(consensus=.40,consensus_forecast=.50,market={'mid':40})
 new=dict(consensus=.60,consensus_forecast=.55,market={'mid':45})
 parts=run.changes(new,old)['components']
 assert parts['forecast_change_pp']==pytest.approx(5)
 assert parts['observation_effect_change_pp']==pytest.approx(15)
 assert parts['gap_change_pp']==pytest.approx(15)
 assert parts['gap_change_pp']==pytest.approx(parts['forecast_change_pp']+parts['observation_effect_change_pp']-parts['market_change_cents'])


def test_distribution_filters_nonfinite_members_and_respects_tail_floor():
 from pipeline.tempdist import members_to_quantiles
 assert members_to_quantiles([None,float('nan'),float('inf'),1,2,3])[7]==2
 d=Dist(apply_observation(normal_quantiles(90,3),90,remaining=[85,87,89]),floor=89.5)
 assert d.quantile(.00001)>=89.5


def test_publication_guard_rejects_old_schema(tmp_path):
 from pipeline.publication_check import check
 (tmp_path/'docs/assets').mkdir(parents=True);(tmp_path/'docs/data').mkdir()
 for path in ['index.html','assets/app.js','assets/math.js','assets/decision.js','assets/app.css']:(tmp_path/'docs'/path).write_text('asset')
 (tmp_path/'docs/data/board.json').write_text('{"schema_version":1,"cities":[{}]}')
 with pytest.raises(ValueError,match='version-2'):check(tmp_path)


def test_meteoblue_public_status_does_not_expose_key_or_restricted_values(monkeypatch):
 from pipeline.sources import meteoblue as mb
 monkeypatch.setenv('METEOBLUE_KEY','secret-do-not-publish')
 status=mb.publication_status({'publish_values':False},{'Test':{0:{'tmax':99}}})
 assert status['state']=='publication_disabled'
 assert '99' not in str(status) and 'secret-do-not-publish' not in str(status)
 monkeypatch.delenv('METEOBLUE_KEY')
 assert mb.publication_status({'publish_values':True},{})['state']=='missing_key'


def test_meteoblue_failed_requests_count_against_budget_and_redact_secrets(tmp_path,monkeypatch,capsys):
 from pipeline.sources import meteoblue as mb
 import requests
 monkeypatch.setenv('METEOBLUE_KEY','private-key')
 calls=[]
 def fail(*args,**kwargs):
  calls.append(1)
  raise requests.HTTPError('https://example.test/?apikey=private-key')
 monkeypatch.setattr(mb.requests,'get',fail)
 cities=[{'name':name,'tz':'UTC','lat':1,'lon':1} for name in ['A','B']]
 cfg={'publish_values':True,'cache_path':str(tmp_path/'cache.json'),'max_calls_per_day':1}
 assert mb.fetch(cities,cfg)=={}
 assert len(calls)==1
 status=mb.publication_status(cfg,{})
 assert status['failures']==1 and status['budget_limited']==1
 assert 'private-key' not in capsys.readouterr().out
 assert sum(mb._load_cache(tmp_path/'cache.json')['calls'].values())==1


def test_meteoblue_expired_cache_is_not_reissued_as_fresh(tmp_path,monkeypatch):
 from pipeline.sources import meteoblue as mb
 from datetime import datetime,timedelta,timezone
 import json
 monkeypatch.setenv('METEOBLUE_KEY','test')
 now=datetime.now(timezone.utc)
 cache=tmp_path/'cache.json'
 cache.write_text(json.dumps({'data':{'A|'+now.date().isoformat():{'at':(now-timedelta(hours=9)).isoformat(),'days':{'0':{'tmax':80}}}},'calls':{now.date().isoformat():1}}))
 assert mb.fetch([{'name':'A','tz':'UTC','lat':1,'lon':1}],{'cache_path':str(cache),'max_calls_per_day':1,'cache_hours':8})=={}
 assert mb.STATUS['A']=='budget_exhausted'
 assert not mb._fresh('not a date',timedelta(hours=8))
 assert not mb._fresh(now.replace(tzinfo=None).isoformat(),timedelta(hours=8))


def test_meteoblue_enabled_response_has_daily_values_and_real_retrieval_time(tmp_path,monkeypatch):
 from pipeline.sources import meteoblue as mb
 from datetime import datetime,timezone
 monkeypatch.setenv('METEOBLUE_KEY','test')
 date=datetime.now(timezone.utc).date().isoformat()
 class Response:
  def raise_for_status(self): pass
  def json(self): return {'data_day':{'time':[date],'temperature_max':[81],'precipitation_probability':[30]}}
 monkeypatch.setattr(mb.requests,'get',lambda *a,**k:Response())
 cfg={'publish_values':True,'cache_path':str(tmp_path/'cache.json')}
 cities=[{'name':'A','tz':'UTC','lat':1,'lon':1}]
 first=mb.fetch(cities,cfg,(0,))
 assert first['A'][0]['tmax']==81 and first['A'][0]['pop']==.3
 assert mb.publication_status(cfg,first)['state']=='available'
 assert mb._fresh(first['A'][0]['retrieved_at'],__import__('datetime').timedelta(minutes=1))
 monkeypatch.setattr(mb.requests,'get',lambda *a,**k: (_ for _ in ()).throw(AssertionError('Should use cache')))
 second=mb.fetch(cities,cfg,(0,))
 assert second['A'][0]['tmax']==81
