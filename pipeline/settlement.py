"""Per-product settlement evidence. Never infer the station from the city name."""
from __future__ import annotations
import copy,json,re
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent

def registry():
    return json.loads((ROOT/'config/settlement.json').read_text())

def configure_cities(cities,kind):
    specs=registry();out=[]
    for original in cities:
        c=copy.deepcopy(original);spec=specs.get(c['name'],{}).get(kind)
        if not spec:continue
        c['display_tz']=c['tz']
        c.update({k:spec[k] for k in ('station','icao','lat','lon','elevation_m')})
        c['tz']=spec['reporting_timezone'];c['settlement']=spec
        if kind=='temperature_low':c['series_low']=spec['series']
        c['verified']=bool(spec.get('station_verified') and spec.get('window_verified'))
        out.append(c)
    return out

def verify(city,markets,date=None):
    spec=copy.deepcopy(city.get('settlement',{}));reasons=[]
    if not spec:reasons.append('No settlement specification')
    if not spec.get('station_verified'):reasons.append('Station mapping is not verified')
    for m in markets:
        rules=' '.join((m.get('rules_primary') or '',m.get('rules_secondary') or ''))
        if not re.search(r'\b'+re.escape(spec.get('station','UNKNOWN'))+r'\b',rules):
            reasons.append('Current rules do not match the configured station')
        if spec.get('source','').lower() not in rules.lower():
            reasons.append('Current rules do not confirm the configured source')
        if spec.get('kind')=='rain' and not re.search(r'strictly greater than 0 inches',rules,re.I):
            reasons.append('Rain threshold changed; review rules')
        if spec.get('kind')=='temperature_low' and not re.search(r'minimum temperature',rules,re.I):
            reasons.append('Current rules do not confirm daily minimum temperature')
        if spec.get('kind')=='temperature_low' and m.get('event_ticker','').split('-')[0]!=spec.get('series'):
            reasons.append('Current rules do not match the configured low-temperature series')
    if date:
        try:
            start=datetime.combine(datetime.fromisoformat(date).date(),datetime.min.time(),ZoneInfo(spec['reporting_timezone']))
            end=(start+timedelta(days=1)).astimezone(timezone.utc)
            for m in markets:
                close=datetime.fromisoformat(m['close_time'].replace('Z','+00:00'))
                if abs((close-end).total_seconds())>60:reasons.append('Contract closing boundary differs from the verified reporting day')
        except (KeyError,ValueError,TypeError):reasons.append('Cannot confirm the current contract reporting boundary')
    if markets:
        first=markets[0]
        spec['rules_url']='https://api.elections.kalshi.com/trade-api/v2/markets/'+str(first.get('ticker',''))
        spec['rules_primary']=first.get('rules_primary','')
        spec['rules_secondary']=first.get('rules_secondary','')
    if not markets:reasons.append('No current contract rules')
    if not spec.get('window_verified'):reasons.append('Reporting window needs source-specific confirmation')
    spec['verified']=not reasons
    spec['reasons']=list(dict.fromkeys(reasons))
    spec['checked_at']=datetime.now(timezone.utc).isoformat()
    return spec
