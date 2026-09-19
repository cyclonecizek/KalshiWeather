"""Prospective distribution comparisons and side-specific executable checks."""
import math
from .tempdist import Dist, QUANTILES, adjust
from .util import edge_for_side


def bounded(values, day):
    d=day['distribution']; floor=d.get('floor'); ceiling=d.get('ceiling')
    values=[max(floor,x) if floor is not None else x for x in values]
    values=[min(ceiling,x) if ceiling is not None else x for x in values]
    return Dist(values,floor=floor,ceiling=ceiling)


class Mixture:
    """Average source CDFs with actual guidance weights, not source quantiles."""
    def __init__(self, components):
        total=sum(w for d,w in components)
        self.components=[(d,w/total) for d,w in components]
    def cdf(self,x):
        return sum(w*d.cdf(x) for d,w in self.components)
    def prob_between(self,lo,hi):
        return max(0,min(1,(1 if hi is None else self.cdf(hi+.5))-(0 if lo is None else self.cdf(lo-.5))))
    def quantile(self,p):
        lo=min(d.quantile(1e-8) for d,w in self.components)-1
        hi=max(d.quantile(1-1e-8) for d,w in self.components)+1
        for _ in range(70):
            mid=(lo+hi)/2
            if self.cdf(mid)<p:lo=mid
            else:hi=mid
        return (lo+hi)/2


def archive(day):
    """Fixed scenarios saved before settlement; never tuned to market prices."""
    variants=day['experiments']['variants']; d=day['distribution']
    def save(key,label,dist):
        qs=[dist.quantile(p) for p in QUANTILES]
        variants[key]=dict(mode='distribution',model=label,multiplier=None,
            probabilities=[dist.prob_between(b['lo'],b['hi']) for b in day['ladder']],
            quantiles=qs,floor=d.get('floor'),ceiling=d.get('ceiling'),method=label)
    for factor in (.75,1.25):
        save(f'spread:{factor}',f'Spread ×{factor}',bounded(adjust(d['quantiles'],0,factor),day))
    components=[]
    for r in day.get('model_inputs',[]):
        q=day.get('diagnostics',{}).get(r['model'],{}).get('quantiles')
        if r.get('included') and r.get('weight',0)>0 and q:
            # Same bias/bounds; no additional disagreement or blanket inflation.
            components.append((bounded(adjust(q,day['diagnostics'].get('_bias',0),1),day),r['weight']))
    if components:save('distribution:mixture','Weighted source mixture (no added inflation)',Mixture(components))


def sensitivity(day,quote,index,rate):
    """Recomputed at current asks after every quote refresh, for BOTH sides."""
    variants=day.get('experiments',{}).get('variants',{})
    required=('spread:0.75','spread:1.25','distribution:mixture')
    selected={k:v for k,v in variants.items() if k in required or k=='distribution:calibrated'}
    result={}
    for side in ('YES','NO'):
        scenarios=[]
        for key,v in selected.items():
            ps=v.get('probabilities',[])
            if index>=len(ps) or not isinstance(ps[index],(int,float)) or not math.isfinite(ps[index]) or not 0<=ps[index]<=1:continue
            p=ps[index] if side=='YES' else 1-ps[index]
            price=quote.get('yes_ask' if side=='YES' else 'no_ask')
            if not isinstance(price,(int,float)) or not math.isfinite(price):continue
            ev=edge_for_side(p,price,rate)
            if ev is not None:scenarios.append(dict(key=key,label=v['model'],probability=p,ev_cents=ev))
        complete=all(any(s['key']==k for s in scenarios) for k in required)
        minimum=min((s['ev_cents'] for s in scenarios),default=None)
        result[side]=dict(complete=complete,fragile=not complete or minimum is None or minimum<=0,
            minimum_ev_cents=minimum,scenarios=scenarios)
    return result
