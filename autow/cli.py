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
from datetime import datetime, timedelta

from . import dates, health, llm, publish, qqstatus
from . import clock
from .extract import TodoDraft
from .models import RawNotice
from .store import Store

CHAT_SOURCE = "qq"
NOTE_SOURCE = "note"
MANUAL_SOURCE = "manual"

# Records which extractor produced the verdicts currently in the database.
SIGNATURE_KEY = "extractor_signature"

# The self-check costs a model call and its answer changes about as often as the
# API key does, so it does not need to run on every scan.
PROBE_KEY = "last_probe"
PROBE_INTERVAL = timedelta(hours=6)

# The site's WAF starts returning empty 412s if it is hammered, so we crawl
# slowly on purpose. This is a "check a few times a day" job, not a scraper.
DELAY_BETWEEN_PAGES = 3.0


def _store(args: argparse.Namespace) -> Store:
    return Store(args.db) if args.db else Store()


def write_dashboards(store: Store) -> None:
    """Refresh everything the phone reads."""
    print("看板数据已写入", publish.write(store))
    qqstatus.write(store)
    health.write()


def describe_llm(store: Store | None = None) -> bool:
    """Say up front which extractor this run will use, and prove it works.

    A misconfigured endpoint used to blow up mid-scan with a stack trace, and
    then (once that was fixed) silently fell back to the rule extractor. Either
    way the answer belonged in the first lines of the log, not at the end of a
    debugging session.
    """
    llm.reset_stats()
    cfg = llm.config()
    if cfg is None:
        print("大模型：未配置 LLM_API_KEY，本次全部走规则抽取")
        return True
    if cfg["mode"] == "mock":
        print("大模型：mock 模式（不联网，用规则结果冒充模型输出）")
        return True
    print(f"大模型：{cfg['model']} @ {cfg['base_url']}")

    if store is not None and not probe_due(store):
        print(f"大模型自检：跳过（{PROBE_INTERVAL} 内已经验证过）")
        return True

    problem = llm.validate_config(cfg)
    if problem:
        # A misconfiguration is not a transient failure. Falling back to the
        # rule extractor here produced a plausible-looking board for a day
        # before anyone noticed, so refuse to run instead.
        print(f"大模型配置有误：{problem}")
        health.write()
        return False

    result = llm.probe()
    if store is not None:
        store.set_meta(PROBE_KEY, clock.iso_now())
    if result.get("ok"):
        print("大模型自检：通过")
    else:
        print(f"大模型自检：失败 —— {result.get('error')}")
    return True


def signature_worth_recording(analysed: int, stored: str | None, signature: str) -> bool:
    """Whether this run may claim the extractor version it just used.

    Finding nothing to do is not the same as having looked. A run whose listing
    came back empty — the site answers a hammered crawler with empty 412s — used
    to record the new signature anyway, so the re-analysis a version bump asks
    for was skipped and never came back: the one run that was supposed to judge
    the old notices again was the run that had nothing to judge.

    Claiming the version is honest once this run has judged something, or once
    the version is already the one on record.
    """
    return analysed > 0 or stored == signature


def probe_due(store: Store) -> bool:
    """Whether this run should spend a call proving the key still works."""
    last = store.get_meta(PROBE_KEY)
    if not last:
        return True
    try:
        seen = datetime.fromisoformat(last)
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=clock.tz())
    return (clock.now() - seen) > PROBE_INTERVAL


# --------------------------------------------------------------------- crawl


