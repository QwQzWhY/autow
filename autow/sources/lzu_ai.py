"""兰州大学人工智能学院官网 (ai.lzu.edu.cn).

It is a Nuxt SSR site, so the notice list and the notice body are both present
in the HTML once the WAF challenge has been solved by the browser. That means
we can read everything through the DOM and never need a second HTTP client.

The list lives at /news?module=<code>&categoryCode=<code>; the code below was
found on the site itself rather than guessed.
"""

from __future__ import annotations

from urllib.parse import urlencode

from playwright.sync_api import BrowserContext

from .. import dates
from ..browser import open_page
from ..models import RawNotice

__all__ = ["NAME", "NOTICE_MODULE", "RawNotice", "list_url", "fetch_list", "fetch_detail"]

BASE = "https://ai.lzu.edu.cn"
NAME = "lzu-ai"

# 通知公告 — the section a student actually needs to watch.
NOTICE_MODULE = "ACA011ZZHQ"

LIST_READY = "article.news-list__item"
DETAIL_READY = ".news-article__header"

_LIST_JS = """
() => [...document.querySelectorAll('article.news-list__item')].map((art) => ({
  title: (art.querySelector('.news-list__title')?.innerText || '').trim(),
  href: art.querySelector('.news-list__title')?.getAttribute('href') || '',
  date: (art.querySelector('time')?.innerText || '').trim(),
}))
"""

_DETAIL_JS = """
() => {
  const header = document.querySelector('.news-article__header');
  const body =
    document.querySelector('.rich-text-preview__content') ||
    document.querySelector('.news-article__body');
  return {
    headerText: header ? header.innerText : '',
    title: (document.querySelector('.news-article__header h1')?.innerText || '').trim(),
    bodyText: body ? body.innerText : '',
  };
}
"""


def list_url(module: str = NOTICE_MODULE, page_no: int = 1) -> str:
    params = {"module": module, "categoryCode": module}
    if page_no > 1:
        params["page"] = str(page_no)
    return f"{BASE}/news?{urlencode(params)}"


def fetch_list(context: BrowserContext, module: str = NOTICE_MODULE, page_no: int = 1) -> list[RawNotice]:
    page = open_page(context, list_url(module, page_no), LIST_READY)
    try:
        rows = page.evaluate(_LIST_JS)
    finally:
        page.close()

    notices = []
    for row in rows:
        href = row["href"]
        if not href:
            continue
        notices.append(
            RawNotice(
                url=href if href.startswith("http") else BASE + href,
                title=row["title"],
                date_text=row["date"],
            )
        )
    return notices


def fetch_detail(context: BrowserContext, notice: RawNotice) -> RawNotice:
    page = open_page(context, notice.url, DETAIL_READY)
    try:
        data = page.evaluate(_DETAIL_JS)
    finally:
        page.close()

    header_lines = [ln.strip() for ln in data["headerText"].splitlines() if ln.strip()]
    notice.title_full = data["title"] or notice.title
    for line in header_lines:
        if line.startswith("发布时间"):
            notice.published_text = line.split("：", 1)[-1].strip()
        elif line.startswith("分类"):
            notice.category = line.split("：", 1)[-1].strip()
    notice.body = data["bodyText"].strip()
    notice.published_at = (
        dates.published_iso(notice.published_text)
        or dates.tail_full_date(notice.body)
    )
    return notice
