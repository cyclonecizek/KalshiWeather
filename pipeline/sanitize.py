"""Remove restricted per-source values from previously published snapshots.

Git history is not rewritten. New boards exclude restricted components from
public numeric products unless publication is explicitly enabled.
"""
from pathlib import Path
import json
from .quality import atomic_json
ROOT=Path(__file__).resolve().parent.parent

def sanitize(board):
    if board.get('meteoblue_published'):return board
    for c in board.get('cities',[]):
        for d in c.get('days',{}).values():
            contributed=d.get('mlm_present') or 'METEOBLUE' in d.get('models',{})
            if contributed:
                # Remove the companion components and ablations too; the
                # aggregate plus known weights can otherwise reveal mLM.
                for k in ('models','raw_models','families','variants'):d.pop(k,None)
            for k in ('predictability','coverage'):d.pop(k,None)
            diag=d.get('diagnostics',{})
            for k in ('METEOBLUE','_predictability','_predictability_widening'):diag.pop(k,None)
    return board

def main():
    n=0
    for path in (ROOT/'docs/data').rglob('*.json'):
        raw=path.read_text();b=json.loads(raw)
        if isinstance(b,dict) and 'cities' in b:
            before=json.dumps(b,sort_keys=True);clean=sanitize(b)
            if json.dumps(clean,sort_keys=True)!=before:
                text=json.dumps(clean,separators=(',',':'),allow_nan=False) if '\n' not in raw.rstrip('\n') else json.dumps(clean,indent=1,allow_nan=False)
                tmp=path.with_suffix('.tmp');tmp.write_text(text);tmp.replace(path)
            n+=1
    print(f'Checked {n} published board snapshots')

if __name__=='__main__':main()
