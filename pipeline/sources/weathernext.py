"""WeatherNext 2 research forecasts; never an operational blend member.

Open-Meteo interpolates the native six-hour model to hourly temperature
and distributes each precipitation accumulation over its six hours.
"""
from copy import deepcopy

from . import hourly
from ..blend import blend
from ..products import TEMPERATURE_KINDS, temperature_config
from ..build_temp import build_distribution

MODEL = 'WEATHERNEXT2'
API_MODEL = 'google_weathernext2_ensemble'
DOCS = 'https://open-meteo.com/en/docs/google-weathernext-api'
NOTE = ('Native 0.25-degree, six-hour guidance interpolated by Open-Meteo to hourly output. '
        'Rain timing within each six-hour period is distributed, not independently predicted. '
        'Comparison only; zero operational blend weight pending verification.')


def fetch(cities, cfg, offsets=(0, 1)):
    # A slow connection in a paired request previously lost both stations.
    # Isolate locations and allow the ensemble endpoint a longer handshake.
    config = {**cfg, 'models': {MODEL: API_MODEL}, 'batch_size': 1,
              'connect_timeout_seconds': 30, 'read_timeout_seconds': 60,
              'cache_minutes': 60, 'temporal_resolution': 'hourly_1'}
    return hourly.fetch(cities, config, offsets).get(MODEL, {})


def attach(city, off, data, day, settings):
    """Use the same windows, observations and post-processing as the blend.

    Keep the source experiment separate from production probabilities and
    source-count/freshness eligibility. Missing members cannot fabricate skill.
    """
    detail = data.get(city['name'], {}).get(off)
    provenance = hourly.DETAILS.get((city['name'], off, MODEL), {})
    result = {'model': MODEL, 'label': 'Google WeatherNext 2', 'status': 'unavailable',
              'included': False, 'weight': 0, 'note': NOTE, 'documentation_url': DOCS,
              'retrieved_at': provenance.get('retrieved_at'), 'model_run_at': None,
              'native_resolution_hours': 6, 'expected_members': 64,
              'hourly': provenance.get('hourly', []), 'member_count': 0}
    day['weathernext'] = result
    if not detail:
        return
    obs = day.get('observed')
    if day['kind'] in TEMPERATURE_KINDS:
        result['member_count'] = len(detail['minima' if day['kind']=='temperature_low' else 'maxima'])
        if result['member_count'] < 3:
            result['status'] = 'insufficient_members'
            return
        cfg = deepcopy(temperature_config(settings,day['kind']))
        cfg['families'] = {'google_research': {'weight': 1, 'members': [MODEL]}}
        members = {MODEL: {city['name']: {off: detail['minima' if day['kind']=='temperature_low' else 'maxima']}}}
        dist, _ = build_distribution(city, off, members, {}, cfg, [], obs=obs,
                                    obs_cfg=settings['sources'].get('observations'))
        if dist is None:
            return
        result.update(value=dist.median(), p10=dist.quantile(.1), p90=dist.quantile(.9))
        variant = {'quantiles': dist.v, 'floor': dist.floor, 'ceiling': dist.ceiling,
                   'probabilities': [dist.prob_between(b['lo'], b['hi']) for b in day['ladder']]}
    else:
        result['member_count'] = len(detail['rain_totals'])
        if result['member_count'] < 3:
            result['status'] = 'insufficient_members'
            return
        probability, method = hourly.rain_probability(detail, obs)
        if probability is None:
            result['status'] = 'insufficient_matching_members'
            return
        cfg = deepcopy(settings)
        cfg['families'] = {'google_research': {'weight': 1, 'members': [MODEL]}}
        forecast = blend({MODEL: probability}, cfg)
        if forecast is None:
            return
        probability = .98 if method == 'observed' else forecast['consensus']
        result.update(value=probability, observation_method=method)
        variant = {'probabilities': [probability]}
    result['status'] = 'ok' if result['member_count']==64 else 'partial_members'
    day['experiments']['variants']['source:'+MODEL] = {
        'mode': 'source', 'model': MODEL, 'multiplier': None, 'weight_scope': 'member', **variant}
