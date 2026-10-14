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
from urllib.parse import urlparse

from . import extract
from . import clock
from .extract import TodoDraft
from .models import RawNotice

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
# Raised from 6000 when the user asked for long messages to arrive whole. Two
# notices already in the database are longer than 6000 characters, so the old
# value was cutting real text — silently, which made the tail look like text
# that had never existed.
#
# Deliberately above the phone's Transcript.RUN_LIMIT (20000): the phone marks
# where it cut, and that mark is part of the body, so an equal cap here would
# cut the marked body again and leave two marks and a gap between them.
MAX_BODY_CHARS = 24000

# Said wherever text was dropped, by the phone and by the cloud alike, and what
# CHAT_PROMPT tells the model to look for. The phone's copy lives in
# Transcript.kt; tests/test_compose.py fails if the two ever drift apart, since a
# differently spelled marker would quietly stop matching the instruction.
TRUNCATION_MARK = "……（通知里只带了这么多，剩下的被系统截断了）"

# Bump this whenever the prompts or the post-processing change, so cached
# verdicts from an older version are not reused. It is also part of the
# extractor signature, which is what makes a deployment re-analyse everything.
# 9: the body sent to the model is no longer cut at 6000 characters, and all
# three prompts now recognise the truncation marker. Verdicts stored under v8
# were produced from a body that may have been missing its tail.
#
# 10: v9 was recorded by two runs that judged nothing at all — the school's WAF
# answered the runner with an empty listing, and the signature was written at
# the end of the crawl regardless (fixed in `signature_worth_recording`). So the
# database says v9 while holding v8's verdicts, and the re-analysis has to be
# asked for once more.
#
# 11: notices aimed at a role the user does not hold are now dropped. Widening
# the sources to 教务处 brought a 学习委员培训 notice onto the board — correct
# text, wrong reader — so both the profile and the notice prompt say it.
#
# 12: v11's re-analysis could not actually reach most of the notices — the pages
# are unreadable from the runner, and a failed fetch left the old verdict
# standing, so the rule never applied to the item it was written for. Re-issued
# together with the change that re-judges stored text instead of re-fetching it.
CACHE_VERSION = 12

# Who the advice is for. The "no post" half is not decoration: 教务处 and 学工部
# both publish things addressed to 学习委员, 班委, 团支书 and so on, and without
# this the model has no way to know the reader is not one of them.
DEFAULT_PROFILE = (
    "兰州大学人工智能学院的本科生，没有担任班委、学习委员、团支部书记、心理委员、"
    "学生会或社团干部等任何学生职务"
)
DEFAULT_GRADE = "2026 级（2026 年入学，现在是本科一年级）"

# Filled in as calls happen so a run can report why it fell back to rules.
# A misconfigured endpoint used to be invisible unless someone read the CI log.
STATS: dict[str, object] = {
    "attempts": 0,
    "failures": 0,
    "last_error": "",
    "probe": None,
    "calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
}


def reset_stats() -> None:
    STATS.update(
        attempts=0,
        failures=0,
        last_error="",
        probe=None,
        calls=0,
        prompt_tokens=0,
        completion_tokens=0,
    )


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

