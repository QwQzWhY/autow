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

    def test_the_shell_never_pins_an_old_page(self):
        """壳缓存里的 index.html 指着上一版 app.js，用户就永远停在旧版本。

        实际发生过：线上已经是 v13，用户刷新多少次页脚都还是 v12。
        """
        sw = read("sw.js")
        self.assertIn("e.request.mode === 'navigate'", sw)
        self.assertIn(
            "e.respondWith(fetch(e.request).catch(() => caches.match('index.html')))",
            sw,
            "页面没有走网络优先，旧 service worker 会把旧首页钉住",
        )

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

    def test_a_failed_report_is_retried_before_giving_up(self):
        # 网络抖一下，不该等到下次打开页面才补报。
        script = read("app.js")
        self.assertIn("STATUS_RETRY_MS", script)
        self.assertIn("return sendStatus(payload, attempt + 1)", script)

    def test_marking_a_card_writes_the_status_file_directly(self):
        """勾选/删除不再走一次 GitHub Actions——排队加跑一趟要几十秒到十几分钟。"""
        script = read("app.js")
        self.assertIn("async function writeStatus(updates)", script)
        self.assertIn("contents/${STATUS_PATH}", script)
        self.assertIn("method: 'PUT'", script)
        self.assertIn("async function fetchStatusMap()", script)
        self.assertIn("applyStatus(data.todos || [], map)", script)
        # 两台设备同时写时 sha 会过期，必须能重读重试。
        self.assertIn("res.status !== 409 && res.status !== 422", script)

    def test_a_new_todo_is_queued_and_confirmed(self):
        """新建走 Actions，但同一并发组会把排队的那次取消掉——实测丢过。"""
        script = read("app.js")
        self.assertIn("function enqueue(payload)", script)
        self.assertIn("await enqueue(payload)", script)
        self.assertIn("function confirmCreated(item, payload, attempt = 0)", script)
        self.assertIn("some((t) => t.key === item.key)", script, "没有回云端确认就不知道丢没丢")

    def test_a_stuck_new_todo_is_sent_once_more(self):
        # 一直没到云端的那条，不能永远停在"未同步"。
        script = read("app.js")
        self.assertIn("payload,", script)
        self.assertIn("item.retried", script)
        self.assertIn("enqueue(payload)", script)

    def test_a_stuck_item_from_an_older_build_is_reconstructed(self):
        """老条目没存原始请求，卡片字段够拼回来一次。"""
        script = read("app.js")
        self.assertIn("function migratePending(item)", script)
        self.assertIn("kind === 'note'", script)
        self.assertIn("item.payload || migratePending(item)", script)

    def test_local_state_the_cloud_missed_is_reported_on_load(self):
        script = read("app.js")
        self.assertIn("kind: 'status', items: behind", script, "积压的改动要一次全报")
        self.assertIn("catchUpDone", script)

    def test_a_backlog_that_cannot_be_reported_also_says_so(self):
        # 没有令牌时静悄悄跳过，正是"只删了一半"没人知道的原因。
        script = read("app.js")
        self.assertIn("条改动没同步", script)

    def test_unsynced_changes_stay_on_screen(self):
        """失败的提示不能只弹 4 秒——用户就是这样白等了一整天。

        现在页面上常驻一条：本机有多少条改动没同步、为什么、点哪里修。
        """
        page = read("index.html")
        self.assertIn('id="sync-note"', page)
        script = read("app.js")
        self.assertIn("function renderSyncNote()", script)
        self.assertIn("还没同步到云端", script)
        self.assertIn("renderSyncNote()", script.split("function drawBoard()")[1])

    def test_the_repo_does_not_have_to_be_typed(self):
        """仓库名就在网址里，别再让用户手敲一遍。"""
        for name in ("app.js", "qq.js"):
            with self.subTest(name):
                script = read(name)
                self.assertIn("function repoFromLocation()", script)
                self.assertIn("|| repoFromLocation()", script, "没拿网址里的仓库名兜底")

    def test_the_board_does_not_wait_for_the_pages_rebuild(self):
        """Pages 要重新构建（45~150 秒），同一份文件在 API 上是提交后立即可读的。

        有令牌就先走 API，没令牌或失败再落回 Pages。
        """
        script = read("app.js")
        self.assertIn("async function fetchBoard()", script)
        self.assertIn("contents/docs/data/todos.json", script)
        self.assertIn("new TextDecoder().decode(bytes)", script, "base64 要按 UTF-8 解")
        self.assertIn("await fetchBoard()", script)

    def test_polling_speeds_up_while_a_change_is_in_flight(self):
        script = read("app.js")
        self.assertIn("FAST_REFRESH_MS", script)
        self.assertIn("unsyncedCount() ? FAST_REFRESH_MS", script)
        self.assertIn("setTimeout(async () =>", script, "固定间隔没法按需加速")

    def test_the_other_device_sees_a_tick_in_about_ten_seconds(self):
        """整块板每 10 秒拉一次太重（手机上跑流量），只看那个小状态文件。"""
        script = read("app.js")
        self.assertIn("STATUS_POLL_MS", script)
        self.assertIn("JSON.stringify(map) !== JSON.stringify(state.statusMap)", script)


if __name__ == "__main__":
    unittest.main()
