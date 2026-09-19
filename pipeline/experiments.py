"""Archive comparable alternatives at issuance, before outcomes are known.

Every alternative keeps the same observations, station, reporting window,
brackets, and common post-processing as the operational forecast. These are
research forecasts, never orders or replacements for the operational blend.
"""
from copy import deepcopy
from .blend import blend
from .build_temp import build_distribution

VERSION = 1


def configurations(cfg, active):
    for model in sorted(active):
        modes = [('source', None)]
        if len(active) > 1:
            modes += [('without', None), ('weight', .5), ('weight', 1.5)]
        for mode, multiplier in modes:
            altered = deepcopy(cfg)
            if mode in ('source', 'without'):
                for family in altered['families'].values():
                    family['members'] = [m for m in family['members']
                        if (m == model if mode == 'source' else m != model)]
            else:
                family = next(f for f in altered['families'].values() if model in f['members'])
                if len(family['members']) == 1:
                    family['weight'] *= multiplier
                else:
                    weights = altered.setdefault('member_weights', {})
                    weights[model] = weights.get(model, 1) * multiplier
            key = f'{mode}:{model}' + (f':{multiplier}' if multiplier else '')
            scope = 'family' if mode == 'weight' and any(f['members'] == [model] for f in cfg['families'].values()) else 'member'
            yield key, dict(mode=mode, model=model, multiplier=multiplier, weight_scope=scope), altered


def archive_temperature(city, off, members, point, cfg, day, obs=None,
                        obs_cfg=None, nbm_sigma=None):
    active = {k for k, v in day['diagnostics'].items()
              if not k.startswith('_') and isinstance(v, dict)}
    out = {}
    for key, meta, altered in configurations(cfg, active):
        dist, _ = build_distribution(city, off, members, point, altered, [],
                                    obs=obs, obs_cfg=obs_cfg, nbm_sigma=nbm_sigma)
        if dist is not None:
            out[key] = {**meta, 'probabilities': [dist.prob_between(b['lo'], b['hi'])
                         for b in day['ladder']], 'quantiles': dist.v, 'floor': dist.floor, 'ceiling': dist.ceiling}
    return {'version': VERSION, 'tickers': [b['market']['ticker'] for b in day['ladder']],
            'variants': out}


def archive_rain(probs, settings, day):
    out = {}
    for key, meta, altered in configurations(settings, set(day['models'])):
        values = {m: v for m, v in probs.items()
                  if any(m in f['members'] for f in altered['families'].values())}
        b = blend(values, altered)
        if b is not None:
            p = .98 if day.get('obs_effect') == 'observed' else b['consensus']
            out[key] = {**meta, 'probabilities': [p]}
    return {'version': VERSION, 'tickers': [day['market']['ticker']], 'variants': out}
