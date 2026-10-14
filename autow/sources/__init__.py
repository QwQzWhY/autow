"""Per-site adapters. Each one knows how to list and read one website.

A source module exposes:

    NAME                                    stable id stored in the database
    fetch_list(context, page_no=1)          -> list[RawNotice]
    fetch_detail(context, notice)           -> RawNotice (title/body/date filled in)
"""

from . import lzu_ai, lzu_jwc, lzu_news, lzu_xgb

SOURCES = (lzu_ai, lzu_jwc, lzu_xgb, lzu_news)

__all__ = ["lzu_ai", "lzu_jwc", "lzu_news", "lzu_xgb", "SOURCES"]
