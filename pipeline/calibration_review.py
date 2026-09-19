"""Evidence-based review queue; approval never happens merely because days pass."""
import hashlib,json,math,os,statistics
from functools import lru_cache
from collections import defaultdict
from .quality import atomic_json,now_iso,age_minutes
from .util import load_yaml
from .performance import ROOT,DATA,read

MIN_DAYS=20

@lru_cache(maxsize=4)
def model_fingerprint(kind=None):
    settings=load_yaml(ROOT/'config/settings.yml')
    if kind=='temperature_low':
        from .products import temperature_config
        settings['temperature']=temperature_config(settings,kind)
        settings['product']='temperature_low'
    # Adding an independent product must not invalidate unchanged high/rain
    # evidence. Their legacy settings hash is retained exactly.
    settings.pop('temperature_low',None)
    # Operational quote schedules/budgets do not change the weather model.
    settings.pop('execution',None)
    return hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()

def review(records,fingerprint=None):
    groups=defaultdict(list)
    for r in records:
        if r.get('model_version')=='2':groups[(r['city'],r['kind'],r['horizon'])].append(r)
    result={}
    for key,rows in sorted(groups.items()):
        current_fingerprint=fingerprint or model_fingerprint(key[1])
        unversioned=len(rows)
        rows=[r for r in rows if r.get('model_fingerprint')==current_fingerprint]
        unversioned-=len(rows)
        if not rows:
            result['|'.join(key)]={'city':key[0],'kind':key[1],'horizon':key[2],'n':0,'required_dates':MIN_DAYS,'ready_for_review':False,'reasons':['0/20 distinct settled dates with the current model fingerprint'],'excluded_prior_records':unversioned,'model_version':'2'}
            continue
        rows=list({r['date']:r for r in sorted(rows,key=lambda r:r['issued_at'])}.values())
        rows.sort(key=lambda r:r['date'])
        differences=[r['brier']-r['market_brier'] for r in rows]
        pairs=[(p,y) for r in rows for p,y in r['pairs']]
        n=len(rows);mean=statistics.mean(differences)
        upper=mean+1.96*statistics.stdev(differences)/math.sqrt(n) if n>1 else None
        # Bin-wise probability calibration, weighted by the actual sample count.
        error=0
        for i in range(10):
            ps=[(p,y) for p,y in pairs if min(9,int(p*10))==i]
            if ps:error+=abs(sum(p-y for p,y in ps))/len(pairs)
        reasons=[]
        if n<MIN_DAYS:reasons.append(f'{n}/{MIN_DAYS} distinct settled dates')
        if upper is None or upper>=0:reasons.append('Paired Brier advantage is not established by the review threshold')
        if error>.10+1e-9:reasons.append('Probability calibration error exceeds 10 percentage points')
        evidence=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
        result['|'.join(key)]={'city':key[0],'kind':key[1],'horizon':key[2],'n':n,'required_dates':MIN_DAYS,
            'excluded_prior_records':unversioned,'first_date':rows[0]['date'],'last_date':rows[-1]['date'],'mean_brier_difference':mean,
            'upper_brier_difference':upper,'calibration_error':error,'ready_for_review':not reasons,
            'reasons':reasons,'evidence_sha256':evidence,'model_version':'2'}
    return result

def publish():
    performance=read(DATA/'performance.json',{})
    from .products import BOARD_FILES
    report={'generated_at':now_iso(),'performance_generated_at':performance.get('generated_at'),
        'model_fingerprint':model_fingerprint(),'groups':review(performance.get('records',[])),
        'model_fingerprints':{kind:model_fingerprint(kind) for kind in BOARD_FILES},
        'note':'Review thresholds are screening rules, not proof of profitability. Approval requires an explicit owner review.'}
    atomic_json(DATA/'calibration_review.json',report)
    return report

def approve(key,actor,owner):
    if not actor or actor!=owner:raise ValueError('Only the repository owner may approve calibration')
    report=publish()
    if age_minutes(report['performance_generated_at'])>48*60:raise ValueError('Refresh settled-outcome scoring before approval')
    entry=report['groups'].get(key)
    if not entry or not entry['ready_for_review']:raise ValueError('Calibration review criteria have not been met')
    kind=key.split('|')[1]
    fingerprint=(report['model_fingerprints'][kind] if kind=='temperature_low' else report['model_fingerprint'])
    approved=read(ROOT/'config/calibration.json',{})
    approved[key]={**entry,'validated':True,'reviewed_at':now_iso(),'reviewed_by':actor,
        'model_fingerprint':fingerprint}
    atomic_json(ROOT/'config/calibration.json',approved)

if __name__=='__main__':
    key=os.environ.get('CALIBRATION_KEY')
    if key:approve(key,os.environ.get('GITHUB_ACTOR'),os.environ.get('GITHUB_REPOSITORY_OWNER'))
    else:print(f"Published {len(publish()['groups'])} calibration review groups")
