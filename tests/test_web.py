"""Guards for the two hand-written pages.

Both mistakes below actually happened while editing: a stray Markdown `**bold**`
in HTML (renders as literal asterisks) and a tab swap where the `hidden`
attribute was not moved with it (renders an empty panel).
"""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


class TestWebPages(unittest.TestCase):
    def test_no_markdown_emphasis_in_the_html(self):
        for name in ("index.html", "qq.html"):
            with self.subTest(name):
                self.assertNotIn(
                    "**",
                    read(name),
                    "HTML 里写了 Markdown 粗体，页面上会原样露出星号",
                )

    def test_the_visible_guide_matches_the_active_tab(self):
        page = read("qq.html")
        active = re.search(r'seg__item is-active" data-mode="(\w+)"', page).group(1)
        hidden = set(re.findall(r'<div id="(\w+)-guide"[^>]*\shidden', page))
        self.assertNotIn(
            f"{active}-guide", hidden, f"{active} 标签是选中状态，对应的面板却被隐藏了"
        )
        self.assertEqual(len(hidden), 1, "应该正好有一个面板是隐藏的")

    def test_the_pages_reference_assets_that_exist(self):
        for name in ("index.html", "qq.html"):
            page = read(name)
            for asset in re.findall(r'(?:src|href)="([^":]+\.(?:js|css))', page):
                path = DOCS / asset.split("?")[0]
                with self.subTest(f"{name} -> {asset}"):
                    self.assertTrue(path.exists(), f"{asset} 不存在")

    def test_every_asset_shares_one_version(self):
        # A fresh script pulling in a cached older module is a runtime error.
        versions = set()
        for name in ("index.html", "qq.html", "sw.js", "app.js"):
            versions |= set(re.findall(r"\?v=(\d+)", read(name)))
        self.assertEqual(
            len(versions), 1, f"资源版本号不一致：{sorted(versions)}（改了要一起改）"
        )


if __name__ == "__main__":
    unittest.main()