NOTICE_PROMPT = """你是一个学生事务助理。用户是{profile}。
通知有两种来源：学院官网，以及学校层级的通知公告（后者什么都有，噪音很多）。
你只保留用户本人需要动手去做的事，其余一律 action_required 为 false。

保留（学生本人能参加、受益或主动提交的，包括自愿性质的）：
- 考试报名：普通话水平测试、四六级、计算机等级考试、各类资格考试
- 活动报名与参与：竞赛、讲座报告会、志愿服务、文体活动、社团活动、培训计划
- 面向学生的评选与选拔：奖学金评定、荣誉称号、骨干培养计划、实习与交流项目
- 学籍与培养：选课退课、成绩复核、转专业、评奖评优、助学金、助学贷款
- 需要本人提交材料、缴费、确认、回复的事项
- 公示期内学生本人可以提异议的事项

判断标准是"学生本人能不能主动去参加或报名"，而不是"是不是每个人都必须做"。
自愿参加的也要保留，不要因为"不是必须"就丢掉。

注意：**通知的收件人是谁不重要，重要的是对学生本人有没有用。** 很多通知开头写的是
"请各学院组织报名""各单位提交名单"，但内容是面向学生的活动、评选或培养计划 ——
这类要保留，把 title 写成学生自己的动作，并按下面的规则标好 needs_recommendation。
不要因为它"是发给学院的"就丢掉。

用户的年级是{grade}，还要按年级过滤：
- 大一新生不会遇到推免、保研、毕业设计、毕业论文、答辩、就业派遣这类临近毕业的事务，
  看到就直接 action_required 为 false。
- 已经过去的年级专属事项（例如入学军训、新生报到）也不要保留。

要丢掉的（与学生本人无关）：
- 招聘启事、招标采购、中标公告、停水停电停暖、设备申购、资产与数据报送、基建维修
- 面向教师、教职工、研究生、校友的事务（教师培训、学术会议征稿、校友活动等）
- 学生本人无事可做的纯行政流程：实验室信息统计、资产盘点、数据报送、会场安排 ——
  判断标准是"学生本人有没有事情可做"，而不是"通知的收件人是不是学院"
- 新闻、喜报、会议纪要、领导讲话、活动报道
- 面向**特定角色或小群体**的通知：学习委员、班委、团支书、心理委员、学生会或社团干部、
  党员、退伍学生、少数民族骨干等。用户是普通学生，没有这些身份，所以"学习委员培训"
  "班团骨干座谈会""××负责人会议"这类要丢掉 —— 先看对象是**全体学生**还是**某个角色**，
  是角色才丢。只有当用户确实持有那个身份时才保留（用户没有）。

规则：
- due_at 是这条事项的截止时间，格式 YYYY-MM-DDTHH:MM。没有明确截止时间就填 null，
  不要凭空编造。文字里只写"公示期自9月24日至9月29日"时，取结束日 23:59。
  通知末尾的落款日期不是截止时间。
- 年份缺失时按最近一次发生的时间推断（今天是 {today}）。
- priority：涉及全体学生、措辞紧急、或 3 天内到期 → high；一般事项 → normal；
  可以慢慢看 → low。
- title 用不超过 30 字的祈使句，说清要做什么，不要照抄原标题。
- detail 用一到两句话说明要做什么、去哪里做、要注意什么。
- needs_recommendation：学生本人可以直接去报名、参加或提交的，填 false；
  必须先经学院选拔、推荐或分配名额才能参加的（骨干培养计划、需要学院推荐的评比、
  由学院组队报名的比赛等），填 **true**。这类**不是丢掉**，而是标出来单独展示，
  让用户自己判断要不要争取。拿不准填 false。
- 正文末尾如果出现「……（通知里只带了这么多，剩下的被系统截断了）」，说明这条通知的
  正文不完整：就按现有的内容判断，不要凭空补出被截掉的过程或时间。
- 拿不准是否与学生本人有关时，action_required 为 false —— 用户宁可漏掉，
  也不想被无关的事情打扰。

只输出 JSON，不要任何解释。"""

CHAT_PROMPT = """你是一个学生事务助理。用户是兰州大学人工智能学院的学生，
下面是从 QQ 群里抽出来的一段原始消息（可能是单独一条，也可能是连续的几条，
每行开头是发送者和时间）。可能很零碎、有错别字、夹杂表情和闲聊。

**先把属于同一件事的几行归到一起**：典型情况是"@全体成员"单独一行，正文在它
前面或后面另一行；也可能一条通知被拆成好几行发出来。归好之后按那一件事来判断和抽取。

群里**发的公告**（形如「群公告」「【群公告】」或直接写着"公告"的那条）往往就是要交材料、
有截止时间的事情，按上面同样的标准判断，不要因为它是公告就当成背景信息略过。

判断这段内容是否包含学生本人需要采取行动的事（比如某科作业/实验报告的截止时间、
调课或补课、考试安排、报名、交材料、群里要求回复统计等）。

如果只是闲聊、通知已过期、或与用户无关，action_required 为 false。
不确定时倾向于 false，用户不希望被无关消息打扰。

用户是普通学生，没有担任班委、学习委员、团支书、心理委员、学生会或社团干部这类职务，
所以针对这些角色的消息（学委培训、班团骨干会议之类）同样与他无关。

规则：
- due_at 是截止时间，格式 YYYY-MM-DDTHH:MM，没有明确时间就填 null，不要编造。
- 今天日期是 {today}，年份缺失时按最近一次发生的时间推断。
- priority：涉及全体、措辞紧急、或 3 天内到期 → high；一般 → normal；不急 → low。
- title 用不超过 30 字的祈使句，说清要做什么。
- detail 说明要做什么、什么时候交、去哪里做。不要复述群名、人名或闲聊内容，
  这些内容会被发布到公网上。
- needs_recommendation：需要先由学院推荐或选拔才能参加的填 true，否则填 false。
- 如果消息末尾出现「……（通知里只带了这么多，剩下的被系统截断了）」，说明手机拿到的
  通知本身就不全：就按现有的内容抽取，并在 detail 里写明"通知被截断，全文要回群里看"。
- 如果 action_required 为 false，title 和 detail 必须留空字符串，不要摘抄原文。

只输出 JSON，不要任何解释。"""

