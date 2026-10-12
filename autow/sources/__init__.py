"""Per-site adapters. Each one knows how to list and read one website.

A source module exposes:

    NAME                                    stable id stored in the database
    fetch_list(context, page_no=1)          -> list[RawNotice]
    fetch_detail(context, notice)           -> RawNotice (title/body/date filled in)
"""

from . import lzu_ai, lzu_news

SOURCES = (lzu_ai, lzu_news)

__all__ = ["lzu_ai", "lzu_news", "SOURCES"]
