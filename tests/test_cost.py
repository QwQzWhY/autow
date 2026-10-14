"""What a run costs, and the two places it was spending more than it had to.

The phone used to re-send the whole context on every message of a run, so a
mention split into four lines cost four workflow runs and four model calls, each
on a longer transcript than the last. And every scan spent a call on the
self-check even though the answer changes about as often as the API key does.
"""

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from autow import cli, clock, health, llm
from autow.store import Store


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def reply(prompt_tokens: int, completion_tokens: int, content: str = "{}") -> FakeResponse:
    return FakeResponse(
        {
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }
    )


class TestTokenAccounting(unittest.TestCase):
    def setUp(self):
        llm.reset_stats()
        self.addCleanup(llm.reset_stats)
        self.cfg = {"mode": "live", "base_url": "https://api.example.com/v1", "api_key": "k"}

    def test_usage_from_each_call_is_added_up(self):
        with mock.patch.object(
            llm.urllib.request, "urlopen", side_effect=[reply(100, 20), reply(50, 10)]
        ):
            llm._call([], self.cfg, "m")
            llm._call([], self.cfg, "m")
        self.assertEqual(llm.STATS["calls"], 2)
        self.assertEqual(llm.STATS["prompt_tokens"], 150)
        self.assertEqual(llm.STATS["completion_tokens"], 30)

    def test_a_response_without_usage_does_not_break_the_count(self):
        bare = FakeResponse({"choices": [{"message": {"content": "{}"}}]})
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=bare):
            llm._call([], self.cfg, "m")
        self.assertEqual(llm.STATS["calls"], 1)
        self.assertEqual(llm.STATS["prompt_tokens"], 0)

    def test_the_totals_are_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {
                    "AUTOW_DOCS_DIR": tmp,
                    "LLM_API_KEY": "k",
                    "LLM_BASE_URL": "https://api.example.com/v1",
                    "LLM_MODEL": "m",
                },
            ):
                llm.STATS["calls"] = 3
                llm.STATS["prompt_tokens"] = 900
                tokens = health.build()["extractor"]["tokens"]
            self.assertEqual(tokens, {"calls": 3, "prompt": 900, "completion": 0})


class TestProbeThrottle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "autow.db")
        self.addCleanup(self.store.close)

        # Without a configured model describe_llm() returns before the throttle.
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
        os.environ.update(
            LLM_API_KEY="sk-x",
            LLM_BASE_URL="https://api.example.com/v1",
            LLM_MODEL="m",
        )

    def test_a_first_run_probes(self):
        self.assertTrue(cli.probe_due(self.store))

    def test_a_recent_probe_is_not_repeated(self):
        self.store.set_meta(cli.PROBE_KEY, clock.iso_now())
        self.assertFalse(cli.probe_due(self.store), "几分钟内不该再花一次调用")

    def test_an_old_probe_runs_again(self):
        stale = clock.now() - cli.PROBE_INTERVAL - timedelta(minutes=1)
        self.store.set_meta(cli.PROBE_KEY, stale.isoformat(timespec="seconds"))
        self.assertTrue(cli.probe_due(self.store))

    def test_a_corrupt_timestamp_probes_rather_than_crashing(self):
        self.store.set_meta(cli.PROBE_KEY, "不是时间")
        self.assertTrue(cli.probe_due(self.store))

    def test_a_successful_probe_is_recorded(self):
        self.store.set_meta(cli.PROBE_KEY, "")
        with mock.patch.object(llm, "probe", return_value={"ok": True}):
            self.assertTrue(cli.describe_llm(self.store))
        self.assertTrue(self.store.get_meta(cli.PROBE_KEY))

    def test_a_skipped_probe_leaves_the_record_alone(self):
        self.store.set_meta(cli.PROBE_KEY, clock.iso_now())
        before = self.store.get_meta(cli.PROBE_KEY)
        with mock.patch.object(llm, "probe", side_effect=AssertionError("不该调用")):
            self.assertTrue(cli.describe_llm(self.store))
        self.assertEqual(self.store.get_meta(cli.PROBE_KEY), before)


if __name__ == "__main__":
    unittest.main()
