"""Describe the actual blend inputs without changing its calculation."""
def describe_inputs(settings, kind, day):
    cfg = settings['temperature'] if kind == 'temperature' else settings
    families = cfg['families']
    values = (day.get('diagnostics', {}) if kind == 'temperature'
              else day.get('models', {}))
    active = {k: [m for m in f['members'] if m in values and values[m] is not None]
              for k, f in families.items()}
    total = sum(families[k]['weight'] for k, ms in active.items() if ms)
    rows = []
    for family, spec in families.items():
        for model in spec['members']:
            included = model in active[family]
            info = values.get(model, {}) if kind == 'temperature' else {}
            weight = (spec['weight'] / total * cfg.get('member_weights',{}).get(model,1) / sum(cfg.get('member_weights',{}).get(m,1) for m in active[family])
                      if included and total else 0)
            status = 'Included' if included else 'Unavailable for this forecast'
            if model == 'METEOBLUE' and not settings['temperature']['sources']['meteoblue'].get('publish_values'):
                status = 'Not enabled for publication'
            elif not included and ((kind == 'temperature' and day.get('diagnostics', {}).get('_observation_used'))
                                   or (kind == 'rain' and day.get('obs_effect') in ('remaining_hours', 'observed'))):
                status = 'Unavailable or excluded by observation conditioning'
            rows.append(dict(model=model, family=spec.get('label', family),
                included=included, weight=weight, status=status,
                value=(info.get('median', info.get('value')) if kind == 'temperature' else values.get(model)),
                p10=info.get('p10'), p90=info.get('p90'),
                members=info.get('n', day.get('sources', {}).get(model, {}).get('member_count')),
                source_type=info.get('type', 'probability guidance' if kind == 'rain' else None)))
            if model == 'NDFD' and day.get('nws_guidance'):
                nws = day['nws_guidance']
                rows[-1]['nws_guidance'] = nws
                if not included:
                    rows[-1]['value'] = nws.get('value')
                    rows[-1]['status'] = ('Available for comparison; excluded from observation-conditioned blend'
                        if nws.get('value') is not None else nws['message'])
    google = day.get('weathernext')
    if google:
        rows.append(dict(model='WEATHERNEXT2', family='Google AI research', included=False,
            weight=0, status='Research comparison: '+google['status'],
            value=google.get('value'),p10=google.get('p10'),p90=google.get('p90'),
            members=google.get('member_count'),source_type='ensemble research'))
    return rows
