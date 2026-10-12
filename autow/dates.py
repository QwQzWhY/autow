"""Chinese date parsing.

The site renders dates without a year ("10月09日"), and the body usually ends
with a full date ("2026年10月9日"). We use the full one when it is there and
otherwise infer the year from the current month.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_FULL = re.compile(r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?")
_MD = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_TIME = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")


def parse_full(text: str) -> date | None:
    """Parse an unambiguous 'YYYY年M月D日' style date."""
    m = _FULL.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_month_day(text: str, reference: date | None = None) -> date | None:
    """Parse 'M月D日' and pick the year closest to `reference`.

    A date with no year is ambiguous. Choosing "the most recent year that is not
    in the future" is right for publish dates but wrong for deadlines — a notice
    published on 10月09日 that says "10月20日前提交" would come out as last year.
    Picking the nearest year to a reference date handles both, as long as the
    reference is the notice's own publish date.
    """
    if parse_full(text):
        return parse_full(text)
    m = _MD.search(text or "")
    if not m:
        return None
    reference = reference or date.today()
    month, day = int(m.group(1)), int(m.group(2))
    candidates = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - reference).days))


def parse_time(text: str) -> tuple[int, int] | None:
    m = _TIME.search(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def published_iso(text: str) -> str | None:
    """Normalise a publish date into an ISO date string, or None."""
    full = parse_full(text)
    if full:
        return full.isoformat()
    md = parse_month_day(text)
    return md.isoformat() if md else None


def tail_full_date(body: str) -> str | None:
    """Many notices end with a full date near the signature line."""
    matches = list(_FULL.finditer(body or ""))
    if not matches:
        return None
    # The signature date is usually the last full date in the document.
    m = matches[-1]
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def human(iso: str | None) -> str:
    if not iso:
        return "无截止日期"
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return d.strftime("%Y-%m-%d %H:%M") if d.hour or d.minute else d.strftime("%Y-%m-%d")
