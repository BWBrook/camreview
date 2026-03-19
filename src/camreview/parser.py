from __future__ import annotations

import re
from datetime import datetime

from .models import FileSnapshot

FILENAME_RE = re.compile(
    r"^(?P<site>\d+_\d+)_(?P<day>\d{1,2})-(?P<month>\d{1,2})-(?P<year>\d{4})_"
    r"(?P<hour>\d{1,2})-(?P<minute>\d{1,2})-(?P<second>\d{1,2})\.jpg$",
    re.IGNORECASE,
)


def parse_capture_filename(filename: str) -> datetime | None:
    match = FILENAME_RE.match(filename)
    if not match:
        return None
    try:
        return datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
        )
    except ValueError:
        return None


def sort_key_from_metadata(snapshot: FileSnapshot) -> tuple[int, datetime | float, str]:
    if snapshot.parsed_timestamp is not None:
        return (0, snapshot.parsed_timestamp, snapshot.filename)
    return (1, snapshot.mtime, snapshot.filename)


