"""Tests for the parsing and extraction logic. No third-party test runner needed:

    python -m unittest discover -s tests
"""

import unittest
from datetime import date, timedelta

from autow import dates, extract
from autow.sources.lzu_ai import RawNotice


class TestDates(unittest.TestCase):
    def test_month_day_picks_year_near_reference(self):
        # A notice published 10-09 mentioning 10月20日 means this year, not last.
        self.assertEqual(
            dates.parse_month_day("10月20日前提交", date(2026, 10, 9)),
            date(2026, 10, 20),
        )

    def test_month_day_in_the_recent_past(self):
        self.assertEqual(
            dates.parse_month_day("10月09日", date(2026, 10, 12)),
            date(2026, 10, 9),
        )

    def test_full_year_date_wins(self):
        self.assertEqual(
            dates.parse_month_day("2024年3月5日", date(2026, 10, 12)),
            date(2024, 3, 5),
        )

    def test_signature_date_at_end_of_body(self):
        body = "有关事项通知如下。\n\n人工智能学院\n2026年10月9日"
        self.assertEqual(dates.tail_full_date(body), "2026-10-09")

    def test_parse_time(self):
        self.assertEqual(dates.parse_time("请于17:30前提交"), (17, 30))
        self.assertIsNone(dates.parse_time("共 99:99 条"))

    def test_published_iso_without_year(self):
        self.assertTrue(dates.published_iso("10月09日").endswith("-10-09"))


class TestExtraction(unittest.TestCase):
    def notice(self, body, title="关于某事的通知", published=None):
        return RawNotice(
            url="https://ai.lzu.edu.cn/news/TEST01",
            title=title,
            title_full=title,
            body=body,
            published_at=published,
        )

    def test_deadline_inside_a_date_range_uses_the_end(self):
        today = date.today()
        start = today + timedelta(days=5)
        end = today + timedelta(days=9)
        notice = self.notice(
            f"现将名单公示如下。公示期自{start.year}年{start.month}月{start.day}日-"
            f"{end.year}年{end.month}月{end.day}日。如有异议请联系。",
            published=today.isoformat(),
        )
        draft = extract.extract_todo(notice)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.due_at[:10], end.isoformat())

    def test_expired_range_is_dropped(self):
        notice = self.notice(
            "现将名单公示如下。公示期自2026年9月24日-2026年9月29日。如有异议请联系。",
            published="2026-09-24",
        )
        self.assertIsNone(extract.extract_todo(notice))

    def test_the_grace_window_is_one_day(self):
        self.assertEqual(extract.EXPIRED_GRACE_DAYS, 1)

    def test_expired_notice_is_dropped(self):
        long_ago = date.today() - timedelta(days=400)
        notice = self.notice(
            f"请于{long_ago.year}年{long_ago.month}月{long_ago.day}日前提交材料。"
        )
        self.assertIsNone(extract.extract_todo(notice))

    def test_upcoming_deadline_is_kept_and_flagged_high(self):
        soon = date.today() + timedelta(days=2)
        notice = self.notice(
            f"全体同学请于{soon.month}月{soon.day}日前提交实验报告。",
            published=date.today().isoformat(),
        )
        draft = extract.extract_todo(notice)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.priority, "high")
        self.assertEqual(draft.due_at[:10], soon.isoformat())

    def test_news_without_action_is_ignored(self):
        notice = self.notice("我院代表队在校运会上取得优异成绩，特此祝贺。", title="喜报")
        self.assertIsNone(extract.extract_todo(notice))

    def test_title_is_cleaned_up(self):
        notice = self.notice(
            f"请于{(date.today() + timedelta(days=5)).month}月"
            f"{(date.today() + timedelta(days=5)).day}日前报名参加。",
            title="关于开展某项工作的通知",
        )
        draft = extract.extract_todo(notice)
        self.assertEqual(draft.title, "开展某项工作")

    def test_external_id_comes_from_the_url(self):
        self.assertEqual(self.notice("").external_id, "TEST01")

    def test_external_id_can_be_set_for_sources_without_a_url(self):
        notice = RawNotice(url="", title="t", title_full="t", external_id="deadbeef")
        self.assertEqual(notice.external_id, "deadbeef")


class TestClipText(unittest.TestCase):
    """标题和详情超长时要说出来，不能从句子中间无声地断掉。

    这五处原来都是裸的 `[:60]` / `[:400]`。看板是把详情原样显示的，所以一条被
    切掉后半截的说明，看上去就是它本来就写到那里为止。
    """

    def test_short_text_is_untouched(self):
        self.assertEqual(extract.clip_text("去图书馆还书", extract.TITLE_LIMIT), "去图书馆还书")

    def test_text_exactly_at_the_limit_is_untouched(self):
        text = "字" * extract.TITLE_LIMIT
        self.assertEqual(extract.clip_text(text, extract.TITLE_LIMIT), text)

    def test_over_long_text_is_marked(self):
        text = "字" * (extract.DETAIL_LIMIT + 50)
        clipped = extract.clip_text(text, extract.DETAIL_LIMIT)
        self.assertTrue(clipped.endswith(extract.TAIL_MARK), clipped[-12:])
        self.assertLessEqual(len(clipped), extract.DETAIL_LIMIT + len(extract.TAIL_MARK))

    def test_it_stops_at_a_sentence_end_when_there_is_one(self):
        # 400 字以内最后一处句号在 380，就从那里断开，不要多带半句。
        first = "甲" * 378 + "。"
        text = first + "乙" * 100
        clipped = extract.clip_text(text, extract.DETAIL_LIMIT)
        self.assertEqual(clipped, first + extract.TAIL_MARK)

    def test_a_mark_at_the_very_start_is_ignored(self):
        # 第一句太短就分段没意义，宁可切在硬上限上。
        text = "。短。" + "丙" * extract.DETAIL_LIMIT
        clipped = extract.clip_text(text, extract.DETAIL_LIMIT)
        self.assertEqual(clipped, text[: extract.DETAIL_LIMIT] + extract.TAIL_MARK)

    def test_it_never_marks_twice(self):
        once = extract.clip_text("字" * 1000, extract.DETAIL_LIMIT)
        self.assertEqual(extract.clip_text(once, extract.DETAIL_LIMIT), once)

    def test_empty_and_blank_input_is_fine(self):
        self.assertEqual(extract.clip_text("", extract.DETAIL_LIMIT), "")
        self.assertEqual(extract.clip_text("   ", extract.DETAIL_LIMIT), "")
        self.assertEqual(extract.clip_text(None, extract.DETAIL_LIMIT), "")

    def test_the_rule_extractor_uses_it(self):
        # 规则抽取的 detail 取自原文那一句，长句同样要带标记。
        notice = RawNotice(
            url="https://ai.lzu.edu.cn/news/TESTCLIP",
            title="关于提交材料的通知",
            title_full="关于提交材料的通知",
            body="请于10月25日前提交材料。" + "补充说明" * 200,
        )
        draft = extract.extract_todo(notice)
        self.assertIsNotNone(draft)
        self.assertLessEqual(len(draft.detail), extract.DETAIL_LIMIT + len(extract.TAIL_MARK))


if __name__ == "__main__":
    unittest.main()
