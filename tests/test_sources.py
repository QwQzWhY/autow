"""Tests for the per-site adapters that do not need a browser."""

import unittest

from autow import publish
from autow.sources import SOURCES, lzu_jwc, lzu_news, lzu_xgb


class TestLzuNews(unittest.TestCase):
    def test_relative_links_resolve_against_the_list_page(self):
        notice = lzu_news.to_notice(
            {"title": "关于某事的通知", "href": "info/1056/1177735.htm", "date": "2026-10-12"}
        )
        self.assertEqual(notice.url, "https://news.lzu.edu.cn/info/1056/1177735.htm")
        self.assertEqual(notice.external_id, "1177735.htm")
        self.assertEqual(notice.published_at, "2026-10-12")

    def test_offsite_links_are_kept_absolute(self):
        # The board mixes in links to other campus hosts.
        notice = lzu_news.to_notice(
            {
                "title": "关于举办国考训练营的通知",
                "href": "https://job.lzu.edu.cn/html/68/article/2026/93192.html",
                "date": "2026-10-12",
            }
        )
        self.assertEqual(notice.url, "https://job.lzu.edu.cn/html/68/article/2026/93192.html")

    def test_rows_without_a_title_or_link_are_dropped(self):
        self.assertIsNone(lzu_news.to_notice({"title": "", "href": "a.htm"}))
        self.assertIsNone(lzu_news.to_notice({"title": "标题", "href": ""}))

    def test_a_date_is_optional(self):
        notice = lzu_news.to_notice({"title": "标题", "href": "info/1/2.htm"})
        self.assertIsNone(notice.published_at)

    def test_there_is_only_one_page(self):
        self.assertEqual(lzu_news.fetch_list(None, page_no=2), [])


