"""Complete, interval-aligned ensemble precipitation totals."""
from datetime import datetime,timedelta,timezone
from . import hourly
from ..util import member_fraction

def _sum_local_day(times,values,date_str):
    start=datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    return hourly.complete_total(hourly.times_utc(times),values,start,start+timedelta(days=1))

def fetch(cities,cfg,threshold_mm=.254,day_offsets=(0,1)):
    data=hourly.fetch(cities,cfg,day_offsets)
    return {m:{c:{off:member_fraction(d['rain_totals'],threshold_mm)
        for off,d in days.items()} for c,days in by_city.items()}
        for m,by_city in data.items()}
