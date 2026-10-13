"""Changing the extractor has to rebuild the board.

The first real deployment switched from the rule fallback to a working model
and nothing changed: the ten rule-derived todos stayed exactly as they were,
because a duplicate dedupe_key simply failed to insert. `--skip-analyzed` also
kept skipping the notices themselves.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from autow import llm
from autow.models import RawNotice
from autow.store import Store


class TestTodoRefresh(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "autow.db")
        self.addCleanup(self.store.close)
        self.notice_id, _ = self.store.upsert_notice(
            "lzu-ai",
            RawNotice(url="https://ai.lzu.edu.cn/news/X1", title="通知", title_full="通知"),
        )

    def add(self, **overrides) -> str:
        fields = dict(
            notice_id=self.notice_id,
            title="提交报告",
            detail="说明",
            due_at="2026-10-20T23:59",
            priority="normal",
            dedupe_key="lzu-ai:X1",
        )
        fields.update(overrides)
        return self.store.add_todo(**fields)

    def test_first_insert_reports_created(self):
        self.assertEqual(self.add(), "created")

    def test_the_same_verdict_again_reports_unchanged(self):
        self.add()
        self.assertEqual(self.add(), "unchanged")

    def test_a_better_model_replaces_the_stale_verdict(self):
        self.add()
        self.assertEqual(self.add(title="交课程报告", priority="high"), "updated")
        row = self.store.all_todos()[0]
        self.assertEqual(row["title"], "交课程报告")
        self.assertEqual(row["priority"], "high")

    def test_refreshing_keeps_the_completion_state(self):
        self.add()
        self.store.conn.execute("UPDATE todos SET status = 'done'")
        self.store.conn.commit()
        self.add(title="换个说法")
        self.assertEqual(self.store.all_todos()[0]["status"], "done")

    def test_a_todo_no_longer_needed_is_removed(self):
        self.add()
        self.assertTrue(self.store.remove_open_todo("lzu-ai:X1"))
        self.assertEqual(self.store.all_todos(), [])

    def test_a_completed_todo_is_left_alone(self):
        self.add()
        self.store.conn.execute("UPDATE todos SET status = 'done'")
        self.store.conn.commit()
        self.assertFalse(self.store.remove_open_todo("lzu-ai:X1"))
        self.assertEqual(len(self.store.all_todos()), 1)

    def test_an_unknown_key_removes_nothing(self):
        self.add()
        self.assertFalse(self.store.remove_open_todo("lzu-ai:NOPE"))
        self.assertEqual(len(self.store.all_todos()), 1)

    def test_the_nomination_flag_round_trips(self):
        self.add(needs_recommendation=True)
        self.assertEqual(self.store.all_todos()[0]["needs_recommendation"], 1)
        self.add(needs_recommendation=False)
        self.assertEqual(self.store.all_todos()[0]["needs_recommendation"], 0)

    def test_flipping_the_nomination_flag_counts_as_a_change(self):
        self.add()
        self.assertEqual(self.add(needs_recommendation=True), "updated")
        self.assertEqual(self.add(needs_recommendation=True), "unchanged")


class TestMigration(unittest.TestCase):
    """The deployed database predates the nomination column."""

    def test_an_old_table_gains_the_new_column(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "old.db"

        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE todos (
                id         INTEGER PRIMARY KEY,
                notice_id  INTEGER NOT NULL,
                title      TEXT NOT NULL,
                detail     TEXT,
                due_at     TEXT,
                priority   TEXT NOT NULL DEFAULT 'normal',
                status     TEXT NOT NULL DEFAULT 'open',
                dedupe_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

        store = Store(path)
        self.addCleanup(store.close)
        columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(todos)")}
        self.assertIn("needs_recommendation", columns)


class TestMeta(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "autow.db")
        self.addCleanup(self.store.close)

    def test_a_missing_key_reads_as_none(self):
        self.assertIsNone(self.store.get_meta("extractor_signature"))

    def test_values_round_trip_and_overwrite(self):
        self.store.set_meta("extractor_signature", "a")
        self.assertEqual(self.store.get_meta("extractor_signature"), "a")
        self.store.set_meta("extractor_signature", "b")
        self.assertEqual(self.store.get_meta("extractor_signature"), "b")


class TestSignature(unittest.TestCase):
    def setUp(self):
        saved = {
            key: os.environ.get(key)
            for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "AUTOW_LLM_MODE")
        }

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)
        os.environ.pop("AUTOW_LLM_MODE", None)

    def test_no_key_is_its_own_signature(self):
        os.environ.pop("LLM_API_KEY", None)
        self.assertEqual(llm.signature(), "rules-only")

    def test_a_model_produces_a_different_signature(self):
        os.environ.update(
            LLM_API_KEY="sk-x", LLM_BASE_URL="https://api.deepseek.com/v1", LLM_MODEL="deepseek-chat"
        )
        self.assertNotEqual(llm.signature(), "rules-only")
        self.assertIn("deepseek-chat", llm.signature())
        self.assertIn("api.deepseek.com", llm.signature())

    def test_switching_model_changes_the_signature(self):
        os.environ.update(
            LLM_API_KEY="sk-x", LLM_BASE_URL="https://api.deepseek.com/v1", LLM_MODEL="deepseek-chat"
        )
        first = llm.signature()
        os.environ["LLM_MODEL"] = "deepseek-reasoner"
        self.assertNotEqual(first, llm.signature())


if __name__ == "__main__":
    unittest.main()
