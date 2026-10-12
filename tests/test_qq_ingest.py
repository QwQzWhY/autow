"""QQ ingest: the decisions it makes and, just as importantly, what it writes down.

`data/autow.db` is committed to the repository and the dashboard repository is
meant to be public, so a QQ message must leave no trace of itself beyond a
timestamp, a verdict and the extracted task title.
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from autow import cli, qqstatus
from autow.store import Store

GROUP = "计科2201班"
SENDER = "李老师"
MARKER = "ZZTOP"  # distinctive, so a leak is unmistakable

SOON = date.today() + timedelta(days=5)
TEXT = f"【重要】《人工智能导论》报告请于{SOON.month}月{SOON.day}日前提交，{MARKER}，全体同学务必完成。"


class QqIngestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "autow.db"
        # Redirect the dashboard files too: without this the tests would
        # overwrite the real docs/data/*.json that ship with the repository.
        os.environ["AUTOW_DOCS_DIR"] = str(Path(tmp.name) / "docs")
        self.addCleanup(os.environ.pop, "AUTOW_DOCS_DIR", None)
        os.environ["AUTOW_LLM_MODE"] = "mock"
        self.addCleanup(os.environ.pop, "AUTOW_LLM_MODE", None)
        os.environ.pop("LLM_API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)

    def ingest(self, **overrides) -> int:
        payload = {
            "group": GROUP,
            "sender": SENDER,
            "text": TEXT,
            "time": f"{date.today().isoformat()}T10:00",
            "nonce": "n1",
        }
        payload.update(overrides)
        return cli.main(
            ["--db", str(self.db), "ingest", "--json",
             json.dumps(payload, ensure_ascii=False)]
        )

    def store(self) -> Store:
        store = Store(self.db)
        self.addCleanup(store.close)
        return store

    # ------------------------------------------------------------- behaviour

    def test_an_actionable_message_becomes_a_todo(self):
        self.assertEqual(self.ingest(), 0)
        todos = self.store().all_todos()
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["due_at"][:10], SOON.isoformat())
        self.assertEqual(todos[0]["dedupe_key"].split(":")[0], "qq")

    def test_the_same_message_twice_is_not_duplicated(self):
        self.ingest()
        self.ingest()
        store = self.store()
        self.assertEqual(len(store.all_todos()), 1)
        self.assertEqual(store.qq_recent()[0]["verdict"], "duplicate")

    def test_a_chatty_message_is_recorded_but_produces_no_todo(self):
        self.assertEqual(self.ingest(text="哈哈哈今天好累啊，有人一起吃饭吗"), 0)
        store = self.store()
        self.assertEqual(len(store.all_todos()), 0)
        self.assertEqual(store.qq_recent()[0]["verdict"], "ignored")

    def test_without_a_model_nothing_is_guessed(self):
        os.environ.pop("AUTOW_LLM_MODE", None)
        self.assertEqual(self.ingest(), 0)
        store = self.store()
        self.assertEqual(len(store.all_todos()), 0)
        self.assertEqual(store.qq_recent()[0]["verdict"], "no-model")
        self.assertEqual(store.qq_counts()["total_received"], 1)

    def test_an_empty_message_is_an_error(self):
        self.assertEqual(self.ingest(text=""), 1)

    # --------------------------------------------------------------- privacy

    def test_message_content_never_reaches_the_database(self):
        self.ingest()
        blob = self.db.read_bytes()
        for secret in (TEXT, MARKER, GROUP, SENDER):
            self.assertNotIn(
                secret.encode("utf-8"), blob, f"{secret!r} leaked into the committed database"
            )

    def test_the_published_status_file_carries_no_message_content(self):
        self.ingest()
        published = json.dumps(qqstatus.build(self.store()), ensure_ascii=False)
        for secret in (TEXT, MARKER, GROUP, SENDER):
            self.assertNotIn(secret, published, f"{secret!r} leaked into the public status file")
        self.assertIn("n1", published)  # the nonce the setup page matches on


class QqStatusTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "autow.db")
        self.addCleanup(self.store.close)

    def test_an_empty_log_reports_as_not_connected(self):
        data = qqstatus.build(self.store)
        self.assertFalse(data["connected"])
        self.assertEqual(data["recent"], [])
        self.assertEqual(data["today_received"], 0)

    def test_counts_and_recent_entries_are_reported(self):
        self.store.log_qq("todo", "提交课程报告", 42, nonce="abc")
        self.store.log_qq("ignored", "", 10)
        data = qqstatus.build(self.store)
        self.assertTrue(data["connected"])
        self.assertEqual(data["total_received"], 2)
        self.assertEqual(data["total_todos"], 1)
        self.assertEqual(data["today_received"], 2)
        self.assertEqual(data["recent"][0]["verdict"], "ignored")
        self.assertEqual(data["recent"][1]["title"], "提交课程报告")
        self.assertEqual(data["recent"][1]["verdict_label"], "已生成待办")


if __name__ == "__main__":
    unittest.main()
