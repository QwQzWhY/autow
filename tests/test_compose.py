"""The two ways to add a todo by hand: pasted text, and typed fields."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autow import cli
from autow import llm
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
        # One connection per test. Opening several onto the same file makes
        # SQLite take locks against itself.
        if getattr(self, "_store", None) is None:
            self._store = Store(self.db)
            self.addCleanup(self._store.close)
        return self._store

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

    # ------------------------------------------------- 同一条通知被转发到多个群

    def test_the_same_notice_in_two_groups_is_one_todo(self):
        for group in ("26级一班", "26级二班"):
            self.ingest(
                {
                    "kind": "qq-message",
                    "group": group,
                    "id": f"episode-{group}",
                    "content": "same-notice",
                    "text": "请于10月25日前提交实验报告到实验中心。",
                }
            )
        self.assertEqual(
            len(self.store().all_todos()), 1, "同一条通知被转到两个群不该变成两条待办"
        )

    def test_two_different_notices_stay_two_todos(self):
        # Without a fingerprint to tell them apart this would wrongly merge.
        for name in ("a", "b"):
            self.ingest(
                {
                    "kind": "qq-message",
                    "group": "G",
                    "id": f"episode-{name}",
                    "content": f"notice-{name}",
                    "text": "请于10月25日前提交实验报告到实验中心。",
                }
            )
        self.assertEqual(len(self.store().all_todos()), 2)

    def test_a_finished_todo_does_not_swallow_a_later_one(self):
        payload = {
            "kind": "qq-message",
            "group": "G",
            "id": "episode-1",
            "content": "same-notice",
            "text": "请于10月25日前提交实验报告到实验中心。",
        }
        self.ingest(payload)
        store = Store(self.db)
        store.conn.execute("UPDATE todos SET status = 'done'")
        store.conn.commit()
        store.close()
        # The same notice comes round again next term.
        self.ingest({**payload, "id": "episode-2"})
        self.assertEqual(len(self.store().all_todos()), 2)

    def test_messages_without_a_fingerprint_still_work(self):
        # Older builds send no `content`; dedupe must not depend on it.
        self.ingest(
            {"kind": "qq-message", "group": "G", "id": "x", "text": "请于10月25日前提交实验报告。"}
        )
        self.assertEqual(len(self.store().all_todos()), 1)


class TestGroupAnnouncement(ComposeTest):
    """群公告走的是同一套抽取，只是它不一定带 @全体成员。

    用户专门要求过要收公告。QQ 把一条群公告当成普通群消息推进群里，通知上的
    措辞里常见「群公告」，但不会可靠地出现 @全体成员，所以关键词表里单列了
    `公告`（见 android .../Keywords.kt）。这里校验云端这一半。
    """

    def ingest_announcement(self, text: str) -> None:
        self.assertEqual(
            self.ingest(
                {
                    "kind": "qq-message",
                    "group": "26级人工智能1班",
                    "sender": "班主任",
                    "id": "ann-1",
                    "text": text,
                }
            ),
            0,
        )

    def test_a_group_announcement_becomes_a_todo(self):
        self.ingest_announcement(
            "【群公告】关于困难认定的通知：请于10月25日前在学工系统提交所需材料。"
        )
        rows = self.store().all_todos()
        self.assertEqual(len(rows), 1, "群公告里的待办没有被建出来")

    def test_an_announcement_split_from_its_mention_is_one_todo(self):
        self.ingest(
            {
                "kind": "qq-message",
                "group": "26级人工智能1班",
                "id": "ann-2",
                "text": "@全体成员",
                "messages": [
                    {"who": "班主任", "text": "@全体成员", "at": "13:00"},
                    {"who": "班主任", "text": "群公告：请于10月25日前提交困难认定材料。", "at": "13:00"},
                ],
            }
        )
        self.assertEqual(len(self.store().all_todos()), 1)

    def test_an_announcement_with_nothing_to_do_creates_nothing(self):
        self.ingest_announcement("群公告：本群禁止发广告，谢谢配合。")
        self.assertEqual(self.store().all_todos(), [])


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def a_verdict(content: str = "{}") -> FakeResponse:
    return FakeResponse({"choices": [{"message": {"content": content}}]})


class TestLongMessages(ComposeTest):
    """超长消息：手机那头已经尽量收全，云端不能再悄悄砍一刀。

    原来这一段是 `body[:6000]`，和普通通知共用，而且砍了没有任何标记——
    缺了后半段的通知读起来和一条就这么长的通知一模一样。
    """

    def setUp(self):
        super().setUp()
        # A real key, so the chat path goes through the HTTP client, and the
        # request that would have gone to the model can be read back.
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_BASE_URL"] = "https://api.example.com/v1"
        os.environ["LLM_MODEL"] = "test-model"
        os.environ.pop("AUTOW_LLM_MODE", None)
        self.addCleanup(self._forget_llm_env)
        self.sent: list[str] = []

        def capture(request, timeout=None):
            payload = json.loads(request.data.decode("utf-8"))
            self.sent.append(payload["messages"][-1]["content"])
            return a_verdict()

        patcher = mock.patch.object(llm.urllib.request, "urlopen", side_effect=capture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _forget_llm_env(self) -> None:
        for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
            os.environ.pop(key, None)

    def body_sent_for(self, text: str) -> str:
        self.ingest({"kind": "qq-message", "group": "G", "id": "long-1", "text": text})
        self.assertEqual(len(self.sent), 1, "应该正好调用一次模型")
        return self.sent[0]

    def test_a_long_notice_reaches_the_model_whole(self):
        # 3000 characters: longer than the phone used to send, and long enough
        # that the old 6000-character cloud cut plus a couple of messages would
        # have taken the tail off.
        notice = "请于10月25日前提交实验报告到实验中心，" + "正文" * 1000 + "末句：请本人签名。"
        self.assertGreater(len(notice), 2000)
        sent = self.body_sent_for(notice)
        self.assertIn(notice, sent, "长正文被云端截断了")
        self.assertNotIn(llm.TRUNCATION_MARK, sent)

    def test_a_body_past_the_cloud_limit_says_it_was_cut(self):
        text = "甲" * (llm.MAX_BODY_CHARS + 500)
        sent = self.body_sent_for(text)
        self.assertIn(llm.TRUNCATION_MARK, sent, "被云端截断了却没说")
        self.assertIn("甲" * llm.MAX_BODY_CHARS, sent)
        self.assertLess(len(sent), llm.MAX_BODY_CHARS + 500, "没有真的截断？")

    def test_a_long_notice_body_is_not_cut_at_the_old_limit(self):
        # 官网通知走的是同一条 `body[:...]`, 旧上限 6000 已经砍到过真实通知
        # （库里最长的一条 6627 字），砍掉的部分同样是无声的。
        body = "关于提交材料的通知。" + "正文" * 4000 + "末句：请本人签字确认。"
        self.assertGreater(len(body), 6000)
        draft, engine = llm.analyze(
            title="关于提交材料的通知", body=body, kind="notice", use_cache=False
        )
        sent = self.sent[-1]
        self.assertIn(body, sent, "长通知的正文被云端截断了")
        self.assertNotIn(llm.TRUNCATION_MARK, sent)
        self.assertIsNone(draft, "这份假请求返回的是空判断")
        self.assertEqual(engine, "llm")

    def test_the_mark_is_the_same_string_the_phone_writes(self):
        # The prompt tells the model to look for this exact sentence, and the
        # phone is what writes it. Two spellings would silently disable that.
        self.assertIn(llm.TRUNCATION_MARK, self.transcript_source(), "手机和云端对「被截断」的说法不一致")
        self.assertIn(llm.TRUNCATION_MARK, llm.CHAT_PROMPT, "提示词没有告诉模型去看这个标记")

    def test_the_cloud_limit_leaves_room_for_the_phone_mark(self):
        # The phone cuts a run at RUN_LIMIT and appends the mark, so the body it
        # sends is a little longer than that. A cloud limit equal to the phone's
        # would cut the marked body again and leave two marks with a gap between
        # them, which reads like three separate losses.
        found = re.search(r"RUN_LIMIT\s*=\s*(\d+)", self.transcript_source())
        self.assertIsNotNone(found, "Transcript.kt 里没有 RUN_LIMIT")
        self.assertGreater(llm.MAX_BODY_CHARS, int(found.group(1)))

    def transcript_source(self) -> str:
        return (
            Path(__file__).resolve().parent.parent
            / "android"
            / "app"
            / "src"
            / "main"
            / "java"
            / "cn"
            / "autow"
            / "bridge"
            / "Transcript.kt"
        ).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
