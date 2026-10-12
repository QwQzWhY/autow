"""The model call is best-effort: a bad endpoint must degrade, never abort.

The first cloud run died with
    ValueError: unknown url type: '***/chat/completions'
because LLM_BASE_URL had been filled in as "api.deepseek.com/v1" (no scheme)
and the exception escaped the scan loop. Both halves of that are covered here.
"""

import os
import unittest
from datetime import date, timedelta
from unittest import mock

from autow import llm
from autow.models import RawNotice

SOON = date.today() + timedelta(days=5)
BODY = f"请全体同学于{SOON.month}月{SOON.day}日前提交课程报告。"


def a_notice() -> RawNotice:
    return RawNotice(
        url="https://ai.lzu.edu.cn/news/TEST99",
        title="关于提交课程报告的通知",
        title_full="关于提交课程报告的通知",
        body=BODY,
        published_at=date.today().isoformat(),
    )


class TestBaseUrl(unittest.TestCase):
    def test_a_bare_host_gets_a_scheme(self):
        self.assertEqual(
            llm.normalise_base_url("api.deepseek.com/v1"), "https://api.deepseek.com/v1"
        )

    def test_an_explicit_scheme_is_left_alone(self):
        self.assertEqual(
            llm.normalise_base_url("http://127.0.0.1:8080/v1"), "http://127.0.0.1:8080/v1"
        )

    def test_whitespace_and_trailing_slash_are_trimmed(self):
        self.assertEqual(
            llm.normalise_base_url("  https://api.deepseek.com/v1/  "),
            "https://api.deepseek.com/v1",
        )

    def test_blank_falls_back_to_the_default(self):
        self.assertEqual(llm.normalise_base_url("   "), llm.DEFAULT_BASE_URL)

    def test_config_normalises_the_environment_value(self):
        with mock.patch.dict(
            os.environ,
            {"LLM_API_KEY": "k", "LLM_BASE_URL": "api.deepseek.com/v1"},
            clear=False,
        ):
            os.environ.pop("AUTOW_LLM_MODE", None)
            self.assertEqual(llm.config()["base_url"], "https://api.deepseek.com/v1")


class TestCallGuard(unittest.TestCase):
    def test_a_scheme_less_url_is_rejected_with_a_readable_message(self):
        cfg = {"mode": "live", "base_url": "api.deepseek.com/v1", "api_key": "k", "model": "m"}
        with self.assertRaises(ValueError) as ctx:
            llm._call([], cfg, "m")
        self.assertIn("LLM_BASE_URL", str(ctx.exception))


class TestFallback(unittest.TestCase):
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
        os.environ["LLM_API_KEY"] = "test-key"

    def test_an_unexpected_model_error_degrades_to_rules(self):
        # Not a URLError: this is the shape that killed the first cloud run.
        boom = mock.Mock(side_effect=ValueError("unknown url type"))
        with mock.patch.object(llm, "_call", boom):
            draft, engine = llm.analyze_notice(a_notice())
        self.assertEqual(engine, "rules")
        self.assertIsNotNone(draft, "规则抽取应该仍然产出待办")
        self.assertEqual(draft.due_at[:10], SOON.isoformat())

    def test_an_unreachable_endpoint_degrades_to_rules(self):
        # Port 9 refuses immediately, so the test stays fast.
        os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
        draft, engine = llm.analyze_notice(a_notice())
        self.assertEqual(engine, "rules")
        self.assertIsNotNone(draft)


if __name__ == "__main__":
    unittest.main()