def crawl(args: argparse.Namespace, store: Store) -> tuple[int, int, int]:
    """Fetch notices and extract todos. Returns (new notices, new todos, analysed)."""
    # Imported lazily on purpose: the QQ ingest path shares this module and runs
    # in a job with no dependencies installed, so Playwright must not be
    # reachable from `import autow.cli`.
    from .browser import stealth_context
    from .sources import SOURCES

    new_notices = new_todos = analysed = 0
    engines: dict[str, int] = {}

    # `--skip-analyzed` normally means "only look at notices we have never
    # seen". That is wrong when the extractor itself changed: the verdicts
    # already stored were produced by a different brain, so judge them again.
    signature = llm.signature()
    stored_signature = store.get_meta(SIGNATURE_KEY)
    reanalyze = args.skip_analyzed and stored_signature != signature
    if reanalyze:
        print(
            f"抽取器变了（{stored_signature or '未记录'} → {signature}），"
            "本次重新分析所有通知"
        )
    outcomes: dict[str, int] = {}

    with stealth_context(headless=not args.headful) as context:
        for source in SOURCES:
            for page_no in range(1, args.pages + 1):
                try:
                    listing = source.fetch_list(context, page_no=page_no)
                except Exception as exc:
                    print(f"[{source.NAME}] 列表抓取失败：{type(exc).__name__}: {exc}")
                    break
                if not listing:
                    break
                print(f"[{source.NAME} p{page_no}] {len(listing)} 条", flush=True)

                for stub in listing:
                    if args.limit and analysed >= args.limit:
                        break
                    # Check the cheap way first: a list URL already tells us
                    # whether we have seen this notice, so skipping costs no
                    # browser request.
                    if (
                        args.skip_analyzed
                        and not reanalyze
                        and store.has_notice(source.NAME, stub.external_id)
                    ):
                        continue
                    try:
                        notice = source.fetch_detail(context, stub)
                    except Exception as exc:
                        print(f"  FAIL {stub.title[:40]} ({type(exc).__name__})")
                        continue

                    notice_id, is_new = store.upsert_notice(source.NAME, notice)
                    new_notices += int(is_new)
                    print(
                        f"  {'NEW ' if is_new else '    '}{notice.published_at or '??'}  "
                        f"{notice.title_full[:52]}"
                    )

                    draft, engine = llm.analyze_notice(notice, cache=store)
                    analysed += 1
                    engines[engine] = engines.get(engine, 0) + 1
                    key = f"{source.NAME}:{notice.external_id}"
                    if draft:
                        outcome = store.add_todo(
                            notice_id=notice_id,
                            title=draft.title,
                            detail=draft.detail,
                            due_at=draft.due_at,
                            priority=draft.priority,
                            dedupe_key=key,
                            needs_recommendation=draft.needs_recommendation,
                        )
                        outcomes[outcome] = outcomes.get(outcome, 0) + 1
                        if outcome == "created":
                            new_todos += 1
                        if outcome != "unchanged":
                            print(
                                f"        -> [{engine}/{outcome}] {draft.priority:<6} "
                                f"{dates.human(draft.due_at):<17} {draft.title}"
                            )
                    elif store.remove_open_todo(key):
                        # A fresh, better model decided this needs no action.
                        outcomes["removed"] = outcomes.get("removed", 0) + 1
                        print(f"        -> [{engine}/removed] 重新判断为无需行动")
                    time.sleep(DELAY_BETWEEN_PAGES)

    # Only record the signature once the crawl actually judged something. A run
    # that died halfway, or whose listing came back empty, re-analyses again next
    # time instead of skipping the rest forever.
    if signature_worth_recording(analysed, stored_signature, signature):
        store.set_meta(SIGNATURE_KEY, signature)
    else:
        print("本次一条通知都没判，抽取器版本暂不记录，下次扫描继续重判")
    print(f"\n引擎使用：{engines}")
    if outcomes:
        print(f"待办变化：{outcomes}")
    return new_notices, new_todos, analysed


def cmd_fetch(args: argparse.Namespace) -> int:
    store = _store(args)
    if not describe_llm(store):
        store.close()
        return 1
    new_notices, new_todos, analysed = crawl(args, store)
    print(f"{new_notices} 条新通知，{new_todos} 条新待办（分析 {analysed} 条）")
    store.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    store = _store(args)
    if not describe_llm(store):
        store.close()
        return 1
    new_notices, new_todos, analysed = crawl(args, store)
    print(f"{new_notices} 条新通知，{new_todos} 条新待办（分析 {analysed} 条）")
    write_dashboards(store)
    store.close()
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    store = _store(args)
    write_dashboards(store)
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


# -------------------------------------------------------------------- ingest


def normalise_time(value: str | None) -> str:
    """Accept an ISO timestamp, otherwise stamp the message with our own clock.

    The sender is a third-party automation app whose date magic text we cannot
    verify, and a naive value from it would be read as UTC and land eight hours
    off. Delivery is near-instant, so our clock is the more reliable source.
    """
    text = (value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=clock.tz())
            return parsed.astimezone(clock.tz()).isoformat(timespec="seconds")
        except ValueError:
            pass
    return clock.iso_now()


