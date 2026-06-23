"""Time conversion helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

SRT_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def srt_timestamp_to_seconds(value: str) -> float:
    match = SRT_TS_RE.match(value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def seconds_to_readable(seconds: float) -> str:
    seconds_int = int(round(seconds))
    minutes, sec = divmod(seconds_int, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"
