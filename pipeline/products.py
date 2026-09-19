"""Product identities are separate, especially for calibration and archives."""
from copy import deepcopy

TEMPERATURE_KINDS = ('temperature', 'temperature_low')
BOARD_FILES = {'rain': 'board.json', 'temperature': 'board_temp.json',
               'temperature_low': 'board_low.json'}
HISTORY_PREFIXES = {'rain': '', 'temperature': 'temp-', 'temperature_low': 'low-'}


def temperature_config(settings, kind):
    if kind == 'temperature':
        return settings['temperature']
    # Low-temperature calibration cannot inherit a high-temperature fit.
    # Begin with independently configured ensemble-only guidance. Periodic
    # NDFD/MOS and provider daily point products are not calendar-day minima.
    cfg = deepcopy(settings['temperature'])
    low = settings['temperature_low']
    cfg.update(low)
    cfg['extremum'] = 'minimum'
    cfg['member_weights'] = deepcopy(low.get('member_weights', {}))
    return cfg
