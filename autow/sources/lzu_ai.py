"""兰州大学人工智能学院官网 (ai.lzu.edu.cn).

It is a Nuxt SSR site, so the notice list and the notice body are both present
in the HTML once the WAF challenge has been solved by the browser. That means
we can read everything through the DOM and never need a second HTTP client.

The list lives at /news?module=<code>&categoryCode=<code>; the code below was
found on the site itself rather than guessed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import BrowserContext, Page

from .. import dates

BASE = "https://ai.lzu.edu.cn"

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


@dataclass
class RawNotice:
    """A notice as it appears on the list page (body not yet fetched)."""

    url: str
    title: str
    date_text: str = ""
    title_full: str = ""
    published_text: str = ""
    published_at: str | None = None
    category: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)

    # Stable identity for dedupe. Derived from the URL for notices; callers that
    # have no URL (QQ messages) set it explicitly.
    external_id: str = ""

    def __post_init__(self) -> None:
        if not self.external_id and self.url:
            self.external_id = self.url.rstrip("/").rsplit("/", 1)[-1]


def list_url(module: str = NOTICE_MODULE, page_no: int = 1) -> str:
    params = {"module": module, "categoryCode": module}
    if page_no > 1:
        params["page"] = str(page_no)
    return f"{BASE}/news?{urlencode(params)}"


def open_page(context: BrowserContext, url: str, ready_selector: str) -> Page:
    """Navigate and wait past the WAF challenge.

    The first response is a 412 carrying the challenge script; the browser then
    re-requests the page and renders it for real. So we wait for a selector
    that only exists on the genuine page, and retry once if it never shows up.
    """
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        page.wait_for_selector(ready_selector, state="attached", timeout=20_000)
    except Exception as first_error:
        page.reload(wait_until="domcontentloaded", timeout=45_000)
        try:
            page.wait_for_selector(ready_selector, state="attached", timeout=25_000)
        except Exception:
            _dump_failure(page, url)
            raise first_error
    return page


def _dump_failure(page: Page, url: str) -> None:
    """Leave enough evidence behind to explain a failed load."""
    if not os.environ.get("AUTOW_DUMP"):
        return
    out = Path(".debug")
    out.mkdir(exist_ok=True)
    slug = url.replace("https://", "").replace("/", "_").replace("?", "_")[:60]
    try:
        (out / f"{slug}.html").write_text(page.content(), encoding="utf-8")
        (out / f"{slug}.txt").write_text(
            page.evaluate("() => document.body.innerText"), encoding="utf-8"
        )
        page.screenshot(path=str(out / f"{slug}.png"), full_page=True)
        print(f"    [debug] dumped .debug/{slug}.*  title={page.title()!r}")
    except Exception as exc:  # pragma: no cover - diagnostics only
        print("    [debug] dump failed:", exc)


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
