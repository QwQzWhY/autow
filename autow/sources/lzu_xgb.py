"""兰州大学学生工作部（处）的通知 (xgb.lzu.edu.cn).

Three columns are worth watching for an undergraduate:

    资助通知  奖助学金、困难认定、助学贷款、勤工助学
    国防教育  《军事理论》课程安排、军训
    思政通知  主题教育、学风建设、评优

The lists are not in the HTML: every column page renders its rows from a JSON
feed, so the feed is what gets read and listing a column costs one small HTTP
request instead of a browser page. Article pages are rendered the same way, so
those do need the browser.

The column ids are not guessed — each column page carries its own
`/xgbnew/lm/<id>.js` include, and that file is the whole column.
"""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext

from .. import dates
from ..browser import open_page
from ..models import RawNotice

BASE = "http://xgb.lzu.edu.cn"
NAME = "lzu-xgb"

COLUMNS = {
    "资助通知": 109848,
    "国防教育": 109850,
    "思政通知": 109846,
}

# The feed holds the entire column — 资助通知 is over 500 articles — and only
# the head of it is recent enough to matter.
MAX_PER_COLUMN = 15

DETAIL_READY = ".article"

_DETAIL_JS = """
() => {
  const area = document.querySelector('.article') || document.body;
  return {
    lines: (area.innerText || '')
      .split('\\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0),
  };
}
"""


def feed_url(column_id: int) -> str:
    return f"{BASE}/xgbnew/lm/{column_id}.js"


def to_notice(row: dict, category: str = "") -> RawNotice | None:
    """One feed row as a notice. Pure, so it is unit-testable."""
    if not isinstance(row, dict):
        return None
    title = (row.get("articleTitle") or "").strip()
    href = (row.get("articleUrl") or row.get("aurl") or "").strip()
    if not title or not href:
        return None
    published = (row.get("publishDate") or "").strip()
    return RawNotice(
        url=urljoin(BASE + "/", href),
        title=title,
        date_text=published,
        published_at=dates.published_iso(published),
        category=category or (row.get("channelName") or "").strip(),
    )


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "AutoW/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def fetch_list(context: BrowserContext, page_no: int = 1) -> list[RawNotice]:
    # The whole column arrives in one feed, so there is no second page to walk.
    if page_no > 1:
        return []
    notices: list[RawNotice] = []
    for column, column_id in COLUMNS.items():
        try:
            rows = json.loads(_get(feed_url(column_id)))
        except Exception as exc:
            # One column failing must not cost the other two, and a scan that
            # silently reads nothing is the failure mode worth printing loudly.
            print(f"[{NAME}] {column} 列表读取失败：{type(exc).__name__}: {exc}")
            continue
        if not isinstance(rows, list):
            print(f"[{NAME}] {column} 的数据不是列表，跳过")
            continue
        for row in rows[:MAX_PER_COLUMN]:
            notice = to_notice(row, column)
            if notice:
                notices.append(notice)
    return notices


def fetch_detail(context: BrowserContext, notice: RawNotice) -> RawNotice:
    page = open_page(context, notice.url, DETAIL_READY)
    try:
        lines = [ln.strip() for ln in page.evaluate(_DETAIL_JS)["lines"] if ln.strip()]
    finally:
        page.close()

    # The article area starts with the headline; the date and the source are the
    # two lines under it, then the body.
    if lines and len(lines[0]) >= 6:
        notice.title_full = lines[0]
        lines = lines[1:]
    if lines and dates.published_iso(lines[0]):
        notice.published_text = lines[0]
        notice.published_at = dates.published_iso(lines[0]) or notice.published_at
        lines = lines[1:]

    notice.body = "\n".join(lines).strip()
    if not notice.published_at:
        notice.published_at = dates.tail_full_date(notice.body)
    return notice
