"""Durable, owner-authored adjustments submitted through GitHub Issues.

Accept only an archived snapshot and bounded scalar changes. Never trust
probabilities supplied by the browser, execute text, or rewrite an old entry.
"""
from __future__ import annotations
import json,os,re,uuid
from datetime import datetime,timezone
from pathlib import Path
from .quality import atomic_json,now_iso
from .tempdist import Dist,adjust
ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'docs/data'

def bounded(value,lo,hi):
    value=float(value)
    if not lo<=value<=hi:raise ValueError(f'Value must be between {lo} and {hi}')
    return value

def create(payload,created_at,identifier,author):
    sid=str(payload.get('snapshot_id',''))
    if not re.fullmatch(r'[0-9TZ.\-a-f]+',sid):raise ValueError('Invalid snapshot id')
    kind=payload.get('kind')
    if kind not in ('rain','temperature'):raise ValueError('Unknown forecast kind')
    prefix='temp-' if kind=='temperature' else ''
    board=json.loads((DATA/'history'/(prefix+sid+'.json')).read_text())
    at=datetime.fromisoformat(created_at.replace('Z','+00:00'))
    built=datetime.fromisoformat(board['generated_at'])
    if not built<=at:raise ValueError('Adjustment predates snapshot')
    city=next(c for c in board['cities'] if c['city']==payload['city'])
    d=next(d for d in city['days'].values() if d['date']==payload['date'])
    if at>=datetime.fromisoformat(d['window_end']):raise ValueError('Reporting window already ended')
    if (at-built).total_seconds()>3*3600:raise ValueError('Snapshot is too old; refresh the board')
    reason=str(payload.get('reason','')).strip()
    if not 10<=len(reason)<=1000:raise ValueError('Reason must be 10-1000 characters')
    if kind=='temperature':
        shift=bounded(payload.get('shift_f',0),-15,15);spread=bounded(payload.get('spread_factor',1),.5,3)
        quants=adjust(d['distribution']['quantiles'],shift,spread)
        floor=d['distribution'].get('floor')
        if floor is not None:quants=[max(floor,x) for x in quants]
        dist=Dist(quants,floor=floor)
        tickers=[b['market']['ticker'] for b in d['ladder']]
        automatic=[b['model_p'] for b in d['ladder']]
        adjusted=[dist.prob_between(b['lo'],b['hi']) for b in d['ladder']]
        values={'shift_f':shift,'spread_factor':spread,'quantiles':quants}
    else:
        pop=bounded(payload['pop_percent'],0,100)/100
        tickers=[d['market']['ticker']];automatic=[d['consensus']];adjusted=[pop];values={'pop_percent':pop*100}
    return dict(id=str(identifier),created_at=created_at,author=author,snapshot_id=sid,city=city['city'],date=d['date'],kind=kind,
        horizon=d['horizon'],reason=reason,tickers=tickers,automatic_probabilities=automatic,adjusted_probabilities=adjusted,**values)

def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    issue=event['issue'];owner=event['repository']['owner']['login']
    # The owner must author the issue. Collaborator and arbitrary issue text
    # cannot change forecasts. Edited issues never rewrite archived forecasts.
    if issue['user']['login']!=owner:raise ValueError('Only repository-owner adjustments are accepted')
    if not issue['title'].startswith('Forecast adjustment:'):return
    match=re.search(r'```json\s*(\{.*?\})\s*```',issue.get('body') or '',re.S)
    if not match:raise ValueError('Missing adjustment JSON')
    entry=create(json.loads(match.group(1)),issue['created_at'],f"issue-{issue['number']}",owner)
    path=DATA/'adjustments.json';items=json.loads(path.read_text()) if path.exists() else []
    if any(x['id']==entry['id'] for x in items):return
    items.append(entry);atomic_json(path,items)
    from .performance import publish
    publish(fetch_outcomes=False)
    print(f"Recorded adjustment {entry['id']}")

if __name__=='__main__':main()