def _stored_notice(digest: str, when: str | None, title: str, category: str) -> RawNotice:
    """An incoming item as it is *stored*.

    The body is intentionally empty and the title carries no group or sender:
    `data/autow.db` is committed to the repository, and the dashboard repo is
    meant to be public. Dedupe uses the digest instead of the raw text.
    """
    return RawNotice(
        url="",
        title=title,
        title_full=title,
        published_text=when or "",
        published_at=(when or "")[:10] or None,
        category=category,
        body="",
        external_id=digest,
    )


def _ingest_chat(data: dict, store: Store) -> int:
    """A message forwarded by the phone's notification bridge."""
    group = (data.get("group") or "").strip()
    sender = (data.get("sender") or "").strip()
    text = (data.get("text") or "").strip()

    # The bridge now sends the run around a mention as separate messages. A
    # transcript keeps the speakers and the order, which is what lets the model
    # tell "these two lines are one notice" from "these are two".
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        lines = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            said = (item.get("text") or "").strip()
            if not said:
                continue
            who = (item.get("who") or "").strip()
            at = (item.get("at") or "").strip()
            head = " ".join(part for part in (who, at) if part)
            lines.append(f"{head}：{said}" if head else said)
        if lines:
            text = "\n".join(lines)
    when = normalise_time(data.get("time"))
    nonce = (data.get("nonce") or "").strip()
    received_at = clock.iso_now()
    # The phone sends a stable id when it has more than one message to join up
    # (a bare "@全体成员" followed by the actual notice). Falling back to the text
    # keeps older builds — which send no id — working unchanged.
    digest = (
        data.get("id") or hashlib.sha1(f"{group}|{sender}|{text}".encode("utf-8")).hexdigest()[:16]
    ).strip()
    category = "QQ 群消息"

    if not text:
        store.log_qq("empty", "", 0, nonce, received_at)
        write_dashboards(store)
        print("消息内容为空", file=sys.stderr)
        return 1

    # Chat messages have no structure to fall back on, so the rule extractor is
    # useless here. Without a model we cannot tell "作业截止" from "大家在吗",
    # so record the fact that a message arrived (the setup page shows it) and
    # leave the board alone rather than filling it with guesses.
    if not llm.config():
        store.upsert_notice(
            CHAT_SOURCE, _stored_notice(digest, when, "QQ 群消息（未处理）", category)
        )
        store.log_qq("no-model", "", len(text), nonce, received_at)
        write_dashboards(store)
        print("未配置大模型（LLM_API_KEY），消息已记录但未处理。", file=sys.stderr)
        return 0

    draft, engine = llm.analyze(
        title="QQ 群消息",
        body=text,
        published=when,
        group=group,
        sender=sender,
        kind="chat",
        cache=store,
        use_cache=False,
    )
    if not draft:
        store.upsert_notice(
            CHAT_SOURCE, _stored_notice(digest, when, "QQ 群消息（无需行动）", category)
        )
        store.log_qq("ignored", "", len(text), nonce, received_at)
        write_dashboards(store)
        print(f"[{engine}] 这条消息不需要行动")
        return 0

    # The same announcement is routinely posted into several class groups, so it
    # arrives here more than once with a different id each time. The content
    # fingerprint collapses those onto one todo.
    content = (data.get("content") or "").strip()
    own_key = f"{CHAT_SOURCE}:{digest}"
    neighbour = store.find_open_todo_by_content(content)
    if neighbour is not None and neighbour["dedupe_key"] == own_key:
        neighbour = None

    if neighbour is not None:
        notice_id = neighbour["notice_id"]
        key = neighbour["dedupe_key"]
    else:
        notice_id, _ = store.upsert_notice(
            CHAT_SOURCE, _stored_notice(digest, when, draft.title, category)
        )
        key = own_key

    outcome = store.add_todo(
        notice_id=notice_id,
        title=draft.title,
        detail=draft.detail,
        due_at=draft.due_at,
        priority=draft.priority,
        dedupe_key=key,
        needs_recommendation=draft.needs_recommendation,
        content_hash=content,
    )
    store.log_qq(
        "todo" if outcome == "created" else "duplicate",
        draft.title,
        len(text),
        nonce,
        received_at,
    )
    write_dashboards(store)
    print(
        f"[{engine}] {'新增' if outcome == 'created' else '已存在'}待办："
        f"{draft.title} / {dates.human(draft.due_at)}"
    )
    return 0


