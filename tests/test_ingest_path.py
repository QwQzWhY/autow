"""Guard the dependency budget of the QQ ingest path.

Incoming QQ messages are handled by a workflow that installs *nothing* — no
Pipfile, no browser. That only works while `autow.cli`, `autow.llm`,
`autow.store` and `autow.publish` stay on the standard library. If someone adds
a top-level Playwright (or other) import to any of them, the ingest job starts
failing in CI with a confusing ImportError, so fail loudly here instead.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import sys, importlib.abc

BLOCKED = ("playwright",)

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is not installed in this environment")
        return None

sys.meta_path.insert(0, Blocker())

import autow.cli
import autow.llm
import autow.publish
import autow.store

print("clean")
"""


class TestIngestPath(unittest.TestCase):
    def test_ingest_imports_without_a_browser(self):
        result = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)

    def test_crawling_does_import_the_browser(self):
        # The crawler obviously does need it — make sure the guard above is not
        # passing just because the probe is broken.
        probe = "import autow.browser; print('browser ok')"
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
