"""The model call is best-effort: a bad endpoint must degrade, never abort.

The first cloud run died with
    ValueError: unknown url type: '***/chat/completions'
because LLM_BASE_URL had been filled in as "api.deepseek.com/v1" (no scheme)
and the exception escaped the scan loop. Both halves of that are covered here.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import json

from autow import health, llm
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


class TestModelOutputIsSanitised(unittest.TestCase):
    """A model verdict is not the last word.

    The first working run rebuilt the board correctly but resurrected eight
    deadlines from June and September — the rule path had always dropped
    expired items and the model path simply did not check.
    """

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
        os.environ.update(
            LLM_API_KEY="sk-x",
            LLM_BASE_URL="https://api.deepseek.com/v1",
            LLM_MODEL="deepseek-chat",
        )

    def reply(self, **fields) -> mock.Mock:
        body = {
            "action_required": True,
            "title": "交课程报告",
            "detail": "发到课程平台",
            "due_at": None,
            "priority": "normal",
        }
        body.update(fields)
        return mock.Mock(return_value=body)

    def test_an_expired_deadline_produces_no_todo(self):
        with mock.patch.object(llm, "_call", self.reply(due_at="2026-06-20T23:59")):
            draft, _ = llm.analyze_notice(a_notice())
        self.assertIsNone(draft)

    def test_a_future_deadline_survives(self):
        soon = date.today() + timedelta(days=5)
        with mock.patch.object(llm, "_call", self.reply(due_at=f"{soon.isoformat()}T23:59")):
            draft, _ = llm.analyze_notice(a_notice())
        self.assertIsNotNone(draft)
        self.assertEqual(draft.due_at[:10], soon.isoformat())

    def test_a_missing_deadline_is_still_a_todo(self):
        with mock.patch.object(llm, "_call", self.reply(due_at=None)):
            draft, _ = llm.analyze_notice(a_notice())
        self.assertIsNotNone(draft)

    def test_a_deadline_from_yesterday_is_tolerated(self):
        # Same-day deadlines pass before the user gets to them; keep one day of
        # slack so the item is still visible.
        yesterday = date.today() - timedelta(days=1)
        with mock.patch.object(llm, "_call", self.reply(due_at=f"{yesterday.isoformat()}T23:59")):
            draft, _ = llm.analyze_notice(a_notice())
        self.assertIsNotNone(draft)

    def test_a_deadline_from_two_days_ago_is_dropped(self):
        older = date.today() - timedelta(days=2)
        with mock.patch.object(llm, "_call", self.reply(due_at=f"{older.isoformat()}T23:59")):
            draft, _ = llm.analyze_notice(a_notice())
        self.assertIsNone(draft)


class TestHealth(unittest.TestCase):
    """A silent fallback to the rule extractor is the failure mode that matters.

    It produced a plausible-looking board for a whole day before anyone noticed,
    so the run has to publish what actually happened.
    """

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
        os.environ["LLM_BASE_URL"] = "https://api.example.com/v1"
        llm.reset_stats()
        self.addCleanup(llm.reset_stats)

    def test_a_failed_call_is_recorded(self):
        boom = mock.Mock(side_effect=ValueError("unknown url type"))
        with mock.patch.object(llm, "_call", boom):
            llm.analyze_notice(a_notice())
        extractor = health.build()["extractor"]
        self.assertTrue(extractor["configured"])
        self.assertEqual(extractor["attempts"], 1)
        self.assertEqual(extractor["failures"], 1)
        self.assertFalse(extractor["ok"])
        self.assertIn("unknown url type", extractor["last_error"])

    def test_no_key_is_reported_as_not_configured(self):
        os.environ.pop("LLM_API_KEY", None)
        extractor = health.build()["extractor"]
        self.assertFalse(extractor["configured"])
        self.assertEqual(extractor["mode"], "off")

    def test_the_api_key_never_appears_in_the_published_file(self):
        boom = mock.Mock(side_effect=ValueError("401 Unauthorized"))
        with mock.patch.object(llm, "_call", boom):
            llm.analyze_notice(a_notice())
        published = json.dumps(health.build(), ensure_ascii=False)
        self.assertNotIn("test-key", published)

    def test_a_failing_probe_is_published(self):
        # This is the signal that a quiet day cannot hide a broken key.
        boom = mock.Mock(side_effect=ValueError("401 Unauthorized"))
        with mock.patch.object(llm, "_call", boom):
            result = llm.probe()
        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])
        self.assertIn("401", health.build()["extractor"]["probe"]["error"])

    def test_a_passing_probe_is_published(self):
        with mock.patch.object(llm, "_call", mock.Mock(return_value={"ok": True})):
            self.assertTrue(llm.probe()["ok"])
        self.assertTrue(health.build()["extractor"]["probe"]["ok"])


class TestMisconfiguration(unittest.TestCase):
    """The first real deployment pasted the API key into the model and base_url
    fields, and the health file published it to a public repository."""

    # A synthetic value shaped like a DeepSeek key. Never paste a real one here:
    # the first version of this test used the key it was describing, which put
    # the credential in the repository a second time.
    KEY = "sk-0000notarealkey0000notarealkey00"

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

    def test_a_key_pasted_as_the_base_url_is_rejected(self):
        os.environ.update(LLM_API_KEY=self.KEY, LLM_BASE_URL=self.KEY, LLM_MODEL="deepseek-chat")
        problem = llm.validate_config(llm.config())
        self.assertIsNotNone(problem)
        self.assertIn("LLM_BASE_URL", problem)

    def test_a_key_pasted_as_the_model_is_rejected(self):
        os.environ.update(
            LLM_API_KEY=self.KEY, LLM_BASE_URL="https://api.deepseek.com/v1", LLM_MODEL=self.KEY
        )
        problem = llm.validate_config(llm.config())
        self.assertIsNotNone(problem)
        self.assertIn("LLM_MODEL", problem)

    def test_a_good_config_passes(self):
        os.environ.update(
            LLM_API_KEY=self.KEY,
            LLM_BASE_URL="https://api.deepseek.com/v1",
            LLM_MODEL="deepseek-chat",
        )
        self.assertIsNone(llm.validate_config(llm.config()))

    def test_real_provider_model_names_are_not_mistaken_for_keys(self):
        for model in ("deepseek-chat", "glm-4-flash", "qwen-plus", "deepseek-reasoner"):
            with self.subTest(model):
                os.environ.update(
                    LLM_API_KEY=self.KEY,
                    LLM_BASE_URL="https://api.deepseek.com/v1",
                    LLM_MODEL=model,
                )
                self.assertIsNone(llm.validate_config(llm.config()))

    def test_health_never_publishes_the_key(self):
        os.environ.update(LLM_API_KEY=self.KEY, LLM_BASE_URL=self.KEY, LLM_MODEL=self.KEY)
        published = json.dumps(health.build(), ensure_ascii=False)
        self.assertNotIn(self.KEY, published)
        self.assertIn("(看起来是密钥，已隐藏)", published)
        self.assertIsNotNone(health.build()["extractor"]["config_error"])


class TestPrompts(unittest.TestCase):
    """A missing placeholder raises KeyError inside analyze(), which the broad
    `except Exception` there turns into a silent fall back to the rule
    extractor — the exact failure mode that cost a day to find. Catch it here.
    """

    def test_the_notice_prompt_fills_every_placeholder(self):
        filled = llm.NOTICE_PROMPT.format(
            today="2026-10-13", profile=llm.profile(), grade=llm.grade()
        )
        for leftover in ("{profile}", "{grade}", "{today}"):
            self.assertNotIn(leftover, filled)

    def test_the_chat_prompt_fills_every_placeholder(self):
        self.assertNotIn("{today}", llm.CHAT_PROMPT.format(today="2026-10-13"))

    def test_the_pasted_prompt_fills_every_placeholder(self):
        # A missing key here raises inside analyze(), which the broad except turns
        # into a silent fall back to the rule extractor.
        filled = llm.PASTED_PROMPT.format(
            today="2026-10-13", profile=llm.profile(), grade=llm.grade()
        )
        for leftover in ("{profile}", "{grade}", "{today}"):
            self.assertNotIn(leftover, filled)

    def test_the_default_profile_names_the_college(self):
        os.environ.pop("AUTOW_STUDENT_PROFILE", None)
        self.addCleanup(os.environ.pop, "AUTOW_STUDENT_PROFILE", None)
        self.assertIn("人工智能学院", llm.profile())

    def test_the_grade_is_configurable_and_defaults_to_2026(self):
        os.environ.pop("AUTOW_STUDENT_GRADE", None)
        self.assertIn("2026", llm.grade())
        os.environ["AUTOW_STUDENT_GRADE"] = "2024 级"
        self.addCleanup(os.environ.pop, "AUTOW_STUDENT_GRADE", None)
        self.assertEqual(llm.grade(), "2024 级")

    def test_the_prompt_tells_the_model_to_keep_optional_things(self):
        # The model dropped 普通话水平测试 by arguing it was not compulsory.
        filled = llm.NOTICE_PROMPT.format(
            today="2026-10-13", profile=llm.profile(), grade=llm.grade()
        )
        self.assertIn("自愿参加", filled)
        self.assertIn("推免", filled)


class TestHealthKeepsTheLastProbe(unittest.TestCase):
    """A QQ ingest never probes, and used to blank that field on the way past."""

    def setUp(self):
        os.environ.update(
            LLM_API_KEY="sk-x",
            LLM_BASE_URL="https://api.deepseek.com/v1",
            LLM_MODEL="deepseek-chat",
        )
        os.environ.pop("AUTOW_LLM_MODE", None)
        self.addCleanup(os.environ.pop, "AUTOW_LLM_MODE", None)

    def test_the_previous_result_survives_a_run_that_does_not_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "health.json").write_text(
                json.dumps({"extractor": {"probe": {"ok": True}}}), encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"AUTOW_DOCS_DIR": tmp}):
                llm.reset_stats()  # nothing probed this run
                probe = health.build()["extractor"]["probe"]
            self.assertTrue(probe["ok"])
            self.assertTrue(probe["carried_over"], "应该标明这是上一次的结果")

    def test_nothing_to_carry_over_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"AUTOW_DOCS_DIR": tmp}):
                llm.reset_stats()
                self.assertIsNone(health.build()["extractor"]["probe"])

    def test_a_real_probe_is_not_marked_as_carried_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "health.json").write_text(
                json.dumps({"extractor": {"probe": {"ok": False}}}), encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"AUTOW_DOCS_DIR": tmp}):
                llm.reset_stats()
                llm.STATS["probe"] = {"ok": True}
                probe = health.build()["extractor"]["probe"]
            self.assertTrue(probe["ok"])
            self.assertNotIn("carried_over", probe)

    def test_a_run_with_nothing_to_do_is_still_ok(self):
        # No new notices means no model calls. That is healthy, not broken.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"AUTOW_DOCS_DIR": tmp}):
                llm.reset_stats()
                llm.STATS["probe"] = {"ok": True}
                extractor = health.build()["extractor"]
            self.assertEqual(extractor["attempts"], 0)
            self.assertEqual(extractor["failures"], 0)
            self.assertTrue(extractor["ok"])

    def test_a_failing_self_check_is_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"AUTOW_DOCS_DIR": tmp}):
                llm.reset_stats()
                llm.STATS["probe"] = {"ok": False, "error": "401"}
                self.assertFalse(health.build()["extractor"]["ok"])


if __name__ == "__main__":
    unittest.main()