# Note for whoever changes a prompt: CHAT_PROMPT is deliberately excluded from
# CACHE_VERSION. Chat verdicts are never cached (see `analyze`), and the version
# is what decides whether stored *notice* verdicts get re-judged — bumping it
# over a chat-only wording change would make every notice in the database be
# analysed again for no reason.

PASTED_PROMPT = """你是一个学生事务助理。用户是{profile}，年级是{grade}。
下面这段文字是**用户自己主动粘贴进来的**，他希望能变成一条待办。

关键是：**既然是他自己粘的，就说明他关心这件事。** 不要用"是不是人人都必须做"
来判断，也不要因为它看起来像行政通知就丢掉。只有当这段文字明显是广告、垃圾信息、
或者完全没有任何可行动内容（例如只是一句寒暄）时，action_required 才为 false。

规则：
- title 用不超过 30 字的祈使句，说清要做什么；如果原文本身就是一条通知标题，
  保留它的关键信息，不要改写成含糊的概括。
- detail 用一到两句话说明要做什么、去哪里做、要注意什么，可以引用原文里的关键句。
- due_at 是截止时间，格式 YYYY-MM-DDTHH:MM，没有明确时间就填 null，不要编造。
  今天是 {today}，年份缺失时按最近一次发生的时间推断。原文末尾的落款日期不是截止时间。
- priority：措辞紧急、涉及全体、或 3 天内到期 → high；一般 → normal；不急 → low。
- needs_recommendation：需要先由学院推荐或选拔才能参加的填 true，否则填 false。
- 如果正文末尾出现「……（通知里只带了这么多，剩下的被系统截断了）」，说明内容不完整，
  按现有内容抽取即可，不要凭空补全。

只输出 JSON，不要任何解释。"""

PASTED_USER_TEMPLATE = """{body}"""

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
    "needs_recommendation": "boolean，需要学院推荐才能参加时为 true",
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


# Values that are credentials, not configuration. Publishing a model name or an
# endpoint into a public health file is fine — until someone pastes a key into
# one of those fields, which is exactly what happened on the first deployment.
# Careful: "glm-4-flash" is a real model name, so provider prefixes are not a
# reliable signal on their own. These are the ones that are never model names.
SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xai-", "AKIA", "Bearer ")


def secret_like(value: str) -> bool:
    """Heuristic: does this look like a credential rather than a name?"""
    text = (value or "").strip()
    if not text:
        return False
    return text.startswith(SECRET_PREFIXES)


def validate_config(cfg: dict[str, str] | None) -> str | None:
    """Return a human-readable problem, or None when the config is usable."""
    if cfg is None or cfg.get("mode") != "live":
        return None

    host = urlparse(cfg["base_url"]).hostname or ""
    if "." not in host:
        # Never echo the value back: if it is the key, the message itself
        # becomes the leak (that is exactly how it escaped the second time).
        return (
            "LLM_BASE_URL 填的不是一个网址。这一项要填接口地址，"
            "例如 https://api.deepseek.com/v1 —— 不是 API Key。"
            + ("（这个值看起来像密钥，已隐去）" if secret_like(host) or len(host) > 40 else "")
        )
    if secret_like(cfg["model"]) or cfg["model"] == cfg["api_key"]:
        return (
            "LLM_MODEL 填的不是模型名。"
            "这一项要填模型标识，例如 deepseek-chat —— 不是 API Key。"
        )
    return None


