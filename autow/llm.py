"""LLM-backed extraction.

Talks to any OpenAI-compatible `/chat/completions` endpoint over stdlib HTTP —
no SDK, so it runs anywhere and adds no dependency. Configure with:

    LLM_API_KEY    required to enable
    LLM_BASE_URL   default https://api.deepseek.com/v1
    LLM_MODEL      default deepseek-chat
    AUTOW_LLM_MODE set to "mock" for offline pipeline testing

Results are cached by notice body so re-running the crawler costs nothing.
Anything the model cannot answer falls back to the rule-based extractor, so the
pipeline still produces todos with no key configured at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime

from . import extract
from .extract import TodoDraft
from .models import RawNotice

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MAX_BODY_CHARS = 6000

# Bump this whenever the prompts or the mock/rule logic change, so cached
# verdicts from an older version are not reused. LLM calls cost money and the
# cache is otherwise keyed only on the text.
CACHE_VERSION = 3

# Filled in as calls happen so a run can report why it fell back to rules.
# A misconfigured endpoint used to be invisible unless someone read the CI log.
STATS: dict[str, object] = {"attempts": 0, "failures": 0, "last_error": "", "probe": None}


def reset_stats() -> None:
    STATS.update(attempts=0, failures=0, last_error="", probe=None)


def probe() -> dict:
    """One minimal call, purely to prove the endpoint and key work.

    A scan only calls the model when a *new* notice turns up, so a quiet day
    would otherwise look identical to a broken configuration. This runs every
    time and is cheap enough to be worth it.
    """
    cfg = config()
    if cfg is None:
        result = {"ok": False, "error": "未配置 LLM_API_KEY"}
    elif cfg["mode"] == "mock":
        result = {"ok": True, "note": "mock 模式，未真正调用"}
    else:
        try:
            _call(
                [{"role": "user", "content": '只输出这个 JSON，不要别的内容：{"ok": true}'}],
                cfg,
                cfg["model"],
            )
            result = {"ok": True}
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    STATS["probe"] = result
    return result

NOTICE_PROMPT = """你是一个学生事务助理。用户是兰州大学人工智能学院的学生。
通知有两种来源：学院官网，以及学校层级的通知公告（后者什么都有，噪音很多）。
你要判断一条通知对学生是否意味着"需要做的具体事情"，并把关键信息抽成结构化数据。

规则：
- 只关注学生本人需要采取行动的事项（报名、提交材料、面试、缴费、开题、答辩、选课、公示期内提异议等）。
- 纯粹的新闻、喜报、会议纪要、人事任命、领导讲话、招聘宣讲，action_required 为 false。
- 学校层级的行政和后勤事项与学生本人无关，action_required 为 false，例如：
  招聘启事、招标采购、中标公告、停水停电停暖、设备申购、实验室信息报送、
  干部任免、基金申报、院系院庆公告。面向全校学生的才可能为 true。
- due_at 是这条事项的截止时间，格式 YYYY-MM-DDTHH:MM。没有明确截止时间就填 null，
  不要凭空编造。文字里只写"公示期自9月24日至9月29日"时，取结束日 23:59。
  通知末尾的落款日期不是截止时间。
- 年份缺失时按最近一次发生的时间推断（今天是 {today}）。
- priority：涉及全体学生、措辞紧急、或 3 天内到期 → high；一般事项 → normal；
  可以慢慢看 → low。
- title 用不超过 30 字的祈使句，说清要做什么，不要照抄原标题。
- detail 用一到两句话说明要做什么、去哪里做、要注意什么。

只输出 JSON，不要任何解释。"""

CHAT_PROMPT = """你是一个学生事务助理。用户是兰州大学人工智能学院的学生，
下面是从 QQ 群里抽出来的原始消息（可能很零碎、有错别字、夹杂表情和闲聊）。

判断这条消息是否包含学生本人需要采取行动的事（比如某科作业/实验报告的截止时间、
调课或补课、考试安排、报名、交材料、群里要求回复统计等）。

如果只是闲聊、通知已过期、或与用户无关，action_required 为 false。
不确定时倾向于 false，用户不希望被无关消息打扰。

规则：
- due_at 是截止时间，格式 YYYY-MM-DDTHH:MM，没有明确时间就填 null，不要编造。
- 今天日期是 {today}，年份缺失时按最近一次发生的时间推断。
- priority：涉及全体、措辞紧急、或 3 天内到期 → high；一般 → normal；不急 → low。
- title 用不超过 30 字的祈使句，说清要做什么。
- detail 说明要做什么、什么时候交、去哪里做。不要复述群名、人名或闲聊内容，
  这些内容会被发布到公网上。
- 如果 action_required 为 false，title 和 detail 必须留空字符串，不要摘抄原文。

只输出 JSON，不要任何解释。"""

NOTICE_USER_TEMPLATE = """标题：{title}
发布日期：{published}

正文：
{body}"""

CHAT_USER_TEMPLATE = """群：{group}
发送者：{sender}
发送时间：{published}

