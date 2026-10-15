"""Turn a notice into a todo.

Stage 0 is deliberately rule-based: it is free, offline and predictable. The
`extract_todo` function is the single seam where an LLM call can later be
slotted in — see LLM_HOOK below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from . import dates
from . import clock
from .models import RawNotice

# Wording that means "you have to do something".
ACTION_WORDS = (
    "报名", "申请", "提交", "截止", "答辩", "面试", "考试", "推荐", "评选",
    "登记", "确认", "领取", "缴费", "上传", "填写", "申报", "注册", "选课",
    "材料", "审核", "开题", "中期", "体检", "公示期", "异议", "反馈",
    "受理", "投票", "统计", "征集",
)

# A deadline is usually announced with one of these.
DEADLINE_WORDS = ("截止", "之前", "前完成", "截至", "前提交", "前报名", "前将", "前完成")

# "自9月24日-9月29日" / "9月24日至9月29日" — the end of the range is the deadline.
RANGE_SPLIT = re.compile(r"[-—~～至到]")

URGENT_WORDS = ("务必", "重要", "紧急", "全体", "各位同学", "所有", "尽快", "及时")

# Boilerplate / announcements that rarely need action from the reader.
NOISE_WORDS = ("喜报", "祝贺", "获奖", "纪实", "召开", "举行", "回顾", "招聘宣讲")

SENTENCE_SPLIT = re.compile(r"[。\n；;]")

# A deadline this far in the past is history, not a todo. One day of slack so a
# notice whose deadline was this morning does not vanish the moment it passes.
EXPIRED_GRACE_DAYS = 1

# How old a notice may be before "no deadline" means "old news".
#
# The official sites list their newest few items per column, and some of those
# columns run slowly: 教务处's front page in October still carried January's
# spring-semester registration notice. Notices that are still live carry a
# deadline, so age plus no deadline is the signal.
STALE_AFTER_DAYS = 30


def is_expired(due_at: str | None, grace_days: int = EXPIRED_GRACE_DAYS) -> bool:
    """True when a deadline is far enough in the past to stop being a todo.

    Shared by both extractors. The rule path always had this check; the model
    path did not, so a re-run happily resurrected deadlines from June.
    """
    if not due_at:
        return False
    try:
        days = (datetime.fromisoformat(str(due_at)).date() - clock.today()).days
    except ValueError:
        return False
    return days < -grace_days


def is_stale_notice(
    published_at: str | None,
    due_at: str | None,
    today: date | None = None,
    days: int = STALE_AFTER_DAYS,
) -> bool:
    """Whether a notice is too old to still be a todo.

    Only when it has no deadline either: a notice published in September and due
    in November is perfectly live, and the deadline is what says so.
    """
    if due_at:
        return False
    if not published_at:
        return False
    try:
        published = date.fromisoformat(str(published_at)[:10])
    except ValueError:
        return False
    return ((today or clock.today()) - published).days > days


@dataclass
class TodoDraft:
    title: str
    detail: str
    due_at: str | None
    priority: str
    # True when the student cannot simply sign up — the college has to nominate
    # or select them first. Surfaced separately on the board.
    needs_recommendation: bool = False


# What the board shows. A title is meant to be a short imperative and a detail
# one or two sentences, so these only bite on something unexpected.
TITLE_LIMIT = 60
DETAIL_LIMIT = 400

# Appended where a title or a detail was cut. Not the same sentence as the phone
# bridge's Transcript.CLIPPED — that one means "the notification never had the
# rest", this one means "the board only shows this much".
TAIL_MARK = "…（后文略）"


def clip_text(text: str, limit: int) -> str:
    """Trim to `limit`, ending on a sentence and saying that it was cut.

    These were bare `[:60]` and `[:400]` slices in five places. A Chinese
    sentence stopped mid-word with nothing to mark it, so the board showed a
    detail that read like it simply ended there — the same silent half-a-message
    problem as the body limit, just smaller.
    """
    value = (text or "").strip()
    if len(value) <= limit or value.endswith(TAIL_MARK):
        return value
    head = value[:limit]
    # Prefer a sentence end: a whole sentence plus a mark reads better than one
    # extra clause and a dangling connector.
    for sentence_end in ("。", "！", "？", "；", "\n"):
        at = head.rfind(sentence_end)
        if at >= limit // 2:
            return head[: at + 1] + TAIL_MARK
    return head.rstrip() + TAIL_MARK


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(body or "") if s.strip()]


def _deadline_from(body: str, reference: date | None = None) -> tuple[str | None, str]:
    """Best-effort deadline plus the sentence it came from.

    `reference` should be the notice's publish date: dates in the body are
    written without a year, and relative to publication they are usually close by.
    """
    reference = reference or clock.today()
    best: tuple[date, str] | None = None
    for sentence in _sentences(body):
        if not any(w in sentence for w in DEADLINE_WORDS + ACTION_WORDS):
            continue
        found = (
            _range_end(sentence, reference)
            or dates.parse_full(sentence)
            or dates.parse_month_day(sentence, reference)
        )
        if not found:
            continue
        if best is None or found < best[0]:
            best = (found, sentence)

    if best is None:
        return None, ""

    day, sentence = best
    # Not named `clock`: that would shadow the module imported above.
    hhmm = dates.parse_time(sentence)
    if hhmm:
        due = datetime(day.year, day.month, day.day, hhmm[0], hhmm[1])
    else:
        due = datetime(day.year, day.month, day.day, 23, 59)
    return due.isoformat(timespec="minutes"), sentence


def _range_end(sentence: str, reference: date) -> date | None:
    """If the sentence holds a date range, return its later end."""
    if not RANGE_SPLIT.search(sentence):
        return None
    parts = RANGE_SPLIT.split(sentence)
    parsed = [dates.parse_full(p) or dates.parse_month_day(p, reference) for p in parts]
    parsed = [d for d in parsed if d]
    return max(parsed) if len(parsed) >= 2 else None


def _clean_title(title: str) -> str:
    title = re.sub(r"^关于", "", title.strip())
    title = re.sub(r"的通知$|的公示$|的公告$|的函$", "", title)
    return title.strip() or title


def extract_todo(notice: RawNotice) -> TodoDraft | None:
    """Return a todo for this notice, or None when it needs no action."""
    blob = f"{notice.title_full}\n{notice.body}"
    if any(w in notice.title_full for w in NOISE_WORDS):
        return None
    if not any(w in blob for w in ACTION_WORDS):
        return None

    reference = None
    if notice.published_at:
        try:
            reference = date.fromisoformat(notice.published_at)
        except ValueError:
            reference = None

    due_at, evidence = _deadline_from(notice.body, reference)
    priority = "high" if any(w in blob for w in URGENT_WORDS) else "normal"
    if due_at:
        if is_expired(due_at):
            return None
        try:
            days = (datetime.fromisoformat(due_at).date() - clock.today()).days
            if 0 <= days <= 3:
                priority = "high"
        except ValueError:
            pass

    detail = evidence or notice.title_full
    return TodoDraft(
        title=_clean_title(notice.title_full),
        detail=clip_text(detail, DETAIL_LIMIT),
        due_at=due_at,
        priority=priority,
    )


# LLM_HOOK -------------------------------------------------------------------
# Later this becomes: build a prompt from notice.body, ask the model for
# {"title", "due_at", "priority", "action_required"} against a JSON schema, and
# fall back to the rules above when the call fails or no API key is configured.
# ---------------------------------------------------------------------------
