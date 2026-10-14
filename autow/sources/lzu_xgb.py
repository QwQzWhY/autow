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
import re
import urllib.request
from html import unescape
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext

from .. import dates
from ..browser import CHROME_UA
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
    # Same site family as the college WAF, and it answers non-browser clients
    # with an empty 412 — from the CI runner that is what "this source returned
    # nothing" looked like. Cheap to ask properly rather than to find out.
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": CHROME_UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{BASE}/tongzhigonggao/zizhufuwu/index.html",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def fetch_list(context: BrowserContext, page_no: int = 1) -> list[RawNotice]:
    # The whole column arrives in one feed, so there is no second page to walk.
    if page_no > 1:
        return []
    notices: list[RawNotice] = []
    problems: list[str] = []
    for column, column_id in COLUMNS.items():
        try:
            rows = json.loads(_get(feed_url(column_id)))
        except Exception as exc:
            # One column failing must not cost the other two, and a scan that
            # silently reads nothing is the failure mode worth printing loudly.
            print(f"[{NAME}] {column} 列表读取失败：{type(exc).__name__}: {exc}")
            problems.append(f"{column}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(rows, list):
            print(f"[{NAME}] {column} 的数据不是列表，跳过")
            problems.append(f"{column}: 返回的不是列表")
            continue
        for row in rows[:MAX_PER_COLUMN]:
            notice = to_notice(row, column)
            if notice:
                notices.append(notice)
    if not notices and problems:
        # Raise so the reason reaches docs/data/health.json. Printing it only
        # reaches a workflow log, which needs a login to read — that is how this
        # source went a whole day returning nothing without anyone noticing.
        raise RuntimeError("；".join(problems))
    return notices


def _first_container(html: str, markers: tuple[str, ...]) -> str:
    """The <div> that carries one of these markers, including its content.

    No third-party HTML parser is available, and the pages here are a single
    uniform CMS template, so the container is found by counting div tags rather
    than by pattern-matching to the closing tag (which nested divs break).
    """
    for marker in markers:
        at = html.find(marker)
        if at < 0:
            continue
        opened = html.rfind("<div", 0, at)
        if opened < 0:
            continue
        depth = 0
        index = opened
        while index < len(html):
            next_open = html.find("<div", index)
            next_close = html.find("</div>", index)
            if next_close < 0:
                break
            if 0 <= next_open < next_close:
                depth += 1
                index = next_open + 4
            else:
                depth -= 1
                index = next_close + 6
                if depth == 0:
                    return html[opened:index]
    return ""


def _text(fragment: str) -> str:
    """Visible text of a fragment, with paragraph breaks kept."""
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(?:p|div|li|tr)>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    lines = [line.strip() for line in unescape(fragment).splitlines()]
    return "\n".join(line for line in lines if line)


ARTICLE_MARKERS = ("class=\"article\"", "class='article'")
BODY_MARKERS = ("id=\"news\"", "id='news'")


def parse_article(html: str) -> tuple[str, str, str]:
    """(title, byline, body) from one 学工部 article page. Pure and testable.

    The page is server-rendered after all: the body sits in
    `<div class='article'><h3>标题</h3><div class='infoBox'>日期 来源</div>
    <div id='news'>正文</div></div>`. Reading it over plain HTTP means no
    browser and no WAF challenge in front of every notice — which is what was
    failing for most of them from the CI runner.
    """
    article = _first_container(html, ARTICLE_MARKERS)
    if not article:
        return "", "", ""
    heading = re.search(r"<h3[^>]*>(.*?)</h3>", article, re.S)
    title = _text(heading.group(1)) if heading else ""
    byline = _text(_first_container(article, ("infoBox",)))
    body = _text(_first_container(article, BODY_MARKERS))
    return title, byline, body


def fetch_detail(context: BrowserContext, notice: RawNotice) -> RawNotice:
    """Read one article over plain HTTP. `context` is unused: no browser needed."""
    title, byline, body = parse_article(_get(notice.url))
    if not body:
        # The notice is not stored, so the next scan tries it again — and the
        # failure count in the health file says it is happening.
        raise ValueError(f"页面里找不到正文（模板变了？）：{notice.url}")

    notice.title_full = title or notice.title
    notice.body = body
    if byline:
        notice.published_text = byline
        notice.published_at = dates.published_iso(byline) or notice.published_at
        # "2026-10-13 学工部" — the tail after the date is the issuing office.
        tail = byline.split()[-1]
        if tail and not dates.published_iso(tail) and not notice.category:
            notice.category = tail
    if not notice.published_at:
        notice.published_at = dates.tail_full_date(notice.body)
    return notice
