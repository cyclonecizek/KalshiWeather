"""Station observations with explicit units, coverage, and retrieval times.

These are provisional station readings, not final settlement observations.
Missing precipitation remains unknown. Repeated rolling-hour accumulations
are de-duplicated into hour buckets and never blindly summed.
"""
from __future__ import annotations
import csv,io,math
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import requests
from ..quality import now_iso,record
from ..util import local_day_window

UA={'User-Agent':'KalshiWeather/research (station observations)'}
THRESHOLD_MM=.254
TRACE_MM=0.0

def c_to_f(c):return None if c is None else c*1.8+32

def number(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None

def amount(obj,temperature=False):
    v=number((obj or {}).get('value'));u=(obj or {}).get('unitCode','').split(':')[-1]
    if v is None:return None
    if temperature:return c_to_f(v) if u=='degC' else v if u=='degF' else None
    return v*{'mm':1,'m':1000,'cm':10,'in':25.4}.get(u,float('nan')) if u in ('mm','m','cm','in') else None

def _from_iowa(icao,start,end):
    r=requests.get('https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py',params={
        'station':icao[1:] if icao.startswith('K') else icao,'data':['tmpf','p01i'],
        'year1':start.year,'month1':start.month,'day1':start.day,
        'year2':(end+timedelta(days=1)).year,'month2':(end+timedelta(days=1)).month,
        'day2':(end+timedelta(days=1)).day,'tz':'Etc/UTC','format':'onlycomma',
        'latlon':'no','missing':'M','trace':'T','report_type':'3'},headers=UA,timeout=30)
    r.raise_for_status();out=[]
    for row in csv.DictReader(io.StringIO(r.text)):
        try:t=datetime.fromisoformat(row['valid']).replace(tzinfo=timezone.utc)
        except (ValueError,KeyError):continue
        p=0.0 if row.get('p01i')=='T' else number(row.get('p01i'))
        out.append((t,number(row.get('tmpf')),None if p is None else p*25.4))
    return out

def _from_awc(icao,start,end):
    r=requests.get('https://aviationweather.gov/api/data/metar',params={'ids':icao,'format':'json','hours':48},headers=UA,timeout=30)
    r.raise_for_status();out=[]
    for m in r.json():
        ts=m.get('obsTime') or m.get('reportTime')
        try:t=datetime.fromtimestamp(ts,timezone.utc) if isinstance(ts,(int,float)) else datetime.fromisoformat(ts.replace('Z','+00:00'))
        except (ValueError,TypeError,AttributeError):continue
        p=number(m.get('precip'));temp=number(m.get('temp'))
        out.append((t,c_to_f(temp),None if p is None else p*25.4))
    return out

def _from_nws(icao,start,end):
    r=requests.get(f'https://api.weather.gov/stations/{icao}/observations',params={'start':start.isoformat()},headers=UA,timeout=30)
    r.raise_for_status();out=[]
    for f in r.json().get('features',[]):
        p=f.get('properties') or {}
        try:t=datetime.fromisoformat(p['timestamp'].replace('Z','+00:00'))
        except (ValueError,KeyError):continue
        out.append((t,amount(p.get('temperature'),True),amount(p.get('precipitationLastHour'))))
    return out

def summarize(rows,start,end,now,source):
    stop=min(end,now)
    rows=sorted({t:(t,tf,mm) for t,tf,mm in rows if start<=t<stop}.values())
    temps=[(t,v) for t,v,_ in rows if v is not None and -100<=v<=140]
    hours={}
    for t,_,mm in rows:
        # Only wholly-contained rolling intervals count toward this day.
        if mm is None or mm<0 or t-timedelta(hours=1)<start:continue
        bucket=t.replace(minute=0,second=0,microsecond=0)
        hours[bucket]=max(hours.get(bucket,0),mm)
    elapsed_hours=max(0,(stop-start).total_seconds()/3600)
    expected=max(1,math.floor(elapsed_hours))
    coverage=min(1,len(temps)/max(1,elapsed_hours))
    latest=temps[-1][0] if temps else None
    fresh=bool(latest and (now-latest).total_seconds()<=90*60)
    temp_complete=bool(fresh and coverage>=.8 and temps[0][0]-start<=timedelta(minutes=90)
        and all(b[0]-a[0]<=timedelta(minutes=90) for a,b in zip(temps,temps[1:])))
    # A count alone can hide a missing hour. Require an unbroken chain of
    # known hourly totals beginning at the reporting-window boundary.
    precip_times=sorted(t for t,_,mm in rows if mm is not None and mm>=0
        and t-timedelta(hours=1)>=start)
    precip_complete=bool(precip_times and elapsed_hours>=1
        and precip_times[0]-timedelta(hours=1)==start
        and all(b-a<=timedelta(hours=1) for a,b in zip(precip_times,precip_times[1:]))
        and stop-precip_times[-1]<=timedelta(minutes=60))
    total=round(sum(hours.values()),3) if hours else None
    return dict(max_f=max((v for _,v in temps),default=None),
        current_f=temps[-1][1] if temps else None,precip_mm=total,
        wet=None if total is None else total>=THRESHOLD_MM,
        precip_complete=precip_complete,temperature_complete=temp_complete,
        coverage=round(coverage,3),n_obs=len(temps),source=source,
        latest_at=latest.isoformat() if latest else None,retrieved_at=now_iso(),
        elapsed=min(1,elapsed_hours/((end-start).total_seconds()/3600)),
        local_hour=(start+timedelta(hours=elapsed_hours)).hour,
        hourly=[{'time':t.isoformat(),'temperature_f':v} for t,v in temps])

def fetch(cities,day_offsets=(0,),cfg=None):
    cfg=cfg or {}
    if not cfg.get('enabled',True) or 0 not in day_offsets:return {}
    out={};now=datetime.now(timezone.utc)
    for c in cities:
        start,end=local_day_window(c['tz'],0);best=None
        for name,fn in [('iowa',_from_iowa),('awc',_from_awc),('nws',_from_nws)]:
            try:
                ob=summarize(fn(c['icao'],start,end),start,end,now,name)
                rank=lambda x:(int(x['temperature_complete'])+int(x['precip_complete']),x['n_obs'])
                if best is None or rank(ob)>rank(best):best=ob
                if ob['temperature_complete'] and ob['precip_complete']:break
            except Exception as exc:
                record('observations-'+name,c['name'],'failed',type(exc).__name__)
        if best and best['n_obs']:
            out[c['name']]={0:best}
            record('observations',c['name'],'ok' if best['temperature_complete'] and best['precip_complete'] else 'partial',latest_at=best['latest_at'])
        else:record('observations',c['name'],'failed','No usable observations')
    return out

def heating_remaining(local_hour):
    """Compatibility only; clock-based distribution collapse is retired."""
    return 1.0

def condition_rain(p_forecast,obs,tolerance=.02):
    if obs and obs.get('precip_complete') and obs.get('wet'):
        return 1-tolerance,'observed'
    return p_forecast,None
