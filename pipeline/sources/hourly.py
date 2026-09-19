"""Shared UTC ensemble trajectories for both boards and station day windows.

Hourly precipitation is an interval ENDING at its timestamp. Temperature
is instantaneous. Missing intervals are never interpreted as zero.
"""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from ..quality import age_minutes, atomic_json, now_iso, record
from ..util import local_day_window, member_fraction

DETAILS = {}

def finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)

def times_utc(times):
    return [datetime.fromisoformat(t.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
            if '+' not in t and not t.endswith('Z') else
            datetime.fromisoformat(t.replace('Z', '+00:00')).astimezone(timezone.utc)
            for t in times]

def window_indices(times, start, end, precipitation=False):
    return [i for i,t in enumerate(times) if (start < t <= end if precipitation else start <= t < end)]

def complete_total(times, values, start, end):
    idx = window_indices(times, start, end, True)
    expected = int((end-start).total_seconds()/3600)
    if len(idx) != expected or any(i >= len(values) or not finite(values[i]) or values[i] < 0 for i in idx):
        return None
    if [times[i] for i in idx] != [start+timedelta(hours=h) for h in range(1,expected+1)]:
        return None
    if idx and (times[idx[0]] != start+timedelta(hours=1) or times[idx[-1]] != end):
        return None
    return sum(values[i] for i in idx)

def summarize(hourly, city, off, now=None):
    now = now or datetime.now(timezone.utc)
    start,end = local_day_window(city['tz'], off)
    times = times_utc(hourly.get('time', []))
    ti = window_indices(times,start,end)
    pi = window_indices(times,start,end,True)
    tk = [k for k in hourly if k == 'temperature_2m' or k.startswith('temperature_2m_member')]
    pk = [k for k in hourly if k == 'precipitation' or k.startswith('precipitation_member')]
    expected = int((end-start).total_seconds()/3600)
    maxima, remaining, rain_totals, past_totals, future_totals = [],[],[],[],[]
    minima, remaining_minima = [], []
    curves=[]
    for k in tk:
        v=hourly[k]
        if len(ti)!=expected or any(i>=len(v) or not finite(v[i]) for i in ti):
            continue
        maxima.append(max(v[i] for i in ti))
        minima.append(min(v[i] for i in ti))
        fi=[i for i in ti if times[i]>=now]
        remaining.append(max((v[i] for i in fi), default=None))
        remaining_minima.append(min((v[i] for i in fi), default=None))
        curves.append([v[i] for i in ti])
    for k in pk:
        v=hourly[k]
        total=complete_total(times,v,start,end)
        if total is None:
            continue
        rain_totals.append(total)
        # Include the current, incomplete hour in future risk conservatively.
        past_totals.append(sum(v[i] for i in pi if times[i] <= now))
        future_totals.append(sum(v[i] for i in pi if times[i] > now))
    def quant(vals,p):
        vals=sorted(vals)
        x=(len(vals)-1)*p; a=int(x); b=min(a+1,len(vals)-1)
        return vals[a]+(vals[b]-vals[a])*(x-a)
    hourly_curve=[]
    if curves:
        for j,i in enumerate(ti):
            vals=[v[j] for v in curves]
            hourly_curve.append({'time':times[i].isoformat(),'median':round(quant(vals,.5),2),
                'p10':round(quant(vals,.1),2),'p90':round(quant(vals,.9),2)})
    return dict(maxima=maxima,remaining=remaining,minima=minima,remaining_minima=remaining_minima,rain_totals=rain_totals,
        past_totals=past_totals,future_totals=future_totals,hourly=hourly_curve,
        window_start=start.isoformat(),window_end=end.isoformat())

def fetch(cities,cfg,day_offsets=(0,1)):
    """Small bounded requests; a per-location cache is shared by both products."""
    out={}
    for key,model in cfg['models'].items():
        pending=[];locations={}
        for c in cities:
            token=hashlib.sha256(f"v2|{model}|{c['lat']}|{c['lon']}".encode()).hexdigest()[:24]
            path=Path('.cache/hourly')/(token+'.json')
            try:saved=json.loads(path.read_text())
            except (OSError,ValueError):saved={}
            if age_minutes(saved.get('retrieved_at'))<=cfg.get('cache_minutes',45):
                locations[c['name']]=(saved['payload'],saved['retrieved_at'])
            else:pending.append((c,path))
        size=max(1,min(5,int(cfg.get('batch_size',4))))
        for start in range(0,len(pending),size):
            batch=pending[start:start+size]
            params={'latitude':','.join(str(c['lat']) for c,_ in batch),
                'longitude':','.join(str(c['lon']) for c,_ in batch),
                'hourly':'temperature_2m,precipitation','models':model,
                'forecast_days':4,'past_days':1,'timezone':'GMT','temperature_unit':'fahrenheit'}
            if cfg.get('temporal_resolution'):
                params['temporal_resolution']=cfg['temporal_resolution']
            error='Request failed'
            for attempt in range(2):
                try:
                    r=requests.get(cfg['ensemble_base'],params=params,
                        timeout=(cfg.get('connect_timeout_seconds',10),cfg.get('read_timeout_seconds',45)))
                    r.raise_for_status();payload=r.json()
                    if isinstance(payload,dict):payload=[payload]
                    if len(payload)!=len(batch) or any(not x.get('hourly') for x in payload):
                        raise ValueError('Missing hourly data or location count mismatch')
                    stamp=now_iso()
                    for (c,path),loc in zip(batch,payload):
                        locations[c['name']]=(loc,stamp)
                        atomic_json(path,dict(payload=loc,retrieved_at=stamp))
                    break
                except Exception as exc:
                    status=getattr(getattr(exc,'response',None),'status_code',None)
                    error=f'HTTP {status}' if status else type(exc).__name__
                    if status and status not in (408,429,500,502,503,504):break
            else:pass
            for c,_ in batch:
                if c['name'] not in locations:record(key,c['name'],'failed',error)
        for c in cities:
            if c['name'] not in locations:continue
            loc,retrieved=locations[c['name']]
            days={off:summarize(loc.get('hourly') or {},c,off) for off in day_offsets}
            out.setdefault(key,{})[c['name']]=days
            good=all(len(d['maxima'])>=3 and len(d['rain_totals'])>=3 for d in days.values())
            record(key,c['name'],'ok' if good else 'partial',retrieved_at=retrieved,
                model_run_at=None,valid_windows=[d['window_start'] for d in days.values()])
            for off,d in days.items():
                DETAILS[(c['name'],off,key)]={**d,'retrieved_at':retrieved,'model_run_at':None}
    return out

def rain_probability(detail, obs, threshold=.254):
    if not obs or not obs.get('precip_complete'):
        return member_fraction(detail['rain_totals'],threshold), 'full_day'
    observed=obs['precip_mm']
    if observed>=threshold:return .98,'observed'
    pairs=list(zip(detail['past_totals'],detail['future_totals']))
    consistent=[future for past,future in pairs if past < threshold]
    if len(consistent)<3:
        # A forecast that did not match the dry observations is not a
        # confident conditional forecast; retain missing/insufficient status.
        return None,'insufficient_matching_members'
    return member_fraction([observed+v for v in consistent],threshold),'remaining_hours'
