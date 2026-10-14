"""The two ways to add a todo by hand: pasted text, and typed fields."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from autow import cli
from autow.store import Store


class ComposeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "autow.db"
        os.environ["AUTOW_DOCS_DIR"] = str(Path(tmp.name) / "docs")
        self.addCleanup(os.environ.pop, "AUTOW_DOCS_DIR", None)
        os.environ["AUTOW_LLM_MODE"] = "mock"
        self.addCleanup(os.environ.pop, "AUTOW_LLM_MODE", None)
        os.environ.pop("LLM_API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)

    def ingest(self, payload: dict) -> int:
        return cli.main(
            ["--db", str(self.db), "ingest", "--json", json.dumps(payload, ensure_ascii=False)]
        )

    def store(self) -> Store:
        store = Store(self.db)
        self.addCleanup(store.close)
        return store

    # ------------------------------------------------------------ 手动填写

    def test_manual_creates_the_todo_exactly_as_typed(self):
        self.assertEqual(
            self.ingest(
                {
                    "kind": "manual",
                    "title": "去图书馆还书",
                    "due_at": "2026-10-20T18:00",
                    "priority": "high",
                    "detail": "三楼",
                }
            ),
            0,
        )
        row = self.store().all_todos()[0]
        self.assertEqual(row["title"], "去图书馆还书")
        self.assertEqual(row["due_at"], "2026-10-20T18:00")
        self.assertEqual(row["priority"], "high")
        self.assertEqual(row["detail"], "三楼")

    def test_manual_without_a_title_is_an_error(self):
        self.assertEqual(self.ingest({"kind": "manual", "title": "   "}), 1)

    def test_manual_works_without_any_model_configured(self):
        os.environ.pop("AUTOW_LLM_MODE", None)
        self.assertEqual(self.ingest({"kind": "manual", "title": "交电费"}), 0)
        self.assertEqual(self.store().all_todos()[0]["title"], "交电费")

    def test_a_bad_priority_falls_back_to_normal(self):
        self.ingest({"kind": "manual", "title": "X", "priority": "垃圾"})
        self.assertEqual(self.store().all_todos()[0]["priority"], "normal")

    def test_an_empty_due_date_is_allowed(self):
        self.ingest({"kind": "manual", "title": "X", "due_at": ""})
        self.assertIsNone(self.store().all_todos()[0]["due_at"])

    # ------------------------------------------------------------ 粘贴通知

    def test_note_runs_the_model_and_creates_a_todo(self):
        self.assertEqual(
            self.ingest(
                {
                    "kind": "note",
                    "text": "关于提交实验报告的通知：请于10月25日前将实验报告交至实验中心。",
                }
            ),
            0,
        )
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_note_with_no_text_is_an_error(self):
        self.assertEqual(self.ingest({"kind": "note", "text": "   "}), 1)

    # ------------------------------------------------------------ 标识与去重

    def test_the_client_id_becomes_the_dedupe_key(self):
        # The board predicts this key, so it can drop its optimistic card as soon
        # as the real one arrives.
        self.ingest({"kind": "manual", "id": "abc123", "title": "X"})
        self.assertEqual(self.store().all_todos()[0]["dedupe_key"], "manual:abc123")

    def test_pasting_the_same_text_twice_does_not_duplicate(self):
        payload = {"kind": "note", "text": "请于10月25日前提交实验报告到实验中心。"}
        self.ingest(payload)
        self.ingest(payload)
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_typing_the_same_thing_twice_does_not_duplicate(self):
        payload = {"kind": "manual", "title": "交电费"}
        self.ingest(payload)
        self.ingest(payload)
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_a_pasted_note_always_creates_something(self):
        # He pasted it deliberately; failing to parse must not mean losing it.
        self.ingest({"kind": "note", "text": "下周三记得找辅导员签字"})
        rows = self.store().all_todos()
        self.assertEqual(len(rows), 1)
        self.assertIn("辅导员", rows[0]["title"])

    def test_manual_items_stay_out_of_the_qq_log(self):
        # The setup page's log is about QQ messages; a typed todo is not one.
        self.ingest({"kind": "manual", "title": "X"})
        self.assertEqual(self.store().qq_recent(), [])

    def test_an_unknown_kind_is_treated_as_a_qq_message(self):
        # The phone bridge sends no `kind` at all, and must keep working.
        self.assertEqual(self.ingest({"text": "有人在吗"}), 0)
        self.assertEqual(len(self.store().qq_recent()), 1)


class TestMentionContext(ComposeTest):
    """A bare "@全体成员" is often its own message, with the notice right after.

    The bridge arms the group on a match and re-sends what follows under the same
    id, so the two arrive here joined up. These check the cloud half of that: the
    id — not the text — decides which todo is being written to.
    """

    def setUp(self):
        super().setUp()
        self.base = {"kind": "qq-message", "group": "26级测试群", "sender": "王老师", "id": "g123"}

    def test_a_mention_alone_creates_nothing(self):
        self.assertEqual(self.ingest({**self.base, "text": "@全体成员"}), 0)
        self.assertEqual(self.store().all_todos(), [])

    def test_the_follow_up_turns_the_mention_into_one_todo(self):
        self.ingest({**self.base, "text": "@全体成员"})
        self.ingest(
            {
                **self.base,
                "text": "@全体成员\n请于10月25日前提交实验报告到实验中心。",
            }
        )
        rows = self.store().all_todos()
        self.assertEqual(len(rows), 1, "触发消息和后续内容应该合成一条待办")
        # Only the count is asserted: with the mock the title is fixed and chat
        # details are blanked on purpose, so the text cannot be checked here.
        self.assertEqual(rows[0]["dedupe_key"], "qq:g123")

    def test_a_second_follow_up_refreshes_rather_than_duplicates(self):
        self.ingest(
            {**self.base, "text": "@全体成员\n请于10月25日前提交实验报告到实验中心。"}
        )
        self.ingest(
            {
                **self.base,
                "text": "@全体成员\n请于10月25日前提交实验报告到实验中心。\n补充：封面要写学号。",
            }
        )
        rows = self.store().all_todos()
        self.assertEqual(len(rows), 1, "同一条通知的补充不该变成第二条待办")

    def test_a_different_id_is_a_different_todo(self):
        self.ingest(
            {**self.base, "text": "@全体成员\n请于10月25日前提交实验报告到实验中心。"}
        )
        self.ingest(
            {
                **self.base,
                "id": "g124",
                "text": "@全体成员\n请于11月2日前提交课程论文。",
            }
        )
        self.assertEqual(len(self.store().all_todos()), 2)

    def test_a_message_without_an_id_still_works(self):
        # Older builds of the bridge send no id at all; they must not break.
        self.ingest(
            {"kind": "qq-message", "group": "G", "sender": "S", "text": "@全体成员 请于10月25日前提交实验报告。"}
        )
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_the_structured_messages_are_what_gets_read(self):
        # `text` here carries no action at all; only the transcript does. If the
        # cloud looked at `text` alone this would produce nothing.
        self.ingest(
            {
                "kind": "qq-message",
                "group": "G",
                "id": "g1",
                "text": "@全体成员",
                "messages": [
                    {"who": "王老师", "text": "@全体成员", "at": "13:00"},
                    {"who": "王老师", "text": "请于10月25日前提交实验报告到实验中心。", "at": "13:01"},
                ],
            }
        )
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_the_notice_may_come_before_the_mention(self):
        # The bridge sends the whole run either way round, so the order inside
        # the transcript is all the cloud has to go on.
        self.ingest(
            {
                "kind": "qq-message",
                "group": "G",
                "id": "g2",
                "text": "@全体成员",
                "messages": [
                    {"who": "王老师", "text": "请于10月25日前提交实验报告到实验中心。", "at": "13:00"},
                    {"who": "王老师", "text": "@全体成员", "at": "13:01"},
                ],
            }
        )
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_chatter_around_a_mention_creates_nothing(self):
        self.ingest(
            {
                "kind": "qq-message",
                "group": "G",
                "id": "g3",
                "text": "@全体成员",
                "messages": [
                    {"who": "A", "text": "@全体成员", "at": "13:00"},
                    {"who": "B", "text": "哈哈哈哈", "at": "13:01"},
                ],
            }
        )
        self.assertEqual(self.store().all_todos(), [])

    def test_a_malformed_message_list_is_ignored_not_fatal(self):
        self.assertEqual(
            self.ingest(
                {
                    "kind": "qq-message",
                    "group": "G",
                    "text": "请于10月25日前提交实验报告到实验中心。",
                    "messages": ["不是对象", {"who": "S"}, None],
                }
            ),
            0,
        )
        self.assertEqual(len(self.store().all_todos()), 1, "应该退回用 text 字段")


if __name__ == "__main__":
    unittest.main()
