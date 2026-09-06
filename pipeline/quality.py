"""Per-source provenance, bounded cache freshness, and observable failures."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

STATUS = {}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def age_minutes(stamp, now=None):
    try:
        at = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if at.tzinfo is None:
            return float('inf')
        age = ((now or datetime.now(timezone.utc)) - at).total_seconds() / 60
        return age if age >= -1 else float('inf')
    except (ValueError, TypeError, AttributeError):
        return float('inf')

def record(source, city, status, message=None, retrieved_at=None, **meta):
    STATUS[f'{source}:{city}'] = dict(source=source, city=city, status=status,
        message=message, retrieved_at=retrieved_at or now_iso(), **meta)

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=1, allow_nan=False)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(data)
    temp.replace(path)