消息原文：
{body}"""

JSON_SHAPE = {
    "action_required": "boolean",
    "title": "string，不超过30字",
    "detail": "string",
    "due_at": "YYYY-MM-DDTHH:MM 或 null",
    "priority": "high | normal | low",
}


def config() -> dict[str, str] | None:
    mode = os.environ.get("AUTOW_LLM_MODE", "").lower()
    if mode == "mock":
        return {"mode": "mock", "base_url": "", "api_key": "", "model": "mock"}
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        return None
    return {
        "mode": "live",
        "base_url": normalise_base_url(os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)),
        "api_key": api_key,
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
    }


def normalise_base_url(value: str) -> str:
    """Make a pasted endpoint usable.

    The obvious way to fill in LLM_BASE_URL is to copy the host out of the docs,
    which gives "api.deepseek.com/v1" with no scheme. urllib then rejects it with
    "unknown url type", so repair it here instead of failing the whole scan.
    """
    value = (value or "").strip().rstrip("/")
    if not value:
        return DEFAULT_BASE_URL
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def _cache_key(kind: str, body: str, model: str, today: date) -> str:
    digest = hashlib.sha1(f"{kind}\n{body}".encode("utf-8")).hexdigest()
    return f"v{CACHE_VERSION}:{model}:{today.isoformat()}:{digest}"


def _mock_response(body: str, title: str, kind: str) -> dict:
    """Deterministic stand-in so the pipeline can be tested without a key."""
    headline = title.strip() or "待办"
    draft = extract.extract_todo(
        RawNotice(url="", title=headline, title_full=headline, body=body)
    )
    # The rule extractor quotes the sentence it matched. That is fine for a
    # public notice, and wrong for a private chat message standing in for a
    # model that has been told not to quote. The detail ends up published.
    detail = "" if (draft and kind == "chat") else (draft.detail if draft else "")
    return {
        "action_required": draft is not None,
        "title": draft.title if draft else "",
        "detail": detail,
        "due_at": draft.due_at if draft else None,
        "priority": draft.priority if draft else "low",
    }


def _call(messages: list[dict[str, str]], cfg: dict[str, str], model: str) -> dict:
    url = f"{cfg['base_url']}/chat/completions"
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"LLM_BASE_URL 不是有效的地址：{url!r}")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        body = json.loads(response.read().decode("utf-8"))
    return json.loads(body["choices"][0]["message"]["content"])


def analyze(
    *,
    title: str,
    body: str,
    published: str | None = None,
    group: str = "",
    sender: str = "",
    kind: str = "notice",
    cache=None,
    use_cache: bool = True,
) -> tuple[TodoDraft | None, str]:
    """Return (todo or None, engine) where engine is 'llm', 'rules' or 'mock'."""
    cfg = config()
    if not cfg:
        if kind != "notice":
            return None, "rules"
        return extract.extract_todo(RawNotice(url="", title=title, title_full=title, body=body)), "rules"

    today = date.today()
    model = cfg["model"]
    key = _cache_key(kind, body, model, today)
    raw = cache.cache_get(key, model) if (cache and use_cache) else None

    if raw is None:
        STATS["attempts"] = int(STATS["attempts"]) + 1
        try:
            if cfg["mode"] == "mock":
                data = _mock_response(
                    body, title if kind == "notice" else "QQ 群消息", kind
                )
            else:
                if kind == "chat":
                    system = CHAT_PROMPT.format(today=today.isoformat())
                    user = CHAT_USER_TEMPLATE.format(
                        group=group or "未知",
                        sender=sender or "未知",
                        published=published or "未知",
                        body=body[:MAX_BODY_CHARS],
                    )
                else:
                    system = NOTICE_PROMPT.format(today=today.isoformat())
                    user = NOTICE_USER_TEMPLATE.format(
                        title=title,
                        published=published or "未知",
                        body=body[:MAX_BODY_CHARS],
                    )
                data = _call(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    cfg,
                    model,
                )
            raw = json.dumps(data, ensure_ascii=False)
            # Chat verdicts are deliberately never cached: every message is
            # unique, so a cache buys nothing, and the cached JSON would put a
            # paraphrase of private chat into a public repository.
            if cache and use_cache:
                cache.cache_put(key, model, raw)
        # Deliberately broad: the model call is best-effort, and a misconfigured
        # endpoint or a malformed response must degrade to the rule extractor
        # rather than abort the whole scan. The engine tally printed at the end
        # of a run makes a systematic failure obvious.
        except Exception as exc:
            STATS["failures"] = int(STATS["failures"]) + 1
            STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
            print(f"    [llm] 调用失败，退回规则抽取：{type(exc).__name__}: {exc}")
            if kind != "notice":
                return None, "rules"
            return extract.extract_todo(
                RawNotice(url="", title=title, title_full=title, body=body)
            ), "rules"

    data = json.loads(raw)
    if not data.get("action_required"):
        return None, "mock" if cfg["mode"] == "mock" else "llm"

    priority = str(data.get("priority") or "normal").lower()
    if priority not in {"high", "normal", "low"}:
        priority = "normal"
    due_at = data.get("due_at") or None
    if due_at and not _looks_like_datetime(due_at):
        due_at = None

    draft = TodoDraft(
        title=(data.get("title") or title)[:60],
        detail=(data.get("detail") or "")[:400],
        due_at=due_at,
        priority=priority,
    )
    return draft, "mock" if cfg["mode"] == "mock" else "llm"


def analyze_notice(notice: RawNotice, cache=None) -> tuple[TodoDraft | None, str]:
    return analyze(
        title=notice.title_full or notice.title,
        body=notice.body,
        published=notice.published_at or notice.published_text,
        kind="notice",
        cache=cache,
    )


def _looks_like_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(str(value))
        return True
    except ValueError:
        return False
