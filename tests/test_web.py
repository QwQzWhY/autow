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
        self.assertIn("queueStatus(item.key, 'deleted')", script)
        self.assertIn("queueStatus(item.key, nowDone ? 'done' : 'open')", script)
        self.assertIn("queueStatus(state.undo.key, 'open')", script, "撤销没报回去")

    def test_the_board_groups_items_without_a_deadline(self):
        # 活动类通知要保留（用户要求过），但不能和"明天到期"挤在同一段里。
        script = read("app.js")
        self.assertIn("splitByDeadline", script)
        self.assertIn("没有截止时间", script)

    def test_the_header_cannot_disagree_with_the_cards(self):
        """页头数字和屏幕上的卡片必须永远一致。

        用户截过一次图：上面写"2 项待办"，下面画着 4 张卡——旧的缓存 JS 用旧算法
        算数字、却按新数据画了卡片。现在数字先算先写，而且画到一半出错会直接显示错误。
        """
        script = read("app.js")
        self.assertIn("function drawBoard()", script)
        self.assertIn("页面出错：", script, "画到一半出错会静默留下半新的页面")
        meta = script.index("项待办") if "项待办" in script else -1
        self.assertGreater(meta, -1)
        self.assertLess(
            script.index("$('meta').textContent"),
            script.index("list.textContent = ''"),
            "数字要写在画卡片之前，否则中途出错时数字会停在上一轮",
        )

    def test_the_footer_says_which_build_is_running(self):
        # 旧缓存页面和新页面长得一模一样，只能靠这个区分。
        script = read("app.js")
        self.assertIn("import.meta.url.match", script)
        self.assertIn("前端 v${ASSET_VERSION}", script)

    def test_a_report_that_cannot_be_sent_says_so(self):
        """一台设备没填令牌时，删除只在本地生效——这件事必须说出来。

        用户就是这么撞上的：电脑上删了 11 条，云端一条都不知道，手机上照样显示，
        而屏幕上没有任何提示。
        """
        script = read("app.js")
        self.assertIn("没同步到云端", script)

    def test_status_reports_are_sent_one_at_a_time(self):
        # 同时发多条时，GitHub 的并发组会把排队的那条取消掉，中间的删除就丢了。
        script = read("app.js")
        self.assertIn("STATUS_SPACING_MS", script)
        self.assertIn("await reportStatus(key, status)", script)
        self.assertIn("while (statusQueue.length)", script, "没有排队，就是并行发")

    def test_local_state_the_cloud_missed_is_reported_on_load(self):
        script = read("app.js")
        self.assertIn("CATCH_UP_PER_LOAD", script)
        self.assertIn("catchUpDone", script)


if __name__ == "__main__":
    unittest.main()