def signature() -> str:
    """Identifies *how* notices are being judged.

    Stored alongside the data so that switching model — or recovering from the
    rule fallback to a working key — re-analyses everything instead of leaving
    stale verdicts attached to notices that were already fetched.
    """
    cfg = config()
    if cfg is None:
        return "rules-only"
    if cfg["mode"] == "mock":
        return "mock"
    return f"{cfg['model']}@{urlparse(cfg['base_url']).hostname}#v{CACHE_VERSION}"


def profile() -> str:
    """Who the advice is for. Widening this changes what survives filtering."""
    return os.environ.get("AUTOW_STUDENT_PROFILE", "").strip() or DEFAULT_PROFILE


def clip_body(body: str) -> str:
    """The body as the model will see it, with any cut said out loud.

    This used to be `body[:6000]` on three separate lines, with no sign that
    anything was missing: a notice whose second half had been dropped reads
    exactly like a notice that ended there.
    """
    if len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS] + "\n" + TRUNCATION_MARK


def grade() -> str:
    """The user's year. Drives the "not for you yet" rules (推免, 毕业设计…)."""
    return os.environ.get("AUTOW_STUDENT_GRADE", "").strip() or DEFAULT_GRADE


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

    # Recorded so the cost of a run is visible instead of guessed at.
    usage = body.get("usage") or {}
    STATS["calls"] = int(STATS["calls"]) + 1
    STATS["prompt_tokens"] = int(STATS["prompt_tokens"]) + int(usage.get("prompt_tokens") or 0)
    STATS["completion_tokens"] = int(STATS["completion_tokens"]) + int(
        usage.get("completion_tokens") or 0
    )
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
        # A pasted note is worth falling back on: the rule extractor still finds
        # explicit dates, which beats silently creating nothing.
        if kind == "chat":
            return None, "rules"
        return extract.extract_todo(RawNotice(url="", title=title, title_full=title, body=body)), "rules"

    today = clock.today()
    model = cfg["model"]
    key = _cache_key(kind, body, model, today)
    raw = cache.cache_get(key, model) if (cache and use_cache) else None

    if raw is None:
        STATS["attempts"] = int(STATS["attempts"]) + 1
        try:
            if cfg["mode"] == "mock":
                data = _mock_response(
                    body, title if kind in ("notice", "note") else "待办", kind
                )
            else:
                if kind == "chat":
                    system = CHAT_PROMPT.format(today=today.isoformat())
                    user = CHAT_USER_TEMPLATE.format(
                        group=group or "未知",
                        sender=sender or "未知",
                        published=published or "未知",
                        body=clip_body(body),
                    )
                elif kind == "note":
                    # Pasted by the user, so relevance filtering is off: he
                    # already decided this matters.
                    system = PASTED_PROMPT.format(
                        today=today.isoformat(), profile=profile(), grade=grade()
                    )
                    user = PASTED_USER_TEMPLATE.format(body=clip_body(body))
                else:
                    system = NOTICE_PROMPT.format(
                        today=today.isoformat(), profile=profile(), grade=grade()
                    )
                    user = NOTICE_USER_TEMPLATE.format(
                        title=title,
                        published=published or "未知",
                        body=clip_body(body),
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
            if kind == "chat":
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

    engine = "mock" if cfg["mode"] == "mock" else "llm"
    draft = TodoDraft(
        title=extract.clip_text(data.get("title") or title, extract.TITLE_LIMIT),
        detail=extract.clip_text(data.get("detail") or "", extract.DETAIL_LIMIT),
        due_at=due_at,
        priority=priority,
        needs_recommendation=bool(data.get("needs_recommendation")),
    )
    if extract.is_expired(draft.due_at):
        print(f"    [{engine}] 截止时间已过（{draft.due_at}），不作为待办")
        return None, engine
    return draft, engine


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