class TestSourceRegistry(unittest.TestCase):
    def test_every_source_exposes_the_same_interface(self):
        for source in SOURCES:
            self.assertTrue(source.NAME, source)
            self.assertTrue(callable(source.fetch_list), source)
            self.assertTrue(callable(source.fetch_detail), source)

    def test_the_two_new_campus_sources_are_registered(self):
        names = {source.NAME for source in SOURCES}
        self.assertIn("lzu-jwc", names, "教务处的通知没人抓")
        self.assertIn("lzu-xgb", names, "学工部的通知没人抓")

    def test_every_source_has_a_unique_name(self):
        names = [source.NAME for source in SOURCES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_source_has_a_name_the_board_can_show(self):
        # 没登记的话，卡片上会直接露出 lzu-xgb 这种英文 id。
        for source in SOURCES:
            with self.subTest(source.NAME):
                self.assertIn(source.NAME, publish.SOURCE_LABELS)


class TestXgb(unittest.TestCase):
    """学工部的列表来自 JSON，不在 HTML 里。"""

    ROW = {
        "articleId": 338466,
        "articleTitle": "兰州大学第九届感恩中国近现代科学家奖助学金笔试通知",
        "articleUrl": "/tongzhigonggao/zizhufuwu/2026/1013/338466.html",
        "publishDate": "2026-10-13",
    }

    def test_a_feed_row_becomes_a_notice(self):
        notice = lzu_xgb.to_notice(self.ROW, "资助通知")
        self.assertEqual(notice.title, self.ROW["articleTitle"])
        self.assertEqual(
            notice.url, "http://xgb.lzu.edu.cn/tongzhigonggao/zizhufuwu/2026/1013/338466.html"
        )
        self.assertEqual(notice.published_at, "2026-10-13")
        self.assertEqual(notice.category, "资助通知")

    def test_the_id_comes_from_the_url(self):
        self.assertEqual(lzu_xgb.to_notice(self.ROW, "资助通知").external_id, "338466.html")

    def test_a_row_without_a_title_or_link_is_dropped(self):
        self.assertIsNone(lzu_xgb.to_notice({**self.ROW, "articleTitle": "  "}, "资助通知"))
        self.assertIsNone(lzu_xgb.to_notice({**self.ROW, "articleUrl": ""}, "资助通知"))

    def test_a_legacy_field_name_still_works(self):
        # 列表模板用 aurl 取值，字段名两边都出现过。
        row = {**self.ROW, "articleUrl": "", "aurl": self.ROW["articleUrl"]}
        self.assertIsNotNone(lzu_xgb.to_notice(row, "思政通知"))

    def test_the_category_falls_back_to_the_channel(self):
        notice = lzu_xgb.to_notice({**self.ROW, "channelName": "学生资助"}, "")
        self.assertEqual(notice.category, "学生资助")

    def test_the_columns_we_watch_are_the_ones_with_student_content(self):
        self.assertIn("资助通知", lzu_xgb.COLUMNS)
        self.assertIn("国防教育", lzu_xgb.COLUMNS)
        # 栏目的 id 是从栏目页上的 /xgbnew/lm/<id>.js 抄来的，不能是猜的。
        for column_id in lzu_xgb.COLUMNS.values():
            self.assertIsInstance(column_id, int)
            self.assertIn(str(column_id), lzu_xgb.feed_url(column_id))

    # 正文页是服务端渲染的，所以这里按真实模板裁一段 HTML 来测——不用联网，
    # 也不用浏览器（浏览器那条路在 CI 的 IP 上大面积被 WAF 挡掉）。
    ARTICLE = """
    <section class='row main_sec'><div class='col-lg-12 col-xs-12'>
      <div class='article'>
        <h3>兰州大学第九届感恩中国近现代科学家奖助学金笔试通知</h3>
        <div class='infoBox'><i class='iconfont icon-shizhong3'></i> 2026-10-13 学工部 </div>
        <div id='news'><p>各学院：<br />&nbsp;&nbsp;共33名学生进入笔试环节（名单附后）。</p>
        <p>时间：2026年10月14日（星期三）中午12:30</p></div>
      </div>
    </div></section>
    """

    def test_the_article_body_is_read_without_a_browser(self):
        title, byline, body = lzu_xgb.parse_article(self.ARTICLE)
        self.assertEqual(title, "兰州大学第九届感恩中国近现代科学家奖助学金笔试通知")
        self.assertIn("2026-10-13", byline)
        self.assertIn("笔试环节", body)
        self.assertIn("10月14日", body)
        self.assertNotIn("<p>", body)

    def test_a_paragraph_break_survives_as_a_line_break(self):
        _title, _byline, body = lzu_xgb.parse_article(self.ARTICLE)
        self.assertIn("名单附后）。\n时间：2026年10月14日", body)

    def test_a_page_that_is_not_an_article_parses_to_nothing(self):
        self.assertEqual(lzu_xgb.parse_article("<html><body>412</body></html>"), ("", "", ""))

    def test_nested_divs_do_not_end_the_body_early(self):
        html = (
            "<div class='article'><h3>标题标题</h3><div class='infoBox'>2026-10-13 学工部</div>"
            "<div id='news'><div class='wrap'><p>里面有嵌套的 div，不能被截断。</p></div></div>"
            "</div>"
        )
        _title, _byline, body = lzu_xgb.parse_article(html)
        self.assertIn("不能被截断", body)
        self.assertNotIn("infoBox", body)


class TestJwc(unittest.TestCase):
    """教务处首页把各栏目各列几条，但不是所有 ul.list 里的链接都是通知。"""

    ROW = {
        "title": "关于召开本科教学工作例会的通知",
        "href": "/tongzhigonggao/2026/0922/337606.html",
        "date": "2026-09-22",
        "category": "综合公告",
    }

    def test_a_list_row_becomes_a_notice(self):
        notice = lzu_jwc.to_notice(self.ROW)
        self.assertEqual(notice.title, self.ROW["title"])
        self.assertEqual(notice.url, "https://jwc.lzu.edu.cn/tongzhigonggao/2026/0922/337606.html")
        self.assertEqual(notice.published_at, "2026-09-22")
        self.assertEqual(notice.category, "综合公告")

    def test_a_navigation_link_is_not_a_notice(self):
        # 导航和栏目入口挂在同一个 list 类下面，不能当成通知。
        for href in (
            "/tongzhigonggao/zonghegonggao/index.html",
            "/jigoushezhi/index.html",
            "https://shpg.lzu.edu.cn",
            "/tongzhigonggao/index.html",
        ):
            with self.subTest(href):
                self.assertIsNone(lzu_jwc.to_notice({**self.ROW, "href": href}))

    def test_a_row_without_a_title_is_dropped(self):
        self.assertIsNone(lzu_jwc.to_notice({**self.ROW, "title": " "}))

    def test_a_row_without_a_date_still_works(self):
        notice = lzu_jwc.to_notice({**self.ROW, "date": ""})
        self.assertIsNotNone(notice)
        self.assertIsNone(notice.published_at)


class FakePage:
    """Just enough page for `_settled_rows`: it hands back row counts in order."""

    def __init__(self, counts: list[int]):
        self.counts = counts
        self.reads = 0
        self.waits = 0

    def evaluate(self, _js):
        count = self.counts[min(self.reads, len(self.counts) - 1)]
        self.reads += 1
        return [{"title": f"t{i}"} for i in range(count)]

    def wait_for_timeout(self, _ms):
        self.waits += 1


class TestJwcSettling(unittest.TestCase):
    """列表是一列一列画出来的，读太快就只拿到一列。"""

    def test_a_partly_rendered_page_is_not_accepted(self):
        # 3 条是「只画出一列」的样子，线上那次就是这么存下来的。
        page = FakePage([3, 3, 54, 54])
        rows = lzu_jwc._settled_rows(page)
        self.assertEqual(len(rows), 54)

    def test_a_settled_full_page_is_taken_immediately(self):
        page = FakePage([54, 54])
        self.assertEqual(len(lzu_jwc._settled_rows(page)), 54)
        self.assertEqual(page.reads, 2, "两次读数一致就该收手，不要白等")

    def test_it_gives_up_instead_of_hanging(self):
        page = FakePage([2])  # 一直只有 2 条
        rows = lzu_jwc._settled_rows(page, attempts=4)
        self.assertEqual(len(rows), 2)
        self.assertLessEqual(page.reads, 4)

    def test_source_names_are_unique(self):
        names = [s.NAME for s in SOURCES]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
