"""Command line entry point.

    python -m autow run              # 采集 + 抽取 + 生成看板数据（云端用这个）
    python -m autow fetch            # 只采集，不发布
    python -m autow todos -v         # 看当前待办
    python -m autow publish          # 只重新生成看板数据
    python -m autow ingest --json …  # 接收 QQ 群消息并判断是否生成待办
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

from . import dates, llm, publish
from .browser import stealth_context
from .sources import lzu_ai
from .sources.lzu_ai import RawNotice
from .store import Store

NOTICE_SOURCE = "lzu-ai"
CHAT_SOURCE = "qq"

# The site's WAF starts returning empty 412s if it is hammered, so we crawl
# slowly on purpose. This is a "check a few times a day" job, not a scraper.
DELAY_BETWEEN_PAGES = 3.0


def _store(args: argparse.Namespace) -> Store:
    return Store(args.db) if args.db else Store()


# --------------------------------------------------------------------- crawl


def crawl(args: argparse.Namespace, store: Store) -> tuple[int, int, int]:
    """Fetch notices and extract todos. Returns (new notices, new todos, analysed)."""
    new_notices = new_todos = analysed = 0
    engines: dict[str, int] = {}

    with stealth_context(headless=not args.headful) as context:
        for page_no in range(1, args.pages + 1):
            listing = lzu_ai.fetch_list(context, page_no=page_no)
            if not listing:
                break
            print(f"[list p{page_no}] {len(listing)} notices", flush=True)

            for stub in listing:
                if args.limit and analysed >= args.limit:
                    break
                # Check the cheap way first: a list URL already tells us whether
                # we have seen this notice, so skipping costs no browser request.
                if args.skip_analyzed and store.has_notice(NOTICE_SOURCE, stub.external_id):
                    continue
                try:
                    notice = lzu_ai.fetch_detail(context, stub)
                except Exception as exc:
                    print(f"  FAIL {stub.title[:40]} ({type(exc).__name__})")
                    continue

                notice_id, is_new = store.upsert_notice(NOTICE_SOURCE, notice)
                new_notices += int(is_new)
                print(
                    f"  {'NEW ' if is_new else '    '}{notice.published_at or '??'}  "
                    f"{notice.title_full[:52]}"
                )

                draft, engine = llm.analyze_notice(notice, cache=store)
                analysed += 1
                engines[engine] = engines.get(engine, 0) + 1
                if draft:
                    created = store.add_todo(
                        notice_id=notice_id,
                        title=draft.title,
                        detail=draft.detail,
                        due_at=draft.due_at,
                        priority=draft.priority,
                        dedupe_key=f"{NOTICE_SOURCE}:{notice.external_id}",
                    )
                    if created:
                        new_todos += 1
                        print(
                            f"        -> [{engine}] {draft.priority:<6} "
                            f"{dates.human(draft.due_at):<17} {draft.title}"
                        )
                time.sleep(DELAY_BETWEEN_PAGES)

    print(f"\n引擎使用：{engines}")
    return new_notices, new_todos, analysed


def cmd_fetch(args: argparse.Namespace) -> int:
    store = _store(args)
    new_notices, new_todos, analysed = crawl(args, store)
    print(f"{new_notices} 条新通知，{new_todos} 条新待办（分析 {analysed} 条）")
    store.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    store = _store(args)
    new_notices, new_todos, analysed = crawl(args, store)
    path = publish.write(store)
    print(f"{new_notices} 条新通知，{new_todos} 条新待办（分析 {analysed} 条）")
    print(f"看板数据已写入 {path}")
    store.close()
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    store = _store(args)
    print("看板数据已写入", publish.write(store))
    store.close()
    return 0


# --------------------------------------------------------------------- todos


def cmd_todos(args: argparse.Namespace) -> int:
    store = _store(args)
    rows = store.open_todos(include_done=args.all)
    if not rows:
        print("没有待办。先跑一次 `python -m autow run`。")
        store.close()
        return 0
    for row in rows:
        flag = {"high": "!!", "normal": "  ", "low": " ."}.get(row["priority"], "  ")
        print(f"{flag} {dates.human(row['due_at']):<17} {row['title']}")
        if args.verbose:
            print(f"     {row['detail'][:160]}")
            print(f"     {row['url']}")
    print(f"\n共 {len(rows)} 条")
    store.close()
    return 0


# -------------------------------------------------------------------- ingest


def cmd_ingest(args: argparse.Namespace) -> int:
    """Take one forwarded QQ message and decide whether it is a todo.

    Input is a JSON object, either via --json or on stdin:
        {"group": "计科2201班", "sender": "李老师", "text": "…", "time": "2026-10-12T10:00"}
    """
    raw = args.json or os.environ.get("AUTOW_INGEST_JSON", "") or sys.stdin.read()
    if not raw.strip():
        print("空输入", file=sys.stderr)
        return 1

    # Chat messages have no structure to fall back on, so the rule extractor is
    # useless here — without a model we would either drop everything or add noise.
    if not llm.config():
        print("未配置大模型（LLM_API_KEY），QQ 消息无法判断，已跳过。", file=sys.stderr)
        return 1

    data = json.loads(raw)
    group = (data.get("group") or "").strip()
    sender = (data.get("sender") or "").strip()
    text = (data.get("text") or "").strip()
    when = (data.get("time") or "").strip() or None
    if not text:
        print("消息内容为空", file=sys.stderr)
        return 1

    store = _store(args)
    digest = hashlib.sha1(f"{group}|{sender}|{text}".encode("utf-8")).hexdigest()[:16]
    notice = RawNotice(
        url=data.get("url") or "",
        title=f"[{group}] {sender}: {text[:60]}",
        title_full=f"QQ 群 {group} · {sender}",
        published_text=when or "",
        published_at=(when or "")[:10] or None,
        category="QQ 群消息",
        body=text,
        external_id=digest,
    )

    notice_id, _ = store.upsert_notice(CHAT_SOURCE, notice)
    draft, engine = llm.analyze(
        title=notice.title_full,
        body=text,
        published=when,
        group=group,
        sender=sender,
        kind="chat",
        cache=store,
    )
    if not draft:
        print(f"[{engine}] 不需要行动：{text[:60]}")
        store.close()
        return 0

    created = store.add_todo(
        notice_id=notice_id,
        title=draft.title,
        detail=draft.detail,
        due_at=draft.due_at,
        priority=draft.priority,
        dedupe_key=f"{CHAT_SOURCE}:{digest}",
    )
    print(
        f"[{engine}] {'新增' if created else '已存在'}待办："
        f"{draft.title} / {dates.human(draft.due_at)}"
    )
    publish.write(store)
    store.close()
    return 0


# ---------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autow", description="学校通知 -> 待办")
    parser.add_argument("--db", default=None, help="SQLite 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_crawl_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--pages", type=int, default=1, help="列表翻页数")
        p.add_argument("--limit", type=int, default=0, help="最多处理多少条通知")
        p.add_argument("--headful", action="store_true", help="显示浏览器窗口（调试用）")
        p.add_argument(
            "--skip-analyzed",
            action="store_true",
            help="跳过已抓过的通知，只分析新通知（省 LLM 调用）",
        )

    run = sub.add_parser("run", help="采集 + 抽取 + 生成看板数据")
    add_crawl_flags(run)
    run.set_defaults(func=cmd_run)

    fetch = sub.add_parser("fetch", help="只采集和抽取，不生成看板")
    add_crawl_flags(fetch)
    fetch.set_defaults(func=cmd_fetch)

    pub = sub.add_parser("publish", help="重新生成看板数据")
    pub.set_defaults(func=cmd_publish)

    todos = sub.add_parser("todos", help="列出台上待办")
    todos.add_argument("--all", action="store_true", help="包含已完成的")
    todos.add_argument("-v", "--verbose", action="store_true", help="显示详情和链接")
    todos.set_defaults(func=cmd_todos)

    ingest = sub.add_parser("ingest", help="接收一条 QQ 群消息")
    ingest.add_argument("--json", default="", help="直接给 JSON，不给则从 stdin 读")
    ingest.set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