def _ingest_note(data: dict, store: Store) -> int:
    """Text the user pasted into the board, for the model to read."""
    text = (data.get("text") or "").strip()
    if not text:
        print("粘贴的内容为空", file=sys.stderr)
        return 1

    when = normalise_time(data.get("time"))
    digest = (data.get("id") or hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]).strip()
    draft, engine = llm.analyze(
        title=text.splitlines()[0][:60],
        body=text,
        published=when,
        kind="note",
        cache=store,
        # Not cached, for the same reason as chat: the pasted text may be
        # something private, and the cached JSON would put a paraphrase of it
        # into a public repository. Re-pasting the same thing is rare.
        use_cache=False,
    )
    if not draft:
        # The user pasted this on purpose. If the model found nothing — or there
        # is no model configured at all — still create the todo from the first
        # line rather than silently dropping what he asked for. He can delete it.
        headline = text.splitlines()[0].strip()[:60] or "粘贴的待办"
        draft = TodoDraft(title=headline, detail=text[:200], due_at=None, priority="normal")
        engine = f"{engine}/按原文"

    notice_id, _ = store.upsert_notice(
        NOTE_SOURCE, _stored_notice(digest, when, draft.title, "手动录入")
    )
    outcome = store.add_todo(
        notice_id=notice_id,
        title=draft.title,
        detail=draft.detail,
        due_at=draft.due_at,
        priority=draft.priority,
        dedupe_key=f"{NOTE_SOURCE}:{digest}",
        needs_recommendation=draft.needs_recommendation,
    )
    write_dashboards(store)
    print(
        f"[{engine}] {'新增' if outcome == 'created' else '已存在'}待办："
        f"{draft.title} / {dates.human(draft.due_at)}"
    )
    return 0


def _ingest_manual(data: dict, store: Store) -> int:
    """Fields the user typed himself. No model involved."""
    title = (data.get("title") or "").strip()
    if not title:
        print("标题不能为空", file=sys.stderr)
        return 1

    when = normalise_time(data.get("time"))
    due_at = (data.get("due_at") or "").strip() or None
    priority = (data.get("priority") or "normal").strip().lower()
    if priority not in {"high", "normal", "low"}:
        priority = "normal"
    digest = (data.get("id") or hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]).strip()

    notice_id, _ = store.upsert_notice(
        MANUAL_SOURCE, _stored_notice(digest, when, title, "手动录入")
    )
    outcome = store.add_todo(
        notice_id=notice_id,
        title=title[:60],
        detail=(data.get("detail") or "").strip()[:400],
        due_at=due_at,
        priority=priority,
        dedupe_key=f"{MANUAL_SOURCE}:{digest}",
        needs_recommendation=bool(data.get("needs_recommendation")),
    )
    write_dashboards(store)
    print(
        f"[手动] {'新增' if outcome == 'created' else '已存在'}待办："
        f"{title} / {dates.human(due_at)}"
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Take one incoming item and decide whether it becomes a todo.

    Three shapes arrive as a JSON object, via --json, stdin or AUTOW_INGEST_JSON:

        {"kind": "qq-message", "group": …, "text": …}   from the phone bridge
        {"kind": "note", "text": …}                     pasted into the board
        {"kind": "manual", "title": …, "due_at": …}     typed into the board
    """
    raw = args.json or os.environ.get("AUTOW_INGEST_JSON", "") or sys.stdin.read()
    if not raw.strip():
        print("空输入", file=sys.stderr)
        return 1

    data = json.loads(raw)
    kind = (data.get("kind") or "qq-message").strip()
    store = _store(args)
    try:
        if kind == "manual":
            return _ingest_manual(data, store)
        if kind == "note":
            return _ingest_note(data, store)
        return _ingest_chat(data, store)
    finally:
        store.close()


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
