"""Run the browser-side tests under `node --test`.

The board's filtering lives in docs/todo-view.js as plain ES modules so it can
be exercised outside a browser. Running it from here keeps `python -m unittest`
as the single entry point for the whole project.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS_TESTS = ROOT / "tests" / "js"


class TestBrowserLogic(unittest.TestCase):
    def test_node_tests_pass(self):
        node = shutil.which("node")
        if node is None:
            self.fail("找不到 node，无法运行前端逻辑的测试")
        # Pass the files explicitly: `node --test <dir>` is not reliably
        # understood as "search this directory" across Node versions.
        files = sorted(str(p) for p in JS_TESTS.glob("*.test.mjs"))
        self.assertTrue(files, f"{JS_TESTS} 下没有测试文件")
        result = subprocess.run(
            [node, "--test", *files],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
