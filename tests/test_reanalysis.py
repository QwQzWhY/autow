"""Changing the extractor has to rebuild the board.

The first real deployment switched from the rule fallback to a working model
and nothing changed: the ten rule-derived todos stayed exactly as they were,
because a duplicate dedupe_key simply failed to insert. `--skip-analyzed` also
kept skipping the notices themselves.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from autow import cli
from autow import llm
from autow.models import RawNotice
from autow.store import Store

try:
    from autow import browser, sources
except ImportError:  # 没装 Playwright 的机器上仍然能跑其余测试
    browser = None
    sources = None


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


class TestNothingFetchedIsNotAReanalysis(unittest.TestCase):
    """一次什么都没抓到的扫描，不能声称自己已经把抽取器换过了。

    实测发生过：CACHE_VERSION 从 8 升到 9 的那次扫描，学校的 WAF 给了空列表
    （对反复访问的爬虫就是回空 412），一条通知都没有判，可它照样把 v9 写进了 meta。
    于是这次升级本来要做的重判被永久跳过——而且从外面完全看不出来：健康文件里
    一切正常，只是所有通知都停留在旧版本的判断上。
    """

    def setUp(self):
        if browser is None or sources is None:
            self.skipTest("没有装 Playwright，跑不了抓取")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "autow.db"
        os.environ["AUTOW_DOCS_DIR"] = str(Path(tmp.name) / "docs")
        self.addCleanup(os.environ.pop, "AUTOW_DOCS_DIR", None)
        os.environ["AUTOW_LLM_MODE"] = "mock"
        self.addCleanup(os.environ.pop, "AUTOW_LLM_MODE", None)
        os.environ.pop("LLM_API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)

    def scan(self, listing: list, details: list | None = None) -> None:
        """跑一次 `run`，列表和详情都由这里喂进去。"""
        source = mock.Mock()
        source.NAME = "lzu-ai"
        source.fetch_list.return_value = listing
        source.fetch_detail.side_effect = details or []

        @contextmanager
        def fake_context(*_args, **_kwargs):
            yield None

        with mock.patch.object(sources, "SOURCES", [source]), mock.patch.object(
            browser, "stealth_context", fake_context
        ), mock.patch.object(cli, "DELAY_BETWEEN_PAGES", 0):
            # 目标行为：一个模拟的"什么都没抓到"的扫描
            rc = cli.main(["--db", str(self.db), "run", "--skip-analyzed", "--pages", "1"])

        self.assertEqual(rc, 0)

    def stored_signature(self) -> str | None:
        # Closed explicitly: an open connection keeps the file locked, and
        # Windows then refuses to delete the temporary directory.
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (cli.SIGNATURE_KEY,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def seed_an_older_version(self) -> None:
        store = Store(self.db)
        store.set_meta(cli.SIGNATURE_KEY, "old-model@example.com#v8")
        store.close()

    def test_an_empty_listing_does_not_claim_the_new_version(self):
        self.seed_an_older_version()
        self.scan([])
        self.assertEqual(
            self.stored_signature(),
            "old-model@example.com#v8",
            "什么都没抓到却把新版本记下了，这次重判就永远不会发生了",
        )

    def test_a_scan_that_judged_something_records_the_version(self):
        self.seed_an_older_version()
        stub = mock.Mock(title="通知", external_id="X1")
        notice = RawNotice(
            url="https://ai.lzu.edu.cn/news/X1",
            title="通知",
            title_full="通知",
            body="请于10月25日前提交实验报告到实验中心。",
            published_at="2026-10-14",
        )
        self.scan([stub], [notice])
        self.assertEqual(self.stored_signature(), llm.signature())

    def test_an_unchanged_version_is_left_as_it_is(self):
        # 没有新通知可判、版本也没变，是正常情况，不该每次都重新抓一遍详情。
        store = Store(self.db)
        store.set_meta(cli.SIGNATURE_KEY, llm.signature())
        store.close()
        self.scan([])
        self.assertEqual(self.stored_signature(), llm.signature())

    def published_health(self) -> dict:
        path = Path(os.environ["AUTOW_DOCS_DIR"]) / "health.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_each_source_reports_what_it_did(self):
        """一个源悄悄不再产出东西，要能在公开文件里看出来。

        CI 日志要登录才能下载，而"哪个源这轮什么都没抓到"正是最先要问的问题。
        """
        stub = mock.Mock(title="通知", external_id="X1")
        notice = RawNotice(
            url="https://ai.lzu.edu.cn/news/X1",
            title="通知",
            title_full="通知",
            body="请于10月25日前提交实验报告到实验中心。",
        )
        self.scan([stub], [notice])
        counts = self.published_health()["sources"]["lzu-ai"]
        self.assertEqual(counts["listed"], 1)
        self.assertEqual(counts["ok"], 1)
        self.assertEqual(counts["failed"], 0)

    def test_a_source_that_listed_nothing_says_so(self):
        self.scan([])
        counts = self.published_health()["sources"]["lzu-ai"]
        self.assertEqual(counts["listed"], 0)

    def test_a_failed_list_fetch_carries_its_error(self):
        source = mock.Mock()
        source.NAME = "lzu-ai"
        source.fetch_list.side_effect = RuntimeError("412 challenge")

        @contextmanager
        def fake_context(*_args, **_kwargs):
            yield None

        with mock.patch.object(sources, "SOURCES", [source]), mock.patch.object(
            browser, "stealth_context", fake_context
        ):
            cli.main(["--db", str(self.db), "run", "--skip-analyzed", "--pages", "1"])
        counts = self.published_health()["sources"]["lzu-ai"]
        self.assertEqual(counts["listed"], 0)
        self.assertIn("412 challenge", counts["error"])

    def test_a_qq_ingest_does_not_wipe_the_last_scans_source_counts(self):
        """QQ 消息会把同一份 health.json 重写一遍，而它不抓网站。

        不带过来的话，上一条群消息一到，上一轮扫描的每个源抓了多少条就没了。
        """
        self.scan([])
        self.assertIn("lzu-ai", self.published_health()["sources"])
        # 同一天里又来了一条群消息
        cli.main(
            [
                "--db",
                str(self.db),
                "ingest",
                "--json",
                json.dumps({"kind": "qq-message", "group": "G", "text": "有人吗"}),
            ]
        )
        self.assertIn(
            "lzu-ai",
            self.published_health()["sources"],
            "群消息把上一轮扫描的诊断信息冲掉了",
        )

    def scan_with_details(self, count: int, failures: int) -> mock.Mock:
        """Run a scan whose first `failures` detail fetches raise."""
        stubs = [mock.Mock(title=f"通知{i}", external_id=f"X{i}") for i in range(count)]
        notice = RawNotice(
            url="https://ai.lzu.edu.cn/news/OK",
            title="通知",
            title_full="通知",
            body="请于10月25日前提交实验报告到实验中心。",
        )
        source = mock.Mock()
        source.NAME = "lzu-ai"
        source.fetch_list.return_value = stubs
        source.fetch_detail.side_effect = [RuntimeError("412")] * failures + [notice] * count

        @contextmanager
        def fake_context(*_args, **_kwargs):
            yield None

        with mock.patch.object(sources, "SOURCES", [source]), mock.patch.object(
            browser, "stealth_context", fake_context
        ), mock.patch.object(cli, "DELAY_BETWEEN_PAGES", 0):
            cli.main(["--db", str(self.db), "run", "--skip-analyzed", "--pages", "1"])
        return source

    def test_a_site_that_keeps_failing_is_given_up_on(self):
        """打不开的页面每条要花一分半，不能把整个扫描拖成两小时。

        实测一次扫描因为 51 次翻页失败跑了 76 分钟。失败的正文不会入库，
        下次扫描本来就会重试，所以连续失败就停手，剩下的留给下一轮。
        """
        source = self.scan_with_details(count=20, failures=20)
        counts = self.published_health()["sources"]["lzu-ai"]
        self.assertEqual(counts["failed"], cli.MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(
            source.fetch_detail.call_count,
            cli.MAX_CONSECUTIVE_FAILURES,
            "连续失败之后还在继续抓——那正是把一次扫描拖到 76 分钟的原因",
        )

    def test_a_success_resets_the_failure_run(self):
        # 中间成功过一次，就不该再算连续失败。
        self.scan_with_details(count=11, failures=cli.MAX_CONSECUTIVE_FAILURES - 1)
        counts = self.published_health()["sources"]["lzu-ai"]
        self.assertEqual(counts["failed"], cli.MAX_CONSECUTIVE_FAILURES - 1)
        self.assertGreater(counts["ok"], 0, "成功一次以后剩下的应该继续抓")


if __name__ == "__main__":
    unittest.main()
