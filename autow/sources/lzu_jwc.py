"""兰州大学教务处的通知公告 (jwc.lzu.edu.cn).

选课、考试、四六级报名、学籍这些事在这里发布，而且学校新闻网的公告里并不都
有它们 —— 库里一条四六级、一条选课都没有，就是漏在这里。

通知公告首页把各个栏目（综合公告、考试中心、实践教学……）各列几条，所以一次
页面加载就覆盖了全部栏目，不用为每一栏各开一次；每条的正文在同一个模板的
`.article` 里。这个站和学院官网一样在 WAF 后面，所以整个源走浏览器。

不是所有 `ul.list` 里的链接都是通知：导航、栏目入口都是同一个 `list` 类，
所以要按文章链接的形状（/2026/0922/337606.html）挑。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext

from .. import dates
from ..browser import open_page
from ..models import RawNotice

BASE = "https://jwc.lzu.edu.cn"
NAME = "lzu-jwc"

LIST_URL = f"{BASE}/tongzhigonggao/index.html"
LIST_READY = "ul.list > li"
DETAIL_READY = ".article"

# The hub page is not paginated; it shows the newest six of each of its nine
# columns (54 rows as of writing), which is what a scan every few hours needs.
# The cap is a backstop above that, not a trim: a cap below the real row count
# silently drops whichever columns come last in the markup, and 考试中心 — the
# one carrying 四六级 — sits near the end.
MAX_ROWS = 60

ARTICLE_HREF = re.compile(r"/20\d\d/\d{4}/\d+\.html$")

_LIST_JS = """
() => {
  const rows = [];
  for (const list of document.querySelectorAll('ul.list')) {
    const heading = list.previousElementSibling;
    const category = (heading?.querySelector('h3')?.innerText || '').trim();
    for (const li of list.querySelectorAll('li')) {
      const link = li.querySelector('a');
      if (!link) continue;
      const href = link.getAttribute('href') || '';
      if (!/\\/20\\d\\d\\/\\d{4}\\/\\d+\\.html$/.test(href)) continue;
      rows.push({
        title: (link.innerText || link.getAttribute('title') || '').trim(),
        href,
        date: (li.querySelector('span')?.innerText || '').trim(),
        category,
      });
    }
  }
  return rows;
}
"""

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


def to_notice(row: dict) -> RawNotice | None:
    """One list row as a notice. Pure, so it is unit-testable."""
    if not isinstance(row, dict):
        return None
    title = (row.get("title") or "").strip()
    href = (row.get("href") or "").strip()
    if not title or not href or not ARTICLE_HREF.search(href):
        return None
    shown = (row.get("date") or "").strip()
    return RawNotice(
        url=urljoin(LIST_URL, href),
        title=title,
        date_text=shown,
        published_at=dates.published_iso(shown),
        category=(row.get("category") or "").strip(),
    )


def fetch_list(context: BrowserContext, page_no: int = 1) -> list[RawNotice]:
    if page_no > 1:
        return []
    page = open_page(context, LIST_URL, LIST_READY)
    try:
        rows = page.evaluate(_LIST_JS)
    finally:
        page.close()
    notices = [n for n in (to_notice(row) for row in rows[:MAX_ROWS]) if n]
    return notices


def fetch_detail(context: BrowserContext, notice: RawNotice) -> RawNotice:
    page = open_page(context, notice.url, DETAIL_READY)
    try:
        lines = [ln.strip() for ln in page.evaluate(_DETAIL_JS)["lines"] if ln.strip()]
    finally:
        page.close()

    if lines and len(lines[0]) >= 6:
        notice.title_full = lines[0]
        lines = lines[1:]
    # The byline reads "发布时间：2026-09-22".
    for index, line in enumerate(lines[:2]):
        if line.startswith("发布时间") or dates.published_iso(line):
            notice.published_text = line.split("：", 1)[-1].strip()
            notice.published_at = dates.published_iso(line) or notice.published_at
            lines = lines[index + 1 :]
            break

    notice.body = "\n".join(lines).strip()
    if not notice.published_at:
        notice.published_at = dates.tail_full_date(notice.body)
    return notice
