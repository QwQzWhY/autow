"""兰州大学新闻网的通知公告 (news.lzu.edu.cn/tzgg.htm).

This is the university-wide notice board: 招聘启事、招标、停水、讲座、评选……
Most of it is irrelevant to an undergraduate, which is exactly what the LLM
prompt is for — we hand it everything and let it decide.

The site is an old-school CMS behind the same JS-challenge WAF as the college
site, so it is read through the same stealth browser.
"""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import BrowserContext

from .. import dates
from ..browser import open_page
from ..models import RawNotice

BASE = "https://news.lzu.edu.cn"
NAME = "lzu-news"

LIST_URL = f"{BASE}/tzgg.htm"
LIST_READY = "ul.list > li > a h3"
DETAIL_READY = ".dtl_tit h1"

_LIST_JS = """
() => [...document.querySelectorAll('ul.list > li > a')]
  .filter((a) => a.querySelector('h3'))
  .map((a) => ({
    title: (a.querySelector('h3').innerText || a.getAttribute('title') || '').trim(),
    href: a.getAttribute('href') || '',
    date: (a.querySelector('span')?.innerText || '').trim(),
  }))
"""

_DETAIL_JS = """
() => {
  const head = document.querySelector('.dtl_tit');
  const body = document.querySelector('.dtl_txt');
  return {
    title: (head?.querySelector('h1')?.innerText || '').trim(),
    meta: (head?.querySelector('.cont-tit')?.innerText || '').trim(),
    bodyText: (body?.innerText || '').trim(),
  };
}
"""


def to_notice(row: dict) -> RawNotice | None:
    """Turn one scraped list row into a notice. Pure, so it is unit-testable."""
    title = (row.get("title") or "").strip()
    href = (row.get("href") or "").strip()
    if not title or not href:
        return None
    # Links point at the news site itself, but job.lzu.edu.cn and other campus
    # hosts show up too, so resolve rather than concatenate.
    return RawNotice(
        url=urljoin(LIST_URL, href),
        title=title,
        date_text=(row.get("date") or "").strip(),
        published_at=dates.published_iso(row.get("date") or ""),
    )


def fetch_list(context: BrowserContext, page_no: int = 1) -> list[RawNotice]:
    # The list page has no pagination links; it shows the recent notices in full.
    if page_no > 1:
        return []
    page = open_page(context, LIST_URL, LIST_READY)
    try:
        rows = page.evaluate(_LIST_JS)
    finally:
        page.close()
    return [n for n in (to_notice(row) for row in rows) if n]


def fetch_detail(context: BrowserContext, notice: RawNotice) -> RawNotice:
    page = open_page(context, notice.url, DETAIL_READY)
    try:
        data = page.evaluate(_DETAIL_JS)
    finally:
        page.close()

    notice.title_full = data["title"] or notice.title
    notice.body = data["bodyText"].strip()
    if not notice.published_at:
        notice.published_at = dates.tail_full_date(notice.body)
    # The byline looks like "来源：党委宣传部（融媒体中心）2026-10-12 浏览：176".
    for line in (data["meta"] or "").splitlines():
        if line.strip().startswith("来源"):
            notice.category = "学校通知"
    return notice
