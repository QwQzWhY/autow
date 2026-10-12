"""One place to ask what time it is.

GitHub's runners are on UTC while the user is on UTC+8, so `date.today()` there
is still yesterday for the first eight hours of the user's day. That quietly
shifts "is this deadline still ahead?" and "the notice says 10月15日" by a whole
day. Everything date-shaped goes through here instead.

A fixed offset is used rather than a IANA zone because mainland China has no
daylight saving, and it keeps `zoneinfo`/`tzdata` out of the dependency list.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

DEFAULT_UTC_OFFSET = 8  # 北京时间


def tz() -> timezone:
    raw = os.environ.get("AUTOW_UTC_OFFSET", "")
    try:
        hours = float(raw)
    except ValueError:
        hours = DEFAULT_UTC_OFFSET
    return timezone(timedelta(hours=hours))


def now() -> datetime:
    return datetime.now(tz())


def today() -> date:
    return now().date()


def iso_now() -> str:
    return now().isoformat(timespec="seconds")
