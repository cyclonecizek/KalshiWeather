"""Per-product settlement evidence. Never infer the station from the city name."""
from __future__ import annotations
import copy,json,re
from datetime import datetime,timezone
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
        c['verified']=bool(spec.get('station_verified') and spec.get('window_verified'))
        out.append(c)
    return out

def verify(city,markets):
    spec=copy.deepcopy(city.get('settlement',{}));reasons=[]
    if not spec:reasons.append('No settlement specification')
    for m in markets:
        rules=' '.join((m.get('rules_primary') or '',m.get('rules_secondary') or ''))
        if not re.search(r'\b'+re.escape(spec.get('station','UNKNOWN'))+r'\b',rules):
            reasons.append('Current rules do not match the configured station')
        if spec.get('source','').lower() not in rules.lower():
            reasons.append('Current rules do not confirm the configured source')
        if spec.get('kind')=='rain' and not re.search(r'strictly greater than 0 inches',rules,re.I):
            reasons.append('Rain threshold changed; review rules')
    if not markets:reasons.append('No current contract rules')
    if not spec.get('window_verified'):reasons.append('Reporting window needs source-specific confirmation')
    spec['verified']=not reasons
    spec['reasons']=list(dict.fromkeys(reasons))
    spec['checked_at']=datetime.now(timezone.utc).isoformat()
    return spec
