"""Workflow files must parse and keep the triggers the project relies on.

A YAML mistake here fails quietly: GitHub simply never runs the workflow, so
the automation goes silent with no error anywhere to look at.
"""

import pathlib
import unittest

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def load(name: str):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on` as the boolean True.
    return data, data.get(True, data.get("on"))


class TestWorkflows(unittest.TestCase):
    def test_every_workflow_parses_and_has_jobs(self):
        files = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(files, "没有找到任何 workflow 文件")
        for path in files:
            with self.subTest(path.name):
                data, triggers = load(path.name)
                self.assertIsInstance(data, dict)
                self.assertIsNotNone(triggers, f"{path.name} 缺少 on:")
                self.assertTrue(data.get("jobs"), f"{path.name} 没有 jobs")

    def test_the_scan_runs_on_a_schedule_and_on_code_changes(self):
        _, triggers = load("scan.yml")
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertIn("push", triggers)
        paths = triggers["push"]["paths"]
        self.assertIn("autow/**", paths)
        # The bot's own data commits must not retrigger the scan, or every run
        # would set off the next one.
        for noisy in ("data/**", "docs/**", "docs/data/**"):
            self.assertNotIn(noisy, paths)

    def test_scan_ignores_the_bot_commits_it_produces(self):
        _, triggers = load("scan.yml")
        self.assertNotIn("data/**", triggers["push"]["paths"])

    def test_qq_ingest_only_fires_on_dispatch(self):
        _, triggers = load("qq.yml")
        self.assertEqual(list(triggers), ["repository_dispatch"])
        self.assertIn("qq-message", triggers["repository_dispatch"]["types"])

    def test_the_scan_commits_its_status_even_when_the_crawl_fails(self):
        # Otherwise a configuration error is invisible: the run goes red and
        # nothing lands in the repository to explain it.
        data, _ = load("scan.yml")
        steps = data["jobs"]["scan"]["steps"]
        commit = [s for s in steps if s.get("name") == "提交结果"][0]
        self.assertEqual(commit.get("if"), "always()")

    def test_the_heavy_jobs_share_one_concurrency_group(self):
        # Otherwise a scheduled scan and an incoming QQ message can both try to
        # commit at the same moment.
        groups = {name: load(name)[0]["concurrency"]["group"] for name in ("scan.yml", "qq.yml")}
        self.assertEqual(groups["scan.yml"], groups["qq.yml"])


if __name__ == "__main__":
    unittest.main()
