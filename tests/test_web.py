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

    def test_the_manual_form_can_flag_a_college_nomination(self):
        """自己加待办的时候也要能勾「需学院推荐」。

        这个分组只在真有这种条目时才显示，所以没有这个勾选框的话，用户既没法自己
        加一条来验证，也没法把不用模型判断的事手动归进去。
        """
        self.assertIn('id="manual-nomination"', read("index.html"))
        script = read("app.js")
        self.assertIn("$('manual-nomination').checked", script, "表单里有勾选框，脚本没读它")
        self.assertIn("needs_recommendation: nomination", script, "勾了却没发给云端")

    def test_pending_cards_go_through_the_same_split(self):
        # 否则刚勾完的那一条会先出现在顶部，等云端回话才跳到下面那一组。
        script = read("app.js")
        self.assertIn("splitByNomination([...pending, ...items])", script)

    def test_the_board_reports_ticks_and_deletes_to_the_cloud(self):
        """勾选和删除不能只写进这台手机的 localStorage。

        不报上去的后果：换台设备看又成了未完成，清一次浏览器数据删掉的会回来，
        公开的 todos.json 里也一直是未完成。
        """
        script = read("app.js")
        self.assertIn("kind: 'status'", script, "看板没有把状态报给云端")
        self.assertIn("reportStatus(item.key, 'deleted')", script)
        self.assertIn("reportStatus(item.key, nowDone ? 'done' : 'open')", script)
        self.assertIn("reportStatus(state.undo.key, 'open')", script, "撤销没报回去")

    def test_the_board_groups_items_without_a_deadline(self):
        # 活动类通知要保留（用户要求过），但不能和"明天到期"挤在同一段里。
        script = read("app.js")
        self.assertIn("splitByDeadline", script)
        self.assertIn("没有截止时间", script)


if __name__ == "__main__":
    unittest.main()
